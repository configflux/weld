"""Static-asset assertions for the visualizer URL-hash state (bd h6z0.4).

The viz UI serializes the current view into ``location.hash`` so a refresh
or copy-paste of the URL restores an identical view. Round-tripping is the
hard acceptance criterion; lexical assertions on ``weld/viz/static/app.js``
guard the contract surface that subsequent features (session history,
named bookmarks) build on.
"""

from __future__ import annotations

import unittest
from importlib.resources import files


def _read_static(name: str) -> str:
    return files("weld.viz").joinpath("static", name).read_text(encoding="utf-8")


class VizHashStateTest(unittest.TestCase):
    """Pin the URL-hash state contract on the shipped static assets."""

    def test_app_js_defines_hash_state_helpers(self) -> None:
        js = _read_static("app.js")
        # Two pure helpers form the round-trip surface: state <-> hash.
        self.assertIn("stateToHash", js)
        self.assertIn("hashToState", js)
        # The hash is read from and written to ``location.hash`` (not search).
        self.assertIn("location.hash", js)

    def test_app_js_persists_documented_state_keys(self) -> None:
        js = _read_static("app.js")
        # Every key called out in the acceptance criteria must appear by name
        # in the file -- this is the contract surface other features (e.g.
        # session history, named bookmarks) will build on.
        for key in (
            "scope",
            "q",
            "node_id",
            "depth",
            "node_types",
            "edge_types",
            "hide_origins",
            "limit",
            "pathA",
            "pathB",
        ):
            self.assertIn(key, js, f"state key {key!r} missing from app.js")

    def test_app_js_rehydrates_hash_before_first_loadSlice(self) -> None:
        js = _read_static("app.js")
        # The rehydration function must run before the first loadSlice() call
        # inside init(); lexically, the call to apply the hash state must
        # appear before the first loadSlice in the init body.
        init_start = js.index("async function init()")
        init_end = js.index("\n}", init_start)
        init_body = js[init_start:init_end]
        # Rehydration call uses the documented helper name.
        self.assertIn("hashToState", init_body)
        # Order check: hash rehydration comes before the first loadSlice call.
        hydrate_pos = init_body.index("hashToState")
        loadslice_pos = init_body.index("loadSlice")
        self.assertLess(
            hydrate_pos,
            loadslice_pos,
            "init() must read location.hash before the first loadSlice()",
        )

    def test_app_js_updates_hash_on_state_change(self) -> None:
        js = _read_static("app.js")
        # A single updater is invoked from every state-mutation site so the
        # URL stays in sync with the live view. The function name is part of
        # the contract; subsequent features (history, bookmarks) call it too.
        self.assertIn("updateHash", js)
        # The updater writes to location.hash via the documented helper.
        # We assert the writer references location.hash so a refactor cannot
        # silently move state to localStorage / sessionStorage.
        update_start = js.index("function updateHash")
        update_end = js.index("\n}", update_start)
        update_body = js[update_start:update_end]
        self.assertIn("location.hash", update_body)
        # Hash updates fire from the same handlers that trigger loadSlice so
        # the URL stays current. After bd h6z0.7 the Apply button is gone:
        # every filter control routes through the same debounced reload, so
        # the canonical hook for this contract is ``scheduleFilterReload``.
        self.assertIn("scheduleFilterReload", js)


if __name__ == "__main__":
    unittest.main()
