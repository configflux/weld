"""``wd prime`` at a root that federates and discovers nothing of its own.

A federated ``wd discover`` reads ``.weld/workspaces.yaml`` and the children's
graphs and resolves no source glob at the root, so a root whose only discovery
input is the registry has nothing of its own to discover and needs no
``discover.yaml``. Prime told it to run ``wd init`` anyway, and then nagged that
its graph was small::

    [ACTION] discover.yaml not found
             -> Run: wd init
    [INFO  ] Graph has only 1 node -- consider adding more sources to discover.yaml

Neither line can be acted on. ``wd init`` there re-scaffolds a stub config the
federated discover never resolves, and a root meta-graph holding one node per
child is the shape federation is *supposed* to produce -- so the only next step
prime offered was one that makes the setup worse.

ADR 0141 D3 is the decision these cases pin, one surface over from the doctor
half that bd 5038-lcq0c.5 landed: at a root holding ``workspaces.yaml``, an
absent ``discover.yaml`` is a note about how this root discovers, not a defect.
The two commands disagreeing about the same root was the whole of the field
report (v0.25.0 finding M3, bd 5038-8bq9z).

Three properties are asserted, and the last two are the load-bearing ones:

* the pure-federation root goes quiet, at **both** spellings
  :func:`weld.workspace_state.find_workspaces_yaml` accepts -- prime settles
  federation by asking that function rather than a detector of its own, since
  disagreeing with the read path about what a workspace root is *is* the
  finding;
* a plain repository is untouched. A carve-out reaching every project would
  tell each un-``wd init``-ed checkout it was fine, which is a worse bug than
  the one M3 reports;
* a root that federates **and** discovers keeps both lines. There the config
  exists and the root really does resolve sources, so "add more sources to
  discover.yaml" names a real file and real work.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.prime import prime

_WORKSPACES_YAML = """\
version: 1
children:
  - name: services-api
    path: services/api
"""

_DISCOVER_YAML = """\
sources:
  - glob: "src/**/*.py"
    type: file
    strategy: python_module
