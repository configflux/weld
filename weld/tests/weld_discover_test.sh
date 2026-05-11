#!/usr/bin/env bash
set -euo pipefail

# weld_discover test: runs AST discovery against a hermetic copy of the
# weld source tree and validates the resulting graph.
#
# Hermetic-copy design (bd fw90)
# -------------------------------
# Earlier revisions ran ``wd init`` / ``wd discover`` directly against
# ``${ROOT}``, the real source tree. That path:
#   1. created ``${ROOT}/.weld/discover.yaml`` when missing (then deleted
#      it in a trap), and
#   2. wrote ``${ROOT}/.weld/file_index.json`` plus the sqlite sidecar
#      regardless of whether ``--output`` was supplied (see
#      ``weld/_discover_sidecar.py::persist_file_index`` and
#      ``weld.discover.discover``).
# Under ``bazel test //...`` that producer-side mutation raced with
# consumer tests reading those same files (notably
# ``weld_graph_integrity_regression_test.py``).
#
# Producer-side fix: stage an explicit-allowlist copy of the weld
# package source into a tmpdir via the shared helper at
# ``weld/tests/_source_tree_copy.py`` (bd 6h8b), then run ``wd init``
# and ``wd discover`` against that copy. The real source tree is never
# touched. The copy footprint is ~2 MB (``weld/`` minus ``tests/`` and
# ``__pycache__`` via :func:`wheel_build_allowlist`) so 30 concurrent
# tests staging copies in parallel does not thrash disk -- this was the
# failure mode of the reverted ``cp -rL <full source>`` attempt
# (commits 1ee8e2b + c6c2f93).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"
ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"

TMPFILE="$(mktemp)"
COPY_ROOT="$(mktemp -d -t weld-discover-fw90.XXXXXX)"
trap 'rm -f "${TMPFILE}"; rm -rf "${COPY_ROOT}"' EXIT

# 1. Stage hermetic copy. Allowlist is:
#    - all of ``${ROOT}/weld`` minus ``tests`` (~6 MB of fixtures) and
#      ``__pycache__`` (computed by :func:`wheel_build_allowlist`).
# Plus a separate top-level ``pyproject.toml`` at ``${COPY_ROOT}`` so
# that ``wd init`` (which inspects ``ROOT_CONFIG_NAMES`` at the root,
# see ``weld/init_detect_constants.py``) detects at least one root
# config and emits a ``config_file`` strategy entry. In this repo the
# canonical ``pyproject.toml`` lives at ``weld/pyproject.toml``, not
# at the repo root, so we re-anchor a copy at ``${COPY_ROOT}``. This
# matches the publish-clone-style ``DEFAULT_ALLOWLIST`` semantics the
# helper anticipates -- bd 6h8b notes that the literal default is the
# publish-clone shape (``weld``, ``pyproject.toml``, ``.weld``) while
# this repo's pyproject is nested.
PYTHONPATH="$(weld_pythonpath "${ROOT}")" python3 - <<PY
import shutil
from pathlib import Path
from weld.tests._source_tree_copy import copy_weld_source, wheel_build_allowlist
src = Path("${ROOT}/weld")
dest = Path("${COPY_ROOT}/weld")
copy_weld_source(src, dest, allowlist=wheel_build_allowlist(src))
# Re-anchor pyproject.toml at the copy root so wd init detects it as a
# root config. wheel_build_allowlist already copied it as
# ${COPY_ROOT}/weld/pyproject.toml; we additionally stage one at
# ${COPY_ROOT}/pyproject.toml. The two files are byte-identical.
pyproject_in_weld = dest / "pyproject.toml"
if pyproject_in_weld.is_file():
    shutil.copy2(pyproject_in_weld, Path("${COPY_ROOT}") / "pyproject.toml")
PY

# 2. Generate a fresh discover.yaml inside the copy. ``--force`` keeps
# the operation idempotent if a downstream consumer ever copies
# ``.weld/`` into the allowlist; today the allowlist excludes it so
# ``--force`` is harmless.
mkdir -p "${COPY_ROOT}/.weld"
weld_in_root "${COPY_ROOT}" "${COPY_ROOT}" init \
  --output "${COPY_ROOT}/.weld/discover.yaml" --force > /dev/null 2>&1

