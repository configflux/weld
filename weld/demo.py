"""``wd demo`` -- materialize a Weld demo workspace.

Thin CLI wrapper around the bundled bootstrap scripts in
:mod:`weld.demos.scripts`. The same scripts are also published at
``scripts/create-{mono,poly}repo-demo.sh`` at the repository root for
users who have a source checkout; ``wd demo`` is the equivalent
entrypoint for users who only have an installed wheel.

Subcommands
-----------

* ``wd demo list`` -- enumerate available demos (text or JSON).
* ``wd demo monorepo --init <dir>`` -- materialize the monorepo demo.
* ``wd demo polyrepo --init <dir>`` -- materialize the polyrepo demo.

Output is deterministic: the same target directory always produces the
same on-disk layout (the underlying scripts seed git commits with the
caller's resolved git identity, and otherwise emit identical files).
Failure modes (missing git identity, populated target directory) are
surfaced verbatim from the bootstrap scripts via stderr and a non-zero
exit code.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Ordered for deterministic ``wd demo list`` output.
_DEMOS: tuple[tuple[str, str, str], ...] = (
    (
        "monorepo",
        "create-monorepo-demo.sh",
        "TypeScript-flavored monorepo (apps, packages, libs, services)",
    ),
    (
        "polyrepo",
        "create-polyrepo-demo.sh",
        "Three-child polyrepo (services-api, services-auth, libs-shared-models)",
    ),
)


def _scripts_dir() -> Path:
    """Return the directory containing the bundled bootstrap scripts."""
    return Path(__file__).resolve().parent / "demos" / "scripts"


def _script_for(kind: str) -> Path:
    for name, filename, _desc in _DEMOS:
        if name == kind:
            path = _scripts_dir() / filename
            if not path.is_file():
                raise FileNotFoundError(
                    f"missing bundled demo script: {path}",
                )
            return path
    raise ValueError(f"unknown demo: {kind}")


def _list_demos(*, as_json: bool) -> int:
    if as_json:
        payload = [
            {"name": name, "script": filename, "description": desc}
            for name, filename, desc in _DEMOS
        ]
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0
    width = max(len(name) for name, _, _ in _DEMOS)
    sys.stdout.write("Available demos:\n")
    for name, _filename, desc in _DEMOS:
        sys.stdout.write(f"  {name.ljust(width)}  {desc}\n")
    sys.stdout.write(
        "\nRun `wd demo <name> --init <dir>` to materialize a demo.\n",
    )
    return 0


def _bash_executable() -> str:
    """Return the bash interpreter used to run the demo scripts."""
    found = shutil.which("bash")
    if found:
        return found
    # Fall back to ``/bin/bash`` so callers see a coherent error message
    # rather than a Python traceback when bash is genuinely absent.
    return "/bin/bash"


def run_demo(kind: str, target: Path) -> int:
    """Invoke the bundled bootstrap script for ``kind`` against ``target``.

    Returns the script's exit code unchanged so callers see the same
    behaviour as if they had run the script directly.
    """
    script = _script_for(kind)
    cmd = [_bash_executable(), str(script), str(target)]
    completed = subprocess.run(cmd, check=False)
    return completed.returncode


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wd demo",
        description=(
            "Materialize a Weld demo workspace (monorepo or polyrepo) "
            "into a clean target directory."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)

    list_parser = sub.add_parser("list", help="List available demos")
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the demo list as JSON",
    )

    for name, _filename, desc in _DEMOS:
        demo_parser = sub.add_parser(name, help=desc)
        demo_parser.add_argument(
            "--init",
            type=Path,
            required=True,
            metavar="DIR",
            help="Target directory to materialize the demo into",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd demo``."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.action == "list":
        return _list_demos(as_json=args.json)

    return run_demo(args.action, args.init)


if __name__ == "__main__":
    raise SystemExit(main())
