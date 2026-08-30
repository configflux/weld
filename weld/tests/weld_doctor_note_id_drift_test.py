"""Static drift guard: every note id doctor can emit is ``--ack``-able.

``wd doctor`` prints a stable ``(id: <note-id>)`` beside each dismissible
finding and invites the reader to ``wd doctor --ack`` it. That invitation is
only honest if :func:`weld._doctor_suppressions._is_valid_note_id` accepts
the id: the allow-list exists to catch typos, so an id the doctor itself
just printed being refused is the allow-list rejecting the truth.

Which is what happened (bd 5038-ilefv): ``agent-graph-missing`` is emitted by
:mod:`weld._doctor_agent_graph` and was never added to ``VALID_NOTE_IDS``.
*How* it escaped is what this test is shaped around -- the id is passed to
``result_cls`` **positionally**, as the fourth argument, so it was invisible
to the ``grep note_id=`` habit that the comment above ``VALID_NOTE_IDS`` asks
maintainers to keep. A hand-maintained list guarded by a comment is not a
gate; this file is.

Static, not a runtime doctor sweep, because a doctor run only emits the notes
whose branch fires -- ``optional-*`` wants the dependency absent,
``mcp-config-missing`` wants no ``.mcp.json`` -- so no fixture set can prove
the *whole* emission surface. Drift is introduced at authoring time, so it is
caught at authoring time. The scan reads the modules materialised next to
``weld.__init__``: the Bazel runfiles copy under a sandboxed run, the real
tree under a bare ``python -m unittest``.

Scope is the *top level* of the package -- ``weld/*.py``, where every doctor
check lives -- and that boundary is load-bearing in both directions. The
target stages ``//weld:all_python_sources``, a ``glob(["*.py"])`` filegroup,
so under Bazel the scanned set provably *is* every top-level module rather
than only those a ``py_library`` happened to pull in: a new
``weld/_doctor_*.py`` cannot hide from this by being wired into some other
target. Descending further would break that guarantee instead of widening
it, since the subpackages are staged unevenly. A check placed in a
subpackage would therefore escape the sweep, which is what
``test_doctor_checks_stay_inside_the_scanned_scope`` fails on.

Four expression shapes can reach ``CheckResult.note_id``, and each has a rule:

* ``note_id="literal"`` -- an exact id;
* ``result_cls(level, message, section, "literal")`` -- also an exact id;
  position 3 *is* the ``note_id`` field of the ``CheckResult`` dataclass;
* ``note_id=f"family-{var}"`` -- a family prefix, which must be covered by
  ``_VALID_NOTE_ID_PREFIXES``;
* anything else (a bare name, a lookup, a call) -- *indirect*: the literals
  live elsewhere, so the file must be registered in
  ``_INDIRECT_NOTE_ID_SOURCES`` alongside the ids it can produce. A new
  indirect emission path fails this test until someone wires it in, which is
  the point of listing the shape at all.

Two residuals, stated rather than hidden. Registration is per file, so a file
already registered that grows a *second*, differently-sourced indirect path is
not re-flagged. And a scan rule that quietly stops matching would turn every
assertion here vacuously green -- which is what
``test_scan_finds_the_known_emission_sites`` is for.
"""

from __future__ import annotations

import ast
import unittest
from dataclasses import dataclass, field
from pathlib import Path

import weld
from weld._doctor_optional import _NOTE_ID_BY_DISPLAY
from weld._doctor_suppressions import (
    _VALID_NOTE_ID_PREFIXES,
    _is_valid_note_id,
)

_PACKAGE_DIR = Path(weld.__file__).resolve().parent

#: The package ships well over a hundred modules. A runfiles tree that
#: somehow carried none of them would make every assertion below vacuous.
_MIN_SCANNED_FILES = 30

#: Constructors whose positional argument 3 is the ``note_id`` field of
#: :class:`weld.doctor.CheckResult`. ``result_cls`` is the injected alias
#: every ``weld/_doctor_*.py`` check is handed instead of the class itself.
_RESULT_CTORS = frozenset({"CheckResult", "result_cls"})
_NOTE_ID_POSITION = 3

