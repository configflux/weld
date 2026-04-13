#!/usr/bin/env bash
# Test: cortex real pip install (CI-only)
#
# This script performs a real editable install, runs cortex from the
# installed entry point, builds a wheel, installs the wheel into a clean
# venv, and re-runs the same assertions. It is NOT registered as a bazel
# sh_test: it requires ensurepip / pip, which is not available in our
# devcontainer. It is invoked directly by a dedicated GitHub Actions job
# (see .github/workflows/ci.yml :: real_install).
#
# Pair this with cortex/tests/cortex_pip_install_test.sh (the structural
# check that runs in the local gate and in bazel CI without requiring pip).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cortex/tests/cortex_test_lib.sh
source "${SCRIPT_DIR}/cortex_test_lib.sh"

REPO_ROOT="$(cortex_test_repo_root "$SCRIPT_DIR")"
CORTEX_ROOT="${REPO_ROOT}/cortex"

# --- Guard: ensurepip must be available (this script is CI-only) ---
if ! python3 -c "import venv; import ensurepip" 2>/dev/null; then
  echo "ERROR: cortex_real_install.sh requires a Python with ensurepip." >&2
  echo "       Install python3-venv (Debian/Ubuntu) or equivalent and retry." >&2
  exit 2
fi

TMPDIR="$(mktemp -d -t cortex-real-install.XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

FAILURES=0
fail() {
  echo "FAIL: $1" >&2
  FAILURES=$((FAILURES + 1))
}

# ---------------------------------------------------------------------------
# Phase 1 — editable install (`pip install -e cortex/`)
# ---------------------------------------------------------------------------
echo ">>> phase 1: editable install"

python3 -m venv "$TMPDIR/editable-venv"
# shellcheck disable=SC1091
source "$TMPDIR/editable-venv/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet -e "$CORTEX_ROOT"

# `cortex --help` from the installed entry point
OUTPUT="$(cortex --help 2>&1)"
if echo "$OUTPUT" | grep -q "Usage: cortex <command>"; then
  echo "OK: cortex --help works after pip install -e"
else
  fail "cortex --help did not work after pip install -e"
  echo "Got: $OUTPUT"
fi

# `python -m cortex --help` from the installed package
OUTPUT="$(python -m cortex --help 2>&1)"
if echo "$OUTPUT" | grep -q "Usage: cortex <command>"; then
  echo "OK: python -m cortex --help works after pip install -e"
else
  fail "python -m cortex --help did not work after pip install -e"
  echo "Got: $OUTPUT"
fi

# `cortex scaffold` produces the expected files
mkdir -p "$TMPDIR/editable-workspace"
(
  cd "$TMPDIR/editable-workspace"
  cortex scaffold local-strategy smoke_test >"$TMPDIR/cortex-editable-scaffold-local.log"
  cortex scaffold external-adapter smoke_test >"$TMPDIR/cortex-editable-scaffold-adapter.log"
  [[ -f ".cortex/strategies/smoke_test.py" ]] || exit 10
  [[ -f ".cortex/adapters/smoke_test.py" ]] || exit 11
) || fail "editable install scaffold commands did not produce expected files"

# `cortex.__file__` must not be None, and bundled runtime assets must resolve.
# Under `set -e` a non-zero exit here aborts the script; this is intentional
# because subsequent wheel checks depend on an intact editable install.
#
# NOTE: we run this from $TMPDIR (a neutral cwd with no cortex/ directory)
# so that Python's sys.path[0] ("") does not accidentally shadow the
# editable-installed cortex with a source-tree cortex/ directory living
# next to the script invocation. Otherwise the check would silently pass
# even when the editable finder is broken.
(
  cd "$TMPDIR"
  python - <<'PY'
from pathlib import Path
import cortex

assert cortex.__file__ is not None, (
    "cortex.__file__ is None after pip install -e — cortex was resolved "
    "as a namespace package. Check cortex/__init__.py and pyproject.toml."
)

pkg = Path(cortex.__file__).resolve().parent
for name in ("cortex_readme.md", "cortex_cmd_claude.md", "cortex_skill_codex.md"):
    t = pkg / "templates" / name
    assert t.is_file(), f"missing template: {t}"
print("OK: markdown templates bundled (editable)")
PY
) || fail "markdown templates not bundled in editable install"

