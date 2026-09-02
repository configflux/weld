"""Materialises the Node/Next.js readiness corpus on disk (bd lrnx1.1).

Two workspaces, because the readiness probe asked two different questions.

**The monorepo** is one git repository laid out as npm workspaces: a Next.js
app-router app under ``apps/web`` (``src/`` layout, ``tsconfig`` path
aliases), an Express service under ``services/api`` written half in TypeScript
and half in legacy CommonJS, and a shared workspace package under
``packages/shared`` behind an ``index.ts`` barrel. File bodies live next door
in :mod:`weld.tests._node_eval_corpus_sources`; the layout, the git plumbing
and the configs are here.

**The polyrepo** is a workspace root federating two tiny child repos -- one
declaring a package name in its ``package.json``, one declaring a dependency
on that name. It exists only for the cross-repo question (gap G8), so it
carries nothing else.

Two configurations of the monorepo, and the difference is the whole reason
this module writes a config at all:

* :func:`write_init_config` writes nothing -- the caller runs ``wd init`` and
  reads what the product itself generated. That is the only honest way to ask
  gap G1 ("init wires what it detects"), and it is what
  ``weld_node_eval_init_e2e_test`` does.
* :func:`write_wired_config` overwrites ``.weld/discover.yaml`` with
  :data:`WIRED_DISCOVER_YAML` -- every dialect claimed, call evidence on, both
  framework strategies and the manifests wired. This is the *generous*
  configuration, the one a user reaches after working around G1 by hand, and
  the probes for gaps G2-G7 run on top of it on purpose: a probe that ran on
  the stock config would be red because init did not wire the file, which is
  G1's finding and not its own. Red for its own reason is the whole contract
  here.

Nothing in either configuration is a workaround the fixes may assume: when G1
lands, ``wd init`` is expected to *produce* the wiring below, and the G1 probe
is what will say so.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from weld.tests import _node_eval_corpus_sources as src

#: Hermetic git: own identity, no ambient user config, stable locale. Mirrors
#: ``_field_eval_corpus_fixture.GIT_ENV`` -- see that module for why this is
#: not optional.
GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}

#: Every file in the monorepo, keyed by its path relative to the repo root.
MONOREPO_FILES: dict[str, str] = {
    "package.json": src.ROOT_PACKAGE_JSON,
    "README.md": src.ROOT_README,
    ".gitignore": src.ROOT_GITIGNORE,
    "apps/web/package.json": src.WEB_PACKAGE_JSON,
    "apps/web/tsconfig.json": src.WEB_TSCONFIG_JSON,
    "apps/web/next.config.mjs": src.WEB_NEXT_CONFIG_MJS,
    "apps/web/src/app/page.tsx": src.WEB_PAGE_TSX,
    "apps/web/src/app/layout.tsx": src.WEB_LAYOUT_TSX,
    "apps/web/src/app/api/orders/route.ts": src.WEB_ROUTE_TS,
    "apps/web/src/lib/greeting.ts": src.WEB_GREETING_TS,
    "services/api/package.json": src.API_PACKAGE_JSON,
    "services/api/src/server.ts": src.API_SERVER_TS,
    "services/api/src/legacy.js": src.API_LEGACY_JS,
    "packages/shared/package.json": src.SHARED_PACKAGE_JSON,
    "packages/shared/index.ts": src.SHARED_INDEX_TS,
    "packages/shared/money.ts": src.SHARED_MONEY_TS,
}

#: The file that defines ``formatPrice`` and the three that call it. Ground
#: truth for gap G2, stated once here so the probe compares against the
#: fixture rather than against a list retyped beside the assertion.
FORMAT_PRICE = "formatPrice"
FORMAT_PRICE_FILE = "packages/shared/money.ts"
FORMAT_PRICE_CALLER_FILES: frozenset[str] = frozenset({
    "apps/web/src/app/page.tsx",
    "apps/web/src/app/api/orders/route.ts",
    "services/api/src/server.ts",
})

#: Gap G3, as ``{import specifier: the file it must bind to}``, read out of
#: ``route.ts`` -- a plain ``.ts`` file, so its import evidence does not depend
#: on how the ``.tsx`` grammar treats ``page.tsx``. ``@acme/shared`` is an npm
#: workspace member name and ``@/lib/greeting`` is a ``tsconfig`` alias;
#: neither is a published package, and the workspace name is checked against
#: the *package directory* rather than one file inside it because ``main``
#: points at the barrel and walking the barrel to ``money.ts`` is gap G5's job.
FIRST_PARTY_IMPORTER = "apps/web/src/app/api/orders/route.ts"
FIRST_PARTY_TARGETS: dict[str, str] = {
    "@acme/shared": "packages/shared/",
    "@/lib/greeting": "apps/web/src/lib/greeting.ts",
}

#: Gap G5: the barrel, and the module it re-exports from. The barrel keeps a
#: file node today; what is missing is any edge a closure could walk from it.
BARREL_FILE = "packages/shared/index.ts"
BARREL_REEXPORT_TARGET = "packages/shared/money.ts"

#: Gap G4: every default-exported component in the Next.js app, and the file
#: that declares it. Both, not just ``page.tsx``: ``layout.tsx``'s default
#: export happens to survive the plain-TypeScript parse of a ``.tsx`` file
#: today, and a probe that asserted only the broken one would let the working
#: one regress unnoticed while the gap was being fixed.
DEFAULT_EXPORT_COMPONENTS: dict[str, str] = {
    "Home": "apps/web/src/app/page.tsx",
    "RootLayout": "apps/web/src/app/layout.tsx",
}

#: Gap G6: the CommonJS file, the function it declares, and the third-party
#: name it ``require``s. ``express`` rather than ``@acme/shared`` on purpose --
#: a require-derived edge to a *first-party* name would also be gap G3, and
#: neither probe would then be red for its own reason.
LEGACY_JS_FILE = "services/api/src/legacy.js"
LEGACY_JS_FUNCTION = "renderOrder"
LEGACY_JS_REQUIRED_PACKAGE = "express"

#: Every express route the fixture registers, as ``(method, path)``. Four
#: across both dialects, two of them through the chained form -- the
#: pass-today assurance probe asserts this set exactly, because "the routes
#: that were found resolve" is true of a run that found none.
EXPRESS_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/health"),
    ("POST", "/orders"),
    ("GET", "/legacy/orders"),
    ("POST", "/legacy/orders"),
})

#: The app-router handler gap G7 is about, and the URL its directory chain
#: spells.
NEXT_ROUTE_FILE = "apps/web/src/app/api/orders/route.ts"
NEXT_ROUTE_PATH = "/api/orders"

#: Every named export of ``money.ts`` -- a const, an interface, a function and
#: a class. The pass-today assurance probe asserts each one reaches the graph
#: as a definite first-party TypeScript symbol; it is green today and the
#: point is that it stays green while the gaps around it are being fixed.
#:
#: Kinds are deliberately *not* asserted, and this is not an oversight:
#: ``_ts_definitions._definition_records`` returns ``(name, None)`` for every
#: non-C# language, and ``tools/tier_check_kinds`` records that state
#: explicitly -- its TypeScript vocabulary is forward-looking, and ``None`` is
#: a non-breach of ADR 0064 criterion 1. Asserting kinds here would mint a
#: ninth red probe for a recorded limitation rather than for a gap.
MONEY_NAMED_EXPORTS: frozenset[str] = frozenset({
    "CURRENCY", "Money", "formatPrice", "PriceFormatter",
})

#: Every ``npm run`` target the manifests declare, as
#: ``(node type, package.json path, script name)``. The manifest assurance
#: probe asserts this set exactly: a subset check would pass a run that found
#: one workspace's scripts and missed the other three.
MANIFEST_SCRIPT_TARGETS: frozenset[tuple[str, str, str]] = frozenset({
    ("build-target", "package.json", "build"),
    ("test-target", "package.json", "test"),
    ("test-target", "package.json", "lint"),
    ("build-target", "apps/web/package.json", "dev"),
    ("build-target", "apps/web/package.json", "build"),
    ("build-target", "services/api/package.json", "start"),
    ("test-target", "services/api/package.json", "test"),
})

#: The generous hand-wiring gaps G2-G7 are probed against. Every dialect the
#: repo contains is claimed, TypeScript emits call evidence, both framework
#: strategies and the build manifests are wired. ``language: javascript`` is
#: the spelling a user would write for a ``.js`` glob and the one ``wd init``
#: is expected to write once G1 lands -- writing ``typescript`` there instead
#: would be the fixture working around G6 rather than probing it.
#:
#: The ``next`` entry is first because that is the order ``wd init`` itself
#: emits a framework strategy in (ADR 0071), and the corpus is meant to look
#: like a config a user would meet. It used to be load-bearing: a route
#: strategy mints a thin boundary-file placeholder for the file it read, the
#: tree-sitter entry mints the canonical ``file:`` node for the same path, and
#: the later entry won the orchestrator merge outright -- so putting ``next``
#: after would have handed the app-router files' file nodes to the placeholder
#: and cost the probes beside it their subject. bd iurvv fixed that (the
#: placeholder states a weaker confidence, so the ADR 0103 veto ranks it and
#: the definite node wins in either order); the ``express`` entry below, which
#: has always sat *after* the tree-sitter entries, is where that defect was
#: measured.
WIRED_DISCOVER_YAML = """\
sources:
  # --- Next.js app-router routes (route nodes per handler export and page) ---
  - glob: "**/*.{ts,tsx,js,jsx,mjs,cjs}"
    type: route
    strategy: next

  # --- TypeScript sources (.ts), with call evidence ---
  - glob: "**/*.ts"
    type: file
    strategy: tree_sitter
    language: typescript
    emit_calls: true

  # --- TypeScript sources (.tsx), with call evidence ---
  - glob: "**/*.tsx"
    type: file
    strategy: tree_sitter
    language: typescript
    emit_calls: true

  # --- JavaScript sources (.js), with call evidence ---
  - glob: "**/*.js"
    type: file
    strategy: tree_sitter
    language: javascript
    emit_calls: true

  # --- express HTTP routes (route nodes per handler registration) ---
  - glob: "**/*.{ts,js}"
    type: route
    strategy: express

  # --- Build manifests (package.json scripts) ---
  - glob: "**/package.json"
    type: config
    strategy: manifest
