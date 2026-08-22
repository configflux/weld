"""``discovery-state.json`` carries claims about the inventory, not a clock.

ADR 0110 made Mode B commit this file beside the graph it explains. It was
still stamping ``created_at`` on every ``save_state``, so a ``wd discover``
that found nothing changed still produced a commit-worthy diff -- one line, in
a mode whose whole complaint is diff noise (bd lrfu).

The field is gone rather than preserved. ADR 0065 had already settled where a
value like that belongs: the volatile per-run fields live in the *gitignored*
``.weld/graph-meta.json`` sidecar -- which is what makes two discover runs at
one commit byte-identical -- and that sidecar already carries the same wall
clock as ``updated_at``, written by the same run. Nothing read the copy here.

What this pins:

1. The write path stamps no clock, so two saves of equal content are equal
   bytes. That is the property the issue actually wants; asserting only "no
   ``created_at`` key" would pass again the day some other timestamp arrives.
2. A state file written by an older weld -- ``created_at`` present -- still
   loads and still serves as an incremental basis. Dropping the key is
   deliberately *not* a ``STATE_VERSION`` bump, and a bump is exactly what a
   careless "unknown field" reading would amount to: every existing repository
   would pay a full re-discovery to buy nothing.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.discovery_state import (
    STATE_FILENAME,
    STATE_VERSION,
    DiscoveryState,
    diff_state,
    load_state,
    save_state,
)

_HASHES = {"src/a.py": "sha256:aaa", "src/b.py": "sha256:bbb"}


def _state_path(root: Path) -> Path:
    return root / ".weld" / STATE_FILENAME


class InventoryCarriesNoClockTest(unittest.TestCase):
    def test_to_dict_has_no_created_at(self) -> None:
        self.assertNotIn("created_at", DiscoveryState(files=dict(_HASHES)).to_dict())

    def test_two_saves_of_equal_content_write_equal_bytes(self) -> None:
        """The no-change discover, reduced to its write path."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_state(root, DiscoveryState(files=dict(_HASHES)))
            first = _state_path(root).read_text(encoding="utf-8")
            save_state(root, DiscoveryState(files=dict(_HASHES)))
            second = _state_path(root).read_text(encoding="utf-8")
        self.assertEqual(
            first, second,
            "a second save of the same inventory must not change the file: "
            "under Mode B this file is tracked, so any per-run stamp is a "
            "commit-worthy diff for no content change",
        )

    def test_a_changed_inventory_still_changes_the_file(self) -> None:
        """The other half: byte-stability must not come from writing nothing."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_state(root, DiscoveryState(files=dict(_HASHES)))
            before = _state_path(root).read_text(encoding="utf-8")
            save_state(root, DiscoveryState(files={**_HASHES, "src/c.py": "sha256:ccc"}))
            after = _state_path(root).read_text(encoding="utf-8")
        self.assertNotEqual(before, after)
        self.assertIn("src/c.py", after)

    def test_a_legacy_state_still_loads_as_an_incremental_basis(self) -> None:
        """A file an older weld wrote keeps working -- no forced full pass."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _state_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({
                    "version": STATE_VERSION,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "files": dict(_HASHES),
                    "files_with_no_nodes": [],
                    "graph_published": True,
                }) + "\n",
                encoding="utf-8",
            )
            loaded = load_state(root)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.files, _HASHES)
            # The point of loading it at all: an unchanged tree is still clean,
            # so the run stays incremental instead of re-extracting everything.
            self.assertFalse(diff_state(loaded, dict(_HASHES)).has_changes)

            # Re-saving it drops the legacy key rather than carrying it forward.
            save_state(root, loaded)
            self.assertNotIn("created_at", json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
