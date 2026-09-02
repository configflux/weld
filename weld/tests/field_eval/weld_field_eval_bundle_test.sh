#!/usr/bin/env bash
# The evaluator's field-eval bundle, run as a gate (bd ...d76r1.12, ...lcq0c.2).
#
# An external evaluation reports a defect by *running weld* and showing what it
# printed. Three rounds have now handed back the same shape of thing, and
# `weld/tests/fixtures/field_eval/` holds the v0.25.0 round's scripts byte for
# byte -- make-fixture.sh, bootstrap-fixture.sh, verify-all-fixes.sh. They sit
# under `fixtures/` rather than beside this file because that is what they are:
# received artifacts, not source we author, and editing one to satisfy a
# repo standard would break the only property that makes this lane worth
# having. (Concretely: round three's make-fixture.sh is 510 lines, over the
# 400-line cap, and `tools/lint_repo.py` already treats `weld/tests/fixtures/`
# as data for exactly this reason.) This file is ours and stays linted. It runs
# them in the order the evaluator does:
#
#   make-fixture -> bootstrap-fixture -> verify-all-fixes
#
# `wd` is a shim on PATH running `python3 -m weld` out of this checkout, so the
# scripts execute unmodified: what the next evaluator runs is what this runs.
#
# One verification script where the v0.24.0 round had two: verify-all-fixes.sh
# supersedes both verify-previous-fixes.sh (theirs) and verify-0.24.0-fixes.sh
# (ours), asserting all eighteen findings from the first two rounds plus the
# four behaviours 0.25.0 introduced -- 22 checks in one PASS/FAIL run.
# run-all-repros.sh is deliberately *not* absorbed: it writes transcripts for a
# human and asserts nothing, and M1-M4 are probes in
# weld_field_eval_v0250_e2e_test.py instead.
#
# This is the *shell surface* twin of the hermetic py_test corpus
# (weld_field_eval_{corpus,e2e,regression_e2e}_test), not a replacement for it.
# Those probe the same findings in-process and run in the fast loop; this one
# proves the scripts we hand back still work as scripts, which no in-process
# port can. It is also the release-audit evidence for field-eval findings
# (docs/testing-hygiene.md, "Fixing a field finding").
#
# One skip and one tolerance, both narrow and both loud:
#
# * no ambient `git` -- nothing here can run, so the whole target skips;
# * no ambient `tree_sitter_c_sharp` -- checks 03a and 08 are the only two of
#   the 22 that read symbol-level C# extraction, and the repo deliberately does
#   not pin that grammar (ADR 0069). With one verification script left, the
#   v0.24.0 shape -- skip the script whole -- would leave this lane asserting
#   nothing at all, so the script runs either way and exactly that two-id
#   failure set is tolerated when the grammar is absent. A third failing check,
#   a check that did not run, or any total other than EXPECTED_CHECKS is red.

#: Two layouts to resolve against, and the difference is Bazel's: an `sh_test`
#: binary is installed at `<package>/<name>`, so under runfiles this script
#: executes from `weld/tests/` however deep its source sits, while a by-hand
#: run executes it from `weld/tests/field_eval/`. `weld_test_lib.sh` marks the
#: package directory in both.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/weld_test_lib.sh" ]]; then
  TESTS_DIR="${SCRIPT_DIR}"
else
  TESTS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi
FIXTURE_DIR="${TESTS_DIR}/fixtures/field_eval"
# shellcheck source=weld/tests/weld_test_lib.sh
source "${TESTS_DIR}/weld_test_lib.sh"

#: The interpreter the `wd` shim runs and the grammar probe asks about. One
#: variable so a by-hand run can point both at a tree-sitter-capable venv.
PY="${WELD_FIELD_EVAL_PYTHON:-python3}"

if ! command -v git >/dev/null 2>&1; then
  printf 'SKIP: the field-eval bundle needs an ambient git; none is on PATH.\n'
  exit 0
