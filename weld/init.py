#!/usr/bin/env python3
"""Bootstrap a .weld/discover.yaml by scanning the project."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from weld._gitattributes_writer import write_repo_git_policy
from weld._init_classify import classify_files
from weld._init_cpp import cpp_buildsystem_source_entries, detect_cpp_buildsystem
from weld._init_csharp import detect_csharp_artifacts
from weld._init_exit import finish_init
from weld._init_framework_sources import (
    _files_entry,
    _source_entry,
    markdown_fallback_doc_source,
    yaml_has_wired_source,
)
from weld._init_entry_offer import EntryWiring, entry_keys
from weld._init_interfaces import detect_interfaces, interface_source_entries
from weld._init_language_entries import LanguageWiring, language_source_entries
from weld._init_wired_ledger import render_ledger

# Re-exports, not uses. The per-language wiring moved to
# weld/_init_language_entries.py, but these names are referred to as
# ``weld.init.<name>`` by tests and by the ADRs that introduced them
# (0046 test peers, 0060 C# anchors, 0071 framework strategies, 0132
# go_package), so the old spelling keeps resolving.
from weld._init_framework_sources import _add_framework_sources  # noqa: F401
from weld._init_language_entries import (  # noqa: F401
    _TREE_SITTER_EMIT_CALLS,
    _TREE_SITTER_LANGUAGES,
    _TREE_SITTER_TEST_PEER_GLOBS,
)
from weld._init_yaml_header import YAML_HEADER as _YAML_HEADER
from weld._safe_text import sanitize_terminal_line
from weld.init_workspace import init_workspace as _init_workspace
from weld.init_workspace import maybe_bootstrap_polyrepo as _maybe_bootstrap_polyrepo
from weld.init_detect import (
    detect_all_from_classified,
    detect_frameworks,
    detect_languages,
    detect_ros2,
    scan_files,
)

def _section_header(label: str) -> str:
    """Return a YAML comment that marks an artifact-class section."""
    return f"\n  # ===== {label} ====="

def _make_stub(glob: str, node_type: str, strategy: str) -> list[str]:
    """Build commented-out YAML lines for a stub entry."""
    return [f'  # - glob: "{glob}"', f"  #   type: {node_type}", f"  #   strategy: {strategy}"]

_ARTIFACT_CLASSES: list[tuple[str, str, str, str]] = [
    ("code",       "src/**/*.py",       "file",   "python_module"),
    ("docs",       "docs/**/*.md",      "doc",    "markdown"),
    ("policy",     "policies/**/*.md",  "doc",    "markdown"),
    ("infra",      "deploy/**/*.yaml",  "config", "config_file"),
    ("build",      "**/*.bazel",        "config", "config_file"),
    ("tests",      "tests/**/*.py",     "file",   "python_module"),
    ("operations", "tools/*.sh",        "tool",   "tool_script"),
]

def generate_yaml(
    languages: dict[str, int],
    frameworks: list[tuple[str, str, str]],
    dockerfiles: list[str], compose_files: list[str], ci_files: list[str],
    claude_agents: list[str], claude_commands: list[str],
    doc_dirs: list[str], python_globs: list[str], root_configs: list[str],
    ros2_pkg_roots: list[str] | None = None,
    csharp_flags: dict[str, bool] | None = None,
    cpp_bs: list[str] | None = None,
    interface_sources: list[str] | None = None,
    doc_fallback: str | None = None,
) -> str:
    """Generate the discover.yaml content using template strings.

    Sources are grouped into artifact-class sections so the starter
    config leads maintainers toward whole-codebase onboarding instead
    of code-only discovery.
    """
    buckets: dict[str, list[str]] = {cls: [] for cls, _, _, _ in _ARTIFACT_CLASSES}

    # --- code + tests: the per-language wiring (weld/_init_language_entries) ---
    # One table, shared with ``wd init --refresh`` so a refreshed config wires
    # the same strategies a full init does rather than a reduced subset of them.
    code_entries, test_entries = language_source_entries(LanguageWiring(
        languages=frozenset(languages),
        frameworks=tuple(frameworks),
        python_globs=tuple(python_globs),
        csharp_flags=csharp_flags,
        ros2_pkg_roots=tuple(ros2_pkg_roots or ()),
        interface_sources=tuple(interface_sources or ()),
    ))
    buckets["code"].extend(code_entries)
    buckets["tests"].extend(test_entries)

    # --- docs ---
    for doc_dir in doc_dirs:
        buckets["docs"].append(_source_entry(
            f"{doc_dir}/*.md", "doc", "markdown",
            comment=f"Documentation ({doc_dir})",
            extra={"id_prefix": f"doc:{doc_dir}"},
        ))
    # No conventional docs dir but markdown is present: wire **/*.md so a
    # docs repo keeping ADRs at the root / under adrs/ is not left with a
    # zero-node graph (Finding 07). Callers pass None when the fallback does
    # not apply (a docs dir was wired, or there is no markdown).
    if doc_fallback:
        buckets["docs"].append(doc_fallback)

    # --- policy ---
    if claude_agents:
        buckets["policy"].append(_source_entry(
            ".claude/agents/*.md", "agent", "frontmatter_md",
            comment="Claude agents",
        ))
    if claude_commands:
        buckets["policy"].append(_source_entry(
            ".claude/commands/*.md", "command", "firstline_md",
            comment="Claude commands",
        ))

    # --- infra ---
    # dockerfile and compose strategies require ``glob``; a literal file name
    # is itself a valid single-file glob pattern. Emitting ``files`` here
    # would crash discovery with KeyError: 'glob'.
    for df in dockerfiles:
        buckets["infra"].append(_source_entry(
            df, "dockerfile", "dockerfile", comment="Dockerfiles",
        ))

    for cf in compose_files:
        buckets["infra"].append(_source_entry(
            cf, "compose", "compose", comment="Docker Compose",
        ))

    # --- build ---
    if ci_files:
        buckets["build"].append(_source_entry(
            ".github/workflows/*.yml", "workflow", "yaml_meta",
            comment="CI workflows",
        ))

    if root_configs:
        buckets["build"].append(_files_entry(
            root_configs, "config", "config_file",
            comment="Root configuration files",
        ))
    if cpp_bs:
        buckets["build"].extend(cpp_bs)

    # --- Assemble sections ---
    sections: list[str] = []
    for cls, stub_glob, stub_type, stub_strat in _ARTIFACT_CLASSES:
        entries = buckets[cls]
        if entries:
            sections.append(_section_header(cls))
            sections.extend(entries)
        else:
            # Emit the stub so maintainers see the artifact class exists.
            sections.append(_section_header(cls + " (uncomment to enable)"))
            sections.extend(_make_stub(stub_glob, stub_type, stub_strat))

    block = "\n".join(sections) if sections else "  # No sources detected"
    record = _wired_entry_record(
        languages, frameworks, python_globs, root_configs)
    return f"{_YAML_HEADER}{_version_stamp()}{record}\nsources:\n{block}\n"


def _wired_entry_record(
    languages: dict[str, int], frameworks: list[tuple[str, str, str]],
    python_globs: list[str], root_configs: list[str],
) -> str:
    """The ``# wired-entry:`` lines recording what this config was wired with.

    A full init writes every entry the entry-shaped table can produce, so the
    record is that table's whole key set. Written here rather than left for the
    first ``wd init --refresh`` to infer, so that an entry deleted between the
    init and that refresh reads as *removed* rather than as never offered --
    which is the difference between a merge that respects a hand edit and one
    that undoes it (bd 5038-j5o5d).
    """
    return render_ledger(set(entry_keys(EntryWiring(
        root_configs=tuple(root_configs),
        frameworks=tuple(frameworks),
        python_globs=tuple(python_globs),
        languages=frozenset(languages),
    ))))


def _version_stamp() -> str:
    """A comment line recording the weld version that generated this config.

    Stamped so a stale ``discover.yaml`` is visible to a human comparing it
    against ``wd --version`` (ADR 0135). Informational only -- the unclaimed
    -source check does not read it back, so pre-stamp configs still get warned.
    When the version cannot be resolved the stamp is omitted, never faked.
    """
    from weld._version import weld_version

    version = weld_version()
    if not version:
        return ""
    return f"#\n# generated-by: weld {version}\n"

def init(root: Path, output: Path, *, force: bool = False) -> bool:
    """Run project detection and generate discover.yaml.

    Returns True on success, False if file exists and force is not set.
    """
    if output.exists() and not force:
        print(f"discover.yaml already exists at {output}", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        return False

    print("Scanning for files...", file=sys.stderr)
    files = scan_files(root)
    print(f"Found {len(files)} files total", file=sys.stderr)

    print("Scanning for languages...", file=sys.stderr)
    languages = detect_languages(files)
    for lang, count in languages.items():
        print(
            sanitize_terminal_line(f"  Found {count} {lang.capitalize()} files"),
            file=sys.stderr,
        )

    print("Detecting frameworks...", file=sys.stderr)
    frameworks = detect_frameworks(root, files)
    for fw, _strategy, path in frameworks:
        print(f"  Detected {fw} in {path}", file=sys.stderr)

    # Single classifier pass over the file list. Every path-shape
    # detector reads the precomputed records instead of re-walking
    # ``files`` (ADR 0027).
    classified = classify_files(root, files)
    detected = detect_all_from_classified(classified)
    structure = detected["structure"]
    dockerfiles, compose_files = detected["dockerfiles"], detected["compose_files"]
    ci_files = detected["ci_files"]
    claude_agents, claude_commands = detected["claude_agents"], detected["claude_commands"]
    doc_dirs, root_configs = detected["doc_dirs"], detected["root_configs"]
    python_globs = detected["python_globs"] if "python" in languages else []
    ros2_pkg_roots = detect_ros2(root, files)
    csharp_flags = detect_csharp_artifacts(files) if "csharp" in languages else None
    cpp_bs = cpp_buildsystem_source_entries(detect_cpp_buildsystem(files, root=root)) if "cpp" in languages else None
    interface_sources = interface_source_entries(
        detect_interfaces(root, files, compose_files), python_globs)

    print(f"Detecting project structure...\n  Structure: {structure}", file=sys.stderr)
    print("Scanning for Dockerfiles...", file=sys.stderr)
    for df in dockerfiles:
        print(f"  Found {df}", file=sys.stderr)
    if not dockerfiles:
        print("  No Dockerfiles found", file=sys.stderr)
    for cf in compose_files:
        print(f"  Found {cf}", file=sys.stderr)
    print("Scanning for CI configurations...", file=sys.stderr)
    for cf in ci_files:
        print(f"  Found .github/workflows/{cf}", file=sys.stderr)
    if not ci_files:
        print("  No CI workflows found", file=sys.stderr)
    print("Scanning for Claude definitions...", file=sys.stderr)
    if claude_agents:
        print(f"  Found {len(claude_agents)} agent definitions", file=sys.stderr)
    if claude_commands:
        print(f"  Found {len(claude_commands)} command definitions", file=sys.stderr)

    doc_fallback = markdown_fallback_doc_source(files, root, doc_dirs)

    print("Generating discover.yaml...", file=sys.stderr)
    yaml_text = generate_yaml(
        languages=languages, frameworks=frameworks,
        dockerfiles=dockerfiles, compose_files=compose_files,
        ci_files=ci_files, claude_agents=claude_agents,
        claude_commands=claude_commands, doc_dirs=doc_dirs,
        python_globs=python_globs, root_configs=root_configs,
        ros2_pkg_roots=ros2_pkg_roots, csharp_flags=csharp_flags, cpp_bs=cpp_bs,
        interface_sources=interface_sources, doc_fallback=doc_fallback,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml_text, encoding="utf-8")
    print(f"Wrote {output}", file=sys.stderr)
    if not yaml_has_wired_source(yaml_text):
        # ADR 0134: a config that wires nothing is a cannot-answer outcome, not
        # a real answer. Say so and point at the remedy instead of leaving a
        # silent all-stub config that discovers a zero-node graph (Finding 07).
        print(
            f"Recognised nothing to wire from {len(files)} files: the config "
            "is all stubs, so `wd discover` will build a zero-node graph. "
            "Uncomment a stub in the section that fits your sources, or add a "
            "source entry, then re-run `wd init --refresh` (keeps what you "
            "wrote) -- or `wd init --force` to regenerate from scratch.",
            file=sys.stderr,
        )
    return True

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="wd init",
        description="Bootstrap a .weld/discover.yaml by scanning the project",
    )
    parser.add_argument("root", nargs="?", default=".",
        help="Project root directory (default: current directory)")
    parser.add_argument("--output", "-o", default=None,
        help="Output path (default: <root>/.weld/discover.yaml)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--force", "-f", action="store_true",
        help="Overwrite existing discover.yaml / workspaces.yaml")
    mode.add_argument("--refresh", action="store_true",
        help="Merge newly-detected strategies into an existing discover.yaml "
             "without discarding hand edits (non-destructive)")
    parser.add_argument("--max-depth", type=int, default=4,
        help="Max depth when scanning for nested git repos (default: 4)")
    parser.add_argument("--respect-gitignore", action="store_true",
        help="Skip gitignored scan-only child repos when writing workspaces.yaml")
    gi = parser.add_mutually_exclusive_group()
    gi.add_argument("--ignore-all", action="store_true",
        help="Write a fully-ignoring .weld/.gitignore (every weld file ignored)")
    gi.add_argument("--track-graphs", action="store_true",
        help="Track graph.json + agent-graph.json (default ignores them)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    output = Path(args.output) if args.output else root / ".weld" / "discover.yaml"
    if args.refresh:
        from weld._init_refresh_report import run_refresh
        run_refresh(root, output)
        return
    success = init(root, output, force=args.force)
    workspaces_out = output.parent / "workspaces.yaml"
    if _init_workspace(root, workspaces_out, force=args.force,
                       max_depth=args.max_depth,
                       respect_gitignore=args.respect_gitignore):
        print(f"Wrote {workspaces_out}", file=sys.stderr)
    _maybe_bootstrap_polyrepo(root, max_depth=args.max_depth)
    modes = {"ignore_all": args.ignore_all, "track_graphs": args.track_graphs}
    policy = write_repo_git_policy(root, output.parent, **modes)
    finish_init(output.parent, config_written=success, policy=policy, **modes)
