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
import os
import shutil
import subprocess
import sys
import traceback
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


def run_demo(kind: str, target: Path, *, bootstrap: bool = True) -> int:
    """Invoke the bundled bootstrap script for ``kind`` against ``target``.

    For ``kind == "polyrepo"`` the workspace is then materialized in-process
    via :func:`weld._workspace_bootstrap.bootstrap_workspace` so the demo is
    immediately usable (``wd workspace status`` works without a separate
    step). Pass ``bootstrap=False`` to preserve the older two-step
    contract for fixtures or tests that want the unmaterialized scaffold.

    Returns the script's exit code (or a non-zero code if the in-process
    bootstrap failed).
    """
    script = _script_for(kind)
    cmd = [_bash_executable(), str(script), str(target)]
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0 or kind != "polyrepo" or not bootstrap:
        return completed.returncode
    # Local import: keep ``weld.demo`` lightweight at module load time.
    from weld._workspace_bootstrap import bootstrap_workspace
    try:
        bootstrap_workspace(target)
    except Exception as exc:  # noqa: BLE001 -- demo, surface as exit code
        sys.stderr.write(f"wd demo polyrepo: bootstrap failed: {exc}\n")
        # Operators chasing a real bug need the full traceback; opt in
        # via ``WELD_DEBUG`` (any non-empty value) so the default
        # output stays a single tidy line for normal users.
        if os.environ.get("WELD_DEBUG", ""):
            traceback.print_exc(file=sys.stderr)
        return 1
    return 0


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
        if name == "polyrepo":
            demo_parser.add_argument(
                "--no-bootstrap",
                action="store_true",
                help=(
                    "Skip the in-process workspace bootstrap; produce the "
                    "scaffold only (the older two-step contract)"
                ),
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd demo``."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.action == "list":
        return _list_demos(as_json=args.json)

    bootstrap = not getattr(args, "no_bootstrap", False)
    return run_demo(args.action, args.init, bootstrap=bootstrap)


if __name__ == "__main__":
    raise SystemExit(main())
