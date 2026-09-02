"""Strategy: Docker Compose service nodes plus build/env/image edges.

ADR 0045 (Layer C2): turn the formerly opaque ``compose:<stem>`` node
into a graph that lets blast-radius reverse-BFS traverse from a
COPY'd source up through the Dockerfile to the compose services that
``build`` from it.

Emitted shape:

- ``compose:<stem>`` node (existing; props.services kept, sorted).
- ``service:<stem>:<name>`` first-class node per declared service.
  ``runtime_image`` prop is set when ``service.image`` is declared and
  ``service.build`` is not (no new node type, no schema bump -- bd
  notes locked the trade-off).
- ``compose --contains--> service`` per service.
- ``service --depends_on--> dockerfile:<path>`` when ``service.build``
  resolves to a Dockerfile path inside the repo. Dangling refs (file not
  on disk) are emitted anyway so graph closure can reconcile; a path
  outside the repo root is dropped, since no node can ever answer it.
- ``service --contains--> file:<env-file>`` per ``env_file:`` entry.
- Legacy ``compose --orchestrates--> service:<bare>`` edges for the
  conventional ``api`` / ``web`` / ``worker`` names are preserved.
"""

from __future__ import annotations

from pathlib import Path

from weld._rel_path import rel_to_root
from weld._yaml import parse_yaml
from weld.strategies._dockerfile_ids import dockerfile_node_id
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult

_LEGACY_SERVICE_MAP: dict[str, str] = {
    "api": "service:api",
    "web": "service:web",
    "worker": "service:worker",
}


def _compose_stem(cf: Path) -> str:
    stem = cf.stem.replace("docker-compose.", "").replace("docker-compose", "default")
    return stem or "default"


def _normalize_under_root(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _resolve_build_dockerfile_id(
    build: object, compose_dir: Path, root: Path,
) -> str | None:
    """Map a ``service.build`` value to the dockerfile node id.

    Forms accepted:

    - ``./api`` (string dir) -> ``<dir>/Dockerfile``.
    - ``./api/Dockerfile`` (string with file) -> direct file.
    - ``{context: ./api, dockerfile: Dockerfile.dev}`` -> joined.

    The id is minted by :func:`weld.strategies._dockerfile_ids.dockerfile_node_id`
    from the resolved path, so it agrees with the node
    :mod:`weld.strategies.dockerfile` emits by construction rather than by a
    comment asking the reader to keep two copies of one expression aligned
    (bd bz5w9). A build path that escapes the repo root returns ``None``: no
    dockerfile node can ever exist for it, so the edge would dangle forever
    rather than be reconciled by graph closure the way a not-yet-written
    in-repo path is.
    """
    if isinstance(build, str):
        candidate = (compose_dir / build).resolve()
        if not candidate.is_file():
            candidate = candidate / "Dockerfile"
    elif isinstance(build, dict):
        ctx_raw = build.get("context", ".")
        df_name = build.get("dockerfile", "Dockerfile")
        candidate = (compose_dir / str(ctx_raw)).resolve() / str(df_name)
    else:
        return None
    rel = _normalize_under_root(candidate, root)
    return None if rel is None else dockerfile_node_id(rel)


def _collect_env_files(svc: dict) -> list[str]:
    raw = svc.get("env_file")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, str)]
    return []


def _service_props(
    name: str, stem: str, svc: dict, file_rel: str,
) -> dict:
    props: dict = {
        "file": file_rel,
        "service_name": name,
        "compose_stem": stem,
        "source_strategy": "compose",
        "authority": "canonical",
        "confidence": "definite",
        "roles": ["config"],
    }
    image = svc.get("image")
    has_build = "build" in svc and svc.get("build") is not None
    if image and not has_build:
        props["runtime_image"] = str(image)
    return props


