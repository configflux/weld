"""What weld has wired into a ``discover.yaml``, recorded in the file itself.

An append-only merge that compares detected entries against the *live* config
cannot tell an entry it never offered from one the maintainer deleted: both
read as absent. Offering both is a merge that undoes hand edits every time it
runs; offering neither is the bug this fixes. The comparison has to be against
what weld itself previously wrote, and until now the only thing weld recorded
about a generated config was its version (``# generated-by: weld <version>``).

This module is that record: one ``# wired-entry:`` comment line per
``(strategy, target)`` key ``wd init`` or ``wd init --refresh`` has written
into this config.

**Why it lives in ``discover.yaml`` and not in ``.weld/``.** The record is a
fact about *this file's own content*, and it has to travel with the file
through git, a branch switch and a fresh clone. Every other ``.weld/`` state
file is rebuildable from source and is gitignored for exactly that reason
(``discovery-state.json``, ``graph.json``, ``file-index-state.json``); a
sidecar would therefore be absent on a teammate's clone, and their first
refresh would resurrect the entry the author had deliberately removed -- the
one outcome the record exists to prevent. Storing it as comments also keeps it
inert: :func:`weld._yaml.parse_yaml` skips comment lines, so no consumer of
the config sees anything new.

**Line-shaped, not block-shaped.** Every line of the record carries the same
``# wired-entr`` prefix, so removing it is an exact line filter that can never
swallow a neighbouring comment, and a maintainer deleting one key line does
exactly what the note above it says.

Configs written before this change carry no record at all, and that needs no
special case: the caller folds the detectable keys the config *already
carries* into the record on every run, so an unrecorded config seeds itself
from its own entries the first time it is refreshed. With no record, weld
genuinely cannot tell "never offered" from "removed long ago", so it offers
what is missing, once, and the removal that follows is durable.
"""

from __future__ import annotations

import re

from weld._init_entry_offer import EntryKey
from weld._yaml import parse_yaml

#: Every line of the record starts with this, note line included.
_LEDGER_PREFIX = "# wired-entr"

#: One recorded key: ``# wired-entry: <strategy> <target>``.
_ENTRY_RE = re.compile(
    r"^# wired-entry:[ \t]+(?P<strategy>\S+)[ \t]+(?P<target>.+?)[ \t]*$",
)

#: The one-line note above the keys. Kept to a single line so the whole record
#: is a flat set of prefixed lines rather than a block with a shape to parse.
_NOTE = (
    "# wired-entries: what weld wired here; delete a line to let "
    "--refresh re-offer it.\n"
)

#: Insert anchor: just below the version stamp when the config has one.
_STAMP_LINE_RE = re.compile(r"^# generated-by: weld .*$\n", re.MULTILINE)


def ledger_keys(text: str) -> set[EntryKey]:
    """Keys weld recorded writing into *text*; empty when it carries no record.

    An absent record and an empty one are the same answer on purpose. The
    caller's ``known`` set is this union the keys the config carries, so a
    config written before the record existed behaves as though it had recorded
    exactly what it wires -- which is what it wires.
    """
    keys: set[EntryKey] = set()
    for line in text.splitlines():
        if not line.startswith(_LEDGER_PREFIX):
            continue
        match = _ENTRY_RE.match(line)
        if match is not None:
            keys.add((match.group("strategy"), match.group("target")))
    return keys


def config_entry_keys(text: str) -> set[EntryKey]:
    """``(strategy, target)`` keys the config's own source entries carry.

    A ``files:`` entry contributes one key per name, so a config listing
    ``package.json`` is offered ``tsconfig.json`` beside it rather than a
    second whole-list entry. ``glob:`` and ``path:`` contribute their pattern,
    which is how :func:`weld._source_resolve.resolve_source_files` names them.

    An ``enabled: false`` entry counts as carried, unlike the language pass:
    disabling an entry is a decision about that entry, and re-offering it
    would undo the decision. An unparsable config carries nothing -- refresh
    then offers its full set rather than dropping entries on a guess.
    """
    try:
        data = parse_yaml(text)
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001 -- an unreadable config carries nothing.
        return set()
    keys: set[EntryKey] = set()
    for src in sources:
        if not isinstance(src, dict):
            continue
        strategy = src.get("strategy")
        if not isinstance(strategy, str):
            continue
        for pattern in (src.get("glob"), src.get("path")):
            if isinstance(pattern, str) and pattern:
                keys.add((strategy, pattern))
        listed = src.get("files")
        if isinstance(listed, list):
            keys.update(
                (strategy, name) for name in listed if isinstance(name, str)
            )
    return keys


def render_ledger(keys: set[EntryKey] | frozenset[EntryKey]) -> str:
    """The record's comment lines for *keys*, or ``""`` when there are none.

    Sorted, so re-running a command that records the same set produces the
    same bytes and a refresh that wired nothing leaves no diff.
    """
    if not keys:
        return ""
    lines = "".join(
        f"# wired-entry: {strategy} {target}\n"
        for strategy, target in sorted(keys)
    )
    return f"{_NOTE}{lines}"


def _strip_ledger(text: str) -> str:
    """Drop the record's own lines, leaving every other comment untouched."""
    return "".join(
        line for line in text.splitlines(keepends=True)
        if not line.startswith(_LEDGER_PREFIX)
    )


def apply_ledger(
    text: str, keys: set[EntryKey] | frozenset[EntryKey],
) -> tuple[str, bool]:
    """Rewrite *text*'s record to *keys*; return ``(text, changed)``.

    Inserted just below the ``# generated-by:`` stamp when the config has one
    -- the two are the same kind of statement about the file -- and otherwise
    just above ``sources:``. A config with neither anchor is returned
    unchanged: there is nowhere to put the record that would not be a guess
    about someone else's file.
    """
    stripped = _strip_ledger(text)
    rendered = render_ledger(keys)
    if not rendered:
        return (stripped, True) if stripped != text else (text, False)
    stamp = _STAMP_LINE_RE.search(stripped)
    if stamp is not None:
        cut = stamp.end()
        updated = f"{stripped[:cut]}{rendered}{stripped[cut:]}"
    else:
        lines = stripped.splitlines(keepends=True)
        index = next(
            (i for i, line in enumerate(lines) if line.startswith("sources:")),
            None,
        )
        if index is None:
            return text, False
        lines.insert(index, rendered)
        updated = "".join(lines)
    return (updated, True) if updated != text else (text, False)
