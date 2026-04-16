"""Strategy: Deploy surface detection from infrastructure configs.

Identifies deployment-related configuration files (docker-compose with
deploy blocks, Cloud Run service YAMLs, Terraform files, etc.) and
models them as ``deploy`` type nodes in the graph.

Only files that contain deploy/infrastructure markers are promoted to
deploy nodes — generic YAML or config files are skipped to avoid
false positives.

"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

# -- Deploy signal detection -------------------------------------------------

#: Patterns in file content that indicate a deploy configuration.
_DEPLOY_SIGNALS: list[re.Pattern[str]] = [
    # Docker Compose deploy section
    re.compile(r"^\s+deploy:", re.MULTILINE),
    # Knative / Cloud Run service
    re.compile(r"kind:\s*Service", re.MULTILINE),
    # Kubernetes deployment
    re.compile(r"kind:\s*Deployment", re.MULTILINE),
    # Terraform resource blocks
    re.compile(r'resource\s+"', re.MULTILINE),
    # Cloud Build configuration
    re.compile(r"^steps:", re.MULTILINE),
    # Docker Compose services with image/build
    re.compile(r"^\s+image:", re.MULTILINE),
    # app.yaml (GCP App Engine)
    re.compile(r"^runtime:", re.MULTILINE),
]

#: File extensions / name patterns that are inherently deploy-related.
_DEPLOY_FILE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"docker-compose.*\.ya?ml$", re.IGNORECASE),
    re.compile(r"\.tf$"),
    re.compile(r"cloudbuild.*\.ya?ml$", re.IGNORECASE),
    re.compile(r"app\.ya?ml$", re.IGNORECASE),
    re.compile(r"service\.ya?ml$", re.IGNORECASE),
    re.compile(r"deployment\.ya?ml$", re.IGNORECASE),
    re.compile(r"\.tfvars$"),
]

#: Known service names to associate deploy surfaces with service nodes.
_SERVICE_MAP: dict[str, str] = {
    "api": "service:api",
    "web": "service:web",
    "worker": "service:worker",
}

def _is_deploy_filename(name: str) -> bool:
    """Check if filename pattern alone indicates a deploy config."""
    return any(p.search(name) for p in _DEPLOY_FILE_PATTERNS)

def _has_deploy_signals(text: str) -> bool:
    """Check if file content contains deploy-related markers."""
    return any(p.search(text) for p in _DEPLOY_SIGNALS)

def _detect_services(text: str) -> list[str]:
    """Detect service names referenced in the deploy config."""
    found: list[str] = []
    for svc_name, svc_id in _SERVICE_MAP.items():
        # Look for the service name as a key or in image references
        if re.search(rf"\b{re.escape(svc_name)}\b", text, re.IGNORECASE):
            found.append(svc_id)
    return found

def _deploy_kind(path: Path, text: str) -> str:
    """Infer the deploy surface kind from the file."""
    name = path.name.lower()
    if name.endswith(".tf") or name.endswith(".tfvars"):
        return "terraform"
    if "docker-compose" in name:
        return "compose"
    if "cloudbuild" in name:
        return "cloudbuild"
    if re.search(r"kind:\s*Service", text):
        return "cloud-run"
    if re.search(r"kind:\s*Deployment", text):
        return "kubernetes"
    if re.search(r"^runtime:", text, re.MULTILINE):
        return "app-engine"
    return "config"

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract deploy surface nodes from infrastructure config files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    # Handle recursive globs
    if "**" in pattern:
        matched = sorted(root.glob(pattern))
        matched = filter_glob_results(root, matched)
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return StrategyResult(nodes, edges, discovered_from)
        matched = sorted(parent.glob(Path(pattern).name))

    for cfg_file in matched:
        if not cfg_file.is_file():
            continue
        if should_skip(cfg_file, excludes):
            continue

        rel_path = str(cfg_file.relative_to(root))
        discovered_from.append(rel_path)

        try:
            text = cfg_file.read_text(encoding="utf-8")
        except OSError:
            continue

        # Only promote to deploy node if file is deploy-related
        is_deploy = _is_deploy_filename(cfg_file.name) or _has_deploy_signals(text)
        if not is_deploy:
            continue

        kind = _deploy_kind(cfg_file, text)
        stem = cfg_file.stem.replace(".", "_").replace("-", "_")
        nid = f"deploy:{stem}"

        # Derive a human-readable label
        label = cfg_file.name

        nodes[nid] = {
            "type": "deploy",
            "label": label,
            "props": {
                "file": rel_path,
                "deploy_kind": kind,
                "source_strategy": "deploy_surface",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        }

        # Create edges to services referenced in the config
        for svc_id in _detect_services(text):
            edges.append({
                "from": nid,
                "to": svc_id,
                "type": "configures",
                "props": {
                    "source_strategy": "deploy_surface",
                    "confidence": "inferred",
                },
            })

    return StrategyResult(nodes, edges, discovered_from)
