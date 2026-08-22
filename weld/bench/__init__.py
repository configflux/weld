"""Weld benchmark harness: token cost and first-context quality.

Two benchmark dimensions:

**Token cost** (runner module)
  Compares the token cost of three retrieval modes for a fixed set of agent
  prompts:
    1. ``grep`` baseline -- bytes an unaided agent would land in context.
    2. ``weld`` CLI -- the JSON stdout of ``wd query``/``wd brief``/
       ``wd callers`` invoked via in-process helpers.
    3. ``weld`` MCP -- the structured ``dict`` returned by
       :func:`weld.mcp_server.dispatch`, JSON-serialized.

**First-context quality** (quality module)
  Measures whether ``wd brief`` and ``wd trace`` return relevant,
  well-bucketed results: bucket hit rate, label recall, and token budget
  compliance.

Both harnesses are **on demand**, not CI gates.

This package is deliberately import-free (ADR 0099, bd 4g0d). ``python -m
weld.bench.synthetic_large_repo`` and ``python -m weld.bench`` (the
``bazel run //weld/bench:bench`` entry) each carry a launch-path guard that
only works because importing this package runs no code of its own --
``python -m pkg.mod`` imports ``pkg`` first, so anything this file imported
would resolve against the launch directory before either guard could run.
Import :mod:`weld.bench.runner`, :mod:`weld.bench.quality`, or
:mod:`weld.bench.bench_cli` directly instead of re-exporting them here --
``weld/cli.py``'s ``wd bench`` dispatch already does.
"""
