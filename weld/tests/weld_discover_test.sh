#!/usr/bin/env bash
set -euo pipefail

# weld_discover test: runs AST discovery against the real codebase and validates counts.
#
# This test adapts to either the monorepo or a standalone weld repo.
# It detects the context by checking for monorepo-specific directories
# (services/, apps/) under the repo root.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"
ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"

TMPFILE="$(mktemp)"
TMPDIR_CLEANUP=""
trap 'rm -f "${TMPFILE}"; [[ -n "${TMPDIR_CLEANUP}" ]] && rm -rf "${TMPDIR_CLEANUP}"' EXIT

# If the repo has no .weld/discover.yaml yet (e.g. fresh standalone clone),
# bootstrap one via `wd init` so that `discover` has sources to scan.
# To avoid dirtying the working tree, generate into a temp directory and
# copy it in, then clean up in the trap.
CREATED_DISCOVER="false"
if [[ ! -f "${ROOT}/.weld/discover.yaml" ]]; then
  mkdir -p "${ROOT}/.weld"
  weld_in_root "${ROOT}" "${ROOT}" init --output "${ROOT}/.weld/discover.yaml" \
    > /dev/null 2>&1
  CREATED_DISCOVER="true"
fi

weld_in_root "${ROOT}" "${ROOT}" discover > "${TMPFILE}"

# Clean up the bootstrapped discover.yaml if we created it
if [[ "${CREATED_DISCOVER}" == "true" ]]; then
  rm -f "${ROOT}/.weld/discover.yaml"
  rmdir "${ROOT}/.weld" 2>/dev/null || true
fi

# Validate JSON
python3 -c "import json; json.load(open('${TMPFILE}'))" || {
  echo "FAIL: discover output is not valid JSON"
  exit 1
}

# Detect repo context: monorepo has services/ and apps/ directories
IS_MONOREPO="false"
if [[ -d "${ROOT}/services" && -d "${ROOT}/apps" ]]; then
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
    # --- Standalone weld repo ---
    # The standalone repo contains weld/ source code. Discovery finds nodes
    # from whatever strategies wd init auto-detected (which may include
    # sqlalchemy/fastapi from test fixtures, agent definitions, config files,
    # and ROS2 fixture nodes). The exact mix depends on the discover.yaml
    # that was auto-generated.

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
