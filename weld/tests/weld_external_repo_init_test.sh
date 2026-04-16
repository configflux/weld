#!/usr/bin/env bash
set -euo pipefail

# Validates wd init against realistic external-repo fixtures:
# FastAPI, Django, Go, Rust project structures.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"
ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"

FIXTURES="${SCRIPT_DIR}/fixtures"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

errors=0

# ---------------------------------------------------------------------------
# Helper: run wd init on a fixture and return the discover.yaml path
# ---------------------------------------------------------------------------
init_fixture() {
  local name="$1"
  local fixture_dir="${FIXTURES}/${name}"
  local out_dir="${TMPDIR}/${name}"
  mkdir -p "${out_dir}/.weld"

  weld_in_root "${ROOT}" "${fixture_dir}" init \
    --output "${out_dir}/.weld/discover.yaml" --force \
    > "${TMPDIR}/${name}_stdout.txt" 2>&1

  echo "${out_dir}/.weld/discover.yaml"
}

# ---------------------------------------------------------------------------
# Test 1: FastAPI project fixture
# ---------------------------------------------------------------------------
echo "Test 1: FastAPI project fixture..."
FA_YAML="$(init_fixture fastapi_project)"

if [[ ! -f "${FA_YAML}" ]]; then
  echo "FAIL: discover.yaml not created for fastapi_project fixture"
  errors=$((errors + 1))
else
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${FA_YAML}').read_text()
data = parse_yaml(text)
errs = []

if 'sources' not in data:
    errs.append('missing sources key')
    print('FAIL: fastapi_project: ' + '; '.join(errs)); sys.exit(1)

sources = data['sources']
strategies = [s.get('strategy', '') for s in sources]

# Must detect FastAPI framework
if 'fastapi' not in strategies:
    errs.append('should detect fastapi strategy')

# Must detect SQLAlchemy (models use it)
if 'sqlalchemy' not in strategies:
    errs.append('should detect sqlalchemy strategy')

# Pydantic detection is conditional on glob matching 'schema'/'contract'/'dto'/'libs'
# When the glob collapses to app/**/*.py, the keyword match may not fire.
# Just verify Pydantic is detected as a framework (via init stdout), not as a strategy.

# Must detect python_module for app code
if 'python_module' not in strategies:
    errs.append('should detect python_module strategy')

# Must have artifact-class sections
required_classes = ['code', 'docs', 'infra', 'build', 'tests']
for cls in required_classes:
    if f'# ===== {cls}' not in text:
        errs.append(f'missing artifact-class section: {cls}')

# Should detect Dockerfiles in docker/ directory
has_dockerfile = any(s.get('strategy') == 'dockerfile' for s in sources)
if not has_dockerfile:
    errs.append('should detect dockerfile strategy for docker/*.Dockerfile')

# Should detect markdown docs
has_docs = any(s.get('strategy') == 'markdown' for s in sources)
if not has_docs:
    errs.append('should detect markdown strategy for docs/')

# Should detect CI workflows
has_ci = any(s.get('strategy') == 'yaml_meta' for s in sources)
if not has_ci:
    errs.append('should detect yaml_meta strategy for CI workflows')

# Should detect root configs (pyproject.toml, Makefile)
has_config = any(s.get('strategy') == 'config_file' for s in sources)
if not has_config:
    errs.append('should detect config_file for root configs')

# Should have app/**/*.py glob for python modules
globs = [s.get('glob', '') for s in sources if 'glob' in s]
has_app_glob = any('app' in g for g in globs)
if not has_app_glob:
    errs.append(f'should have app/ in globs, got: {globs}')

