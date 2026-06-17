"""Resolve git remote URL + HEAD sha for the viz "open in editor" links.

Discovered once at summary build time so click handlers in the browser
don't re-shell out per node. Falls back gracefully when the working
directory isn't a git checkout, has no ``origin`` remote, or the git
binary is missing -- in any of those cases the returned dict carries
``None`` values and the inspector simply hides the remote link.

Acceptance contract (bd h6z0.13):
- SSH remote ``git@github.com:owner/repo.git`` ->
  ``https://github.com/owner/repo``
- HTTPS remote ``https://github.com/owner/repo.git`` ->
  ``https://github.com/owner/repo`` (strip trailing ``.git``)
- Any failure -> ``remote_url=None`` / ``head_sha=None``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_remote_info(root: Path | str) -> dict[str, str | None]:
    """Return ``{remote_url, head_sha}`` for the repo at ``root``.

    Both fields are ``None`` when the repository, the ``origin`` remote,
    or the git binary is unavailable. The caller treats either ``None``
    as "no remote link" and only the local ``vscode://file/...`` link
    is rendered.
    """
    return {
        "remote_url": _normalize_remote(_run_git(root, "remote", "get-url", "origin")),
        "head_sha": _run_git(root, "rev-parse", "HEAD"),
    }


def _run_git(root: Path | str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or "").strip()
    return output or None


def _normalize_remote(raw: str | None) -> str | None:
    """Convert an SSH or HTTPS git remote URL to an HTTPS browser URL.

    Accepts the two forms emitted by ``git remote get-url origin``:
      - ``git@<host>:<owner>/<repo>.git`` (SSH)
      - ``https://<host>/<owner>/<repo>[.git]`` (HTTPS)
    Returns the HTTPS browser-form (no trailing ``.git``) so callers can
    append ``/blob/<sha>/<path>#L<line>`` directly. Returns ``None`` when
    the remote URL doesn't match either form.
    """
    if not raw:
        return None
    if raw.startswith("git@") and ":" in raw:
        host, _, path = raw[len("git@"):].partition(":")
        if not host or not path:
            return None
        url = f"https://{host}/{path}"
    elif raw.startswith("https://") or raw.startswith("http://"):
        url = raw
    else:
        return None
    if url.endswith(".git"):
        url = url[:-len(".git")]
    return url.rstrip("/") or None
