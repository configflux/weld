"""Single sink for operational ``[weld] ...`` notices (bd gcrf).

Every operational notice, warning, or banner weld prints for humans goes to
**stderr**, never stdout. stdout is reserved for command payloads -- most
importantly the machine-readable JSON emitted under ``--json`` -- so a notice
must never interleave with, or precede, a payload a consumer will
``json.loads``. Routing such lines through :func:`emit` keeps that invariant in
one place: the destination is fixed to ``sys.stderr`` here and cannot be
accidentally set to stdout at a call site.
"""

from __future__ import annotations

import sys
from typing import IO

__all__ = ["emit"]

_PREFIX = "[weld]"


def emit(message: str, *, stream: IO[str] | None = None) -> None:
    """Write one ``[weld] ...`` line to stderr (never stdout).

    *message* is written as-is when it already starts with ``[weld]``;
    otherwise the prefix is prepended. A trailing newline is ensured. *stream*
    is a test seam -- production callers omit it and always get ``sys.stderr``
    (a caller may still thread a captured stderr through for isolation, but must
    never pass stdout).
    """
    err = stream if stream is not None else sys.stderr
    text = message if message.startswith(_PREFIX) else f"{_PREFIX} {message}"
    if not text.endswith("\n"):
        text += "\n"
    err.write(text)
