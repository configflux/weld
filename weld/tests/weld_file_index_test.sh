#!/usr/bin/env bash
set -euo pipefail

# weld file index tests: exercises index building and find subcommand.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

REPO_ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"
ROOT="${TMPDIR}/project"
mkdir -p "${ROOT}/.weld"

wd_cmd() {
  weld_in_root "${REPO_ROOT}" "${ROOT}" "$@"
}

# --- Setup: create sample files ---

# Python file with classes, functions, imports
mkdir -p "${ROOT}/services/api/src"
cat > "${ROOT}/services/api/src/rate_limits.py" <<'PY'
import redis
from datetime import datetime

class RateLimiter:
    """Rate limiter using sliding window."""
    def check_rate(self, key: str) -> bool:
        return True

def configure_limits():
    pass
PY

# Python file with auth content
mkdir -p "${ROOT}/services/api/src/routers"
cat > "${ROOT}/services/api/src/routers/auth.py" <<'PY'
from fastapi import APIRouter

class AuthHandler:
    pass

def login():
    pass

def logout():
    pass
PY

# TypeScript file with auth content
mkdir -p "${ROOT}/apps/web/lib"
cat > "${ROOT}/apps/web/lib/auth-ux.ts" <<'TS'
import { Session } from "./session"

export function getAuthToken(): string {
    return ""
}

export const AUTH_COOKIE = "auth_token"
TS

# Markdown file
mkdir -p "${ROOT}/docs"
cat > "${ROOT}/docs/authentication.md" <<'MD'
# Authentication

## Login Flow

Users authenticate via email link.

## Rate Limiting

Auth endpoints are rate-limited.
MD

# YAML file
cat > "${ROOT}/services/config.yaml" <<'YAML'
database:
  host: localhost
rate_limits:
  window: 60
auth:
  enabled: true
YAML

# Files in excluded directories (should NOT be indexed)
mkdir -p "${ROOT}/.git/objects"
echo "should be excluded" > "${ROOT}/.git/objects/test.py"
mkdir -p "${ROOT}/node_modules/pkg"
echo "should be excluded" > "${ROOT}/node_modules/pkg/index.ts"
mkdir -p "${ROOT}/__pycache__"
echo "should be excluded" > "${ROOT}/__pycache__/cached.py"
mkdir -p "${ROOT}/.weld/internal"
echo "should be excluded" > "${ROOT}/.weld/internal/meta.py"
mkdir -p "${ROOT}/bazel-out/bin"
echo "should be excluded" > "${ROOT}/bazel-out/bin/gen.py"
mkdir -p "${ROOT}/bazel-testlogs"
echo "should be excluded" > "${ROOT}/bazel-testlogs/log.py"

# Nested agent worktree copies (should NOT be indexed)
mkdir -p "${ROOT}/.claude/worktrees/agent-abc123/services/api/src"
cat > "${ROOT}/.claude/worktrees/agent-abc123/services/api/src/rate_limits.py" <<'PY'
class DuplicateRateLimiter:
    pass
PY
mkdir -p "${ROOT}/.claude/worktrees/agent-abc123/docs"
echo "# Duplicate Doc" > "${ROOT}/.claude/worktrees/agent-abc123/docs/authentication.md"

# .worktrees directory (legacy/external worktree path, should NOT be indexed)
mkdir -p "${ROOT}/.worktrees/some-branch/services"
echo "class ShadowService: pass" > "${ROOT}/.worktrees/some-branch/services/shadow.py"

# --- Test 1: Build index ---
echo "--- Test 1: Build index ---"
wd_cmd build-index 2>/dev/null

# Verify file-index.json was created
if [[ ! -f "${ROOT}/.weld/file-index.json" ]]; then
  echo "FAIL: file-index.json not created"
  exit 1
fi

# Verify it contains the expected files
python3 -c "
import json, sys
_raw = json.load(open('${ROOT}/.weld/file-index.json'))
idx = _raw.get('files', _raw)

# Check that indexed files are present
assert 'services/api/src/rate_limits.py' in idx, f'rate_limits.py missing from index; keys: {list(idx.keys())}'
assert 'services/api/src/routers/auth.py' in idx, 'auth.py missing from index'
assert 'apps/web/lib/auth-ux.ts' in idx, 'auth-ux.ts missing from index'
assert 'docs/authentication.md' in idx, 'authentication.md missing from index'
assert 'services/config.yaml' in idx, 'config.yaml missing from index'

print('PASS: index contains expected files')
"

# --- Test 2: Excluded directories ---
echo "--- Test 2: Excluded directories ---"
python3 -c "
import json
_raw = json.load(open('${ROOT}/.weld/file-index.json'))
idx = _raw.get('files', _raw)

for path in idx:
    parts = path.split('/')
    assert '.git' not in parts, f'.git should be excluded: {path}'
    assert 'node_modules' not in parts, f'node_modules should be excluded: {path}'
    assert '__pycache__' not in parts, f'__pycache__ should be excluded: {path}'
    assert '.weld' not in parts, f'.weld should be excluded: {path}'
    for p in parts:
        assert not p.startswith('bazel-'), f'bazel-* should be excluded: {path}'

print('PASS: excluded directories are not indexed')
"

# --- Test 2b: Agent worktree copies excluded ---
echo "--- Test 2b: Agent worktree copies excluded ---"
python3 -c "
import json
_raw = json.load(open('${ROOT}/.weld/file-index.json'))
idx = _raw.get('files', _raw)

for path in idx:
    assert '.claude/worktrees' not in path, f'.claude/worktrees should be excluded: {path}'
    parts = path.split('/')
    assert '.worktrees' not in parts, f'.worktrees should be excluded: {path}'

# Verify the canonical files are still present (not over-excluded)
assert 'services/api/src/rate_limits.py' in idx, 'canonical rate_limits.py should be present'
assert 'docs/authentication.md' in idx, 'canonical authentication.md should be present'

print('PASS: agent worktree copies excluded, canonical files preserved')
"

# --- Test 3: Python token extraction ---
echo "--- Test 3: Python token extraction ---"
python3 -c "
import json
_raw = json.load(open('${ROOT}/.weld/file-index.json'))
idx = _raw.get('files', _raw)

tokens = idx['services/api/src/rate_limits.py']
assert 'RateLimiter' in tokens, f'RateLimiter not in tokens: {tokens}'
assert 'check_rate' in tokens, f'check_rate not in tokens: {tokens}'
assert 'configure_limits' in tokens, f'configure_limits not in tokens: {tokens}'
assert 'redis' in tokens, f'redis not in tokens: {tokens}'
assert 'rate_limits' in tokens, f'rate_limits (stem) not in tokens: {tokens}'

print('PASS: Python tokens extracted correctly')
"

# --- Test 4: TypeScript token extraction ---
echo "--- Test 4: TypeScript token extraction ---"
python3 -c "
import json
_raw = json.load(open('${ROOT}/.weld/file-index.json'))
idx = _raw.get('files', _raw)

tokens = idx['apps/web/lib/auth-ux.ts']
assert 'getAuthToken' in tokens, f'getAuthToken not in tokens: {tokens}'
assert 'AUTH_COOKIE' in tokens, f'AUTH_COOKIE not in tokens: {tokens}'

print('PASS: TypeScript tokens extracted correctly')
"

# --- Test 5: Markdown token extraction ---
echo "--- Test 5: Markdown token extraction ---"
python3 -c "
import json
_raw = json.load(open('${ROOT}/.weld/file-index.json'))
idx = _raw.get('files', _raw)

tokens = idx['docs/authentication.md']
assert 'Authentication' in tokens, f'Authentication heading not in tokens: {tokens}'
assert 'Login' in tokens, f'Login not in tokens: {tokens}'
assert 'Rate' in tokens, f'Rate not in tokens: {tokens}'
assert 'Limiting' in tokens, f'Limiting not in tokens: {tokens}'

print('PASS: Markdown tokens extracted correctly')
"

# --- Test 6: YAML token extraction ---
echo "--- Test 6: YAML token extraction ---"
python3 -c "
import json
_raw = json.load(open('${ROOT}/.weld/file-index.json'))
idx = _raw.get('files', _raw)

tokens = idx['services/config.yaml']
assert 'database' in tokens, f'database not in tokens: {tokens}'
assert 'rate_limits' in tokens, f'rate_limits not in tokens: {tokens}'
assert 'auth' in tokens, f'auth not in tokens: {tokens}'

print('PASS: YAML tokens extracted correctly')
"

# --- Test 7: find subcommand - rate returns rate_limits.py ---
echo "--- Test 7: find 'rate' returns rate_limits.py ---"
out="$(wd_cmd find rate)"
echo "${out}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['query'] == 'rate', f'query mismatch: {d[\"query\"]}'
paths = [f['path'] for f in d['files']]
assert any('rate_limits.py' in p for p in paths), f'rate_limits.py not in results: {paths}'

print('PASS: find rate returns rate_limits.py')
"

# --- Test 8: find subcommand - auth returns files across Python and TypeScript ---
echo "--- Test 8: find 'auth' returns cross-language results ---"
out="$(wd_cmd find auth)"
echo "${out}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
paths = [f['path'] for f in d['files']]
has_py = any(p.endswith('.py') for p in paths)
has_ts = any(p.endswith('.ts') for p in paths)
assert has_py, f'No .py files in auth results: {paths}'
assert has_ts, f'No .ts files in auth results: {paths}'

print('PASS: find auth returns Python and TypeScript files')
"

# --- Test 9: find subcommand - nonexistent term returns empty ---
echo "--- Test 9: find nonexistent term returns empty ---"
out="$(wd_cmd find zzzznonexistent42)"
echo "${out}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['query'] == 'zzzznonexistent42'
assert d['files'] == [], f'expected empty files, got: {d[\"files\"]}'

print('PASS: find nonexistent returns empty')
"

# --- Test 10: output format ---
echo "--- Test 10: Output format matches spec ---"
out="$(wd_cmd find rate)"
echo "${out}" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'query' in d, 'missing query key'
assert 'files' in d, 'missing files key'
assert isinstance(d['files'], list), 'files should be a list'
if d['files']:
    f = d['files'][0]
    assert 'path' in f, 'file entry missing path'
    assert 'tokens' in f, 'file entry missing tokens'
    assert isinstance(f['tokens'], list), 'tokens should be a list'

print('PASS: output format matches spec')
"

echo ""
echo "PASS: all weld_file_index tests passed"
