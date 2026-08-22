"""Argument parser for ``wd warm`` (ADR 0067).

Kept separate from :mod:`weld.warm` so the orchestration module stays focused
and under the line-count cap, mirroring the ``discover`` / ``_discover_cli``
split -- including that split's zero-imports-back shape (ADR 0130): this
module takes no dependency on :mod:`weld.warm` at all. ``build_parser``
receives its two warm-specific defaults as parameters from its one caller
(:func:`weld.warm.main`) instead of importing them, which is what keeps this
a one-directional ``warm -> _warm_cli`` edge rather than the ``depends_on``
cycle ADR 0130 catalogued and broke.
"""

from __future__ import annotations

import argparse


def build_parser(
    *, default_max_ancestors: int, env_source: str
) -> argparse.ArgumentParser:
    """Build the ``wd warm`` argument parser.

    *default_max_ancestors* and *env_source* are :mod:`weld.warm`'s own
    ``DEFAULT_MAX_ANCESTORS``/``ENV_SOURCE`` constants, passed in by the
    caller rather than imported here (ADR 0130).
    """
    parser = argparse.ArgumentParser(
        prog="wd warm",
        description=(
            "Fetch a CI-published graph artifact for the nearest-ancestor "
            "commit and refresh it to HEAD; fall back to a full local discover "
            "when no artifact is reachable."
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
            f"{env_source} environment variable."
        ),
    )
    parser.add_argument(
        "--max-ancestors",
        type=int,
        default=default_max_ancestors,
        metavar="N",
        help=(
            "How many commits (HEAD plus ancestors) to probe for a published "
            f"artifact (default: {default_max_ancestors})."
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