fi
if ! command -v "${PY}" >/dev/null 2>&1; then
  printf 'SKIP: the field-eval bundle needs %s; it is not on PATH.\n' "${PY}"
  exit 0
fi

# `weld_test_repo_root` reads its marker at <dir>/../__main__.py, so it is
# handed the package directory -- weld/tests, not weld/tests/field_eval.
REPO_ROOT="$(weld_test_repo_root "${TESTS_DIR}")"
PYPATH="$(weld_pythonpath "${REPO_ROOT}")"

if [[ -n "${TEST_TMPDIR:-}" ]]; then
  WORK="${TEST_TMPDIR}/field-eval"
else
  # By hand, outside Bazel: our own tempdir, and ours to remove.
  OWN_TMP="$(mktemp -d)"
  trap 'rm -rf "${OWN_TMP}"' EXIT
  WORK="${OWN_TMP}/field-eval"
fi
mkdir -p "${WORK}/bin" "${WORK}/home"

# Environment pinned rather than inherited: a redirected HOME so no ambient
# git or weld config is read, telemetry off, and WELD_AUTO_REFRESH=0 so a read
# can never rewrite the graph a check is about to assert on (the evaluator
# sets that same variable by hand in their N5 probe, for the same reason).
export HOME="${WORK}/home"
export WELD_TELEMETRY="off"
export WELD_AUTO_REFRESH="0"
export WELD_SOURCE_CHECKOUT_NOTICE="off"
export PYTHONHASHSEED="0"
# A read-only runfiles tree makes every one of the ~40 subprocesses recompile
# weld; a writable per-run cache makes that a first-call cost, not a per-call one.
export PYTHONPYCACHEPREFIX="${WORK}/pycache"

cat > "${WORK}/bin/wd" <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="${PYPATH}" "${PY}" -m weld "\$@"
EOF
chmod +x "${WORK}/bin/wd"
export PATH="${WORK}/bin:${PATH}"

# Leave the runfiles root before running anything. `python -m weld` puts the
# working directory first on sys.path, and the runfiles tree has a *partial*
# `weld/` package in it (`//weld:module_entrypoint` is the entry point, not the
# library), so a `wd` invoked from there imports that stub and dies on the
# first submodule. Every script below takes an absolute path, so the only
# working directory this target needs is one with no `weld/` in it.
cd "${WORK}"

# The shim must run, and must resolve to the tree under test: a `python3 -m
# weld` that finds some *other* installed weld runs green and proves nothing
# about this branch. Asserted through `wd --version` rather than through the
# import alone, because that is the call every script below makes first -- an
# import that resolves while the CLI cannot start is the failure that hid here
# once already.
if ! VERSION="$(wd --version 2>&1)" || [[ -z "${VERSION}" ]]; then
  printf 'FAIL: the wd shim cannot run: %s\n' "${VERSION}"
  exit 1
fi
LOADED="$(PYTHONPATH="${PYPATH}" "${PY}" -c 'import weld; print(weld.__file__)' 2>&1 || true)"
EXPECTED="${REPO_ROOT}/weld/__init__.py"
if [[ "$(readlink -f "${LOADED}")" != "$(readlink -f "${EXPECTED}")" ]]; then
  printf 'FAIL: the wd shim imports weld from %s, not the tree under test (%s)\n' \
    "${LOADED}" "${EXPECTED}"
  exit 1
fi
printf 'field-eval bundle: %s from %s\n' "${VERSION}" "${REPO_ROOT}"

RC=0
step() { # step <label> <command...>
  printf '\n=== %s\n' "$1"
  local label="$1"
  shift
  if "$@"; then return 0; fi
  printf '!!! %s FAILED\n' "${label}"
  RC=1
}

