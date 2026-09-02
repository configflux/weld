"""How a ``dockerfile:`` node id is spelled, and what it stays (bd bz5w9).

The e2e probe ``weld_discover_dockerfile_identity_e2e_test`` asserts the
*behaviour* -- two images, two nodes -- keyed on ``props.file`` so it stays
true whatever the ids look like. This file is the other half: the id spelling
itself, which is a contract because ``wd context``, ``wd impact``, the compose
strategy's ``depends_on`` target and the documented examples all name it.

Three claims are pinned here:

1. The id is the Dockerfile's repo-relative path, so files that differ only by
   directory differ as nodes -- and so do ``Dockerfile`` and ``Dockerfile.dev``
   in one directory, the collision ADR 0045 recorded as deferred (``Path.stem``
   drops the ``.dev``, so both used to be ``dockerfile:Dockerfile``).

2. A repo-root ``Dockerfile`` keeps ``dockerfile:Dockerfile`` byte-for-byte.
   That is the back-compat promise: the rel path of a root file *is* its name,
   so the single-Dockerfile repo -- every blast-radius fixture, every doc
   example -- does not move.

3. The replaced stem id survives as ``props.aliases`` where it names one file
   unambiguously, and is withheld where it does not. Withholding matters as
   much as minting: two nodes claiming one alias is the same ambiguity a layer
   up, since ``weld._alias_index.build_alias_index`` settles a duplicate claim
   first-writer-wins.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._alias_index import build_alias_index, resolve_id
from weld.strategies._dockerfile_ids import (
    dockerfile_node_id,
    legacy_alias_by_path,
    legacy_dockerfile_node_id,
)
from weld.strategies.dockerfile import extract


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        _write(root / rel, body)


def _dockerfile_nodes(result) -> dict[str, dict]:
    return {
        nid: node for nid, node in result.nodes.items()
        if node.get("type") == "dockerfile"
    }


def _aliases(node: dict) -> list[str]:
    return list((node.get("props") or {}).get("aliases") or [])


class DockerfileNodeIdTest(unittest.TestCase):
    """Claim 1 and 2: the id is the path, and the root case is unchanged."""

    def test_a_root_dockerfile_keeps_the_bare_id(self) -> None:
        """The back-compat promise, asserted on the literal string.

        Every fixture, golden and documented example in the repo is a
        root-level Dockerfile; if this moved, all of them would.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _tree(root, {"Dockerfile": "FROM python:3.12-slim\n"})

            result = extract(root, {"glob": "Dockerfile*"}, {})
            self.assertEqual(
                sorted(_dockerfile_nodes(result)), ["dockerfile:Dockerfile"],
            )

    def test_same_name_in_two_directories_are_two_nodes(self) -> None:
        """The headline collision, at unit level."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _tree(root, {
                "apps/shop/Dockerfile": "FROM python:3.12-slim\n",
                "apps/blog/Dockerfile": "FROM node:20-alpine\n",
            })

            result = extract(root, {"glob": "apps/*/Dockerfile"}, {})
            nodes = _dockerfile_nodes(result)
            self.assertEqual(sorted(nodes), [
                "dockerfile:apps/blog/Dockerfile",
                "dockerfile:apps/shop/Dockerfile",
            ])
            self.assertEqual(
                nodes["dockerfile:apps/blog/Dockerfile"]["props"]["base_image"],
                "node:20-alpine",
            )
            self.assertEqual(
                nodes["dockerfile:apps/shop/Dockerfile"]["props"]["base_image"],
                "python:3.12-slim",
            )

    def test_dockerfile_and_dockerfile_dev_in_one_directory(self) -> None:
        """ADR 0045's deferred collision: ``Path.stem`` drops the suffix.

        Both files used to mint ``dockerfile:Dockerfile``. Nothing about the
        directory saves them -- this is the same-directory case, so a fix that
        only prefixed the directory would still collide here.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _tree(root, {
                "Dockerfile": "FROM python:3.12-slim\n",
                "Dockerfile.dev": "FROM python:3.12\n",
            })

            result = extract(root, {"glob": "Dockerfile*"}, {})
            self.assertEqual(sorted(_dockerfile_nodes(result)), [
                "dockerfile:Dockerfile",
                "dockerfile:Dockerfile.dev",
            ])

    def test_the_label_still_names_the_file(self) -> None:
        """Query recall does not depend on the id.

        ``wd query "Dockerfile"`` ranks on the label, which is the file name
        both before and after the id moved -- so the recall the alias is
        sometimes wanted for was never the alias's job.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _tree(root, {"docker/Dockerfile": "FROM python:3.12-slim\n"})

            result = extract(root, {"glob": "docker/Dockerfile"}, {})
            node = _dockerfile_nodes(result)["dockerfile:docker/Dockerfile"]
            self.assertEqual(node["label"], "Dockerfile")


class DockerfileLegacyAliasTest(unittest.TestCase):
    """Claim 3: the replaced id resolves, but only where it means one file."""

    def test_a_lone_non_root_dockerfile_carries_the_legacy_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _tree(root, {"docker/Dockerfile": "FROM python:3.12-slim\n"})

            result = extract(root, {"glob": "docker/Dockerfile"}, {})
            nodes = _dockerfile_nodes(result)
            self.assertEqual(
                _aliases(nodes["dockerfile:docker/Dockerfile"]),
                ["dockerfile:Dockerfile"],
            )

    def test_the_legacy_id_actually_resolves_through_the_index(self) -> None:
        """The alias is only worth carrying if the resolver honours it.

        Asserted through ``weld._alias_index`` rather than by reading the
        prop, because that module is what ``wd context`` and the MCP tools go
        through -- and it has guards of its own that could drop the alias.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _tree(root, {"docker/Dockerfile": "FROM python:3.12-slim\n"})

            nodes = extract(root, {"glob": "docker/Dockerfile"}, {}).nodes
            index = build_alias_index(nodes)
            self.assertEqual(
                resolve_id("dockerfile:Dockerfile", nodes, index),
                "dockerfile:docker/Dockerfile",
            )

    def test_an_ambiguous_legacy_id_is_withheld_from_every_claimant(
        self,
    ) -> None:
        """Two files, one old id, no alias -- there is no right answer.

        Minting it on both would put the collision back one layer up:
        ``build_alias_index`` keeps whichever claim it saw first.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _tree(root, {
                "apps/shop/Dockerfile": "FROM python:3.12-slim\n",
                "apps/blog/Dockerfile": "FROM node:20-alpine\n",
            })

            result = extract(root, {"glob": "apps/*/Dockerfile"}, {})
            for nid, node in _dockerfile_nodes(result).items():
                with self.subTest(node=nid):
                    self.assertEqual(_aliases(node), [])

    def test_a_root_dockerfile_keeps_the_id_rather_than_aliasing_it(
        self,
    ) -> None:
        """The shadow case: a root ``Dockerfile`` beside ``docker/Dockerfile``.

        The root file owns ``dockerfile:Dockerfile`` outright. If the subdir
        file also claimed it as an alias, the alias index would have to choose
        between an alias and a real node id -- it warns and drops, but the
        graph on disk would still be asserting something false. Both are
        claimants, so neither is aliased.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _tree(root, {
                "Dockerfile": "FROM python:3.12-slim\n",
                "docker/Dockerfile": "FROM node:20-alpine\n",
            })

            result = extract(root, {"glob": "**/Dockerfile"}, {})
            nodes = _dockerfile_nodes(result)
            self.assertEqual(sorted(nodes), [
                "dockerfile:Dockerfile",
                "dockerfile:docker/Dockerfile",
            ])
            for nid, node in nodes.items():
                with self.subTest(node=nid):
                    self.assertEqual(_aliases(node), [])


