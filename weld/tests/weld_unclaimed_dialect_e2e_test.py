"""A wired glob that misses a dialect is a gap, as a probe through the real CLI.

The upgrade path for every existing Node user, verified end to end: a repo of
``src/a.ts``, ``src/p.tsx`` and ``src/legacy.js`` whose hand-written
``discover.yaml`` wires one entry on ``**/*.ts``. Two of the three files are
invisible to the graph, and before ADR 0144 every diagnostic said the config
was fine -- ``wd doctor`` reported nothing, ``wd prime`` reported nothing, and
``wd init --refresh`` answered "discover.yaml is already current: every
language present on disk has a wired strategy". The old check compared
languages on disk against the flat set of ``strategy:`` *names* the config
mentions, so a glob that matches none of a language's files still claimed it,
and ``.tsx`` is typescript to ``EXT_TO_LANG`` exactly as ``.ts`` is.

This module landed **red on purpose** (bd 5038-wqea5) and was flipped by the
fix, not by editing it. It drives the CLI in a subprocess because every surface
the bug reached is a command: the doctor row, the prime line, the refresh
merge, and the config that merge writes. An in-process call to
``detect_unclaimed_source_classes`` would have agreed with itself.

Four repos, because the change has to add signal *and* no noise:

* ``repro`` -- the issue's repo. Both gaps reported, with the *unclaimed* count
  (one typescript file, not the two the language has), by doctor and by prime.
* ``refresh`` -- the same repo again, so the merge runs on an unasserted copy.
  ``--refresh`` must wire the dialect-family glob and the JavaScript entry,
  keep the hand-written entry, and leave doctor silent afterwards: a warning
  its own remedy cannot close is worse than the silence it replaced.
* ``control`` -- a config that genuinely claims its languages. Silent.
* ``scaffold`` -- an enabled entry that matches nothing today (``**/*.test.ts``
  in a repo with no tests) beside one that does. Silent: the claim is a matched
  file, so an inert entry neither claims nor accuses, and a language with no
  files on disk is never reported at all.

The CLI runner is local rather than imported from the Node corpus harness: the
fixture here is three files and a hand-written config, and that harness carries
the npm-workspaces monorepo and hard-fails without the tree-sitter grammars --
neither of which this probe parses a byte with. It is the third such runner
(``_field_eval_e2e_harness``, ``_node_eval_e2e_harness``) and follows their
environment pinning: fixed hash seed, locale and TZ, ``HOME`` inside the
tempdir so no ambient config is read, telemetry off, and ``WELD_AUTO_REFRESH=0``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

#: Every ``wd`` invocation is bounded. Generous: the assertion is never about
#: how long a command took, only that it terminated.
CLI_TIMEOUT_SECONDS = 120

#: The issue's repro tree. One file per class the bug hides: a claimed source
#: file, its dialect sibling, and a language of its own.
REPRO_FILES: dict[str, str] = {
    "src/a.ts": "export const a = 1;\n",
    "src/p.tsx": "export default function P() { return null; }\n",
    "src/legacy.js": "module.exports = { legacy: true };\n",
}

#: One entry, wired by hand, exactly as a pre-0.25 `wd init` would have left a
#: TypeScript repo. It claims `a.ts` and nothing else.
HAND_WRITTEN_CONFIG = """# generated-by: weld 0.20.0
sources:
  - glob: "**/*.ts"
    type: file
    strategy: tree_sitter
    language: typescript
"""

#: What a claiming config looks like: the dialect *families*, so every file
#: `EXT_TO_LANG` counts is a file some entry matches (ADR 0142 D1).
CLAIMING_CONFIG = """# generated-by: weld 0.25.0
sources:
  - glob: "**/*.{ts,tsx}"
    type: file
    strategy: tree_sitter
    language: typescript
  - glob: "**/*.{js,jsx,mjs,cjs}"
    type: file
    strategy: tree_sitter
    language: javascript
"""

#: A claiming config plus a scaffolded entry that matches nothing in this repo.
#: The noise case: an inert entry must not start warning, and must not stop the
#: entry beside it from claiming.
SCAFFOLD_CONFIG = CLAIMING_CONFIG + """  - glob: "**/*.test.ts"
    type: file
    strategy: tree_sitter
    language: typescript
