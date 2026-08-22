"""The one place a strategy turns a ``discover.yaml`` glob into paths.

ADR 0020 settled what ``glob:`` and ``exclude:`` *mean*. It said nothing about
who applies them, and the answer became "every strategy, separately": fourteen
private ``_resolve_glob`` copies in two shapes, plus ten strategies that
inlined a third variant. They drifted -- two expanded ``{a,b}`` and twelve did
not, nine sorted and five returned walk order, one re-filtered a list
``walk_glob`` had already filtered -- and the drift is why one exclude defect
took three issues to find (bd 3abf fixed one copy, bd eerc found eleven more,
bd 9gdq found twenty-five strategies with no copy to fix). ADR 0112 replaced
all of it with this module.

Two shapes, because callers need two; **one** resolution path, because a
second one is how the drift started:

* :func:`resolve_glob` -- the files.
* :func:`resolve_glob_with_provenance` -- the files plus the ADR 0017
  ``discovered_from`` list, which is :func:`resolve_glob` composed with
  :func:`weld.strategies._provenance.file_provenance` and nothing else.

Three properties are decisions, not incidental behaviour:

**No ``(root / pattern).parent.is_dir()`` guard.** Every copy carried one. It
is byte-identical to what :func:`weld.glob_match.walk_glob`'s own non-``**``
branch already does, so for a flat glob it was dead weight -- and for a ``**``
glob it was bd t06t, because ``Path('docs/**/*.md').parent`` is the literal
path ``docs/**``, which is never a directory. Nine strategies therefore emitted
*nothing* for a recursive glob (``tool_script`` was the tenth of that family
and had already had its guard removed by bd 0edz): silence, not a subset, so
there was no partial result to notice. Not having the guard is the whole fix.

**Braces are expanded, so they are expanded everywhere.** Only
``typescript_exports`` and ``express`` used to, and each did it *above*
``walk_glob`` -- which is why ``weld._source_resolve.resolve_source_files``,
resolving the same ``discover.yaml`` entry, saw a pattern it could not match
and recorded an empty in-scope file set for a glob the strategy happily
emitted nodes from. The expansion therefore belongs to
:func:`weld.glob_match.expand_braces`, inside the walker both callers share,
so the strategy's file set and the set discovery records cannot diverge (and
the run-level glob memo, bd cjij, is keyed on the pattern as written and so is
actually hit). The widening is safe by construction: neither ``Path.glob`` nor
``weld.glob_match._glob_pattern_to_regex`` understands ``{``, so an unexpanded
brace group matched nothing at all. This turns silence into matches, the same
failure mode as the paragraph above.

**The result is sorted.** Nine of the fourteen copies already sorted, as did
the ~19 further strategies that used to call ``walk_glob`` directly and wrap
it -- folded into this module too (bd 6gzj), so the reviewer question "does
``exclude:`` work in this strategy?" now has one call site to read instead of
twenty. The five that did not sort were the outliers. Sorting once here is
what lets a strategy stop reasoning about walk order; that it changes no byte
of any graph was measured over this repo and eight non-Python corpora, not
argued (ADR 0112).

Excludes are **not** re-applied after the walk. ``walk_glob`` prunes matching
directories *during descent* -- which is what gives the directory form
(``pkg/tests``) its meaning, where filtering a resolved list cannot -- and
applies the repo-boundary filter on the way out. A second
``filter_glob_results`` pass over the result (``test_peer`` had one) is
redundant, and redundancy in this exact place is what the copies were.
"""

from __future__ import annotations

from pathlib import Path

from weld.glob_match import walk_glob
from weld.strategies._provenance import file_provenance

__all__ = [
    "resolve_glob",
    "resolve_glob_with_provenance",
]


def resolve_glob(
    root: Path,
    pattern: str,
    excludes: list[str] | None = None,
) -> list[Path]:
    """Resolve a source entry's *pattern* under *root* into sorted files.

    ``**``, ``{a,b}`` and ``exclude:`` are all :func:`walk_glob`'s job, and
    the one line below is deliberately the whole implementation: what a
    strategy needed on top of that call was a sort, and fourteen copies each
    decided separately whether to do one. Now none of them decide.

    Returns files only -- ``walk_glob`` yields no directories from either
    branch since bd 0d73, so a strategy no longer has to guard its emitter
    against being handed one. ``set`` because a pattern with brace
    alternatives can match one file through more than one of them.
    """
    return sorted(set(walk_glob(root, pattern, excludes=excludes)))


def resolve_glob_with_provenance(
    root: Path,
    pattern: str,
    excludes: list[str] | None = None,
) -> tuple[list[Path], list[str]]:
    """:func:`resolve_glob`, plus the ``discovered_from`` list for those files.

    The provenance half is per *file*, never per directory: at the repo root a
    directory entry degenerates to ``"./"``, the marker that widens
    ``source_stale`` to the whole tree permanently (ADR 0017, bd 8ia5, bd
    od2a). :mod:`weld.strategies._provenance` owns that rule; this function
    only composes it, so the two can never disagree.

    Record the whole resolved list, not the subset a run parses: under ADR
    0084 dirty-scoping a strategy parses only the dirty files, and narrowing
    provenance to those would drop every clean sibling's claim on the next
    incremental pass.
    """
    files = resolve_glob(root, pattern, excludes)
    return files, file_provenance(root, files)
