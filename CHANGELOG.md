<!-- markdownlint-disable MD013 -->
# Changelog

All notable user-facing changes to this project are recorded here.

## v0.26.0 - 2026-09-02

### Added

- Next.js is a framework weld knows. An app-router application used to reach
  the graph as files and symbols with nothing to say what it serves: the two
  functions in `app/api/orders/route.ts` were two exports, never the
  `GET /api/orders` and `POST /api/orders` the app actually answers, and a
  page was not a URL at all. `wd init` now detects Next.js and wires a `next`
  route entry the way it wires `express`, `gin` or `axum`, and that strategy
  reads the app-router conventions: every HTTP-verb function an
  `app/**/route.ts` exports, and every `app/**/page.tsx`, becomes a `route:`
  node at the URL its directory chain spells. Route groups (`(marketing)`) and
  parallel slots (`@modal`) drop out of that URL exactly as Next.js drops them,
  dynamic segments (`[id]`, `[...slug]`) keep the spelling the source gives
  them, and a private `_folder` is not routed at all. A page reads as the `GET`
  it answers rather than as a shape of its own, so `wd query`, `wd context` and
  a route inventory give one kind of answer to "what does this app expose"
  whichever framework declared it; `props.route_source` tells a hand-written
  handler from a page when you want only one of them. Detection keys on a
  `next.config.*` file or a `next` dependency in a `package.json` rather than
  on an import, because an app-router handler imports nothing from `next` —
  a repository can be an entire Next.js application with no `from "next"` in
  it. Existing projects pick this up with `wd init --refresh`, which merges
  the new entry into a `discover.yaml` without discarding hand edits.
  <!-- verify: file=weld/strategies/next.py grep="app-router" -->

### Fixed

- `wd init --refresh` now delivers a newly-wired *entry*, not only a newly-read
  language. Refresh could only ever add source entries for a language nothing
  on disk claimed, and a root configuration file is not a language: when
  `tsconfig.json` joined the manifests weld wires by default, every existing
  TypeScript project kept a graph with no node for the file that decides how
  the project resolves its own imports, and the only command that added the
  entry was `wd init --force` — which discards the hand edits `--refresh`
  exists to preserve. Every diagnostic said the config was current, because by
  the only question refresh asked, it was. Refresh now also compares the
  entries weld detects against the entries your config carries, so a root
  config that joined the table after your config was written is merged in, and
  so is a framework entry (`express`, `next`, `gin`, `axum`, and the Python
  set) for a language your config already claims — the case where the language
  check has nothing to say and the entry is missing all the same. Append-only
  and hand-edit preserving as before.

  An entry you delete stays deleted: `wd init` and `wd init --refresh` record
  what they wired as `# wired-entry:` comment lines under the version stamp,
  and refresh only offers entries that record has never held. The lines are
  inert comments — delete one and the next refresh offers that entry again. A
  `discover.yaml` written before weld kept that record seeds it from its own
  entries the first time you refresh, so an entry you had removed long ago may
  come back once; remove it again and it stays out.
  <!-- verify: file=weld/_init_wired_ledger.py grep="wired-entry" -->

- Every Dockerfile in a repository is its own node. A Dockerfile used to be
  identified by its file name alone, so a monorepo that builds one image per
  app — `apps/shop/Dockerfile`, `apps/blog/Dockerfile` — got a single
  `dockerfile:Dockerfile` node standing for all of them, and `Dockerfile`
  beside `Dockerfile.dev` in one directory collapsed the same way. The
  survivor was not simply the last one read: it kept the first image's `FROM`
  while accumulating every image's `COPY` edges, so `wd context` on it named a
  base image belonging to one service and source files belonging to another,
  and `wd impact` on a file copied by the blog image reported the shop image
  as affected. Nothing warned, and `wd stats` reported one Dockerfile, which
  looks plausible in a repository that has several. A Dockerfile is now
  identified by its path, so each gets its own node with its own base image
  and its own `contains` edges. A Dockerfile at the repository root keeps the
  short `dockerfile:Dockerfile` id unchanged, and where a project's only
  Dockerfile lives in a subdirectory the old short id still resolves to it, so
  existing links, bookmarks and saved queries keep working. Compose
  `depends_on` edges point at the same per-image ids, so a service still
  reaches the Dockerfile it builds from. Run `wd discover` to pick this up;
  existing graphs are rebuilt automatically on the next run.
  <!-- verify: file=weld/strategies/_dockerfile_ids.py grep="repo-relative path is the identity" -->

- A framework route entry no longer costs a handler file its evidence. When a
  `discover.yaml` claimed the same file twice — once with a language entry and
  once with a framework route entry (`express`, `next`, `gin` or `axum`) — the
  file node for every file the route strategy found a route in was reduced to
  its path, its language and its role. Its `exports`, its imports, the
  first-party import targets `wd impact` follows, its `types` and its
  `line_count` were all gone, so `wd context` on an HTTP handler — the file a
  reader is most likely to ask about — reported less than `wd context` on the
  plain module beside it, and anything reading `props.exports` saw none.
  Whether it happened at all depended on the order the two entries appeared in
  the file, which is why a `wd init`-generated config was unaffected and a
  hand-edited one that appended the framework afterwards was not. Both orders
  now give the same answer: the file keeps everything the language pass
  recorded about it, and the `route:` nodes and the `exposes` edge naming
  their declaring file are unchanged. A configuration that wires only the
  route strategy still gets a file node for the boundary file, exactly as
  before. Existing projects pick this up on the next `wd discover`.
  <!-- verify: file=weld/strategies/_ts_route_helpers.py grep="confidence: inferred" -->

- A `glob:` with a wildcard in a *directory* segment now matches. A
  `discover.yaml` entry like `apps/*/package.json`, `services/*/src/*.py` or
  `packages/*/Dockerfile` resolved to nothing at all — no nodes, no error, and
  `wd discover` still exiting 0, so there was no partial result to notice.
  This mattered most in a monorepo, where per-package globs are the natural
  way to write a config, and it did not stop at the missing nodes: because the
  freshness accounting resolved those patterns correctly while discovery did
  not, every file such a glob named was reported as "in-scope file never
  ingested" the moment a clean `wd discover` finished, leaving the repository
  permanently stale and paying a refresh on every read that could never fix
  it. Both are gone: the files are discovered, `wd stale` reads clean after a
  discover, and editing one of them is noticed as the content change it is.
  `*` still spans exactly one path segment, so `apps/*/package.json` matches
  `apps/web/package.json` and not `apps/web/vendor/package.json` — use
  `apps/**/package.json` for that — and single-directory globs such as
  `src/*.py` are unchanged. Existing projects pick the files up on the next
  `wd discover`; no config change is needed.
  <!-- verify: file=weld/glob_match.py grep="_directory_part_is_literal" -->

- Four strategies now resolve `glob:` the way every other one does. `fastapi`,
  `pydantic`, `compose` and the `events` config surface each carried their own
  copy of the path resolution the rest of the strategies share, so the fix
  above did not reach them — and they failed in two different ways. `fastapi`
  and `pydantic` emitted *nothing* for a pattern like `api/*/routers/*.py` or
  any `**` glob: no routes, no contracts, no error. `compose` and `events`
  quietly read the repository root instead, so a `deploy/*/docker-compose.yml`
  entry declared services and channels from whatever compose file happened to
  sit at the top level and none from the files it named — a wrong answer that
  looks like a right one. Both are gone: all four resolve exactly the files
  their glob matches, at any depth, and `exclude:` now applies to them as it
  already did elsewhere. A FastAPI route is attributed to the app module
  beside *its own* routers directory rather than to one directory for the
  whole pattern, which is what makes per-service layouts come out right.
  Single-directory globs are unchanged, and existing projects pick this up on
  the next `wd discover` with no config change.
  <!-- verify: file=weld/strategies/compose.py grep="resolve_glob" -->

- `wd stale` now understands a `{a,b}` glob group, so a new file in a
  TypeScript or JavaScript project is noticed. `wd discover` has always
  expanded a group like `**/*.{ts,tsx}` and read every file it names, but the
  check that asks whether a graph has fallen behind read the pattern as
  written — and a brace group means nothing there, so it matched no file at
  all. The two halves disagreed in silence: the files were in the graph, while
  the accounting believed none of them had ever been in scope. That left the
  check blind for a whole class of project, because these are exactly the
  entries `wd init` writes for TypeScript (`**/*.{ts,tsx}`) and JavaScript
  (`**/*.{js,jsx,mjs,cjs}`). Add a module to a project configured that way and
  nothing reacted: `wd stale` answered `stale: no`, no refresh ran, and reads
  kept answering "no such symbol" for a file sitting in the tree. It is
  reported now — as `in-scope file never ingested`, the same way any other
  never-read file is — and the refresh that follows picks it up. Nothing else
  moves: a group still names exactly its alternatives and never a wildcard, a
  freshly discovered project still reads clean rather than stale, and a
  configuration with no brace group in it answers exactly as it did before.
  <!-- verify: file=weld/_staleness_coverage.py grep="expand_braces" -->

- Stale-config detection now reads what your config *claims*, not what it
  *mentions*. `wd doctor`, `wd prime` and `wd init --refresh` compared the
  languages on disk against the flat set of strategy names the config named
  anywhere, so a `discover.yaml` wiring one `**/*.ts` entry counted as
  claiming JavaScript, Go, Rust, Java and C++ as well — and counted as
  claiming the `.tsx` files beside its `.ts` ones, which nothing was reading.
  On the upgrade path every existing Node project takes, that meant a repo of
  `a.ts`, `p.tsx` and `legacy.js` had two of three files invisible to the
  graph while `wd doctor` reported nothing and `wd init --refresh` answered
  "discover.yaml is already current". A wired entry now claims the files its
  glob actually matches, so the same repo is told about both gaps and
  `--refresh` wires them — the dialect-family glob and the JavaScript entry —
  without discarding the entry you wrote by hand. The warning names the
  unclaimed file count rather than the language's total, and stays quiet about
  what you scoped on purpose: an entry scaffolded for files that do not exist
  yet, a language absent from disk, and a subtree your config deliberately
  leaves out are all silent, exactly as before.
  <!-- verify: file=weld/_unclaimed_sources.py grep="A claim is a matched file" -->
- `wd init` now claims a TypeScript project's `tsconfig.json`. Every other
  ecosystem's manifest was already wired as a root config -- `pyproject.toml`,
  `package.json`, `Cargo.toml`, `go.mod`, `MODULE.bazel`, `Makefile` -- and
  TypeScript's was the omission, so a stock `wd discover` on a TS repository
  produced a graph with no node for the file that decides how that repository
  resolves its own imports. Weld already *reads* it, for the
  `compilerOptions.paths` aliases behind first-party import resolution; it
  simply never recorded that it had. `tsconfig.json` at the repository root
  now becomes a `config:` node like the manifests beside it, so `wd query
  tsconfig` finds it and a config inventory is complete. Root-level only, as
  every entry in that list is: a monorepo's `apps/web/tsconfig.json` belongs
  to one package, not to the repository. A fresh `wd init` writes the entry,
  and `wd init --refresh` merges it into an existing `discover.yaml` beside
  the entries you wrote by hand.
  <!-- verify: file=weld/init_detect_constants.py grep="tsconfig.json" -->

- `wd callers` now answers for TypeScript. Call evidence was recorded, but
  neither end of it could be used: every call ran from a synthetic
  whole-*file* symbol to an `unresolved` callee that nothing ever bound, so
  asking who calls a shared function returned nothing in every configuration
  — on a workspace where three files called it. Both ends are now read from
  what the code says. A call is attributed to the export it sits inside, so
  `export async function GET()` calling `formatPrice` is recorded as `GET`
  calling it rather than as "the route file" calling it; and a callee whose
  name arrived through a named import binds to the exported symbol behind
  that import, including through a package's re-export-only `index.ts`
  barrel, where the definition is looked up inside the package the import
  named. `wd callers` and `wd impact` therefore answer at function
  granularity for TypeScript the way they already did for Python. Nothing is
  guessed to get there: a name a package defines twice is an ambiguity and
  stays unresolved, as do a third-party function, a method called on a value,
  and every other callee the graph cannot bind — so "we cannot say" is still
  visibly different from "nothing calls this". The evidence arrives with the
  next `wd discover`, which rebuilds in full.
  <!-- verify: file=weld/_graph_closure_ts_calls.py grep="resolve_ts_call_targets" -->

- A third-party package imported by a file named after it no longer vanishes
  from the graph. Weld resolves an import against the importing file's own
  ancestor directories, so `providers/anthropic.py` writing `import anthropic`
  offered `providers.anthropic` as a reading -- which is the importing file
  itself. Weld drops an edge from a file to itself, so the dependency did not
  merely point somewhere odd, it disappeared: no package node, no edge, nothing
  anywhere in the graph to say the file used that SDK. On weld's own tree that
  hid `anthropic`, `openai`, `ollama` and `tree_sitter` -- every provider SDK it
  ships an extra for, plus its parser dependency -- so a package inventory, a
  `wd impact` from one of them, or a cross-repo join on the package name all
  under-reported, with nothing to show anything was missing. A reading that
  lands on the importing file is now refused and the walk continues outward, so
  a file named after the package it imports resolves exactly as any other file
  importing that name does. Only the inferred readings are bound: a module that
  genuinely imports itself is still read as itself, and no other import moves.
  The fix arrives with the next `wd discover`; no full rebuild is needed.
  <!-- verify: file=weld/_graph_closure_modules.py grep="_readings" -->
- A function defined under a `src/` layout now reports its callers. 0.25.0
  fixed the *file* level of this: `src/acme/config.py` imported as `from
  acme.config import load_config` stopped minting a second, external copy of
  the module. The symbol level was still split in two. Weld names a symbol
  after the path its file sits at -- `src.acme.config` -- while every import
  in such a tree writes the name the *source root* makes importable,
  `acme.config`, so each call was recorded against a second symbol keyed on
  the written spelling. That shadow held the call edges and knew no file; the
  real definition knew its file and answered `wd callers` with nothing. One
  function, two identities, and `wd impact` blind to the half that mattered.
  An absolute import is now read the way the interpreter reads it: weld walks
  up from the importing file while `__init__.py` is there, and the first
  directory without one is the entry Python puts on `sys.path` and the name a
  written import resolves under. So `acme.config` and `src.acme.config` are
  one module, the calls land on the definition, and no shadow is minted. The
  same reading already applied to a script directory importing the file next
  to it; it now applies from inside a package tree, which is what a `src/`
  layout is. Node ids do not change, and nothing is guessed: a written name
  the tree does not actually contain keeps the spelling it was given, one the
  glob already owns is never re-pointed, and a module never resolves an import
  to itself. The first `wd discover` after upgrading rebuilds in full, which
  is what moves an existing graph's call edges onto the definitions.
  <!-- verify: file=weld/strategies/_python_source_root_import.py grep="_source_root_prefix" -->
- The cross-repo `package_graph` resolver now recognises a .NET project as the
  *producer* of the package its consumers name. A `.csproj` used to tell weld
  only what a repo consumed -- its `<PackageReference>` entries -- so a schema
  or client library whose published name exists nowhere but its project file
  could be joined *from* but never *to*, and every reference pointing at it
  resolved to nothing. On a .NET-heavy workspace that is most of the dependency
  graph, absent silently rather than reported missing. A project file now also
  declares what its repo publishes: `<PackageId>` when it states one, otherwise
  the project filename, which is the name `dotnet pack` would give it. A
  project declaring `<IsPackable>false</IsPackable>` publishes nothing and is
  credited with nothing, and a `<PackageId>` still holding an unexpanded
  `$(...)` property reference is not read as a package name. Producer manifests
  are read through one per-ecosystem registry -- `pyproject.toml`, `go.mod`,
  `.csproj` and `.proto` -- and the gitignore and vendored-tree boundary
  applies to producers exactly as it does to the consumed side, so a copied-in
  project file under `vendor/`, `packages/`, `bin/` or `obj/` still makes no
  repo the producer of somebody else's package.
  <!-- verify: file=weld/cross_repo/_manifest_readers.py grep="MANIFEST_READERS" -->
- `wd doctor` no longer fails a polyrepo workspace root for having no
  `discover.yaml`. A federated `wd discover` reads `.weld/workspaces.yaml` and
  the children's own graphs and resolves no source glob at the root, so a root
  that only federates has nothing of its own to discover and needs no
  `discover.yaml` -- but doctor graded that absence `[fail]`, so the healthiest
  possible workspace reported `Status: errors` and exited 1 while `wd workspace
  status` showed every child green. Anything gating on that exit code read a
  correct setup as a broken one. Where the registry is present and
  `discover.yaml` is absent, the absence is now a `[note]` that says why -- this
  root federates; it has no sources of its own to discover -- and the exit code
  stays 0. Doctor settles that by asking the same question every graph-backed
  read already asks about a root, so the two can no longer disagree about what
  a workspace root is. Nothing else moves: a root that federates *and*
  discovers keeps its `[ok]` line with the source count, and a plain repository
  missing `discover.yaml` is still an error and still exits 1.
  <!-- verify: file=weld/_doctor_config.py grep="this root federates" -->
- `wd prime` no longer tells a polyrepo workspace root to run `wd init` for a
  `discover.yaml` it has no use for. At a root whose only discovery input is
  `.weld/workspaces.yaml`, prime reported `[ACTION] discover.yaml not found`
  with `-> Run: wd init`, and then `[INFO  ] Graph has only 1 node — consider
  adding more sources to discover.yaml`. Neither line could be acted on:
  `wd init` there re-scaffolds a stub config that federated discovery never
  resolves, and a root meta-graph holding one node per present child is exactly
  the shape federation is meant to produce — so the only next step prime
  offered was one that makes the setup worse, and it contradicted `wd doctor`,
  which already treats that absence as a note. Both lines are federation-aware
  now: the absent config is an `[INFO  ]` line saying the root federates and
  has no sources of its own to discover, the node-count advisory is dropped,
  and a healthy workspace root finishes with `Weld is up to date. No actions
  needed.` Prime settles this by asking the same question every graph-backed
  read already asks about a root, so the two commands can no longer disagree
  about what a workspace root is. Nothing else moves: a root that federates
  *and* discovers keeps its `[OK    ]` line with the source count **and** keeps
  the node-count advisory, because there the config exists and the root really
  does resolve sources; and a plain repository with no `discover.yaml` still
  gets the `[ACTION]` and the `wd init` next step.
  <!-- verify: file=weld/_prime_config.py grep="this root federates" -->
- `wd init` now wires the TypeScript and JavaScript it just told you it found.
  It counted your `.tsx` files, counted your `.js` files, and named the Express
  it detected -- then wrote a config claiming only `**/*.ts`. On a Next.js app
  that is most of the repository: every page, layout and component invisible to
  the first `wd discover` you ever run, with nothing to say so, because the
  language *was* wired and only its other dialects were not. Init now writes one
  entry per dialect family -- `**/*.{ts,tsx}` for TypeScript and
  `**/*.{js,jsx,mjs,cjs}` for JavaScript -- so a dialect it counts is a dialect
  it claims. Detecting Express now wires the `express` route strategy the same
  way detecting Gin or Axum wires theirs, and both family entries carry
  `emit_calls`, so a stock TypeScript graph records the call evidence
  `wd callers` and `wd impact` are answered from. JavaScript also joins the
  languages `wd doctor` and `wd prime` report as unclaimed, so a repository
  whose JavaScript nothing speaks for is now both reported and fixable by
  `wd init --refresh` -- previously it was not merely unreported, it was
  unrefreshable. An existing config is left exactly as you wrote it: a config
  that already wires a `**/*.ts` entry by hand gets the `**/*.{ts,tsx}` family
  entry and the framework entry merged in beside it, and the entry you wrote
  stays.
  <!-- verify: file=weld/_init_language_entries.py grep="{ts,tsx}" -->
