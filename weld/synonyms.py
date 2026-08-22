"""Domain-aware synonym expansion for wd query.

Maps conceptual terms to their codebase-specific equivalents so that queries
like 'authentication' also match nodes containing 'auth', 'login', 'session',
etc.  The synonym table is a plain Python dict with zero external dependencies.

The expansion happens BEFORE the inverted index lookup: ``expand_tokens()``
transforms the user's query tokens into an expanded set, and each expanded
alternative is tried independently (OR semantics within a synonym group).

"""

from __future__ import annotations

from weld.query_index import SEPARATOR_CHARS

# ---------------------------------------------------------------------------
# Synonym table: conceptual term -> list of aliases
#
# Each key is a conceptual term that users might search for.  The values are
# alternative tokens that commonly appear in codebases for that concept.
# Keep entries lowercase.  Target ~50-100 entries total.
# ---------------------------------------------------------------------------

SYNONYMS: dict[str, list[str]] = {
    # Authentication & authorization
    "authentication": ["auth", "login", "session", "token", "credential", "jwt", "oauth"],
    "authorization": ["auth", "authz", "permission", "role", "acl", "rbac", "policy"],
    "login": ["auth", "signin", "sign_in", "authentication", "credential"],
    "logout": ["signout", "sign_out", "session"],
    "session": ["auth", "token", "cookie", "jwt"],
    "token": ["jwt", "auth", "bearer", "credential", "session"],

    # Database & storage
    "database": ["db", "sql", "postgres", "sqlite", "mysql", "schema", "migration", "alembic", "model"],
    "migration": ["alembic", "schema", "migrate", "upgrade", "downgrade", "db"],
    "schema": ["model", "table", "column", "migration", "db", "entity"],
    "sql": ["query", "db", "database", "postgres", "sqlite"],
    "storage": ["db", "database", "store", "cache", "redis", "s3", "bucket"],
    "model": ["schema", "entity", "table", "orm", "db"],

    # Pipeline & workers
    "pipeline": ["worker", "stage", "acquire", "extract", "match", "notify", "task", "job", "queue"],
    "worker": ["pipeline", "task", "job", "queue", "celery", "process"],
    "queue": ["worker", "task", "job", "celery", "redis", "pipeline"],
    "task": ["worker", "job", "queue", "pipeline", "schedule"],
    "job": ["worker", "task", "queue", "pipeline"],
    "startup": ["entrypoint", "main", "program", "launch", "boot", "run", "runtime", "execution"],
    "start": ["startup", "entrypoint", "main", "program", "launch", "boot", "run"],
    "entrypoint": ["startup", "main", "program", "launch", "boot", "run"],
    "execution": ["startup", "entrypoint", "flow", "run", "call", "invoke"],
    "flow": ["trace", "path", "execution", "call", "invoke", "startup"],

    # API & HTTP
    "api": ["endpoint", "route", "handler", "rest", "http", "request", "response", "controller"],
    "endpoint": ["route", "handler", "api", "url", "path"],
    "route": ["endpoint", "handler", "url", "path", "api"],
    "request": ["http", "api", "handler", "middleware"],
    "response": ["http", "api", "handler", "status"],
    "middleware": ["handler", "interceptor", "filter", "auth"],

    # Testing
    "test": ["spec", "fixture", "mock", "assert", "unittest", "pytest"],
    "fixture": ["test", "mock", "factory", "seed", "sample"],
    "mock": ["stub", "fake", "fixture", "test", "patch"],

    # Configuration & deployment
    "config": ["settings", "env", "configuration", "dotenv", "yaml", "toml"],
    "deploy": ["deployment", "ci", "cd", "release", "docker", "k8s", "helm"],
    "docker": ["container", "dockerfile", "compose", "image", "deploy"],
    "ci": ["cd", "github_actions", "workflow", "pipeline", "deploy", "build"],

    # Frontend
    "frontend": ["web", "ui", "react", "component", "page", "view"],
    "component": ["ui", "react", "widget", "element", "web"],
    "page": ["route", "view", "screen", "component", "web"],

    # Error handling & logging
    "error": ["exception", "fault", "failure", "raise", "catch", "handler"],
    "logging": ["log", "logger", "audit", "trace", "debug"],
    "log": ["logging", "logger", "audit", "trace"],

    # Security
    "security": ["auth", "encryption", "secret", "vulnerability", "sanitize", "xss", "csrf", "ssrf"],
    "secret": ["credential", "key", "password", "env", "vault"],
    "encryption": ["encrypt", "decrypt", "hash", "cipher", "ssl", "tls"],

    # Data processing
    "extract": ["parse", "scrape", "transform", "etl", "acquisition"],
    "transform": ["convert", "map", "parse", "etl", "process"],
    "notification": ["notify", "alert", "email", "webhook", "push"],

    # Build & tooling
    "build": ["bazel", "compile", "bundle", "webpack", "make"],
    "lint": ["format", "style", "eslint", "ruff", "flake8", "prettier"],
    "dependency": ["import", "require", "package", "module", "dep"],

    # Domain entities
    "store": ["retailer", "supermarket", "chain", "location", "shop"],
    "retailer": ["store", "supermarket", "chain", "vendor"],
    "flyer": ["circular", "ad", "promotion", "deal", "offer"],
    "product": ["item", "sku", "grocery", "good"],
    "price": ["cost", "deal", "discount", "promotion", "match"],

    # Documentation
    "documentation": ["doc", "docs", "readme", "guide", "manual", "adr"],
    "doc": ["documentation", "docs", "readme", "guide"],
}

