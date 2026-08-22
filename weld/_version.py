"""Canonical resolution of weld's own version string.

Two things need to say how old the running weld is -- ``wd --version`` and
anything that stamps identity into a payload (telemetry events, the MCP
``initialize`` reply) -- and they must not answer differently. Both
environments weld runs in are covered here:

* installed (``pip``/``uv`` install of ``configflux-weld``) -- distribution
  metadata is authoritative;
* raw source checkout (``python -m weld`` with no install) -- there is no
  metadata, so the repo-root ``VERSION`` file next to the package is the
  only source of truth.

Resolution can genuinely fail (a partial checkout with neither), so the
answer is optional rather than a guessed number: callers pick the
placeholder that suits their surface, because a version-shaped lie and a
human-readable "unknown" are wrong in different places.

Stdlib-only and import-cheap on purpose -- this is imported from startup
paths that must not pull the rest of the package in with them.
"""

from __future__ import annotations

from pathlib import Path

#: PyPI distribution name. The import package is ``weld``; the published
#: distribution is not, so metadata lookups must use this name.
DISTRIBUTION_NAME = "configflux-weld"

#: Bound on what the ``VERSION`` file may contribute. It is plain text with
#: no schema, and what it says travels: into the MCP ``initialize`` reply,
#: into every telemetry event, onto stdout. A version is short and
#: single-line by construction, so anything longer is corruption rather than
#: a version. The same number bounds the read, so an arbitrarily large file
#: never reaches memory on a startup path -- and because the read stops one
#: character past the limit, an over-long line is *rejected* rather than
#: silently truncated into something that still looks like a version.
#: Metadata is not bounded here: it comes from an installed distribution
#: that packaging tools already validated.
_MAX_VERSION_LEN = 128


def version_file_path() -> Path:
    """Path of the repo-root ``VERSION`` file for a source checkout.

    Points one level above the package directory, which is the repo root in
    a checkout and site-packages in an installed wheel -- where no
    ``VERSION`` exists, and none is needed because metadata answers first.
    """
    return Path(__file__).resolve().parent.parent / "VERSION"


def weld_version() -> str | None:
    """Return weld's version, or ``None`` when it cannot be determined.

    Never raises: every caller is on a startup or telemetry path where
    failing to name a version must not fail the operation itself.
    """
    try:
        from importlib.metadata import version

        resolved = version(DISTRIBUTION_NAME).strip()
        if resolved:
            return resolved
    except Exception:  # noqa: BLE001 -- absent/unreadable metadata is normal.
        pass

    try:
        with version_file_path().open(encoding="utf-8") as handle:
            from_file = handle.readline(_MAX_VERSION_LEN + 1).strip()
    except Exception:  # noqa: BLE001 -- the promise above is unconditional.
        # Deliberately wider than OSError: a corrupt VERSION file raises
        # UnicodeDecodeError (a ValueError), and an interpreter that gives
        # the module no ``__file__`` fails before the read even starts.
        # Neither may reach a caller that was told this cannot fail, and
        # no failure here is worth more than "version unknown".
        return None
    if not from_file or len(from_file) > _MAX_VERSION_LEN:
        return None
    return from_file
