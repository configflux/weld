"""Adapter implementations for ``wd bench --public`` (ADR 0059).

Each adapter is a thin wrapper that takes a :class:`PublicTask` and a
repository root and returns an :class:`AdapterResult`. Adapters MUST NOT
raise on missing external binaries -- they report ``status="unavailable"``
instead so the bench keeps running on the remaining adapters.

Modules:

  - :mod:`weld.bench.adapters.weld`         -- weld retrieval stack
  - :mod:`weld.bench.adapters.grep`         -- grep baseline
  - :mod:`weld.bench.adapters.tree_sitter`  -- ``tree-sitter-cli`` wrapper
  - :mod:`weld.bench.adapters.graphify`     -- ``graphify`` external CLI
"""
