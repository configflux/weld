#!/usr/bin/env bash
# Regression guard: piping wd output through `head` must exit quietly.
#
# When downstream consumers (like `head`) close their stdin early, Python
# emits a BrokenPipeError traceback unless the CLI handles SIGPIPE
# explicitly. Covers one graph command (`list`) and one non-graph command
# (`--help`) per acceptance criteria on bd issue p1a.5.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${SCRIPT_DIR}/weld_test_lib.sh"

# weld_test_lib.sh enables `set -euo pipefail`; relax `-e` and `pipefail`
# here because the tests intentionally close the pipe early (SIGPIPE on the
# wd side) and need to continue inspecting captured stderr afterward.
set +e
set +o pipefail

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

REPO_ROOT="$(weld_test_repo_root "${SCRIPT_DIR}")"
ROOT="${TMPDIR}/project"
mkdir -p "${ROOT}/.weld"

PYPATH="$(weld_pythonpath "${REPO_ROOT}")"

wd_pipe_to_head() {
  # Run `python3 -m weld "$@" | head -n 1` in a subshell and capture the
  # stderr of the wd command (not head). Print captured stderr on stdout so
  # the caller can inspect it, and echo the wd pipeline exit status on
  # stderr as a trailing line the caller can read via a second invocation.
  local err_file="$1"
  shift
  (
    cd "${ROOT}"
    PYTHONPATH="${PYPATH}" python3 -m weld "$@" 2>"${err_file}"
  ) | head -n 1 >/dev/null
  # We deliberately ignore the SIGPIPE exit code of the pipeline; the point
  # of this test is what the wd process printed to stderr, not its status.
}

# Seed a graph large enough that `list --type file` output exceeds the pipe
# buffer, guaranteeing the writer hits EPIPE after `head` closes its stdin.
# One add-node call per node is slow but deterministic; 200 entries reliably
# produces output well above the 64 KiB pipe buffer.
seed_graph() {
  (
    cd "${ROOT}"
    local seed_batch="${TMPDIR}/seed.json"
    python3 - "${seed_batch}" <<'PY'
import json, sys
path = sys.argv[1]
nodes = {}
for i in range(200):
    node_id = f"file:seed/path{i:04d}"
    nodes[node_id] = {
        "type": "file",
        "label": f"path{i:04d}",
        "props": {
            "file": f"seed/path{i:04d}.py",
            "line_count": 1,
            "notes": "pad " * 16,
        },
    }
with open(path, "w", encoding="utf-8") as fh:
    json.dump({"nodes": nodes, "edges": []}, fh)
PY
    PYTHONPATH="${PYPATH}" python3 -m weld import "${seed_batch}" >/dev/null
  )
}

seed_graph

# --- Graph command: `wd list --type file | head -n 1` must emit empty stderr.
ERR_FILE="${TMPDIR}/list.err"
wd_pipe_to_head "${ERR_FILE}" list --type file
if [[ -s "${ERR_FILE}" ]]; then
  echo "FAIL: wd list --type file | head produced stderr output:" >&2
  cat "${ERR_FILE}" >&2
  exit 1
fi
echo "PASS: wd list --type file | head -n 1 exits quietly"

# --- Non-graph command: `wd --help | head -n 1` must emit empty stderr.
ERR_FILE="${TMPDIR}/help.err"
wd_pipe_to_head "${ERR_FILE}" --help
if [[ -s "${ERR_FILE}" ]]; then
  echo "FAIL: wd --help | head produced stderr output:" >&2
  cat "${ERR_FILE}" >&2
  exit 1
fi
echo "PASS: wd --help | head -n 1 exits quietly"

# --- Non-graph JSON command: `wd query <term> | head -n 1` must emit empty stderr.
ERR_FILE="${TMPDIR}/query.err"
wd_pipe_to_head "${ERR_FILE}" query path
if [[ -s "${ERR_FILE}" ]]; then
  echo "FAIL: wd query path | head produced stderr output:" >&2
  cat "${ERR_FILE}" >&2
  exit 1
fi
echo "PASS: wd query path | head -n 1 exits quietly"

echo "PASS: all CLI SIGPIPE tests passed"