def expand_tokens(tokens: list[str]) -> list[str]:
    """Expand query tokens using the synonym table.

    For each token, if it appears as a key in ``SYNONYMS``, the token and all
    its aliases are included in the result.  Unknown tokens pass through
    unchanged.  The result is deduplicated and lowercased.

    This is intended to be called on the raw query tokens *before* the
    inverted-index lookup so that conceptual queries match related terms.
    """
    if not tokens:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for tok in tokens:
        tok_lower = tok.lower()
        if tok_lower not in seen:
            seen.add(tok_lower)
            result.append(tok_lower)
        for alias in SYNONYMS.get(tok_lower, []):
            alias_lower = alias.lower()
            if alias_lower not in seen:
                seen.add(alias_lower)
                result.append(alias_lower)
    return result

_VOWELS = frozenset("aeiou")
# -s endings that are not naive plurals (over-stemming guards).
_NON_PLURAL_S = ("ss", "us", "is", "as", "ies")

def _stem_variants(token: str) -> list[str]:
    """Return singular/plural stem-equivalents of *token* (no external deps).

    A deliberately small, symmetric two-rule heuristic (ADR 0075 part 3) so
    a query group like ``strategy`` also matches the path token
    ``strategies`` (``strategy`` is **not** a substring of ``strategies``):

    * Rule 1 -- ``-ies <-> -y`` for a consonant + ``y`` stem
      (``strategy`` <-> ``strategies``, ``entry`` <-> ``entries``);
    * Rule 2 -- the simple ``-s <-> (null)`` plural
      (``test`` <-> ``tests``).

    Over-stemming is guarded by minimum lengths and by excluding the
    non-plural ``-ss``/``-us``/``-is``/``-as`` endings, so short or
    coincidental tokens (``is``, ``css``, ``status``, ``db``) yield nothing.
    The input token itself is never returned.  Intentionally not a full
    stemmer -- this is the minimal bridge ADR 0075 specifies.
    """
    t = token.lower()
    n = len(t)
    out: set[str] = set()
    # Rule 1: -ies <-> -y (consonant + y).
    if t.endswith("ies") and n >= 5:
        out.add(t[:-3] + "y")
    elif t.endswith("y") and n >= 4 and t[-2] not in _VOWELS:
        out.add(t[:-1] + "ies")
    # Rule 2: simple -s plural (both directions).
    if t.endswith("s") and n >= 4 and not t.endswith(_NON_PLURAL_S):
        out.add(t[:-1])
    elif not t.endswith("s") and not t.endswith("y") and n >= 3:
        out.add(t + "s")
    out.discard(t)
    return sorted(out)

