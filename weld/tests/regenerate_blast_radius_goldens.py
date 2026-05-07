"""One-shot regenerator for blast-radius fixture goldens.

Wraps :func:`run_one_fixture` with the regen flag so operators can
rebuild the goldens after an intentional schema change without
needing to set ``REGEN_BLAST_RADIUS_GOLDENS`` by hand or fight the
Bazel-test sandbox semantics. Wired as ``py_binary`` in the test
package's BUILD file under the rule name
``regenerate_blast_radius_goldens``::

    bazel run //weld/tests:regenerate_blast_radius_goldens

Prints one ``[blast-radius] regenerating: <name>`` line per fixture
to stderr, plus a final summary. Exits 0 on success, 1 if no
fixture directories exist (which usually means the harness is
mis-wired in BUILD or the fixture root was renamed).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld.tests._blast_radius_harness import (  # noqa: E402
    discover_fixture_names,
    fixtures_dir,
    regen_env_var,
)
from weld.tests.weld_blast_radius_fixtures_test import (  # noqa: E402
    run_one_fixture,
)


def main() -> int:
    os.environ[regen_env_var()] = "1"
    names = discover_fixture_names()
    if not names:
        print(
            f"[blast-radius] no fixtures found under {fixtures_dir()}",
            file=sys.stderr,
        )
        return 1
    case = unittest.TestCase()
    for name in names:
        print(f"[blast-radius] regenerating: {name}", file=sys.stderr)
        run_one_fixture(name, case, regen=True)
    print(
        f"[blast-radius] regenerated {len(names)} fixture(s).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
