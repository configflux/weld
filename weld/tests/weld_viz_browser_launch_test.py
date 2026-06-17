"""Tests for the ``wd viz`` browser-launch deprecation-noise guard.

Regression for DEP0169: on VS Code remote / Codespaces the ``$BROWSER``
handler shells out to a Node CLI (``code … --openExternal``) that still calls
the legacy ``url.parse()`` and prints a ``[DEP0169]`` deprecation warning.
Python's ``webbrowser`` launches that handler as a ``GenericBrowser`` whose
stderr is inherited by ``wd viz``, so the launcher's warning leaks into the
visualizer's terminal output. ``_open_browser_quietly`` scopes
``NODE_OPTIONS=--no-deprecation`` around the launch so Node-based launchers
stay quiet, restoring any prior value afterward.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from weld.viz import server as viz_server


class VizBrowserLaunchTest(unittest.TestCase):
    def _seen_node_options(self) -> str | None:
        captured: dict[str, str | None] = {}

        def _fake_open(url: str) -> bool:
            captured["value"] = os.environ.get("NODE_OPTIONS")
            return True

        with patch.object(viz_server.webbrowser, "open", _fake_open):
            viz_server._open_browser_quietly("http://127.0.0.1:0/")
        return captured["value"]

    def test_injects_no_deprecation_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NODE_OPTIONS", None)
            self.assertEqual(self._seen_node_options(), "--no-deprecation")
            self.assertNotIn("NODE_OPTIONS", os.environ)

    def test_preserves_and_restores_existing_node_options(self) -> None:
        with patch.dict(os.environ, {"NODE_OPTIONS": "--max-old-space-size=512"}):
            seen = self._seen_node_options()
            self.assertEqual(seen, "--max-old-space-size=512 --no-deprecation")
            self.assertEqual(os.environ["NODE_OPTIONS"], "--max-old-space-size=512")


if __name__ == "__main__":
    unittest.main()