def _emit_service_edges(
    service_id: str,
    svc: dict,
    compose_dir: Path,
    root: Path,
) -> tuple[list[dict], dict[str, dict]]:
    """Return (edges, env_file_nodes) for one compose service.

    The ``file:<env-file>`` source nodes must be emitted alongside the
    ``contains`` edges so the post-process dedup pass does not silently
    prune the edges as dangling. This mirrors the dockerfile strategy's
    Layer C2 fix-forward behaviour for COPY/ADD source nodes.
    """
    edges: list[dict] = []
    file_nodes: dict[str, dict] = {}
    build = svc.get("build")
    if build is not None:
        df_id = _resolve_build_dockerfile_id(build, compose_dir, root)
        if df_id:
            edges.append(
                {
                    "from": service_id,
                    "to": df_id,
                    "type": "depends_on",
                    "props": {
                        "source_strategy": "compose",
                        "confidence": "definite",
                    },
                },
            )
    for env_file in _collect_env_files(svc):
        env_path = (compose_dir / env_file).resolve()
        rel = _normalize_under_root(env_path, root)
        if rel:
            target = f"file:{rel}"
            label = Path(rel).name
            file_attr = rel
        else:
            target = f"file:{env_file}"
            label = Path(env_file).name
            file_attr = env_file
        # ``roles=["config"]``: env_file refs are runtime
        # configuration files. ``ROLE_VALUES`` does not include
        # "env_file" so we fall back to the broader "config" role,
        # which matches the role on the compose service node itself.
        file_nodes.setdefault(
            target,
            {
                "type": "file",
                "label": label,
                "props": {
                    "file": file_attr,
                    "source_strategy": "compose",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["config"],
                    "origin": "project",
                },
            },
        )
        edges.append(
            {
                "from": service_id,
                "to": target,
                "type": "contains",
                "props": {
                    "source_strategy": "compose",
                    "confidence": "definite",
                },
            },
        )
    return edges, file_nodes


def _parse_compose(cf: Path) -> dict | None:
    try:
        text = cf.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = parse_yaml(text)
    except Exception:  # noqa: BLE001 -- partial parse is OK
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _build_service_records(
    parsed: dict, stem: str, file_rel: str,
) -> tuple[dict[str, dict], list[str]]:
    """Return ({service_id: node}, sorted-service-name-list)."""
    services_section = parsed.get("services") or {}
    if not isinstance(services_section, dict):
        return {}, []
    names = sorted(
        n for n in services_section.keys()
        if isinstance(n, str) and isinstance(services_section[n], dict)
    )
    nodes: dict[str, dict] = {}
    for name in names:
        svc = services_section[name]
        sid = f"service:{stem}:{name}"
        nodes[sid] = {
            "type": "service",
            "label": name,
            "props": _service_props(name, stem, svc, file_rel),
        }
    return nodes, names


def _legacy_orchestrates_edges(
    compose_id: str, names: list[str],
) -> list[dict]:
    out: list[dict] = []
    for name in names:
        legacy = _LEGACY_SERVICE_MAP.get(name)
        if legacy:
            out.append(
                {
                    "from": compose_id,
                    "to": legacy,
                    "type": "orchestrates",
                    "props": {
                        "source_strategy": "compose",
                        "confidence": "definite",
                    },
                },
            )
    return out


def _process_compose_file(
    cf: Path, root: Path,
) -> tuple[dict[str, dict], list[dict]] | None:
    parsed = _parse_compose(cf)
    if parsed is None:
        return None
    rel_path = rel_to_root(cf, root)
    stem = _compose_stem(cf)
    compose_id = f"compose:{stem}"

    service_nodes, names = _build_service_records(parsed, stem, rel_path)

    nodes: dict[str, dict] = {
        compose_id: {
            "type": "compose",
            "label": cf.name,
            "props": {
                "file": rel_path,
                "services": list(names),
                "source_strategy": "compose",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        },
    }
    nodes.update(service_nodes)

    edges: list[dict] = []
    services_section = parsed.get("services") or {}
    compose_dir = cf.parent
    for name in names:
        sid = f"service:{stem}:{name}"
        edges.append(
            {
                "from": compose_id,
                "to": sid,
                "type": "contains",
                "props": {
                    "source_strategy": "compose",
                    "confidence": "definite",
                },
            },
        )
        svc = services_section.get(name) or {}
        svc_edges, svc_file_nodes = _emit_service_edges(
            sid, svc, compose_dir, root,
        )
        edges.extend(svc_edges)
        for fid, fnode in svc_file_nodes.items():
            nodes.setdefault(fid, fnode)
    edges.extend(_legacy_orchestrates_edges(compose_id, names))
    return nodes, edges


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract compose nodes plus service/build/env_file edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])

    # bd b9xgd: this used to resolve its own glob -- ``(root / pattern).parent``,
    # then ``parent = root`` when that was not a directory, then one
    # directory's worth of ``glob()``. That is the copy ADR 0112 says is gone,
    # kept here because this strategy was never migrated, and its fallback made
    # the failure *worse* than the silent one its siblings had: for any ``**``
    # pattern or wildcard directory segment the parent is a literal path and
    # never a directory, so the strategy quietly globbed the repo root instead
    # and emitted the wrong set -- a partial result that looks like an answer.
    for cf in resolve_glob(root, pattern, excludes):
        rel_path = rel_to_root(cf, root)
        discovered_from.append(rel_path)
        result = _process_compose_file(cf, root)
        if result is None:
            continue
        cf_nodes, cf_edges = result
        nodes.update(cf_nodes)
        edges.extend(cf_edges)

    return StrategyResult(nodes, edges, discovered_from)