- A TypeScript import that names your own workspace package, or an alias from
  your own `tsconfig`, now points at the file that defines it. In an npm
  workspaces monorepo `import { formatPrice } from "@acme/shared"` minted a
  `package:typescript:acme-shared` node — a claim that the code lives outside
  the repository, three directories from where it actually is — and
  `import { greeting } from "@/lib/greeting"` did the same, because nothing in
  discovery read `compilerOptions.paths` at all. So the shared package a
  monorepo is built around had no incoming edges from the apps that use it:
  `wd impact` on it reported nothing, `wd context` showed no consumers, and a
  dependency inventory listed first-party code as a third-party dependency.
  Weld now reads the workspace member map out of the root `package.json` and
  the alias map out of the nearest `tsconfig.json` (or `jsconfig.json`) above
  the importing file, and binds both spellings to their defining files —
  honouring `exports`/`types`/`main` entry points, falling back to the
  conventional `index` locations when a declared entry names a build output
  the checkout has not produced, and scoping each alias to the config that
  declares it so two apps may give `@/*` two different meanings. Nothing else
  about an import moves: a genuine dependency still mints its package node
  with the origin it always had, and a first-party name whose file the graph
  does not hold draws no edge rather than a false external one. Arrives with
  the next `wd discover`; the discovery-state version is bumped, so the first
  run after upgrading is a full one.
  <!-- verify: file=weld/strategies/_ts_first_party.py grep="FirstPartyImports" -->
- The cross-repo `package_graph` resolver now reads npm `package.json`
  manifests, so a workspace of Node repositories gets a dependency graph
  instead of an empty one. It read Python, Go, MSBuild and protobuf manifests
  and nothing else, so an app declaring `"@acme/ui-kit": "^1.2.0"` beside the
  repository that publishes `@acme/ui-kit` was joined to nothing -- and
  nothing said so: `wd impact` on that library's repo node reported no
  dependents for a package the whole workspace depended on. A `package.json`
  now declares both halves. Its `name` is what the repository publishes, and
  its runtime `dependencies` are what it consumes; `devDependencies`,
  `peerDependencies` and `optionalDependencies` are deliberately not edges,
  since a build-time tool and a contract with whoever installs the package are
  not run-time dependencies on a sibling repository. A package marked
  `"private": true` publishes nothing -- npm's own rule -- so a workspace root
  or an application is never credited with producing a package named after it.
  Workspace members are read where they sit, `packages/` included, and a
  dependency satisfied inside the same repository stays inside it instead of
  becoming a cross-repo edge. A vendored `node_modules` declares nothing even
  in a repository that commits it: one `npm install` leaves a complete
  self-naming manifest for every transitive dependency on disk, and reading
  those would credit your app with producing `react`.
  <!-- verify: file=weld/cross_repo/_manifest_readers.py grep="read_package_json" -->
- One malformed manifest in one child repository no longer costs a workspace
  every cross-repo edge. A `package.json` or `pyproject.toml` nested thousands
  of levels deep makes the parser raise a recursion error rather than report
  bad syntax, and that walked straight past the per-manifest guard: the
  resolver as a whole was abandoned with a single warning, so one unreadable
  file silently deleted every package edge in the graph -- including the ones
  nothing near it had anything to do with. Such a manifest now contributes
  nothing, exactly as an unparseable one always did, and every other manifest
  in the workspace is read normally.
  <!-- verify: file=weld/cross_repo/_manifest_readers.py grep="RecursionError" -->
- A component in a `.tsx` file reaches the graph. `tree-sitter-typescript`
  ships two grammars, and weld used the one that does not know JSX for every
  TypeScript file -- so `<main>` in a Next.js page was a syntax error, error
  recovery swallowed the declaration around it, and `export default function
  Home()` produced no symbol, no exports and no imports. A Next.js app's pages,
  layouts and components are all that shape, so its own screens were the part
  of it weld could not see, and nothing said so: the file still had a node, it
  was simply empty. Weld now chooses the grammar per file from its extension,
  so a `.tsx` file is read as JSX and its `.ts` neighbour as plain TypeScript,
  under the one entry `wd init` already writes. `language: tsx` is accepted as
  a spelling of the same thing -- it used to name a grammar module that has
  never existed -- and records the file as TypeScript either way, so one
  language keeps one symbol namespace whichever dialect a file is written in.
  <!-- verify: file=weld/strategies/_ts_dialect.py grep="grammar_variant" -->
- A barrel file now leads somewhere. An `index.ts` whose whole content is
  `export { formatPrice } from "./money"` is what a package's `main` points
  at, so every read arriving through the package name arrives there -- and it
  had no node of its own, no exports, no imports, and one outbound edge, to
  its own call-graph sentinel. The module it published was unreachable from
  the entry point that published it. Weld now records the names on the file
  node's `props.reexports` and the module they come from in
  `props.imports_from`, which resolves to a `depends_on` edge on the defining
  file exactly as a plain import does; `export * from "./money"` records the
  dependency without the names, which is all that form states. Re-exported
  names stay out of `props.exports`: that list is what becomes `symbol:`
  definition nodes, and a barrel defines nothing, so `wd callers` and `wd
  impact` still answer about the module that declares a function rather than
  about every barrel that republishes it. A renamed re-export records the name
  it publishes, and a local `export { a }` -- which forwards nothing -- is
  unaffected.
  <!-- verify: file=weld/strategies/_ts_file_props.py grep="reexported_names" -->

- JavaScript now delivers the surface the platform table has been claiming for
  it. A `.js` or `.jsx` file wired as `language: javascript` produced *nothing*
  — not a symbol, not an import, not even a file node — because weld shipped no
  JavaScript query file at all, and queries are loaded before any file is read,
  so the whole source entry was abandoned with one warning naming an absolute
  path inside the install. A Node service written in CommonJS was invisible to
  every question the graph answers. It now contributes its functions and
  classes as symbols attributed to the file that declares them, its
  `module.exports` / `exports.x` names as the module's published surface, and
  its dependencies from both `import` and `require("…")` — so a required
  third-party package becomes a package node with a `depends_on` edge, the same
  evidence a TypeScript import has always produced. `module.exports =
  require("./impl")` is read as the re-export it is, which keeps a package's
  entry-point facade in the graph rather than dropping the file every consumer
  arrives at first. What is *not* promoted is deliberate: a top-level `const`
  bound to a `require` is an import rather than a definition, and a name
  republished through `module.exports` is recorded as published without
  claiming this file defines it. JavaScript remains Tier 2. The evidence
  arrives with the next `wd discover`, which rebuilds in full.
  <!-- verify: file=weld/languages/javascript.yaml grep="commonjs_exports" -->

## v0.25.0 - 2026-09-01

### Fixed

- `wd callers` and `wd impact` now see a call made through a name unpacked from
  a deferred-import helper. Python libraries routinely hide an import inside a
  small function to break an import cycle -- `def _api(): from x import a, b;
  return a, b` -- and then unpack what it returns at the call site: `a, b =
  _api()`, then `b(...)`. The import itself was already understood, so a call
  by the *imported* name resolved; the local name the unpack binds appears in
  no import statement, so the call through it had no answer, and the real
  definition under-reported its callers. That miss lands hardest on exactly the
  functions the idiom exists to share, which are the ones "who calls this?" gets
  asked about before a signature changes. Such a helper is now read where its
  return value is written in its own source -- its body is nothing but imports
  and one `return` of the names those imports bound -- and the unpacked name
  resolves to the definition it holds. Everything else stays unresolved rather
  than guessed: a helper that computes anything, is decorated, is imported from
  another module or nested inside a function; a returned name it did not import
  itself; a name two imports both bind; an unpack whose names do not line up
  one-for-one with the return; and a name the surrounding scope binds a second
  time. The rule reads the file's own import table, so it can never name a
  module that table would not, and one scope's unpack binds nothing in a
  sibling or nested scope.
  <!-- verify: file=weld/strategies/_python_lazy_api.py grep="lazy_api_accessors" -->

- Cross-repo dependency edges in a polyrepo root graph now all carry the same
  type, `cross_repo:depends_on`. Three resolvers record the same fact -- repo A
  depends on repo B -- by three different routes: `package_graph` reads build
  manifests, `compose_topology` reads `depends_on` in a compose file, and
  `package_import_resolver` reads import statements. Only the first spelled the
  type with the `cross_repo:` prefix that marks a cross-repo edge; the other two
  emitted a bare `depends_on`, which is also the ordinary *within*-repo
  dependency type. So a tool filtering a workspace graph for cross-repo
  dependencies -- by prefix, the only thing that distinguishes them -- saw one
  resolver's edges and silently missed the other two's, while the edges it
  missed were indistinguishable from single-repo ones sharing the graph. The
  three now read one shared constant, so the spelling can no longer drift
  between them, and every registered resolver is held to the prefix rule the
  workspace graph validator already enforced. Nothing else changes: within-repo
  `depends_on` edges are untouched, and the affected edges' endpoints and
  properties are exactly as before.
  <!-- verify: file=weld/_federation_endpoints.py grep="CROSS_REPO_DEPENDS_ON" -->
- `wd callers` and `wd impact` now follow an explicit relative import to the
  module it actually names. `from .helper import work` inside `pkg/caller.py`
  means `pkg.helper` -- the interpreter reads the leading dot as "my own
  package" -- but discovery dropped the dot and recorded the call against a
  top-level `helper` module that exists nowhere, confidently and as
  third-party. `from . import helper` was skipped outright, so a call through
  it had no answer at all. Relative imports are how most Python libraries spell
  their own internal calls, so on such a codebase this was the common case
  rather than an edge: the definitions collected no callers, and "who else
  breaks if I change this?" answered empty for a package's own internals. The
  leading dots are now counted against the importing file's package, the same
  arithmetic the interpreter does, including inside a package's `__init__.py`,
  where one dot means that package itself rather than its parent. Two shapes
  are refused rather than guessed, because the interpreter refuses them too: a
  level that walks past the top-level package, and a relative import in a
  module that is under no package at all. Both keep their honest "unresolved"
  -- notably at the top of a tree, where the invented name and a real
  neighbouring module can coincide. A codebase that spells every import
  absolutely is unaffected.
  <!-- verify: file=weld/strategies/_python_relative_import.py grep="absolute_module" -->
- `wd callers` and `wd impact` now find the call sites of a function imported
  by bare name from the file next door. A script directory -- one with no
  `__init__.py`, the usual shape for a repository's `tools/` or `scripts/` --
  is placed on `sys.path` by the interpreter itself, so `from helper import
  work` there means the `helper.py` sitting beside the importer. Discovery read
  the name the way the source spells it instead, and recorded the call against
  a top-level `helper` module that exists nowhere, confidently and as
  third-party. The real definition therefore collected no callers at all, and
  "who else breaks if I change this?" -- the question a shared helper is asked
  most -- answered with only the callers inside its own file. On this
  repository that fix removes 233 invented symbols and re-points 1035 calls
  onto the definitions they actually reach. The refusals are deliberate and
  measured: a file that sits beside an `__init__.py` is inside a package, where
  a bare name is an absolute import, so a module named after the library it
  wraps (`providers/anthropic.py` importing `anthropic`) is never resolved to
  itself; and a name with no matching file beside the importer keeps the
  spelling it was given, so an ordinary third-party import is untouched. A
  sibling that *is* there wins over a standard-library module of the same name,
  which is what the interpreter does too.
  <!-- verify: file=weld/strategies/_python_source_root_import.py grep="normalize_source_root_imports" -->

- An incremental `wd discover` no longer keeps a placeholder symbol alive after
  the last file that referenced it is deleted. When a package re-exports a name
  through its `__init__.py`, discovery records a placeholder for the re-exported
  symbol, and the module name then resolves to that placeholder for every file
  importing the package -- including files that never mention the symbol.
  Deleting the one file that actually used it left the placeholder standing on
  the strength of those bystanders' import records, which discovery had itself
  derived from the placeholder's presence, so each kept the other alive. A full
  `wd discover` of the same tree records neither, and pointed the surviving
  imports at the package instead, so a refreshed graph disagreed with a rebuilt
  one and stayed that way. An import record derived from the graph no longer
  counts as a reference; a real call, inheritance, or decorator still does, so a
  placeholder shared by several files survives the loss of any one of them
  exactly as before.
  <!-- verify: file=weld/_discover_placeholder_anchor.py grep="_DERIVED_EDGE_STRATEGIES" -->
- An incremental `wd discover` no longer throws away the standard-library
  decorators it had already resolved. A symbol used only as a decorator --
  `@dataclass`, `@property`, `@staticmethod`, `@abstractmethod` -- is recorded
  as decorating the thing below it, so the record points *away* from it, and
  the refresh's cleanup pass, which only ever looked at records pointing *at* a
  symbol, read every one of them as unreferenced and deleted it. Nothing was
  lost from the finished graph: the records left hanging were noticed and
  repaired by re-reading the files that wrote them. But that repair redid the
  entire refresh -- on this repository, 7 deleted symbols, 203 hanging records
  and 122 untouched files re-read, every time a refresh had anything at all to
  clean up. A symbol is now kept while any record still mentions it, in either
  direction. A decorator that genuinely goes away with the last file that used
  it is still removed, exactly as before.
  <!-- verify: file=weld/_discover_placeholder_anchor.py grep="edge_anchored_node_ids" -->
- `wd callers` and `wd impact` now find the call sites of a classmethod or
  static method reached through an imported class. `from mypkg.corpus import
  Corpus` followed by `Corpus.build(rows)` had no answer at all: discovery once
  invented a `mypkg.corpus.build` that no module defines, and after that was
  stopped the call simply went unresolved -- so either way the real
  `Corpus.build` reported no callers, and the blast radius of changing it read
  empty. The method's own symbol is now the target, decided after every glob has
  been read and merged, where the class definition is actually visible. It is
  decided conservatively, and the refusals are the point: the retarget happens
  only when the imported name is a class discovery has read *and* the method is
  one it defines. A constant that merely looks the same at the call site -- a
  dict, a compiled regex, a message template -- keeps its honest "unresolved",
  as does a class called on a member it inherits rather than defines, a
  standard-library class, and a third-party one. Removing the method later
  degrades the call back to unresolved on both the full and the incremental
  path, rather than leaving a refreshed graph pointing at a symbol that is gone.
  <!-- verify: file=weld/_graph_closure_import_attr.py grep="resolve_class_base" -->
- A full `wd discover` and an incremental refresh now agree on a call into a
  module that a *different* source glob owns. `from mypkg import helpers`
  followed by `helpers.load()`, written in a file one glob covers while
  `mypkg/helpers.py` is covered by another, resolved to `mypkg.helpers.load`
  after an incremental refresh and to nothing at all on a full discover of the
  same tree -- two different graphs for one unchanged repository, and which one
  you got depended on how the graph had last been built. Discovery decides that
  import from its own glob only, which is the same question on both paths, and
  the wider one is now answered once, after every glob has been read and merged.
  The refresh answer is the one that wins: a full discover reaches
  `mypkg.helpers.load` too. Deleting `mypkg/helpers.py` degrades both paths back
  to unresolved rather than leaving a refreshed graph pointing at a symbol that
  is gone. Calls within a single glob, imports of a value rather than a
  submodule, and standard-library imports are unchanged.
  <!-- verify: file=weld/_graph_closure_import_attr.py grep="rewrite_import_attr_targets" -->
- `wd callers` no longer answers with a function that does not exist. Calling a
  method on an imported value -- `from mypkg.tables import LIMITS`, then
  `LIMITS.get(name)` -- was resolved as though `LIMITS` named an imported
  *module*, so the method name became a symbol of its own: a placeholder for
  `mypkg.tables.get`, a function no module defines under any spelling. Asking
  who calls it named the calling function, confidently, so a reader acting on
  that answer got a false positive rather than a miss. Discovery now reads the
  distinction the import statement already draws -- `import mypkg.tables as t`
  binds a module, `from mypkg.tables import LIMITS` binds a value -- and leaves
  a method call on a value unresolved rather than inventing a target for it. A
  module import, a submodule import of your own code that discovery has walked
  (`from mypkg import tables`, then `tables.load()`), and a plain call to an
  imported function all resolve exactly as before. On weld's own tree this
  removed 27 invented symbols, and the file-to-module dependency edges that had
  been anchored on them moved onto names that exist.
  <!-- verify: file=weld/strategies/_python_expr_resolve.py grep="_imported_value_attr" -->
- `wd callers` and `wd impact` now see the callers of a symbol that consumers
  import from a re-export facade. When a module publishes names it does not
  define -- `from ._impl import parse` in a package's public `api.py`, then
  `from mypkg.api import parse` everywhere else -- the call resolved against the
  calling module's import table onto a placeholder for `mypkg.api.parse`, a
  function that module does not have. Every consumer taking the documented
  public import path hung off that placeholder, so asking who calls the real
  definition answered "no callers" and the blast radius of changing a
  re-exported symbol read as empty. Discovery now follows the facade's own
  imports to the module that defines the name and drops the placeholder; on
  weld's own tree that reconnected 91 calls across 44 symbols. The walk is
  bounded and refuses to guess: it never leaves your own code, stops when two
  imported modules both define the name, and terminates on an import cycle.
  <!-- verify: file=weld/_graph_closure_reexport.py grep="rewrite_reexport_targets" -->
- A documentation repository's `README.md` now becomes a graph node. `wd init`
  wires a `**/*.md` source when a repository has no conventional docs directory
  but does have markdown, and the `markdown` strategy skips `README.md` unless
  a source entry sets `include_readme` — so the fallback indexed every markdown
  file *except* the one that is usually the repository's index. On a docs repo
  of 28 markdown files, the two that did not become nodes were its index pages.
  The fallback now sets `include_readme` (the conventional `docs/` entry keeps
  the default skip, where a README really is the project's front door rather
  than one of its documents), and a README doc node takes its label from the
  `#` title it declares instead of from its filename: a README titled
  `# Platform Documentation` used to answer `wd query "Platform Documentation"`
  with a different document, because "Readme" was the only name the graph knew
  it by. Existing configs are unaffected until regenerated; add
  `include_readme: true` to a source entry to opt in by hand.
  <!-- verify: file=weld/_init_framework_sources.py grep="include_readme" -->
- The unclaimed-source warning no longer sends you to the destructive fix.
  `wd doctor` and `wd prime` end their "N C# files present but no wired
  strategy claims 'csharp'" line with a remedy, and that remedy was
  `wd init --force` alone -- the mode that regenerates `discover.yaml` from a
  fresh scan and discards every hand edit, custom glob and comment in it.
  `wd init --refresh` merges the missing entries in and keeps all of that, so
  a maintainer who followed the advice literally threw away the config they
  had tuned. The warning now names both, non-destructive first --
  `run: wd init --refresh (keeps your entries) or wd init --force (regenerate
  from scratch)` -- and `wd prime` lists `wd init --refresh` under **Next
  steps**, because a step you are told to run should not be the one that
  loses your work. `wd init`'s advisory for a config that recognised nothing
  to wire points the same way.
  <!-- verify: file=weld/_unclaimed_sources.py grep="def unclaimed_message" -->
