"""A four-framework boundary-file corpus: one repo, three wirings (bd iurvv).

Every route strategy in this tree that cannot name a router *symbol* hangs its
diagnostic ``exposes`` edge off the boundary **file** node and mints a thin
``file:`` placeholder so that edge cannot dangle when the strategy runs alone.
Four do it -- ``express`` and ``next`` through the shared
:mod:`weld.strategies._ts_route_helpers`, ``axum`` and ``gin`` through their own
copies -- so the corpus carries one boundary file per framework, in the language
that framework belongs to, each with real definitions and real imports for the
tree-sitter pass to find.

The three configurations are the whole point, and they differ **only** in the
order the source entries are declared:

* :data:`ROUTES_LAST` -- the tree-sitter entries first, the route entries after.
  The adversarial order, and the one a user reaches by appending a framework to
  a config that already claimed the language.
* :data:`ROUTES_FIRST` -- the order ``wd init`` itself emits (ADR 0071), and the
  order the Node readiness corpus hand-wires for the same reason.
* :data:`ROUTES_ONLY` -- no tree-sitter entry at all. The placeholder is the
  only claim on the file id, which is the case it was minted for.

A correct discovery answers the first two identically, and keeps the placeholder
in the third. Stating the entries once and composing the three orders from them
is deliberate: an order-independence probe whose three configs were retyped
could differ somewhere other than the order and prove nothing about it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

#: Hermetic git: own identity, no ambient user config, stable locale. The same
#: shape ``_node_eval_corpus.GIT_ENV`` uses, and for the same reason -- a repo
#: that inherits the developer's git config is not the repo the gate ran on.
GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}


class Boundary(NamedTuple):
    """One framework's boundary file and the evidence a discovery owes it.

    ``exports`` / ``imports`` / ``import_targets`` are what the **tree-sitter**
    pass records on the file node; ``routes`` are the ids the **route** strategy
    mints and hangs its ``exposes`` edge on. A probe asserts both halves on one
    run, because the defect is precisely that having the second costs the first.
    """

    strategy: str
    path: str
    exports: frozenset[str]
    imports: frozenset[str]
    import_targets: dict[str, str]
    routes: frozenset[str]


#: The express service. ``@shared/money`` is a ``tsconfig`` path alias, which
#: is what makes ``props.import_targets`` non-empty (ADR 0142 D3): a *relative*
#: import is bound by the graph closure and never recorded on the node, so a
#: corpus that imported only relatively could not see that half disappear.
EXPRESS_SERVER_TS = """\
import express from "express";
import { formatPrice } from "@shared/money";

export const app = express();

app.get("/orders", (req, res) => res.json({ total: formatPrice(1) }));
app.post("/orders", (req, res) => res.status(201).end());

export function startServer(port: number): void {
  app.listen(port);
}
"""

#: The Next.js app-router handler. Two exported HTTP-method functions, so the
#: app-router file convention mints two routes off one file.
NEXT_ROUTE_TS = """\
import { formatPrice } from "@shared/money";

export async function GET(): Promise<Response> {
  return Response.json({ price: formatPrice(2) });
}