# Function-word stopwords dropped from MULTI-token queries before strict-AND so
# the content-bearing tokens drive matching (and the leading content token
# becomes the OR-fallback "subject", ``token_groups[0]``). Deliberately tight:
# articles, a few prepositions, demonstratives, copula/auxiliary verbs, and WH
# question words -- and NOTHING content-ish (``work``/``test``/``set`` name code
# and must survive). All lowercase; the query paths lowercase before tokenizing.
_QUERY_STOPWORDS: frozenset[str] = frozenset({
    # articles
    "a", "an", "the",
    # common prepositions
    "of", "to", "for", "in", "on",
    # demonstratives / expletive pronouns
    "it", "this", "that",
    # copula / auxiliary verbs
    "is", "are", "does", "do",
    # WH question words
    "how", "where", "what", "when", "why", "who", "which", "whose", "whom",
})

def filter_stopwords(tokens: list[str]) -> list[str]:
    """Drop function-word stopwords from a MULTI-token query, order-preserved.

    Removes only tokens in :data:`_QUERY_STOPWORDS` so natural-language /
    conceptual queries ("how does auth work") match on their content tokens
    instead of padding strict-AND with function words and dumping to the noisy
    OR-fallback. Two guards keep already-good queries byte-identical:

    * a single-token query is returned unchanged -- a lone token (even a
      stopword like ``"the"``) is never stripped, and a bare symbol / lexical
      query is exactly one token, so single-token/symbol results do not move;
    * an all-stopword query (nothing content-bearing survives) is returned
      unchanged, so a degenerate ``"how does it"`` keeps its prior behaviour
      instead of collapsing to an empty, match-everything token set.

    Applied at the single :func:`expand_token_groups` chokepoint (below) so
    every query path -- the JSON ``Graph`` read path, its sqlite peer, and
    federation -- filters identically and their rank/hit contracts cannot
    drift. Deterministic: a fixed-set membership test over an order-preserving
    comprehension. Expects the pre-lowercased tokens the query paths produce.
    """
    if len(tokens) <= 1:
        return list(tokens)
    kept = [tok for tok in tokens if tok not in _QUERY_STOPWORDS]
    return kept if kept else list(tokens)

def _separator_variants(token: str) -> list[str]:
    """Return same-alphabet re-spellings of *token* (bd pxjc, widened bd 2xoj).

    Humans type a name the way its project spells it; code spells it the way
    the language allows. ``wd query "tree-sitter availability gate"`` returned
    no tree-sitter node at all -- not a ranking failure, a *match* failure:
    every node is spelled ``tree_sitter``, and ``tree-sitter`` is not a
    substring of it, so nothing was ever a candidate. The same query with an
    underscore returns ``file:weld/strategies/tree_sitter`` first.

    bd pxjc's fix swapped only ``-``/``_``, two of the six characters
    ``weld.query_index.SEPARATOR_CHARS`` actually splits an indexed field on.
    A query token spelled with ``.``, ``/``, ``:`` or ``·`` inherited the same
    match failure one separator further out (``'graph.json'`` could not reach
    a node spelled ``graph_json``, and vice versa -- bd 2xoj). This widens the
    rule to the whole alphabet, imported from :mod:`weld.query_index` rather
    than re-declared here, so the two cannot drift apart silently again.

    THE RULE -- canonical-form join, bounded, deterministic. A token with no
    separator character yields nothing (the overwhelming majority -- the
    early return keeps that case free). Otherwise: replace every separator
    character in the token with one pivot character to get a canonical form,
    then, for each of the alphabet's characters in turn, respell that
    canonical form using it in place of the pivot. Whichever respelling
    reproduces the input verbatim is the token itself and is dropped, not
    returned. The output is therefore at most ``len(SEPARATOR_CHARS)`` entries
    -- *always*, regardless of how many separators or how many distinct
    separator characters the input token contains, which is what keeps this
    O(1) per token instead of combinatorial (a token with k separator
    occurrences does not get more variants than a token with one).

    Variants join the *same* group, so they are OR-ed with the raw token and
    never add an AND clause. They are re-spellings of the WHOLE token only --
    never the punctuation-split parts (``'graph.json'`` never yields ``'graph'``
    or ``'json'`` on their own). Adding the parts would let a token match a
    subject that merely mentions one fragment in passing (``weld/serializer``
    exports ``dumps_graph``, which contains ``'graph'``), which is the
    cosmetic-match shape ADR 0113 rejects for a sibling mechanism -- the index
    already carries those parts for nodes genuinely about just one fragment,
    and widening the query side to match them too would turn a compound-token
    query into an unrelated bag-of-words query.

    A token that is NOTHING BUT separator characters (``'_'``, ``'---'``) is
    not a compound name and is also skipped, returning ``[]``: the canonical
    form of an all-punctuation token collapses to a run of one repeated
    character, so respelling it would hand back single-character "variants"
    like ``'/'`` or ``'.'`` -- and a lone punctuation character is a substring
    of nearly every indexed token in a real graph (any file path contains
    ``'/'``, any node id contains ``':'``), so trying it as a query token
    widens toward matching everything. ``weld_sqlite_query_test.py``'s
    injection-probe suite pins the observable contract this guards
    (``wd query "_"`` must stay empty, the same as the sibling ``'%'`` probe)
    -- SQL ``LIKE`` metacharacter escaping was never the mechanism here, so
    widening candidacy could reopen it by a different door.

    The token itself is never returned.
    """
    t = token.lower()
    if not any(ch in t for ch in SEPARATOR_CHARS):
        return []
    if all(ch in SEPARATOR_CHARS for ch in t):
        return []
    pivot = SEPARATOR_CHARS[0]
    canonical = t
    for ch in SEPARATOR_CHARS[1:]:
        canonical = canonical.replace(ch, pivot)
    out: list[str] = []
    for sep in SEPARATOR_CHARS:
        variant = canonical.replace(pivot, sep)
        if variant != t and variant not in out:
            out.append(variant)
    return out


