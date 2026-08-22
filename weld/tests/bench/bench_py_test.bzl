"""``bench_py_test``: the local/no-sandbox py_test used across this package.

Every test in weld/tests/bench touches a real corpus, a real timing budget, or
real tree-sitter/libclang tooling, so most of them repeat the same two
attributes: ``local = True`` and ``tags = ["no-sandbox"]``. This macro
declares that invariant once instead of on ~20 near-identical py_test blocks
(bd x6uf), which is what pushed BUILD.bazel to the 400-line cap.

Callers that also belong to the serial wall-clock lane (ADR 0070) pass
``tags = ["benchmark"]``; ``no-sandbox`` is merged in rather than replacing
it, so the two tags coexist exactly as they did before this macro existed.
Every other py_test attribute (data, deps, size, timeout, args, env, ...)
passes through unchanged via ``**kwargs``.

One deliberate non-user: ``weld_public_bench_version_test`` in BUILD.bazel
stays a plain ``py_test``. It is hermetic (no corpus, no repo on disk) and
belongs in the fast loop, so it must not pick up ``local``/``no-sandbox``.
"""

load("@rules_python//python:defs.bzl", "py_test")

def bench_py_test(name, tags = [], local = True, **kwargs):
    """py_test defaulting local = True and tags += ["no-sandbox"]."""
    if "no-sandbox" not in tags:
        tags = tags + ["no-sandbox"]
    py_test(
        name = name,
        local = local,
        tags = tags,
        **kwargs
    )
