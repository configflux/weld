"""Argument parser for ``wd warm`` (ADR 0067).

Kept separate from :mod:`weld.warm` so the orchestration module stays focused
and under the line-count cap, mirroring the ``discover`` / ``_discover_cli``
split.
"""

from __future__ import annotations

import argparse

from weld.warm import DEFAULT_MAX_ANCESTORS, ENV_SOURCE


def build_parser() -> argparse.ArgumentParser:
    """Build the ``wd warm`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="wd warm",
        description=(
            "Fetch a CI-published graph artifact for the nearest-ancestor "
            "commit and refresh it to HEAD; fall back to a full local discover "
            "when no artifact is reachable (ADR 0067)."
        ),
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Project root directory (default: .)",
    )
    parser.add_argument(
        "--source",
        default=None,
        metavar="URL_OR_DIR",
        help=(
            "Artifact source: an https:// URL template containing '{sha}', a "
            "file:// URL/template, or a local directory laid out as "
            "<dir>/<sha>/graph.json. Defaults to the "
            f"{ENV_SOURCE} environment variable."
        ),
    )
    parser.add_argument(
        "--max-ancestors",
        type=int,
        default=DEFAULT_MAX_ANCESTORS,
        metavar="N",
        help=(
            "How many commits (HEAD plus ancestors) to probe for a published "
            f"artifact (default: {DEFAULT_MAX_ANCESTORS})."
        ),
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help=(
            "Do not run a local discover when no artifact is landed; leave any "
            "existing graph untouched and report the miss."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the warm result as JSON instead of a human-readable line.",
    )
    return parser
