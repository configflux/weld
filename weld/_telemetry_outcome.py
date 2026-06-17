"""Outcome classification for telemetry events (ADR 0035).

Pure functions that map an exit-time exception to the
``(outcome, exit_code, error_kind)`` triple a telemetry event records.
Split out of :mod:`weld._telemetry` so the writer module stays under the
line-count cap; the logic has no I/O and no Recorder state.

The load-bearing rule lives here: ``SystemExit`` must be classified by
its ``.code``, not merely by its type. argparse raises ``SystemExit`` for
both the ``--help`` / clean-exit path and usage errors, so treating every
``SystemExit`` as an error fabricated a large fake failure rate from
``--help`` runs (the bug this module fixes).
"""

from __future__ import annotations


def _classify_system_exit(exc: BaseException | None) -> tuple[str, int, str | None]:
    """Classify a ``SystemExit`` by its ``.code``, not merely its type.

    Clean exit (code ``None`` or ``0``) maps to ``"ok"`` / exit 0. A
    nonzero integer code maps to ``"error"`` with a ``SystemExitCode<N>``
    category, so a usage error (argparse exits ``2``) is distinguishable
    from a bare crash. A non-integer code (e.g. ``sys.exit("message")``)
    records the generic ``"SystemExit"`` category -- the message is never
    read, so it cannot leak into the local artifact (ADR 0035).
    """
    code = getattr(exc, "code", None)
    if code is None:
        return "ok", 0, None
    if isinstance(code, bool) or not isinstance(code, int):
        # Non-int (string message, object) or bool: never read the value.
        return "error", 1, "SystemExit"
    if code == 0:
        return "ok", 0, None
    if 1 <= code <= 255:
        return "error", code, f"SystemExitCode{code}"
    # Out-of-range int code: record a clamped error, generic category.
    return "error", 1, "SystemExit"


def classify_outcome(
    exc_type: type[BaseException] | None,
    exc: BaseException | None = None,
) -> tuple[str, int, str | None]:
    """Map an exit-time exception to ``(outcome, exit_code, error_kind)``.

    ``None`` -> ``"ok"`` / exit 0. ``KeyboardInterrupt`` and
    ``BrokenPipeError`` -> ``"interrupted"`` with exit codes 130 and 141
    (POSIX convention). ``SystemExit`` is classified by its ``.code`` via
    :func:`_classify_system_exit`. Any other exception is ``"error"`` /
    exit 1 with the exception class name as ``error_kind``.
    """
    if exc_type is None:
        return "ok", 0, None
    if issubclass(exc_type, KeyboardInterrupt):
        return "interrupted", 130, "KeyboardInterrupt"
    if issubclass(exc_type, BrokenPipeError):
        return "interrupted", 141, "BrokenPipeError"
    if issubclass(exc_type, SystemExit):
        return _classify_system_exit(exc)
    return "error", 1, exc_type.__name__