"""

# ------------------------------------------------------------------ polyrepo

#: ``(child name, path relative to the workspace root)``, ordered as
#: ``workspaces.yaml`` lists them.
POLYREPO_CHILDREN: tuple[tuple[str, str], ...] = (
    ("packages-ui-kit", "packages/ui-kit"),
    ("apps-storefront", "apps/storefront"),
)

#: Which child produces the package name, which consumes it, and the name
#: itself: ground truth for gap G8's single expected join.
UI_KIT_PACKAGE = "@acme/ui-kit"
UI_KIT_CHILD = POLYREPO_CHILDREN[0][0]
STOREFRONT_CHILD = POLYREPO_CHILDREN[1][0]

POLYREPO_FILES: dict[str, dict[str, str]] = {
    "packages/ui-kit": {
        "package.json": src.UI_KIT_PACKAGE_JSON,
        "index.ts": src.UI_KIT_INDEX_TS,
    },
    "apps/storefront": {
        "package.json": src.STOREFRONT_PACKAGE_JSON,
        "src/main.ts": src.STOREFRONT_MAIN_TS,
    },
}

_WORKSPACES_YAML_TEMPLATE = """\
version: 1
scan:
  max_depth: 4
  respect_gitignore: false
children:
{children}cross_repo_strategies: [{cross_repo_strategies}]
"""

_CHILD_ENTRY = "  - name: {name}\n    path: {path}\n"

#: The children are ignored at the root, so the root repo tracks nothing but
#: its own workspace config -- the shape a federation root actually has.
_POLYREPO_ROOT_GITIGNORE = "packages/\napps/\n"

_ROOT_WELD_GITIGNORE = (
    "# Track the shared workspace config; ignore generated local state.\n"
    "*\n"
    "!.gitignore\n"
    "!workspaces.yaml\n"
)


# ------------------------------------------------------------------- plumbing


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=GIT_ENV,
        check=True,
    )


def init_repo(root: Path) -> None:
    """``git init`` with the fixture's own identity (no ambient config)."""
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.com")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "commit.gpgsign", "false")


