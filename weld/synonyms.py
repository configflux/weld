"""Domain-aware synonym expansion for wd query.

Maps conceptual terms to their codebase-specific equivalents so that queries
like 'authentication' also match nodes containing 'auth', 'login', 'session',
etc.  The synonym table is a plain Python dict with zero external dependencies.

The expansion happens BEFORE the inverted index lookup: ``expand_tokens()``
transforms the user's query tokens into an expanded set, and each expanded
alternative is tried independently (OR semantics within a synonym group).

"""

from __future__ import annotations

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

def expand_token_groups(tokens: list[str]) -> list[list[str]]:
    """Expand each token into a group of [itself + synonym aliases].

    Returns one group per original token.  Used by ``Graph.query()`` so
    that synonym alternatives are OR-ed within a group and AND-ed across
    groups (multi-token queries still require every original concept).
    """
    groups: list[list[str]] = []
    for tok in tokens:
        group = [tok]
        seen = {tok}
        for alias in SYNONYMS.get(tok, []):
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
