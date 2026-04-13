"""Strategy: Docker Compose service declarations."""

from __future__ import annotations

import re
from pathlib import Path

from cortex.strategies._helpers import StrategyResult, filter_glob_results

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Docker Compose service declarations."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    parent = (root / pattern).parent
    if not parent.is_dir():
        parent = root

    for cf in filter_glob_results(root, sorted(parent.glob(Path(pattern).name))):
        rel_path = str(cf.relative_to(root))
        discovered_from.append(rel_path)
        stem = cf.stem.replace("docker-compose.", "").replace("docker-compose", "default")
        if stem == "":
            stem = "default"
        nid = f"compose:{stem}"
        services_found: list[str] = []
        in_services = False
        try:
            for line in cf.read_text(encoding="utf-8").splitlines():
                stripped = line.rstrip()
                if stripped == "services:":
                    in_services = True
                    continue
                if in_services:
                    if line and not line[0].isspace():
                        in_services = False
                        continue
                    match = re.match(r"^  (\w[\w-]*):", line)
                    if match:
                        services_found.append(match.group(1))
        except OSError:
            continue

        nodes[nid] = {
            "type": "compose",
            "label": cf.name,
            "props": {
                "file": rel_path,
                "services": services_found,
                "source_strategy": "compose",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        }
        service_map = {
            "api": "service:api",
            "web": "service:web",
            "worker": "service:worker",
        }
        for svc in services_found:
            service_id = service_map.get(svc)
            if service_id:
                edges.append(
                    {
                        "from": nid,
                        "to": service_id,
                        "type": "orchestrates",
                        "props": {
                            "source_strategy": "compose",
                            "confidence": "definite",
                        },
                    }
                )

    return StrategyResult(nodes, edges, discovered_from)
