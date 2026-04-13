# Example: Monorepo TypeScript Discovery

Demonstrates cortex discovering a TypeScript monorepo with workspace
packages. The built-in `typescript_exports` strategy extracts exported
symbols from each package, and the `manifest` strategy discovers
build/test scripts from `package.json` files. Cross-package dependency
edges are captured via import analysis.

## What This Example Contains

- `packages/ui/` -- a shared React component library (`@acme/ui`)
  exporting `Button` and `Card` components
- `packages/api/` -- a typed API client package (`@acme/api`) exporting
  `fetchItems`, `createItem`, and shared types
- `apps/web/` -- a Next.js application (`@acme/web`) that imports from
  both `@acme/ui` and `@acme/api`
- `package.json` -- root workspace manifest defining the monorepo
  structure
- `.cortex/discover.yaml` -- cortex discovery configuration with
  per-package source entries and manifest extraction

## Monorepo Structure

```
04-monorepo-typescript/
  package.json              (root workspace)
  packages/
    ui/
      package.json          (@acme/ui)
      src/
        Button.tsx          exports Button, ButtonProps
        Card.tsx            exports Card, CardProps
        index.ts            barrel re-exports
    api/
      package.json          (@acme/api)
      src/
        client.ts           exports fetchItems, createItem
        types.ts            exports ApiResponse, ApiError
  apps/
    web/
      package.json          (@acme/web, depends on @acme/ui + @acme/api)
      src/
        App.tsx             imports Button, Card, fetchItems
        layout.tsx          imports Card
```

## Running Discovery

```bash
cd examples/04-monorepo-typescript
cortex discover
```

## What the Graph Contains

After running discovery, the output JSON graph includes:

- **File nodes** for each TypeScript source file, scoped by package
  using `id_prefix` (e.g., `file:ui/Button`, `file:api/client`,
  `file:web/App`)
- **Exported symbols** listed in each node's `exports` property
  (functions, classes, interfaces, types)
- **Import tracking** via the `imports_from` property, showing which
  modules each file depends on (e.g., `App.tsx` imports from
  `@acme/ui` and `@acme/api`)
- **Package containment edges** linking package nodes (`pkg:ui`,
  `pkg:api`, `pkg:web`) to their constituent files
- **Config nodes** for each `package.json`, with build/test script
  targets extracted by the `manifest` strategy

Example output (abbreviated):

```json
{
  "nodes": {
    "file:ui/Button": {
      "type": "file",
      "label": "Button",
      "props": {
        "file": "packages/ui/src/Button.tsx",
        "exports": ["Button", "ButtonProps"],
        "source_strategy": "typescript_exports"
      }
    },
    "file:api/client": {
      "type": "file",
      "label": "client",
      "props": {
        "file": "packages/api/src/client.ts",
        "exports": ["fetchItems", "createItem"],
        "source_strategy": "typescript_exports"
      }
    },
    "file:web/App": {
      "type": "file",
      "label": "App",
      "props": {
        "file": "apps/web/src/App.tsx",
        "exports": ["App"],
        "imports_from": ["@acme/ui", "@acme/api"],
        "source_strategy": "typescript_exports"
      }
    },
    "config:package_json": {
      "type": "config",
      "label": "package.json (@acme/monorepo)",
      "props": {
        "file": "package.json",
        "source_strategy": "manifest"
      }
    }
  },
  "edges": [
    {"from": "pkg:ui", "to": "file:ui/Button", "type": "contains"},
    {"from": "pkg:api", "to": "file:api/client", "type": "contains"},
    {"from": "pkg:web", "to": "file:web/App", "type": "contains"}
  ]
}
```

## Key Discovery Configuration Patterns

### Per-Package Scoping

Each workspace package gets its own source entry with `id_prefix` and
`package` fields. This scopes node IDs by package and creates
containment edges:

```yaml
- glob: "packages/ui/src/**/*.{ts,tsx}"
  strategy: typescript_exports
  id_prefix: ui
  package: "pkg:ui"
```

Without `id_prefix`, files with the same name across packages (e.g.,
`index.ts`) would produce colliding node IDs. The prefix ensures
uniqueness: `file:ui/index` vs `file:api/index`.

### Cross-Package Dependencies

When tree-sitter is available, the `typescript_exports` strategy
captures import sources in each node's `imports_from` property. This
reveals cross-package dependency edges:

- `apps/web/src/App.tsx` imports from `@acme/ui` and `@acme/api`
- `apps/web/src/layout.tsx` imports from `@acme/ui`

These edges make the package dependency graph explicit in the
knowledge graph without requiring a separate analysis pass.

### Manifest Extraction

The `manifest` strategy scans `package.json` files and extracts
build/test script targets as typed nodes:

```yaml
- glob: "**/package.json"
  strategy: manifest
```

This produces `build-target` and `test-target` nodes for scripts like
`npm run build`, `npm run test`, and `npm run lint`.

## Customizing

- Add more workspace packages by creating new directories under
  `packages/` or `apps/` and adding corresponding source entries to
  `.cortex/discover.yaml`
- Use `exclude` in source entries to skip generated files:
  `exclude: ["**/dist/**", "**/*.d.ts"]`
- Combine with the `config_file` strategy to discover
  `tsconfig.json` files as configuration nodes
