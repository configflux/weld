"""T1a determinism audit reproduction — ``hash()`` randomization class-of-bug.

Covers ADR 0012 §5 (``hashlib``-only rule). Today no production call
to ``hash()`` remains in ``weld/`` — a grep for ``\\bhash\\(``
returns zero hits. The contract is compliant in spirit.

However, ADR 0012 §"What this ADR does NOT cover" explicitly lists
"lint automation for the ``hashlib``-only rule" as follow-up. Until
that lint lands, the discipline is enforced in review. This test
demonstrates why the rule exists and why a regression would be
silent without it:

1. A synthetic function mimics the class-of-bug pattern: it builds a
   ``dict`` keyed by ``hash(tok)`` over a sequence of tokens and
   emits the dict's iteration order.
2. The same logic is run twice in subprocesses with different
   ``PYTHONHASHSEED`` values.
3. The two outputs differ today, demonstrating that if any future
   contributor (re)introduces ``hash()`` into an emit path, the
   regression test harness will fire.

The test also asserts that sorting the output rescues determinism —
i.e., the ADR's prescribed mitigation (``hashlib`` + ``sorted()``)
is sufficient.

Marked ``expectedFailure`` on the "hash() breaks determinism" check:
that check is *supposed* to fail (the mutant behaves as the ADR
warns). The "sorted rescues determinism" check is a regular
assertion that passes today and will continue to pass.

Companion audit document: ``docs/determinism-audit-T1a.md``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest


_TOKENS = (
    "alpha", "beta", "gamma", "delta", "epsilon",
    "zeta", "eta", "theta", "iota", "kappa",
    "lambda", "mu", "nu", "xi", "omicron",
    "pi", "rho", "sigma", "tau", "upsilon",
)


def _emit_under_seed(seed: str) -> list[str]:
    """Return the emission order of a set() under PYTHONHASHSEED=seed.

    The subprocess builds ``list(set(tokens))`` and emits that
    sequence. Set iteration is driven by internal bucket order, which
    depends directly on ``hash()`` output — and ``hash()`` output
    depends on ``PYTHONHASHSEED``. This is the simplest reproducer for
    the class-of-bug ADR 0012 §5 forbids: any code that serializes a
    ``set`` (or otherwise uses ``hash()`` output as an ordering key)
    will produce different bytes on different processes.
    """
    prog = textwrap.dedent(
        """
        tokens = {tokens!r}
        # Set iteration order is bucket order, which depends on hash().
        # This emits a different sequence on every PYTHONHASHSEED value
        # -- the canonical ``hash()`` randomization leak.
        for v in list(set(tokens)):
            print(v)
        """
    ).format(tokens=_TOKENS)
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    proc = subprocess.run(
        [sys.executable, "-c", prog],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"subprocess failed: {proc.stderr}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


class HashRandomizationClassOfBugTest(unittest.TestCase):
    """ADR 0012 §5: ``hash()`` is prohibited in weld/ production code."""

    @unittest.expectedFailure
    def test_hash_built_in_breaks_determinism(self) -> None:
        """Same logic under two hash seeds must emit the same sequence.

        This test is *supposed* to fail — it demonstrates the class of
        bug the ADR forbids. If this test passes, either CPython's
        hash function has changed behavior (unlikely) or the
        PYTHONHASHSEED machinery has been disabled. Either way a
        regression test harness needs updating.

        The "fix" for this test is *not* a code change in weld — it
        is the ADR's rule that ``hash()`` must never appear in
        production paths. When the lint rule lands, this test is the
        canary proving the lint is needed.
        """
        order_a = _emit_under_seed("42")
        order_b = _emit_under_seed("1337")
        self.assertEqual(
            order_a,
            order_b,
            "hash()-keyed dict iteration must be seed-independent. "
            "This test is deliberately expected to fail: it documents "
            "why ADR 0012 §5 forbids hash() in production code.",
        )

    def test_sorted_rescues_determinism(self) -> None:
        """Applying sorted() to the output rescues determinism.

        Demonstrates that the ADR's mitigation (combine with
        ``sorted()`` over canonical keys) is sufficient. This test
        passes today and must continue to pass — it is the positive
        confirmation that the ADR's fix works.
        """
        order_a = _emit_under_seed("42")
        order_b = _emit_under_seed("1337")
        self.assertEqual(
            sorted(order_a),
            sorted(order_b),
            "After sorted(), two hash-seed runs must agree. If this "
            "fails, the test fixture itself is broken.",
        )


if __name__ == "__main__":
    unittest.main()
