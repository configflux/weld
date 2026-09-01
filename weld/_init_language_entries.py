"""The one table of per-language ``discover.yaml`` source entries.

``wd init`` and ``wd init --refresh`` both have to answer the same question --
*which strategies does this language need wired?* -- and until field eval
v0.24.0 (N7) they answered it from two different tables. ``weld.init``'s
``generate_yaml`` routed C# through :mod:`weld._init_csharp`, Go and Rust
through their framework helpers, plus ROS2 and the interface stack;
``weld._init_refresh`` carried a private reduced copy that knew only about
tree-sitter and test peers. The consequence was silent and expensive: a
maintainer who followed ``wd doctor``'s advice to ``--refresh`` got three
strategies where ``--force`` wires ten, *and* a clean doctor afterwards, so
nothing was left to say a further tier was still unwired.

This module is that single table. :func:`language_source_entries` returns
exactly the entries a full ``wd init`` emits for a set of languages, split into
the two artifact-class buckets they land in (``code`` and ``tests``) and in the
order ``generate_yaml`` has always emitted them -- so the full-init output is
byte-for-byte what it was, and a refresh appending the same blocks produces a
config indistinguishable from a freshly generated one.

Emission order is a contract, not an accident. Framework entries precede their
language's tree-sitter entry so the canonical tree-sitter ``file:`` node wins
the later orchestrator merge over a framework strategy's thin boundary-file
placeholder (ADR 0071); ``go_package`` and the C# stack follow their
tree-sitter entries for the mirror-image reason. Each phase below iterates its
*own* canonical order and filters by the caller's language set, which is what
lets one function serve both a full init (every detected language) and a
refresh (only the unclaimed ones) without either reordering the other's output.
"""

from __future__ import annotations

from dataclasses import dataclass

from weld._init_csharp import csharp_source_entries
from weld._init_framework_sources import (
    _add_framework_sources,
    _add_go_framework_sources,
    _add_rust_framework_sources,
    _source_entry,
)
from weld._init_go import go_package_source_entry
from weld._init_ros2 import ros2_source_entries

# Tree-sitter-backed languages: name -> tuple of file extensions (C++ covers
# .cpp/.cc/.h/...). Languages in ``_TREE_SITTER_EMIT_CALLS`` also emit
# function-level call graph nodes via the per-source ``emit_calls`` flag.
_TREE_SITTER_LANGUAGES: dict[str, tuple[str, ...]] = {
    "csharp": (".cs",), "go": (".go",), "rust": (".rs",), "typescript": (".ts",),
    "cpp": (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".hxx", ".ipp", ".tpp"),
    "java": (".java",),
}
_TREE_SITTER_EMIT_CALLS: frozenset[str] = frozenset({"cpp", "csharp"})
# Tree-sitter languages whose test convention a glob can target, paired
# with the glob(s) the matching ``weld.strategies._test_peer_*`` resolver
# recognizes, so `wd init` scaffolds a `test_peer` source entry (the
# `tests` edge) alongside the `tree_sitter` entry -- parity with the
# Python python_module + test_peer pairing (ADR 0046). A language may need
# more than one glob (TS spreads tests across ``*.test`` / ``*.spec`` /
# ``__tests__/`` shapes), so the value is a tuple. Only globs the resolver
# actually pairs are listed, so a stock init never writes an entry the
# resolver would ignore:
#   - rust: Cargo integration tests under ``tests/`` resolved to
#     ``src/<name>.rs`` by ``_test_peer_rust``.
#   - go: ``foo_test.go`` beside ``foo.go`` (same dir) -- ``_test_peer_go``.
#   - typescript: ``*.test.ts`` / ``*.spec.ts`` (mid-suffix) and
#     ``__tests__/*.ts`` (Jest dir) -- all recognized by
#     ``_test_peer_ts.is_test_file``.
_TREE_SITTER_TEST_PEER_GLOBS: dict[str, tuple[str, ...]] = {
    "rust": ("**/tests/*.rs",),
    "go": ("**/*_test.go",),
    "typescript": ("**/*.test.ts", "**/*.spec.ts", "**/__tests__/*.ts"),
}


def _language_label(language: str) -> str:
    """Display label for a language in a generated YAML comment."""
    return "C#" if language == "csharp" else language.capitalize()


@dataclass(frozen=True)
class LanguageWiring:
    """What the detectors found, as the entry builders need to see it.

    ``languages`` is the *selection* -- which languages to emit for. Every
    other field is a detection artifact and is always the whole-repo value:
    ``wd init`` passes the full detected set as the selection and gets its
    complete config back, while ``wd init --refresh`` passes only the unclaimed
    languages and gets exactly their slice of that same config. Narrowing the
    artifacts instead of the selection would make a refresh emit *different*
    entries rather than *fewer*, which is the drift this module exists to end.
    """

    #: Languages to emit entries for (a subset of what was detected).
    languages: frozenset[str]
    #: ``detect_frameworks`` output: ``(framework, strategy, detected_in_path)``.
    frameworks: tuple[tuple[str, str, str], ...] = ()
    #: ``find_python_glob_roots`` output, whole-repo.
    python_globs: tuple[str, ...] = ()
    #: ``detect_csharp_artifacts`` flags, or None when no C# was detected.
    csharp_flags: dict[str, bool] | None = None
    #: ``detect_ros2`` package roots.
    ros2_pkg_roots: tuple[str, ...] = ()
    #: Pre-built interface entries (``interface_source_entries`` output).
    interface_sources: tuple[str, ...] = ()


