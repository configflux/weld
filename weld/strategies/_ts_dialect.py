"""Which tree-sitter grammar parses a TypeScript file (ADR 0142 D4, bd lrnx1.5).

``tree_sitter_typescript`` ships two grammars, not one. ``language_typescript()``
does not know JSX, so it parses ``<main>`` in a ``.tsx`` file as broken
TypeScript: the tree carries an error, error recovery swallows whatever
declaration the JSX was inside, and the file's exports come back empty. Next.js
puts every page, layout and component behind exactly that shape, so the readiness
probe found a Next.js app whose components reached the graph as nothing at all.

The dialect is a property of the **file**, not of the source entry. ``wd init``
writes one TypeScript entry per repo -- ``**/*.{ts,tsx}``, ``language:
typescript`` -- because ``.ts`` and ``.tsx`` are one language with one module
graph, and a user who splits them by hand still means one language. So the weld
``language`` stays ``typescript`` for both (one symbol namespace, one definition
promotion, one set of finalisers) and only the *grammar* differs per file. The
alternative -- a ``tsx`` language of its own -- would mint
``symbol:tsx:...:Home`` beside ``symbol:typescript:...:formatPrice`` and strand
every cross-dialect call and import resolution on the seam.

``language: tsx`` in a ``discover.yaml`` is therefore read as a spelling of
"TypeScript, JSX dialect" rather than as a language: :func:`canonical_language`
folds it into ``typescript``, and the per-file rule below does the rest. Before
this module it resolved to neither a grammar module (``tree_sitter_tsx`` does
not exist; the TSX grammar lives *inside* ``tree_sitter_typescript``) nor a
query file (``weld/languages/tsx.yaml`` does not exist) -- a config that looked
reasonable, warned about a missing query file and produced nothing.
"""

from __future__ import annotations

from pathlib import Path

#: Weld language names that mean "TypeScript", whatever dialect the file is in.
#: ``tsx`` is accepted for the reason in the module docstring; it is folded to
#: ``typescript`` before anything downstream sees it.
_TYPESCRIPT_LANGUAGES: frozenset[str] = frozenset({"typescript", "tsx"})

#: File suffixes that need the JSX-aware grammar, lower-cased. ``.jsx`` is
#: absent on purpose: it is JavaScript, whose grammar parses JSX natively and
#: whose weld-side support is gap G6's to land, not this module's to presume.
_TSX_SUFFIXES: frozenset[str] = frozenset({".tsx"})

#: The tree-sitter grammar key for the JSX-aware TypeScript grammar.
#: ``weld.strategies._ts_parse`` maps it to ``tree_sitter_typescript`` +
#: ``language_tsx()``.
TSX_GRAMMAR = "tsx"

#: The weld language every TypeScript dialect is recorded as.
TYPESCRIPT_LANGUAGE = "typescript"


def canonical_language(language: str) -> str:
    """Fold a TypeScript dialect spelling onto the weld language name.

    ``tsx`` -> ``typescript``; everything else is returned unchanged, so this
    is safe to call on every source entry regardless of language.
    """
    return TYPESCRIPT_LANGUAGE if language in _TYPESCRIPT_LANGUAGES else language


def grammar_variant(language: str, path: str | Path) -> str:
    """Return the tree-sitter grammar key that should parse *path*.

    ``typescript`` for a ``.ts`` file, :data:`TSX_GRAMMAR` for a ``.tsx`` one,
    and *language* itself for every non-TypeScript language -- the identity
    that lets callers apply this unconditionally.

    The suffix decides, even when the source entry said ``language: tsx``: a
    ``.ts`` file wired under that entry is still plain TypeScript, and the TSX
    grammar rejects the type-assertion syntax (``<T>value``) that only the
    plain one accepts.
    """
    if language not in _TYPESCRIPT_LANGUAGES:
        return language
    suffix = Path(path).suffix.lower()
    return TSX_GRAMMAR if suffix in _TSX_SUFFIXES else TYPESCRIPT_LANGUAGE
