"""Managed-region marker parser and writer for ``wd bootstrap``.

Implements ADR 0033 (``docs/adrs/0033-bootstrap-managed-content.md``):
bootstrap templates declare named, contiguous managed regions with HTML or
``#`` comment markers; ``wd bootstrap`` writes/diffs only inside those
regions while leaving operator-curated content outside the markers untouched.

The marker shapes accepted are::

    <!-- weld-managed:start name=<slug> -->
    ...managed body...
    <!-- weld-managed:end name=<slug> -->

and the ``#``-comment equivalent for non-markdown templates::

    # weld-managed:start name=<slug>
    ...managed body...
    # weld-managed:end name=<slug>

Each region's ``name`` is a kebab-case ASCII slug, unique per file. Markers
must each occupy their own line. Nesting and duplicate names are hard parse
errors.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

# Marker grammar. ``start`` and ``end`` keywords are the only difference; the
# rest of the line shape is identical so the regexes mirror each other. A
# single ``name=<slug>`` attribute is required on both ends; further
# attributes are not accepted (kept minimal per ADR §1).
_NAME_RE = r"(?P<name>[a-z0-9][a-z0-9-]*)"
_HTML_START = re.compile(
    r"^[ \t]*<!--\s*weld-managed:start\s+name=" + _NAME_RE + r"\s*-->[ \t]*$"
)
_HTML_END = re.compile(
    r"^[ \t]*<!--\s*weld-managed:end\s+name=" + _NAME_RE + r"\s*-->[ \t]*$"
)
_HASH_START = re.compile(
    r"^[ \t]*#\s*weld-managed:start\s+name=" + _NAME_RE + r"\s*$"
)
_HASH_END = re.compile(
    r"^[ \t]*#\s*weld-managed:end\s+name=" + _NAME_RE + r"\s*$"
)
# Loose detector for "any start marker on this line". Used only to flag
# malformed markers (e.g. missing name=) and to decide migration mode.
_ANY_START = re.compile(r"weld-managed:start")
_ANY_END = re.compile(r"weld-managed:end")


class MarkerError(ValueError):
    """Raised when a file's managed-region markers are malformed."""


@dataclass(frozen=True)
class Region:
    """A single managed region inside a parsed file."""

    name: str
    # Lines inside the region (exclusive of the start/end marker lines), with
    # trailing newlines preserved. Empty list when the region is empty.
    body_lines: tuple[str, ...]
    start_line: int  # 1-based line number of the start marker
    end_line: int  # 1-based line number of the end marker

    @property
    def body(self) -> str:
        return "".join(self.body_lines)


def _classify_marker(line: str) -> tuple[str, str] | None:
    """Return ``(kind, name)`` if *line* is a marker, else ``None``.

    *kind* is one of ``"start"`` or ``"end"``.
    """
    for regex, kind in (
        (_HTML_START, "start"),
        (_HTML_END, "end"),
        (_HASH_START, "start"),
        (_HASH_END, "end"),
    ):
        m = regex.match(line)
        if m:
            return kind, m.group("name")
    return None


def parse_regions(text: str) -> list[Region]:
    """Parse *text* and return its managed regions in source order.

    Raises ``MarkerError`` when markers are malformed: missing ``name=``,
    mismatched start/end names, nested starts, unterminated regions, dangling
    ends, or duplicate region names within a single file.
    """
    regions: list[Region] = []
    current_name: str | None = None
    current_start_line: int = 0
    current_body: list[str] = []
    seen: set[str] = set()
    lines = text.splitlines(keepends=True)

    for i, raw in enumerate(lines, start=1):
        # Strip line-ending for marker classification only; the body retains
        # the original characters (including trailing newlines).
        bare = raw.rstrip("\r\n")
        marker = _classify_marker(bare)
        if marker is None:
            # A free-form line that *mentions* a marker keyword without
            # parsing as one is treated as a hard error: it almost certainly
            # is a typo of an intended marker (e.g. missing ``name=``).
            if _ANY_START.search(bare) or _ANY_END.search(bare):
                raise MarkerError(
                    f"line {i}: malformed weld-managed marker "
                    f"(expected 'name=<slug>'): {bare!r}"
                )
            if current_name is not None:
                current_body.append(raw)
            continue

        kind, name = marker
        if kind == "start":
            if current_name is not None:
                raise MarkerError(
                    f"line {i}: nested weld-managed:start name={name!r} "
                    f"inside open region {current_name!r}"
                )
            if name in seen:
                raise MarkerError(
                    f"line {i}: duplicate region name {name!r} in same file"
                )
            current_name = name
            current_start_line = i
            current_body = []
        else:  # kind == "end"
            if current_name is None:
                raise MarkerError(
                    f"line {i}: weld-managed:end name={name!r} with no "
                    f"open start"
                )
            if name != current_name:
                raise MarkerError(
                    f"line {i}: weld-managed:end name={name!r} does not "
                    f"match open start name={current_name!r}"
                )
            regions.append(
                Region(
                    name=current_name,
                    body_lines=tuple(current_body),
                    start_line=current_start_line,
                    end_line=i,
                )
            )
            seen.add(current_name)
            current_name = None
            current_body = []

    if current_name is not None:
        raise MarkerError(
            f"end-of-file: weld-managed:start name={current_name!r} not "
            f"closed by a matching :end"
        )
    return regions


