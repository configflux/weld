"""Region-aware destination writer for ``wd bootstrap``.

Split from ``weld.bootstrap_managed`` for line-count hygiene. The parser and
region primitives live in ``bootstrap_managed``; this module wires them into
the writer/diff semantics described in ADR 0033.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from weld._safe_text import sanitize_terminal_text
from weld.bootstrap_managed import (
    MarkerError,
    append_region,
    has_any_start,
    parse_regions,
    pre_marker_message,
    region_diff,
    replace_region_bodies,
    whole_file_diff,
)


# The diffs below are the most attacker-influenced text ``wd`` prints: the
# bytes are whatever the scanned repository contains, so a destination file
# holding a raw ``ESC [ 2 J`` would clear the operator's screen mid-review.
#
# Each one escapes inline rather than through a shared ``_print_diff`` helper,
# which is deliberate and was measured: routing them through a helper hides the
# write behind a parameter, and ``tools.lint_terminal_safety`` then reports zero
# findings whether the helper escapes or not. The repetition buys a boundary the
# guard can actually verify, which is the whole point of having one.


def process_template_dest(
    rendered: str,
    dest: Path,
    display: str,
    *,
    force: bool,
    diff: bool,
    framework: str,
    include_unmanaged: bool,
) -> bool:
    """Apply the rendered template to *dest* with region-aware semantics.

    Returns True when a diff/refusal is signalled (file missing, drifted,
    pre-marker layout, or malformed markers); returns False on no-op, write,
    or clobber. Callers accumulate the True returns into the ``--diff``
    exit-code count.
    """
    template_regions = parse_regions(rendered)
    has_regions = bool(template_regions)

    if not dest.is_file():
        if diff:
            print(f"{display} is missing; would seed from template.")
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
        print(f"Wrote {display}")
        return False

    existing = dest.read_text(encoding="utf-8")

    if not has_regions:
        return _process_unmanaged_dest(
            existing, rendered, dest, display, force=force, diff=diff,
        )

    if not has_any_start(existing):
        if force and not diff:
            dest.write_text(rendered, encoding="utf-8")
            print(f"Wrote {display} (re-seeded with managed-region markers)")
            return False
        print(sanitize_terminal_text(pre_marker_message(display, framework)))
        return True

    return _process_managed_dest(
        existing, rendered, dest, display,
        force=force, diff=diff, include_unmanaged=include_unmanaged,
    )


def _process_unmanaged_dest(
    existing: str, rendered: str, dest: Path, display: str,
    *, force: bool, diff: bool,
) -> bool:
    """Whole-file fallback used when the template has no managed regions."""
    if diff:
        if existing == rendered:
            return False
        diff_text = "".join(
            difflib.unified_diff(
                existing.splitlines(keepends=True),
                rendered.splitlines(keepends=True),
                fromfile=f"{display} (on disk)",
                tofile=f"{display} (template)",
            )
        )
        if diff_text:
            print(
                sanitize_terminal_text(diff_text),
                end="" if diff_text.endswith("\n") else "\n",
            )
        else:
            print(f"{display} differs from the current template.")
        return True
    if not force:
        if existing == rendered:
            print(f"{display} is up-to-date, skipping.")
        else:
            print(
                f"{display} differs from the current template. "
                f"Run `wd bootstrap <framework> --diff` to inspect or "
                f"`--force` to update."
            )
        return False
    dest.write_text(rendered, encoding="utf-8")
    print(f"Wrote {display}")
    return False


def _process_managed_dest(
    existing: str, rendered: str, dest: Path, display: str,
    *, force: bool, diff: bool, include_unmanaged: bool,
) -> bool:
    """Region-aware writer/diff for templates that carry markers."""
    try:
        diff_text, drifted, missing = region_diff(
            existing, rendered,
            fromfile=f"{display} (on disk)",
            tofile=f"{display} (template)",
        )
    except MarkerError as exc:
        print(f"{display}: malformed managed-region markers: {exc}")
        return True

    if diff:
        if include_unmanaged:
            full = whole_file_diff(
                existing, rendered,
                fromfile=f"{display} (on disk)",
                tofile=f"{display} (template)",
            )
            if full:
                print(
                    sanitize_terminal_text(full),
                    end="" if full.endswith("\n") else "\n",
                )
                return True
            return False
        if not drifted and not missing:
            return False
        if diff_text:
            print(
                sanitize_terminal_text(diff_text),
                end="" if diff_text.endswith("\n") else "\n",
            )
        for name in missing:
            print(f"{display}: managed region {name!r} missing on disk.")
        return True

    if not drifted and not missing:
        print(f"{display} is up-to-date, skipping.")
        return False

    if not force:
        if drifted:
            print(
                sanitize_terminal_text(diff_text),
                end="" if diff_text.endswith("\n") else "\n",
            )
        for name in missing:
            print(f"{display}: managed region {name!r} missing on disk.")
        names = ", ".join(sorted(set(drifted) | set(missing)))
        print(
            f"{display} differs in managed region(s): {names}. "
            f"Run `wd bootstrap <framework> --diff` to inspect or "
            f"`--force` to update."
        )
        return False

    new_text = replace_region_bodies(existing, rendered, drifted)
    for name in missing:
        new_text = append_region(new_text, rendered, name)
    dest.write_text(new_text, encoding="utf-8")
    for name in drifted:
        print(f"clobbered managed region {name!r} in {display}")
    for name in missing:
        print(f"appended managed region {name!r} to {display}")
    return False
