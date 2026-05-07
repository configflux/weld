"""Strategy: Dockerfile nodes with base image info and COPY/ADD source edges.

ADR 0045 (Layer C2): emit ``dockerfile:<stem> --contains--> file:<src>``
for each ``COPY`` and ``ADD`` instruction whose source resolves to a
repo-relative path. Multi-stage ``COPY --from=<stage>`` and URL ``ADD``
are explicitly skipped (counters surface on the dockerfile node's props
so consumers see the overapproximation).

Directory-COPY bridge: when ``COPY ./app /service/app`` resolves to an
on-disk directory, the strategy ALSO emits
``file:<dir> --contains--> file:<dir>/<child>`` edges for every regular
interior file. Without this bridge, ``wd impact app/lib.py`` cannot
reverse-traverse to the dockerfile (the only contains-edge from the
dockerfile is to ``file:app``, with no edge linking ``file:app/lib.py``
back to ``file:app``). Walks are bounded: the resolved dir must live
strictly inside the discovery root (``.`` / repo root is rejected with a
``dir_walk_skipped`` counter), symlinks are skipped, and excluded
directory names (``.git``, ``node_modules``, ``__pycache__``) are
honoured exactly as the rest of discovery does.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    is_excluded_dir_name,
    should_skip,
)

# Conventional service-id mapping kept for back-compat with older
# fixtures and the legacy ``dockerfile --builds--> service:<bare>`` edge.
_LEGACY_SERVICE_MAP: dict[str, str] = {
    "api": "service:api",
    "web": "service:web",
    "worker": "service:worker",
}


def _split_copy_args(rest: str) -> list[str]:
    """Tokenize a ``COPY``/``ADD`` argument list, ignoring flag tokens.

    Flags like ``--from=builder`` and ``--chown=foo`` are consumed by the
    caller to set per-instruction state; they must not show up in the
    positional arg list. Everything else is shell-split so quoting works
    on paths with spaces.
    """
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    return [t for t in tokens if not t.startswith("--")]


def _is_url(token: str) -> bool:
    return token.startswith(("http://", "https://"))


def _is_glob(token: str) -> bool:
    return any(ch in token for ch in "*?[")


def _resolve_src(
    src: str, dockerfile_dir: Path, root: Path,
) -> str | None:
    """Resolve a COPY/ADD source token to a repo-relative posix path.

    Returns ``None`` when the source escapes the repo root; the caller
    should drop that case and bump ``unresolved_paths_dropped``.
    """
    candidate = (dockerfile_dir / src).resolve()
    try:
        rel = candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return rel.as_posix()


def _parse_copy_add_sources(
    line: str,
) -> tuple[list[str], bool]:
    """Return (source-tokens, multistage-flag) for one COPY/ADD line.

    Multi-stage ``--from=<stage>`` is detected before tokenizing the
    positional args. The destination (last token) is dropped.
    """
    upper = line.lstrip().upper()
    if upper.startswith("COPY "):
        rest = line.lstrip()[len("COPY "):]
    elif upper.startswith("ADD "):
        rest = line.lstrip()[len("ADD "):]
    else:
        return [], False
    multistage = "--from=" in rest
    args = _split_copy_args(rest)
    if len(args) < 2:
        return [], multistage
    sources = args[:-1]
    return sources, multistage


def _walk_dir_children(abs_dir: Path, root_resolved: Path) -> list[str]:
    """Return sorted repo-relative posix paths for files under *abs_dir*.

    *root_resolved* must be the caller's already-resolved discovery
    root; passing it in avoids re-running ``Path.resolve()`` once per
    walk in a loop.

    Bounds:

    * skips symlinks (file or dir) -- they would let the walk escape the
      repo root in surprising ways and the dockerfile build wouldn't
      have followed them anyway without an explicit ``COPY --link``.
    * honours :func:`is_excluded_dir_name` so vendored ``node_modules``
      / ``__pycache__`` / ``.git`` trees never balloon the contains-edge
      count.
    * yields only files whose resolved path stays strictly under *root*
      (defensive; symlink skip above already covers the common escape
      vector).
    * deterministic: results are returned in lexicographic order so
      golden bytes stay stable across filesystems.
    """
    children: list[str] = []
    # Manual recursion so we can prune excluded directories cheaply
    # without paying ``rglob`` cost on a vendored ``node_modules`` tree.
    stack: list[Path] = [abs_dir]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            name = entry.name
            if entry.is_dir():
                if is_excluded_dir_name(name):
                    continue
                stack.append(entry)
                continue
            if not entry.is_file():
                continue
            try:
                resolved = entry.resolve()
                rel = resolved.relative_to(root_resolved)
            except (OSError, ValueError):
                continue
            children.append(rel.as_posix())
    children.sort()
    return children


def _process_dockerfile(
    df: Path, root: Path, rel_path: str,
) -> tuple[dict, list[dict], dict[str, dict]]:
    """Return (props, edges, file_nodes) for a single Dockerfile.

    *file_nodes* is the set of ``file:<rel-path>`` source nodes the
    Dockerfile COPYs / ADDs from. They must be emitted alongside the
    ``contains`` edges so :func:`weld._discover_postprocess._clean_and_dedup_edges`
    does not silently prune the edges as dangling — the bug that
    motivated this fix-forward (Layer C2 QA blocker).
    """
    base_image = ""
    nid = f"dockerfile:{df.stem.replace('.', '_')}"
    edges: list[dict] = []
    file_nodes: dict[str, dict] = {}
    multistage_skipped = 0
    glob_count = 0
    unresolved_paths_dropped = 0
    dir_walk_skipped = 0
    seen_targets: set[str] = set()
    # Track which directory ``file:<dir>`` nodes have already been
    # walked so a Dockerfile that COPYs the same directory under two
    # different lines does not double-emit interior edges.
    walked_dirs: set[str] = set()
    root_resolved = root.resolve()

    try:
        text = df.read_text(encoding="utf-8")
    except OSError:
        return {}, [], {}

    dockerfile_dir = df.parent
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        upper = line.upper()
        if not base_image and upper.startswith("FROM "):
            base_image = (
                line.split(None, 1)[1].split(" AS ")[0].strip()
            )
            continue
        if not (upper.startswith("COPY ") or upper.startswith("ADD ")):
            continue
        sources, multistage = _parse_copy_add_sources(line)
        if multistage:
            multistage_skipped += 1
            continue
        for src in sources:
            if _is_url(src):
                continue
            if _is_glob(src):
                # Globs without expansion produce a meaningless target
                # (``file:*.py`` is not a real path on disk and never
                # matches another node). Track the count so the
                # overapproximation is visible on the dockerfile node's
                # props, but drop the edge entirely so downstream
                # consumers do not see a phantom contains-edge.
                glob_count += 1
                continue
            resolved = _resolve_src(src, dockerfile_dir, root)
            if resolved is None:
                unresolved_paths_dropped += 1
                continue
            target = f"file:{resolved}"
            if target in seen_targets:
                continue
            seen_targets.add(target)
            # Emit the source-file node alongside the edge. Without
            # this, ``_clean_and_dedup_edges`` prunes the contains edge
            # because its ``to`` is not a known node id, and the
            # blast-radius ``wd impact <copied-source>`` query returns
            # nothing. ID format is ``file:<rel-posix-with-ext>`` to
            # match the existing strategy contract (the ext-stripped
            # form used by ``python_module`` is reserved for Python
            # source modules and would mis-merge with COPY targets that
            # are config files like requirements.txt).
            # ``roles=["build"]`` mirrors the role on the parent
            # dockerfile node: a COPY/ADD source is an input to the
            # container build. ``ROLE_VALUES`` does not currently have
            # a finer-grained "build_input" entry; reusing "build"
            # keeps the contract validator happy and is semantically
            # accurate.
            file_nodes[target] = {
                "type": "file",
                "label": Path(resolved).name,
                "props": {
                    "file": resolved,
                    "source_strategy": "dockerfile",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["build"],
                    "origin": "project",
                },
            }
            edges.append(
                {
                    "from": nid,
                    "to": target,
                    "type": "contains",
                    "props": {
                        "source_strategy": "dockerfile",
                        "confidence": "definite",
                    },
                },
            )

            # Directory-COPY bridge: when the resolved source is an
            # on-disk directory, emit ``file:<dir> --contains-->
            # file:<child>`` edges so reverse-BFS from any interior file
            # reaches the dockerfile. ``COPY .`` / ``COPY ./`` lands
            # the resolved path on the discovery root itself, which
            # would walk the entire repo; reject that case explicitly
            # and surface a counter so the overapproximation is
            # visible on the dockerfile node.
            abs_dir = (dockerfile_dir / src).resolve()
            try:
                if not abs_dir.is_dir() or abs_dir.is_symlink():
                    continue
            except OSError:
                continue
            if abs_dir == root_resolved:
                dir_walk_skipped += 1
                continue
            if target in walked_dirs:
                continue
            walked_dirs.add(target)
            for child_rel in _walk_dir_children(abs_dir, root_resolved):
                child_id = f"file:{child_rel}"
                if child_id == target:
                    # Defensive: never self-loop a directory back to
                    # itself if a child somehow resolves to the same
                    # rel path (shouldn't happen, but cheap to check).
                    continue
                # Only set the file node if no other strategy (or this
                # one) has already supplied it; ``setdefault`` semantics
                # match the outer loop's collision rule.
                file_nodes.setdefault(child_id, {
                    "type": "file",
                    "label": Path(child_rel).name,
                    "props": {
                        "file": child_rel,
                        "source_strategy": "dockerfile",
                        "authority": "derived",
                        "confidence": "definite",
                        "roles": ["build"],
                        "origin": "project",
                    },
                })
                edges.append(
                    {
                        "from": target,
                        "to": child_id,
                        "type": "contains",
                        "props": {
                            "source_strategy": "dockerfile",
                            "confidence": "definite",
                        },
                    },
                )

    legacy_service = _LEGACY_SERVICE_MAP.get(df.stem.replace(".", "_"))
    if legacy_service:
        edges.append(
            {
                "from": nid,
                "to": legacy_service,
                "type": "builds",
                "props": {
                    "source_strategy": "dockerfile",
                    "confidence": "definite",
                },
            },
        )

    props: dict = {
        "file": rel_path,
        "base_image": base_image,
        "source_strategy": "dockerfile",
        "authority": "canonical",
        "confidence": "definite",
        "roles": ["build"],
    }
    if multistage_skipped:
        props["multistage_skipped"] = multistage_skipped
    if glob_count:
        props["glob_count"] = glob_count
    if unresolved_paths_dropped:
        props["unresolved_paths_dropped"] = unresolved_paths_dropped
    if dir_walk_skipped:
        props["dir_walk_skipped"] = dir_walk_skipped
    return props, edges, file_nodes


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Dockerfile nodes plus COPY/ADD source-contains edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    parent = (root / pattern).parent
    if not parent.is_dir():
        return StrategyResult(nodes, edges, discovered_from)

    for df in filter_glob_results(root, sorted(parent.glob(Path(pattern).name))):
        if should_skip(df, excludes):
            continue
        rel_path = str(df.relative_to(root))
        discovered_from.append(rel_path)

        props, df_edges, df_file_nodes = _process_dockerfile(df, root, rel_path)
        if not props:
            continue
        nid = f"dockerfile:{df.stem.replace('.', '_')}"
        nodes[nid] = {
            "type": "dockerfile",
            "label": df.name,
            "props": props,
        }
        # Merge file-source nodes; never overwrite a node already
        # supplied by another strategy (e.g. python_module's
        # extension-stripped ``file:`` form would never collide here
        # because the dockerfile strategy keeps the extension, but the
        # defensive setdefault keeps the contract explicit).
        for fid, fnode in df_file_nodes.items():
            nodes.setdefault(fid, fnode)
        edges.extend(df_edges)

    return StrategyResult(nodes, edges, discovered_from)