# ------------------------------------------------------------------ drift
# The shell fixture and the py_test corpus's materialiser are two copies of
# one workspace. Nothing else compares them, so they can silently diverge and
# the two lanes quietly stop testing the same thing. Asserted here because it
# is only possible now that make-fixture.sh is in-tree: identical file bodies
# (git metadata excluded, it carries timestamps and object ids) and identical
# tracked sets per repo -- the second is what pins the COMMIT ORDER, i.e. that
# acme_notify/, .venv/ and .gitignore stay untracked in notify-service.
drift_guard() {
  local base="${WORK}/drift"
  rm -rf "${base}"
  mkdir -p "${base}"
  # `step` runs this inside an `if`, which suspends `set -e` for the whole
  # body -- so each half says explicitly that it must succeed, rather than
  # letting a half-built tree reach the comparison and fail as "drift".
  "${FIXTURE_DIR}/make-fixture.sh" "${base}/shell" >/dev/null || return 1
  PYTHONPATH="${PYPATH}" "${PY}" - "${base}/python" <<'PYFIXTURE' || return 1
import sys
from pathlib import Path

from weld.tests._field_eval_corpus_fixture import materialize_workspace

materialize_workspace(Path(sys.argv[1]), git=True)
PYFIXTURE
  PYTHONPATH="${PYPATH}" "${PY}" - "${base}/shell" "${base}/python" <<'PYDRIFT'
import hashlib
import subprocess
import sys
from pathlib import Path

REPOS = (
    ".",
    "libs/order-schema",
    "libs/billing-schema",
    "services/order-gateway",
    "services/notify-service",
    "docs-site",
)


def snapshot(root: Path) -> dict[str, str]:
    """sha256 of every file under *root*, excluding git's own metadata."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if ".git" in rel.parts or not path.is_file():
            continue
        out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def tracked(root: Path, repo: str) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(root / repo), "ls-files"],
        check=True, capture_output=True, text=True,
    )
    return sorted(proc.stdout.splitlines())


shell_root, python_root = Path(sys.argv[1]), Path(sys.argv[2])
shell, python = snapshot(shell_root), snapshot(python_root)
problems = []
for name, missing in (
    ("only make-fixture.sh writes", sorted(set(shell) - set(python))),
    ("only the materialiser writes", sorted(set(python) - set(shell))),
):
    if missing:
        problems.append(f"  {name}: {missing}")
differing = sorted(k for k in set(shell) & set(python) if shell[k] != python[k])
if differing:
    problems.append(f"  bodies differ: {differing}")
for repo in REPOS:
    shell_tracked, python_tracked = tracked(shell_root, repo), tracked(python_root, repo)
    if shell_tracked != python_tracked:
        problems.append(
            f"  {repo}: tracked sets differ -- "
            f"only shell {sorted(set(shell_tracked) - set(python_tracked))}, "
            f"only python {sorted(set(python_tracked) - set(shell_tracked))}"
        )
if problems:
    print(
        "make-fixture.sh and weld.tests._field_eval_corpus_fixture have "
        f"drifted ({len(shell)} vs {len(python)} files):",
        *problems, sep="\n", file=sys.stderr,
    )
    raise SystemExit(1)
print(f"  identical: {len(shell)} files, identical tracked sets in {len(REPOS)} repos")
PYDRIFT
}

# ------------------------------------------------------- verify-all-fixes
#: How many checks the script must print, and the two of them that read
#: symbol-level C# extraction. The count is pinned rather than read off the
#: run: a script that silently stopped running half its checks would
#: otherwise be a smaller green run instead of a failure. A bundle that adds
#: a check bumps this in the same commit that copies the script in.
#:
#: 22, not the 21 the bundle's own README and header say: the evaluator
#: counts *findings*, and finding 03 is checked twice -- 03a asserts the
#: positive (a C# repo reports csharp symbols) and 03b the negative (a
#: Python-only one does not). Counted here as what the script prints, since
#: that is what this guard can actually see.
EXPECTED_CHECKS=22
GRAMMAR_DEPENDENT_CHECKS="03a 08"

# Run the evaluator's verification script and judge its PASS/FAIL lines
# rather than only its exit status. Without an ambient `tree_sitter_c_sharp`
# the two grammar-dependent checks fail -- the script is a byte-for-byte copy
# with no skip of its own -- and it is the *only* verification script in this
# lane, so skipping it would leave the target asserting nothing. Tolerating
# exactly those two ids keeps the other twenty live and still fails on a
# third.
#: The received script writes four fixed absolute paths -- /tmp/wv25-good.yaml,
#: -refresh.txt, -force.txt -- so two copies of it running at once overwrite
#: each other's state and check N7 compares one run's `--refresh` output
#: against another run's `--force`. Measured: green alone, red in 2 of 3 under
#: `--runs_per_test=3`, and the same race is reachable from two worktrees
#: gating at once, where it would read as a real regression in `wd init`.
#:
#: It is fixed here rather than there because the script is a received
#: artifact this lane must run byte for byte -- editing it would break the one
#: property the lane has. A whole-machine lock changes nothing the script sees,
#: only when it runs. `flock` is util-linux and effectively always present; if
#: it is not, the run proceeds unserialised rather than skipping, and says so.
_LOCK="/tmp/weld-field-eval-bundle.lock"

run_verify_script() { # run_verify_script <logfile>
  if command -v flock >/dev/null 2>&1; then
    flock "${_LOCK}" "${FIXTURE_DIR}/verify-all-fixes.sh" "${WS}" >"$1" 2>&1
    return
  fi
  printf 'NOTE: no flock; running verify-all-fixes.sh unserialised.\n'
  "${FIXTURE_DIR}/verify-all-fixes.sh" "${WS}" >"$1" 2>&1
}

verify_all_fixes() {
  local log="${WORK}/verify-all-fixes.log"
  local rc=0
  run_verify_script "${log}" || rc=$?
  cat "${log}"

  local ran failed tolerated unexpected id failed_count
  ran="$(grep -cE '^[[:space:]]+(PASS|FAIL)[[:space:]]' "${log}" || true)"
  if [[ "${ran}" -ne "${EXPECTED_CHECKS}" ]]; then
    printf 'FAIL: ran %s checks, expected %s\n' "${ran}" "${EXPECTED_CHECKS}"
    return 1
  fi
  if [[ "${rc}" -eq 0 ]]; then return 0; fi

  failed="$(awk '$1 == "FAIL" { printf "%s ", $2 }' "${log}")"
  if [[ -z "${failed}" ]]; then
    printf 'FAIL: exited %s with no FAIL line to account for it\n' "${rc}"
    return 1
  fi
  tolerated=""
  if ! "${PY}" -c "import tree_sitter_c_sharp" >/dev/null 2>&1; then
    tolerated="${GRAMMAR_DEPENDENT_CHECKS}"
  fi
  unexpected=""
  failed_count=0
  for id in ${failed}; do
    failed_count=$((failed_count + 1))
    case " ${tolerated} " in
      *" ${id} "*) ;;
      *) unexpected="${unexpected} ${id}" ;;
    esac
  done
  if [[ -n "${unexpected}" ]]; then
    printf 'FAIL: checks failed that this environment does not excuse:%s\n' \
      "${unexpected}"
    return 1
  fi
  printf 'TOLERATED: %s-- no ambient tree_sitter_c_sharp, which this repo\n' "${failed}"
  printf '           deliberately does not pin (ADR 0069). The other %s checks passed.\n' \
    "$((EXPECTED_CHECKS - failed_count))"
  return 0
}

# ------------------------------------------------------------------ bundle
WS="${WORK}/ws"
step "drift guard: make-fixture.sh vs the py_test materialiser" drift_guard
step "make-fixture.sh" "${FIXTURE_DIR}/make-fixture.sh" "${WS}"
step "bootstrap-fixture.sh" "${FIXTURE_DIR}/bootstrap-fixture.sh" "${WS}"
step "verify-all-fixes.sh" verify_all_fixes

printf '\n'
if [[ "${RC}" -eq 0 ]]; then
  printf 'field-eval bundle: every script passed.\n'
else
  printf 'field-eval bundle: at least one script failed.\n'
fi
exit "${RC}"