# Build a wheel from the editable venv (keeps dependencies deterministic)
mkdir -p "$TMPDIR/dist"
pip wheel --quiet --no-deps "$CORTEX_ROOT" -w "$TMPDIR/dist"
WHEEL_PATH="$(find "$TMPDIR/dist" -maxdepth 1 -name '*.whl' | head -n 1)"
if [[ -z "${WHEEL_PATH}" ]]; then
  fail "pip wheel did not produce a wheel artifact"
fi

deactivate 2>/dev/null || true

# ---------------------------------------------------------------------------
# Phase 2 — wheel install (`pip install <wheel>`) in a clean venv
# ---------------------------------------------------------------------------
echo ">>> phase 2: wheel install"

python3 -m venv "$TMPDIR/wheel-venv"
# shellcheck disable=SC1091
source "$TMPDIR/wheel-venv/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet "$WHEEL_PATH"

# `cortex --help` from the installed wheel
OUTPUT="$(cortex --help 2>&1)"
if echo "$OUTPUT" | grep -q "Usage: cortex <command>"; then
  echo "OK: cortex --help works after wheel install"
else
  fail "cortex --help did not work after wheel install"
  echo "Got: $OUTPUT"
fi

# `cortex scaffold` from the installed wheel
mkdir -p "$TMPDIR/wheel-workspace"
(
  cd "$TMPDIR/wheel-workspace"
  cortex scaffold local-strategy wheel_test >"$TMPDIR/cortex-wheel-scaffold-local.log"
  cortex scaffold external-adapter wheel_test >"$TMPDIR/cortex-wheel-scaffold-adapter.log"
  [[ -f ".cortex/strategies/wheel_test.py" ]] || exit 12
  [[ -f ".cortex/adapters/wheel_test.py" ]] || exit 13
) || fail "wheel install scaffold commands did not produce expected files"

# `cortex.__file__` must not be None after wheel install (the original bug).
# Run from $TMPDIR for the same reason as the editable-install check above:
# avoid cwd shadowing the installed cortex with a source-tree cortex/ dir.
(
  cd "$TMPDIR"
  python - <<'PY'
from pathlib import Path
import cortex

assert cortex.__file__ is not None, (
    "cortex.__file__ is None after wheel install — cortex was resolved "
    "as a namespace package. Check cortex/__init__.py and pyproject.toml."
)

pkg = Path(cortex.__file__).resolve().parent
for name in ("cortex_readme.md", "cortex_cmd_claude.md", "cortex_skill_codex.md"):
    t = pkg / "templates" / name
    assert t.is_file(), f"missing template: {t}"
print("OK: markdown templates bundled (wheel)")
PY
) || fail "markdown templates not bundled in wheel install"

# `cortex.strategies.tree_sitter.load_language_queries("python")` must load
# the bundled python query YAML (exercises package-data + path resolution).
# This requires the tree-sitter optional extra so `import tree_sitter` succeeds
# inside the module; install it into the wheel venv first.
pip install --quiet tree-sitter tree-sitter-python

python - <<'PY' || fail "bundled query YAML did not load after wheel install"
from cortex.strategies.tree_sitter import load_language_queries

queries = load_language_queries("python")
assert "exports" in queries, (
    f"expected 'exports' query in python.yaml, got keys: {sorted(queries)}"
)
print("OK: bundled query YAML loads after wheel install")
PY

deactivate 2>/dev/null || true

# ---------------------------------------------------------------------------
# Final verdict
# ---------------------------------------------------------------------------
if [[ $FAILURES -gt 0 ]]; then
  echo "RESULT: $FAILURES failure(s)" >&2
  exit 1
fi

echo "PASS: cortex real pip install"
