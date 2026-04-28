"""``wd telemetry`` subcommand surface.

Implements the seven subcommands described by ADR 0035 § 4 ("``wd
telemetry`` subcommand surface"):

- ``status``  — enabled flag, opt-out decision source, resolved path,
  on-disk file size, event count, schema version.
- ``show [--last=N] [--json]`` — pretty-print the trailing N events
  (default 20) or pass through raw JSONL with ``--json``. Lines that
  fail to parse surface as ``{"_corrupt": true}`` skeletons rather
  than crashing the command.
- ``path`` — print the resolved telemetry file path on a single line.
- ``export --output=FILE`` — copy the file bit-for-bit. The destination
  is rejected if any of its path components is named ``.weld`` (paranoid
  guard mandated by the ADR: the export must visibly leave project state).
- ``clear [--yes]`` — delete the file. Without ``--yes`` the user is
  prompted on stdin; the deletion proceeds only on ``y``/``yes``
  (case-insensitive).
- ``disable`` / ``enable`` — toggle ``.weld/telemetry.disabled``.

Every subcommand wraps its body in :class:`weld._telemetry.Recorder`
under a per-verb command name (``telemetry-status``, …). The outer
``cli.main()`` Recorder added in T3 will see ``command="telemetry"``;
the inner record here narrows the surface for triage. The command names
used here are added to :data:`weld._telemetry_allowlist.CLI_COMMANDS`
so the redactor lets them through.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import IO

from weld import _telemetry as tel
from weld._telemetry import Recorder


# ---------------------------------------------------------------------------
# Path helpers.
# ---------------------------------------------------------------------------


_CONFIG_FILENAME = "telemetry.disabled"


def _resolve_root_and_path(start: Path) -> tuple[Path, Path]:
    """Resolve ``(root, telemetry_file)`` for the given starting directory.

    Returns the project (or workspace) root used for opt-out lookup along
    with the resolved telemetry file path. Falls back to ``start`` when
    ``resolve_path`` lands at the XDG state location, in which case the
    sentinel file's lookup root degrades gracefully (no sentinel can exist
    outside ``.weld/``).
    """
    target = tel.resolve_path(start)
    if target is None:
        # Defensive: ``resolve_path`` only returns None if XDG itself fails.
        target = start / ".weld" / tel.TELEMETRY_FILENAME
    # Best-effort: when the resolved path is ``<root>/.weld/telemetry.jsonl``
    # peel ``.weld`` and the filename to recover the root.
    if target.parent.name == ".weld":
        root = target.parent.parent
    else:
        root = start
    return root, target


def _format_path(target: Path, root: Path) -> str:
    """Render ``target`` as a repo-relative path when possible.

    The ADR's ``status`` example uses ``.weld/telemetry.jsonl`` when run
    inside a project. Outside any project we keep the absolute path.
    """
    try:
        rel = target.resolve().relative_to(root.resolve())
        return str(rel)
    except (OSError, ValueError):
        return str(target)


def _path_has_weld_component(p: Path) -> bool:
    """Return True iff any path component (resolved) is exactly ``.weld``."""
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    # ``Path.parts`` includes the final component as well, which is what
    # we want -- a file literally named ``.weld`` is also rejected. The
    # rule from the ADR is: refuse anything whose path components
    # contain ``.weld``.
    return any(part == ".weld" for part in resolved.parts) or any(
        part == ".weld" for part in p.parts
    )


# ---------------------------------------------------------------------------
# Subcommand implementations.
# ---------------------------------------------------------------------------


def _cmd_status(_args: argparse.Namespace, target: Path,
                root: Path, out: IO[str]) -> int:
    enabled, source = tel.is_enabled(cli_flag=None, root=root)
    try:
        size = target.stat().st_size if target.exists() else 0
    except OSError:
        size = 0
    try:
        events = sum(
            1 for _ in target.open("r", encoding="utf-8", errors="replace")
        ) if target.exists() else 0
    except OSError:
        events = 0
    rendered_path = _format_path(target, root)
    out.write(f"enabled: {'true' if enabled else 'false'}\n")
    out.write(f"source: {source.value}\n")
    out.write(f"path: {rendered_path}\n")
    out.write(f"size_bytes: {size}\n")
    out.write(f"events: {events}\n")
    out.write(f"schema_version: {tel.TELEMETRY_SCHEMA_VERSION}\n")
    return 0


def _cmd_show(args: argparse.Namespace, target: Path,
              _root: Path, out: IO[str]) -> int:
    if not target.exists():
        return 0
    last = max(0, int(getattr(args, "last", 20) or 20))
    try:
        # Binary read keeps us robust against partial-line writes.
        raw = target.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return 0
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    tail = lines[-last:] if last > 0 else lines
    if args.json:
        for ln in tail:
            out.write(ln)
            out.write("\n")
        return 0
    parsed: list[dict] = []
    for ln in tail:
        try:
            obj = json.loads(ln)
            if isinstance(obj, dict):
                parsed.append(obj)
            else:
                parsed.append({"_corrupt": True, "_raw_kind": type(obj).__name__})
        except json.JSONDecodeError:
            parsed.append({"_corrupt": True})
    rendered = "\n".join(json.dumps(ev, indent=2, sort_keys=True) for ev in parsed)
    if rendered:
        out.write(rendered)
        out.write("\n")
    return 0


def _cmd_path(_args: argparse.Namespace, target: Path,
              _root: Path, out: IO[str]) -> int:
    out.write(f"{target}\n")
    return 0


def _cmd_export(args: argparse.Namespace, target: Path,
                _root: Path, out: IO[str], err: IO[str]) -> int:
    dest = Path(args.output)
    if _path_has_weld_component(dest):
        err.write(
            "wd telemetry export refuses to write into any .weld/ directory; "
            "choose a destination outside the project's .weld/.\n"
        )
        return 1
    if not target.exists():
        err.write(
            "wd telemetry export: no telemetry file to export "
            f"(expected at {target}).\n"
        )
        return 1
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(target), str(dest))
    except OSError as exc:  # pragma: no cover - filesystem-dependent
        err.write(f"wd telemetry export failed: {type(exc).__name__}\n")
        return 1
    out.write(f"exported {target} -> {dest}\n")
    return 0


def _cmd_clear(args: argparse.Namespace, target: Path,
               _root: Path, out: IO[str], err: IO[str]) -> int:
    if not target.exists():
        out.write("nothing to clear\n")
        return 0
    if not args.yes:
        try:
            answer = input("Delete telemetry file? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in {"y", "yes"}:
            out.write("aborted\n")
            return 0
    try:
        target.unlink()
    except OSError as exc:
        err.write(f"wd telemetry clear failed: {type(exc).__name__}\n")
        return 1
    out.write(f"cleared {target}\n")
    return 0


def _cmd_disable(_args: argparse.Namespace, _target: Path,
                 root: Path, out: IO[str], err: IO[str]) -> int:
    sentinel = root / ".weld" / _CONFIG_FILENAME
    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
    except OSError as exc:
        err.write(f"wd telemetry disable failed: {type(exc).__name__}\n")
        return 1
    out.write(f"telemetry disabled (created {sentinel})\n")
    return 0


def _cmd_enable(_args: argparse.Namespace, _target: Path,
                root: Path, out: IO[str], err: IO[str]) -> int:
    sentinel = root / ".weld" / _CONFIG_FILENAME
    if sentinel.exists():
        try:
            sentinel.unlink()
        except OSError as exc:
            err.write(f"wd telemetry enable failed: {type(exc).__name__}\n")
            return 1
        out.write(f"telemetry enabled (removed {sentinel})\n")
    else:
        out.write("telemetry already enabled\n")
    return 0


# ---------------------------------------------------------------------------
# argparse plumbing.
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wd telemetry",
        description="Manage local telemetry recorded by wd (ADR 0035).",
    )
    sub = parser.add_subparsers(dest="cmd", metavar="subcommand")

    sub.add_parser(
        "status",
        help="show enabled/disabled, decision source, path, size, count",
    )

    show = sub.add_parser(
        "show", help="pretty-print the last N telemetry events"
    )
    show.add_argument(
        "--last", type=int, default=20,
        help="how many trailing events to show (default 20)",
    )
    show.add_argument(
        "--json", action="store_true",
        help="emit raw JSONL instead of pretty-printed objects",
    )

    sub.add_parser("path", help="print the resolved telemetry file path")

    exp = sub.add_parser(
        "export", help="copy the telemetry file to FILE (refuses .weld/ targets)"
    )
    exp.add_argument(
        "--output", required=True, metavar="FILE",
        help="destination path; must not contain a .weld component",
    )

    cl = sub.add_parser(
        "clear", help="delete the telemetry file (prompts unless --yes)"
    )
    cl.add_argument(
        "--yes", action="store_true",
        help="skip the confirmation prompt",
    )

    sub.add_parser(
        "disable", help="create .weld/telemetry.disabled (turns telemetry off)"
    )
    sub.add_parser(
        "enable", help="remove .weld/telemetry.disabled (turns telemetry on)"
    )
    return parser


def _dispatch(args: argparse.Namespace, target: Path, root: Path,
              out: IO[str], err: IO[str]) -> int:
    cmd = args.cmd
    if cmd == "status":
        return _cmd_status(args, target, root, out)
    if cmd == "show":
        return _cmd_show(args, target, root, out)
    if cmd == "path":
        return _cmd_path(args, target, root, out)
    if cmd == "export":
        return _cmd_export(args, target, root, out, err)
    if cmd == "clear":
        return _cmd_clear(args, target, root, out, err)
    if cmd == "disable":
        return _cmd_disable(args, target, root, out, err)
    if cmd == "enable":
        return _cmd_enable(args, target, root, out, err)
    # argparse already enforced the subcommand list; defensive default.
    err.write(f"unknown telemetry subcommand: {cmd!r}\n")
    return 2


_VERB_TO_COMMAND = {
    "status": "telemetry-status",
    "show": "telemetry-show",
    "path": "telemetry-path",
    "export": "telemetry-export",
    "clear": "telemetry-clear",
    "disable": "telemetry-disable",
    "enable": "telemetry-enable",
}


def main(argv: list[str]) -> int:
    """Entry point. Returns 0 (ok) / 1 (error) / 2 (usage)."""
    parser = _build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        return 2
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 for usage errors; preserve the code.
        code = exc.code if isinstance(exc.code, int) else 2
        return code
    if not getattr(args, "cmd", None):
        parser.print_help(sys.stderr)
        return 2

    root, target = _resolve_root_and_path(Path.cwd())
    command_name = _VERB_TO_COMMAND.get(args.cmd, "telemetry")

    out = sys.stdout
    err = sys.stderr
    # ``clear`` is the one subcommand that must NOT self-record: the
    # ADR's UX promises that "the next event re-prints" the first-run
    # notice after a wipe, which only works if clear leaves the file
    # gone. Recording here would immediately re-create it.
    if args.cmd == "clear":
        return _dispatch(args, target, root, out, err)
    rc_holder: list[int] = [1]
    with Recorder(
        surface="cli",
        command=command_name,
        flags=_collect_flag_names(argv),
        root=root,
    ) as rec:
        rc_holder[0] = _dispatch(args, target, root, out, err)
        rec.set_exit_code(rc_holder[0])
    return rc_holder[0]


def _collect_flag_names(argv: list[str]) -> list[str]:
    """Return the long/short flag names found in ``argv``.

    ``--key=value`` and ``--flag`` both surface as ``--key`` / ``--flag``.
    Positionals and bare values are skipped. The :class:`Recorder` will
    further filter through ``CLI_FLAGS`` so unknown names are dropped.
    """
    names: list[str] = []
    for tok in argv:
        if not isinstance(tok, str):
            continue
        if tok.startswith("--"):
            head = tok.split("=", 1)[0]
            names.append(head)
        elif tok.startswith("-") and len(tok) > 1 and tok != "--":
            names.append(tok)
    return names
