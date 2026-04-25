"""CLI smoke tests for `wd doctor --security` and `wd security` (ADR 0025).

Verifies:
- `wd security` is a thin alias for `wd doctor --security`.
- `--json` emits valid JSON conforming to the trust-posture schema.
- `wd doctor` (no flag) prints a pointer line that mentions the security
  view when high signals fire (ADR 0025: "wd doctor integrates or points
  to the security view").
- Exit code is non-zero when high signals fire.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from weld import doctor as doctor_mod
from weld import security as security_mod


def _write_minimal_workspace(root: Path, *, risky: bool = False) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    lines = ["sources:"]
    lines.append('  - glob: "src/**/*.py"')
    lines.append("    type: file")
    lines.append("    strategy: python_module")
    if risky:
        lines.append('  - glob: "ext/**/*.json"')
        lines.append("    type: file")
        lines.append("    strategy: external_json")
        lines.append('    command: "/usr/bin/echo hi"')
    (weld_dir / "discover.yaml").write_text("\n".join(lines) + "\n")
    (weld_dir / "graph.json").write_text(
        '{"meta":{"schema_version":4},"nodes":{},"edges":[]}'
    )
    if risky:
        (weld_dir / "strategies").mkdir()
        (weld_dir / "strategies" / "custom.py").write_text("# x\n")


class SecurityAliasTest(unittest.TestCase):
    """`wd security` and `wd doctor --security` produce equivalent output."""

    def test_alias_parity_human(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_minimal_workspace(root)

            buf_a = io.StringIO()
            with redirect_stdout(buf_a):
                rc_a = security_mod.main(["--root", str(root)])

            buf_b = io.StringIO()
            with redirect_stdout(buf_b):
                rc_b = doctor_mod.main(["--root", str(root), "--security"])

            self.assertEqual(rc_a, rc_b)
            self.assertEqual(buf_a.getvalue(), buf_b.getvalue())

    def test_alias_parity_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_minimal_workspace(root)

            buf_a = io.StringIO()
            with redirect_stdout(buf_a):
                rc_a = security_mod.main(["--root", str(root), "--json"])

            buf_b = io.StringIO()
            with redirect_stdout(buf_b):
                rc_b = doctor_mod.main(
                    ["--root", str(root), "--security", "--json"]
                )

            self.assertEqual(rc_a, rc_b)
            self.assertEqual(buf_a.getvalue(), buf_b.getvalue())


class SecurityJsonTest(unittest.TestCase):
    """`wd security --json` emits a parseable trust-posture document."""

    def test_json_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_minimal_workspace(root)

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = security_mod.main(["--root", str(root), "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("risk", payload)
            self.assertIn("signals", payload)
            self.assertIn("recommendations", payload)


class HighRiskExitCodeTest(unittest.TestCase):
    """Risky workspaces exit non-zero from the security surface."""

    def test_high_risk_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_minimal_workspace(root, risky=True)

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = security_mod.main(["--root", str(root)])
            self.assertEqual(rc, 1)
            output = buf.getvalue()
            self.assertIn("high", output.lower())


class DoctorPointsToSecurityViewTest(unittest.TestCase):
    """`wd doctor` (no flag) points at the security view on high-risk repos."""

    def test_doctor_no_flag_points_to_security_on_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_minimal_workspace(root, risky=True)

            buf = io.StringIO()
            with redirect_stdout(buf):
                doctor_mod.main(["--root", str(root)])
            output = buf.getvalue()
            self.assertIn("wd security", output)


if __name__ == "__main__":
    unittest.main()
