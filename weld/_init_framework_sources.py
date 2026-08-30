"""YAML source-entry builders and framework->glob mapping for ``wd init``.

Split out of :mod:`weld.init` so the discover.yaml *formatting* helpers
(``_source_entry`` / ``_files_entry``) and the *framework-to-source*
mapping (``_add_framework_sources`` and its glob-selection helpers) live in
one cohesive module. :mod:`weld.init` re-imports these names, so existing
callers and tests that reference ``weld.init._add_framework_sources``
continue to work unchanged.

These helpers are pure string/path logic with no dependency on the rest of
``weld.init``; keeping them here avoids an import cycle.
"""

from __future__ import annotations

from pathlib import Path


def markdown_fallback_doc_source(
    files: list[Path], root: Path, doc_dirs: list[str],
) -> str | None:
    """A ``**/*.md`` docs entry for a repo whose markdown lives outside docs/.

    ``wd init``'s docs detection only recognises the conventional directory
    names (``docs`` / ``doc`` / ``documentation``). A docs repository that
    keeps its markdown at the root or under ``adrs/`` / ``architecture/``
    therefore got an empty ``sources:`` block and a zero-node graph, silently
    (field eval v0.23.1 Finding 07). ADRs are among the highest-value nodes,
    so leaving them unwired is a real loss, not a stylistic one.

    Returns a single ``- glob: "**/*.md"`` markdown source-entry string when
    ``doc_dirs`` is empty *and* at least one ``.md`` file is present under
    ``root``; otherwise ``None`` (a conventional docs dir is already wired, or
    there is no markdown to wire). ``root`` is accepted for signature symmetry
    with the other detectors; the decision needs only the extensions of
    ``files``.
    """
    if doc_dirs:
        return None
    if not any(f.suffix.lower() == ".md" for f in files):
        return None
    return _source_entry(
        "**/*.md", "doc", "markdown",
        comment="Documentation (markdown, no conventional docs/ dir found)",
        extra={"id_prefix": "doc:md"},
    )


def yaml_has_wired_source(yaml_text: str) -> bool:
    """True when the generated config wires at least one real source entry.

    ``wd init`` always emits every artifact-class section, filling empty ones
    with *commented-out* stub lines (``  # - glob:``) so a maintainer can see
    the class exists. A repo weld recognised nothing in therefore still
    produces a well-formed ``sources:`` block -- one made entirely of stubs.
    This predicate separates that all-stub "recognised nothing" outcome from a
    config that wired something, so :func:`weld.init.init` can say which one it
    is (ADR 0134: a cannot-answer outcome must state the reason, not masquerade
    as a real answer). An uncommented ``- glob:`` / ``- files:`` line is the
    marker of a real entry; a stub line starts with ``#`` after stripping.
    """
    for raw in yaml_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith(("- glob:", "- files:")):
            return True
    return False


def _source_entry(
    glob: str, node_type: str, strategy: str,
    *, comment: str = "", extra: dict[str, str] | None = None,
) -> str:
    """Return one ``- glob:``/``type:``/``strategy:`` YAML block."""
    lines = [f"\n  # --- {comment} ---"] if comment else []
    lines += [f'  - glob: "{glob}"', f"    type: {node_type}", f"    strategy: {strategy}"]
    if extra:
        lines += [f"    {k}: {v}" for k, v in extra.items()]
    return "\n".join(lines)


def _files_entry(
    file_list: list[str], node_type: str, strategy: str, *, comment: str = "",
) -> str:
    """Return one ``- files: [...]``/``type:``/``strategy:`` YAML block."""
    lines = [f"\n  # --- {comment} ---"] if comment else []
    inner = ", ".join(f'"{f}"' for f in file_list)
    lines += [f"  - files: [{inner}]", f"    type: {node_type}", f"    strategy: {strategy}"]
    return "\n".join(lines)


def _glob_matches_path(glob: str, path: str) -> bool:
    """``find_python_glob_roots`` emits 3 shapes: ``*.py``,
    ``<dir>/*.py``, ``<top>/**/*.py`` (bd et6o)."""
    if not path.endswith(".py"):
        return False
    if glob == "*.py":
        return "/" not in path
    if "/**/*.py" in glob:
        return path.startswith(glob.split("/**/*.py", 1)[0] + "/")
    dirpart = glob[:-len("/*.py")] if glob.endswith("/*.py") else ""
    rest = path.removeprefix(dirpart + "/") if dirpart else ""
    return bool(rest) and rest != path and "/" not in rest