- `wd find` no longer answers `no matches` from a file index that does not
  exist. `find` reads `.weld/file-index.json` rather than the graph, so it was
  deliberately exempt from the "No Weld graph found." refusal every graph-backed
  read gives -- and that exemption was read as having no precondition at all: in
  a checkout with no index (a fresh worktree of a repository that ignores its
  weld config, or any repo where `wd discover` has not run) it reported a clean
  negative about a tree it had never searched, and exited 0 doing it. Next to
  `wd query` and `wd brief`, which refuse the same checkout with guidance, that
  is the one wrong answer a search can give: it reads as "no such file" and
  sends you to grep. `find` now applies the same rule to the artifact it
  actually reads. Where no index is reachable -- its own, and at a polyrepo root
  every registered child's -- it emits `error[file_index_missing]` with the
  remedy (`wd discover`) and exits non-zero; where the checkout could never have
  received one, it names that prerequisite too, the same sentence the graph
  route prints. Unchanged: an index that exists and matches nothing is a real
  answer and still exits 0, and `find` never runs discovery for you. The MCP
  `weld_find` tool returns the same `file_index_missing` payload the CLI message
  is rendered from.
  <!-- verify: file=weld/_find_precondition.py grep="def ensure_file_index_exists" -->
- A polyrepo child no longer looks like the producer of every package in its
  vendored virtualenv. The `package_graph` resolver reads build manifests out
  of each child working tree, and it walked that tree with its own skip list:
  a service carrying a `.venv` was credited with producing every distribution
  inside it -- a `pyproject.toml` in a `.dist-info` directory, a `.proto`
  shipped by a code generator -- so every sibling declaring one of those as a
  dependency got an edge to it that no manifest in either repo supports. On a
  workspace whose one service carried a virtualenv, eleven of seventeen
  cross-repo edges were fabricated this way, and `scan.respect_gitignore` made
  no difference because the scan never asked Git anything. The scan now reads
  only the files a child repo claims: Git-visible files, so `.gitignore` is
  honoured natively, falling back to the shared excluded-directory set for a
  child that is not a Git repository. Vendored and build-output directories
  stay excluded on both routes -- a `vendor/` tree a repo commits is code it
  carries, not code it publishes.
  <!-- verify: file=weld/cross_repo/_package_manifest_scan.py grep="def scan_child_manifests" -->
- `wd init --refresh` now wires the same strategies `wd init --force` wires.
  For each language it claims, refresh emitted only the tree-sitter entry and
  its test-peer companion, while a full init routes that language through its
  whole stack -- so on a .NET repo refresh wired three strategies where
  `--force` wires ten, leaving the project, solution, MSBuild, test-framework,
  ASP.NET and EF Core entries unreachable. Worse, it *cleared* the
  unclaimed-source warning while doing so, so a maintainer who followed the
  advice to a clean `wd doctor` had nothing left to tell them a further tier of
  their codebase was still invisible. Both commands now emit from one table:
  refresh wires the framework entries, `go_package`, the C# stack, and any
  detected gRPC / event / ROS2 sources, and drops any entry your config already
  wires rather than repeating it. Hand edits are still preserved exactly.
  <!-- verify: file=weld/_init_language_entries.py grep="def language_source_entries" -->
- `wd graph validate` no longer passes a polyrepo root graph whose cross-repo
  edges point at nothing. Its federation branch only ever checked the *shape*
  of an endpoint -- one separator, two non-empty halves -- and skipped the
  dangling-reference check for anything that matched, so a root graph whose
  every cross-repo edge referenced a node that existed in neither the root nor
  any child reported `{"valid": true, "errors": []}` and exited 0. At a
  workspace root each endpoint is now resolved against the ids the workspace
  actually holds: one naming a node no repo has fails as a dangling reference,
  and one pointing into a registered child that is missing, uninitialized, or
  corrupt fails as an *unverifiable* one, naming the child and its state --
  a reference that cannot be verified is not a verified reference. Outside a
  workspace root, and for `wd graph validate-fragment`, the shape check is
  unchanged: it is the correct answer when there are no children to resolve
  into. `wd doctor` reports the same finding as a `[fail]` under `[Edges]`,
  which previously only counted edges without asking whether they led
  anywhere.
  <!-- verify: file=weld/_federation_validate.py grep="def classify_endpoint" -->
- `wd discover` at a polyrepo root stops writing cross-repo edges that no
  reader can resolve. A resolver edge whose endpoints name nothing is dropped
  with a warning naming the resolver that emitted it (capped per resolver, so
  a wholly broken resolver cannot bury the rest of the output), rather than
  being merged into a graph where it is unreachable. One buggy resolver still
  cannot sink the pass. Discovery also records the resolver run itself on
  `meta.cross_repo` -- which strategies ran, which children were read, and how
  many edges were kept and dropped -- written whenever a pass happened,
  including when it produced no edges at all, so a reader can tell "nothing
  depends on this repo" from "nothing ever looked".
  <!-- verify: file=weld/_discover_federate.py grep="def _stamp_cross_repo" -->
- Importing a first-party Python module by the name the source actually uses
  no longer mints a second, "external" copy of it beside the real file. A
  package laid out under a source root -- `src/acme_notify/config.py` imported
  as `from acme_notify.config import load_config`, or `src/broker.py` imported
  from `src/main.py` as `from broker import Subscriber` -- was matched against
  its full repository path (`src.acme_notify.config`), so the import never
  found the file the graph already held and was recorded as an outside
  dependency instead. Every such import added a spurious external node: 1441
  of them in this repository alone, and `wd query` then answered with several
  representations of one function, ranking the real definition below a
  placeholder. Imports are now also resolved against the importing file's own
  enclosing directories, nearest first, so they land on the file node. Names
  belonging to the standard library are deliberately left alone -- a local
  `warnings.py` does not shadow the standard `warnings` module -- and an
  inferred match is only accepted when it lands on a real file, never on a
  placeholder. Genuinely external packages, including those a sibling
  repository publishes, are unaffected and still recorded as dependencies.
  <!-- verify: file=weld/_graph_closure_modules.py grep="def python_source_root_candidates" -->
- `wd stale` and `wd workspace status` now agree on how many child
  repositories are checked out. Both report a `present` count, and the two
  disagreed with each other and with `--json`: at a root with four healthy
  children `wd stale` summarised them as `0 present, 0 stale`, and editing one
  child changed that to `1 present, 1 stale` -- the present count was tracking
  the stale count rather than counting anything, because the roster tallied a
  `present` state the freshness check never produces (a child that is on disk
  and up to date is reported `fresh`), leaving every healthy child in no
  bucket at all. `wd workspace status` had the opposite half of the problem:
  a child whose graph had drifted was moved out of `present` into `stale`, so
  the same four children were counted 4, then 3. One meaning now holds on both
  surfaces -- `present` is "checked out on disk", `stale` is a sub-count of
  those whose graph is behind, and `missing` / `uninitialized` / `corrupt`
  children are the absent ones, broken out by state. A child state neither
  surface recognises is reported under its own name instead of being dropped,
  so the counts always add up to the number of registered children. The
  per-child lines and both `--json` payloads are unchanged.
  <!-- verify: file=weld/_cli_render_freshness.py grep="_ON_DISK_CHILD_STATES" -->
- Cross-repo resolvers that join whole repositories now emit edges that
  resolve. A polyrepo root graph holds two kinds of node id -- a child's own
  nodes, written `<child-name>\x1f<node-id>` and resolved inside that child,
  and the root's own `repo:<name>` nodes, which live in no child at all. The
  `package_graph` and `compose_topology` resolvers wrote a hybrid of the two,
  `<child-name>\x1frepo:<child-name>`, which belongs to neither: every edge
  they produced named a node that existed nowhere, so the manifest and
  compose joins they correctly found were unreachable to every reader, and
  `wd impact "repo:<producer>"` reported no dependents at all. Both now emit
  the root's `repo:<name>` ids. One helper family builds and parses every
  cross-repo endpoint, replacing the two hand-written spellings and the three
  separate places that split an endpoint apart -- each of which read an edge
  between two repositories as an edge touching no repository, which is why a
  stale-child guard, an incremental invalidation and an override's
  unknown-child warning all passed silently over exactly those edges.
  <!-- verify: file=weld/_federation_endpoints.py grep="def repo_node_id" -->
- `wd impact` on a `repo:<name>` node stops asserting a configuration fact it
  never read. It used to print "no cross-repo resolver is wired
  (cross_repo_strategies is empty)" whenever nothing pointed at the target --
  including at workspaces whose `cross_repo_strategies` named a resolver in
  the very file that sentence pointed at. The verdict is now read off the
  record `wd discover` leaves behind: a repository whose resolvers ran and
  found no dependents is a measured `0`, carrying `measured_by` with the
  resolvers that measured it (rendered as `Measured by:` in the human output),
  while a repository no resolver ever looked at stays `Risk: UNKNOWN` -- with
  a reason that now distinguishes an empty `cross_repo_strategies` from one
  that is set but whose pass never ran, and says which it is by reading the
  file.
  <!-- verify: file=weld/_impact_cannot_answer.py grep="def cross_repo_measured_by" -->
- `wd workspace status` no longer counts a child that is gone. It reported the
  lifecycle status stored in the workspace ledger, and that ledger records what
  the last `wd discover` found -- so a child deleted, moved, or renamed since
  then was still counted `present`, still rendered `present` on its own line,
  and in `--json` still carried a derived `"freshness": {"state": "fresh"}` for
  a directory that was not there. `wd stale`, which rebuilds the child roster
  live, reported the same workspace correctly, so the two commands printed
  different numbers for the same children. Every lifecycle status is now
  re-probed on disk at read time, so both surfaces answer from one source.
  Where the stored ledger and the disk disagree, the difference is named below
  the child lines -- `docs-site: ledger says present, disk says missing`, with
  `run: wd discover` as the remedy -- and `--json` carries the same as a
  top-level `drift` array, always present and empty when the ledger agrees. A
  child registered since the last discover, or dropped from `workspaces.yaml`,
  is reported the same way. The command reports drift; it never writes the
  ledger.
  <!-- verify: file=weld/_workspace_drift.py grep="def reconcile" -->
- `wd stats` reports child lifecycle from disk, so it no longer disagrees with
  the other two surfaces. Its workspace block read the stored ledger, which was
  the last place a child deleted since the previous `wd discover` still counted
  as `present` -- `wd stale` and `wd workspace status` both reported it
  `missing`. Every child is now re-probed at read time through the same code
  path those commands use. The human summary also splits registered from
  present, `workspaces: 3 registered, 2 present`, where it printed a bare
  `workspaces: 3 children`: that was a registered count, and a bare count in
  that position reads as "3 are here". When the stored ledger and the disk
  disagree, one line follows -- `workspace ledger drift: 1 child differs from
  the stored ledger -- run wd workspace status for detail` -- pointing at the
  command that names which child and how; `wd stats` stays a summary and does
  not repeat the per-child block. In `--json`, `workspaces` gains `present` and
  `drift_count`, both always emitted, with `drift_count` at `0` when the ledger
  agrees; every existing key keeps its name and meaning. A child registered but
  never cloned now reads `missing` instead of `unknown` even before a ledger
  has ever been written, and each child's `path` is now filled in from
  `workspaces.yaml` -- it was `null` for every child as soon as a ledger
  existed, because the ledger records the child's graph path under a different
  name and never carried a `path` of its own.
  <!-- verify: file=weld/_graph_stats_cli.py grep="def _child_rows" -->

- An incremental `wd discover` no longer aborts when a graph on disk carries a
  malformed placeholder property. The placeholder purge decides whether an
  external-package node was authored by a strategy by reading
  `props.source_strategy` back off `.weld/graph.json`, and it tested that value
  for set membership without first checking it was a string -- so a hand-edited
  or corrupted graph carrying, say, `"source_strategy": []` raised
  `TypeError: unhashable type: 'list'` and took the whole incremental discover
  down with it. The module's own contract already said a missing or non-dict
  `props` reads as "not a purgeable placeholder" rather than raising; the value
  check was the missing half. A non-string value now reads as "not matching" --
  the safe side, retaining a node rather than purging one -- and the same guard
  was applied to the one sibling purge rule with the same copied shape
  (`props.origin` in the tree-sitter package purge). Well-formed graphs are
  untouched: both predicates answer exactly as before on string-valued
  properties.
  <!-- verify: file=weld/_discover_external_package_purge.py grep="_is_edge_anchored_external_package" -->

## v0.24.0 - 2026-08-30

### Added

- `wd doctor` and `wd prime` now warn when a language present on disk has no
  discovery strategy wired for it. `.weld/discover.yaml` is generated once by
  `wd init` and never revisited, so a checkout initialised before a strategy
  shipped kept discovering with the old config -- a repository could have all
  of a language's source invisible to the graph while both commands reported
  healthy. The new read-only check re-runs `wd init`'s language detection and
  compares it against the strategies the config wires; a language nothing
  claims is surfaced as a suppressible warning naming the file count, the
  language, and the remedy (`wd init --refresh` or `wd init --force`), and it
  can be dismissed with `wd doctor --ack unclaimed-source-<language>`.
  Detection is language-granular, so a repository that merely lacks an
  optional framework extractor stays quiet. `wd init` also stamps the
  generating weld version into each `discover.yaml` it writes
  (`# generated-by: weld <version>`) so config drift is visible against
  `wd --version`.
  <!-- verify: file=weld/_unclaimed_sources.py grep="def detect_unclaimed_source_classes" -->
- `wd init --refresh` merges newly supported languages into an existing
  `.weld/discover.yaml` without discarding hand edits. `--force` was the only
  remediation for a stale config, and it regenerates from scratch, losing
  custom globs, extra strategies, exclusions, and comments. `--refresh` is the
  non-destructive middle path: it appends source entries for languages present
  on disk that no wired strategy claims, under a marked refresh section, while
  preserving the existing file byte-for-byte, and bumps the
  `# generated-by: weld <version>` stamp. A second refresh is a no-op; a
  missing config is reported explicitly and points at `wd init`. `--refresh`
  and `--force` are mutually exclusive.
  <!-- verify: file=weld/_init_refresh.py grep="def refresh" -->
- New `package_graph` cross-repo resolver for polyrepo workspaces. Neither
  shipped resolver covered dependency-by-package: `service_graph` matches URL
  hosts and `channel_binding` matches event-channel topics, so a schema
  library consumed via a C# `PackageReference`, a Python `pyproject`
  dependency, or a `go.mod` `require` -- the common polyrepo shape -- produced
  no inbound cross-repo edges at a federation root. `package_graph` reads each
  child's build manifests, collects produced names (`pyproject` project name,
  `go.mod` module path, `.proto` package) and consumed names, and joins them
  case-insensitively into `cross_repo:depends_on` edges between `repo:<name>`
  nodes, so `wd impact "repo:<producer>"` now sees the consumers. Enable it by
  listing `package_graph` under `cross_repo_strategies` in
  `.weld/workspaces.yaml`.
  <!-- verify: file=weld/cross_repo/package_graph.py grep="class PackageGraphResolver" -->
- Python discovery now records a dependency on the specific imported symbol
  when that symbol is actually referenced in the module body. The
  event-handler pattern imports a contract and passes it by value
  (`subscriber.subscribe(OrderPlacedEvent)`) without ever calling it, so
  "which services depend on this contract" under-reported Python consumers
  while C# reported them correctly. A `from x import Name` whose `Name` is
  used now yields a `package:python:x.Name` node and a distinct `depends_on`
  edge; unreferenced imports stay parent-package-only, `import *` is skipped,
  and aliased imports resolve to the real symbol.
  <!-- verify: file=weld/strategies/python_module.py grep="def _referenced_names" -->
- The MCP `weld_*` tools' missing-graph error now carries the reason a linked
  worktree could not seed itself, matching the CLI. An agent driving MCP from
  a fresh worktree of a repository that gitignores `.weld/discover.yaml`
  previously got only the bare "No Weld graph found." with no way to learn
  that no worktree of that repository can ever seed; the payload's `error`
  now appends the cause after the standing summary, so existing consumers
  matching on the summary are unaffected. The probe is read-only and runs
  only once a call has already failed.
  <!-- verify: file=weld/_mcp_guard.py grep="def missing_graph_payload" -->
- `wd stale --json` and MCP `weld_stale` gain an optional `seed_blocked_reason`
  field, emitted only when `reason` is `no graph` and a seeding prerequisite
  is missing (a linked worktree whose repository does not track
  `.weld/discover.yaml`). Previously the freshness probe answered `no graph`
  truthfully but implied `wd discover` as the remedy, when the actual fix is
  tracking the config repository-wide. Every existing payload is
  byte-identical; the field appears only where a seeding question is open.
  <!-- verify: file=weld/_stale_payload.py grep="def seed_block_detail" -->

### Fixed

- Python discovery keeps the full dotted import path when minting
  `package:python:*` references. Dotted imports were truncated to three
  segments, so `acme.platform.order.schema.v1` and `.v2` (distinct contract
  versions) both collapsed onto `acme.platform.order` -- exactly the split
  impact analysis needs -- while the C# path kept the full namespace. Graphs
  rebuilt with `wd discover` regenerate the ids automatically.
  <!-- verify: file=weld/tests/weld_lazy_import_capture_test.py grep="class DeepImportPathTest" -->
