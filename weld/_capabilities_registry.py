"""Registry tables for the runtime capability matrix (ADR 0043 Layer B).

Split from :mod:`weld.capabilities` to keep both files under the 400-line
cap. The registry below maps every public strategy module under
``weld/strategies/`` to its capability declaration; the enforcement test
in ``weld/tests/weld_capabilities_test.py`` fails the moment disk and
registry drift, which is the discipline ADR 0043 calls out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Allowed evidence tags. Languages and frameworks share the same registry
# vocabulary; per-output filtering happens in
# :func:`weld.capabilities.compute_capabilities`.
LANGUAGE_EVIDENCE: frozenset[str] = frozenset(["file", "module", "imports", "symbols", "calls", "tests"])
FRAMEWORK_EVIDENCE: frozenset[str] = frozenset(["nodes_emitted", "srcs_edges", "deps_edges", "test_edges"])


@dataclass(frozen=True)
class StrategyCapability:
    """What a single strategy contributes to the capability matrix.

    A strategy is allowed to attribute to a language, a framework, or
    both. ``evidence`` lists the flags the strategy is *capable* of
    producing in principle; runtime crosses this with the actual graph
    contents in :func:`weld.capabilities.compute_capabilities`.

    Per ADR 0046 (multi-language test-peer edges), a strategy may
    attribute to *several* languages. The ``languages`` field is the
    multi-language form; ``language`` remains the single-language fast
    path. Consumers that need the full set should iterate
    :func:`languages_set` (handles the union deterministically). It is
    invalid to set both fields to non-empty values for the same entry.

    The same multi/single split applies to frameworks: the ``manifest``
    strategy processes both ``package.json`` (npm) and ``Makefile``
    (make), so it declares ``frameworks={'npm', 'make'}`` rather than a
    single ``framework='npm'`` (which would misclassify a Makefile-only
    repo). Consumers should iterate :func:`frameworks_set` to handle
    both shapes deterministically. It is invalid to set both
    ``framework`` and ``frameworks`` to non-empty values for the same
    entry.
    """

    language: str | None = None
    languages: frozenset[str] = field(default_factory=frozenset)
    framework: str | None = None
    frameworks: frozenset[str] = field(default_factory=frozenset)
    evidence: frozenset[str] = field(default_factory=frozenset)
    file_extensions: frozenset[str] = field(default_factory=frozenset)
    file_basenames: frozenset[str] = field(default_factory=frozenset)

    def languages_set(self) -> frozenset[str]:
        """Return all languages this capability attributes to.

        Returns the multi-language ``languages`` field when populated,
        otherwise wraps ``language`` in a singleton (or an empty set
        for framework-only entries).
        """
        if self.languages:
            return self.languages
        if self.language is not None:
            return frozenset((self.language,))
        return frozenset()

    def frameworks_set(self) -> frozenset[str]:
        """Return all frameworks this capability attributes to.

        Returns the multi-framework ``frameworks`` field when populated,
        otherwise wraps ``framework`` in a singleton (or an empty set
        for language-only entries).
        """
        if self.frameworks:
            return self.frameworks
        if self.framework is not None:
            return frozenset((self.framework,))
        return frozenset()


def _lang(
    name: str, evidence: tuple[str, ...], exts: tuple[str, ...] = (),
) -> StrategyCapability:
    return StrategyCapability(
        language=name,
        evidence=frozenset(evidence),
        file_extensions=frozenset(exts),
    )


def _multi_lang(
    names: tuple[str, ...],
    evidence: tuple[str, ...],
    exts: tuple[str, ...] = (),
) -> StrategyCapability:
    """Multi-language entry constructor.

    Used by ``test_peer`` (ADR 0046) so a single strategy can claim
    the ``tests`` evidence flag across Python, Go, TS/JS, Java, C#,
    and Rust without registering one stem per language.
    """
    return StrategyCapability(
        languages=frozenset(names),
        evidence=frozenset(evidence),
        file_extensions=frozenset(exts),
    )


def _fw(
    name: str,
    evidence: tuple[str, ...],
    exts: tuple[str, ...] = (),
    basenames: tuple[str, ...] = (),
) -> StrategyCapability:
    return StrategyCapability(
        framework=name,
        evidence=frozenset(evidence),
        file_extensions=frozenset(exts),
        file_basenames=frozenset(basenames),
    )


def _multi_fw(
    names: tuple[str, ...],
    evidence: tuple[str, ...],
    exts: tuple[str, ...] = (),
    basenames: tuple[str, ...] = (),
) -> StrategyCapability:
    """Multi-framework entry constructor.

    Used by ``manifest`` so a single strategy can claim both ``npm``
    (``package.json``) and ``make`` (``Makefile``/``GNUmakefile``)
    without splitting into two registry entries (which would break the
    one-stem-per-strategy registry-vs-disk invariant).
    """
    return StrategyCapability(
        frameworks=frozenset(names),
        evidence=frozenset(evidence),
        file_extensions=frozenset(exts),
        file_basenames=frozenset(basenames),
    )


# Map: strategy filename stem (matches a public module in
# ``weld/strategies/``) -> capability declaration. Keys are exhaustive;
# the enforcement test fails if disk and registry drift.
STRATEGY_CAPABILITIES: dict[str, StrategyCapability] = {
    # --- Python language ---
    "python_module": _lang("python", ("file", "module", "imports"), (".py",)),
    "python_callgraph": _lang("python", ("symbols", "calls"), (".py",)),
    "python_package": _lang("python", ("module",), (".py",)),
    # ADR 0046 (Layer C3): test_peer dispatches to per-language
    # resolvers and now claims ``tests`` evidence for Python, Go,
    # TS/JS, Java, C#, and Rust.
    "test_peer": _multi_lang(
        ("python", "go", "typescript", "javascript", "java", "csharp", "rust"),
        ("file", "tests"),
        (".py", ".go", ".ts", ".tsx", ".js", ".jsx", ".java", ".cs", ".rs"),
    ),
    "tool_script": _lang("python", ("file",), (".py",)),
    # --- Tree-sitter languages (multi-language pass) ---
    "tree_sitter": StrategyCapability(
        evidence=frozenset(["file", "symbols", "imports", "calls"]),
    ),
    "typescript_exports": _lang(
        "typescript",
        ("file", "symbols", "imports", "calls"),
        (".ts", ".tsx"),
    ),
    "java": _lang("java", ("file", "module", "symbols"), (".java",)),
    "csharp": _lang("csharp", ("file", "module", "symbols"), (".cs",)),
    "cpp_resolver": _lang(
        "cpp",
        ("file", "symbols", "imports", "calls"),
        (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"),
    ),
    # --- Build / runtime frameworks ---
    "bazel": _fw(
        "bazel",
        ("nodes_emitted", "srcs_edges", "deps_edges"),
        (".bzl",),
        ("BUILD", "BUILD.bazel", "MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel"),
    ),
    # ``manifest`` processes both ``package.json`` (npm) and
    # ``Makefile``/``GNUmakefile`` (make), so it claims both frameworks
    # rather than misclassifying a Makefile-only repo as ``npm``. See
    # the bd issue capabilities/manifest-misclassification.
    "manifest": _multi_fw(
        ("npm", "make"),
        ("nodes_emitted",),
        basenames=("package.json", "Makefile", "GNUmakefile"),
    ),
    "dockerfile": _fw(
        "dockerfile",
        # ADR 0045 (Layer C2): COPY/ADD instructions emit ``contains``
        # edges to source ``file:*`` nodes (srcs_edges).
        ("nodes_emitted", "srcs_edges"),
        basenames=("Dockerfile",),
    ),
    "compose": _fw(
        "compose",
        # ADR 0045 (Layer C2): each declared service becomes a first-class
        # ``service:<stem>:<name>`` node (nodes_emitted), env_file refs
        # produce ``contains`` edges (srcs_edges), and ``service.build``
        # produces ``depends_on`` edges to dockerfiles (deps_edges).
        ("nodes_emitted", "srcs_edges", "deps_edges"),
        basenames=(
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        ),
    ),
    # ``deploy_surface`` processes ``Chart.yaml`` (helm), ``*.tf``
    # (terraform), and k8s manifests, so it claims all three
    # frameworks rather than misclassifying a Chart.yaml-only or
    # ``.tf``-only repo as ``k8s``. The per-framework split is in
    # :data:`MULTI_FRAMEWORK_FILES` below.
    "deploy_surface": _multi_fw(
        ("k8s", "helm", "terraform"),
        ("nodes_emitted",),
        exts=(".tf",),
        basenames=("Chart.yaml",),
    ),
    "gh_workflow": _fw(
        "github_workflow",
        ("nodes_emitted",),
        (".yml", ".yaml"),
    ),
    "yaml_meta": _fw(
        "github_workflow",
        ("nodes_emitted",),
        (".yml", ".yaml"),
    ),
    "config_file": _fw(
        "config",
        ("nodes_emitted",),
        basenames=("MODULE.bazel", ".bazelrc", "CLAUDE.md", "AGENTS.md"),
    ),
    "grpc_proto": _fw("proto", ("nodes_emitted", "deps_edges"), (".proto",)),
    "grpc_proto_parser": _fw("proto", ("nodes_emitted",), (".proto",)),
    "grpc_bindings": _fw("proto", ("nodes_emitted",), (".py",)),
    "fastapi": _fw("fastapi", ("nodes_emitted",), (".py",)),
    "flask": _fw("flask", ("nodes_emitted",), (".py",)),
    "gin": _fw("gin", ("nodes_emitted",), (".go",)),  # ADR 0071: Go routes
    "axum": _fw("axum", ("nodes_emitted",), (".rs",)),  # ADR 0071: Rust routes
    "express": _fw("express", ("nodes_emitted",), (".ts", ".js", ".mjs", ".cjs", ".tsx", ".jsx")),  # bd 2jt5.2.15: TS/JS routes
    "pydantic": _fw("pydantic", ("nodes_emitted",), (".py",)),
    "sqlalchemy": _fw("sqlalchemy", ("nodes_emitted",), (".py",)),
    "http_client": _fw("http_client", ("nodes_emitted",), (".py",)),
    "events": _fw("events", ("nodes_emitted",), (".py",)),
    "events_bindings": _fw("events", ("nodes_emitted",), (".py",)),
    "events_callsite": _fw("events", ("nodes_emitted",), (".py",)),
    "events_config": _fw("events", ("nodes_emitted",), (".yml", ".yaml", ".toml")),
    "boundary_entrypoint": _fw("boundary", ("nodes_emitted",)),
    "runtime_contract": _fw("runtime_contract", ("nodes_emitted",)),
    "worker_stage": _fw("worker", ("nodes_emitted",)),
    "ros2_package": _fw("ros2", ("nodes_emitted",), basenames=("package.xml",)),
    "ros2_interfaces": _fw(
        "ros2", ("nodes_emitted",), (".msg", ".srv", ".action"),
    ),
    "ros2_topology": _fw("ros2", ("nodes_emitted",), (".py", ".cpp")),
    "ros2_launch": _fw("ros2", ("nodes_emitted",), (".launch.py",)),
    "ros2_cmake": _fw("ros2", ("nodes_emitted",), basenames=("CMakeLists.txt",)),
    # ADR 0057 Wave 1: general-purpose C++ build-system parsers. Each
    # entry attributes to a distinct package-manager / build-system
    # framework so the capability matrix lights up per ecosystem.
    "cpp_cmake": _fw(
        "cmake",
        ("nodes_emitted",),
        exts=(".cmake",),
        basenames=("CMakeLists.txt",),
    ),
    "cpp_conan": _fw(
        "conan",
        ("nodes_emitted",),
        basenames=("conanfile.txt", "conanfile.py"),
    ),
    "cpp_vcpkg": _fw(
        "vcpkg",
        ("nodes_emitted",),
        basenames=("vcpkg.json",),
    ),
    "cpp_buildsystem_detector": _fw(
        "cpp_buildsystem",
        ("nodes_emitted",),
        basenames=(
            "CMakeLists.txt",
            "Makefile",
            "GNUmakefile",
            "meson.build",
            "BUILD",
            "BUILD.bazel",
        ),
    ),
    # ADR 0057 Wave 3: optional libclang-driven semantic layer. The
    # strategy is opt-in (``pip install configflux-weld[cpp-libclang]``
    # + ``WELD_CPP_LIBCLANG=1`` + a ``compile_commands.json``); when
    # dormant it contributes nothing. The capability is registered
    # nonetheless so ``wd capabilities`` lists the libclang framework
    # row instead of hiding it.
    "cpp_libclang": _fw(
        "cpp_libclang", ("nodes_emitted",), basenames=("compile_commands.json",),
    ),
    "markdown": _fw("markdown", ("nodes_emitted",), (".md",)),
    "firstline_md": _fw("markdown", ("nodes_emitted",), (".md",)),
    "frontmatter_md": _fw("markdown", ("nodes_emitted",), (".md",)),
    "runbook": _fw("runbook", ("nodes_emitted",), (".md",)),
    "concept_from_bd": _fw(
        "bd", ("nodes_emitted",), basenames=("issues.jsonl",),
    ),
    # Static web frontend (HTML/CSS/JS) surfaced as queryable ``file``
    # nodes (element ids / CSS selectors / JS fn names in props.headings).
    "viz_frontend": _fw(
        "web_frontend", ("nodes_emitted",), (".html", ".htm", ".css", ".js", ".mjs"),
    ),
    # ADR 0056 Wave 1: ``.csproj`` and ``.sln`` parsers. Wave 1 covers
    # project + solution graph (ProjectReference, Directory.Build.props,
    # solution-level configurations). Wave 2 / Wave 3 layer framework
    # awareness (routes, EF Core, MSBuild targets) on top.
    "csharp_project": _fw("dotnet", ("nodes_emitted", "deps_edges"), (".csproj",)),
    "csharp_solution": _fw("dotnet", ("nodes_emitted",), (".sln",)),
    # ADR 0056 Wave 2: framework-aware extraction over C# source files.
    # Each strategy attributes to a distinct framework so the
    # capability matrix lights up per ecosystem (aspnetcore, efcore,
    # xUnit/NUnit/MSTest).
    "csharp_aspnet_routes": _fw(
        "aspnetcore", ("nodes_emitted",), (".cs",),
    ),
    "csharp_efcore": _fw(
        "efcore", ("nodes_emitted",), (".cs",),
    ),
    "csharp_test_framework": _fw(
        "csharp_test", ("nodes_emitted",), (".cs",),
    ),
    # ADR 0056 Wave 3: MSBuild target extraction. Parses
    # <Target Name="..."> declarations from .csproj/.props/.targets and
    # emits build-target nodes plus BeforeTargets/AfterTargets
    # depends_on edges.
    "csharp_msbuild_targets": _fw(
        "msbuild",
        ("nodes_emitted", "deps_edges"),
        exts=(".csproj", ".props", ".targets"),
    ),
    "csharp_package": _lang("csharp", ("module",), (".cs",)),  # ADR 0060
}


# Public strategy module set on disk under ``weld/strategies/``.
# Adding a public strategy without updating ``STRATEGY_CAPABILITIES``
# makes ``weld_capabilities_test.test_expected_strategies_match_disk``
# fail; that is the design.
EXPECTED_STRATEGIES: frozenset[str] = frozenset(STRATEGY_CAPABILITIES.keys())


# Per-framework basename / extension split for multi-framework
# strategies. Without this, ``compute_capabilities`` cannot tell which
# of a strategy's basenames belongs to which framework, and a
# Makefile-only repo with ``manifest`` wired would falsely report
# ``npm: nodes_emitted=true``. Single-framework strategies use
# ``cap.file_basenames`` / ``cap.file_extensions`` directly and do not
# appear here.
#
# A framework whose entry is ``((), ())`` is declared for
# registry-completeness only -- the strategy attributes to it (so the
# row appears in the matrix with all flags False by default) but the
# registry has no file-level signature for it. Used by
# ``deploy_surface`` for ``k8s`` because k8s manifests are detected by
# file content, not by basename/extension.
MULTI_FRAMEWORK_FILES: dict[
    str, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]
] = {
    # stem -> framework -> (extensions, basenames)
    "manifest": {
        "npm": ((), ("package.json",)),
        "make": ((), ("Makefile", "GNUmakefile")),
    },
    # ``deploy_surface`` partitions: ``Chart.yaml`` -> helm, ``*.tf``
    # -> terraform, and k8s manifests are recognised structurally by
    # the strategy itself (no registry-level basename/extension split).
    # Without this entry, a Chart.yaml-only repo would falsely report
    # ``k8s: nodes_emitted=true``.
    "deploy_surface": {
        "helm": ((), ("Chart.yaml",)),
        "terraform": ((".tf",), ()),
        "k8s": ((), ()),
    },
}


# Frameworks present-on-disk but not yet wired to a strategy. Extracted
# to :mod:`weld._capabilities_registry_missing` to keep this file under
# the 400-line cap. Re-exported here so all existing import sites are
# unaffected.
from weld._capabilities_registry_missing import (  # noqa: E402
    MISSING_FRAMEWORK_PATTERNS,  # noqa: F401 -- re-exported for callers
)
