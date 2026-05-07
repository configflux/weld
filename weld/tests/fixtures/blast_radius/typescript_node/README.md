# Fixture: typescript_node

Tiny TypeScript / npm repo. Three TS files plus a `package.json`:

```
typescript_node/
  package.json   # scripts: test (jest), build (tsc)
  src/
    lib.ts       # exports add, double
    main.ts      # imports + uses double
    lib.test.ts  # Jest spec for lib
```

## Strategies wired

- `typescript_exports` -- AST (tree-sitter) or regex extraction of
  exported symbols and `import` targets. Both paths are
  deterministic; the golden encodes whichever the test environment
  uses.
- `manifest` -- extracts the `scripts` table from `package.json`
  so the npm `build` and `test` scripts surface as graph nodes.

## Documented seed

- `impact_lib` (`target.input = src/lib.ts`, depth=3): a change to
  `lib.ts` reverse-traverses to `main.ts` (which imports it).

## Layer C3 caveat

Layer C3 (multi-language test peer) is a separate task and is
NOT yet shipped. Until it lands, `src/lib.test.ts` does not emit a
`tests` edge to `src/lib.ts` -- it is just another TS module with
its own exports/imports. After C3 lands, regenerate the goldens to
pick up the new `tests` edge: that drift is expected and is the
mechanical path through `bazel run :regenerate_blast_radius_goldens`.
