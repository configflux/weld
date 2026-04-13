#!/usr/bin/env bash
set -euo pipefail

cortex_test_repo_root() {
  local script_dir="$1"

  if [[ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]]; then
    printf '%s\n' "${BUILD_WORKSPACE_DIRECTORY}"
    return 0
  fi

  local marker="${script_dir}/../__main__.py"
  if [[ -e "${marker}" ]]; then
    local kg_dir
    kg_dir="$(cd "$(dirname "$(readlink -f "${marker}")")" && pwd)"
    printf '%s\n' "$(dirname "${kg_dir}")"
    return 0
  fi

  local root="${script_dir}"
  while [[ "${root}" != "/" && ! -e "${root}/cortex/__main__.py" ]]; do
    root="$(dirname "${root}")"
  done
  printf '%s\n' "${root}"
}

cortex_pythonpath() {
  local repo_root="$1"
  if [[ -n "${PYTHONPATH:-}" ]]; then
    printf '%s:%s\n' "${repo_root}" "${PYTHONPATH}"
  else
    printf '%s\n' "${repo_root}"
  fi
}

cortex_in_root() {
  local repo_root="$1"
  local work_root="$2"
  shift 2
  (
    cd "${work_root}"
    PYTHONPATH="$(cortex_pythonpath "${repo_root}")" python3 -m cortex "$@"
  )
}
