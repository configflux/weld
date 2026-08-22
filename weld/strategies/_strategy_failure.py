"""Files -- and footprint-less source entries -- no strategy spoke for.

``discovery-state.json`` records ``files_with_no_nodes`` so the ADR 0008
per-file repair does not loop on a file whose strategy legitimately produces
nothing -- an empty ``__init__.py``, an issue store with no open issues. That
set was derived as the unconditional complement of the graph's anchors, which
records an *outcome* as an *intent*: a file whose strategy never ran, or ran
and failed on it, was written into the same exemption by the very run that
failed it. The exemption is keyed on the path alone, so a failure whose cause
is external to the file -- ``--safe`` refusing a project-local strategy, an
absent optional dependency, an ``external_json`` command that exited non-zero
-- never re-arms: only a content change re-dirties a file, and the content
never changes. The file stays absent from the graph while every freshness
signal reads clean.

This module is the channel that keeps the two apart (bd hch4). Whatever
discovers it cannot speak for a file notes the path here; the orchestrator
drains the set at the end of the run and records it as
``DiscoveryState.files_with_failed_strategy``, which exempts the vouching
audit -- so a permanently degraded environment keeps its incremental basis --
but not the per-file repair, so the next capable pass re-runs the file.

Two kinds of producer, one channel:

* the orchestrator's ``run_source``, when the strategy could not be loaded or
  the ``external_json`` adapter refused: no strategy looked at that source's
  files at all;
* a strategy, for the individual files it was handed and could not process
  (``python_module`` on a ``SyntaxError``).

A source entry configured with no ``glob``/``path``/``files`` key at all --
a command-only ``external_json`` adapter is the shipped example -- resolves
to an empty file list, so every one of the calls above becomes a no-op for
it: there is no path to note (bd um00). The second channel below closes that
gap, keyed by entry identity instead of by path, and read the same way: not
exempt from the incremental repair, so a fixed command is retried on the
next pass, but exempt from nothing that would cost the run its basis.

Both channels live in the **strategies** package for the reason
:mod:`weld.strategies._incremental_hint` does: runtime depends on strategies,
never the reverse, and both layers reference these keys.
"""

from __future__ import annotations

from typing import Iterable

#: Reserved ``context`` key accumulating repo-relative paths this run could
#: not speak for, sibling to
#: :data:`weld.strategies._incremental_hint.INCREMENTAL_HINT_KEY`.
STRATEGY_FAILURE_KEY = "_strategy_failed_files"

#: Reserved ``context`` key accumulating footprint-less source entries this
#: run could not speak for, keyed by
#: :func:`weld._discover_basis.entry_fingerprint` (bd um00).
SOURCE_FAILURE_KEY = "_strategy_failed_entries"

#: Closed vocabulary for the ``kind`` a source-entry failure is recorded
#: under. One per distinct branch in ``run_external_json`` /  ``run_source``
#: that reaches :func:`note_source_failure` -- precise rather than coarse, so
#: a report can group by failure class without re-parsing ``reason``.
KIND_MISSING_COMMAND = "missing_command"
KIND_SAFE_MODE_SKIPPED = "safe_mode_skipped"
KIND_BAD_COMMAND_STRING = "bad_command_string"
KIND_COMMAND_NOT_FOUND = "command_not_found"
KIND_TIMEOUT = "timeout"
KIND_NONZERO_EXIT = "nonzero_exit"
KIND_INVALID_JSON = "invalid_json"
KIND_INVALID_OUTPUT_SHAPE = "invalid_output_shape"
KIND_VALIDATION_FAILED = "validation_failed"
KIND_STRATEGY_UNAVAILABLE = "strategy_unavailable"

#: Cap on the recorded ``reason`` string. Mirrors the stderr snippet
#: convention in ``weld._discover_strategies.run_external_json``
#: (``proc.stderr[:200]``) -- long enough to identify the failure, short
#: enough that a chatty command or a hostile ``discover.yaml`` cannot use
#: state as an unbounded sink.
_MAX_SOURCE_REASON = 200