"""

#: An empty graph so `wd doctor` grades the config rather than a missing file:
#: the unclaimed row is a `warn`, and a warn must never raise the exit code.
EMPTY_GRAPH = '{"meta":{"schema_version":4},"nodes":{},"edges":[]}'

#: The doctor row ids the check emits. Formatting-independent, and the string a
#: user copies into `wd doctor --ack`.
TS_NOTE_ID = "(id: unclaimed-source-typescript)"
JS_NOTE_ID = "(id: unclaimed-source-javascript)"


def _weld_import_root() -> str:
    """Directory to put on ``PYTHONPATH`` so ``-m weld`` finds this checkout.

    Under Bazel that is the runfiles tree; from a plain source checkout it is
    the repo root. Both are ``weld/tests/<this file>`` minus three components
    -- the resolved form is tried second because a runfiles entry is a symlink
    into the source tree and only one of the two spellings has ``weld/`` under
    it in every layout.
    """
    here = Path(__file__).absolute()
    for candidate in (here.parents[2], here.resolve().parents[2]):
        if (candidate / "weld" / "__main__.py").is_file():
            return str(candidate)
    raise RuntimeError(  # pragma: no cover - a broken runfiles tree
        f"cannot locate weld/__main__.py above {here}"
    )


def _cli_env(home: Path) -> dict[str, str]:
    """The fixed environment every ``wd`` subprocess in this module runs under."""
    inherited = [
        os.path.abspath(entry)
        for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if entry
    ]
    return {
        "PATH": "/usr/bin:/usr/local/bin:/bin",
        "HOME": str(home),
        "PYTHONPATH": os.pathsep.join([_weld_import_root(), *inherited]),
        "PYTHONPYCACHEPREFIX": str(home / "pycache"),
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "WELD_TELEMETRY": "off",
        "WELD_AUTO_REFRESH": "0",
        "WELD_SOURCE_CHECKOUT_NOTICE": "off",
    }


class _Repo:
    """A materialised repo plus a real ``wd`` runner bound to it."""

    def __init__(self, base: Path, name: str, config: str, files: dict[str, str]):
        self.root = base / name
        home = base / f"home-{name}"
        home.mkdir(parents=True, exist_ok=True)
        self.env = _cli_env(home)
        (self.root / ".weld").mkdir(parents=True)
        (self.root / ".weld" / "discover.yaml").write_text(config, encoding="utf-8")
        (self.root / ".weld" / "graph.json").write_text(EMPTY_GRAPH, encoding="utf-8")
        for rel, body in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

    def wd(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "weld", *args],
            cwd=str(self.root),
            env=self.env,
            capture_output=True,
            text=True,
            input="",
            timeout=CLI_TIMEOUT_SECONDS,
        )

    def output(self, *args: str) -> str:
        """``wd <args>`` stdout+stderr, failing loudly on a non-zero exit."""
        proc = self.wd(*args)
        if proc.returncode != 0:
            raise AssertionError(
                f"`wd {' '.join(args)}` failed (rc={proc.returncode}):"
                f"\n{proc.stdout}\n{proc.stderr}"
            )
        return f"{proc.stdout}\n{proc.stderr}"

    def config_text(self) -> str:
        return (self.root / ".weld" / "discover.yaml").read_text(encoding="utf-8")


_TMP: tempfile.TemporaryDirectory | None = None
_OUT: dict[str, str] = {}
_CONFIG_AFTER_REFRESH = ""


def _assert_cli_is_this_checkout(repo: _Repo) -> None:
    """The subprocess must import weld from the tree under test.

    A ``python -m weld`` that resolves to some *other* installed weld runs
    green and proves nothing about this branch.
    """
    proc = subprocess.run(
        [sys.executable, "-c", "import weld; print(weld.__file__)"],
        cwd=str(repo.root),
        env=repo.env,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )
    expected = (Path(_weld_import_root()) / "weld").resolve()
    loaded = Path(proc.stdout.strip() or "/nonexistent").parent.resolve()
    if proc.returncode != 0 or loaded != expected:
        raise AssertionError(
            f"the CLI subprocess imports weld from {loaded}, not the tree under "
            f"test ({expected}); rc={proc.returncode}\n{proc.stderr}"
        )


def setUpModule() -> None:
    """Materialise the four repos and run every ``wd`` command once."""
    global _TMP, _CONFIG_AFTER_REFRESH
    _TMP = tempfile.TemporaryDirectory()
    base = Path(_TMP.name)

    repro = _Repo(base, "repro", HAND_WRITTEN_CONFIG, REPRO_FILES)
    _assert_cli_is_this_checkout(repro)
    _OUT["repro_doctor"] = repro.output("doctor")
    _OUT["repro_prime"] = repro.output("prime")

    # A second copy, so the merge runs on a tree no other probe asserted on.
    refreshed = _Repo(base, "refresh", HAND_WRITTEN_CONFIG, REPRO_FILES)
    _OUT["refresh"] = refreshed.output("init", "--refresh")
    _CONFIG_AFTER_REFRESH = refreshed.config_text()
    _OUT["refresh_doctor"] = refreshed.output("doctor")

    control = _Repo(base, "control", CLAIMING_CONFIG, REPRO_FILES)
    _OUT["control_doctor"] = control.output("doctor")
    _OUT["control_prime"] = control.output("prime")

    scaffold = _Repo(base, "scaffold", SCAFFOLD_CONFIG, {"src/a.ts": "export const a = 1;\n"})
    _OUT["scaffold_doctor"] = scaffold.output("doctor")


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


class UnclaimedDialectReportedTest(unittest.TestCase):
    """The repro repo: both gaps are reported, by both surfaces."""

    def test_doctor_reports_the_unclaimed_dialect_and_the_unclaimed_language(self):
        out = _OUT["repro_doctor"]
        self.assertIn(TS_NOTE_ID, out, out)
        self.assertIn(JS_NOTE_ID, out, out)

    def test_doctor_counts_the_unclaimed_files_not_the_language_total(self):
        # The repo has two typescript files; one of them (`a.ts`) is claimed.
        # Reporting `2` would mean the count is still the language total, which
        # is the reading that let a claimed `.ts` speak for an unread `.tsx`.
        out = _OUT["repro_doctor"]
        self.assertIn("1 typescript file", out, out)
        self.assertNotIn("2 typescript files", out, out)
        self.assertIn("1 javascript file", out, out)

    def test_doctor_offers_the_non_destructive_remedy_first(self):
        out = _OUT["repro_doctor"]
        rows = [ln for ln in out.splitlines() if TS_NOTE_ID in ln]
        self.assertEqual(len(rows), 1, out)
        line = rows[0]
        self.assertIn("wd init --refresh", line)
        self.assertIn("wd init --force", line)
        self.assertLess(
            line.index("wd init --refresh"), line.index("wd init --force"), line,
        )

    def test_doctor_stays_exit_zero(self):
        # `output` raises on a non-zero exit, so reaching this assertion is the
        # proof; it is spelled out because "a warn never fails doctor" is the
        # contract automation keys on (ADR 0135).
        self.assertIn("Status:", _OUT["repro_doctor"])

    def test_prime_reports_both_and_offers_refresh_as_the_next_step(self):
        out = _OUT["repro_prime"]
        self.assertIn("1 typescript file", out, out)
        self.assertIn("1 javascript file", out, out)
        _, _, next_steps = out.partition("Next steps:")
        self.assertIn("wd init --refresh", next_steps, out)


class RefreshClosesTheGapTest(unittest.TestCase):
    """`wd init --refresh` wires what is reported and keeps what is written."""

    def test_refresh_reports_wiring_both_languages(self):
        out = _OUT["refresh"]
        self.assertNotIn("already current", out, out)
        self.assertIn("typescript", out, out)
        self.assertIn("javascript", out, out)

    def test_refresh_names_each_language_with_its_file_count(self):
        # One unread `.tsx` beside a wired `**/*.ts` is the ordinary shape now
        # that a claim is per file class, so the singular has to read right --
        # it said "(1 files)" while only a whole unwired language could be
        # reported. `(1 file)` is not a substring of `(1 files)`.
        self.assertIn("wired typescript (1 file)", _OUT["refresh"])
        self.assertIn("wired javascript (1 file)", _OUT["refresh"])

    def test_refresh_wires_the_dialect_family_and_the_javascript_entry(self):
        self.assertIn('glob: "**/*.{ts,tsx}"', _CONFIG_AFTER_REFRESH)
        self.assertIn('glob: "**/*.{js,jsx,mjs,cjs}"', _CONFIG_AFTER_REFRESH)

    def test_refresh_keeps_the_hand_written_entry(self):
        self.assertIn('- glob: "**/*.ts"', _CONFIG_AFTER_REFRESH)
        self.assertIn("language: typescript", _CONFIG_AFTER_REFRESH)

    def test_doctor_is_silent_after_the_refresh(self):
        # The remedy must be able to close what the warning opens, or the
        # warning is permanent noise (ADR 0144).
        self.assertNotIn("unclaimed-source-", _OUT["refresh_doctor"])


class NoNewNoiseTest(unittest.TestCase):
    """A claiming config, and an inert entry beside one, both stay silent."""

    def test_control_repo_is_silent(self):
        self.assertNotIn("unclaimed-source-", _OUT["control_doctor"])
        self.assertNotIn("no wired strategy", _OUT["control_prime"])

    def test_scaffolded_glob_that_matches_nothing_does_not_warn(self):
        # `**/*.test.ts` matches no file in this repo, and there is no
        # JavaScript on disk at all: neither is a gap.
        self.assertNotIn("unclaimed-source-", _OUT["scaffold_doctor"])


if __name__ == "__main__":
    unittest.main()