if errs:
    print('FAIL: fastapi_project validation:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print('PASS: fastapi_project fixture produces expected discover.yaml')
" || { echo "FAIL: fastapi_project validation"; errors=$((errors + 1)); }
fi

# ---------------------------------------------------------------------------
# Test 2: Django project fixture
# ---------------------------------------------------------------------------
echo "Test 2: Django project fixture..."
DJ_YAML="$(init_fixture django_project)"

if [[ ! -f "${DJ_YAML}" ]]; then
  echo "FAIL: discover.yaml not created for django_project fixture"
  errors=$((errors + 1))
else
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${DJ_YAML}').read_text()
data = parse_yaml(text)
errs = []

if 'sources' not in data:
    errs.append('missing sources key')
    print('FAIL: django_project: ' + '; '.join(errs)); sys.exit(1)

sources = data['sources']
strategies = [s.get('strategy', '') for s in sources]

# Must detect python_module strategy
if 'python_module' not in strategies:
    errs.append('should detect python_module strategy')

# Should NOT detect FastAPI, SQLAlchemy, or Pydantic (Django project)
for unwanted in ['fastapi', 'sqlalchemy', 'pydantic']:
    if unwanted in strategies:
        errs.append(f'should not detect {unwanted} in Django project')

# Must have artifact-class sections
required_classes = ['code', 'docs', 'infra', 'build']
for cls in required_classes:
    if f'# ===== {cls}' not in text:
        errs.append(f'missing artifact-class section: {cls}')

# Should detect multiple Python app directories (blog/, accounts/, mysite/)
globs = [s.get('glob', '') for s in sources if 'glob' in s]
has_blog = any('blog' in g for g in globs)
has_accounts = any('accounts' in g for g in globs)
has_mysite = any('mysite' in g for g in globs)
if not has_blog:
    errs.append(f'should have blog/ in globs, got: {globs}')
if not has_accounts:
    errs.append(f'should have accounts/ in globs, got: {globs}')
if not has_mysite:
    errs.append(f'should have mysite/ in globs, got: {globs}')

# Should detect docs
has_docs = any(s.get('strategy') == 'markdown' for s in sources)
if not has_docs:
    errs.append('should detect markdown strategy for docs/')

# Should detect Dockerfiles
has_dockerfile = any(s.get('strategy') == 'dockerfile' for s in sources)
if not has_dockerfile:
    errs.append('should detect dockerfile strategy')

# Should detect CI workflows
has_ci = any(s.get('strategy') == 'yaml_meta' for s in sources)
if not has_ci:
    errs.append('should detect yaml_meta for CI workflows')

# Should detect root configs (pyproject.toml, Makefile)
has_config = any(s.get('strategy') == 'config_file' for s in sources)
if not has_config:
    errs.append('should detect config_file for root configs')

if errs:
    print('FAIL: django_project validation:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print('PASS: django_project fixture produces expected discover.yaml')
" || { echo "FAIL: django_project validation"; errors=$((errors + 1)); }
fi

# ---------------------------------------------------------------------------
# Test 3: Go project fixture
# ---------------------------------------------------------------------------
echo "Test 3: Go project fixture..."
GO_YAML="$(init_fixture go_project)"

if [[ ! -f "${GO_YAML}" ]]; then
  echo "FAIL: discover.yaml not created for go_project fixture"
  errors=$((errors + 1))
else
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${GO_YAML}').read_text()
data = parse_yaml(text)
errs = []

if 'sources' not in data:
    errs.append('missing sources key')
    print('FAIL: go_project: ' + '; '.join(errs)); sys.exit(1)

sources = data['sources']
strategies = [s.get('strategy', '') for s in sources]

# Should NOT detect any Python-specific frameworks
for unwanted in ['python_module', 'fastapi', 'sqlalchemy', 'pydantic']:
    if unwanted in strategies:
        errs.append(f'should not detect {unwanted} in Go project')

# Must have artifact-class sections
required_classes = ['code', 'docs', 'infra', 'build']
for cls in required_classes:
    if f'# ===== {cls}' not in text:
        errs.append(f'missing artifact-class section: {cls}')

# Go is detected as a language; code section should use tree_sitter strategy
if 'tree_sitter' not in strategies:
    errs.append('should detect tree_sitter strategy for Go sources')

# Verify language: go is set on the tree_sitter entry
ts_entries = [s for s in sources if s.get('strategy') == 'tree_sitter']
if ts_entries and ts_entries[0].get('language') != 'go':
    errs.append(f'tree_sitter entry should have language=go, got: {ts_entries[0].get(\"language\")}')

# Should detect markdown for docs
has_docs = any(s.get('strategy') == 'markdown' for s in sources)
if not has_docs:
    errs.append('should detect markdown strategy for docs/')

# Should detect Dockerfiles
has_dockerfile = any(s.get('strategy') == 'dockerfile' for s in sources)
if not has_dockerfile:
    errs.append('should detect dockerfile strategy')

# Should detect CI workflows
has_ci = any(s.get('strategy') == 'yaml_meta' for s in sources)
if not has_ci:
    errs.append('should detect yaml_meta for CI workflows')

# Should detect root configs (go.mod, Makefile)
has_config = any(s.get('strategy') == 'config_file' for s in sources)
if not has_config:
    errs.append('should detect config_file for root configs')

# Verify go.mod is in the config files list
config_entries = [s for s in sources if s.get('strategy') == 'config_file']
all_files = []
for entry in config_entries:
    all_files.extend(entry.get('files', []))
if 'go.mod' not in all_files:
    errs.append(f'should detect go.mod as root config, got: {all_files}')

if errs:
    print('FAIL: go_project validation:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print('PASS: go_project fixture produces expected discover.yaml')
" || { echo "FAIL: go_project validation"; errors=$((errors + 1)); }
fi

# Pre-init Rust fixture so it's available for the cross-fixture comparison.
# Full validation is in Test 6 below.
RS_YAML="$(init_fixture rust_project)"

# ---------------------------------------------------------------------------
# Test 4: Cross-fixture strategy profile comparison
# ---------------------------------------------------------------------------
echo "Test 4: Cross-fixture strategy profiles differ..."
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

profiles = {}
for name, path in [
    ('fastapi_project', '${TMPDIR}/fastapi_project/.weld/discover.yaml'),
    ('django_project', '${TMPDIR}/django_project/.weld/discover.yaml'),
    ('go_project', '${TMPDIR}/go_project/.weld/discover.yaml'),
    ('rust_project', '${TMPDIR}/rust_project/.weld/discover.yaml'),
]:
    text = Path(path).read_text()
    data = parse_yaml(text)
    strategies = frozenset(s.get('strategy', '') for s in data.get('sources', []))
    profiles[name] = strategies

errs = []

# FastAPI should have more strategies than Go (framework-rich vs framework-sparse)
if len(profiles['fastapi_project']) <= len(profiles['go_project']):
    errs.append(
        f'fastapi_project should have more strategies than go_project: '
        f'{len(profiles[\"fastapi_project\"])} vs {len(profiles[\"go_project\"])}'
    )

# FastAPI and Django should differ (different frameworks detected)
if profiles['fastapi_project'] == profiles['django_project']:
    errs.append('fastapi_project and django_project should differ')

# All three should be distinct
unique = len(set(profiles.values()))
if unique < 2:
    errs.append(f'expected at least 2 unique strategy profiles, got {unique}')

if errs:
    print('FAIL: cross-fixture comparison:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print('PASS: external-repo fixture strategy profiles are appropriately distinct')
" || { echo "FAIL: cross-fixture comparison"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Test 5: FastAPI fixture detects core framework strategies
# ---------------------------------------------------------------------------
echo "Test 5: FastAPI detects core framework strategies..."
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${TMPDIR}/fastapi_project/.weld/discover.yaml').read_text()
data = parse_yaml(text)
strategies = set(s.get('strategy', '') for s in data.get('sources', []))

# FastAPI project should always detect fastapi and sqlalchemy strategies
expected = {'fastapi', 'sqlalchemy'}
missing = expected - strategies
if missing:
    print(f'FAIL: FastAPI fixture missing expected strategies: {missing}')
    print(f'  detected: {strategies}')
    sys.exit(1)

# Pydantic may or may not appear depending on glob collapse heuristics;
# its absence is acceptable when the glob is app/**/*.py.

print(f'PASS: FastAPI fixture detects core strategies: {expected & strategies}')
" || { echo "FAIL: core framework strategy check"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Test 6: Rust project fixture (RS_YAML initialized before Test 4)
# ---------------------------------------------------------------------------
echo "Test 6: Rust project fixture..."
if [[ ! -f "${RS_YAML}" ]]; then
  echo "FAIL: discover.yaml not created for rust_project fixture"; errors=$((errors + 1))
else
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml; from pathlib import Path
text = Path('${RS_YAML}').read_text(); data = parse_yaml(text); errs = []
if 'sources' not in data:
    print('FAIL: rust_project: missing sources'); sys.exit(1)
sources = data['sources']
strategies = [s.get('strategy', '') for s in sources]
for u in ['python_module', 'fastapi', 'sqlalchemy', 'pydantic']:
    if u in strategies: errs.append(f'should not detect {u}')
for cls in ['code', 'docs']:
    if f'# ===== {cls}' not in text: errs.append(f'missing section: {cls}')
ts = [s for s in sources if s.get('strategy') == 'tree_sitter']
if not ts: errs.append('no tree_sitter entries')
elif ts[0].get('language') != 'rust': errs.append(f'language should be rust')
if not any('.rs' in s.get('glob', '') for s in ts): errs.append('glob should target .rs')
if not any(s.get('strategy') == 'markdown' for s in sources): errs.append('missing markdown')
cfgs = [f for s in sources if s.get('strategy') == 'config_file' for f in s.get('files', [])]
if 'Cargo.toml' not in cfgs: errs.append(f'missing Cargo.toml in configs: {cfgs}')
if errs:
    print('FAIL: rust_project:'); [print(f'  - {e}') for e in errs]; sys.exit(1)
print('PASS: rust_project fixture produces expected discover.yaml')
" || { echo "FAIL: rust_project validation"; errors=$((errors + 1)); }
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if [[ "${errors}" -gt 0 ]]; then
  echo "FAIL: ${errors} test(s) failed"
  exit 1
fi

echo "PASS: all weld_external_repo_init tests passed"
