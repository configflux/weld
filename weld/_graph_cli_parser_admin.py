"""Graph-administration subparsers: ``import`` / ``validate*`` / ``migrate``.

Split out of :mod:`weld._graph_cli_parser`, which had reached the 400-line
cap exactly, so the next one-line addition to any subparser broke the build
(bd 6osw hit this adding ``--no-refresh`` to ``stale``). These three are the
cohesive group to move: every other ``_add_*`` there registers a *read* or a
node/edge mutator, while these register the whole-graph administration
commands -- take a graph in, check one, or rewrite one wholesale under an
ADR. They also share the property that none of them is on the read path, so
nothing here is reachable from a ``wd query``.

The parser is still assembled in one place; :func:`weld._graph_cli_parser.build_parser`
calls into this module the same way it calls its local ``_add_*`` helpers.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["add_import_validate", "add_migrate"]


def add_import_validate(sub) -> None:
    """Register ``wd import`` / ``validate`` / ``validate-fragment``."""
    p_imp = sub.add_parser("import", help="Import/merge from file")
    p_imp.add_argument("file", type=Path, help="JSON file to import")
    sub.add_parser(
        "validate", help="Validate graph against the metadata contract",
    )
    p_vf = sub.add_parser(
        "validate-fragment", help="Validate a JSON fragment",
    )
    p_vf.add_argument("file", type=Path, help="JSON fragment file")
    p_vf.add_argument(
        "--source-label", default="fragment", help="Diagnostic label",
    )
    p_vf.add_argument(
        "--allow-dangling", action="store_true", help="Skip ref checks",
    )


def add_migrate(sub) -> None:
    """Register ``wd migrate`` -- ADR-driven graph migrations.

    The first migration shipped under this command is
    ``--add-confidence`` (ADR 0050): backfill missing ``confidence``
    props on legacy graphs by classifying each edge's
    ``source_strategy`` against the static map in
    :mod:`weld._confidence_defaults`.
    """
    p = sub.add_parser(
        "migrate",
        help=(
            "Apply ADR-driven graph migrations "
            "(currently --add-confidence)."
        ),
    )
    p.add_argument(
        "--add-confidence", action="store_true",
        help=(
            "Backfill missing edge confidence props using the "
            "source_strategy -> confidence map. "
            "Strategies not in the map default to 'speculative'."
        ),
    )