- `wd capabilities` attributes languages truthfully in both directions.
  Tree-sitter-backed languages (C#, Go, Rust, TypeScript, Java, C++) reported
  all-no even when every node came from the tree-sitter strategy, because the
  strategy's per-source `language:` key was never read; and the test-peer
  strategy declared seven languages under one extension set, so a Python-only
  repository reported C#, Go, Java, Rust, and TypeScript tests as present.
  Each language now flips only when the graph holds a file of that language.
  The matrix feeds `wd impact` risk scoring, so risk verdicts on those
  repositories change accordingly.
  <!-- verify: file=weld/_capabilities_language.py grep="def tree_sitter_language_rows" -->
- `wd init` no longer produces a silent zero-node config for docs
  repositories. Docs detection only recognised the conventional directory
  names (`docs/`, `doc/`, `documentation/`), so a repository keeping its
  markdown at the root or under `adrs/` or `architecture/` got an empty
  `sources:` block and a zero-node graph. When no conventional docs directory
  exists but markdown is present, `wd init` now wires a `**/*.md` docs
  source; and whenever the generated config wires nothing at all, it says so
  on stderr and points at the remedy instead of leaving a config that
  discovers nothing.
  <!-- verify: file=weld/_init_framework_sources.py grep="def markdown_fallback_doc_source" -->
- Graph-backed reads at a polyrepo federation root with no root graph now
  report "No Weld graph found" and exit non-zero instead of printing a
  well-formed empty result. `wd query`, `wd context`, `wd path`,
  `wd callers`, `wd references`, and `wd communities` returned exit 0 with
  empty output at a graph-less root -- indistinguishable from a genuine
  negative answer -- while `wd brief` and `wd stale` on the same tree
  correctly reported the missing graph. `wd find` stays exempt: it answers
  from the file index, not the graph.
  <!-- verify: file=weld/tests/weld_federation_missing_graph_test.py grep="class FederationMissingGraphGuidanceTest" -->
- `wd brief` ranks an exact identifier match first. When many nodes shared a
  query token the exact class symbol landed in id order (position 13 of 20
  in the reported case) while `wd query` ranked it first. Brief now applies
  the same exact-identifier preference ahead of its ranking composite, and
  the `relevance` field distinguishes `exact match` from `token match`
  (neighbours keep `related ...`) so callers can re-rank without re-querying.
  Envelope keys and field order are unchanged.
  <!-- verify: file=weld/_brief_rank.py grep="def primary_relevance" -->
- `wd brief` federates at a polyrepo root. At a root with
  `.weld/workspaces.yaml`, the CLI read only the root meta-graph and
  returned `primary: []` / "No matches found" for terms `wd query` resolved
  to child nodes, while the MCP `weld_brief` tool already federated. The CLI
  now loads the same federated graph, `wd brief --json` and `weld_brief`
  agree byte-for-byte at a federation root, and the bootstrap guidance naming
  brief the default starting point is truthful again.
  <!-- verify: file=weld/_brief_cli.py grep="def _load_brief_graph" -->
- `wd impact` on a `repo:<name>` node with no cross-repo resolver configured
  now reports `Risk: UNKNOWN` with the reason and a pointer to
  `cross_repo_strategies`, exiting non-zero, instead of a confident
  `Risk: LOW, 0 dependents` that was fabricated rather than measured. A
  genuine measured empty result (a non-repo node with no dependents) stays
  `LOW` / exit 0; a repo node with inbound cross-repo edges is answered
  normally. CLI and MCP report the outcome identically.
  <!-- verify: file=weld/_impact_cannot_answer.py grep="def uncomputable_repo_reason" -->
- `wd lint`'s circular-dependency check no longer counts function-scoped
  lazy imports as evidence. The referenced-import dependency evidence added
  in this release gave a lazy import the same weight as a top-level one, so
  the sanctioned cycle-breaking idiom surfaced as a new violation. Python
  discovery now tracks which imports are lazy-only, the resulting
  `depends_on` edge is marked `deferred`, and the cycle walk excludes
  deferred edges.
  <!-- verify: file=weld/_graph_closure_deferred.py grep="def deferred_edge_props" -->
- `cross_repo_strategies: [channel_binding]` in `.weld/workspaces.yaml` now
  passes validation. The resolver was registered and documented but missing
  from the loader's allow-list, so declaring it failed at load time. A
  drift-guard test now pins the allow-list to the resolver registry so a
  future resolver cannot be registered without being accepted here.
  <!-- verify: file=weld/workspace.py grep="channel_binding" -->
- `wd stale` at a federation root distinguishes absent children from
  present-and-fresh ones. Where zero child repositories were checked out on
  disk, the summary read `children: N (0 stale)` -- "all healthy" -- when in
  fact none existed to be stale. The child summary now reports how many
  registered children are present, how many are stale, and breaks the absent
  ones out by lifecycle state, e.g.
  `children: 4 registered, 0 present, 0 stale (missing=4)`. The `--json`
  payload is unchanged.
  <!-- verify: file=weld/_cli_render_freshness.py grep="def child_roster_lines" -->
- Worktree seeding names the missing prerequisite instead of a bare
  "no graph". Seeding reads the worktree's own `.weld/discover.yaml`, which
  git only checks out when the repository tracks it, so a project that
  ignores all of `.weld/` had seeding permanently off for every worktree and
  nothing said so. The first read in such a worktree now states the cause
  between the headline and the standing remediation, and `wd doctor` on the
  main checkout notes the same cause before any worktree exists (treating a
  force-added, tracked config as not ignored). A checkout with no git at all
  keeps the old message.
  <!-- verify: file=weld/_worktree_seed.py grep="def seed_blocked_reason" -->
- `wd doctor --ack agent-graph-missing` is accepted. Doctor printed
  `(id: agent-graph-missing)` and invited the reader to dismiss it, but the
  note-id allow-list never included it, so the acknowledgement exited 2. A
  new guard derives the emitted note-id set from the package source itself,
  so a computed note id cannot be emitted without also being acknowledgeable.
  <!-- verify: file=weld/_doctor_suppressions.py grep="agent-graph-missing" -->

## v0.23.1 - 2026-08-22

### Fixed

- Symbol doc-comment extraction (the `props.summary` channel for Go and
  Rust symbols) now degrades to "no summary" when the optional
  tree-sitter dependency cannot be imported, instead of raising
  `ModuleNotFoundError` from inside extraction. The call-graph and
  symbol-capture helpers on the same path received the same hardening:
  every lazy tree-sitter import in the strategy layer is guarded, and a
  repository lint now enforces that contract so an unguarded import
  cannot ship again.
  <!-- verify: file=weld/strategies/_ts_doc_comments.py grep="except ImportError" -->

## v0.23.0 - 2026-08-22

### Added

- `wd doctor` now warns when `.weld/.gitignore` is a recognized template
  missing lines the current template ships. A recent fix taught `wd init` /
  `wd workspace bootstrap` to self-heal a stale `.weld/.gitignore` by
  resyncing it, but only when either command actually runs -- a checkout
  that runs `wd discover` on every change and never re-runs `wd init` after
  initial setup got no signal that its ignore file had fallen behind (five prior
  recurrences of exactly this: `file-index-state.json`, `auto-refresh.jsonl`,
  `graph.write.lock`, `telemetry.jsonl`, and `.enrichment-prompted` each
  shipped a template line an existing checkout never picked up). The new
  check reuses the same recognition read-only: a `.weld/.gitignore` weld can
  still fully account for (config-only, `--track-graphs`, or `--ignore-all`)
  that is missing lines gets a `[warn]` naming them and pointing at `wd init`
  as the fix. A file weld cannot fully account for -- hand-edited, foreign,
  or simply absent -- stays silent, matching the same leave-alone posture the
  write path already has.
  <!-- verify: file=weld/_doctor_gitignore.py grep="def check_gitignore_resync" -->
- Go source now mints real package nodes, so cross-repo Go imports resolve
  and every Go file with a declaration satisfies the file-anchor rule. Go
  previously minted zero `package:go:*` nodes from a repo's own source --
  the only such node ever seen was a placeholder synthesised for an
  unresolved import, never a producer declaration -- so a polyrepo
  workspace's cross-repo import resolver had nothing genuine to match a
  sibling's import against, and any Go file with a promoted function,
  method, or type declaration had no parent to anchor it. `wd discover`
  now mints one `package:go:<import path>` node per Go package directory
  (derived from `go.mod` plus directory layout, mirroring the existing C#
  and Python package strategies) and links it to its member files, so a
  federated polyrepo workspace produces genuine cross-repo dependency edges
  between Go repos.
  <!-- verify: file=weld/strategies/go_package.py grep="def extract" -->
- Discovery now records what a TypeScript / JavaScript test mocks.
  `jest.mock("./payment-gateway")` and `vi.mock("../lib/thing")` name a module
  by string, so the test imports nothing from it and the dependency was
  invisible: asking who touches a module omitted every test that only mocks
  it. Weld resolves the specifier against the test file's own directory --
  including the `./a.js` spelling TypeScript's ESM output uses for `a.ts`, and
  directory imports resolving to `index` -- and records a dependency on the
  file it names. Only targets that resolve to a file actually on disk are
  recorded; a bare package name such as `axios` is left alone, because
  resolving it needs `moduleNameMapper` or `tsconfig` path aliases that weld
  does not read, and guessing a same-named local file would attribute a mock
  of an npm package to your own source. Python's equivalent
  (`unittest.mock.patch("dotted.string")`) is unchanged, and both record the
  same dependency shape, so one query answers the question in either language.
  <!-- verify: file=weld/strategies/_mock_module_ts.py grep="doMock" -->

- `wd stale --check` exits non-zero when the graph is stale, so a repository
  that commits its graph can gate on freshness in one line:
  `wd stale --check --no-refresh`. Nothing previously stopped a commit whose
  tracked graph was older than the source beside it -- `wd stale` always
  exited 0 and `wd doctor` grades staleness a warning. The report is printed
  either way, so a failing job says why it failed. Without the flag the
  command is unchanged, so existing scripts keep working.
  <!-- verify: file=weld/_graph_cli_parser.py grep="--check" -->

- `wd stale` names which source(s) it considers newer, and why. A
  `source_stale: true` verdict used to give no way to act on it beyond "run
  `wd discover`" -- the same line whether one file had changed or the signal
  simply had no basis to settle. `stale_sources` now lists each diverging
  path with a reason (`changed since last discovery`, `content differs`,
  `ingested file vanished`, or `in-scope file never ingested`), capped at 50
  entries with `stale_sources_omitted` reporting how many more there were.
  Some stale states -- no recorded commit, or unreachable history -- still
  have no single file to blame and report an empty list rather than a
  guess. `wd stale`'s text output and `--json` (and `weld_stale` over MCP)
  all carry the same fields.
  <!-- verify: file=weld/_staleness.py grep="stale_sources" -->

- `wd` now says when it is not the weld in the tree you are standing in.
  `wd` is a console script, so it runs whichever copy of weld is installed
  on your `PATH` — inside a weld source checkout that is a different copy
  from the one in front of you, and nothing used to say so. A command run to
  check a change would exercise the installed build and report a perfectly
  plausible result that owed nothing to the edit, which reads exactly like a
  correct answer. Weld now prints one line to stderr in that situation,
  naming the running version and its package directory alongside the
  checkout and its `VERSION`. The trigger is which package is executing, not
  a version difference: `VERSION` only moves at release time, so a version
  comparison would fall silent for every unreleased change. Ordinary use of
  an installed weld never sees the line — it needs a weld checkout above the
  current directory — and an editable install of the checkout you are in
  resolves to the same package and stays silent too. stdout is untouched, so
  `--json` output is unaffected; `WELD_SOURCE_CHECKOUT_NOTICE=off` silences
  it.
  <!-- verify: file=weld/_source_checkout_notice.py grep="def emit_source_checkout_notice" -->
- A fresh `git worktree add` answers on its first read. A new worktree used
  to arrive with no graph at all, so the first `wd query` in it exited with
  first-run guidance and left a cold full discovery as the only way forward.
  Weld now copies the graph from another checkout of the same repository --
  the main checkout first, then any other worktree -- and immediately
  reconciles it against the new tree, so the answer describes the branch you
  are on and not the one it was seeded from. That reconcile is incremental,
  costing roughly the branch delta rather than a full pass, and one line on
  stderr names the source
  checkout and the resulting `branch@sha`. Sibling checkouts are found
  through git itself, so nested, sibling, temp-directory, and
  bare-repository-hub layouts all work regardless of which tool created
  them -- there are no path conventions to match. The worktree needs its own
  `.weld/discover.yaml`, because configuration comes from the tree that owns
  it; a plain clone, having no sibling to seed from, keeps the existing
  first-run guidance. The optional SQLite index is not copied and rebuilds
  lazily, and `WELD_AUTO_REFRESH=0` disables seeding along with auto-refresh,
  so a frozen read still writes nothing under `.weld/`.
  <!-- verify: file=weld/_worktree_seed_mode_a.py grep="copy_seed_worktree" -->
- A tracked graph (`wd init --track-graphs`) now carries its own staleness
  basis into a fresh clone or worktree. The committed `graph.json` arrives
  without the gitignored bookkeeping that records which commit it was built
  at, so a fresh checkout used to treat a perfectly good graph as undatable,
  report it stale, and -- having no incremental state either -- pay a full
  rediscover, the exact cost that committing the graph exists to avoid. The
  first read now reconstructs that basis from the graph file's own commit
  history. The reconstructed value is deliberately conservative: it is at or
  before the commit the graph truly describes, so it can only over-trigger a
  refresh, never report a stale graph as fresh. A basis already recorded
  inside an older graph is preferred over a reconstructed one. Incremental
  state is borrowed from another checkout only when that checkout's graph is
  byte-identical to yours, so state can never describe a revision other than
  the graph it is paired with. With nothing drifted, the first read reports
  fresh and runs no discovery at all.
  <!-- verify: file=weld/_worktree_seed.py grep="def _record_tracked_basis" -->
- Answers now say which branch they came from. `wd stale` reports `branch`
  -- read live -- beside `graph_branch`, the branch that was checked out when
  the graph was built, and MCP reads carry `branch` in their `freshness`
  object. An answer served from the wrong checkout is visible instead of
  silent. A detached `HEAD` and a non-git tree have no branch to report, and
  say so (`null` in the JSON forms). The branch is recorded only in the
  gitignored sidecar and never inside `graph.json`, so a tracked graph stays
  byte-identical across worktrees sitting at the same commit.
  <!-- verify: file=weld/_stale_payload.py grep="graph_branch" -->
- The MCP read tools accept an optional `root`, so one running server can
  answer from any checkout of the repository it was started against -- the
  capability `wd --root` has always had on the command line. An agent that
  creates a git worktree after the server started no longer gets the launch
  checkout's answers for it. The bound is git's own notion of repository
  identity: `root` must be an existing directory of that same repository (a
  linked worktree, the main checkout, or a subdirectory of either), so a
  separate clone is refused even when its contents are identical, with a new
  `root_out_of_bounds` error code whose message never repeats the path it was
  given and reads the same for every reason it was refused. `weld_enrich` and
  `weld_review` write, so they take no `root` and always act on the server's
  own root. A named checkout that has no graph yet is bootstrapped on first
  use, exactly as the CLI bootstraps it. `docs/mcp.md` documents the
  parameter and the error code.
  <!-- verify: file=weld/_mcp_guard.py grep="resolve_dispatch_root" -->

### Changed

- `graph.json` is now written one entity per line. It was indented JSON, at
  roughly 74 lines per node, so any graph change was a six-figure diff on the
  largest file in the tree and any two branches that both re-discovered
  conflicted across the whole of it. Every node and every edge now occupies
  exactly one line (`meta` keeps its indented block), which on a
  9,000-node graph is 711,368 lines down to 49,303 and 21.1 MB down to
  15.9 MB. The file is still a single valid JSON document -- not JSON Lines
  -- so every reader parses it exactly as before, no schema field moved, and
  graphs written by older and newer weld versions load in each other. It is
  also faster to write and to parse, because the indentation was a quarter of
  the bytes. Existing graphs are rewritten once on the next discovery; the
  derived caches keyed to the old bytes (query-state sidecar, SQLite index,
  `wd warm` integrity tag) all detect the change and rebuild.
  <!-- verify: file=weld/serializer.py grep="_dumps_canonical_text" -->
- `wd init --track-graphs` now commits each artifact together with the record
  that explains it -- `graph.json` with `discovery-state.json`,
  `file-index.json` with `file-index-state.json` -- and writes a
  `.weld/.gitattributes` that resolves conflicts on them by regenerating
  rather than by hand. A fresh clone previously arrived holding a graph and
  no account of what that graph had read, so it reported stale on its first
  read and paid one full discovery before it could answer anything; it now
  answers straight away, and refreshes only when the sources have actually
  moved. Because git refuses to clone merge-driver configuration, each clone
  registers the driver once with
  `git config merge.weld-regenerable.driver true`; `wd init --track-graphs`
  does it for the checkout it runs in, and a clone that never runs it simply
  gets ordinary conflict markers.
  <!-- verify: file=weld/_gitattributes_writer.py grep="MERGE_DRIVER_NAME" -->
- The managed `.weld/.gitignore` no longer commits `file-index.json` in the
  default mode. The filename index is rebuilt from your tree by discovery, so
  tracking it contradicted that mode's own rule -- track the source-of-truth
  config, ignore everything weld can rebuild -- and it churned on every
  discovery. It was tracked by omission rather than by choice: the name
  appeared in neither policy. It is now ignored by default and tracked only
  under `--track-graphs`, where a warm `wd find` in a fresh checkout is the
  point. Repositories initialised by an earlier weld keep their existing
  policy file and can untrack it with
  `git rm --cached .weld/file-index.json`.
  <!-- verify: file=weld/_gitignore_writer.py grep="file-index.json" -->

- `wd` reads resolve which checkout answers instead of assuming the graph
  sits in the directory the command was run from. A read from a subdirectory
  now answers from the enclosing project rather than reporting no graph, and
  the upward walk stops at the boundary of the git worktree you are standing
  in -- so a worktree created inside another checkout can never climb out and
  answer from the outer checkout's branch. Outside a git checkout only the
  current directory is examined, so weld never wanders into an unrelated
  parent project. Resolution never redirects to a different checkout.
  `--root` is unchanged and still wins outright, and `wd discover`, `wd init`,
  and `wd warm` keep explicit-root semantics: commands that write state still
  default to the current directory.
  <!-- verify: file=weld/_root_resolver.py grep="resolve_weld_root" -->
- The MCP stdio server resolves its launch root the way `wd` does. Started
  without an argument from a subdirectory, it now serves the checkout you are
  standing in instead of the single directory it happened to start in, which
  usually held no `.weld/` and so answered nothing. Passing an explicit ROOT
  is unchanged.
  <!-- verify: file=weld/_mcp_stdio.py grep="resolve_weld_root" -->
- `weld_stale` now answers exactly what `wd stale --json` prints in a polyrepo
  workspace, as it already did in a single repo. This fixes a blind spot and
  changes a response shape. The blind spot: top-level `stale` was computed
  from the workspace root's own graph alone, so an agent polling a workspace
  root could read `stale: false` while a child repository's graph had drifted
  commits behind its source -- the CLI reported `true` for the same workspace.
  Child freshness is now folded in, and each child is dated through the same
  oracle the CLI uses, so `commits_behind` and the reason for staleness are
  reported rather than the "cannot date this graph" fallback the old path
  produced for any child whose recorded commit lives in the local sidecar.
  The shape: `children` changes from an object keyed by child name to a
  name-sorted list of `{name, state, reason, commits_behind}`, matching the
  CLI. A child that is missing, uninitialized, or corrupt still reports that
  word and is still never counted stale, but reports it in `state` rather than
  `status`; per-child `graph_sha`, `current_sha`, `source_stale`, `sha_behind`,
  and the `error` string on a corrupt child are no longer included, the last of
  which `wd workspace status --json` still reports. Code reading
  `children["name"]` should match on the `name` field instead.
  <!-- verify: file=weld/_mcp_read.py grep="stale_payload" -->
- `weld_stale` no longer refreshes the graph before answering. It is the
  freshness probe, and healing first left it unable to report the thing it
  measures: with auto-refresh on -- the default -- the tool re-ran discovery,
  in a polyrepo workspace across every drifted child, and then reported
  `stale: false`, while `wd stale` at the same root in the same second
  reported `true`. It now reports the state it finds without rewriting the
  graph, so the two surfaces agree whether or not auto-refresh is enabled, and a
  drifted child is visible instead of quietly repaired. Nothing else moves:
  every graph-backed read still auto-refreshes before serving and still
  carries the inline `freshness` object, so a `stale: true` here is followed
  by a self-healing read exactly as before. Anything that used `weld_stale`
  as its refresh trigger should call a graph-backed read, or `wd discover`,
  instead.
  <!-- verify: file=weld/_mcp_read.py grep="_load_single_repo_cached(root_path).stale()" -->

### Security

- The MCP `weld_stale` tool no longer runs discovery. Its published
  description tells a client the tool is advisory and does not mutate the
  graph, but refreshing before answering meant it could rewrite
  `.weld/graph.json` and -- because discovery imports project-local strategy
  modules from `.weld/strategies/` -- execute repository code, from the one
  graph tool a client had been told was a read-only probe. It no longer
  writes the graph or runs project code, so the declared contract and the
  behavior match. The graph-backed
  read tools still refresh by design, and `WELD_AUTO_REFRESH=0` still
  disables that for the whole server.
  <!-- verify: file=weld/_mcp_tools.py grep="never refreshes" -->
- The MCP stdio server no longer imports modules from the directory it is
  launched in. `python -m` places that directory -- for an MCP client, the
  repository being analyzed -- ahead of the standard library on Python's
  module search path, so a repository carrying its own `mcp/` package,
  `json.py`, or any other module weld imports had that code executed when
  the server started against it. The entry is now removed before anything
  else loads. The same removal stops a repository from choosing what weld
  reports about the environment: a crafted `mcp-*.dist-info` in that
  directory could previously supply the SDK version printed in the
  install-hint message. A `PYTHONPATH` naming the same directory is
  deliberately left alone. `docs/mcp.md` gains a trust-model section
  stating the boundary,
  including the handful of modules Python's own module runner resolves
  before weld gets control and the `PYTHONSAFEPATH=1` launch option that
  closes them.
  <!-- verify: file=weld/_launch_path.py grep="guard_module_launch" -->

### Fixed

- `wd discover --incremental` no longer leaves a phantom external-dependency
  node behind after the last file that imported it is deleted. The first
  file to import something outside the project -- Go's `strings`, Python's
  `os` -- gets a placeholder node to anchor the import; deleting that file
  correctly dropped the connecting edge but left the placeholder itself
  sitting in the graph with nothing pointing at it, which a fresh full
  discovery of the same tree never produces. Incremental discovery now drops
  that placeholder too, once nothing imports it any more, matching what full
  discovery already does -- a placeholder with another surviving importer is
  untouched.
  <!-- verify: file=weld/_discover_external_package_purge.py grep="emptied_external_package_node_ids" -->
- `wd discover --incremental` no longer leaves a phantom "unresolved symbol"
  node behind after the last file that referenced it is deleted. A call,
  base class, or trait/interface reference that cannot be resolved to a
  known symbol gets a placeholder node so the call/inheritance graph stays
  referentially closed; deleting the file that made the only such reference
  correctly dropped the connecting edge but left the placeholder itself
  sitting in the graph with nothing pointing at it, which a fresh full
  discovery of the same tree never produces. Incremental discovery now drops
  that placeholder too, once nothing references it any more, matching what
  full discovery already does -- a placeholder with another surviving
  reference, in any language, is untouched.
  <!-- verify: file=weld/_discover_unresolved_symbol_purge.py grep="emptied_unresolved_symbol_node_ids" -->
- `wd init` and `wd workspace bootstrap` now resync an existing
  `.weld/.gitignore` instead of leaving it exactly as first written. A
  checkout initialised before a later weld release started ignoring an
  additional generated file (`.enrichment-prompted`, `telemetry.jsonl`, and
  others before it) carried the omission forever -- `git status` stayed
  noisy until someone deleted the file and reran `wd init` by hand.
  Re-running either command now recognizes an existing file's policy from
  its own content and appends whichever lines that policy's template has
  gained since, leaving every existing line untouched; a hand-edited file,
  or one that does not cleanly match a known policy, is left alone
  entirely, and neither command ever switches an existing file to a
  different policy on its own.
  <!-- verify: file=weld/_gitignore_writer.py grep="resync_weld_gitignore" -->
- A Go file's `imports_from` no longer keeps its quotes. The `imports` query in
  `weld/languages/go.yaml` captures the Go grammar's
  `interpreted_string_literal` node, which is the verbatim source token,
  quotes included -- so `props.imports_from` held `'"github.com/spf13/cobra"'`
  where every sibling language already held the clean
  `'github.com/spf13/cobra'`. An exact-string consumer of that field, such as
  the cross-repo `package_import_resolver`, could never match a Go import
  against anything. `imports_from` and the derived `imports_origin`
  classification now share one clean, quote-stripped shape.
  <!-- verify: file=weld/strategies/_go_tree_sitter.py grep="strip_import_quotes" -->

- `wd query` no longer ranks a test above the code it covers. Asking "where is
  `graph.json` written" returned eight symbols from a lint *test* about
  `json.dumps` and never showed the module that actually writes the file,
  which sat past the result limit. The cause was naming rather than scoring: a
  test states its subject in a whole sentence
  (`test_unwrapped_graph_dump_is_flagged`) while the subject states itself in
  a word (`dumps_graph`), so on a prose question the test always carries more
  of the vocabulary. Test files, test symbols and test build targets now sort
  below non-test matches of equal relevance. They are re-ranked, never
  dropped, and the demotion switches off entirely when your query names tests
  (`test`, `spec`, `fixture`, `unittest`, `pytest`) or matches a test's name
  exactly -- so searching for a test still finds it first.
  <!-- verify: file=weld/_test_paths.py grep="test_noise_demotion" -->

- `wd find` now finds field names in Python files. It matched only what a
  module *defines* -- classes, public functions, imports, `__all__`,
  module-level constants -- so a schema field was unfindable in `.py` while
  the same word was found in shell scripts and Markdown, which are indexed by
  a plain text scan. Searching for a field returned a confident-looking
  answer that silently omitted every Python file, including the one declaring
  it. Class attributes, keyword-argument names and identifier-shaped string
  literals (the dict-key form most field reads take) are now indexed too,
  bounded per file and required to look like an identifier so prose, paths and
  values such as `"utf-8"` stay out.
  <!-- verify: file=weld/_file_index_extractors.py grep="_field_surface_names" -->

- `wd references` no longer discards its text matches when it has no graph
  match. A name that is not a node of its own -- a dataclass field, a dict key
  -- printed only `node not found`, while `--json` returned the matching files
  at that same moment. The two channels are independent, so the text hits are
  now shown alongside the message rather than instead of it.
  <!-- verify: file=weld/_cli_render.py grep="textual hits" -->

- The optional MCP stdio server no longer tells users with an
  installed-but-unusable `mcp` SDK that it is not installed. An SDK that is
  present but does not provide the MCP SDK 2.x API now gets its own message,
  naming the version found and the fix that applies -- `pip install -U
  "mcp>=2"`, or `pip install -U "configflux-weld[mcp]"` -- rather than
  pointing at an extra those users already have. An absent SDK still points
  at `pip install "configflux-weld[mcp]"`; both paths keep exit status 2.
  `README.md` and `docs/mcp.md` now state the `mcp>=2,<3` requirement and
  cover both cases under troubleshooting.
  <!-- verify: file=weld/_mcp_stdio.py grep="not provide the MCP SDK 2.x API" -->

- `wd find` now sees a project's extensionless executables. The index
  admitted a file by its extension or by a known basename, and a script such
  as `gradlew`, `configure`, `mvnw`, or a git hook has neither -- no suffix
  to match, and a name each project chooses for itself -- so a repository's
  most-invoked commands were absent from search while every file around them
  was indexed. Searching for one answered "no matches", which reads as "this
  does not exist" rather than "this kind of file is not indexed". An
  extensionless file that opens with `#!` is now indexed as the script it
  is. The check reads two bytes, so a binary is rejected on its magic number
  rather than by decoding it; files with an unrecognised extension are
  unaffected, and hidden dotfiles stay out regardless of their contents, so
  a `.env` cannot be drawn into a searchable index.
  <!-- verify: file=weld/file_index.py grep="_opens_with_shebang" -->

- Editing a source file no longer erases the build targets that declare it.
  On a Bazel repository, a refresh after any edit dropped that file's
  `build-target --contains-->` edges, so "which target builds this" and
  "which target runs this test" went unanswerable for every file anyone had
  touched since the last full discovery -- and no later refresh brought them
  back, while freshness reported the graph clean. The edges were declared in
  a `BUILD` file that had not itself changed, so nothing re-read it to
  restore them. Every edge read out of a `BUILD` file now records which file
  declared it, which is what a refresh needs in order to tell "this edge
  points at a file that changed" from "this edge is out of date". Deleting a
  declared source still removes its edges, as it always did.
  <!-- verify: file=weld/strategies/_bazel_labels.py grep="def edge_props" -->

## v0.22.1 - 2026-08-10

### Fixed

- Broken-reference diagnostics no longer truncate file extensions that
  extend a shorter known one: paths ending in `.tsv` were checked as
  `.ts` and `.jsonl` as `.json`, producing spurious `broken_reference`
  reports for files that exist. `.tsv` and `.jsonl` references are now
  extracted with their full extension and checked correctly.
  <!-- verify: file=weld/_agent_graph_asset.py grep="jsonl" -->
- Concurrent graph mutations no longer lose writes: every mutating
  command (`wd add-node`, `add-edge`, `rm-node`, `rm-edge`, `import`,
  `migrate`, `touch`, `wd enrich`, and the enrichment MCP tool) now
  serializes on an exclusive `.weld/graph.write.lock`, so parallel
  writers queue instead of silently overwriting each other's nodes. Set
  `WELD_GRAPH_LOCK_TIMEOUT` (seconds, default 60) to wait longer than
  the default before a contended writer gives up.
  <!-- verify: file=weld/_graph_write_lock.py grep="WELD_GRAPH_LOCK_TIMEOUT" -->
- The optional MCP stdio server now targets MCP SDK 2.0, and the `[mcp]`
  extra requires `mcp>=2,<3`. Fresh installs of `configflux-weld[mcp]` no
  longer crash at startup on the latest SDK: the server completes the
  handshake, advertises its tools, and serves tool calls as before.
  <!-- verify: file=weld/_mcp_stdio.py grep=add_request_handler -->

## v0.22.0 - 2026-07-17

### Added

- MQTT channel discovery: a new `events_mqtt` strategy extracts MQTT
  publish and subscribe topics as channel nodes, so message-driven flows
  surface in `wd query` and `wd context` alongside HTTP routes and
  function calls.
  <!-- verify: file=weld/strategies/events_mqtt.py grep="mqtt" -->
- DDS/IDL discovery: a new `dds_idl` strategy parses IDL interface
  definitions and surfaces DDS topics, structs, and interfaces as graph
  nodes. `wd init` auto-wires the strategy for `.idl` files.
  <!-- verify: file=weld/strategies/dds_idl.py grep="idl" -->
- Producer-to-consumer channel linking: discovery now joins the site that
  publishes to a message channel with the sites that consume it, emitting
  explicit producer-to-consumer edges both within a repository and, in a
  polyrepo workspace, across child repositories, so an event can be traced
  from where it is produced to everywhere it is handled.
  <!-- verify: file=weld/cross_repo/channel_binding.py grep="channel" -->
- Full read-tool federation: in a polyrepo workspace the remaining
  cross-repo read tools, such as callers, references, find, trace, and
  impact, now resolve across child repositories, so cross-repo questions
  return complete results instead of stopping at a single repository
  boundary.
  <!-- verify: file=weld/federation_tools.py grep="federat" -->
- Project-local capability registration: a project can register its own
  discovery-strategy capabilities through a declarative manifest, so
  project-local strategies participate in discovery without changing the
  bundled strategy set.
  <!-- verify: file=weld/_capabilities_local.py grep="capabilit" -->
- Saved views in `wd viz`: the interactive visualizer can now save named
  views and restore them later, behind an opt-in write flag so the graph
  and its saved-view store are written only when you ask.
  <!-- verify: file=weld/viz/_views.py grep="saved" -->

### Changed

- Bounded read envelope: read commands and MCP tools now return a
  size-bounded result envelope (neighbor spray is filtered and fan-out is
  capped against a byte budget), so large graphs return focused,
  predictable context instead of oversized payloads.
  <!-- verify: file=weld/_envelope_diet.py grep="envelope" -->
- CLI and MCP now return identical results: `weld_query` and the other
  read paths route through a single shared read implementation, so a `wd`
  command with `--json` and the equivalent MCP tool call produce the same
  output.
  <!-- verify: file=weld/read.py grep="read_query" -->
- Faster incremental refresh: on-read graph refresh re-parses only the
  modules whose source actually changed and caches the dirty-tree result
  by working-tree signature, so repeated reads between edits stay fast on
  large repositories.
  <!-- verify: file=weld/_refresh_cache.py grep="signature" -->
- The bundled tree-sitter grammars are pinned to the 0.25.x line
  (tree-sitter 0.25.2), keeping parser behavior consistent across
  platforms.
  <!-- verify: file=third_party/python/requirements_lock.txt grep="tree-sitter==0.25.2" -->

## v0.21.0 - 2026-06-17

### Added

- Rust, Go, and TypeScript are now Tier 1 in the language-support
  ladder, joining Python, C#, Java, and C++. Tier 1 means each
  strategy stack passes the measurable promotion contract — canonical
  kind vocabulary, class-level edge accuracy, framework strategies,
  deterministic output across reruns, polyrepo federation, enrichment
  coverage, gold-query F1 against a pinned corpus, and a per-corpus
  performance budget — rather than being an editorial claim. The
  language-support table is now generated directly from those
  baselines, so the documented status can no longer drift from what
  the harness actually measures.
  <!-- verify: file=README.md grep="tree-sitter-rust" -->
- New framework strategies for `axum` (Rust), `gin` (Go), and
  `express` (TypeScript / JavaScript). `wd init` auto-detects these
  frameworks and wires the strategy automatically, so routes and
  handlers surface as graph nodes for `wd query` and `wd context`
  the same way FastAPI, Django, and Flask already do.
  <!-- verify: file=weld/strategies/axum.py grep=axum -->
- Go discovery now emits `inherits` edges for struct embedding and
  `implements` edges where a type satisfies an interface, so
  `wd context` on a Go type shows its real structural relationships
  instead of stopping at declarations.
  <!-- verify: file=weld/strategies/_go_inherits.py grep=implements -->
- `wd viz` gained a substantial interactive overhaul: a corner
  minimap, full keyboard shortcuts with a cheatsheet, shareable
  views via URL hash state, browser back/forward through view
  history, a node-type legend, manual layout control, an
  open-in-editor action (VS Code and git-remote links), and a
  clearer A/B path workflow with persistent pills. The graph is now
  navigable without leaving the canvas.
  <!-- verify: file=weld/viz/static/app.js grep=keyboard -->
- `wd viz` now includes an in-UI Changes tab that renders the graph
  diff between the working tree and the committed graph, plus an
  Export view menu for saving the current view.
  <!-- verify: file=weld/viz/_diff.py grep=diff -->
- Static frontend assets are now discoverable: a `viz_frontend`
  strategy surfaces frontend files as queryable file nodes so they
  appear in `wd query` and `wd find` rather than being invisible to
  the graph.
  <!-- verify: file=weld/strategies/viz_frontend.py grep=frontend -->
- New `wd warm` command fetches a prebuilt graph artifact so a fresh
  checkout or CI job can come up with a ready graph without running a
  full local discovery first.
  <!-- verify: file=weld/warm.py grep=warm -->
- The CLI and MCP server now share a single structured error contract:
  failures carry a stable error code and an actionable hint, and the
  same code surfaces whether you hit the error from `wd` or through an
  MCP tool call.
  <!-- verify: file=weld/_errors.py grep=hint -->
- Query results now understand singular/plural equivalence, so a
  search for `handlers` matches `handler` and vice versa without a
  manual synonym list.
  <!-- verify: file=weld/synonyms.py grep=plural -->

### Changed

- Reads now self-heal. `wd` and the MCP server refresh the graph
  incrementally on read — the no-change path is sub-second — and a
  root in a polyrepo workspace auto-recurses into stale children, so
  query and context results reflect the current source without a
  manual `wd discover` between edits.
  <!-- verify: file=weld/_auto_refresh.py grep=refresh -->
- Volatile graph metadata (`updated_at`, `git_sha`) moved out of the
  tracked graph into a gitignored sidecar, so routine `wd` reads no
  longer produce spurious graph diffs and version-control noise.
  <!-- verify: file=weld/_graph_meta_sidecar.py grep=git_sha -->
- Unresolved-reference sentinels are now hidden from query output by
  default, and results carry an explicit confidence indicator so
  low-confidence matches are visible rather than silently mixed in.
  <!-- verify: file=weld/_confidence_defaults.py grep=confidence -->
- Discovery post-processing fuses what were two canonicalization
  passes into one, trimming hundreds of milliseconds off a warm
  discover on a large repository with no change in output.
  <!-- verify: file=weld/_discover_postprocess.py grep=canonical -->

### Fixed

- Polyrepo federation query paths now apply OR-fallback consistently,
  so a multi-term query returns the same results whether or not the
  eager federation index is in use.
  <!-- verify: file=weld/_federation_eager_or_fallback.py grep=fallback -->
- A corrupt current graph now surfaces as an explicit
  `graph_corrupt` error instead of silently returning an empty diff
  or empty results.
  <!-- verify: file=weld/_errors.py grep=graph_corrupt -->
- The bundled YAML parser now expands literal and folded block
  scalars, so multi-line values in `.weld` configuration files are
  read correctly without requiring a system YAML library.
  <!-- verify: file=weld/_yaml_block_scalar.py grep=scalar -->

## v0.20.1 - 2026-05-18

### Fixed

- Test suites that exercise the tree-sitter language bindings now
  skip cleanly when the optional `tree_sitter` Python module is not
  installed, instead of failing with `ModuleNotFoundError`. This
  affects the C++, C#, Java, and federation tier-check gates and the
  bench adapter's smoke tests. Users who install `configflux-weld`
  without the `tree-sitter` extra can now run `python -m pytest`
  against the bundled tests without seeing spurious failures.

### Changed

- Continuous integration now installs `tree_sitter` plus the bundled
  language grammars before running the test suite, so the tree-sitter
  smoke coverage actually runs (rather than skipping) in the public
  CI pipeline. End users see no functional change; the effect is
  fewer regressions slipping past CI between releases.

## v0.20.0 - 2026-05-17

### Added

- C#, Python, and Java are now Tier 1 in the language-support ladder.
  Tier 1 means the strategy stack passes a measurable promotion
  contract — canonical kind vocabulary, class-level edge accuracy,
  framework strategies, deterministic output across reruns, polyrepo
  federation participation, enrichment description coverage, gold
  query F1 against pinned corpora, and a per-corpus performance
  budget — rather than editorial claim. The new `wd tier-check`
  surface gates promotion and is run automatically against bundled
  fixtures on every change. C++ stays at Preview pending a Tier 1
  baseline snapshot.
  <!-- verify: file=README.md grep="Tier 1" -->
- Real bundled fixtures exercise the C#, Python, Java, and C++
  tree-sitter strategy stacks end-to-end inside the test suite,
  so language regressions surface locally before they reach
  downstream consumers. Each fixture ships with gold-query F1
  expectations checked into version control and verified against
  the discovered graph on every test run.
  <!-- verify: file=tools/tier_check.py grep=criterion -->
- New `flask` strategy joins the Python framework lineup. `wd init`
  auto-detects Flask projects (root-level `app.py`, `src/flask/**`
  layouts, or any module exposing a `Flask(__name__)` factory) and
  wires the strategy automatically. Flask routes, blueprints, and
  view functions surface as graph nodes so `wd query <route>` and
  `wd context <handler>` work the same way they already do for
  FastAPI and Django.
  <!-- verify: file=weld/strategies/flask.py grep=Flask -->
- FastAPI handler symbols now expose `exposes` edges from each
  route-decorated function to its mounted path, closing a parity
  gap with Django's URL-pattern edges. `wd query` on an HTTP path
  now resolves to the Python function that handles it without
  needing a `wd context` second hop.
  <!-- verify: file=weld/strategies/fastapi.py grep=exposes -->
- Pytest-style `test_*.py` files are now recognised as test peers
  alongside the existing `*_test.py` convention. The `test_peer`
  strategy tags both filename shapes with `props.origin = "test"`
  and emits `tests` edges from the test file to the module under
  test for `src/<pkg>/` layouts. Projects that follow the pytest
  convention get the same `wd query` "show me the tests for X"
  affordance as projects that use the Bazel-style suffix.
  <!-- verify: file=weld/strategies/test_peer.py grep=test_ -->
- `wd init` now auto-detects Java tree-sitter sources and wires
  the `java_tree_sitter` strategy automatically. Maven and Gradle
  projects with `src/main/java/**` layouts get the same lights-up
  behaviour C# and Python projects already have, without a manual
  `.weld/discover.yaml` edit.
  <!-- verify: file=weld/strategies/_java_tree_sitter.py grep=class -->
- `wd init` now auto-wires the canonical C++ singleton config files
  (`.clang-format`, `.clang-tidy`, `WORKSPACE`, `WORKSPACE.bazel`) when
  they exist at the repository root, so polyglot repos that include
  these files light up in `wd query` and `wd context` without a manual
  `.weld/discover.yaml` edit. Nested copies are intentionally not
  picked up — singletons at the root reflect repository-wide
  configuration, while nested ones are diagnostic-only.
  <!-- verify: file=weld/_init_cpp.py grep=has_clang_format -->
- C++ navigation queries now boost single-include amalgamation files
  (`single_include/`, `dist/single_header/`, `amalgamated/`, and
  `*amalgamation*` paths) so the import-surface header outranks
  same-score modular peers on single-token searches. Discovery stamps
  `props.amalgamation = true` at file-node minting time, and
  `wd query <name>` uses the marker as a coarse tiebreak ahead of the
  score so libraries like `nlohmann/json` surface their canonical
  include first. Non-C++ ranking is unchanged.
  <!-- verify: file=weld/ranking.py grep=is_amalgamation_file_node -->
- Opt-in eager inverted-index aggregation for polyrepo federation.
  High-QPS callers (MCP servers, batch evaluators) can now build a
  single in-memory index across every fresh-sidecar child at
  construction time and serve queries from memory instead of paying
  per-query SQLite reads. Enable via `FederatedGraph(root,
  eager_index=True)` or the `WELD_FEDERATION_EAGER=1` environment
  variable; the default stays lazy so single-shot CLI queries are
  unaffected. Match sets are byte-identical to the lazy path.
  <!-- verify: file=weld/federation.py grep=eager_index -->
- C++ tree-sitter discovery captures type-USE sites via a new
  `type_uses` query (template_type heads, parameter and return types,
  base-class clauses, friend declarations), exposed as a sorted and
  deduped `type_uses` prop on the C++ file node. Headers that only
  USE a type (rather than defining it or calling its methods) now
  rank for it in `wd query <Class>` — closing a gap where
  `include/nlohmann/json.hpp` did not surface for `json_pointer`.
  <!-- verify: file=weld/languages/cpp.yaml grep=type_uses -->
- `wd init` now auto-wires C++ build-system globs
  (`**/CMakeLists.txt`, `**/BUILD.bazel`, `**/BUILD`, `**/meson.build`)
  through the `cpp_buildsystem_detector` strategy, plus a root
  `CMakeLists.txt` singleton via `config_file`. C++ repos get build
  graph nodes referencing their CMake or Bazel files without an
  explicit `.weld/discover.yaml` edit.
  <!-- verify: file=weld/_init_cpp.py grep=cpp_buildsystem_source_entries -->
- New `csharp_package` strategy walks `.cs` files, mints
  `package:csharp:<dotted>` nodes from each file's primary namespace
  (file-scoped or first block-scoped), and emits `contains` edges
  from package to file. C# graphs now follow the same per-namespace
  anchoring pattern Python and Java already have, so `wd query` and
  `wd context` on a C# namespace returns its files. Auto-wired by
  `wd init` whenever any `.cs` files are detected.
  <!-- verify: file=weld/strategies/csharp_package.py grep=package:csharp -->
- Markdown headings inside doc nodes are now indexed so multi-token
  `wd query` searches resolve to the relevant section of a long
  document rather than only to the document as a whole. Headings
  are tokenised on the same vocabulary as filenames and symbols, so
  queries like `wd query "release smoke"` land on the right
  subsection of a README or runbook.
  <!-- verify: file=weld/query_index.py grep=heading -->
- C# discovery emits `contains` edges from each `.csproj` to its
  member `.cs` files. Federation producers see the project as the
  natural anchor for its files, so `wd context <csproj>` returns the
  expected file set and cross-repo imports resolve through the
  project node rather than synthesising a fallback container.
  <!-- verify: file=weld/strategies/csharp_msbuild_targets.py grep=contains -->
- The `package_import_resolver` cross-repo resolver now recognises
  C# `using` statements alongside Python `import`/`from`. Polyrepo
  workspaces that mix .NET services and Python libraries resolve
  import edges across both languages without separate manual
  resolver registrations.
  <!-- verify: file=weld/cross_repo/package_import_resolver.py grep=csharp -->
- Federation display IDs are now rendered with the child-repo prefix
  in the CLI so it is obvious which repo a result came from in
  polyrepo workspaces. Existing JSON output continues to carry the
  full canonical ID; only the human-readable display form changed.
  <!-- verify: file=weld/_cli_render.py grep=display_id -->

### Changed

- README "Supported languages" now uses a single tier ladder
  (Tier 1 / Tier 2 / Preview / Experimental / Not supported)
  in place of six separate status vocabularies. Tier movement is
  harness-gated against a measurable contract (kind correctness,
  class-level edge accuracy, framework strategies, determinism,
  federation participation, enrichment coverage, query F1, and a
  performance budget) rather than editorial claim. ROS2 moves to a
  Frameworks sub-table (host language: C++ / Python). TypeScript
  and JavaScript split into separate rows.
  <!-- verify: file=README.md grep="tier-check harness" -->
- PyPI-facing `weld/README.md` gains a "Language support" section
  that lifts the tier-ladder paragraph, language table, and
  frameworks sub-table from the root README, so PyPI readers can
  answer "does weld support my language?" without leaving the page.
  The existing "Platform coverage" link is renamed to "AI client
  platform coverage" so it is unambiguously about AI clients
  (Claude Code, Codex, Cursor, Copilot, Gemini, OpenCode, generic
  MCP), not programming languages.
  <!-- verify: file=weld/README.md grep="Language support" -->
- Test peer files now carry `props.origin = "test"` and the closure
  layer extends the same provenance to test-only packages, so
  `wd query` can filter test code in or out of result sets without
  reparsing filenames. Production callers that want non-test results
  only can rely on the prop directly.
  <!-- verify: file=weld/strategies/test_peer.py grep=origin -->
- Per-discover parse cache for tree-sitter grammars: the grammar
  load and parsed tree for each file are now reused across the
  multiple queries that run during a single discover pass, instead
  of being rebuilt per query. The cache lives for the duration of
  one `wd discover` call and is discarded between runs; output is
  byte-identical to the pre-cache path. Discovery on large C# and
  C++ repos sees materially less CPU time without behavioural drift.
  <!-- verify: file=weld/strategies/_ts_parse.py grep=parse_cache -->

### Fixed

- C# external base-class references now resolve to a single
  canonical FQN node rather than minting one symbol per
  partial-class declaration. Inheritance chains across files of the
  same partial class collapse to the expected target instead of
  scattering across one-off siblings.
  <!-- verify: file=weld/strategies/_csharp_inheritance.py grep=canonical -->
- C# package nodes are minted from `.csproj` for federation
  producers even when no `.cs` file inside the project triggered a
  namespace mint, so polyrepo workspaces get a consistent package
  anchor on the child side regardless of which file the federation
  consumer asks for first.
  <!-- verify: file=weld/strategies/csharp_msbuild_targets.py grep=csproj -->
- C# `inherits` and `implements` edges originate at the class
  symbol node rather than at the file node, so multi-class files
  no longer collapse distinct inheritance chains together and
  `wd context symbol:csharp:<class>` returns the correct
  inheritance neighbours per class.
  <!-- verify: file=weld/strategies/_csharp_inheritance.py grep="symbol:" -->
- C# canonical kinds are now singular and suffix-strip-free
  (`controller` rather than `controllers`, `dbcontext` rather than
  `dbcontexts`). The tier-check kind vocabulary recognises both
  the legacy plural and the new singular form during the
  transition, but newly emitted graphs use the canonical singular
  consistently.
  <!-- verify: file=weld/strategies/_csharp_tree_sitter.py grep=kind -->
- C# top-level statement files (`Program.cs` with no namespace
  declaration) are now tagged with `entrypoint` role on the file
  anchor, so the file-anchor-symmetry rule's entrypoint allow-list
  recognises them and the runtime classification matches the
  already-emitted `entrypoint:` and `boundary:` runtime nodes.
  <!-- verify: file=weld/strategies/_csharp_tree_sitter.py grep=entrypoint -->
