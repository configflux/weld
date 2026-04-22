#!/usr/bin/env python3
"""Validate and extract curated release notes from CHANGELOG.md."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

EXIT_USAGE = 2
EXIT_DATAERR = 65

HEADING_RE = re.compile(
    r"^## v(?P<version>[0-9]+\.[0-9]+\.[0-9]+) - "
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})\s*$"
)
ANY_VERSION_HEADING_RE = re.compile(r"^## v[0-9]+\.[0-9]+\.[0-9]+ - ")
PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|WIP)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ReleaseSection:
    version: str
    date: str
    body: str


def _normalize_version(version: str) -> str:
    normalized = version.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", normalized):
        raise ValueError(f"invalid version {version!r}; expected X.Y.Z or vX.Y.Z")
    return normalized


def _version_from_args(args: argparse.Namespace) -> str:
    if args.version:
        return _normalize_version(args.version)
    if args.version_file:
        try:
            return _normalize_version(Path(args.version_file).read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"cannot read version file {args.version_file}: {exc}") from exc
    raise ValueError("either --version or --version-file is required")


def find_section(changelog: Path, version: str) -> ReleaseSection:
    try:
        lines = changelog.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read release notes changelog {changelog}: {exc}") from exc

    start = None
    match: re.Match[str] | None = None
    for index, line in enumerate(lines):
        candidate = HEADING_RE.match(line)
        if candidate and candidate.group("version") == version:
            start = index
            match = candidate
            break

    if start is None or match is None:
        raise ValueError(
            f"{changelog} has no release notes section for v{version}; "
            "expected heading format '## vX.Y.Z - YYYY-MM-DD'"
        )

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if ANY_VERSION_HEADING_RE.match(lines[index]):
            end = index
            break

    body = "\n".join(lines[start + 1:end]).strip()
    return ReleaseSection(version=version, date=match.group("date"), body=body)


def check_section(section: ReleaseSection) -> None:
    if not section.body:
        raise ValueError(f"release notes for v{section.version} are empty")
    if PLACEHOLDER_RE.search(section.body):
        raise ValueError(
            f"release notes for v{section.version} contain placeholder text "
            "(TODO, TBD, or WIP)"
        )


def cmd_check(args: argparse.Namespace) -> int:
    version = _version_from_args(args)
    section = find_section(Path(args.changelog), version)
    check_section(section)
    print(f"[release-notes] v{version} notes verified in {args.changelog}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    version = _version_from_args(args)
    section = find_section(Path(args.changelog), version)
    check_section(section)
    if args.output:
        Path(args.output).write_text(section.body + "\n", encoding="utf-8")
    else:
        sys.stdout.write(section.body + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or extract the current release notes section."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("check", "extract"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--changelog", default="CHANGELOG.md")
        source = sub.add_mutually_exclusive_group(required=True)
        source.add_argument("--version")
        source.add_argument("--version-file")
        if name == "extract":
            sub.add_argument("--output")
            sub.set_defaults(func=cmd_extract)
        else:
            sub.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return args.func(args)
    except ValueError as exc:
        print(f"[release-notes][error] {exc}", file=sys.stderr)
        return EXIT_DATAERR


if __name__ == "__main__":
    raise SystemExit(main())