#: Files that *compute* a note id rather than spelling it, mapped to the ids
#: they can produce. ``weld/_doctor_optional.py`` looks its id up in
#: ``_NOTE_ID_BY_DISPLAY``, so that mapping's values are its emission set.
_INDIRECT_NOTE_ID_SOURCES: dict[str, frozenset[str]] = {
    "_doctor_optional.py": frozenset(_NOTE_ID_BY_DISPLAY.values()),
}

#: One id per literal-matching rule, so a rule that stops matching fails
#: loudly instead of silently narrowing the scan. ``mcp-config-missing`` is
#: a keyword literal; ``agent-graph-missing`` is the positional one whose
#: invisibility is the reason this test exists.
_SENTINEL_IDS = frozenset({"mcp-config-missing", "agent-graph-missing"})


@dataclass
class _Scan:
    """What the top-level AST sweep found, with provenance for messages."""

    #: exact note id -> ``weld/``-relative file that emits it
    exact: dict[str, str] = field(default_factory=dict)
    #: f-string family prefix -> ``weld/``-relative file that emits it
    prefixes: dict[str, str] = field(default_factory=dict)
    #: ``weld/``-relative files whose note id cannot be read statically
    indirect: set[str] = field(default_factory=set)
    files: int = 0


def _iter_source_files() -> list[Path]:
    """Top-level ``weld/*.py`` -- see the module docstring on scope.

    ``glob`` rather than ``rglob`` on purpose: this set is exactly what
    ``//weld:all_python_sources`` stages, so it is complete by construction
    under Bazel.
    """
    return [
        path
        for path in sorted(_PACKAGE_DIR.glob("*.py"))
        if not path.name.endswith("_test.py")
    ]


def _imported_weld_modules(tree: ast.AST) -> set[str]:
    """Dotted ``weld.*`` module names imported anywhere in ``tree``.

    Includes function-local imports -- ``weld.doctor`` defers
    ``weld._unclaimed_sources`` inside a helper, and a deferred check is
    exactly as much a doctor check as an eager one. Relative imports are
    resolved too, so ``from .strategies.x import y`` cannot slip a check
    below the top level while reading as depth 1; that assumes ``tree`` is
    a top-level ``weld/*.py``, which is the only way this is called.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = f"weld.{module}" if module else "weld"
            if module.split(".")[0] == "weld":
                names.add(module)
        elif isinstance(node, ast.Import):
            names.update(
                alias.name
                for alias in node.names
                if alias.name.split(".")[0] == "weld"
            )
    return names


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _note_id_expressions(tree: ast.AST) -> list[ast.expr]:
    """Every expression in ``tree`` that can land in ``note_id``."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        found.extend(kw.value for kw in node.keywords if kw.arg == "note_id")
        if (
            _callee_name(node.func) in _RESULT_CTORS
            and len(node.args) > _NOTE_ID_POSITION
        ):
            found.append(node.args[_NOTE_ID_POSITION])
    return found


def _scan_package() -> _Scan:
    scan = _Scan()
    for path in _iter_source_files():
        scan.files += 1
        rel = path.relative_to(_PACKAGE_DIR).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for expr in _note_id_expressions(tree):
            if isinstance(expr, ast.Constant):
                if expr.value is None:
                    # Deliberately not individually suppressible.
                    continue
                if isinstance(expr.value, str):
                    scan.exact.setdefault(expr.value, rel)
                    continue
            elif isinstance(expr, ast.JoinedStr):
                head = expr.values[0] if expr.values else None
                if isinstance(head, ast.Constant) and isinstance(
                    head.value, str
                ):
                    scan.prefixes.setdefault(head.value, rel)
                    continue
            scan.indirect.add(rel)
    return scan