# 3. Run discover against the copy. Output goes to ${TMPFILE} (stdout).
# Discovery's side-effect writes (``.weld/file_index.json``, sqlite
# sidecar, etc.) land inside ``${COPY_ROOT}/.weld`` which the trap
# removes -- the real source tree stays untouched.
weld_in_root "${COPY_ROOT}" "${COPY_ROOT}" discover > "${TMPFILE}"

# Validate JSON
python3 -c "import json; json.load(open('${TMPFILE}'))" || {
  echo "FAIL: discover output is not valid JSON"
  exit 1
}

# Detect repo context: monorepo has services/ and apps/ directories.
# Inspected against the COPY (not ROOT) because the assertion counts
# below describe what discovery actually saw. The hermetic copy never
# carries services/ or apps/ under the current allowlist, so this is
# always "false" in this repo's layout; the monorepo branch is
# preserved for downstream consumers who extend the allowlist to
# include their own service tree.
IS_MONOREPO="false"
if [[ -d "${COPY_ROOT}/services" && -d "${COPY_ROOT}/apps" ]]; then
  IS_MONOREPO="true"
fi

python3 -c "
import json, sys, os

with open('${TMPFILE}') as f:
    data = json.load(f)

nodes = data['nodes']
edges = data['edges']
is_monorepo = '${IS_MONOREPO}' == 'true'

# Count by type
by_type = {}
for n in nodes.values():
    t = n['type']
    by_type[t] = by_type.get(t, 0) + 1

errors = []

if is_monorepo:
    # --- Monorepo: full node type counts ---
    entity_count = by_type.get('entity', 0)
    if entity_count < 20:
        errors.append(f'expected >=20 entity nodes, got {entity_count}')

    enum_count = by_type.get('enum', 0)
    if enum_count < 8:
        errors.append(f'expected >=8 enum nodes, got {enum_count}')

    route_count = by_type.get('route', 0)
    if route_count < 5:
        errors.append(f'expected >=5 route nodes, got {route_count}')

    contract_count = by_type.get('contract', 0)
    if contract_count < 15:
        errors.append(f'expected >=15 contract nodes, got {contract_count}')

    stage_count = by_type.get('stage', 0)
    if stage_count < 4:
        errors.append(f'expected >=4 stage nodes, got {stage_count}')

    dockerfile_count = by_type.get('dockerfile', 0)
    if dockerfile_count < 3:
        errors.append(f'expected >=3 dockerfile nodes, got {dockerfile_count}')

    agent_count = by_type.get('agent', 0)
    if agent_count < 8:
        errors.append(f'expected >=8 agent nodes, got {agent_count}')

    command_count = by_type.get('command', 0)
    if command_count < 5:
        errors.append(f'expected >=5 command nodes, got {command_count}')

    tool_count = by_type.get('tool', 0)
    if tool_count < 5:
        errors.append(f'expected >=5 tool nodes, got {tool_count}')

    workflow_count = by_type.get('workflow', 0)
    if workflow_count < 1:
        errors.append(f'expected >=1 workflow nodes, got {workflow_count}')

    config_count = by_type.get('config', 0)
    if config_count < 3:
        errors.append(f'expected >=3 config nodes, got {config_count}')

    doc_count = by_type.get('doc', 0)
    if doc_count < 9:
        errors.append(f'expected >=9 doc nodes, got {doc_count}')

    file_count = by_type.get('file', 0)
    if file_count < 50:
        errors.append(f'expected >=50 file nodes, got {file_count}')

    total = len(nodes)
    if total < 300:
        errors.append(f'expected >=300 total nodes, got {total}')

    # --- Monorepo edge counts ---
    fk_edges = [e for e in edges if e['type'] == 'depends_on']
    if len(fk_edges) < 10:
        errors.append(f'expected >=10 depends_on edges, got {len(fk_edges)}')

    resp_edges = [e for e in edges if e['type'] == 'responds_with']
    if len(resp_edges) < 5:
        errors.append(f'expected >=5 responds_with edges, got {len(resp_edges)}')

    builds_edges = [e for e in edges if e['type'] == 'builds']
    if len(builds_edges) < 2:
        errors.append(f'expected >=2 builds edges, got {len(builds_edges)}')

    invokes_edges = [e for e in edges if e['type'] == 'invokes']
    if len(invokes_edges) < 5:
        errors.append(f'expected >=5 invokes edges, got {len(invokes_edges)}')

    contains_edges = [e for e in edges if e['type'] == 'contains']
    if len(contains_edges) < 20:
        errors.append(f'expected >=20 contains edges, got {len(contains_edges)}')

    # --- Monorepo structural checks ---
    if 'entity:Store' not in nodes:
        errors.append('entity:Store not found')
    if 'entity:Offer' not in nodes:
        errors.append('entity:Offer not found')
    if 'stage:extraction' not in nodes:
        errors.append('stage:extraction not found')
    if 'service:api' not in nodes:
        errors.append('service:api not found')

    if 'dockerfile:api' not in nodes:
        errors.append('dockerfile:api not found')
    if 'agent:tdd' not in nodes:
        errors.append('agent:tdd not found')
    if 'workflow:ci' not in nodes:
        errors.append('workflow:ci not found')
    if 'compose:e2e' not in nodes:
        errors.append('compose:e2e not found')
    if 'package:weld' not in nodes:
        errors.append('package:weld not found')
    if 'file:weld/discover' not in nodes:
        errors.append('file:weld/discover not found')
    if 'file:weld/repo_boundary' not in nodes:
        errors.append('file:weld/repo_boundary not found')
    if 'doc:weld-guide/onboarding' not in nodes:
        errors.append('doc:weld-guide/onboarding not found')

    for legacy in ['tool:weld_discover', 'tool:weld_file_index', 'tool:weld_graph', 'tool:weld_init']:
        if legacy in nodes:
            errors.append(f'legacy wrapper node should not be rediscovered: {legacy}')

    # --- Monorepo file node checks ---
    api_app = nodes.get('file:api/app', {})
    if not api_app:
        errors.append('file:api/app not found')
    else:
        props = api_app.get('props', {})
        if 'imports_from' not in props:
            errors.append('file:api/app missing props.imports_from')
        if 'line_count' not in props:
            errors.append('file:api/app missing props.line_count')

    acq_service = nodes.get('file:worker/acquisition/service', {})
    if not acq_service:
        errors.append('file:worker/acquisition/service not found')
    else:
        props = acq_service.get('props', {})
        if 'imports_from' not in props:
            errors.append('file:worker/acquisition/service missing props.imports_from')
        if 'line_count' not in props:
            errors.append('file:worker/acquisition/service missing props.line_count')
        if not isinstance(props.get('imports_from', None), list):
            errors.append('file:worker/acquisition/service imports_from should be a list')

    store = nodes.get('entity:Store', {})
    if store:
        props = store.get('props', {})
        if 'table' not in props:
            errors.append('entity:Store missing props.table')
        if 'file' not in props:
            errors.append('entity:Store missing props.file')
        if 'columns' not in props:
            errors.append('entity:Store missing props.columns')

    tdd = nodes.get('agent:tdd', {})
    if tdd:
        props = tdd.get('props', {})
        if 'file' not in props:
            errors.append('agent:tdd missing props.file')
        if 'description' not in props:
            errors.append('agent:tdd missing props.description')
        else:
            description = str(props['description']).lower()
            if 'spec-driven' not in description:
                errors.append('agent:tdd description should mention spec-driven delivery')