def expand_token_groups(tokens: list[str]) -> list[list[str]]:
    """Expand each token into a group of [itself + synonym aliases + stems].

    Returns one group per original token.  Used by ``Graph.query()`` so
    that synonym alternatives are OR-ed within a group and AND-ed across
    groups (multi-token queries still require every original concept).

    Each group also gains the singular/plural stem-equivalents of the raw
    token (:func:`_stem_variants`) so a singular query (``strategy``) covers
    a plural path token (``strategies``) within the same group -- ADR 0075
    part 3.  Stems join the same group, so this never adds a new AND clause.

    It likewise gains the ``-``/``_`` re-spellings (:func:`_separator_variants`)
    so a query typed the way a project spells its name (``tree-sitter``) covers
    the way the language spells it (``tree_sitter``) -- bd pxjc.

    Element 0 of every returned group is the raw token the user typed, before
    any alias, stem or separator variant. :func:`weld._test_paths.
    query_names_tests` and :func:`weld._issue_concepts.query_names_backlog`
    both read that position to decide whether their demotion applies, so the
    ordering here is load-bearing rather than incidental: appending is what
    keeps one synonym table from silently widening those guards.

    Function-word stopwords are dropped first (:func:`filter_stopwords`) so a
    natural-language query ("how does auth work") yields groups only for its
    content tokens. This is the single chokepoint all query paths route
    through, so the JSON / sqlite / federation surfaces filter identically.
    """
    tokens = filter_stopwords(tokens)
    groups: list[list[str]] = []
    for tok in tokens:
        group = [tok]
        seen = {tok}
        for alias in (
            list(SYNONYMS.get(tok, []))
            + _stem_variants(tok)
            + _separator_variants(tok)
        ):
            a = alias.lower()
            if a not in seen:
                seen.add(a)
                group.append(a)
        groups.append(group)
    return groups

def candidate_nodes_grouped(
    index: dict[str, set[str]],
    token_groups: list[list[str]],
) -> set[str] | None:
    """Return candidate node IDs using synonym-expanded token groups.

    For each group, collects the union of nodes matching ANY token in that
    group (substring match in the inverted index).  Intersects across groups
    so multi-token queries require every original concept to match.

    Returns ``None`` when the index is empty (caller should full-scan).
    """
    if not index:
        return None
    result: set[str] | None = None
    for group in token_groups:
        group_hits: set[str] = set()
        for tok in group:
            for indexed_token, node_ids in index.items():
                if tok in indexed_token:
                    group_hits |= node_ids
        if result is None:
            result = group_hits
        else:
            result &= group_hits
        if not result:
            return set()
    return result
