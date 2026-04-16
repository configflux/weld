"""Template: project-local weld strategy.

Copy this file to ``.weld/strategies/<name>.py`` and customize it for your
repository.  The ``extract()`` function is the only required entry point.

How it works:
  - ``wd discover`` resolves strategies by name from ``discover.yaml``.
  - Project-local strategies in ``.weld/strategies/`` take priority over
    bundled ones in ``weld/strategies/``.
  - Your ``extract()`` receives the repo root, the source entry from
    ``discover.yaml``, and a shared context dict.
  - Return a ``StrategyResult(nodes, edges, discovered_from)``.

Example ``discover.yaml`` entry for this template::

    sources:
      - strategy: my_strategy   # matches .weld/strategies/my_strategy.py
        glob: "*.txt"           # your custom config keys

Customize the logic below to extract whatever matters for your project --
config manifests, internal tool metadata, generated code registries, etc.
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._helpers import StrategyResult

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract nodes from files matching a glob pattern.

    This is a minimal working example.  Replace the body with logic that
    fits your repository.

    Parameters
    ----------
    root
        Absolute path to the repository root.
    source
        The source entry from ``discover.yaml``.  Any keys you add to
        the YAML entry are available here (e.g. ``source["glob"]``).
    context
        A mutable dict shared across all strategies in a single
        ``discover`` run.  Use it to pass data between strategies
        when needed.

    Returns
    -------
    StrategyResult
        A named tuple of ``(nodes, edges, discovered_from)``.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    # --- Read config from the source entry ----------------------------------
    pattern = source.get("glob", "")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    # --- Walk matching files ------------------------------------------------
    for filepath in sorted(root.glob(pattern)):
        if not filepath.is_file():
            continue
        rel = str(filepath.relative_to(root))
        discovered_from.append(rel)

        # Build a stable node ID from the relative path
        safe_name = rel.replace("/", "_").replace(".", "_")
        node_id = f"file:{safe_name}"

        nodes[node_id] = {
            "type": "file",
            "label": filepath.name,
            "props": {
                "file": rel,
                "source_strategy": "my_strategy",
                "authority": "canonical",
                "confidence": "definite",
            },
        }

    return StrategyResult(nodes, edges, discovered_from)