def language_source_entries(
    wiring: LanguageWiring,
) -> tuple[list[str], list[str]]:
    """Return ``(code_entries, tests_entries)`` for ``wiring.languages``.

    The two lists are the ``code`` and ``tests`` artifact-class buckets of
    ``discover.yaml``, in emission order. Callers that append everything to one
    place (``--refresh``) concatenate them; ``generate_yaml`` keeps them apart
    so each lands under its own section header.
    """
    code: list[str] = []
    tests: list[str] = []
    _add_language_framework_entries(wiring, code)
    _add_python_entries(wiring, code, tests)
    _add_tree_sitter_entries(wiring, code)
    _add_test_peer_entries(wiring, tests)
    _add_stack_entries(wiring, code)
    return code, tests


def _add_language_framework_entries(
    wiring: LanguageWiring, code: list[str],
) -> None:
    """Framework strategies, gated on their own language being selected.

    Every framework these helpers wire is detected by reading source files of
    exactly one language family (``weld._init_framework_scan._LANG_FRAMEWORKS``):
    SQLAlchemy / FastAPI / Flask / Pydantic / HTTPClient from ``.py``, Gin from
    ``.go``, Axum from ``.rs``. So a detected framework implies its language is
    in the detected set, and gating here can only ever *narrow* a refresh -- it
    never drops an entry a full init would have emitted.
    """
    frameworks = list(wiring.frameworks)
    python_globs = list(wiring.python_globs)
    if "python" in wiring.languages:
        _add_framework_sources(code, frameworks, python_globs)
    # Go framework strategies (gin) precede the tree-sitter Go entry so
    # the canonical tree-sitter file node wins the orchestrator merge.
    if "go" in wiring.languages:
        _add_go_framework_sources(code, frameworks)
    # Rust framework strategies (axum) precede the tree-sitter Rust entry
    # for the same merge-order reason (ADR 0071).
    if "rust" in wiring.languages:
        _add_rust_framework_sources(code, frameworks)


def _add_python_entries(
    wiring: LanguageWiring, code: list[str], tests: list[str],
) -> None:
    """python_module / python_callgraph / test_peer entries per python glob.

    These emit file/symbol nodes; the Python framework strategies emit
    route/entity/contract. They coexist on the same glob without
    de-duplication (bd et6o).
    """
    if "python" not in wiring.languages:
        return
    added: set[str] = set()
    for glob in wiring.python_globs:
        if glob in added:
            continue
        if "test" in glob.lower():
            for strat in ("python_module", "test_peer"):  # ADR 0046
                tests.append(_source_entry(
                    glob, "file", strat,
                    comment=f"Python tests in {glob.split('/')[0]} ({strat})"))
        else:
            # ADR 0004: pair non-test python source with a callgraph
            # entry (symbol nodes + calls edges).
            code.append(_source_entry(
                glob, "file", "python_module",
                comment=f"Python modules in {glob.split('/')[0]}"))
            code.append(_source_entry(
                glob, "symbol", "python_callgraph",
                comment=f"Python call graph in {glob.split('/')[0]}"))
        added.add(glob)


def _add_tree_sitter_entries(wiring: LanguageWiring, code: list[str]) -> None:
    """One ``tree_sitter`` entry per extension, plus Go's package anchor."""
    for lang, exts in _TREE_SITTER_LANGUAGES.items():
        if lang not in wiring.languages:
            continue
        extras: dict[str, str] = {"language": lang}
        if lang in _TREE_SITTER_EMIT_CALLS:
            extras["emit_calls"] = "true"
        label = _language_label(lang)
        for ext in exts:
            code.append(_source_entry(
                f"**/*{ext}", "file", "tree_sitter",
                comment=f"{label} sources ({ext})",
                extra=extras,
            ))
        if lang == "go":  # ordered after tree_sitter, like the C# stack below
            code.append(go_package_source_entry())


def _add_test_peer_entries(wiring: LanguageWiring, tests: list[str]) -> None:
    """Pair each tree-sitter test convention with a ``test_peer`` entry.

    Mirrors the Python python_module + test_peer pairing (ADR 0046) so the
    ``tests`` edge is emitted by a stock ``wd init`` + ``wd discover``. The
    tree_sitter entry only emits symbol/definition nodes; the ``tests`` edge
    needs the per-language test_peer resolver.
    """
    for lang, test_globs in _TREE_SITTER_TEST_PEER_GLOBS.items():
        if lang not in wiring.languages:
            continue
        label = _language_label(lang)
        for test_glob in test_globs:
            tests.append(_source_entry(
                test_glob, "file", "test_peer",
                comment=f"{label} tests (test_peer; ADR 0046)",
            ))


def _add_stack_entries(wiring: LanguageWiring, code: list[str]) -> None:
    """The multi-strategy stacks: C#, ROS2, and the interface strategies.

    C# is language-keyed. ROS2 and the interface strategies (gRPC, events,
    runtime-contract; ADR 0080) are keyed on artifacts rather than on a source
    language -- a ``package.xml`` tree or a ``.proto`` directory -- so they fire
    whenever the detector found them, exactly as a full init does. A refresh
    therefore offers them too, and drops any block the existing config already
    wires rather than gating them on a language they do not belong to.
    """
    if "csharp" in wiring.languages and wiring.csharp_flags:
        code.extend(csharp_source_entries(wiring.csharp_flags))
    if wiring.ros2_pkg_roots:
        code.extend(ros2_source_entries(list(wiring.ros2_pkg_roots)))
    if wiring.interface_sources:
        code.extend(wiring.interface_sources)
