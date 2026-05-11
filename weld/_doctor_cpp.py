"""``wd doctor --cpp`` helpers for the libclang Wave 3 path (ADR 0057).

The ``wd doctor --cpp`` flag surfaces three things:

1. Whether the ``[cpp-libclang]`` extra is importable.
2. Whether ``compile_commands.json`` is present.
3. Coverage: how many ``.cpp``/``.cc`` files in the repo are covered
   by the database, and how many are not.

The split coverage number is the headline: a compile-database that
covers only half of a project tells the user libclang Wave 3 will be
dormant on the uncovered files (tree-sitter still runs there). The
report is read-only and the only filesystem access is the database
read itself.

Factored out of :mod:`weld.doctor` to keep both files under the
400-line cap. The CLI entry point in ``weld.doctor`` dispatches to
:func:`check_cpp` when ``--cpp`` is passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weld.strategies._cpp_libclang_db import (
    ENABLE_ENV_VAR,
    covered_files,
    env_enabled,
    find_compile_db,
    is_libclang_available,
    parse_entries,
)
from weld.strategies._cpp_header_pairing import PAIRABLE_SOURCE_EXTS


@dataclass(frozen=True)
class CppCoverageReport:
    """A summary of the C++ libclang readiness at a given root.

    Attributes:
        libclang_available: True when ``clang.cindex`` imports cleanly.
        env_enabled: True when ``WELD_CPP_LIBCLANG=1`` is set.
        db_path: Repo-relative path to the database when found, else None.
        db_entries: Number of well-formed entries in the database.
        covered_count: How many ``.cpp``/``.cc`` files the database
            references.
        on_disk_count: How many ``.cpp``/``.cc`` files weld's discovery
            walker would otherwise see in the repo.
    """

    libclang_available: bool
    env_enabled: bool
    db_path: str | None
    db_entries: int
    covered_count: int
    on_disk_count: int

    @property
    def db_present(self) -> bool:
        return self.db_path is not None

    @property
    def uncovered_count(self) -> int:
        return max(0, self.on_disk_count - self.covered_count)


def check_cpp(root: Path, result_cls: type) -> list:
    """Run the ``--cpp`` doctor section and return result rows.

    *result_cls* is the ``CheckResult`` shape used by :mod:`weld.doctor`.
    We accept it as a parameter so this module has no circular import
    on the doctor entry point.
    """
    report = compute_report(root)
    return _format(report, result_cls)


def compute_report(root: Path) -> CppCoverageReport:
    """Build a :class:`CppCoverageReport` for *root*."""
    db_path_obj = find_compile_db(root)
    if db_path_obj is not None:
        try:
            rel = db_path_obj.relative_to(root).as_posix()
        except ValueError:
            rel = db_path_obj.name
        entries = parse_entries(db_path_obj, root=root)
    else:
        rel = None
        entries = []
    on_disk = _count_cpp_sources_on_disk(root)
    return CppCoverageReport(
        libclang_available=is_libclang_available(),
        env_enabled=env_enabled(),
        db_path=rel,
        db_entries=len(entries),
        covered_count=len(covered_files(entries)),
        on_disk_count=on_disk,
    )


def _count_cpp_sources_on_disk(root: Path) -> int:
    """Count ``.cpp``/``.cc``/``.cxx`` / etc. files reachable from *root*.

    Walks the tree once; respects the shared exclusion policy used by
    every other strategy via the ``_helpers`` filter. We rglob for the
    superset extensions and dedupe by path so a single file is never
    counted twice.
    """
    # Bounded to a generous cap so a giant generated mirror does not
    # turn the doctor into a long-running scan.
    cap = 200_000
    paths: set[Path] = set()
    for ext in PAIRABLE_SOURCE_EXTS:
        if len(paths) >= cap:
            break
        try:
            for candidate in root.rglob(f"*{ext}"):
                if not candidate.is_file():
                    continue
                # Skip anything inside excluded directory roots
                # (``.git``, ``node_modules``, etc.) via the shared
                # filter.
                if _is_excluded(candidate, root):
                    continue
                paths.add(candidate)
                if len(paths) >= cap:
                    break
        except OSError:
            continue
    return len(paths)


def _is_excluded(path: Path, root: Path) -> bool:
    """Return True when *path* lives inside an excluded directory."""
    from weld.strategies._helpers import is_excluded_dir_name

    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    for part in rel.parts[:-1]:
        if is_excluded_dir_name(part):
            return True
    return False


def _format(report: CppCoverageReport, result_cls: type) -> list:
    """Format a :class:`CppCoverageReport` into a list of ``CheckResult``."""
    results: list = []

    if report.libclang_available:
        results.append(
            result_cls("ok", "libclang extra installed", "C++ libclang"),
        )
    else:
        results.append(
            result_cls(
                "note",
                "libclang extra not installed -- "
                "pip install 'configflux-weld[cpp-libclang]'",
                "C++ libclang",
            ),
        )

    if report.env_enabled:
        results.append(
            result_cls(
                "ok",
                f"{ENABLE_ENV_VAR}=1 (libclang strategy opt-in active)",
                "C++ libclang",
            ),
        )
    else:
        results.append(
            result_cls(
                "note",
                f"{ENABLE_ENV_VAR} not set -- libclang strategy stays dormant",
                "C++ libclang",
            ),
        )

    if report.db_present:
        results.append(
            result_cls(
                "ok",
                f"compile_commands.json found at {report.db_path} "
                f"({report.db_entries} entries)",
                "C++ libclang",
            ),
        )
    else:
        results.append(
            result_cls(
                "note",
                "compile_commands.json not found -- "
                "generate with cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON "
                "or wd discover --emit-compile-db-stub",
                "C++ libclang",
            ),
        )

    if report.on_disk_count > 0:
        if report.covered_count == 0:
            results.append(
                result_cls(
                    "note",
                    f"compile-db coverage: 0/{report.on_disk_count} "
                    "C++ source files (libclang would be dormant)",
                    "C++ libclang",
                ),
            )
        elif report.uncovered_count == 0:
            results.append(
                result_cls(
                    "ok",
                    f"compile-db coverage: {report.covered_count}/"
                    f"{report.on_disk_count} C++ source files (full)",
                    "C++ libclang",
                ),
            )
        else:
            results.append(
                result_cls(
                    "warn",
                    f"compile-db coverage: {report.covered_count}/"
                    f"{report.on_disk_count} C++ source files "
                    f"({report.uncovered_count} uncovered fall back to "
                    "tree-sitter)",
                    "C++ libclang",
                ),
            )
    return results


#: The placeholder ``compile_commands.json`` content the ``wd discover
#: --emit-compile-db-stub`` flag writes. It is a *valid* JSON document
#: (an empty array) plus a sibling README documenting how to generate
#: a real database. The empty-array form is intentional: parsing it
#: with :func:`parse_entries` returns no entries so the libclang
#: strategy stays dormant until the user regenerates it.
COMPILE_DB_STUB_BODY: str = "[]\n"

#: Sibling README written next to the stub. We put the documentation
#: in a separate file because ``compile_commands.json`` itself must
#: stay machine-readable.
COMPILE_DB_STUB_README: str = """\
# compile_commands.json (placeholder)

