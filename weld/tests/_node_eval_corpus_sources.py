"""File bodies for the Node/Next.js readiness corpus (bd lrnx1.1).

Split from :mod:`weld.tests._node_eval_corpus` so both stay under the
400-line cap: this module is *only* the payload -- what each fixture file
contains -- and the sibling is the layout, the git plumbing and the two
``.weld`` configs.

The workspace these bodies build is the one the readiness probe ran on: an
npm-workspaces monorepo holding a Next.js app-router app, an Express service
written half in TypeScript and half in legacy CommonJS, and a shared workspace
package behind an ``index.ts`` barrel. Unlike the field-eval corpora there is
no evaluator script to be faithful to -- the probe was driven by hand -- so
this module *is* the source of truth, and every shape in it is load-bearing:

* ``page.tsx`` and ``layout.tsx`` are **default exports**, which is what every
  Next.js page, layout and template is (gap G4).
* ``page.tsx`` imports both first-party spellings a Next app uses: the
  workspace package name ``@acme/shared`` and the ``tsconfig`` alias
  ``@/lib/greeting`` (gap G3). Neither is a published package, and neither may
  mint an external package node.
* ``index.ts`` re-exports and declares nothing of its own -- the barrel shape
  a workspace package puts behind its ``main`` (gap G5).
* ``formatPrice`` is called from exactly three TypeScript files and from
  nowhere else, so "who calls me" has a checkable ground truth (gap G2). It is
  deliberately *not* called from ``legacy.js``: JavaScript extraction is its
  own gap (G6) and entangling the two would leave neither probe red for its
  own reason.
* ``route.ts`` exports ``GET`` and ``POST`` under ``app/api/orders/`` -- the
  app-router convention a route node should come from (gap G7).
* ``legacy.js`` is CommonJS end to end: ``require``, a plain function
  declaration, a chained ``.route().get().post()`` registration and
  ``module.exports`` (gap G6, plus the express assurance probe's JS half).
* ``server.ts`` and ``legacy.js`` between them register four express routes
  across both dialects, two of them through the chained form -- the
  pass-today assurance that wiring express still works.

The two ``package.json`` files under ``polyrepo`` are the whole of the G8
fixture: one repo declares a package name, the other declares a dependency on
it, and nothing else in either repo is needed to ask whether npm reaches the
cross-repo package graph.
"""

from __future__ import annotations

# ------------------------------------------------------------------ monorepo

#: The workspaces root. ``scripts`` is what the manifest assurance probe
#: reads; ``workspaces`` is what the G3 fix will read to learn that
#: ``@acme/shared`` is first-party.
ROOT_PACKAGE_JSON = """\
{
  "name": "acme-web-platform",
  "private": true,
  "version": "0.1.0",
  "workspaces": [
    "apps/*",
    "services/*",
    "packages/*"
  ],
  "scripts": {
    "build": "next build",
    "test": "vitest run",
    "lint": "eslint ."
  }
}
"""

ROOT_README = """\
# Acme Web Platform

An npm-workspaces monorepo: a Next.js storefront, an Express API service, and
a shared money-formatting package.
"""

ROOT_GITIGNORE = """\
node_modules/
.next/
"""

# ------------------------------------------------------------- apps/web

WEB_PACKAGE_JSON = """\
{
  "name": "@acme/web",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build"
  },
  "dependencies": {
    "@acme/shared": "*",
    "next": "15.0.3",
    "react": "18.3.1"
  }
}
"""

#: ``baseUrl`` + ``paths`` is the alias configuration ``create-next-app``
#: writes. ``@/lib/greeting`` in ``page.tsx`` resolves through it and through
#: nothing else.
WEB_TSCONFIG_JSON = """\
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "esnext",
    "moduleResolution": "bundler",
    "jsx": "preserve",
    "strict": true,
    "baseUrl": ".",
    "paths": {
      "@/*": [
        "src/*"
      ]
    }
  },
  "include": [
    "src"
  ]
}
"""

#: The Next.js detection marker a framework detector would key on, alongside
#: the ``next`` dependency above. Deliberately ``.mjs``: that is what
#: ``create-next-app`` writes, and it is not a file any source glob in the
#: fixture claims.
WEB_NEXT_CONFIG_MJS = """\
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
"""

#: Gap G4's file, and gap G3's: a default-exported component whose two imports
#: are both first-party spellings.
WEB_PAGE_TSX = """\
import { formatPrice } from "@acme/shared";
import { greeting } from "@/lib/greeting";

export default function Home() {
  const price = formatPrice(1299);
  return (
    <main>
      <h1>{greeting("storefront")}</h1>
      <p>{price}</p>
    </main>
  );
}
"""