def _find_matching_glob(
    globs: list[str], keywords: tuple[str, ...], path: str = "",
    prefer_dirs: tuple[str, ...] = (),
) -> str | None:
    """Pick a python_glob: prefer one rooted at a *prefer_dirs* directory
    segment (FastAPI ``routers/`` lives apart from its app-instantiation
    file), then one covering detection *path* (bd et6o), then a keyword."""
    pref = next((g for g in globs if any(
        seg in prefer_dirs
        for seg in g.rsplit("/*.py", 1)[0].replace("/**", "").split("/")
    )), None) if prefer_dirs else None
    hit = pref or next(
        (g for g in globs if path and _glob_matches_path(g, path)), None)
    return hit or next(
        (g for g in globs if any(k in g.lower() for k in keywords)), None)


def _add_framework_sources(
    sources: list[str], frameworks: list[tuple[str, str, str]],
    python_globs: list[str],
) -> None:
    """Append framework-specific source entries.

    For route-extraction strategies, ``prefer_dirs`` steers the glob at the
    directory where route declarations live. FastAPI's ``APIRouter`` files
    sit under ``routers/``, which is a *different* directory from the file
    that runs ``FastAPI()`` (the detection path) -- without this preference
    the generated config globbed the app directory and emitted zero routes.
    """
    fw_to_path = {fw: path for fw, _strategy, path in frameworks}
    fallback = python_globs[0] if python_globs else "**/*.py"
    for fw, kw, prefer, node_type, strategy, label, hint in (
        ("SQLAlchemy", ("domain", "model", "entities", "libs"), (), "entity", "sqlalchemy", "SQLAlchemy domain models", "model directory"),
        ("FastAPI", ("router", "route", "api", "services"), ("routers", "router"), "route", "fastapi", "FastAPI routes", "router directory"),
        ("Flask", ("app", "blueprints", "views", "routes", "api", "flask"), (), "route", "flask", "Flask routes", "app/blueprints directory"),
    ):
        if fw in fw_to_path:
            matched = _find_matching_glob(python_globs, kw, fw_to_path[fw], prefer)
            sources.append(_source_entry(
                matched or fallback, node_type, strategy,
                comment=label if matched else f"{label} (adjust glob to match your {hint})",
            ))
    if "Pydantic" in fw_to_path:
        matched = _find_matching_glob(python_globs,
            ("contract", "schema", "dto", "libs"), fw_to_path["Pydantic"])
        if matched:
            sources.append(_source_entry(matched, "contract", "pydantic",
                comment="Pydantic contracts/schemas"))
    if "HTTPClient" in fw_to_path:
        # Outbound HTTP call sites (httpx/requests) are not confined to a
        # conventional directory the way FastAPI routers are, so there is
        # no ``prefer_dirs`` and no keyword steer -- prefer the python_glob
        # covering the import's detection path, else the first glob. The
        # entry mirrors the hand-written polyrepo example: ``type: file``,
        # ``strategy: http_client`` on the same glob shape as python_module
        # so the rpc:http:out nodes land for cross-repo call resolution.
        matched = _find_matching_glob(python_globs, (), fw_to_path["HTTPClient"])
        sources.append(_source_entry(
            matched or fallback, "file", "http_client",
            comment="Outbound HTTP client calls"
            if matched else "Outbound HTTP client calls (adjust glob to match your sources)",
        ))


def _add_go_framework_sources(
    sources: list[str], frameworks: list[tuple[str, str, str]],
) -> None:
    """Append Go framework-strategy source entries (ADR 0071, GO-2).

    Mirrors :func:`_add_framework_sources` for the Go ecosystem. gin is
    detected by :data:`weld.init_detect.FRAMEWORK_PATTERNS` (the
    canonical ``"github.com/gin-gonic/gin"`` import path); when present we
    emit a ``gin`` source entry over ``**/*.go``. The entry is appended
    *before* the tree-sitter Go entry by the caller so the canonical
    tree-sitter ``file:`` node wins the later orchestrator merge over
    gin's thin boundary-file placeholder.
    """
    detected = {fw for fw, _strategy, _path in frameworks}
    if "Gin" in detected:
        sources.append(_source_entry(
            "**/*.go", "route", "gin",
            comment="gin HTTP routes (route nodes per handler registration)",
        ))


def _add_rust_framework_sources(
    sources: list[str], frameworks: list[tuple[str, str, str]],
) -> None:
    """Append Rust framework-strategy source entries (ADR 0071).

    Mirrors :func:`_add_go_framework_sources` for the Rust ecosystem.
    axum is detected by :data:`weld.init_detect.FRAMEWORK_PATTERNS` (the
    ``use axum::`` / ``use axum;`` declaration); when present we emit an
    ``axum`` source entry over ``**/*.rs``. The entry is appended
    *before* the tree-sitter Rust entry by the caller so the canonical
    tree-sitter ``file:`` node wins the later orchestrator merge over
    axum's thin boundary-file placeholder.
    """
    detected = {fw for fw, _strategy, _path in frameworks}
    if "Axum" in detected:
        sources.append(_source_entry(
            "**/*.rs", "route", "axum",
            comment="axum HTTP routes (route nodes per .route registration)",
        ))
