"""Doctor check for the discovery config, ``.weld/discover.yaml``.

Carved out of :mod:`weld.doctor` to keep the dispatcher under the 400-line
CLAUDE.md cap, matching every other doctor concern already split into its own
file -- callers of ``weld.doctor.doctor()`` see the identical ``[Config]``
output.

The absent-config verdict is the part with a decision behind it (ADR 0141 D3).
A workspace root that only federates has nothing of its own to discover: a
federated ``wd discover`` reads ``.weld/workspaces.yaml`` and the children's
graphs, and resolves no source glob at the root. An absent ``discover.yaml``
there does not say the root is unconfigured -- it says the root's discovery
input is the registry. Grading it a ``fail`` made the healthiest possible
workspace root report ``Status: errors`` and exit 1 while ``wd workspace
status`` showed every child green -- and agents gate on the exit code, so a
correct setup read as a broken one (field-eval v0.25.0 finding M3). It is a
``note`` there instead: visible, because a root that *meant* to discover still
wants to know, and never fatal.

Federation is decided by :func:`weld.workspace_state.find_workspaces_yaml` --
the same question every graph-backed read asks (``wd brief``, the MCP
readiness guard, the ``wd find`` precondition). Doctor disagreeing with the
read path about what a federation root is *is* the finding, so a second
detector spelled locally here would put it straight back, one file over. A
root that also discovers, and a plain repository, keep the behaviour they had.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from weld._yaml import parse_yaml
from weld.workspace_state import find_workspaces_yaml


def check_discover_yaml(
    weld_dir: Path, root: Path, result_cls: type[Any]
) -> list[Any]:
    """Report the discovery config at *weld_dir*, or its considered absence."""
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        if find_workspaces_yaml(root) is not None:
            return [
                result_cls(
                    "note",
                    ".weld/discover.yaml not found -- this root federates; it "
                    "has no sources of its own to discover",
                    "Config",
                )
            ]
        return [result_cls("fail", ".weld/discover.yaml not found", "Config")]
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
        count = len(sources) if isinstance(sources, list) else 0
    except Exception:
        count = 0
    suffix = "entries" if count != 1 else "entry"
    return [
        result_cls(
            "ok",
            f".weld/discover.yaml found ({count} source {suffix})",
            "Config",
        )
    ]


__all__ = ["check_discover_yaml"]