class DoctorNoteIdDriftTest(unittest.TestCase):
    """``VALID_NOTE_IDS`` must cover what the doctor suite actually emits."""

    scan: _Scan

    @classmethod
    def setUpClass(cls) -> None:
        cls.scan = _scan_package()

    def test_every_emitted_note_id_is_ackable(self):
        for note_id, source in sorted(self.scan.exact.items()):
            with self.subTest(note_id=note_id):
                self.assertTrue(
                    _is_valid_note_id(note_id),
                    f"weld/{source} emits note id {note_id!r} and wd doctor "
                    f"prints it as ack-able, but _is_valid_note_id refuses "
                    f"it -- so `wd doctor --ack {note_id}` exits 2. Add it "
                    f"to weld/_doctor_suppressions.VALID_NOTE_IDS and to the "
                    f"README 'The valid note ids are ...' list.",
                )

    def test_every_note_id_family_is_registered(self):
        for prefix, source in sorted(self.scan.prefixes.items()):
            with self.subTest(prefix=prefix):
                self.assertTrue(
                    prefix.startswith(_VALID_NOTE_ID_PREFIXES),
                    f"weld/{source} mints note ids of the {prefix!r} family "
                    f"at runtime, but no entry in _VALID_NOTE_ID_PREFIXES "
                    f"covers that prefix, so none of them can be acked. "
                    f"Register the family prefix (exact ids cannot work "
                    f"here: the members are not knowable up front).",
                )

    def test_indirect_note_id_sources_are_registered(self):
        unregistered = sorted(
            self.scan.indirect - set(_INDIRECT_NOTE_ID_SOURCES)
        )
        self.assertEqual(
            unregistered,
            [],
            f"these files compute a note_id instead of spelling it, so this "
            f"scan cannot read the ids they emit: {unregistered}. Add each "
            f"to _INDIRECT_NOTE_ID_SOURCES in this file, mapped to the ids "
            f"it can produce, so they are checked against the allow-list.",
        )
        stale = sorted(set(_INDIRECT_NOTE_ID_SOURCES) - self.scan.indirect)
        self.assertEqual(
            stale,
            [],
            f"_INDIRECT_NOTE_ID_SOURCES registers {stale}, but they no "
            f"longer compute a note id -- the scan reads their ids "
            f"directly now. Drop the entries so the registry does not rot.",
        )

    def test_registered_indirect_note_ids_are_ackable(self):
        for source, ids in sorted(_INDIRECT_NOTE_ID_SOURCES.items()):
            self.assertTrue(ids, f"weld/{source} registered with no ids")
            for note_id in sorted(ids):
                with self.subTest(source=source, note_id=note_id):
                    self.assertTrue(
                        _is_valid_note_id(note_id),
                        f"weld/{source} can emit note id {note_id!r}, which "
                        f"_is_valid_note_id refuses. Add it to "
                        f"VALID_NOTE_IDS and to the README list.",
                    )

    def test_doctor_checks_stay_inside_the_scanned_scope(self):
        """Nothing ``weld.doctor`` pulls in may sit below the top level.

        The sweep reads ``weld/*.py`` only, so a check module living in a
        subpackage would emit note ids this file never sees -- a blind spot
        that reads as a pass. Fail on the move instead, and widen the scan
        deliberately if the move is the right one.
        """
        tree = ast.parse(
            (_PACKAGE_DIR / "doctor.py").read_text(encoding="utf-8")
        )
        below = sorted(
            name
            for name in _imported_weld_modules(tree)
            if name.count(".") > 1
        )
        self.assertEqual(
            below,
            [],
            f"weld/doctor.py imports {below} from below weld/*.py, which "
            f"this scan does not read -- any note id emitted there is "
            f"unchecked. Widen _iter_source_files to stage and read those "
            f"modules too, then update the scope note in the docstring.",
        )

    def test_scan_finds_the_known_emission_sites(self):
        """Guard the false green: a rule that stops matching fails here."""
        self.assertGreaterEqual(
            self.scan.files,
            _MIN_SCANNED_FILES,
            f"only {self.scan.files} weld modules were scanned -- the "
            f"package sources are missing from {_PACKAGE_DIR}, which would "
            f"make every other assertion in this file vacuously true.",
        )
        missing = sorted(_SENTINEL_IDS - set(self.scan.exact))
        self.assertEqual(
            missing,
            [],
            f"the scan no longer finds {missing}. Those ids are still "
            f"emitted, so a matching rule in _note_id_expressions has "
            f"stopped firing and this file is now checking less than it "
            f"claims -- fix the rule, do not relax the sentinel.",
        )
        self.assertTrue(
            self.scan.prefixes,
            "the scan found no f-string note id families, but "
            "weld/_unclaimed_sources.py emits one -- the JoinedStr rule "
            "has stopped firing.",
        )
        self.assertTrue(
            self.scan.indirect,
            "the scan found no indirect note ids, but "
            "weld/_doctor_optional.py computes one -- the fall-through "
            "rule has stopped firing.",
        )


if __name__ == "__main__":
    unittest.main()
