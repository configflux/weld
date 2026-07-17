"""Tests for the dds_idl strategy (generic non-ROS2 DDS ``.idl`` files).

Extracts struct data contracts, enums, and DDS topic channels from OMG
IDL text used by CycloneDDS / FastDDS. Per ADR 0086's static-truth policy
extraction is text-only: no ``idlc``/``fastddsgen`` run and no
``#include`` following. Topic channels reuse the ``ros2_dds`` transport
value (the DDS/RTPS wire) with ``surface_kind="pub_sub"`` and no
``protocol`` (see the strategy module docstring).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.contract import validate_fragment  # noqa: E402
from weld.strategies.dds_idl import extract  # noqa: E402


def _write(pkg: Path, name: str, body: str) -> None:
    (pkg / name).write_text(textwrap.dedent(body))


def _run(root: Path, glob: str = "idl/**/*.idl") -> tuple[dict, list, list]:
    result = extract(root, {"glob": glob}, {})
    return result.nodes, result.edges, list(result.discovered_from)


class DdsIdlFastddsTest(unittest.TestCase):
    """FastDDS-style ``@topic`` annotated definitions."""

    def _extract(self) -> tuple[dict, list]:
        d = tempfile.mkdtemp()
        root = Path(d)
        pkg = root / "idl" / "sensors"
        pkg.mkdir(parents=True)
        _write(pkg, "image.idl", """\
            module Sensors {
              enum PixelFormat { RGB, GRAY, DEPTH };
              @nested
              struct Header {
                unsigned long seq;
                string frame_id;
              };
              @topic
              struct Image {
                Header header;
                unsigned long width, height;
                sequence<octet> data;
                double calibration[9];
              };
            };
        """)
        res = extract(root, {"glob": "idl/**/*.idl"}, {})
        return res.nodes, res.edges

    def test_annotated_struct_emits_contract_and_definite_channel(self) -> None:
        nodes, _ = self._extract()
        cid = "contract:dds:sensors.image"
        chid = "channel:ros2_dds:sensors.image"
        self.assertIn(cid, nodes)
        self.assertIn(chid, nodes)
        self.assertEqual(nodes[cid]["type"], "contract")
        self.assertEqual(nodes[chid]["type"], "channel")
        self.assertEqual(nodes[chid]["props"]["confidence"], "definite")

    def test_channel_interaction_metadata_reuses_ros2_dds(self) -> None:
        nodes, _ = self._extract()
        props = nodes["channel:ros2_dds:sensors.image"]["props"]
        self.assertEqual(props["transport"], "ros2_dds")
        self.assertEqual(props["surface_kind"], "pub_sub")
        self.assertEqual(props["boundary_kind"], "internal")
        self.assertEqual(props["topic"], "Sensors.Image")
        # Omission-over-guess: no PROTOCOL value fits non-ROS2 DDS.
        self.assertNotIn("protocol", props)

    def test_nested_struct_has_contract_but_no_channel(self) -> None:
        nodes, _ = self._extract()
        self.assertIn("contract:dds:sensors.header", nodes)
        self.assertNotIn("channel:ros2_dds:sensors.header", nodes)

    def test_enum_node_carries_members(self) -> None:
        nodes, _ = self._extract()
        eid = "enum:dds:sensors.pixelformat"
        self.assertIn(eid, nodes)
        self.assertEqual(
            nodes[eid]["props"]["members"], ["RGB", "GRAY", "DEPTH"],
        )

    def test_fields_parsed_with_types_and_comma_declarators(self) -> None:
        nodes, _ = self._extract()
        fields = nodes["contract:dds:sensors.image"]["props"]["fields"]
        by_name = {f["name"]: f["type"] for f in fields}
        self.assertEqual(by_name["header"], "Header")
        self.assertEqual(by_name["width"], "unsigned long")
        self.assertEqual(by_name["height"], "unsigned long")
        self.assertEqual(by_name["data"], "sequence<octet>")
        self.assertEqual(by_name["calibration"], "double")

    def test_edges_contains_and_implements(self) -> None:
        nodes, edges = self._extract()
        keys = {(e["from"], e["to"], e["type"]) for e in edges}
        file_nid = "file:idl/sensors/image"
        cid = "contract:dds:sensors.image"
        chid = "channel:ros2_dds:sensors.image"
        self.assertIn((file_nid, cid, "contains"), keys)
        self.assertIn((file_nid, chid, "contains"), keys)
        self.assertIn((chid, cid, "implements"), keys)

    def test_fragment_validates(self) -> None:
        nodes, edges = self._extract()
        errors = validate_fragment(
            {"nodes": nodes, "edges": edges},
            source_label="strategy:dds_idl",
            allow_dangling_edges=True,
        )
        self.assertEqual(errors, [], f"unexpected validation errors: {errors}")


class DdsIdlCyclonedddsTest(unittest.TestCase):
    """CycloneDDS-style ``#pragma keylist`` topics without annotations."""

    def test_keylist_struct_is_definite_topic(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "idl"
            pkg.mkdir(parents=True)
            _write(pkg, "chatter.idl", """\
                module Chat {
                  struct Message {
                    long index;
                    string content;
                  };
                };
                #pragma keylist Message index
            """)
            nodes, _, _ = _run(root)
            chid = "channel:ros2_dds:chat.message"
            self.assertIn("contract:dds:chat.message", nodes)
            self.assertIn(chid, nodes)
            self.assertEqual(nodes[chid]["props"]["confidence"], "definite")

    def test_plain_top_level_struct_is_inferred_topic(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "idl"
            pkg.mkdir(parents=True)
            _write(pkg, "plain.idl", """\
                struct Telemetry {
                  double voltage;
                  double current;
                };
            """)
            nodes, _, _ = _run(root)
            chid = "channel:ros2_dds:telemetry"
            self.assertIn("contract:dds:telemetry", nodes)
            self.assertIn(chid, nodes)
            self.assertEqual(nodes[chid]["props"]["confidence"], "inferred")

    def test_nested_modules_build_dotted_qualified_name(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "idl"
            pkg.mkdir(parents=True)
            _write(pkg, "nested.idl", """\
                module A {
                  module B {
                    struct Deep { long x; };
                  };
                };
            """)
            nodes, _, _ = _run(root)
            self.assertIn("contract:dds:a.b.deep", nodes)
            self.assertIn("channel:ros2_dds:a.b.deep", nodes)


class DdsIdlRobustnessTest(unittest.TestCase):
    """Comments, includes, and non-idl inputs are handled safely."""

    def test_comments_hide_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "idl"
            pkg.mkdir(parents=True)
            _write(pkg, "c.idl", """\
                // struct LineHidden { long a; };
                /* struct BlockHidden { long b; }; */
                // #pragma keylist Real c
                struct Real { long c; };
            """)
            nodes, _, _ = _run(root)
            self.assertIn("contract:dds:real", nodes)
            self.assertNotIn("contract:dds:linehidden", nodes)
            self.assertNotIn("contract:dds:blockhidden", nodes)
            # The commented-out pragma must NOT promote the topic to definite.
            self.assertEqual(
                nodes["channel:ros2_dds:real"]["props"]["confidence"], "inferred",
            )

    def test_include_directive_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "idl"
            pkg.mkdir(parents=True)
            _write(pkg, "inc.idl", """\
                #include "../../../etc/passwd"
                #include <dds/dds.idl>
                struct Safe { long ok; };
            """)
            nodes, _, discovered = _run(root)
            # Only the local struct is emitted; no include is read.
            self.assertIn("contract:dds:safe", nodes)
            self.assertEqual(discovered, ["idl/inc.idl"])

    def test_skip_constructs_do_not_derail_walk(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "idl"
            pkg.mkdir(parents=True)
            _write(pkg, "mixed.idl", """\
                module M {
                  typedef sequence<long> LongSeq;
                  const long MAX = 10;
                  union Variant switch (long) {
                    case 1: long a;
                    default: double b;
                  };
                  struct After { long survives; };
                };
            """)
            nodes, _, _ = _run(root)
            self.assertIn("contract:dds:m.after", nodes)

    def test_annotation_string_argument_cannot_inject_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "idl"
            pkg.mkdir(parents=True)
            _write(pkg, "ann.idl", """\
                @verbatim("declares a struct Phantom { long x; };")
                struct Genuine {
                  @unit("m/s") double speed;
                };
            """)
            nodes, _, _ = _run(root)
            self.assertIn("contract:dds:genuine", nodes)
            self.assertNotIn("contract:dds:phantom", nodes)
            fields = nodes["contract:dds:genuine"]["props"]["fields"]
            self.assertEqual(
                [f["name"] for f in fields], ["speed"],
            )

    def test_non_idl_and_missing_glob_yield_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "idl"
            pkg.mkdir(parents=True)
            _write(pkg, "notes.md", "# not idl")
            nodes, edges, discovered = _run(root)
            self.assertEqual((nodes, edges, discovered), ({}, [], []))
            empty = extract(root, {}, {})
            self.assertEqual(empty.nodes, {})
            self.assertEqual(empty.edges, [])
            self.assertEqual(list(empty.discovered_from), [])


if __name__ == "__main__":
    unittest.main()