def has_any_start(text: str) -> bool:
    """Return True if *text* contains any ``weld-managed:start`` line."""
    for line in text.splitlines():
        if _classify_marker(line) is not None and "start" in line.lower():
            # _classify_marker matched, but it could be a start or end. Look
            # for the literal ``:start`` in the matched line to be sure.
            return ":start" in line
    return False


def region_diff(
    existing_text: str,
    template_text: str,
    *,
    fromfile: str,
    tofile: str,
) -> tuple[str, list[str], list[str]]:
    """Compute a region-scoped unified diff.

    Returns a tuple ``(diff_text, drifted_names, missing_names)`` where:

    * ``diff_text`` is the concatenated unified diff for every drifted region.
    * ``drifted_names`` lists regions whose body bytes differ.
    * ``missing_names`` lists regions present in *template_text* but missing
      from *existing_text*.
    """
    template_regions = {r.name: r for r in parse_regions(template_text)}
    existing_regions = {r.name: r for r in parse_regions(existing_text)}

    chunks: list[str] = []
    drifted: list[str] = []
    missing: list[str] = []
    for name, tmpl_region in template_regions.items():
        if name not in existing_regions:
            missing.append(name)
            continue
        existing_region = existing_regions[name]
        if existing_region.body == tmpl_region.body:
            continue
        drifted.append(name)
        chunk = "".join(
            difflib.unified_diff(
                existing_region.body_lines,
                tmpl_region.body_lines,
                fromfile=f"{fromfile} [region={name}]",
                tofile=f"{tofile} [region={name}]",
            )
        )
        chunks.append(chunk)
    return "".join(chunks), drifted, missing


def replace_region_bodies(
    existing_text: str, template_text: str, region_names: list[str]
) -> str:
    """Return *existing_text* with the named regions overwritten from template.

    Only the bodies are replaced; the start/end marker lines on disk are
    preserved (they already match the template's marker shape by name).
    Unknown region names are ignored — callers are expected to compute the
    overlap themselves.
    """
    if not region_names:
        return existing_text
    template_regions = {r.name: r for r in parse_regions(template_text)}
    existing_regions = parse_regions(existing_text)
    name_set = set(region_names)
    out_lines = existing_text.splitlines(keepends=True)
    # Walk regions in reverse so line indices don't shift while splicing.
    for region in reversed(existing_regions):
        if region.name not in name_set:
            continue
        tmpl = template_regions.get(region.name)
        if tmpl is None:
            continue
        new_body = list(tmpl.body_lines)
        # Slice covers the body only (between start and end marker lines).
        out_lines[region.start_line:region.end_line - 1] = new_body
    return "".join(out_lines)


def append_region(existing_text: str, template_text: str, name: str) -> str:
    """Append a missing region from *template_text* to *existing_text*.

    The appended block is preceded by exactly one blank line so it forms a
    well-formed markdown block regardless of the existing trailing newline.
    """
    template_regions = {r.name: r for r in parse_regions(template_text)}
    if name not in template_regions:
        raise MarkerError(
            f"cannot append region {name!r}: not present in template"
        )
    region = template_regions[name]
    template_lines = template_text.splitlines(keepends=True)
    # start_line/end_line are 1-based and inclusive of the marker lines.
    block = "".join(
        template_lines[region.start_line - 1:region.end_line]
    )
    if not existing_text.endswith("\n"):
        existing_text += "\n"
    if not existing_text.endswith("\n\n"):
        existing_text += "\n"
    return existing_text + block


def whole_file_diff(
    existing_text: str,
    template_text: str,
    *,
    fromfile: str,
    tofile: str,
) -> str:
    """Whole-file unified diff (used by ``--diff --include-unmanaged``)."""
    return "".join(
        difflib.unified_diff(
            existing_text.splitlines(keepends=True),
            template_text.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def pre_marker_message(display_path: str, framework: str) -> str:
    """Actionable message printed when a file predates the marker layout."""
    return (
        f"{display_path}: pre-marker layout detected. "
        f"Run `wd bootstrap {framework} --force` to re-seed the file with "
        f"managed-region markers (this overwrites any in-region edits but "
        f"preserves the file when --force is omitted), or hand-add the "
        f"markers per docs/adrs/0033-bootstrap-managed-content.md."
    )


def lint_template_dir(templates_dir: Path) -> list[str]:
    """Return a list of human-readable lint errors for the template dir.

    Verifies: every ``*.md`` template parses cleanly; sibling ``*.cli.md``
    variants declare the same region ``name`` set; the ``codex_mcp_config.toml``
    template parses cleanly when present.
    """
    errors: list[str] = []

    def _names(path: Path) -> set[str] | None:
        try:
            text = path.read_text(encoding="utf-8")
            return {r.name for r in parse_regions(text)}
        except MarkerError as exc:
            errors.append(f"{path.name}: {exc}")
            return None
        except OSError as exc:  # pragma: no cover - filesystem edge
            errors.append(f"{path.name}: {exc}")
            return None

    md_files = sorted(p for p in templates_dir.glob("*.md") if not p.name.endswith(".cli.md"))
    for md in md_files:
        names_md = _names(md)
        cli = md.with_name(md.stem + ".cli.md")
        if not cli.is_file():
            continue
        names_cli = _names(cli)
        if names_md is None or names_cli is None:
            continue
        if names_md != names_cli:
            errors.append(
                f"{md.name} vs {cli.name}: sibling-variant region-name "
                f"mismatch (md={sorted(names_md)}, cli={sorted(names_cli)})"
            )

    for toml in templates_dir.glob("*.toml"):
        _names(toml)

    return errors