export async function POST(): Promise<Response> {
  return new Response(null, { status: 201 });
}
"""

#: The shared module both TypeScript boundaries import through the alias.
SHARED_MONEY_TS = """\
export function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
"""

TSCONFIG_JSON = """\
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@shared/*": ["shared/*"]
    }
  }
}
"""

#: The axum router. ``pub`` throughout: the Rust ``exports`` query captures a
#: definition only through its visibility modifier, so a private helper would
#: leave the file with no exports and the tree-sitter pass would skip it --
#: which would make the probe pass for the wrong reason.
AXUM_ROUTES_RS = """\
use axum::routing::get;
use axum::Router;

pub struct AppState {
    pub name: String,
}

pub fn router() -> Router {
    Router::new().route("/health", get(health))
}

pub fn health() -> &'static str {
    "ok"
}
"""

#: The gin registrar. The ``github.com/gin-gonic/gin`` import is not decoration:
#: the strategy gates extraction on it so an unrelated ``.GET(...)`` callsite
#: cannot over-fire. Indentation is written as ``\t`` escapes rather than as
#: literal tabs, because gofmt indents with tabs and the repo linter refuses a
#: literal tab in a Python source file -- so the fixture stays the Go a user
#: would actually have on disk without breaking the file that carries it.
GIN_ROUTES_GO = (
    "package handlers\n"
    "\n"
    "import (\n"
    '\t"net/http"\n'
    "\n"
    '\t"github.com/gin-gonic/gin"\n'
    ")\n"
    "\n"
    "func Register(r *gin.Engine) {\n"
    '\tr.GET("/ping", Ping)\n'
    "}\n"
    "\n"
    "func Ping(c *gin.Context) {\n"
    '\tc.String(http.StatusOK, "pong")\n'
    "}\n"
)

#: Every boundary file, one per framework, keyed by the route strategy that
#: claims it. Ordered as the assertions read them.
BOUNDARIES: tuple[Boundary, ...] = (
    Boundary(
        strategy="express",
        path="api/server.ts",
        exports=frozenset({"app", "startServer"}),
        imports=frozenset({"express", "@shared/money"}),
        import_targets={"@shared/money": "shared/money.ts"},
        routes=frozenset({"route:GET:/orders", "route:POST:/orders"}),
    ),
    Boundary(
        strategy="next",
        path="web/app/api/orders/route.ts",
        exports=frozenset({"GET", "POST"}),
        imports=frozenset({"@shared/money"}),
        import_targets={"@shared/money": "shared/money.ts"},
        routes=frozenset({"route:GET:/api/orders", "route:POST:/api/orders"}),
    ),
    Boundary(
        strategy="axum",
        path="svc/src/routes.rs",
        exports=frozenset({"AppState", "router", "health"}),
        imports=frozenset({"axum"}),
        import_targets={},
        routes=frozenset({"route:GET:/health"}),
    ),
    Boundary(
        strategy="gin",
        path="svc/handlers/routes.go",
        exports=frozenset({"Register", "Ping"}),
        imports=frozenset({"net/http", "github.com/gin-gonic/gin"}),
        import_targets={},
        routes=frozenset({"route:GET:/ping"}),
    ),
)

BOUNDARY_BY_STRATEGY: dict[str, Boundary] = {b.strategy: b for b in BOUNDARIES}

#: Every file the repository contains, keyed by its repo-relative path.
FILES: dict[str, str] = {
    "tsconfig.json": TSCONFIG_JSON,
    "shared/money.ts": SHARED_MONEY_TS,
    "api/server.ts": EXPRESS_SERVER_TS,
    "web/app/api/orders/route.ts": NEXT_ROUTE_TS,
    "svc/src/routes.rs": AXUM_ROUTES_RS,
    "svc/handlers/routes.go": GIN_ROUTES_GO,
}

# --------------------------------------------------------------- the wirings

#: The language entries. ``emit_calls`` is on for TypeScript so the file node
#: carries the same evidence a real Node config produces.
_TREE_SITTER_ENTRIES = """\
  - glob: "**/*.ts"
    type: file
    strategy: tree_sitter
    language: typescript
    emit_calls: true

  - glob: "**/*.rs"
    type: file
    strategy: tree_sitter
    language: rust

  - glob: "**/*.go"
    type: file
    strategy: tree_sitter
    language: go
"""

#: The framework entries, one per boundary above.
_ROUTE_ENTRIES = """\
  - glob: "**/*.ts"
    type: route
    strategy: express

  - glob: "**/*.ts"
    type: route
    strategy: next

  - glob: "**/*.rs"
    type: route
    strategy: axum

  - glob: "**/*.go"
    type: route
    strategy: gin
"""

_HEADER = "version: 1\nsources:\n"

#: Language first, frameworks appended -- the order that reproduces bd iurvv.
ROUTES_LAST: str = _HEADER + _TREE_SITTER_ENTRIES + "\n" + _ROUTE_ENTRIES

#: Frameworks first -- the order ``wd init`` emits, which masked the defect.
ROUTES_FIRST: str = _HEADER + _ROUTE_ENTRIES + "\n" + _TREE_SITTER_ENTRIES

#: Frameworks only: the placeholder is the sole claim on each boundary file id.
ROUTES_ONLY: str = _HEADER + _ROUTE_ENTRIES


def materialize(root: Path, config: str) -> Path:
    """Write the corpus and *config* into *root* as a committed git repo."""
    for rel, body in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text(config, encoding="utf-8")
    _git_init(root)
    return root


def _git_init(root: Path) -> None:
    """A real git repository, so the repo-boundary snapshot is not empty."""
    for argv in (
        ["init", "--quiet"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
        ["add", "-A"],
        ["commit", "-m", "corpus", "--quiet"],
    ):
        proc = subprocess.run(
            ["git", *argv], cwd=str(root), env=GIT_ENV,
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:  # pragma: no cover - a broken git
            raise AssertionError(
                f"git {' '.join(argv)} failed in {root}:\n{proc.stderr}"
            )


__all__ = [
    "BOUNDARIES",
    "BOUNDARY_BY_STRATEGY",
    "Boundary",
    "FILES",
    "ROUTES_FIRST",
    "ROUTES_LAST",
    "ROUTES_ONLY",
    "materialize",
]