def note_strategy_failure(context: object, files: Iterable[str]) -> None:
    """Record *files* as repo-relative paths no strategy spoke for.

    Additive and idempotent: one run may fail a source at load time and a
    second entry may point another strategy at the same path, and reporting a
    file twice must mean exactly what reporting it once means.

    Does nothing when *context* is not a dict. A strategy invoked directly --
    by a test, or by any caller that keeps no shared context -- must not fail
    on bookkeeping that only the orchestrator consumes.
    """
    if not isinstance(context, dict):
        return
    bucket = context.get(STRATEGY_FAILURE_KEY)
    if not isinstance(bucket, set):
        bucket = set()
        context[STRATEGY_FAILURE_KEY] = bucket
    bucket.update(str(f) for f in files)


def drain_strategy_failures(context: object) -> set[str]:
    """Remove and return the paths noted in *context*; ``set()`` if none.

    Drained rather than read so that what the state records is what *this*
    pass found. Nothing is lost by forgetting: a file recorded as failed is
    exempt from nothing that would keep it out of the next pass's repair set,
    so it is dirty on that pass, so its source re-runs and re-reports it for
    as long as the failure lasts.
    """
    if not isinstance(context, dict):
        return set()
    bucket = context.pop(STRATEGY_FAILURE_KEY, None)
    return set(bucket) if isinstance(bucket, set) else set()


def note_source_failure(
    context: object, entry_id: str, *, kind: str, reason: str = "",
) -> None:
    """Record *entry_id* as a footprint-less source entry no strategy spoke for.

    Entry-keyed sibling of :func:`note_strategy_failure`, for the class of
    entry that function is structurally a no-op for: one with no
    ``glob``/``path``/``files`` key, so *files* there is always empty (bd
    um00). *entry_id* is a :func:`weld._discover_basis.entry_fingerprint`,
    computed by the caller -- this module stays free of the runtime-layer
    import that would require.

    *reason* is bounded to :data:`_MAX_SOURCE_REASON` and stored verbatim,
    never full command output, so a chatty or hostile command cannot inflate
    state.

    Overwrites rather than accumulates per *entry_id*: a second call in the
    same run means the entry was attempted again and failed again, and the
    latest attempt is what the next pass should describe.

    Does nothing when *context* is not a dict, for the same reason
    :func:`note_strategy_failure` does not: a strategy or adapter invoked
    directly must not fail on bookkeeping only the orchestrator consumes.
    """
    if not isinstance(context, dict):
        return
    bucket = context.get(SOURCE_FAILURE_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
        context[SOURCE_FAILURE_KEY] = bucket
    bucket[str(entry_id)] = {
        "kind": str(kind),
        "reason": str(reason)[:_MAX_SOURCE_REASON],
    }


def drain_source_failures(context: object) -> dict[str, dict]:
    """Remove and return the entry failures noted in *context*; ``{}`` if none.

    Drained for the same reason :func:`drain_strategy_failures` is: what the
    state records is what *this* pass found, so an entry that stops failing
    is not carried forward as still-failed.
    """
    if not isinstance(context, dict):
        return {}
    bucket = context.pop(SOURCE_FAILURE_KEY, None)
    return dict(bucket) if isinstance(bucket, dict) else {}


__all__ = [
    "KIND_BAD_COMMAND_STRING",
    "KIND_COMMAND_NOT_FOUND",
    "KIND_INVALID_JSON",
    "KIND_INVALID_OUTPUT_SHAPE",
    "KIND_MISSING_COMMAND",
    "KIND_NONZERO_EXIT",
    "KIND_SAFE_MODE_SKIPPED",
    "KIND_STRATEGY_UNAVAILABLE",
    "KIND_TIMEOUT",
    "KIND_VALIDATION_FAILED",
    "SOURCE_FAILURE_KEY",
    "STRATEGY_FAILURE_KEY",
    "drain_source_failures",
    "drain_strategy_failures",
    "note_source_failure",
    "note_strategy_failure",
]
