"""Write bundled Cortex starter templates into the current repository."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_TEMPLATE_FILES = {
    "external-adapter": "external_adapter.py",
    "local-strategy": "local_strategy.py",
}

_DEFAULT_OUTPUT_DIRS = {
    "external-adapter": Path(".cortex") / "adapters",
    "local-strategy": Path(".cortex") / "strategies",
}

_MODULE_STEM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def _templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"

def _template_path(kind: str) -> Path:
    template_path = _templates_dir() / _TEMPLATE_FILES[kind]
    if not template_path.is_file():
        raise FileNotFoundError(f"missing bundled template: {template_path}")
    return template_path

def _default_output_path(kind: str, name: str) -> Path:
    if not _MODULE_STEM_RE.fullmatch(name):
        raise ValueError(
            "name must be a simple Python module stem when --output is omitted",
        )
    return _DEFAULT_OUTPUT_DIRS[kind] / f"{name}.py"

def _resolve_output_path(output: Path | None, *, cwd: Path, kind: str, name: str) -> Path:
    chosen = output if output is not None else _default_output_path(kind, name)
    if not chosen.is_absolute():
        chosen = cwd / chosen
    return chosen

def _display_path(path: Path, *, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)

def scaffold_template(
    kind: str,
    name: str,
    *,
    output: Path | None = None,
    force: bool = False,
    cwd: Path | None = None,
) -> Path:
    """Copy a bundled template into the current repository."""
    if kind not in _TEMPLATE_FILES:
        raise ValueError(f"unknown template kind: {kind}")

    work_root = (cwd or Path.cwd()).resolve()
    output_path = _resolve_output_path(output, cwd=work_root, kind=kind, name=name)
    template_text = _template_path(kind).read_text(encoding="utf-8")

    if output_path.exists() and not force:
        raise FileExistsError(
            f"refusing to overwrite existing file without --force: {output_path}",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template_text, encoding="utf-8")
    return output_path

def main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``cortex scaffold``."""
    parser = argparse.ArgumentParser(
        prog="cortex scaffold",
        description="Write bundled Cortex templates into the current repository",
    )
    sub = parser.add_subparsers(dest="kind", required=True)

    kinds = (
        ("local-strategy", "Write the local strategy template"),
        ("external-adapter", "Write the external adapter template"),
    )
    for kind, help_text in kinds:
        kind_parser = sub.add_parser(kind, help=help_text)
        kind_parser.add_argument("name", help="Logical name for the scaffold")
        kind_parser.add_argument(
            "--output",
            type=Path,
            default=None,
            help="Custom output path (defaults under .cortex/)",
        )
        kind_parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite an existing file",
        )

    args = parser.parse_args(argv)

    try:
        output_path = scaffold_template(
            args.kind,
            args.name,
            output=args.output,
            force=args.force,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"[cortex] error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    cwd = Path.cwd().resolve()
    print(f"Wrote {_display_path(output_path, cwd=cwd)}")
    print("Remember to wire this into .cortex/discover.yaml.")