This file is a placeholder written by `wd discover --emit-compile-db-stub`
(ADR 0057 Wave 3). It does NOT enable the libclang strategy on its
own -- it is just here to document how to generate a real database.

## Generating a real compile_commands.json

### CMake
Pass `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` at configure time:

```bash
cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
ln -sf build/compile_commands.json compile_commands.json
```

### Bazel
Use Hedron's compile-commands extractor:

```bash
bazel run @hedron_compile_commands//:refresh_all
```

### Make
Use `bear` to intercept the build:

```bash
bear -- make
```

## Activating the libclang strategy

Once a real database exists, enable the strategy with:

```bash
pip install 'configflux-weld[cpp-libclang]'
WELD_CPP_LIBCLANG=1 wd discover --output .weld/graph.json
```

Run `wd doctor --cpp` to verify coverage.
"""

STUB_README_FILENAME: str = "compile_commands.README.md"


def emit_compile_db_stub(root: Path) -> tuple[Path, Path]:
    """Write the placeholder ``compile_commands.json`` + README under *root*.

    Returns the ``(json_path, readme_path)`` pair. Refuses to overwrite
    an existing well-formed database (i.e. a file whose body is *not*
    the empty array we wrote); the README is rewritten unconditionally
    because we own its content.

    Raises ``FileExistsError`` when the json path already contains
    something non-trivial so callers can choose to fail loudly.
    """
    json_path = root / "compile_commands.json"
    readme_path = root / STUB_README_FILENAME
    if json_path.is_file():
        try:
            existing = json_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            existing = ""
        if existing.strip() not in ("", "[]"):
            raise FileExistsError(
                f"{json_path.name} already exists and is non-empty; "
                "refusing to overwrite. Remove it first if you want a stub.",
            )
    json_path.write_text(COMPILE_DB_STUB_BODY, encoding="utf-8")
    readme_path.write_text(COMPILE_DB_STUB_README, encoding="utf-8")
    return json_path, readme_path


__all__ = [
    "COMPILE_DB_STUB_BODY",
    "COMPILE_DB_STUB_README",
    "CppCoverageReport",
    "STUB_README_FILENAME",
    "check_cpp",
    "compute_report",
    "emit_compile_db_stub",
]