"""

# The node-count advisory, quoted on the half that carries the bad advice
# rather than on the whole sentence (which is free to be reworded).
_NODE_COUNT_NAG = "consider adding more sources to discover.yaml"


def _small_graph() -> str:
    """A graph small enough to trip the node-count advisory.

    The advisory fires below five nodes, and every case here uses this same
    graph -- so the federation cases and their plain-repository controls differ
    only in whether a registry is present, which is the variable under test.
    The node *contents* are deliberately absent: nothing here asserts what a
    root meta-graph holds, so writing one out by hand would only be a claim
    about federated discovery that this file is in no position to make.
    """
    return json.dumps({"meta": {"schema_version": 4}, "nodes": {}, "edges": []})


def _weld_project(root: Path, *, workspaces: str | None, discover: bool) -> None:
    """Materialise a ``.weld/`` project under *root*.

    *workspaces* is where the registry is written relative to *root* (``None``
    for a plain repository), so one builder covers both spellings
    ``find_workspaces_yaml`` accepts. ``file-index.json`` is written so the
    only next step prime can offer is the one under test.
    """
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True)
    (weld_dir / "graph.json").write_text(_small_graph(), encoding="utf-8")
    (weld_dir / "file-index.json").write_text("{}", encoding="utf-8")
    if workspaces is not None:
        (root / workspaces).write_text(_WORKSPACES_YAML, encoding="utf-8")
    if discover:
        (weld_dir / "discover.yaml").write_text(_DISCOVER_YAML, encoding="utf-8")


def _config_lines(output: str) -> list[str]:
    """Every status line prime renders *about* ``discover.yaml`` itself.

    The node-count advisory names the file too, but inside its message rather
    than straight after the tag -- anchoring on that boundary keeps the two
    apart, so a case can assert one without accidentally matching the other.
    """
    return [line for line in output.splitlines() if "] discover.yaml" in line]


def _next_steps(output: str) -> list[str]:
    """The commands prime lists under ``Next steps:``, or ``[]`` when it lists none."""
    if "Next steps:" not in output:
        return []
    block = output.split("Next steps:", 1)[1]
    steps: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        _, sep, command = line.partition(". ")
        steps.append(command if sep else line)
    return steps


class PrimeAtAPureFederationRoot(unittest.TestCase):
    """``workspaces.yaml`` present, ``discover.yaml`` absent -- the M3 shape."""

    def test_the_absent_config_is_not_an_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=False)

            output = prime(root)
            lines = _config_lines(output)

            self.assertEqual(len(lines), 1, output)
            self.assertNotIn("[ACTION]", lines[0])
            self.assertNotIn("wd init", _next_steps(output))

    def test_the_absent_config_says_why_instead_of_prescribing_wd_init(self) -> None:
        """Silence would leave the reader guessing whether to run ``wd init``.

        Asserted on the word the sentence is about, not the sentence, so the
        wording stays free to change -- but on that one line, so a stray
        "federates" elsewhere in the report cannot stand in for it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=False)

            lines = _config_lines(prime(root))

            self.assertEqual(len(lines), 1, f"{lines}")
            self.assertIn("[INFO  ]", lines[0])
            self.assertIn("federat", lines[0].lower())

    def test_no_node_count_nag_about_a_root_meta_graph(self) -> None:
        """One node per child is the intended shape, not a thin configuration."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=False)

            self.assertNotIn(_NODE_COUNT_NAG, prime(root))

    def test_prime_offers_no_next_steps_at_a_healthy_federation_root(self) -> None:
        """The verdict, not one row: nothing else picks up the slack.

        Asserting only that the two lines went quiet would still pass if a
        neighbouring check started prescribing ``wd init`` off the same absent
        file -- which is exactly what the reader would still meet.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=False)

            output = prime(root)

            self.assertEqual(_next_steps(output), [], output)
            self.assertIn("No actions needed", output)

    def test_the_registry_is_found_where_the_read_path_looks_for_it(self) -> None:
        """A top-level ``workspaces.yaml`` federates too, so prime agrees.

        ``find_workspaces_yaml`` accepts ``.weld/workspaces.yaml`` *and* a
        top-level ``workspaces.yaml``; every graph-backed read decides
        federation by asking it. Prime calling only one of the two a federation
        root would put the same disagreement back, one spelling over.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces="workspaces.yaml", discover=False)

            output = prime(root)
            lines = _config_lines(output)

            self.assertEqual(len(lines), 1, output)
            self.assertNotIn("[ACTION]", lines[0])
            self.assertNotIn(_NODE_COUNT_NAG, output)


class PrimeWhereTheCarveOutMustNotReach(unittest.TestCase):
    """The two shapes ADR 0141 D3 leaves exactly as they were."""

    def test_a_plain_repository_still_gets_the_action(self) -> None:
        """No registry, no config -- an uninitialised project, and still ``wd init``.

        The regression guard for the fix above: a carve-out that swallowed this
        case would report every un-``wd init``-ed checkout healthy.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=None, discover=False)

            output = prime(root)
            lines = _config_lines(output)

            self.assertEqual(len(lines), 1, output)
            self.assertIn("[ACTION] discover.yaml not found", lines[0])
            self.assertIn("wd init", _next_steps(output))

    def test_a_plain_repository_still_gets_the_node_count_nag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=None, discover=False)

            self.assertIn(_NODE_COUNT_NAG, prime(root))

    def test_a_root_that_federates_and_discovers_is_unchanged(self) -> None:
        """Both files present: the ordinary ``[OK]`` line with its source count.

        A workspace root may hold sources of its own (shared tooling, a
        top-level service); federation says nothing about that, and the note
        would be a lie there.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=True)

            lines = _config_lines(prime(root))

            self.assertEqual(len(lines), 1, f"{lines}")
            self.assertIn("[OK    ] discover.yaml exists (1 active source)", lines[0])

    def test_a_root_that_federates_and_discovers_keeps_the_node_count_nag(self) -> None:
        """There the advisory names a file that exists and sources really resolved."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=True)

            self.assertIn(_NODE_COUNT_NAG, prime(root))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