else:
    # --- Standalone weld repo (hermetic copy mode) ---
    # The hermetic copy contains only weld/ (minus tests/, minus
    # __pycache__/), so 'wd init' auto-detects a small Python project.
    # Discovery emits nodes from whatever strategies init wired in
    # (typically python_module / python_callgraph / python_package for
    # the weld package itself plus config_file for pyproject.toml).

    config_count = by_type.get('config', 0)
    if config_count < 1:
        errors.append(f'expected >=1 config nodes in standalone repo, got {config_count}')

    total = len(nodes)
    if total < 5:
        errors.append(f'expected >=5 total nodes in standalone repo, got {total}')

    # Verify at least some node types were discovered
    if len(by_type) < 2:
        errors.append(f'expected >=2 distinct node types, got {len(by_type)}: {list(by_type.keys())}')

    # Basic structural: meta must be present
    meta = data.get('meta', {})
    if 'version' not in meta:
        errors.append('meta missing version')

if errors:
    print('FAIL: discovery validation errors:')
    for e in errors:
        print(f'  - {e}')
    print(f'Summary: {len(nodes)} nodes, {len(edges)} edges, by_type={json.dumps(by_type)}')
    sys.exit(1)

print(f'PASS: {len(nodes)} nodes, {len(edges)} edges discovered')
print(f'  by_type={json.dumps(by_type)}')
"
