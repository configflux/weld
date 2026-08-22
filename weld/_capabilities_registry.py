"""Registry tables for the runtime capability matrix (ADR 0043 Layer B).

Split from :mod:`weld.capabilities` to keep both files under the 400-line
cap. The registry below maps every public strategy module under
``weld/strategies/`` to its capability declaration; the enforcement test
in ``weld/tests/weld_capabilities_test.py`` fails the moment disk and
registry drift, which is the discipline ADR 0043 calls out.

The declaration *shape* -- the evidence vocabularies, the
:class:`StrategyCapability` dataclass, and its constructors -- lives in
:mod:`weld._capabilities_registry_types` and is re-exported here, so this
module holds the table and nothing else.
"""

from __future__ import annotations

from weld._capabilities_registry_types import (
    # The two evidence vocabularies are re-exported, not used here: this
    # module is the table, and every existing import site names them
    # through this module. Same pattern as MISSING_FRAMEWORK_PATTERNS below.
    FRAMEWORK_EVIDENCE,  # noqa: F401 -- re-exported for callers
    LANGUAGE_EVIDENCE,  # noqa: F401 -- re-exported for callers
    StrategyCapability,
    _fw,
    _lang,
    _multi_fw,
    _multi_lang,
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
    "dds_idl": _fw("dds", ("nodes_emitted",), (".idl",)),
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
    "events_mqtt": _fw("events", ("nodes_emitted",), (".py",)),
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
    # ``validates`` edges from a lint/checker module to the paths its own
    # string literals name (ADR 0016 governance verb). Declared as a
    # framework, not as Python language evidence: the strategy reads ``.py``
    # only because that is what validators are written in, and attributing
    # it to ``python`` would inflate the language row with governance work
    # that says nothing about Python extraction depth.
    "validator_targets": _fw("lint", ("nodes_emitted",), (".py",)),
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
    "go_package": _lang("go", ("module",), (".go",)),  # ADR 0132
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