WEB_LAYOUT_TSX = """\
export default function RootLayout({ children }: { children: unknown }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
"""

WEB_GREETING_TS = """\
export function greeting(name: string): string {
  return `Hello, ${name}`;
}
"""

#: Gap G7's file: app-router route handlers, named for their HTTP verb, whose
#: URL path is the directory chain that contains them.
#:
#: It carries gap G3's two first-party imports as well as ``page.tsx`` does,
#: and that duplication is deliberate: this is a plain ``.ts`` file, so its
#: import evidence survives whatever the ``.tsx`` grammar does to
#: ``page.tsx``'s. G3's probe reads it here for that reason -- a first-party
#: identity probe that went red because a JSX parse dropped the import would
#: be reproducing G4, not G3.
WEB_ROUTE_TS = """\
import { formatPrice } from "@acme/shared";
import { greeting } from "@/lib/greeting";

export async function GET(): Promise<Response> {
  return Response.json({ label: greeting("orders"), total: formatPrice(4200) });
}

export async function POST(request: Request): Promise<Response> {
  const body = await request.json();
  return Response.json({ total: formatPrice(body.cents) });
}
"""

# --------------------------------------------------------- services/api

API_PACKAGE_JSON = """\
{
  "name": "@acme/api",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "start": "node dist/server.js",
    "test": "vitest run"
  },
  "dependencies": {
    "@acme/shared": "*",
    "express": "4.21.1"
  }
}
"""

#: Two direct-verb express registrations, plus the third TypeScript call site
#: of ``formatPrice``.
API_SERVER_TS = """\
import express from "express";
import { formatPrice } from "@acme/shared";

export const app = express();

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.post("/orders", (req, res) => {
  res.json({ total: formatPrice(req.body.cents) });
});

export function startServer(port: number): void {
  app.listen(port);
}
"""

#: Gap G6's file. CommonJS from top to bottom: ``require`` for both a
#: third-party and a first-party name, a plain function declaration, a chained
#: ``.route().get().post()`` registration, and ``module.exports``. The chain is
#: written on one line because that is the shape the express strategy's
#: chained-verb scan recognises.
API_LEGACY_JS = """\
const express = require("express");
const { CURRENCY } = require("@acme/shared");

const router = express.Router();

function renderOrder(order) {
  return { id: order.id, currency: CURRENCY };
}

router.route("/legacy/orders").get((req, res) => res.json(renderOrder(req.order))).post((req, res) => res.status(201).json(renderOrder(req.body)));

module.exports = { router, renderOrder };
"""

# -------------------------------------------------------- packages/shared

SHARED_PACKAGE_JSON = """\
{
  "name": "@acme/shared",
  "version": "0.1.0",
  "main": "index.ts",
  "types": "index.ts"
}
"""

#: Gap G5's file: re-exports only, declares nothing. It is what ``main`` points
#: at, so every ``@acme/shared`` import in the workspace lands here first.
SHARED_INDEX_TS = """\
export { CURRENCY, formatPrice, PriceFormatter } from "./money";
export type { Money } from "./money";
"""

#: The definition side of gap G2, and the whole of the named-export assurance
#: probe: one const, one interface, one function, one class -- four kinds a
#: TypeScript reader should see.
SHARED_MONEY_TS = """\
export const CURRENCY = "USD";

export interface Money {
  cents: number;
  currency: string;
}

export function formatPrice(cents: number): string {
  return `${(cents / 100).toFixed(2)} ${CURRENCY}`;
}

export class PriceFormatter {
  readonly currency: string;

  constructor(currency: string) {
    this.currency = currency;
  }

  describe(money: Money): string {
    return `${money.cents} ${this.currency}`;
  }
}
"""

# ------------------------------------------------------------------ polyrepo

#: Gap G8's producer: a repo whose published name lives only in its
#: ``package.json``.
UI_KIT_PACKAGE_JSON = """\
{
  "name": "@acme/ui-kit",
  "version": "1.2.0",
  "main": "index.ts",
  "dependencies": {
    "react": "18.3.1"
  }
}
"""

UI_KIT_INDEX_TS = """\
export function button(label: string): string {
  return `[${label}]`;
}
"""

#: Gap G8's consumer: it declares the producer's name as a runtime dependency
#: and nothing else joins the two repos.
STOREFRONT_PACKAGE_JSON = """\
{
  "name": "@acme/storefront",
  "version": "0.3.0",
  "dependencies": {
    "@acme/ui-kit": "^1.2.0",
    "react": "18.3.1"
  },
  "devDependencies": {
    "vitest": "2.1.4"
  }
}
"""

STOREFRONT_MAIN_TS = """\
import { button } from "@acme/ui-kit";

export function render(): string {
  return button("Buy");
}
"""
