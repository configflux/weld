"""Strategy: Bazel BUILD file extraction for build and test targets.

Parses BUILD.bazel and BUILD files to extract build-target and test-target
nodes.  Recognizes common Bazel rule types (py_library, py_binary, py_test,
sh_test, sh_binary, etc.) and models them as first-class graph objects.

``load()`` is resolved here (ADR 0044 amendment). A BUILD file's loads are read
before its targets are evaluated, because both kinds of loaded symbol change
what the targets say: a constant supplies a real ``srcs`` list, and a
zero-argument macro call declares real targets. Every ``.bzl`` read is appended
to ``discovered_from``, so editing a macro marks the graph source-stale instead
of letting the targets it declares drift from the tree (bd akwh).

``deps`` entries naming an external workspace (``@pypi//tree_sitter_cpp``) mint
an ``external-dep`` node and a ``depends_on`` edge instead of being dropped
(ADR 0121) -- the declared-but-unanalyzed dependency a wheel-carrying test
silently inherited from the host user site otherwise had no graph trace at
all (bd srzy, bd c42b).

A macro call that passes arguments is resolved too (ADR 0123): call-site
positional and keyword arguments bind onto the macro's parameters, including
a trailing ``**kwargs`` splat, before its body is walked -- so
``bench_py_test(name = "...", srcs = [...], deps = [...])`` (22 call sites in
``weld/tests/bench/BUILD.bazel``) mints real targets instead of the nothing a
parameterized call yielded before. The external-dep resolution above composes
with this automatically: it reads the macro-expanded ``deps`` list, not the
mechanism that produced it.

Every edge emitted here carries ADR 0074's ``props.provenance.file``, set to
the BUILD file that declared it (:func:`weld.strategies._bazel_labels.edge_props`).
This strategy's edges all cross out of the BUILD file into something another
source entry owns, which is precisely the shape the incremental purge drops
when it cannot attribute an edge to a producing file (bd cpkp).

``native.glob(...)`` inside a macro body now resolves too (bd x9lg / ADR 0044
amendment), bound to the CALLING package's directory like every other
macro-attributed fact here, never the ``.bzl``'s own.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id
from weld._rel_path import rel_to_root
from weld.strategies._bazel_labels import (
    data_edges,
    edge_props,
    resolve_dep_label,
    resolve_external_dep_label,
    resolve_src_labels,
)
from weld.strategies._bazel_glob import glob_bindings
from weld.strategies._bazel_loads import resolve_build_loads, zero_arg_calls
from weld.strategies._bazel_macro_args import bind_macro_call, param_macro_calls
from weld.strategies._bazel_nodes import bzl_node, external_dep_node
from weld.strategies._bazel_starlark import (
    module_bindings,
    parse_module,
    parse_targets,
    targets_in,
)
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._strategy_failure import note_strategy_failure

# Bazel rule patterns: maps rule name -> (node_type, role)
_BUILD_RULES: dict[str, tuple[str, str]] = {
    "py_library": ("build-target", "build"),
    "py_binary": ("build-target", "build"),
    "py_test": ("test-target", "test"),
    "sh_test": ("test-target", "test"),
    "sh_binary": ("build-target", "build"),
    "sh_library": ("build-target", "build"),
    "genrule": ("build-target", "build"),
    "filegroup": ("build-target", "build"),
    "exports_files": ("build-target", "build"),
}

def _parse_build_file(text: str) -> list[dict]:
    """Parse a BUILD file and return a list of target dicts.

    Each dict has: rule, name, srcs (list), deps (list), data (list).

    Convenience wrapper that flattens the unparseable case to ``[]`` and
    resolves no loads -- it has no filesystem to read them from.
    :func:`extract` drives the parser directly instead, because it must tell
    "this file declared nothing" from "weld could not read this file" in order
    to record the second as a strategy failure (bd hch4).
    """
    return parse_targets(text, _BUILD_RULES) or []


def _bzl_reader(root: Path):
    """Return a repo-relative text reader bounded to *root*.

    ``resolve_bzl_label`` already rejects ``..`` segments and absolute labels,
    so this is the second half of the same containment check: a *symlink*
    inside the repo can point outside it, and a BUILD file is repository input
    reaching a filesystem read. An unreadable, absent, or escaping path is
    ``None``, which costs the load's bindings and nothing else.
    """
    resolved_root = root.resolve()

    def read(rel_path: str) -> str | None:
        candidate = (root / rel_path).resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            return None
        try:
            if not candidate.is_file():
                return None
            return candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    return read


def _expand_loads(
    tree, pkg_dir: str, read, cache: dict, glob_env: dict | None = None
) -> tuple[list[dict] | None, list[str]]:
    """Return ``(targets, bzl_paths)`` for a parsed BUILD file.

    Order matters and is the whole point: loads bind first, then the BUILD
    file's own module-level assignments fold over them, and only then are its
    targets evaluated. Macro call sites are expanded last, each under the
    bindings of the ``.bzl`` that *defined* it but emitted into the package
    that *called* it -- the caller places every target it gets back.

    ``None`` targets preserves the ADR 0105 failure channel: the file parsed
    but the evaluator could not finish it, which is a failure to record and
    re-run, not a file that declared nothing (bd hch4). ``bzl_paths`` is
    returned either way, because those files *were* read.

    Zero-parameter and parameterized macros expand through two separate
    loops (ADR 0123): a zero-argument call is deduplicated by name because
    every call to the same zero-parameter macro produces identical output,
    while a parameterized call is not -- ``bench_py_test(name = "a", ...)``
    and ``bench_py_test(name = "b", ...)`` are two different declarations and
    both must expand. ``bind_macro_call`` resolves each parameterized call's
    arguments against *env* (this BUILD file's own scope, where the call
    expression was written) and the macro's own module bindings (its
    defaults' scope); a call shape it declines to bind (``None``) simply
    contributes no targets, same as any other unevaluatable construct here.

    *glob_env* is merged in at each expansion site, not into the cached
    ``Macro.bindings`` (shared across every calling package within one run),
    so ``native.glob(...)`` in a macro body resolves against the CALLING
    package's files (bd x9lg) -- the package its rule-call targets already go to.
    """
    loaded = resolve_build_loads(tree, pkg_dir, read, cache)
    env = module_bindings(tree, {**loaded.bindings, **(glob_env or {})})

    targets = targets_in(tree, _BUILD_RULES, env, loaded.origins)
    if targets is None:
        return None, loaded.read_paths

    for macro_name in zero_arg_calls(tree, loaded.macros):
        macro = loaded.macros[macro_name]
        macro_env = {**macro.bindings, **(glob_env or {})}
        expanded = targets_in(macro.node, _BUILD_RULES, macro_env, macro.origins)
        for target in expanded or []:
            # The macro body is the declaration, so its own file is always an
            # origin -- ``bzl`` from the walk only catches the *other* files it
            # reads a name from.
            target["bzl"] = sorted(set(target["bzl"]) | {macro.path})
            targets.append(target)

    for macro_name, call in param_macro_calls(tree, loaded.param_macros):
        macro = loaded.param_macros[macro_name]
        bound = bind_macro_call(macro.node, call, env, macro.bindings)
        if bound is None:
            continue
        bound = {**bound, **(glob_env or {})}
        expanded = targets_in(macro.node, _BUILD_RULES, bound, macro.origins)
        for target in expanded or []:
            target["bzl"] = sorted(set(target["bzl"]) | {macro.path})
            targets.append(target)
    return targets, loaded.read_paths


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Bazel build and test target nodes from BUILD files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []
    pending_data: list[tuple[str, str, str, str]] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    matched = resolve_glob(root, pattern, excludes)
    read_bzl = _bzl_reader(root)
    # One cache per run: BUILD files share their .bzl files heavily, and it
    # is what bounds a hostile fan-out to one read per file (ADR 0109).
    load_cache: dict = {}
    # Likewise one directory listing per package per run (bd mhn7).
    glob_cache: dict = {}

    for build_file in matched:
        if not build_file.is_file():
            continue

        rel_path = rel_to_root(build_file, root)
        discovered_from.append(rel_path)

        try:
            text = build_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            note_strategy_failure(context, [rel_path])
            continue

        # Derive the Bazel package path (directory relative to root)
        pkg_dir = build_file.parent
        if pkg_dir == root:
            pkg_label = "//"
            pkg_rel = ""
        else:
            # POSIX form: this feeds ``.bzl`` label resolution, and the paths
            # that come back become ``props.file`` and ``discovered_from``
            # entries, which are graph-vocabulary paths and POSIX by contract
            # (:mod:`weld._rel_path`).
            pkg_rel = pkg_dir.relative_to(root).as_posix()
            pkg_label = "//" + pkg_rel

        tree = parse_module(text)
        if tree is None:
            # A BUILD file weld cannot parse is a failure, not this strategy
            # deciding no target lives there (bd hch4). Recording it as a
            # decision would exempt it from the ADR 0008 per-file repair for
            # good, so the day the parser grows to accept the file, nothing
            # would ever re-read it.
            note_strategy_failure(context, [rel_path])
            continue

        targets, bzl_paths = _expand_loads(
            tree, pkg_rel, read_bzl, load_cache, glob_bindings(pkg_dir, glob_cache),
        )
        # Provenance before the parse verdict: these files were read, so a
        # later edit to one must re-run this strategy whether or not this
        # BUILD file finished evaluating.
        discovered_from.extend(bzl_paths)
        for bzl_rel in bzl_paths:
            nodes.setdefault(file_id(bzl_rel), bzl_node(bzl_rel))
        if targets is None:
            note_strategy_failure(context, [rel_path])
            continue

        for target in targets:
            rule = target["rule"]
            name = target["name"]
            node_type, role = _BUILD_RULES[rule]

            nid = f"{node_type}:{pkg_label}:{name}"
            bazel_label = f"{pkg_label}:{name}"

            # Resolve labels deterministically.  Sort inputs first so
            # downstream emit order is independent of BUILD file order.
            unresolved: list[str] = []
            srcs_targets: list[str] = []
            for src in sorted(target["srcs"]):
                spellings = resolve_src_labels(src, pkg_label)
                if not spellings:
                    unresolved.append(src)
                else:
                    srcs_targets.extend(spellings)

            deps_targets: list[str] = []
            # (repo, name), keyed by node id -- one entry per distinct
            # external dependency this target declares (ADR 0121). A dict
            # rather than a list because the node still needs minting once
            # per id, not once per mention.
            external_deps: dict[str, tuple[str, str]] = {}
            for dep in sorted(target["deps"]):
                resolved = resolve_dep_label(dep, pkg_label)
                if resolved is not None:
                    deps_targets.append(resolved)
                    continue
                external = resolve_external_dep_label(dep)
                if external is None:
                    unresolved.append(dep)
                else:
                    ext_id, ext_repo, ext_name = external
                    external_deps[ext_id] = (ext_repo, ext_name)

            # ``data`` is deferred to a second pass. A label like
            # ``//weld:pyproject.toml`` and one like ``//weld/tests:fixture_files``
            # are the same shape but different things -- a source file and a
            # filegroup target -- and only the finished target set can tell
            # them apart. Resolving inline would have to guess (bd oj3m).
            for entry in sorted(set(target["data"])):
                pending_data.append((nid, entry, pkg_label, rel_path))

            unresolved_sorted = sorted(set(unresolved))
            nodes[nid] = {
                "type": node_type,
                "label": bazel_label,
                "props": {
                    "file": rel_path,
                    "bazel_label": bazel_label,
                    "rule": rule,
                    # ADR 0105: the rule kind is the most natural word for
                    # "show me the libraries", and ``props.rule`` is on no
                    # query channel -- ``wd query "py_library"`` matched
                    # nothing against a graph full of them (bd vpzh).
                    # ``keywords`` is the generic bag both read paths index,
                    # so declaring it here needs no core edit.
                    #
                    # The rule alone: ``node_type`` is the node ID's own
                    # prefix, so ``_split_field(nid)`` already indexes
                    # "build"/"target" for every one of these nodes. Adding it
                    # here would buy no reachability and spend index on the
                    # query hot path, which is the budget that keeps this bag
                    # short enough to afford at all.
                    "keywords": [rule],
                    "source_strategy": "bazel",
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": [role],
                    "unresolved_labels": unresolved_sorted,
                    "unresolved_labels_dropped": len(unresolved_sorted),
                },
            }

            # build-target -> contains -> <src> for every srcs entry (ADR
            # 0044 Layer C1), one edge per plausible spelling of the entry
            # -- the referrer contract of ADR 0111. Sorted and deduplicated
            # for determinism. ``edge_props`` stamps the producing BUILD file
            # so ADR 0074's incremental purge keeps this edge when only the
            # source it points at is dirty (bd cpkp).
            for file_nid in sorted(set(srcs_targets)):
                edges.append({
                    "from": nid,
                    "to": file_nid,
                    "type": "contains",
                    "props": edge_props(rel_path),
                })

            # build-target -> depends_on -> build-target for every deps
            # entry (preserved from pre-Layer-C1 behaviour, now using
            # the canonical resolver).
            for dep_nid in sorted(deps_targets):
                edges.append({
                    "from": nid,
                    "to": dep_nid,
                    "type": "depends_on",
                    "props": edge_props(rel_path),
                })

            # build-target -> depends_on -> external-dep for every
            # external-workspace deps entry (ADR 0121). The node is minted
            # once per distinct id (setdefault: content is a pure function
            # of repo/name, so first-write and every-write agree, same
            # pattern as the .bzl node below) and reused by every other
            # target that names the same dependency. No parallel "tests"
            # edge -- a grammar wheel is a precondition the test needs, not
            # the subject the test is testing (see the tests-edge block
            # below, which stays deps_targets-only on purpose).
            for ext_id, (ext_repo, ext_name) in external_deps.items():
                nodes.setdefault(ext_id, external_dep_node(ext_repo, ext_name))
            for ext_id in sorted(external_deps):
                edges.append({
                    "from": nid,
                    "to": ext_id,
                    "type": "depends_on",
                    "props": edge_props(rel_path),
                })

            # For test targets, also emit a "tests" edge to the
            # depended-on build target.  Inferred confidence: a test
            # depends on the lib it is testing, but Bazel does not name
            # this relationship explicitly.
            if node_type == "test-target":
                for dep_nid in sorted(deps_targets):
                    edges.append({
                        "from": nid,
                        "to": dep_nid,
                        "type": "tests",
                        "props": edge_props(rel_path, "inferred"),
                    })

            # target -> depends_on -> file:<bzl> for the .bzl files this
            # target's own declaration reads -- the manifest whose constant is
            # its srcs, or the macro whose body declared it. That is what makes
            # "where do I register a new module so it lands in //weld:runtime"
            # answerable from the graph (bd 73xa, bd rh3l), and what makes the
            # answer short enough to read.
            for bzl_rel in target["bzl"]:
                edges.append({
                    "from": nid,
                    "to": file_id(bzl_rel),
                    "type": "depends_on",
                    "props": edge_props(rel_path),
                })

    edges.extend(data_edges(pending_data, nodes))
    return StrategyResult(nodes, edges, discovered_from)
