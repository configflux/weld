#!/usr/bin/env bash
set -euo pipefail

# cortex CLI test: exercises graph engine commands against a temporary graph.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cortex/tests/cortex_test_lib.sh
source "${SCRIPT_DIR}/cortex_test_lib.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

REPO_ROOT="$(cortex_test_repo_root "${SCRIPT_DIR}")"
ROOT="${TMPDIR}/project"
mkdir -p "${ROOT}/.cortex"

kg_cmd() {
  cortex_in_root "${REPO_ROOT}" "${ROOT}" "$@"
}

# --- add-node ---
out="$(kg_cmd add-node entity:Store --type entity --label Store --props '{"table":"store"}')"
echo "${out}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['id']=='entity:Store', f'add-node id: {d}'"

out="$(kg_cmd add-node entity:Source --type entity --label Source --props '{}')"
echo "${out}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['id']=='entity:Source'"

out="$(kg_cmd add-node entity:Flyer --type entity --label Flyer --props '{}')"

# --- add-edge ---
out="$(kg_cmd add-edge entity:Source entity:Store --type depends_on --props '{"fk":"store.id"}')"
echo "${out}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['type']=='depends_on'"

kg_cmd add-edge entity:Flyer entity:Source --type depends_on --props '{}' > /dev/null

# --- stats ---
out="$(kg_cmd stats)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['total_nodes']==3, f'expected 3 nodes, got {d[\"total_nodes\"]}'
assert d['total_edges']==2, f'expected 2 edges, got {d[\"total_edges\"]}'
"

# --- query ---
out="$(kg_cmd query Store)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert len(d['matches'])>=1, 'query should match Store'
assert d['matches'][0]['id']=='entity:Store'
"

# --- context ---
out="$(kg_cmd context entity:Store)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['node']['id']=='entity:Store'
assert len(d['neighbors'])>=1, 'Store should have neighbors'
assert len(d['edges'])>=1, 'Store should have edges'
"

# --- path ---
out="$(kg_cmd path entity:Flyer entity:Store)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['path'] is not None, 'path should exist'
assert len(d['path'])==3, f'expected 3-hop path, got {len(d[\"path\"])}'
"

# --- list ---
out="$(kg_cmd list --type entity)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert len(d)==3, f'expected 3 entities, got {len(d)}'
"

# --- dump ---
out="$(kg_cmd dump)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert 'meta' in d and 'nodes' in d and 'edges' in d, 'dump should have meta/nodes/edges'
"

# --- rm-edge ---
out="$(kg_cmd rm-edge entity:Source entity:Store --type depends_on)"
echo "${out}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['removed_count']==1"

# --- rm-node ---
out="$(kg_cmd rm-node entity:Flyer)"
echo "${out}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['removed']==True"

# Verify cleanup
out="$(kg_cmd stats)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d['total_nodes']==2, f'expected 2 nodes after rm, got {d[\"total_nodes\"]}'
assert d['total_edges']==0, f'expected 0 edges after rm, got {d[\"total_edges\"]}'
"

# --- import ---
cat > "${TMPDIR}/import.json" <<'SEED'
{
  "nodes": {
    "concept:test": {"type": "concept", "label": "Test Concept", "props": {}}
  },
  "edges": [
    {"from": "concept:test", "to": "entity:Store", "type": "relates_to", "props": {}}
  ]
}
SEED
out="$(kg_cmd import "${TMPDIR}/import.json")"
echo "${out}" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['added_nodes']==1"

# --- Tokenized query: single token matches node ID segment ---
echo "--- Tokenized query: single token matches node ID segment ---"

# Add file nodes with path-based IDs for tokenized query tests
kg_cmd add-node "file:web/app/stores/page" --type file --label "page" \
  --props '{"file":"apps/web/app/stores/page.tsx","exports":["StoresPage"],"imports_from":["react","shell"],"line_count":42}' > /dev/null

kg_cmd add-node "file:web/app/flyers/current/page" --type file --label "page" \
  --props '{"file":"apps/web/app/flyers/current/page.tsx","exports":["FlyersPage"],"imports_from":["react"],"line_count":55}' > /dev/null

kg_cmd add-node "file:web/components/shell" --type file --label "shell" \
  --props '{"file":"apps/web/components/shell.tsx","exports":["SiteHeader","SiteFooter","PageIntro","SectionCard"],"imports_from":["react","link"],"line_count":120}' > /dev/null

# Single token "stores" should match the stores page via node ID segment
out="$(kg_cmd query stores)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ids = [m['id'] for m in d['matches']]
assert 'file:web/app/stores/page' in ids, f'stores should match file:web/app/stores/page, got: {ids}'
print('PASS: single token stores matches via node ID segment')
"

# --- Tokenized query: single token matches props.file ---
echo "--- Tokenized query: single token matches props.file ---"
out="$(kg_cmd query flyers)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ids = [m['id'] for m in d['matches']]
assert 'file:web/app/flyers/current/page' in ids, f'flyers should match via props.file, got: {ids}'
print('PASS: single token flyers matches via props.file')
"

# --- Tokenized query: matches via props.exports ---
echo "--- Tokenized query: matches via props.exports ---"
out="$(kg_cmd query footer)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ids = [m['id'] for m in d['matches']]
assert 'file:web/components/shell' in ids, f'footer should match shell via SiteFooter export, got: {ids}'
print('PASS: footer matches shell node via exports')
"

# --- Tokenized query: multi-word input ---
echo "--- Tokenized query: multi-word input ---"
out="$(kg_cmd query "stores page")"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ids = [m['id'] for m in d['matches']]
assert 'file:web/app/stores/page' in ids, f'stores page should match, got: {ids}'
# stores page should rank higher than flyers page (matches more tokens)
if 'file:web/app/flyers/current/page' in ids:
    stores_idx = ids.index('file:web/app/stores/page')
    flyers_idx = ids.index('file:web/app/flyers/current/page')
    assert stores_idx < flyers_idx, f'stores/page should rank above flyers page; stores={stores_idx}, flyers={flyers_idx}'
print('PASS: multi-word query tokenized and ranked correctly')
"

# --- Tokenized query: deterministic ranking ---
echo "--- Tokenized query: deterministic ranking ---"
# Run the same query twice; results must be identical
out1="$(kg_cmd query "page")"
out2="$(kg_cmd query "page")"
python3 -c "
import json,sys
d1=json.loads(sys.argv[1])
d2=json.loads(sys.argv[2])
ids1 = [m['id'] for m in d1['matches']]
ids2 = [m['id'] for m in d2['matches']]
assert ids1 == ids2, f'query results not deterministic: {ids1} vs {ids2}'
print('PASS: query ranking is deterministic')
" "${out1}" "${out2}"

# --- Tokenized query: shell matches by label and ID ---
echo "--- Tokenized query: shell matches by label and ID ---"
out="$(kg_cmd query shell)"
echo "${out}" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ids = [m['id'] for m in d['matches']]
assert 'file:web/components/shell' in ids, f'shell should match via label/id, got: {ids}'
print('PASS: shell matches via label/id')
"

echo "PASS: all cortex CLI tests passed"
