#!/usr/bin/env bash
set -euo pipefail

# weld_fixture_init_test: validates wd init against polyglot fixture repos.
#
# Tests wd init on four fixture repository shapes:
#   1. Python/Bazel monorepo
#   2. TypeScript/Node single-service
#   3. C++/clang project
#   4. Legacy/custom-build onboarding

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
# Test 1: Python/Bazel fixture
# ---------------------------------------------------------------------------
echo "Test 1: Python/Bazel fixture..."
PB_YAML="$(init_fixture python_bazel)"

if [[ ! -f "${PB_YAML}" ]]; then
  echo "FAIL: discover.yaml not created for python_bazel fixture"
  errors=$((errors + 1))
else
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${PB_YAML}').read_text()
data = parse_yaml(text)
errs = []

if 'sources' not in data:
    errs.append('missing sources key')
    print('FAIL: python_bazel: ' + '; '.join(errs)); sys.exit(1)

sources = data['sources']
strategies = [s.get('strategy', '') for s in sources]

# Must detect Python and framework strategies
if 'python_module' not in strategies:
    errs.append('should detect python_module strategy')
if 'fastapi' not in strategies:
    errs.append('should detect fastapi strategy')
if 'sqlalchemy' not in strategies:
    errs.append('should detect sqlalchemy strategy')

# Must have artifact-class sections
required_classes = ['code', 'docs', 'policy', 'infra', 'build', 'tests', 'operations']
for cls in required_classes:
    if f'# ===== {cls}' not in text:
        errs.append(f'missing artifact-class section: {cls}')

# Monorepo detection: should see services/ paths in globs
globs = [s.get('glob', '') for s in sources if 'glob' in s]
has_services = any('services' in g for g in globs)
if not has_services:
    errs.append(f'should have services/ in globs for monorepo, got {globs}')

# Should detect docs
has_docs_strategy = any(s.get('strategy') == 'markdown' for s in sources)
if not has_docs_strategy:
    errs.append('should detect markdown strategy for docs/')

# Should detect CI workflow
has_ci = any(s.get('strategy') == 'yaml_meta' for s in sources)
if not has_ci:
    errs.append('should detect yaml_meta strategy for CI workflows')

# Should detect config files (MODULE.bazel, pyproject.toml)
has_config = any(s.get('strategy') == 'config_file' for s in sources)
if not has_config:
    errs.append('should detect config_file strategy for root configs')

