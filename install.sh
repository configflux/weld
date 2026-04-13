#!/bin/sh
# install.sh — POSIX installer for configflux-cortex
# See cortex/docs/adr/0007-installer-strategy.md for design rationale.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/configflux/cortex/main/install.sh | sh
#
# Respects .cortex-version file (plain semver, CWD-or-ancestor walk).
# Idempotent: re-running upgrades an existing installation.
set -eu

# -- Constants ---------------------------------------------------------------

REPO_URL="https://github.com/configflux/cortex.git"
INSTALL_SUBDIRECTORY="cortex"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
MAX_PYTHON_MINOR=13
LOCAL_BIN="${HOME}/.local/bin"

# -- Logging -----------------------------------------------------------------

info()  { printf '[cortex]  %s\n' "$*"; }
warn()  { printf '[cortex]  WARN: %s\n' "$*" >&2; }
error() { printf '[cortex]  ERROR: %s\n' "$*" >&2; }
die()   { error "$@"; exit 1; }

# -- OS / arch detection -----------------------------------------------------

detect_os_arch() {
    OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64)  ARCH="amd64" ;;
        aarch64) ARCH="arm64" ;;
        arm64)   ARCH="arm64" ;;
    esac
    info "Detected OS=$OS ARCH=$ARCH"
}

# -- Python detection --------------------------------------------------------

# Find a compatible Python (3.10 -- 3.13).
# Checks versioned binaries first (newest to oldest), then generic names.
find_python() {
    PYTHON=""
    minor=$MAX_PYTHON_MINOR
    while [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; do
        candidate="python${MIN_PYTHON_MAJOR}.${minor}"
        if command -v "$candidate" >/dev/null 2>&1; then
            if check_python_version "$candidate"; then
                PYTHON="$candidate"
                return 0
            fi
        fi
        minor=$((minor - 1))
    done

    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if check_python_version "$candidate"; then
                PYTHON="$candidate"
                return 0
            fi
        fi
    done

    return 1
}

# Verify a Python binary is within supported range.
check_python_version() {
    _py="$1"
    _ver="$("$_py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" || return 1
    _major="${_ver%%.*}"
    _minor="${_ver#*.}"
    [ "$_major" -eq "$MIN_PYTHON_MAJOR" ] || return 1
    [ "$_minor" -ge "$MIN_PYTHON_MINOR" ] && [ "$_minor" -le "$MAX_PYTHON_MINOR" ]
}

# -- Package manager detection -----------------------------------------------

# Select best available installer: uv > pipx > pip.
find_installer() {
    INSTALLER=""
    if command -v uv >/dev/null 2>&1; then
        INSTALLER="uv"
        return 0
    fi
    if command -v pipx >/dev/null 2>&1; then
        INSTALLER="pipx"
        return 0
    fi
    # pip must come from the selected Python.
    if "$PYTHON" -m pip --version >/dev/null 2>&1; then
        INSTALLER="pip"
        return 0
    fi
    return 1
}

# -- .cortex-version resolution ----------------------------------------------

# Walk from CWD to filesystem root looking for .cortex-version.
# Sets VERSION_PIN if found, empty otherwise.
resolve_version_pin() {
    VERSION_PIN=""
    dir="$(pwd)"
    while true; do
        if [ -f "${dir}/.cortex-version" ]; then
            # Read first non-blank, non-whitespace-only line.
            VERSION_PIN="$(grep -v '^[[:space:]]*$' "${dir}/.cortex-version" | head -1 | tr -d '[:space:]')"
            if [ -n "$VERSION_PIN" ]; then
                info "Found .cortex-version pin: $VERSION_PIN"
                return 0
            fi
        fi
        parent="$(dirname "$dir")"
        if [ "$parent" = "$dir" ]; then
            break
        fi
        dir="$parent"
    done
    return 1
}

# -- Build install spec ------------------------------------------------------

# Compute the pip-style install specifier.
# If VERSION_PIN is set, install that exact git tag; otherwise install HEAD.
build_install_spec() {
    base="git+${REPO_URL}#subdirectory=${INSTALL_SUBDIRECTORY}"
    if [ -n "${VERSION_PIN:-}" ]; then
        INSTALL_SPEC="git+${REPO_URL}@v${VERSION_PIN}#subdirectory=${INSTALL_SUBDIRECTORY}"
    else
        INSTALL_SPEC="$base"
    fi
}

# -- Install -----------------------------------------------------------------

do_install() {
    build_install_spec
    info "Installing configflux-cortex via $INSTALLER ..."
    info "  spec: $INSTALL_SPEC"

    case "$INSTALLER" in
        uv)
            uv tool install --force --from "$INSTALL_SPEC" configflux-cortex
            ;;
        pipx)
            pipx install --force "$INSTALL_SPEC"
            ;;
        pip)
            "$PYTHON" -m pip install --user --upgrade "$INSTALL_SPEC"
            ;;
        *)
            die "Unknown installer: $INSTALLER"
            ;;
    esac
}

# -- PATH advisory -----------------------------------------------------------

check_path() {
    case ":${PATH}:" in
        *":${LOCAL_BIN}:"*)
            return 0
            ;;
    esac
    warn "${LOCAL_BIN} is not in your PATH."
    warn "Add the following to your shell profile:"
    warn "  export PATH=\"${LOCAL_BIN}:\$PATH\""
}

# -- Main --------------------------------------------------------------------

main() {
    info "configflux-cortex installer"
    info "========================"

    detect_os_arch

    if ! find_python; then
        die "No compatible Python found (need ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}--${MIN_PYTHON_MAJOR}.${MAX_PYTHON_MINOR}). Install Python and retry."
    fi
    info "Using Python: $PYTHON ($("$PYTHON" --version 2>&1))"

    if ! find_installer; then
        die "No package installer found. Install uv, pipx, or pip and retry."
    fi
    info "Using installer: $INSTALLER"

    resolve_version_pin || true

    do_install

    check_path

    info "Done. Run 'cortex --help' to get started."
}

main "$@"
