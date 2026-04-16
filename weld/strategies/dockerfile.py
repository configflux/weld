"""Strategy: Dockerfile nodes with base image info."""

from __future__ import annotations

from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Dockerfile nodes with base image info."""
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
        base_image = ""
        try:
            for line in df.read_text(encoding="utf-8").splitlines():
                if line.strip().upper().startswith("FROM "):
                    base_image = (
                        line.strip().split(None, 1)[1].split(" AS ")[0].strip()
                    )
                    break
        except OSError:
            continue

        stem = df.stem.replace(".", "_")
        service_map = {
            "api": "service:api",
            "web": "service:web",
            "worker": "service:worker",
        }
        nid = f"dockerfile:{stem}"
        nodes[nid] = {
            "type": "dockerfile",
            "label": df.name,
            "props": {
                "file": rel_path,
                "base_image": base_image,
                "source_strategy": "dockerfile",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["build"],
            },
        }
        service_id = service_map.get(stem)
        if service_id:
            edges.append(
                {
                    "from": nid,
                    "to": service_id,
                    "type": "builds",
                    "props": {
                        "source_strategy": "dockerfile",
                        "confidence": "definite",
                    },
                }
            )

    return StrategyResult(nodes, edges, discovered_from)