- External package IDs minted by graph closure for unresolved
  imports now route through the canonical `package_id` helper so
  they agree case-for-case with the strategy-side minters. Mixed-case
  C# imports (e.g. `System` next to `system`) no longer produce
  case-variant duplicate `package:csharp:*` nodes; a discovery pass on
  a real C# repo found around 130 such pairs before this fix. Symbol
  IDs continue to preserve case because most languages legitimately
  distinguish members like `SIZE` and `Size`.
  <!-- verify: file=weld/graph_closure.py grep=_ensure_package_node -->
- Canonical-id-uniqueness rule preserves case for `symbol:*` IDs,
  so case-different members on the same enclosing type (`SIZE`
  constant vs `Size` property in C#, Java, or C++) are no longer
  collapsed into a spurious uniqueness violation. Package IDs still
  casefold via `canonical_slug`; the split keeps namespace
  deduplication honest without losing legitimate member
  distinctions.
  <!-- verify: file=weld/_graph_closure_invariants.py grep=canonical_slug_case_sensitive -->
- File-node IDs preserve case on case-sensitive filesystems, so
  `Foo.cs` and `foo.cs` (legitimately distinct on Linux) no longer
  collapse to a single node. Discovery on Linux build hosts now
  matches the source-tree shape rather than the Windows-case-fold
  shape the previous implementation imposed.
  <!-- verify: file=weld/_node_ids.py grep=canonical_slug_case_sensitive -->
- `wd query` now promotes exact tree-sitter symbol matches above
  prose mentions, so a query that is a literal symbol name surfaces
  the symbol node ahead of documentation or other files that merely
  mention the name in passing.
  <!-- verify: file=weld/ranking.py grep=exact_symbol_match_rank -->

## v0.19.1 - 2026-05-12

### Fixed

- Tree-sitter discovery no longer fails to load per-language grammars in
  hermetic build sandboxes. The previous probe used a top-level
  `find_spec` check that could short-circuit before the grammar
  packages were actually importable on the sandboxed Python path; the
  probe now defers to a real import attempt and only marks a grammar
  unavailable when the import itself raises. End users on a normal
  `pip install` saw no functional change in v0.19.0 — this patch
  exists to unblock automated test runs in hermetic environments.
  <!-- verify: file=weld/strategies/tree_sitter.py -->

## v0.19.0 - 2026-05-12

### Added

- C# discovery now auto-wires its full strategy stack on `wd init` when
  matching artifacts are present: solution/project parsing, MSBuild
  target extraction, test-framework detection (xUnit / NUnit / MSTest),
  ASP.NET route extraction, and EF Core entity/relationship surfacing.
  Polyglot repos that previously needed manual `.weld/discover.yaml`
  edits to opt C# in now light up out of the box.
  <!-- verify: file=weld/init.py grep=csharp -->
- C# inheritance edges (`inherits` and `implements`) are now extracted
  from the `base_list` syntax node, so class hierarchies and interface
  contracts show up in graph queries and visualizations rather than
  being silently dropped during discovery.
  <!-- verify: file=weld/strategies/_csharp_inheritance.py -->
- Per-method call graphs are now on by default for C# tree-sitter
  discovery (`emit_calls: true`). `wd query` and downstream tooling
  see method-level edges automatically without a config override.
  <!-- verify: file=weld/init.py grep=emit_calls -->

### Fixed

- Per-grammar tree-sitter warnings now surface to stderr end-to-end.
  When a grammar is missing or fails to load, discovery emits a clear
  warning rather than silently no-op-ing — previously a misconfigured
  grammar could drop entire languages from the graph with no signal.
  <!-- verify: file=weld/strategies/_ts_parse.py grep=append_missing_grammar_warning -->
- The `csharp_msbuild_targets` glob now matches case-insensitively,
  so `*.csproj`, `*.CSPROJ`, and other case variants found in Windows-
  authored repos are no longer missed during discovery.
  <!-- verify: file=weld/strategies/csharp_msbuild_targets.py grep=case-insensitive -->
- Canonical-id-uniqueness rule now preserves the URL scheme when
  comparing IDs, so `csproj://X` and `solution://X` are no longer
  collapsed into a spurious uniqueness violation. The previous behavior
  could mask real graph nodes during cross-repo federation.
  <!-- verify: file=weld/_graph_closure_invariants.py grep=_url_scheme -->
- `load_ts_language` now handles both the legacy `language()` export
  and the newer `language_typescript()` / `language_tsx()` function
  names introduced in `tree-sitter-typescript >= 0.23`. Prior installs
  on newer grammar versions would fail with `AttributeError` at TS
  discovery time.
  <!-- verify: file=weld/strategies/_ts_parse.py grep=language_typescript -->

## v0.18.2 - 2026-05-11

### Fixed

- Public CI on the v0.18.1 tag stayed red on two surfaces. (1) The
  doc-validation test that reads `docs/determinism-audit-T1a.md`
  (a file v0.18.0 excluded from the publish set) now skips gracefully
  when the file is absent rather than raising `FileNotFoundError`.
  (2) The public-surface audit grew a per-pattern allowlist so the
  enrichment provider precedence chain in `weld/_first_run_enrich.py`
  and `weld/_first_run_render.py` can keep its provider env-var-name
  literals (part of the public-API contract for first-run enrichment)
  without being treated as embedded credentials.
  <!-- verify: file=tools/public_surface_audit.py grep=ENV_VAR_NAME_ALLOWLIST -->

## v0.18.1 - 2026-05-11

### Fixed

- Public CI on the v0.18.0 tag failed at `bazel build` with
  "missing input file" errors for three test targets
  (`weld_first_run_detect_test`, `weld_first_run_enrich_test`,
  `weld_discover_first_run_test`). The corresponding source files are
  intentionally not shipped, but the public `BUILD.bazel` still
  referenced them. The publish-time BUILD-stripping logic now drops
  those names from the comprehension before sync, so the public
  `bazel test //...` no longer breaks on missing inputs.
  <!-- verify: file=tools/publish_strip_exclusions.py grep=_FIRST_RUN_NAMES -->

## v0.18.0 - 2026-05-11

### Added

- `wd review` JSON-first triage CLI for speculative edges. Subcommands
  `list / show / accept / reject / reset / status` operate on a stable
  16-hex edge id minted from
  `sha1(from + "\x00" + to + "\x00" + type + "\x00" + source_strategy)`.
  Accepted edges promote `speculative -> definite` in place; rejected
  edges are dropped at the next `wd discover` via the post-process
  contract, with a ghost-emit warning when a strategy keeps re-emitting
  a rejected edge. Bulk `--pattern` operations are gated by `--yes` and
  bounded against ReDoS (regex length cap, nested-quantifier reject,
  match-length clamp). State lives at `.weld/review-state.json` and is
  gitignored by default.
- MCP server: 14 graph-backed tools (`weld_query`, `weld_find`,
  `weld_context`, `weld_path`, `weld_brief`, `weld_stale`,
  `weld_callers`, `weld_references`, `weld_export`, `weld_trace`,
  `weld_impact`, `weld_enrich`, `weld_diff`, `weld_review`). The new
  `weld_review` tool exposes `op=list / show / accept / reject` for
  agent-driven triage of the speculative-edge backlog.
  <!-- verify: file=weld/mcp_helpers.py grep=build_review_tool -->
- `wd bench --public` produces a committable real-corpus benchmark
  report against user-supplied codebases. A libclang variant adapter
  joins the default tree-sitter path so C++ semantic coverage can be
  compared head-to-head when a `compile_commands.json` is available;
  when libclang or its prerequisites are missing, the adapter reports
  `unavailable` instead of producing misleading zero scores.

### Changed

- README "Supported languages" table and C++ subsection now explicitly
  label C++ (and ROS2, which inherits the C++ caveat) as **Tier 2
  (preview), not Tier 1**. The substance was already documented as
  "ships and runs, quality not yet measured at scale"; the change makes
  the tier label unambiguous for readers skimming the table. The same
  README section was reorganised to put the multi-language install path
  up front instead of buried.
  <!-- verify: file=README.md grep="Tier 2 (preview)" -->
- `FederatedGraph.query` now builds a per-query inverted index lazily,
  so cold-cache queries on large federations stop paying full-graph
  tokenization cost up front. Ranking and result semantics are
  unchanged; the change is observable only as faster first-query
  latency on large federations.
  <!-- verify: file=weld/federation.py grep="lazy per-query inverted index" -->

### Fixed

- `weld` public-benchmark adapter now reports `unavailable` (instead of
  silently scoring zero) when the `[tree-sitter]` extra is not
  installed. Prior runs that lacked the extra recorded misleadingly low
  F1 numbers; the adapter is now honest about the missing capability,
  and the published benchmark numbers have been refreshed with a real
  libclang run against `nlohmann/json`.

## v0.17.2 - 2026-05-07

### Fixed

- Test-suite isolation: `weld_graph_integrity_regression_test` now
  re-checks for `.weld/discover.yaml` at test execution time rather
  than at module import time. This avoids a spurious failure when
  another test in the same parallel run transiently creates and
  deletes the workspace's discover.yaml between import and test
  execution.
  <!-- verify: file=weld/tests/weld_graph_integrity_regression_test.py grep=_DiscoverYamlGated -->

## v0.17.1 - 2026-05-07

### Fixed

- Blast-radius fixture test (`weld_blast_radius_fixtures_test`) now
  passes consistently across published and source-tree environments.
  The harness was reading capability-classification settings from the
  surrounding workspace's `.weld/discover.yaml` instead of from each
  fixture's own config, causing identical test data to drift between
  environments. Fixed by scoping the harness's `Graph` wrapper to the
  fixture's scratch directory.
  <!-- verify: file=weld/tests/_blast_radius_harness.py grep=scratch_root -->

## v0.17.0 - 2026-05-07

### Added

- `wd impact` ships as a v1 blast-radius command. Multi-seed selection
  resolves seeds from a node ID, an explicit file list (`--files`), the
  current working tree (`--working-tree`), or a git diff range
  (`--from-diff REF`); the diff parser uses hardened porcelain rename
  detection so file moves do not orphan their seeds. Output carries a
  runtime capability matrix and structured `warnings` (including a
  `stale_graph` warning), and a stale-graph gate refuses to emit results
  unless `--allow-stale` is passed. A `<framework>` fixture suite
  (Python pip, Bazel Python, TypeScript Node, Dockerfile/Compose)
  underwrites the multi-language coverage with end-to-end expected-output
  fixtures.
  <!-- verify: file=weld/impact_cli.py grep=from-diff -->
- Multi-language origin classification stamps `props.origin` at strategy
  emission time across Python, Go, TypeScript / JavaScript, Rust, Java,
  C#, and C++. Origins are now produced by the language strategies
  themselves rather than derived after the fact, which collapses an
  entire class of post-discovery drift between origin and language. The
  visualization adapter accepts a `hide_origins` filter so external,
  stdlib, and toolchain nodes can be hidden from the graph view, and
  polyrepo federation re-tags cross-child origins from `external` to
  `project` when one child imports another.
  <!-- verify: file=weld/_graph_origin.py grep=ORIGINS -->
- C++ system-include resolution discovers includes installed under Nix
  store paths, Conda environments, `/opt`, Bazel hermetic toolchain
  roots, and the platform toolchain's standard search paths. C++ nodes
  whose include path resolves to one of these origins are now linked
  rather than left unresolved.
  <!-- verify: file=weld/strategies/_cpp_system_include.py -->
- Bazel `srcs` and `deps` edges connect each `BUILD.bazel` rule to its
  source files and dependency labels, so `wd query` and `wd impact` can
  walk the build graph alongside the language graphs.
  <!-- verify: file=weld/strategies/bazel.py grep=srcs -->
- Dockerfile and Compose edges link container images to their build
  context. `COPY` and `ADD` instructions whose source resolves to a
  repo-relative path emit edges to the copied file, and a directory-COPY
  bridge expands `COPY ./app /service/app` into per-file edges to every
  source file inside the bridged directory.
  <!-- verify: file=weld/strategies/dockerfile.py grep=COPY -->
- Multi-language test-peer edges discover each module's canonical
  sibling test across Python, Go, TypeScript / JavaScript, Rust, Java,
  and C#. `wd query` and `wd find` can now locate the nearest test for
  a non-Python module directly from the graph.
  <!-- verify: file=weld/strategies/test_peer.py grep=test_peer -->
- `wd find` ranks basename matches above prose mentions. A query that is
  a literal filename for a node in the graph now surfaces that file
  ahead of documentation that mentions the basename in passing, so the
  primary hit is the file itself rather than the doc that talks about
  it.
  <!-- verify: file=weld/file_index.py grep=basename -->
- `wd bench --compare` corpus expands from 4 to 26 tasks. The benchmark
  fixture covers a wider range of agent-prompt shapes (lookup,
  ownership, route discovery, dependency tracing, test-peer resolution),
  giving the comparative score against a grep baseline a more
  representative signal.
  <!-- verify: file=weld/bench_tasks/fixtures/default.yaml grep=id: -->
- Markdown inter-doc reference edges connect each Markdown file to the
  other Markdown files it links to, so doc-cluster discovery follows the
  hyperlink graph rather than only filename heuristics.
  <!-- verify: file=weld/strategies/markdown.py -->
- Per-file graph<->state cross-check on incremental discovery catches
  the case where the on-disk graph and the discovery state file disagree
  on which files have been processed. A file present in the state cache
  but missing from the graph (or vice versa) now triggers a re-discovery
  of that file rather than silently producing a partial graph.
  <!-- verify: file=weld/_discover_state_check.py grep=cross-check -->
- Capabilities classifier deduplicates multi-framework entries. When a
  capability matches more than one framework signal (e.g. a service
  that has both a `deploy_surface` annotation and a manifest entry), it
  appears exactly once in the capabilities output rather than producing
  duplicate rows.
  <!-- verify: file=weld/capabilities.py grep=deduplicated -->

### Changed

- Deprecated alias compatibility (Python file-anchor IDs and ROS2
  cluster IDs) remains in place. The v0.16.0 release notes flagged
  retirement in v0.17.0; that retirement is deferred to a future
  release. Pinning to 0.16.x is no longer required to keep external
  consumers of the prior IDs working — alias-aware lookup continues to
  resolve legacy IDs to their canonical form.
  <!-- verify: file=weld/_alias_index.py grep=build_alias_index -->

## v0.16.0 - 2026-05-03

### Added

- Canonical node-ID library (`weld._node_ids`) provides one slug rule and one
  shape rule for `file:`, `package:`, and entity IDs across every discovery
  strategy. Replaces three divergent local `_slug` implementations with a
  shared `canonical_slug` so paired strategies cannot drift on character-set
  rules.
  <!-- verify: file=weld/_node_ids.py grep=canonical_slug -->
- Unified `ensure_node` primitive (`weld._graph_node_registry`) replaces the
  twelve ad-hoc `_ensure_*` helpers across the agent-graph, ROS2, and
  graph-closure layers. Two creation paths for the same logical entity now
  merge into one node with set-union of `sources`, `aliases`, and list props,
  and authority-precedence resolution on conflicts. Order-independent by
  construction.
  <!-- verify: file=weld/_graph_node_registry.py grep=ensure_node -->
- Three new architectural lint rules close the closure-determinism gap by
  construction: `canonical-id-uniqueness` rejects two non-aliased nodes
  sharing a normalized canonical key; `file-anchor-symmetry` rejects file
  nodes with outgoing `contains` but no inbound edge; and
  `strategy-pair-consistency` rejects file-set drift between paired
  strategies (e.g. `python_module` vs `python_callgraph`).
  <!-- verify: file=weld/_graph_closure_invariants.py grep=check_canonical_id_uniqueness -->
- Alias-aware lookup resolves legacy node IDs to their canonical form across
  `wd query`, `wd context`, `wd path`, `wd export`, and the MCP server. The
  alias index is built once at graph-load time and rides alongside the BM25
  cache in `_query_sidecar`; the index invalidates by graph hash so old IDs
  cannot bleed across upgrades. Defensive collision guard refuses to alias
  into another node's canonical id.
  <!-- verify: file=weld/_alias_index.py grep=build_alias_index -->
- `python_package` strategy emits `package:python:<dotted>` nodes with
  `contains` edges to each `*.py` file member, including a synthetic
  `package:python:tools` namespace for `tools/` scripts that lack a Python
  package marker file. Closes 134 file-anchor-symmetry violations the rule
  would otherwise flag for the weld and tools surfaces.
  <!-- verify: file=weld/strategies/python_package.py grep=_dotted_name -->
- `python_module._extract_imports` now walks function-local lazy imports in
  addition to top-level imports, matching the qualified `from foo import _bar`
  pattern that strategy plugins use to avoid circular dependencies. Captures
  the lazy import in `weld/strategies/ros2_topology.py:347` so
  `file:weld/strategies/_ros2_py` gains an inbound `depends_on` edge.
  <!-- verify: file=weld/strategies/python_module.py grep=_extract_imports -->

### Fixed

- Skill nodes no longer duplicate when the same logical skill is reached via
  multiple discovery paths. Previously `skill:generic:architecture-decision`
  surfaced as two separate nodes (one with edges, one orphan) because the
  collision-suffix mechanism in `agent_graph_materialize._node_id_for_values`
  hashed the discovery path into the ID. The merge now happens at construction
  via `ensure_node`, with all source paths recorded under `sources` and prior
  hash-suffixed IDs preserved under `aliases`.
  <!-- verify: file=weld/agent_graph_materialize.py grep=ensure_node -->
- `file:weld/strategies/_ros2_py` is no longer a structurally orphan file
  anchor. Two independent fixes contribute: the lazy-import capture above
  surfaces the `depends_on` edge from `ros2_topology`, and the new
  `python_package` strategy supplies the `contains` edge from
  `package:python:weld.strategies`.
  <!-- verify: file=weld/_graph_closure_invariants.py grep=check_file_anchor_symmetry -->

### Breaking changes

- **Graph node IDs renamed — Python file anchors.** Python file
  IDs change shape from `file:{stem}` to `file:{rel_posix_path_without_ext}`
  in 0.16.0 to eliminate stem-only collisions across directories
  (`weld/strategies/python_module.py` and `tools/python_module.py` previously
  collapsed onto the same `file:python_module` ID). The `python_module`
  strategy also drops its unilateral `_*`-skip rule so paired strategies
  (`python_module` + `python_callgraph`) process the same input set; the
  symptom this closes is the orphan `file:weld/strategies/_ros2_py` shape
  reported on 2026-05-02.

  Old IDs are preserved on each renamed node's `props.aliases` list for one
  minor version. `wd query`, `wd context`, the MCP server, and the query
  sidecar resolve old IDs transparently through the alias index (alias-aware
  lookup ships in PR 2). Sidecar caches invalidate by graph hash on first
  discovery after upgrade; no stale-cache hits possible.

  **User-visible impact:**
  - Existing MCP conversation transcripts referencing old `file:<stem>` IDs
    continue to work via alias lookup.
  - Prior `wd query` JSON output that recorded an old ID can be fed back
    into `wd context <old-id>` and will resolve.
  - Files starting with `_` (other than the package init module) now appear
    as graph nodes; expected node-count delta is small but non-zero on this
    repo.

  **Deprecation:** aliases retire in 0.17.0. Pin to 0.16.x if external
  scripts assume the old prefix and cannot be updated before the next minor.

- **Skill node IDs.** Skill IDs no longer carry a SHA1 hash
  suffix when the same logical skill is reached via multiple discovery paths.
  Two-path collisions now merge into one node with both source paths recorded
  under `sources` and the prior suffixed IDs preserved under `aliases` for one
  minor version. Agent transcripts referencing `skill:generic:foo:abc12345`
  still resolve.
  <!-- verify: file=weld/agent_graph_materialize.py grep=legacy_skill_id_with_suffix -->

- **ROS2 cluster IDs renamed.** ROS2 package IDs change shape
  from `ros_package:<name>` to `package:ros2:<slug>` across the
  `ros2_package`, `ros2_interfaces`, `ros2_topology`, `ros2_launch`, and
  `ros2_cmake` strategies. ROS2 file-anchor IDs follow the same
  `file:<rel_posix_path_without_ext>` form as Python file anchors.

  Every renamed node carries the legacy ID under `props.aliases` for one
  minor version (retired in 0.17.0). The `_ensure_*` helpers across the ROS2
  cluster now route through the unified `weld._graph_node_registry.ensure_node`
  primitive so that two strategies materializing the same node merge their
  provenance instead of dropping the second claim.

  `.weld/discover.yaml` registers `strategy_pairs` entries for the ROS2,
  gRPC, and tree-sitter clusters. The strategy-pair-consistency rule is a
  structural no-op until a downstream workspace configures these strategies
  in `sources`; it then catches file-set drift with
  `pair_asymmetry_allowlist` entries documenting any genuine asymmetry.

  **User-visible impact:**
  - MCP transcripts and prior `wd query` JSON referencing `ros_package:<name>`
    still resolve via the alias index (alias-aware lookup landed in PR 2).
  - Sidecar caches invalidate by graph hash on first discovery after upgrade;
    no stale-cache hits possible.

  **Deprecation:** aliases retire in 0.17.0. Pin to 0.16.x if external
  scripts assume the old prefix and cannot be updated before the next minor.

- **gRPC and tree-sitter cluster IDs renamed.** gRPC
  rpc, contract, and enum IDs (`rpc:grpc:<package>.<service>.<method>`
  and friends) and the tree-sitter language family
  (`tree_sitter`, `typescript_exports`, `_csharp_tree_sitter`,
  `_java_tree_sitter`) now mint IDs through the canonical
  `weld._node_ids` contract. Mixed-case package and service names
  lower-case via `canonical_slug` (e.g. `package:csharp:Microsoft.AspNetCore.Mvc`
  -> `package:csharp:microsoft.aspnetcore.mvc`); tree-sitter file
  anchors move from `file:<stem>` to `file:<rel_posix_path_without_ext>`
  to match the form Python file anchors picked up in PR 1.

  The `runtime_contract` strategy's bespoke local `_slug` helper was
  deleted in favour of the shared `canonical_slug` so the URL-derived
  rpc IDs use the same rule as the rest of the graph. The `test_peer`
  strategy now mints `file:weld/tests/<stem>` (full path) instead of
  the legacy `file:tests/<stem>`, with the trailing `_test` suffix
  preserved on the stem so test/production semantic distinction is
  retained.

  Every renamed node carries the legacy ID under `props.aliases` for
  one minor version (retired in 0.17.0). MCP transcripts and prior
  `wd query` JSON referencing the legacy IDs continue to resolve via
  the alias index. Sidecar caches invalidate by graph hash on first
  discovery after upgrade.

  **Deprecation:** aliases retire in 0.17.0. Pin to 0.16.x if external
  scripts assume the old prefix and cannot be updated before the next
  minor.

## v0.15.0 - 2026-05-02

### Added

- `wd communities` reports projected graph community structure: the discovered
  graph is split into communities, unresolved-symbol sentinels are projected
  out, and each community surfaces its top hub nodes so users can navigate
  large graphs by topic instead of scanning a flat node list.
  <!-- verify: file=weld/graph_communities.py grep=build_graph_communities -->
- The retrieval surface (`wd query`, `wd find`, `wd context`, `wd path`,
  `wd callers`, `wd references`, `wd stale`, `wd stats`) now defaults to a
  human-readable text format. Pass `--json` for the machine-readable envelope
  used by tools and the MCP server.
  <!-- verify: file=weld/_cli_render.py grep=render_query -->
- `wd export` accepts the centre node id as a positional argument
  (`wd export <node>`); the legacy `--node <id>` flag is deprecated and prints
  a deprecation warning, but still works for one release.
  <!-- verify: file=weld/_export_cli.py grep=run_export -->
- `wd doctor` surfaces Agent Graph health as a first-class section, reporting
  agent count, broken references, and discovery diagnostics so missing or
  malformed agent definitions are caught alongside graph and provider checks.
  <!-- verify: file=weld/_doctor_agent_graph.py grep=check_agent_graph -->
- `wd lint` is signal-first: violations are grouped by rule with stable
  ordering, and orphan-detection now suppresses test files and obviously
  intentional standalone modules by default. Use the existing rule-disable
  flags to opt back in to noisier output.
  <!-- verify: file=weld/arch_lint_orphan.py grep=detect_orphans -->
- `wd discover` prints a one-line success summary to stderr (graph path,
  node and edge counts, elapsed time); pass `--quiet` to suppress it. Stdout
  still carries the canonical graph payload.
  <!-- verify: file=weld/_discover_summary.py grep=emit_summary -->
- `wd agents discover` text mode surfaces diagnostics (broken references,
  unresolved invocations, missing files) inline with the agent listing, so
  agent-graph problems are visible without dropping to `--json`.
  <!-- verify: file=weld/agent_graph_cli.py grep=_run_discover -->
- `wd stats` and `wd prime` reframe description coverage around *meaningful*
  nodes only (functions, classes, modules with non-trivial bodies), so the
  headline coverage metric reflects nodes a human would actually want to
  describe instead of being diluted by trivial graph artefacts.
  <!-- verify: file=weld/_prime_coverage.py grep=describe_meaningful_coverage -->

### Fixed

- `wd discover` re-runs strategies whose declared outputs are missing from
  the on-disk graph. Previously a partial discovery state could leave a
  strategy permanently skipped on subsequent runs; the discovery state now
  diffs declared-vs-present outputs and forces re-run when they disagree.
  <!-- verify: file=weld/discovery_state.py grep=DiscoveryState -->
- `wd communities` projects unresolved-symbol sentinels out of the community
  graph and reports top-level hubs per community. Earlier output put noisy
  unresolved nodes at the top of every community summary; the projection step
  removes them while preserving the underlying edges for hub ranking.
  <!-- verify: file=weld/graph_communities.py grep=_hub_nodes -->
- `wd find <basename>` now hits exact basenames such as `install.sh`,
  `BUILD.bazel`, or `pyproject.toml`. The file index previously only emitted
  tokenised path fragments, so bare basenames missed.
  <!-- verify: file=weld/file_index.py grep=_tokenize_path -->
- `wd enrich` lists the available providers and explains the agent-direct
  enrichment path when no provider is configured, instead of failing with a
  bare "missing provider" error.
  <!-- verify: file=weld/_enrich_safe.py grep=_format_no_provider_error -->
- `wd query` demotes unresolved-symbol sentinels (`symbol:unresolved:<name>`)
  in the ranker so resolved symbols outrank sentinels regardless of BM25
  delta; sentinels now only surface when nothing else matches.
  <!-- verify: file=weld/ranking.py grep=resolution_penalty -->
- `wd callers <bare-name>` resolves bare names the same way `wd references`
  does. Previously `wd callers DiscoveryState` errored "node not found"
  while `wd references DiscoveryState` worked; the resolver is now shared
  between both commands.
  <!-- verify: file=weld/graph.py grep=_resolve_symbol_name -->
- `wd query` falls back to a per-group OR union when a multi-token
  strict-AND query yields zero matches. Results are tagged with
  `degraded_match=or_fallback` so consumers know the result was relaxed;
  single-token queries skip the fallback.
  <!-- verify: file=weld/graph_query.py grep=query_or_fallback -->

## v0.14.0 - 2026-05-02

### Added

- `wd discover` now closes the graph deterministically across supported
  languages. Source-backed symbols link to their files, imports / includes /
  use edges resolve into deterministic dependencies, call edges carry
  provenance, and unresolved sentinels are reduced in `wd stats` and `wd viz`.
  <!-- verify: file=weld/graph_closure.py grep=close_graph -->
- New `test_peer` discovery strategy surfaces sibling `*_test.py` files for
  every Python module so `wd query` and `wd find` can locate a module's
  nearest unit test directly from the graph.
  <!-- verify: file=weld/strategies/test_peer.py grep=test_peer -->
- `wd find` and `wd query` now surface module-level Python constants
  (top-level UPPER_CASE assignments), so configuration values and defaults
  appear alongside functions and classes in search results.
  <!-- verify: file=weld/strategies/python_module.py grep=_module_constant_names -->
- `wd doctor` probes the standalone `copilot` CLI used by the `copilot-cli`
  enrichment provider, introduces a `[note]` level for soft recommendations
  (so missing optional providers and missing MCP config are no longer
  presented as `[warn]`), and adds `--ack <id>` / `--unack <id>` /
  `--list-acks` to persist per-project dismissals in `.weld/doctor.yaml`.
  <!-- verify: file=weld/_doctor_optional.py grep=copilot -->
- `wd agents discover` now infers references through `subagent_type=`,
  `Skill()` calls, and bare `/command` mentions inside agent and command
  bodies, surfaces `weld.invokes_agents` frontmatter for orchestrator
  agents, scans frontmatter descriptions for inferred references, applies
  an implicit-default `applies_to_path` to instruction files, parses
  `.codex/config.toml` as a Codex MCP source, and explodes
  `.claude/settings.json` permissions into per-entry edges. The bare
  `/command` terminator class extends to `!`, `?`, `]`, and `}`, and the
  `wd agents demo` fixture now mirrors a realistic seven-platform,
  nine-asset deployment.
  <!-- verify: file=weld/agent_graph_discovery.py -->

### Fixed

- `wd discover` keeps `.weld/file-index.json` in sync with `.weld/graph.json`.
  Stale index files were drifting after partial runs and causing `wd find` to
  miss recently-discovered files.
  <!-- verify: file=weld/discover.py grep=_persist_file_index -->

## v0.13.2 - 2026-04-30

### Added

- Workspace child scans can now opt into Git ignore rules with
  `scan.respect_gitignore: true`, `wd init --respect-gitignore`, or
  `wd workspace bootstrap --respect-gitignore`. The default remains
  compatibility-safe: gitignored child repos are still discovered unless the
  workspace opts in.
  <!-- verify: file=weld/_workspace_bootstrap_cli.py grep=--respect-gitignore -->
- `scan.exclude_paths` now accepts workspace-relative glob patterns with `*`
  and `**` in addition to bare directory names and exact paths, so workspace
  bootstraps can skip folders or extension-shaped generated directories.
  <!-- verify: file=weld/workspace_scan_filter.py grep=matches_exclude -->

## v0.13.1 - 2026-04-29

### Added

- `wd discover` now models a startup flow and trace import contract, with C# and C++ tree-sitter strategies that surface native and managed startup entrypoints alongside the existing Python entrypoint detection.
  <!-- verify: file=weld/trace_contract.py grep=TRACE_EDGE_TYPES -->
- `wd workspace bootstrap --exclude-path PATH` (repeatable) lets you pass scan exclusions on the command line; the values are persisted into the rewritten workspaces yaml so subsequent runs respect them.
  <!-- verify: file=weld/_workspace_bootstrap_cli.py grep=--exclude-path -->

### Fixed

- `wd workspace bootstrap` rescans now honor the workspace's configured `scan.exclude_paths` instead of walking ignored paths. Previously a workspace root containing operational nested repositories under an excluded prefix (e.g. a quarantine directory) could derive an invalid child name and abort bootstrap with `WorkspaceConfigError: invalid character in name`. Scan-only entries whose auto-derived child name fails validation are now filtered and reported instead of failing the whole run.
  <!-- verify: file=weld/_workspace_bootstrap_cli.py grep=exclude_paths -->

## v0.13.0 - 2026-04-28

### Added

- `wd agents viz` opens a local read-only browser explorer for `.weld/agent-graph.json`, reusing the existing graph visualizer while keeping `wd viz` focused on `.weld/graph.json`.

## v0.12.0 - 2026-04-28

### Added

- Local-only telemetry recording success/failure of CLI invocations and MCP tool calls. Default-on; opt out with `WELD_TELEMETRY=off`, `--no-telemetry`, or `wd telemetry disable`. Run `wd telemetry --help` for details.
- New `copilot-cli` enrichment provider for `wd enrich`. Uses the standalone `copilot` binary, so no API key is required (auth lives in the binary itself). Set `WELD_COPILOT_BINARY` to override the binary path.

### Fixed

- `wd init --output <dir>` now writes the polyrepo workspaces file alongside the discover config in the directory named by `--output`. Previously it was dropped at the working-directory default, which leaked into the source-of-truth `.weld/` and silently flipped subsequent `wd discover` runs into federation mode.

## v0.11.6 - 2026-04-28

### Changed

- `wd discover` examples in the README quickstart and the PyPI README now default to `--safe` mode. The trust-model section explains when it is appropriate to drop the flag. Both READMEs are aligned so the GitHub and PyPI evaluators see the same first-run command.

### Added

- New runtime-pending markers in `docs/runtime-validation.md` for the three `Partial` matrix rows awaiting live-client validation (Codex, Claude Code, VS Code/Copilot). The markers make it explicit that those rows have not yet been validated against a real client and have not been promoted to `Supported`.

### Fixed

- README markdown is no longer compressed into single-line paragraphs in raw form. Long prose in the description, "Try it in 5 minutes" call-out, and demo-script blurb is reflowed to <=200 characters per line for readability when reading the README on GitHub or via `cat`/`less`.

## v0.11.5 - 2026-04-27

### Fixed

- `wd init` inside a linked git worktree of a bootstrapped polyrepo now mirrors the main checkout's `.weld/workspaces.yaml` instead of silently degrading to a single-service graph. Linked worktrees do not contain copies of nested-git child repos (git does not clone them), so the FS scan returns empty and the worktree had no way to participate in federation -- `wd discover` produced a tiny local graph (~73 nodes for the reporter) instead of the federated one. The federation **discover** path already handles linked worktrees via `resolve_child_root`; `wd init` now uses the same `git_main_checkout_path` helper to inherit the registry. After this fix, `wd init` in a worktree produces `workspaces.yaml`, `workspace-state.json`, and a federated `wd discover` graph with no manual yaml restore needed. Operator-authored worktree-local yaml is preserved (`force=False` is honoured).

## v0.11.4 - 2026-04-27

### Fixed

- `wd workspace bootstrap` no longer misses nested-git children when the children dir matches a root `.gitignore` pattern. The FS scanner previously folded root gitignore into its exclusion set; polyrepos whose operator added `services/` (or any common children-dir name) to root `.gitignore` were silently masked, sending bootstrap to single-service mode and leaving `wd workspace status` permanently broken until manual recovery. A nested `.git` directory is now treated as a workspace child by definition -- gitignore tracks VCS state, not workspace topology. Callers that need project-specific exclusions must now pass them explicitly via `exclude_paths`.
- `wd init --force` at a polyrepo root now materialises `workspace-state.json` and runs the federated graph build (delegates to `bootstrap_workspace` after the per-child init step). Previously `wd init` only wrote yaml + per-child `discover.yaml`, leaving `wd workspace status` to fail until the operator separately ran `wd workspace bootstrap`.

## v0.11.3 - 2026-04-27

### Fixed

- `wd workspace bootstrap` no longer misroutes a nested-git polyrepo to single-service mode after a `.weld/` reset. Two federation predicates disagreed: `wd discover` decides federation by config presence, while bootstrap used a filesystem-only scan that honoured root `.gitignore`, `DEFAULT_MAX_DEPTH=4`, and `_BUILTIN_EXCLUDE_DIRS`. After `rm -rf .weld/` the FS scan could return zero even when the operator had restored a valid `.weld/workspaces.yaml`, leaving the workspace stuck without `.weld/workspace-state.json` and breaking `wd workspace status`. Bootstrap now uses a unified merge predicate where `workspaces.yaml` is authoritative when present and the FS scan augments it; corrupt yaml falls back to scan with the parse failure surfaced in `BootstrapResult.errors`. `wd init` at a polyrepo root now also runs per-child init so every child gets its own `.weld/discover.yaml`.

## v0.11.0 - 2026-04-27

### Added

- `wd bootstrap` adopts a managed-region marker model. Each bundled template under `weld/templates/` declares one or more `<!-- weld-managed:start name=... -->` regions; `wd bootstrap <fw> --diff` and the writer's no-op / refuse / clobber / append paths operate **inside** those markers only. Operator-curated content outside the markers is left untouched after the first write, so a single edited line outside a managed region no longer reads as a full-file replacement in `--diff`.
- `wd bootstrap` ships `--include-unmanaged`: paired with `--diff`, it falls back to the whole-file unified diff for operators who want to fully resync past the managed-region scope. The flag is rejected with a clear error when used outside `--diff`.
- `wd brief` falls back to an OR-of-tokens retrieval when its strict AND query returns zero matches on a multi-token query. The fallback result carries `degraded_match: "or_fallback"` so callers know they did not get the strict-AND ranking. `graph.query()`'s AND semantics are unchanged.
- Live-client runtime validation now has a real Codex AGENTS.md + skill record and clearly-marked `result: pending` stubs for Claude Code MCP, Claude Code skill/subagent, and VS Code Copilot custom instructions. A new launch-copy guard rejects platform claims in launch material that are not backed by a recorded row.

### Changed

- The pre-marker-layout migration: `wd bootstrap` prints an actionable message and exits non-zero on files that contain no `weld-managed:start` line; `--force` re-seeds the file with the bundled template verbatim (markers and all). No silent corruption, no heuristic anchor matching.
- The `_FEDERATION_PARAGRAPH` block appended in federation mode is itself a managed region named `federation`, so federated workspaces get the same drift-detection treatment as the rest of the bootstrap surface.
- README's comparison-table row for Sourcegraph drops the misleading "you commit with your code" line; the row now describes the actual config-only default and the `wd init --track-graphs` opt-in.
- The Copilot bundled skill template (`wd bootstrap copilot`) installs `weld` via `uv tool install configflux-weld` instead of the contributor `pip install -e ./weld` path.
- Bootstrap design and migration semantics finalized for the managed-region template model.

### Fixed

- `wd discover` in federated workspaces now stamps `meta.git_sha` on the root meta-graph. Single-repo discover already did so; the federated path skipped it, which made `wd prime --agent all` always print "graph.json has no git SHA — may be stale" immediately after a successful discover.

## v0.10.0 - 2026-04-26

### Added

- `wd init --track-graphs` is now actually shipped in the wheel. The opt-in keeps generated graphs (`graph.json`, `query_state.bin`, etc.) tracked in git so warm-CI / warm-MCP setups continue to work; without the flag the managed `.weld/.gitignore` follows the config-only default.
- Public install/contributor docs split into separate audiences in `README.md` and `CONTRIBUTING.md` so downstream consumers do not have to skim past contributor-only setup.

### Changed

- Public-facing runtime-validation copy tightened, and a dedicated `runtime_claims_lint` checks that documented runtime claims match the code.

## v0.9.0 - 2026-04-26

### Added

- `wd agents audit --strict` surfaces previously-suppressed canonical/rendered group pairs (they no longer hide audit findings when strict mode is set).
- `WELD_INIT_FRAMEWORK_CAP` env override lets forensic re-runs of `wd init` raise or remove the per-language framework sample cap; `0` disables the cap, custom positive integers set a custom cap, unset/empty/negative/non-numeric values fall back to the built-in default silently.
- Query state sidecar: `wd query` now persists the inverted index and BM25 corpus to `.weld/query_state.bin` after `wd discover`, so cold-path query startup drops from ~1.28 s to ~0.54 s on a representative 100k-node graph (about 58% faster). The sidecar is content-addressed via blake2b digest + node count + weld schema version + format-version envelope; on freshness mismatch or corruption the sidecar is silently rebuilt.
- `wd demo polyrepo --init` auto-bootstraps the workspace before discovery so the first run produces a populated graph instead of an empty one.
- Bootstrap traceback surfaced under `WELD_DEBUG=1` in `wd demo polyrepo` so the demo's bootstrap exception handler shows the underlying cause when set.

### Changed

- Edge-type weighted impact and plan-change ranking: `_score_asset()` and `_secondary_assets` consult an edge-weight table (semantic=5.0, related=2.0, incidental=0.5) and a `SECONDARY_THRESHOLD=1.0`. Canonical-authority assets bypass the secondary threshold so authoritative nodes always render even when only attached via low-weight edges.
- `wd init` framework detection merged into a single classifier pass (`_init_classify.py`); per-file `detect_*` walks coalesce, dropping a representative `wd init` cold run from 41.2 s to 8.6 s on a 100k synthetic tree (about 79% faster). No behavior change vs. multi-pass detection — same constants, same heuristics.
- `wd discover` now warns on stderr when the prior `graph.json` is unreadable instead of silently rewriting it. The previous graph is preserved untouched if the load fails; operators see the failure and can decide whether to rerun.
- `agent_graph_render_pairs` only honors `render_paths` from `authority="canonical"` nodes. Non-canonical nodes can no longer suppress duplicate-name audit findings via render-paths.

### Fixed

- Go gin framework detection now matches the canonical `github.com/gin-gonic/gin` import path; the quoted-path matcher pre-filters block comments and raw-string literals so commented-out imports and string-fixture content no longer trigger false positives.
- `unused_skill` audit suppression tightened to a word-boundary regex match and respects skill name mentions in agent body / instruction text, eliminating substring false positives.
- Bench `test_discover_stability` no longer flakes against tiny-time clocks (sub-millisecond mtime resolution).

## v0.8.3 - 2026-04-25

### Fixed

- CHANGELOG entry for v0.8.2 listed `11 graph-backed tools` and named the
  nonexistent `weld_callees`. The MCP registry has had 13 tools since v0.8.2
  (matching `docs/mcp.md` and the in-process registry); the entry is
  corrected here so changelog readers and PyPI long-description match the
  shipped surface.

## v0.8.2 - 2026-04-25

### Added

- `wd security` (and `wd doctor --security` mode) shows trust posture as a
  scannable view: project-local strategies under `.weld/strategies`,
  `external_json` adapters in `.weld/discover.yaml`, enrichment provider
  network use, MCP importability, and safe-mode availability. Risk level
  rolls up to `low`/`medium`/`high` with recommendations; `--json` output
  is available for tooling.
- `wd agents render` (preview) writes Agent Graph artifacts with a safe
  contract: dry-run/diff by default, `--write` required to write,
  `--force` required to clobber, provenance headers on rendered files,
  and a drift audit.
- `wd demo` command family wraps the new bootstrap scripts: `wd demo
  list`, `wd demo monorepo --init <dir>`, and `wd demo polyrepo --init
  <dir>` for a frictionless first-run experience.
- `scripts/create-monorepo-demo.sh` and `scripts/create-polyrepo-demo.sh`
  build deterministic demo workspaces in a tempdir without manual nested
  `git init`. Fail gracefully when Git identity is missing.
- MCP server: 14 graph-backed tools (`weld_query`, `weld_find`,
  `weld_context`, `weld_path`, `weld_brief`, `weld_stale`, `weld_callers`,
  `weld_references`, `weld_export`, `weld_trace`, `weld_impact`,
  `weld_enrich`, `weld_diff`, `weld_review`) return an actionable error
  payload when neither `.weld/graph.json` nor `.weld/workspaces.yaml` is
  present.
- Installed-wheel MCP smoke test (`weld_mcp_install_smoke_test`) builds
  the wheel, installs it, and asserts `python -m weld.mcp_server --help`
  works from the installed copy. Catches packaging regressions like the
  v0.8.0 missing-`weld.cross_repo` failure.
- Public Agent Graph guide at `docs/agent-graph.md`: what the Agent Graph
  is and is not, supported asset types and platform formats, node and
  edge types, an example graph, the `wd agents` commands, authority and
  drift, the read-only-first policy, render/export status, and known
  limitations. README links to it from key features, the Agent Graph
  quickstart, and the Documentation section.
- Platform fixtures for AGENTS.md, SKILL.md, and `.mcp.json` formats
  under `weld/tests/fixtures/agent_graph/` give deterministic Agent
  Graph coverage aligned with the platform-support claims.
- `docs/runtime-validation.md` records real-client validation entries
  (client version, date, tester, OS, scenario, result, notes) and is
  linked from `docs/platform-support.md`.
- `docs/visualization-examples.md` shows monorepo, polyrepo, Agent Graph,
  and MCP query terminal output captured from real demo workspaces.
- `docs/performance.md` reports measured `wd discover`, `wd query`, and
  `wd workspace status` timings at 1k / 10k / 100k file scales for
  single-repo and polyrepo workspaces, plus a reproducible synthetic
  generator at `weld/bench/synthetic_large_repo.py`.
- `docs/mcp-registry-submission.md` and
  `docs/mcp-registry-payload.yaml` draft the upstream MCP Registry
  submission. The submission is held until launch; this release ships
  the local draft only.

### Changed

- README, `CONTRIBUTING.md`, `docs/community.md`, `docs/launch.md`, and
  the changelog no longer reference GitHub Discussions. Open-ended
  feedback is routed to GitHub Issues; Discussions is deferred until
  there is a concrete reason to enable it.

## v0.8.0 - 2026-04-25

### Added

- Agent-graph subsystem: a static, persisted graph of agents alongside the
  code graph. Schema vocabulary, persisted storage, static discovery, and
  metadata/reference parsing are now part of `wd discover`. New CLI surface:
  `wd agents discover|list|explain|impact|audit|plan-change` for inspecting
  the agent graph and reasoning about change impact, with authority-drift
  detection and a maintainer skill. Demo fixtures included.
- `wd discover --safe` refuses to run project-local strategy or extractor
  code when set, so an untrusted repository can be scanned without
  executing unreviewed Python from `.weld/strategies/`. `wd discover`
  without `--safe` now prints a one-time warning before running
  project-local code.
- `wd enrich --safe` refuses providers that would touch the network or an
  LLM, so enrichment can run in offline / sandboxed contexts without
  surprises.
- `wd mcp config --client={claude,vscode,cursor}` writes or merges the
  MCP-server entry for the chosen client. Malformed existing JSON in
  `--merge` mode now exits non-zero instead of silently overwriting.
- `wd stats` now surfaces top authority nodes, staleness, and a
  per-workspace breakdown by default. `--top N` controls the authority
  list size.
- `wd validate` emits actionable error diagnostics with suggested
  remediations, and gates federation bypasses on
  `schema_version: 2` so older graphs cannot accidentally use new
  cross-repo features.
- Federation: cross-repo resolvers declared in
  `cross_repo_strategies` are now executed during `wd discover` at a
  polyrepo workspace root, producing cross-repo edges in the federated
  graph.
- TypeScript discovery now pins `tree-sitter-typescript` and dispatches
  TSX files to the TSX grammar so React component exports are
  discovered correctly.
- `wd doctor` adds PM first-run UX sections covering install, init,
  discover, and graph health, and now documents and verifies its
  exit-code contract.
- Read-side commands (`wd query`, `wd context`, `wd trace`, `wd impact`,
  `wd diff`, `wd enrich`) print friendly guidance pointing to
  `wd discover` when the graph is missing, instead of stack traces.
- Examples: `examples/04-monorepo` ships a runnable PM demo with
  services, shared libs, Docker, CI, and docs.
  `examples/05-polyrepo` makes its `api` and `auth` services runnable
  via `uvicorn` and adds three children plus a cross-repo edge for
  federation demos.
- GitHub: issue templates and contact routing for incoming community
  reports; `docs/community.md` documents how feedback is organized.

### Changed

- `wd-retry-hint` formatting is centralized so retry guidance is
  consistent across CLI commands.

### Fixed

- `wd discover` honors brace globs (e.g. `**/*.{ts,tsx}`) in the
  `typescript_exports` strategy.
- `wd doctor` no longer fails when run inside an empty directory.

## v0.7.0 - 2026-04-23

### Fixed

- `wd discover` no longer overwrites `.weld/graph-previous.json` before
  parsing the current graph. A corrupt `graph.json` now leaves the last
  good recovery snapshot intact so `wd diff` and manual recovery keep
  working.

### Changed

- `exclude:` patterns in `.weld/discover.yaml` now match against the
  full repo-relative path, not just the filename. Segmented patterns
  like `.cache/**`, `compiler/**`, and `**/*.gen.py` work as expected;
  bare filename patterns (`README.md`, `*.pyc`) continue to match via
  a basename fallback. Source-level `exclude` is applied uniformly in
  `resolve_source_files`, so strategies no longer need to opt in for
  excludes to take effect.

## v0.6.0 - 2026-04-22

### Added

- Added atomic `wd discover --output PATH` writes so discovery can preserve
  incremental context and avoid truncating the existing graph before it is
  read.
- Added `wd bootstrap --diff` and `wd bootstrap --force` so existing agent
  bootstrap files can be compared with, or upgraded to, the bundled templates.
- Added `wd prime --agent {auto,claude,codex,copilot,all}` to surface missing
  bootstrap files for the active agent framework.

### Changed

- `wd prime` now bases graph freshness guidance on source-file staleness
  instead of SHA drift alone, preserving enriched graphs when tracked sources
  have not changed.
- Agent docs, examples, templates, and warnings now recommend
  `wd discover --output .weld/graph.json` as the primary graph refresh path.

### Fixed

- `wd` output piped into commands that close early, such as `head`, now exits
  quietly instead of printing a `BrokenPipeError` traceback.
- Graph-only commits after `wd touch` no longer cause repeated stale-graph
  prompts when source files are unchanged.

## v0.5.1 - 2026-04-21

### Added

- Added `wd find --limit N`; file-search results now include an integer
  `score` so callers can rank broad token matches.
- Added fallback behavior for `wd context <id>`: when an exact node id is not
  found, Weld searches for likely matches instead of returning an empty result.
- Expanded the edge vocabulary with governance and provenance edge types:
  `owned_by`, `gates`, `gated_by`, `supersedes`, `validates`, `generates`,
  `migrates`, and `contracts`.
- Added `wd touch` and source-file freshness metadata so graph snapshots can
  record the current git revision without changing nodes or edges.
- Added `wd workspace bootstrap`, a one-shot polyrepo setup flow that initializes
  the root, scans nested repositories, initializes children, runs recursive
  discovery, and rebuilds the root meta-graph.

### Changed

- Tool-generated edges should now record origin through `props.source`, with
  `confidence` using the existing `definite`, `inferred`, or `speculative`
  vocabulary.
- Workspace discovery now honors root `.gitignore` entries when scanning for
  nested repositories.
- `wd prime` avoids suggesting workspace bootstrap when only the shared MCP
  surface is missing.

### Fixed

- Bootstrap progress logs now go to stderr so JSON output remains parseable.
- Workspace bootstrap refreshes `workspaces.yaml` when the filesystem scan
  diverges from persisted child state.
- Recursive bootstrap failures are mirrored into `BootstrapResult.errors`.
- The bundled Weld README template no longer contains the placeholder project
  URL.
