"""Node.js / Next.js readiness corpus (bd lrnx1.1, ADR 0142).

An evaluator-style pass over a realistic Node workspace -- an npm-workspaces
monorepo with a Next.js app-router app, an Express service in TypeScript and
legacy CommonJS, and a shared package behind a barrel -- found eight
out-of-box gaps. ADR 0142 D7 puts all eight in as red probes through the real
CLI *before* any fix, so each fix flips its own marker and cannot be called
done without one, and pins pass-today assurance probes beside them so the next
silent coverage loss trips a gate instead of a customer.

Five targets, four subjects:

* `weld_node_eval_init_e2e_test` -- gap G1: `wd init` must wire the dialects
  it counted and the framework it named. Its own module because it is the only
  one that reads the config the product *generated*; every other probe hand-
  wires first, so that it is red for its own gap rather than for this one.
* `weld_node_eval_symbols_e2e_test` -- gaps G4/G5/G6: what a file contributes
  (a default-exported component, a barrel's re-exports, JavaScript at all).
* `weld_node_eval_resolution_e2e_test` -- gaps G2/G3/G7: what the connections
  answer (who calls me, where does this first-party name live, what routes
  does this app expose).
* `weld_node_eval_polyrepo_e2e_test` -- gap G8: npm in the cross-repo package
  graph. A second, two-repo workspace, so it is its own module.
* `weld_node_eval_probe_inventory_test` -- the guard on all four: one probe
  per gap, none skipped, each naming its gap and owning bd issue, and every
  module still carrying a pass-today probe. Structural and subprocess-free.

**Every target is hermetic and untagged, and runs in the fast loop.** That is
worth stating plainly, because ADR 0142 records that no CI lane had ever run a
real TypeScript grammar and the natural conclusion is that this corpus needs
an ambient lane. It does not: `tree-sitter`, `tree-sitter-typescript` and
`tree-sitter-javascript` are all in `third_party/python/requirements_lock.txt`
and reach a `py_test` as ordinary deps, and rules_python puts them on
`PYTHONPATH`, so the `python -m weld` subprocesses inherit them. The grammars
were always available to any target that asked; no target asked. An ambient
lane with a self-skip would have been strictly worse here -- a skip is how a
lane comes to assert nothing, which is the exact failure this corpus exists to
end. `_node_eval_e2e_harness.assert_grammars_available` therefore fails loudly
instead of skipping, and `WELD_NODE_EVAL_PYTHON` lets a by-hand run point at a
grammar-capable interpreter.

Runtime budget: each E2E target materialises one workspace and bootstraps it
(one `wd init` + one `wd discover`; the polyrepo, two of each plus a federated
root discover), then runs its probes. They are split by subject rather than
merged partly for that -- four modest targets, well inside the 10 s fast-loop
ceiling, where one merged target would pay for every fixture at once.
"""

load("@rules_python//python:defs.bzl", "py_test")

#: The materialiser, its file bodies, the CLI harness and the shared marker
#: contract ride in `srcs` (the `_field_eval_*` pattern): they are test data,
#: not a surface anything imports outside these targets.
_FIXTURE = [
    "_node_eval_corpus.py",
    "_node_eval_corpus_sources.py",
    "_node_eval_e2e_harness.py",
    "_probe_markers.py",
]

_PROBE_MODULES = [
    "weld_node_eval_init_e2e_test",
    "weld_node_eval_polyrepo_e2e_test",
    "weld_node_eval_resolution_e2e_test",
    "weld_node_eval_symbols_e2e_test",
]

_DEPS = [
    ":graph_invariants_lib",
    "//weld:runtime",
    "//weld:workspace",
    "//weld:yaml",
    "//weld/cross_repo",
    "//weld/strategies",
    "//weld/strategies:helpers",
]

#: The real grammars, which is the whole point of the corpus. Declared on the
#: E2E targets only: the inventory guard imports the probe modules without
#: running them, so it needs no grammar and should not wait on one.
_GRAMMARS = [
    "@pypi//tree_sitter",
    "@pypi//tree_sitter_javascript",
    "@pypi//tree_sitter_typescript",
]

def node_eval_tests():
    # data: these launch the real `python -m weld` entry point out of the
    # runfiles tree (the `weld_cli_launch_path_test` pattern), so the module
    # entrypoint has to be there as a regular package, not synthesised.
    [py_test(
        name = _name,
        srcs = _FIXTURE + [_name + ".py"],
        data = ["//weld:module_entrypoint"],
        deps = _DEPS + _GRAMMARS,
        env = {"PYTHONHASHSEED": "0"},
    ) for _name in _PROBE_MODULES]

    py_test(
        name = "weld_node_eval_probe_inventory_test",
        srcs = _FIXTURE + [
            _name + ".py"
            for _name in _PROBE_MODULES
        ] + ["weld_node_eval_probe_inventory_test.py"],
        deps = _DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )
