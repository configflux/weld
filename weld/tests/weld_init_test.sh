#!/usr/bin/env bash
set -euo pipefail

# weld_init test: validates the wd init bootstrap command.
# Tests:
#   1. Running on this repo produces valid discover.yaml with expected detections
#   2. Running on a bare Python directory produces minimal Python-only config
#   3. Generated YAML is parseable by weld/_yaml.py
#   4. Verbose output contains expected progress messages
#   5. Does not overwrite existing discover.yaml without --force
#   6. Uses Git-visible files for source discovery and skips ignored untracked trees

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"
ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

errors=0

# --- Detect repo context ---
# Monorepo has services/ and apps/ directories; standalone has only weld/.
IS_MONOREPO="false"
if [[ -d "${ROOT}/services" && -d "${ROOT}/apps" ]]; then
  IS_MONOREPO="true"
fi

# --- Test 1: wd init on the real repo produces valid discover.yaml ---
echo "Test 1: wd init on real repo..."
REAL_OUT="${TMPDIR}/real_repo_out"
mkdir -p "${REAL_OUT}/.weld"

weld_in_root "${ROOT}" "${ROOT}" init --output "${REAL_OUT}/.weld/discover.yaml" \
  > "${TMPDIR}/real_stdout.txt" 2>&1

if [[ ! -f "${REAL_OUT}/.weld/discover.yaml" ]]; then
  echo "FAIL: discover.yaml not created for real repo"
  errors=$((errors + 1))
else
  # Validate it is parseable by weld/_yaml.py
  IS_MONOREPO_PY="${IS_MONOREPO}" python3 -c "
import sys, os; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${REAL_OUT}/.weld/discover.yaml').read_text()
data = parse_yaml(text)
is_monorepo = os.environ.get('IS_MONOREPO_PY', 'false') == 'true'

errs = []

# Must have sources key
if 'sources' not in data:
    errs.append('missing sources key')
else:
    sources = data['sources']
    if not isinstance(sources, list):
        errs.append(f'sources should be list, got {type(sources).__name__}')
    elif len(sources) < 1:
        errs.append('sources list is empty')

    # Check that strategy entries have required keys
    for i, src in enumerate(sources):
        if 'strategy' not in src:
            errs.append(f'source[{i}] missing strategy key')
        if 'glob' not in src and 'files' not in src:
            errs.append(f'source[{i}] missing glob or files key')

# Check for expected detections depending on repo context
strategies = [s.get('strategy', '') for s in data.get('sources', [])]

if is_monorepo:
    # Monorepo has sqlalchemy models, fastapi routes, python modules
    if 'sqlalchemy' not in strategies:
        errs.append('should detect sqlalchemy strategy')
    if 'fastapi' not in strategies:
        errs.append('should detect fastapi strategy')
    if 'python_module' not in strategies:
        errs.append('should detect python_module strategy')
else:
    # Standalone weld repo: test fixtures contain framework
    # imports (fastapi, sqlalchemy), so init detects those. Verify at
    # least one Python-related strategy is active.
    python_strategies = {'python_module', 'sqlalchemy', 'fastapi', 'pydantic'}
    active_python = python_strategies & set(strategies)
    if not active_python:
        errs.append(f'should detect at least one Python strategy, got: {strategies}')

if errs:
    print('FAIL: real repo validation errors:')
    for e in errs:
        print(f'  - {e}')
    sys.exit(1)

print('PASS: real repo discover.yaml is valid and has expected strategies')
" || { echo "FAIL: real repo YAML validation"; errors=$((errors + 1)); }
fi

# --- Test 2: wd init on bare Python directory ---
echo "Test 2: wd init on bare Python directory..."
BARE="${TMPDIR}/bare_python"
mkdir -p "${BARE}/src"
cat > "${BARE}/src/main.py" << 'PYEOF'
def hello():
    print("hello world")
PYEOF
cat > "${BARE}/src/utils.py" << 'PYEOF'
def add(a, b):
    return a + b
PYEOF

weld_in_root "${ROOT}" "${BARE}" init --output "${BARE}/.weld/discover.yaml" \
  > "${TMPDIR}/bare_stdout.txt" 2>&1

if [[ ! -f "${BARE}/.weld/discover.yaml" ]]; then
  echo "FAIL: discover.yaml not created for bare Python dir"
  errors=$((errors + 1))
else
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${BARE}/.weld/discover.yaml').read_text()
data = parse_yaml(text)

errs = []

if 'sources' not in data:
    errs.append('missing sources key')
else:
    sources = data['sources']
    strategies = [s.get('strategy', '') for s in sources]
    # Should have python_module for the Python files
    if 'python_module' not in strategies:
        errs.append('bare Python dir should have python_module strategy')
    # Should NOT have fastapi, sqlalchemy, etc.
    for unwanted in ['fastapi', 'sqlalchemy', 'pydantic', 'compose', 'dockerfile']:
        if unwanted in strategies:
            errs.append(f'bare Python dir should not have {unwanted} strategy')

if errs:
    print('FAIL: bare python validation errors:')
    for e in errs:
        print(f'  - {e}')
    sys.exit(1)

print('PASS: bare Python directory produces minimal config')
" || { echo "FAIL: bare Python YAML validation"; errors=$((errors + 1)); }
fi

# --- Test 3: Verbose output contains expected progress messages ---
echo "Test 3: verbose output..."
python3 -c "
import sys
with open('${TMPDIR}/real_stdout.txt') as f:
    output = f.read()

errs = []
expected = ['Scanning', 'Found', 'Generating']
for word in expected:
    if word not in output:
        errs.append(f'verbose output missing expected word: {word}')

if errs:
    print('FAIL: verbose output validation:')
    for e in errs:
        print(f'  - {e}')
    print(f'Output was: {output[:500]}')
    sys.exit(1)

print('PASS: verbose output contains expected progress messages')
" || { echo "FAIL: verbose output test"; errors=$((errors + 1)); }

# --- Test 4: Does not overwrite existing discover.yaml without --force ---
echo "Test 4: no-overwrite guard..."
GUARD="${TMPDIR}/guard_test"
mkdir -p "${GUARD}/.weld"
echo "# existing config" > "${GUARD}/.weld/discover.yaml"
cat > "${GUARD}/app.py" << 'PYEOF'
print("hello")
PYEOF

if weld_in_root "${ROOT}" "${GUARD}" init --output "${GUARD}/.weld/discover.yaml" \
  > "${TMPDIR}/guard_stdout.txt" 2>&1; then
  # Should have exited non-zero or the file should still be the original
  content=$(cat "${GUARD}/.weld/discover.yaml")
  if [[ "${content}" != "# existing config" ]]; then
    echo "FAIL: discover.yaml was overwritten without --force"
    errors=$((errors + 1))
  else
    echo "PASS: no-overwrite guard works (command succeeded but did not overwrite)"
  fi
else
  echo "PASS: no-overwrite guard works (command exited non-zero)"
fi

# --- Test 5: --force flag allows overwrite ---
echo "Test 5: --force flag..."
weld_in_root "${ROOT}" "${GUARD}" init --output "${GUARD}/.weld/discover.yaml" --force \
  > "${TMPDIR}/force_stdout.txt" 2>&1

content=$(cat "${GUARD}/.weld/discover.yaml")
if [[ "${content}" == "# existing config" ]]; then
  echo "FAIL: --force did not overwrite discover.yaml"
  errors=$((errors + 1))
else
  echo "PASS: --force flag allows overwrite"
fi

# --- Test 6: Git-visible files define init coverage ---
echo "Test 6: git-visible source detection..."
GITVISIBLE="${TMPDIR}/git_visible"
mkdir -p "${GITVISIBLE}/.weld" "${GITVISIBLE}/toolkit" "${GITVISIBLE}/tracked_ignored" "${GITVISIBLE}/ignored_only"
git -C "${GITVISIBLE}" init -q
cat > "${GITVISIBLE}/.gitignore" <<'EOF'
ignored_only/
tracked_ignored/
EOF
cat > "${GITVISIBLE}/toolkit/main.py" <<'PYEOF'
def toolkit_entry():
    return True
PYEOF
cat > "${GITVISIBLE}/tracked_ignored/kept.py" <<'PYEOF'
def tracked_even_if_ignored():
    return True
PYEOF
cat > "${GITVISIBLE}/ignored_only/ghost.py" <<'PYEOF'
def ignored_untracked():
    return True
PYEOF
git -C "${GITVISIBLE}" add .gitignore toolkit/main.py
git -C "${GITVISIBLE}" add -f tracked_ignored/kept.py

weld_in_root "${ROOT}" "${GITVISIBLE}" init --output "${GITVISIBLE}/.weld/discover.yaml" \
  > "${TMPDIR}/git_visible_stdout.txt" 2>&1

python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${GITVISIBLE}/.weld/discover.yaml').read_text()
data = parse_yaml(text)

globs = [src.get('glob', '') for src in data.get('sources', []) if 'glob' in src]

def has_glob(prefix: str) -> bool:
    return any(g == f'{prefix}/*.py' or g == f'{prefix}/**/*.py' for g in globs)

errs = []
if not has_glob('toolkit'):
    errs.append(f'missing toolkit python glob in {globs}')
if not has_glob('tracked_ignored'):
    errs.append(f'missing tracked_ignored python glob in {globs}')
if any('ignored_only' in g for g in globs):
    errs.append(f'ignored_only should be excluded from globs: {globs}')

if errs:
    print('FAIL: git-visible init validation errors:')
    for e in errs:
        print(f'  - {e}')
    sys.exit(1)

print('PASS: wd init follows Git-visible files and skips ignored untracked trees')
" || { echo "FAIL: git-visible init test"; errors=$((errors + 1)); }

# --- Test 7: Artifact-class section headers present in output ---
echo "Test 7: artifact-class section headers..."
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from pathlib import Path

text = Path('${REAL_OUT}/.weld/discover.yaml').read_text()

errs = []

# Every generated config must have artifact-class sections
required_classes = ['code', 'docs', 'policy', 'infra', 'build', 'tests', 'operations']
for cls in required_classes:
    # Sections appear as '# ===== <class> =====' or '# ===== <class> (uncomment to enable) ====='
    if f'# ===== {cls}' not in text:
        errs.append(f'missing artifact-class section header for: {cls}')

# Header must document artifact classes
if 'Artifact classes:' not in text:
    errs.append('header missing Artifact classes documentation')

if errs:
    print('FAIL: artifact-class validation errors:')
    for e in errs:
        print(f'  - {e}')
    sys.exit(1)

print('PASS: discover.yaml contains all artifact-class section headers')
" || { echo "FAIL: artifact-class section headers test"; errors=$((errors + 1)); }

# --- Test 8: Stub entries for empty artifact classes ---
echo "Test 8: stub entries for empty classes..."
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from pathlib import Path

text = Path('${REAL_OUT}/.weld/discover.yaml').read_text()

errs = []

# Sections without detected entries must have commented stubs
# On the real repo, 'operations' is typically an empty class
# Every empty class header includes '(uncomment to enable)' and
# must be followed by a commented entry (# - glob:)
lines = text.splitlines()
for i, line in enumerate(lines):
    if '(uncomment to enable)' in line:
        # Next non-empty line should be a commented glob entry
        found_stub = False
        for j in range(i + 1, min(i + 5, len(lines))):
            if lines[j].strip().startswith('# - glob:') or lines[j].strip().startswith('# - files:'):
                found_stub = True
                break
            if lines[j].strip() and not lines[j].strip().startswith('#'):
                break  # hit a non-comment non-empty line
        if not found_stub:
            errs.append(f'empty section at line {i+1} missing commented stub entry')

if errs:
    print('FAIL: stub entry validation errors:')
    for e in errs:
        print(f'  - {e}')
    sys.exit(1)

print('PASS: empty artifact classes have commented stub entries')
" || { echo "FAIL: stub entries test"; errors=$((errors + 1)); }

# --- Test 9: Bare directory gets all 7 artifact-class sections ---
echo "Test 9: bare directory artifact classes..."
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from pathlib import Path

text = Path('${BARE}/.weld/discover.yaml').read_text()

errs = []
required_classes = ['code', 'docs', 'policy', 'infra', 'build', 'tests', 'operations']
for cls in required_classes:
    if f'# ===== {cls}' not in text:
        errs.append(f'bare dir missing artifact-class section: {cls}')

# Bare Python dir should have code entries but stubs for most others
if 'python_module' not in text:
    errs.append('bare dir should have python_module in code section')

# Count how many sections are stubs (uncomment to enable)
stub_count = text.count('(uncomment to enable)')
# A bare Python dir should have several stub sections
if stub_count < 3:
    errs.append(f'expected at least 3 stub sections, got {stub_count}')

if errs:
    print('FAIL: bare directory artifact-class validation:')
    for e in errs:
        print(f'  - {e}')
    sys.exit(1)

print('PASS: bare directory has all 7 artifact-class sections with appropriate stubs')
" || { echo "FAIL: bare directory artifact classes test"; errors=$((errors + 1)); }

if [[ "${errors}" -gt 0 ]]; then
  echo "FAIL: ${errors} test(s) failed"
  exit 1
fi

echo "PASS: all weld_init tests passed"