class LegacyAliasRuleTest(unittest.TestCase):
    """The rule itself, without a filesystem in the way."""

    def test_legacy_id_reproduces_the_replaced_expression(self) -> None:
        """The alias only helps if it is the id old graphs actually carry."""
        self.assertEqual(
            legacy_dockerfile_node_id("docker/Dockerfile.orders-api"),
            "dockerfile:Dockerfile",
        )
        self.assertEqual(
            legacy_dockerfile_node_id("api/Container.file.dev"),
            "dockerfile:Container_file",
        )

    def test_the_rule_is_a_function_of_the_set_not_the_order(self) -> None:
        """Two walk orders over one tree must write the same aliases.

        Discovery sorts its matches, but the rule is what guarantees the
        answer would not have changed if it did not.
        """
        paths = ["docker/Dockerfile", "api/Containerfile"]
        self.assertEqual(
            legacy_alias_by_path(paths),
            legacy_alias_by_path(list(reversed(paths))),
        )

    def test_a_self_alias_is_never_emitted(self) -> None:
        """A root ``Dockerfile``'s legacy id is its new id."""
        self.assertEqual(legacy_alias_by_path(["Dockerfile"]), {})
        self.assertEqual(
            dockerfile_node_id("Dockerfile"),
            legacy_dockerfile_node_id("Dockerfile"),
        )


if __name__ == "__main__":
    unittest.main()
