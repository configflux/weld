#!/usr/bin/env bash
# Test: cortex structural / package layout
#
# Verifies that `cortex/pyproject.toml` is well-formed, that the package
# entry points exist, that `python -m cortex --help` works from source,
# and that `import cortex` resolves to a regular package whose bundled
# runtime assets (templates/*.md, languages/*.yaml) are accessible
# relative to ``Path(cortex.__file__).parent``.
#
# This check runs everywhere (local gate and CI) and MUST NOT have any
# SKIP paths. It does NOT perform a real pip install — that lives in
# cortex/tests/cortex_real_install.sh and runs in a CI-only job, because
# the local devcontainer ships without ensurepip.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cortex/tests/cortex_test_lib.sh
source "${SCRIPT_DIR}/cortex_test_lib.sh"

REPO_ROOT="$(cortex_test_repo_root "$SCRIPT_DIR")"
CORTEX_ROOT="${REPO_ROOT}/cortex"

FAILURES=0

fail() {
  echo "FAIL: $1"
  FAILURES=$((FAILURES + 1))
}

# --- Guard: pyproject.toml must exist ---
if [[ ! -f "$CORTEX_ROOT/pyproject.toml" ]]; then
  fail "cortex/pyproject.toml not found"
  exit 1
fi

# --- Validate pyproject.toml structure ---
if ! CORTEX_PYPROJECT="$CORTEX_ROOT/pyproject.toml" python3 - <<'PY'; then
import os
import pathlib
import sys
import tomllib

path = pathlib.Path(os.environ["CORTEX_PYPROJECT"])
try:
    data = tomllib.loads(path.read_text())
except Exception as e:
    print(f"FAIL: pyproject.toml is not valid TOML: {e}")
    sys.exit(1)

project = data.get("project", {})
errors: list[str] = []

if not project.get("name"):
    errors.append("project.name is missing")
if not project.get("version"):
    errors.append("project.version is missing")

req_python = project.get("requires-python", "")
if not req_python or not req_python.startswith(">="):
    errors.append("project.requires-python is missing or invalid")

deps = project.get("dependencies", [])
if deps:
    errors.append(f"expected zero mandatory dependencies, got {deps}")

scripts = project.get("scripts", {})
if scripts.get("cortex") != "cortex.cli:main":
    errors.append(
        f"expected project.scripts.cortex='cortex.cli:main', got {scripts.get('cortex')!r}"
    )
if scripts.get("kg") != "cortex.compat:kg_shim_main":
    errors.append(
        f"expected project.scripts.kg='cortex.compat:kg_shim_main' (deprecation shim), got {scripts.get('kg')!r}"
    )

optional = project.get("optional-dependencies", {})
if "tree-sitter" not in optional:
    errors.append("optional-dependencies should include tree-sitter extra")
else:
    extra = optional["tree-sitter"]
    required = [
        "tree-sitter",
        "tree-sitter-cpp",
        "tree-sitter-go",
        "tree-sitter-javascript",
        "tree-sitter-python",
        "tree-sitter-rust",
        "tree-sitter-typescript",
    ]
    for package in required:
        if not any(str(req).startswith(package) for req in extra):
            errors.append(f"tree-sitter extra missing {package}")

tool = data.get("tool", {})
setuptools_cfg = tool.get("setuptools", {})
package_data = setuptools_cfg.get("package-data", {})
cortex_data = package_data.get("cortex", [])
required_patterns = {"languages/*.yaml", "templates/*.py", "templates/*.md"}
missing_patterns = sorted(required_patterns - set(cortex_data))
if missing_patterns:
    errors.append(f"package-data.cortex missing patterns: {missing_patterns}")

build = data.get("build-system", {})
if not build.get("requires"):
    errors.append("build-system.requires is missing")
if build.get("build-backend") != "setuptools.build_meta":
    errors.append(
        f"expected setuptools.build_meta backend, got {build.get('build-backend')!r}"
    )

if errors:
    for e in errors:
        print(f"FAIL: {e}")
    sys.exit(1)
print("OK: pyproject.toml structure is valid")
PY
  FAILURES=$((FAILURES + 1))
fi

# --- Validate package entry points exist ---
if [[ ! -f "$CORTEX_ROOT/__init__.py" ]]; then
  fail "__init__.py not found in cortex/"
fi
if [[ ! -f "$CORTEX_ROOT/__main__.py" ]]; then
  fail "__main__.py not found in cortex/"
fi
if [[ ! -f "$CORTEX_ROOT/cli.py" ]]; then
  fail "cli.py not found in cortex/"
fi

# --- Validate python -m cortex works from source (no install) ---
OUTPUT="$(cortex_in_root "$REPO_ROOT" "$REPO_ROOT" --help 2>&1)" || true
if echo "$OUTPUT" | grep -q "Usage: cortex <command>"; then
  echo "OK: python -m cortex --help works from source"
else
  fail "python -m cortex --help did not produce expected output from source"
  echo "Got: $OUTPUT"
fi

# --- Import cortex and resolve bundled runtime assets ---
#
# This is the check that matters. It does `import cortex`, asserts
# ``cortex.__file__`` is not None (which catches the namespace-package
# regression), then resolves bundled markdown templates and tree-sitter
# language queries relative to ``Path(cortex.__file__).parent``.
#
# No SKIP paths — this runs everywhere. If it fails, CI fails and the
# local gate fails.
if (
  cd "$REPO_ROOT"
  PYTHONPATH="$(cortex_pythonpath "$REPO_ROOT")" python3 - <<'PY'
from pathlib import Path

import cortex

# --- regression guard: cortex must be a regular package, not a namespace one ---
if cortex.__file__ is None:
    raise AssertionError(
        "cortex.__file__ is None — cortex was resolved as a namespace "
        "package. Check cortex/__init__.py and pyproject.toml packaging."
    )

pkg = Path(cortex.__file__).resolve().parent

# --- bundled markdown templates must resolve relative to the package ---
templates_dir = pkg / "templates"
required_templates = (
    "cortex_readme.md",
    "cortex_cmd_claude.md",
    "cortex_skill_codex.md",
)
missing_templates = [
    name for name in required_templates if not (templates_dir / name).is_file()
]
if missing_templates:
    raise AssertionError(
        f"bundled templates missing under {templates_dir}: {missing_templates}"
    )

# --- bundled tree-sitter language queries must resolve relative to the package ---
languages_dir = pkg / "languages"
required_languages = (
    "python.yaml",
    "go.yaml",
    "typescript.yaml",
    "rust.yaml",
    "cpp.yaml",
)
missing_languages = [
    name for name in required_languages if not (languages_dir / name).is_file()
]
if missing_languages:
    raise AssertionError(
        f"bundled language queries missing under {languages_dir}: "
        f"{missing_languages}"
    )

print("OK: import cortex resolves runtime assets from", pkg)
PY
); then
  :
else
  fail "structural asset check failed — see traceback above"
fi

# --- Final verdict ---
if [[ $FAILURES -gt 0 ]]; then
  echo "RESULT: $FAILURES failure(s)"
  exit 1
fi

echo "PASS: cortex structural / package layout test"
