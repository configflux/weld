"""Repo-relative script paths a GitHub Actions ``run:`` step names (bd lwrh).

``run:`` steps are shell text embedded in workflow YAML. Neither
``gh_workflow`` nor ``yaml_meta`` looked inside them, so a workflow that
invoked a repo script by path -- even the release pipeline's own gate,
``tools/publish_overlays/publish-pypi.yml`` running
``tools/release_claims_lint.py`` before every PyPI upload -- had no edge to
that script. ``wd context`` on the script showed BUILD-target and symbol
edges but nothing naming the workflow that actually runs it, so "what
invokes this" fell back to grep.

This module does not add a second shell grammar. A ``run:`` block's body is
shell text with the same grammar a standalone script has, extension and all,
so extraction here is split in two honest pieces:

1. **YAML-shaped**: find every ``run:`` key and read its value -- an inline
   scalar (the rest of the line) or a block scalar (``|``/``>``), whose body
   is every following line indented more than the key itself, per YAML's own
   rule. Line-based, deliberately not a full YAML parser, matching the
   discipline both ``gh_workflow`` and ``yaml_meta`` already use for the rest
   of a workflow file.
2. **Shell-shaped**: hand the extracted text to
   :func:`weld.strategies._shell_refs.shell_text_references`, the exact
   parser :mod:`weld.strategies.tool_script` uses for a standalone script's
   body (ADR 0106) -- same comment rule, same safety refusals (no absolute
   paths, no ``..``, no symlinks, no self-reference guessing), same
   "unresolved yields nothing" discipline. A conditional invocation
   (``if [ -f x ]; then python x; fi``) still counts: the reference is the
   fact, not the shape of the guard around it.

Scope is deliberately narrower than "the whole YAML file is shell-ish text":
a path-like string in an ``env:`` default or a ``with:`` input is not an
invocation, and scanning the whole document would claim it as one. Only
``run:`` bodies are handed to the shell scanner.

No new bound constants: :func:`workflow_script_references` reuses
``shell_text_references``'s own line/char/referent caps on the concatenated
``run:`` text, the same way a script three times the size of any real
workflow file would already be bounded.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._shell_refs import shell_text_references

#: A ``run:`` mapping key, optionally introduced by a list-item dash
#: (``- run: ...`` is how a nameless step is written). The captured
#: ``indent`` is the column ``run:`` itself starts at, which is what a block
#: scalar's continuation lines must be indented past -- YAML measures a
#: block scalar's own indentation from its key, not from the ``-`` marker.
_RUN_KEY = re.compile(r"^(?P<indent>[ \t]*(?:-[ \t]+)?)run:[ \t]*(?P<rest>.*)$")

#: A YAML block scalar indicator (``|``, ``>``, with optional chomp
#: (``+``/``-``) and explicit indentation-level digits, in either order),
#: optionally followed by a comment. Matched against the text *after*
#: ``run:`` to decide inline-scalar vs. block-scalar; not a full YAML
#: grammar, just enough to tell "there is a command on this line" from
#: "the command starts on the next line".
_BLOCK_SCALAR = re.compile(r"^[|>][+\-0-9]*(?:\s*#.*)?$")


def _extract_run_text(text: str) -> str:
    """Return the concatenated body of every ``run:`` step in *text*."""
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        match = _RUN_KEY.match(lines[i])
        if match is None:
            i += 1
            continue
        indent = len(match.group("indent"))
        rest = match.group("rest").strip()
        i += 1
        if rest and not _BLOCK_SCALAR.match(rest):
            # Inline scalar: the command is the rest of this line.
            blocks.append(rest)
            continue
        # Block scalar (or an empty/malformed rest): consume every
        # following line indented more than the key. A blank line does not
        # end the block -- YAML block scalars permit blank lines in the
        # middle of their own body.
        block_lines: list[str] = []
        while i < n:
            line = lines[i]
            if line.strip() == "":
                block_lines.append("")
                i += 1
                continue
            line_indent = len(line) - len(line.lstrip(" "))
            if line_indent <= indent:
                break
            block_lines.append(line)
            i += 1
        if block_lines:
            blocks.append("\n".join(block_lines))
    return "\n".join(blocks)


def workflow_script_references(root: Path, text: str) -> list[str]:
    """Return repo-relative script paths named in *text*'s ``run:`` steps.

    *text* is a whole workflow YAML file's content. *root* is the discovery
    root every candidate path is resolved against -- see
    :func:`weld.strategies._shell_refs.shell_text_references` for the exact
    resolution and safety rules.
    """
    return shell_text_references(root, _extract_run_text(text))


__all__ = ["workflow_script_references"]