if errs:
    print('FAIL: python_bazel validation:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print('PASS: python_bazel fixture produces expected discover.yaml')
" || { echo "FAIL: python_bazel validation"; errors=$((errors + 1)); }
fi

# ---------------------------------------------------------------------------
# Test 2: TypeScript/Node fixture
# ---------------------------------------------------------------------------
echo "Test 2: TypeScript/Node fixture..."
TS_YAML="$(init_fixture typescript_node)"

if [[ ! -f "${TS_YAML}" ]]; then
  echo "FAIL: discover.yaml not created for typescript_node fixture"
  errors=$((errors + 1))
else
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${TS_YAML}').read_text()
data = parse_yaml(text)
errs = []

if 'sources' not in data:
    errs.append('missing sources key')
    print('FAIL: typescript_node: ' + '; '.join(errs)); sys.exit(1)

sources = data['sources']
strategies = [s.get('strategy', '') for s in sources]

# Should NOT detect Python frameworks (no Python files with imports)
for unwanted in ['fastapi', 'sqlalchemy', 'pydantic']:
    if unwanted in strategies:
        errs.append(f'should not detect {unwanted} in TypeScript project')

# Must have artifact-class sections
required_classes = ['code', 'docs', 'policy', 'infra', 'build', 'tests', 'operations']
for cls in required_classes:
    if f'# ===== {cls}' not in text:
        errs.append(f'missing artifact-class section: {cls}')

# Should detect config_file for root configs (package.json, Makefile)
has_config = any(s.get('strategy') == 'config_file' for s in sources)
if not has_config:
    errs.append('should detect config_file strategy for package.json/Makefile')

# Structure should be single-service (no services/ or apps/ dirs)
# (We just verify the YAML was generated; structure detection is internal)

if errs:
    print('FAIL: typescript_node validation:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print('PASS: typescript_node fixture produces expected discover.yaml')
" || { echo "FAIL: typescript_node validation"; errors=$((errors + 1)); }
fi

# ---------------------------------------------------------------------------
# Test 3: C++/clang fixture
# ---------------------------------------------------------------------------
echo "Test 3: C++/clang fixture..."
CPP_YAML="$(init_fixture cpp_clang)"

if [[ ! -f "${CPP_YAML}" ]]; then
  echo "FAIL: discover.yaml not created for cpp_clang fixture"
  errors=$((errors + 1))
else
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${CPP_YAML}').read_text()
data = parse_yaml(text)
errs = []

if 'sources' not in data:
    errs.append('missing sources key')
    print('FAIL: cpp_clang: ' + '; '.join(errs)); sys.exit(1)

sources = data['sources']
strategies = [s.get('strategy', '') for s in sources]

# No Python detected means no python_module, fastapi, sqlalchemy
for unwanted in ['python_module', 'fastapi', 'sqlalchemy', 'pydantic']:
    if unwanted in strategies:
        errs.append(f'should not detect {unwanted} in C++ project')

# Must have artifact-class sections
required_classes = ['code', 'docs', 'policy', 'infra', 'build', 'tests', 'operations']
for cls in required_classes:
    if f'# ===== {cls}' not in text:
        errs.append(f'missing artifact-class section: {cls}')

# Should detect config_file for CMakeLists.txt and/or Makefile
has_config = any(s.get('strategy') == 'config_file' for s in sources)
if not has_config:
    errs.append('should detect config_file strategy for CMakeLists.txt/Makefile')

# Most sections should be stubs since C++ is not a detected language
stub_count = text.count('(uncomment to enable)')
if stub_count < 4:
    errs.append(f'expected at least 4 stub sections for C++ project, got {stub_count}')

if errs:
    print('FAIL: cpp_clang validation:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print('PASS: cpp_clang fixture produces expected discover.yaml')
" || { echo "FAIL: cpp_clang validation"; errors=$((errors + 1)); }
fi

# ---------------------------------------------------------------------------
# Test 4: Legacy/custom-build fixture
# ---------------------------------------------------------------------------
echo "Test 4: Legacy/custom-build fixture..."
LG_YAML="$(init_fixture legacy_onboarding)"

if [[ ! -f "${LG_YAML}" ]]; then
  echo "FAIL: discover.yaml not created for legacy_onboarding fixture"
  errors=$((errors + 1))
else
  python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

text = Path('${LG_YAML}').read_text()
data = parse_yaml(text)
errs = []

if 'sources' not in data:
    errs.append('missing sources key')
    print('FAIL: legacy_onboarding: ' + '; '.join(errs)); sys.exit(1)

sources = data['sources']
strategies = [s.get('strategy', '') for s in sources]

# Should detect python_module for the .py files
if 'python_module' not in strategies:
    errs.append('should detect python_module for legacy Python files')

# Should NOT detect frameworks (no imports)
for unwanted in ['fastapi', 'sqlalchemy', 'pydantic']:
    if unwanted in strategies:
        errs.append(f'should not detect {unwanted} in legacy project')

# Must have all artifact-class sections
required_classes = ['code', 'docs', 'policy', 'infra', 'build', 'tests', 'operations']
for cls in required_classes:
    if f'# ===== {cls}' not in text:
        errs.append(f'missing artifact-class section: {cls}')

# Should detect doc directory (doc/)
has_docs = any(s.get('strategy') == 'markdown' for s in sources)
if not has_docs:
    errs.append('should detect markdown strategy for doc/ directory')

# Should detect config_file for Makefile
has_config = any(s.get('strategy') == 'config_file' for s in sources)
if not has_config:
    errs.append('should detect config_file strategy for Makefile')

if errs:
    print('FAIL: legacy_onboarding validation:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print('PASS: legacy_onboarding fixture produces expected discover.yaml')
" || { echo "FAIL: legacy_onboarding validation"; errors=$((errors + 1)); }
fi

# ---------------------------------------------------------------------------
# Test 5: Cross-fixture comparison — each shape has unique strategy profile
# ---------------------------------------------------------------------------
echo "Test 5: Cross-fixture strategy profiles differ..."
python3 -c "
import sys; sys.path.insert(0, '${ROOT}')
from weld._yaml import parse_yaml
from pathlib import Path

profiles = {}
for name, path in [
    ('python_bazel', '${TMPDIR}/python_bazel/.weld/discover.yaml'),
    ('typescript_node', '${TMPDIR}/typescript_node/.weld/discover.yaml'),
    ('cpp_clang', '${TMPDIR}/cpp_clang/.weld/discover.yaml'),
    ('legacy_onboarding', '${TMPDIR}/legacy_onboarding/.weld/discover.yaml'),
]:
    text = Path(path).read_text()
    data = parse_yaml(text)
    strategies = frozenset(s.get('strategy', '') for s in data.get('sources', []))
    profiles[name] = strategies

errs = []

# python_bazel should have more strategies than cpp_clang
if len(profiles['python_bazel']) <= len(profiles['cpp_clang']):
    errs.append(
        f'python_bazel should have more strategies than cpp_clang: '
        f'{len(profiles[\"python_bazel\"])} vs {len(profiles[\"cpp_clang\"])}'
    )

# python_bazel and legacy_onboarding should differ (framework detection)
if profiles['python_bazel'] == profiles['legacy_onboarding']:
    errs.append('python_bazel and legacy_onboarding should have different strategy profiles')

# All four should not be identical
unique = len(set(profiles.values()))
if unique < 3:
    errs.append(f'expected at least 3 unique strategy profiles, got {unique}')

if errs:
    print('FAIL: cross-fixture comparison:')
    for e in errs: print(f'  - {e}')
    sys.exit(1)

print('PASS: fixture strategy profiles are appropriately distinct')
" || { echo "FAIL: cross-fixture comparison"; errors=$((errors + 1)); }

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if [[ "${errors}" -gt 0 ]]; then
  echo "FAIL: ${errors} test(s) failed"
  exit 1
fi

echo "PASS: all weld_fixture_init tests passed"
