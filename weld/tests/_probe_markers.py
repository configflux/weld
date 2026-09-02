"""The red-probe marker contract, shared by every probe corpus in this tree.

A probe corpus lands its findings *red* -- one expected failure per finding,
each naming the finding id and the bd issue that owns its fix -- and the fix
flips the marker rather than the file going quiet (ADR 0141 D5, ADR 0142 D7,
docs/testing-hygiene.md "Fixing a field finding"). Two corpora now do this:
the field-eval rounds (``weld_field_eval_*_e2e_test``) and the Node/Next.js
readiness gaps (``weld_node_eval_*_e2e_test``).

These two functions are that contract's mechanism, and they live here rather
than in either corpus's harness because a second copy is exactly how the
second corpus comes to be policed less than the first: the inventory guards
read markers through :func:`finding_marker`, so a corpus whose decorator
stapled the reason somewhere slightly different would satisfy a guard that
never noticed. One decorator, one reader, both guards.

:mod:`weld.tests._field_eval_e2e_harness` re-exports both names, so the
existing field-eval call sites keep importing them from where they always did.
"""

from __future__ import annotations

import unittest


def expected_finding_failure(finding: str, bd_issue: str, summary: str):
    """Mark a probe as still reproducing *finding*; the fix task flips it.

    ``unittest.expectedFailure`` takes no reason, and a probe with no recorded
    reason is one nobody can tell from a test that was quietly given up on. So
    the finding id, its bd issue and a one-line summary are stapled to the
    method -- rendered into its docstring (visible in ``-v`` output) and left
    on ``__weld_expected_finding__`` for the structural guard that checks no
    probe is ever silenced instead of fixed.
    """

    def decorate(func):
        func.__weld_expected_finding__ = (finding, bd_issue, summary)
        doc = (func.__doc__ or "").rstrip()
        func.__doc__ = (
            f"{doc}\n\n    EXPECTED FAILURE until {finding} is fixed "
            f"({bd_issue}): {summary}\n    "
        )
        return unittest.expectedFailure(func)

    return decorate


def finding_marker(case: type[unittest.TestCase], name: str):
    """Return the ``(finding, bd_issue, summary)`` a test method carries, if any."""
    return getattr(getattr(case, name, None), "__weld_expected_finding__", None)