def commit_all(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def materialize_monorepo(root: Path, *, git: bool = True) -> Path:
    """Lay the npm-workspaces monorepo down under *root*; return *root*.

    *git* is on by default and is not decoration: the manifest scan reads only
    files the repository claims (ADR 0137 s6), and ``wd stale`` answers from
    git, so an un-versioned tree answers different questions than a real
    checkout does.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in MONOREPO_FILES.items():
        _write(root / rel, body)
    if git:
        init_repo(root)
        commit_all(root, "acme web platform")
    return root


def write_wired_config(root: Path) -> Path:
    """Overwrite ``.weld/discover.yaml`` with :data:`WIRED_DISCOVER_YAML`."""
    path = Path(root) / ".weld" / "discover.yaml"
    _write(path, WIRED_DISCOVER_YAML)
    return path


def workspaces_yaml(*, cross_repo_strategies: tuple[str, ...] = ()) -> str:
    """Render the polyrepo root's ``workspaces.yaml``."""
    return _WORKSPACES_YAML_TEMPLATE.format(
        children="".join(
            _CHILD_ENTRY.format(name=name, path=path)
            for name, path in POLYREPO_CHILDREN
        ),
        cross_repo_strategies=", ".join(cross_repo_strategies),
    )


def write_workspaces_yaml(
    root: Path, *, cross_repo_strategies: tuple[str, ...] = ()
) -> Path:
    """Write (or rewrite) the polyrepo root ``workspaces.yaml``."""
    path = Path(root) / ".weld" / "workspaces.yaml"
    _write(path, workspaces_yaml(cross_repo_strategies=cross_repo_strategies))
    return path


def materialize_polyrepo(
    root: Path,
    *,
    git: bool = True,
    cross_repo_strategies: tuple[str, ...] = ("package_graph",),
) -> Path:
    """Lay the two-repo Node polyrepo down under *root*; return *root*.

    The resolver is wired from the start, unlike the field-eval corpus which
    ships the default and lets a probe turn it on: there is exactly one
    question here (does npm reach the package graph at all) and a run with no
    resolver could not ask it.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    _write(root / ".gitignore", _POLYREPO_ROOT_GITIGNORE)
    _write(root / ".weld" / ".gitignore", _ROOT_WELD_GITIGNORE)
    write_workspaces_yaml(root, cross_repo_strategies=cross_repo_strategies)
    if git:
        init_repo(root)
        commit_all(root, "acme storefront workspace root")
    for rel, files in POLYREPO_FILES.items():
        child = root / rel
        child.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            _write(child / name, body)
        if git:
            init_repo(child)
            commit_all(child, f"{rel} initial commit")
    return root
