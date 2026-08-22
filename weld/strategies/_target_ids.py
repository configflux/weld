"""Plausible node-ID spellings for a repo-relative path named in prose.

Two strategies record a relationship to a file path they merely *read about*:
``validator_targets`` harvests path literals out of a lint module, and
``concept_from_bd`` harvests them out of an issue description. Some *other*
strategy minted the node for that path, under whichever ID class its
``.weld/discover.yaml`` entry declared, so a referrer cannot know the
spelling -- it offers every plausible one and lets the post-processor's
dangling-edge sweep keep whichever resolves. That guess is this module.

Only referrers belong here. A strategy that parses a path reference *and*
mints its node (``compose``, ``dockerfile``) has nothing to guess: an edge to
a node you created yourself cannot dangle. The one stub ``validator_targets``
mints goes through :func:`weld._node_ids.file_id` directly for the same
reason.

The rule is centralised because it was previously duplicated and one copy
silently rotted: the ADR 0041 rename moved ``file:`` IDs from ``file:<stem>``
to ``file:<rel-path-without-extension>``, ``validator_targets`` was written
against the new rule, and ``concept_from_bd`` kept emitting the old one.
Nothing failed loudly, because a wrong spelling is indistinguishable from an
unresolvable one -- :func:`weld._discover_postprocess._clean_and_dedup_edges`
drops both without a word. The result was twelve orphan ``concept:`` nodes
that ``wd lint`` reported and no test caught. One copy of the rule, imported
by every referrer, is what stops the next convention change from doing the
same thing again.

Every spelling comes from :mod:`weld._node_ids` (ADR 0041 Layer 1): ``file:``
from :func:`weld._node_ids.file_id`, ``config:`` from
:func:`weld._node_ids.config_id`, which :mod:`weld.strategies.config_file`
also mints from, and ``tool:`` from :func:`weld._node_ids.tool_id`, which
:mod:`weld.strategies.tool_script` mints from. Re-exported here so a referrer
reads one module; bd hxsi moved the rule itself out of this file, because a
guesser holding the authoritative copy of a minting rule is the inversion
that let the previous copy rot.

The ``tool:`` spelling arrived last and only once it was safe to offer
(bd mdvp). This module's docstring had claimed since bd hxsi that shell
scripts "reach the graph as ``tool:``, ``doc:``, ``config:``, or
``workflow:``, which the other spellings below cover" -- and no code path
emitted ``tool:``. So every ``validates`` edge a lint aimed at a shell script
fell back to the ``config:`` spelling, dangled, and left nothing in the graph
joining a validator to the script it governs. The fix could not just be a
fourth entry in the list: ``tool:`` IDs were bare stems, so offering the
spelling would have risked landing an edge on a same-stem script in a
different directory -- the confidently wrong claim the dangling-edge sweep
cannot catch, because the node it hits is real.
:func:`weld._node_ids.tool_id` path-qualifies the ID, which is what removed
the hazard rather than merely narrowing it.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import config_id, file_id, tool_id

#: Extensions whose files are surfaced as ``doc:`` nodes by the markdown
#: strategy family rather than ``file:`` nodes.
DOC_EXTENSIONS = frozenset({".md"})

#: Extensions that ``file:`` nodes are actually minted for. This
#: deny-by-default list is a correctness guard, not a filter:
#: :func:`weld._node_ids.file_id` strips the final extension, so
#: ``install.sh`` and ``install.py`` mint the *same* ``file:install`` ID.
#: Offering the ``file:`` spelling for a ``.sh`` literal would therefore let
#: a shell script's edge silently land on a same-stem Python module -- a
#: confidently wrong claim, and one the dangling-edge sweep cannot catch
#: because the node it hits is real. No weld strategy mints ``file:`` nodes
#: for shell, markdown, or the config/build family; those reach the graph as
#: ``tool:``, ``doc:``, ``config:``, or ``workflow:``, which the other
#: spellings below cover.
FILE_NODE_EXTENSIONS = frozenset({".py", ".pyi"})

#: Extensions whose files ``tool_script`` may have claimed as ``tool:``
#: nodes. Safe to offer only because :func:`weld._node_ids.tool_id`
#: path-qualifies the ID; see this module's docstring for what a bare-stem
#: ``tool:`` would have let a referrer claim. Extensionless entry points
#: (``gradlew``, ``configure``, a repo's top-level task runner) are ``tool:``
#: nodes too, and :func:`target_ids` covers them by suffix absence rather
#: than by listing names here.
TOOL_EXTENSIONS = frozenset({".sh"})


def target_ids(rel_path: str) -> list[str]:
    """Return the plausible node-ID spellings for *rel_path*.

    The repository names the same file under different ID classes depending
    on which strategy claimed it: ``file:`` for source, ``doc:`` for markdown
    under a ``doc:``-prefixed source entry, ``config:`` for the root config
    files. Emitting each candidate and letting the discovery post-processor
    drop the unresolved ones keeps a referring strategy independent of
    another entry's ``id_prefix``.

    The ``file:`` spelling is offered only for the extensions in
    :data:`FILE_NODE_EXTENSIONS`; see that constant for why a wider offer
    would mint edges onto the wrong node rather than onto no node. The
    ``tool:`` spelling is offered for :data:`TOOL_EXTENSIONS` and for
    extensionless paths, which is the shape ``tool_script`` exists to
    classify.

    The order is fixed (``file:``, ``doc:``, ``tool:``, ``config:``) so edge
    order in a caller's output does not depend on anything but the path
    itself (ADR 0012 § 3 canonical output).

    Examples
    --------
    >>> target_ids("weld/discover.py")
    ['file:weld/discover', 'config:weld_discover_py']
    >>> target_ids("docs/mcp.md")
    ['doc:docs/mcp', 'config:docs_mcp_md']
    >>> target_ids("install.sh")
    ['tool:install', 'config:install_sh']
    >>> target_ids("tools/publish.sh")
    ['tool:tools/publish', 'config:tools_publish_sh']
    >>> target_ids("gradlew")
    ['tool:gradlew', 'config:gradlew']
    """
    suffix = Path(rel_path).suffix
    stem_path = file_id(rel_path).split(":", 1)[1]
    out: list[str] = []
    if suffix in FILE_NODE_EXTENSIONS:
        out.append(f"file:{stem_path}")
    if suffix in DOC_EXTENSIONS:
        out.append(f"doc:{stem_path}")
    if suffix in TOOL_EXTENSIONS or not suffix:
        out.append(tool_id(rel_path))
    out.append(config_id(rel_path))
    return out


__all__ = [
    "DOC_EXTENSIONS",
    "FILE_NODE_EXTENSIONS",
    "TOOL_EXTENSIONS",
    "config_id",
    "target_ids",
    "tool_id",
]
