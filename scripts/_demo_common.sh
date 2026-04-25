#!/usr/bin/env bash
# Shared helpers for the Weld demo bootstrap scripts.
#
# These scripts are public, frictionless on-ramps that materialize a
# Weld demo workspace (monorepo or polyrepo) into a target directory
# with seeded source files, .weld configs, and committed git history.
#
# Sourced by:
#   scripts/create-monorepo-demo.sh
#   scripts/create-polyrepo-demo.sh
#
# Portable bash 3.2+ (works on macOS default /bin/bash and Linux bash).

set -euo pipefail

# Pretty error: emit to stderr and exit non-zero.
die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*"
}

# Verify the tools the demo scripts depend on are on PATH.
# We deliberately keep this list short and standard.
demo_require_tools() {
  local missing=""
  for tool in git mkdir cat; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      missing="$missing $tool"
    fi
  done
  if [ -n "$missing" ]; then
    die "missing required tool(s):${missing}. Install them and retry."
  fi
}

# Resolve the git identity that a freshly-created repo will inherit.
# A new `git init` inside the demo directory only sees system + global
# (and the new repo's own local) config -- it does NOT inherit
# identity from whatever repo the script happens to be invoked from.
# So we deliberately query system and global only and warn early if
# neither has a usable identity, instead of letting `git commit`
# fail with a confusing "empty ident name" error mid-run.
#
# Sets the globals DEMO_GIT_NAME and DEMO_GIT_EMAIL so seed_repo can
# apply them as repo-local config without depending on commit-time
# inheritance. This makes the script work on machines where the user
# only ever set identity inside other repos (--local), without
# silently rewriting their global config.
demo_require_git_identity() {
  local name email
  name="$(git config --system --get user.name 2>/dev/null || true)"
  email="$(git config --system --get user.email 2>/dev/null || true)"
  if [ -z "$name" ]; then
    name="$(git config --global --get user.name 2>/dev/null || true)"
  fi
  if [ -z "$email" ]; then
    email="$(git config --global --get user.email 2>/dev/null || true)"
  fi
  if [ -z "$name" ] || [ -z "$email" ]; then
    cat >&2 <<'EOF'
error: git identity is not configured (no user.name or user.email).

The demo scripts run `git commit` to seed the demo repos, which
requires a user.name and user.email visible to a freshly-created
repository. Configure them once with:

  git config --global user.name  "Your Name"
  git config --global user.email "you@example.com"

Then re-run this script.
EOF
    exit 1
  fi
  DEMO_GIT_NAME="$name"
  DEMO_GIT_EMAIL="$email"
}

# Resolve and validate the target directory argument.
# The directory must either not exist (we create it) or be empty.
# Outputs the absolute path of the resolved target on stdout.
demo_resolve_target() {
  local target="${1:-}"
  if [ -z "$target" ]; then
    die "missing target directory.\n  usage: $(basename "$0") <target-dir>"
  fi
  if [ -e "$target" ]; then
    if [ ! -d "$target" ]; then
      die "target exists and is not a directory: $target"
    fi
    if [ -n "$(ls -A "$target" 2>/dev/null || true)" ]; then
      die "target directory is not empty: $target"
    fi
  fi
  mkdir -p "$target"
  # Portable absolute path (avoid GNU readlink -f for macOS compat).
  ( cd "$target" && pwd )
}

# Initialize a git repo and create one seed commit at the given path.
# Stages and commits everything currently in the directory. Applies
# the identity resolved by demo_require_git_identity as repo-local
# config so the commit succeeds even if the user has no global
# identity but ran the script after sourcing one elsewhere.
# Args: <repo-path> <commit-message>
demo_seed_repo() {
  local path="$1"
  local message="$2"
  if ! ( cd "$path" && git init --quiet --initial-branch=main >/dev/null 2>&1 ); then
    ( cd "$path" && git init --quiet )
  fi
  ( cd "$path" \
    && git config user.name  "${DEMO_GIT_NAME:-Weld Demo}" \
    && git config user.email "${DEMO_GIT_EMAIL:-demo@example.com}" \
    && git add -A \
    && git commit --quiet -m "$message" )
}
