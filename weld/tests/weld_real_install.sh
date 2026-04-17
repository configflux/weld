#!/usr/bin/env bash
# Test: weld real pip install (CI-only)
#
# This script performs a real editable install, runs weld from the
# installed entry point, builds a wheel, installs the wheel into a clean
# venv, and re-runs the same assertions. It is NOT registered as a bazel
# sh_test: it requires ensurepip / pip, which is not available in our
# devcontainer. It is invoked directly by a dedicated GitHub Actions job
# (see .github/workflows/ci.yml :: real_install).
#
# Pair this with weld/tests/weld_pip_install_test.sh (the structural
# check that runs in the local gate and in bazel CI without requiring pip).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"

REPO_ROOT="$(weld_test_repo_root "$SCRIPT_DIR")"
WELD_ROOT="${REPO_ROOT}/weld"
EXPECTED_VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"

# --- Guard: ensurepip must be available (this script is CI-only) ---
if ! python3 -c "import venv; import ensurepip" 2>/dev/null; then
  echo "ERROR: weld_real_install.sh requires a Python with ensurepip." >&2
  echo "       Install python3-venv (Debian/Ubuntu) or equivalent and retry." >&2
  exit 2
fi

TMPDIR="$(mktemp -d -t weld-real-install.XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

FAILURES=0
fail() {
  echo "FAIL: $1" >&2
  FAILURES=$((FAILURES + 1))
}

check_wd_version() {
  local phase="$1"
  local output

  output="$(wd --version 2>&1)"
  if echo "$output" | grep -qF "$EXPECTED_VERSION"; then
    echo "OK: wd --version reports ${EXPECTED_VERSION} (${phase})"
  else
    fail "wd --version after ${phase} did not contain VERSION ${EXPECTED_VERSION}"
    echo "Got: $output"
  fi
}

# ---------------------------------------------------------------------------
# Phase 1 — editable install (`pip install -e weld/`)
# ---------------------------------------------------------------------------
echo ">>> phase 1: editable install"

python3 -m venv "$TMPDIR/editable-venv"
# shellcheck disable=SC1091
source "$TMPDIR/editable-venv/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet -e "$WELD_ROOT"

check_wd_version "editable install"

# `wd --help` from the installed entry point
OUTPUT="$(wd --help 2>&1)"
if echo "$OUTPUT" | grep -q "Usage: wd <command>"; then
  echo "OK: wd --help works after pip install -e"
else
  fail "wd --help did not work after pip install -e"
  echo "Got: $OUTPUT"
fi

# `python -m weld --help` from the installed package
OUTPUT="$(python -m weld --help 2>&1)"
if echo "$OUTPUT" | grep -q "Usage: wd <command>"; then
  echo "OK: python -m weld --help works after pip install -e"
else
  fail "python -m weld --help did not work after pip install -e"
  echo "Got: $OUTPUT"
fi

# `wd scaffold` produces the expected files
mkdir -p "$TMPDIR/editable-workspace"
(
  cd "$TMPDIR/editable-workspace"
  wd scaffold local-strategy smoke_test >"$TMPDIR/weld-editable-scaffold-local.log"
  wd scaffold external-adapter smoke_test >"$TMPDIR/weld-editable-scaffold-adapter.log"
  [[ -f ".weld/strategies/smoke_test.py" ]] || exit 10
  [[ -f ".weld/adapters/smoke_test.py" ]] || exit 11
) || fail "editable install scaffold commands did not produce expected files"

# `weld.__file__` must not be None, and bundled runtime assets must resolve.
# Under `set -e` a non-zero exit here aborts the script; this is intentional
# because subsequent wheel checks depend on an intact editable install.
#
# NOTE: we run this from $TMPDIR (a neutral cwd with no weld/ directory)
# so that Python's sys.path[0] ("") does not accidentally shadow the
# editable-installed weld with a source-tree weld/ directory living
# next to the script invocation. Otherwise the check would silently pass
# even when the editable finder is broken.
(
  cd "$TMPDIR"
  python - <<'PY'
from pathlib import Path
import weld

assert weld.__file__ is not None, (
    "weld.__file__ is None after pip install -e — weld was resolved "
    "as a namespace package. Check weld/__init__.py and pyproject.toml."
)

pkg = Path(weld.__file__).resolve().parent
for name in ("weld_readme.md", "weld_cmd_claude.md", "weld_skill_codex.md", "codex_mcp_config.toml"):
    t = pkg / "templates" / name
    assert t.is_file(), f"missing template: {t}"
print("OK: bundled templates present (editable)")
PY
) || fail "bundled templates not present in editable install"

# Build a wheel from the editable venv (keeps dependencies deterministic)
mkdir -p "$TMPDIR/dist"
pip wheel --quiet --no-deps "$WELD_ROOT" -w "$TMPDIR/dist"
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

check_wd_version "wheel install"

# `wd --help` from the installed wheel
OUTPUT="$(wd --help 2>&1)"
if echo "$OUTPUT" | grep -q "Usage: wd <command>"; then
  echo "OK: wd --help works after wheel install"
else
  fail "wd --help did not work after wheel install"
  echo "Got: $OUTPUT"
fi

# `wd scaffold` from the installed wheel
mkdir -p "$TMPDIR/wheel-workspace"
(
  cd "$TMPDIR/wheel-workspace"
  wd scaffold local-strategy wheel_test >"$TMPDIR/weld-wheel-scaffold-local.log"
  wd scaffold external-adapter wheel_test >"$TMPDIR/weld-wheel-scaffold-adapter.log"
  [[ -f ".weld/strategies/wheel_test.py" ]] || exit 12
  [[ -f ".weld/adapters/wheel_test.py" ]] || exit 13
) || fail "wheel install scaffold commands did not produce expected files"

# `weld.__file__` must not be None after wheel install (the original bug).
# Run from $TMPDIR for the same reason as the editable-install check above:
# avoid cwd shadowing the installed weld with a source-tree weld/ dir.
(
  cd "$TMPDIR"
  python - <<'PY'
from pathlib import Path
import weld

assert weld.__file__ is not None, (
    "weld.__file__ is None after wheel install — weld was resolved "
    "as a namespace package. Check weld/__init__.py and pyproject.toml."
)

pkg = Path(weld.__file__).resolve().parent
for name in ("weld_readme.md", "weld_cmd_claude.md", "weld_skill_codex.md", "codex_mcp_config.toml"):
    t = pkg / "templates" / name
    assert t.is_file(), f"missing template: {t}"
print("OK: bundled templates present (wheel)")
PY
) || fail "bundled templates not present in wheel install"

# `weld.strategies.tree_sitter.load_language_queries("python")` must load
# the bundled python query YAML (exercises package-data + path resolution).
# This requires the tree-sitter optional extra so `import tree_sitter` succeeds
# inside the module; install it into the wheel venv first.
pip install --quiet tree-sitter tree-sitter-python

python - <<'PY' || fail "bundled query YAML did not load after wheel install"
from weld.strategies.tree_sitter import load_language_queries

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

echo "PASS: weld real pip install"
