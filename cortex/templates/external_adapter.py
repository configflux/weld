#!/usr/bin/env python3
"""Template: external adapter command for Cortex discovery.

Copy this file into your repository and wire it into ``discover.yaml``
as an ``external_json`` source::

    sources:
      - strategy: external_json
        command: "python3 .cortex/adapters/my_adapter.py"

How it works:
  - ``cortex discover`` runs the command with ``cwd`` set to the repo root.
  - The command must print a JSON object to stdout with this shape::

        {
            "nodes": { "<id>": { "type": "...", "label": "...", "props": {} }, ... },
            "edges": [ { "from": "...", "to": "...", "type": "...", "props": {} }, ... ],
            "discovered_from": [ "path/to/source", ... ]
        }

  - The output is validated against the cortex fragment contract before
    being merged into the graph.  Invalid fragments are rejected.

Customize the ``build_fragment()`` function below to extract whatever
your project needs -- build targets, compiler output, CI metadata, etc.
"""

from __future__ import annotations

import json
import sys

def build_fragment() -> dict:
    """Build a cortex fragment dict.

    Replace this with your real extraction logic.  The example below
    emits a single tool node that could represent a custom build
    script, linter, or analysis tool in your repo.

    Valid node types (see ``cortex/contract.py`` or ``cortex validate-fragment``):
        service, package, entity, stage, concept, doc, route, contract, enum, file,
        dockerfile, compose, agent, command, tool, workflow, test-suite, config,
        policy, runbook, build-target, test-target, boundary, entrypoint

    Valid edge types:
        contains, depends_on, produces, consumes, implements, documents, relates_to,
        responds_with, accepts, builds, orchestrates, invokes, configures, tests,
        represents, feeds_into, enforces, verifies, exposes
    """
    nodes = {
        "tool:my-custom-tool": {
            "type": "tool",
            "label": "My Custom Tool",
            "props": {
                "source_strategy": "external_json",
                "authority": "external",
                "confidence": "definite",
                "file": "tools/my-custom-tool.sh",
            },
        },
    }

    edges: list[dict] = []

    discovered_from = ["tools/my-custom-tool.sh"]

    return {
        "nodes": nodes,
        "edges": edges,
        "discovered_from": discovered_from,
    }

def main() -> None:
    """Entry point: emit the fragment as JSON to stdout."""
    fragment = build_fragment()
    json.dump(fragment, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

if __name__ == "__main__":
    main()
