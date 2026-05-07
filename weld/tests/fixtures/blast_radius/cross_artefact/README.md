# Fixture: cross_artefact

The combined scenario. One source file participates in every
artefact type weld supports today, and the impact engine must
cross all of them in a single reverse-BFS.

```
cross_artefact/
  MODULE.bazel.in        # renamed at copy time
  Dockerfile             # COPY ./app -> bundle (directory COPY)
  docker-compose.yml     # service api: build .
  app/
    BUILD.bazel.in       # renamed at copy time
    lib.py               # greet()
    main.py              # imports + uses greet
    lib_test.py          # py_test for lib
```

The Dockerfile uses a directory COPY (`COPY ./app /service/app`),
the natural shape for bundling a Python package. Layer C2 emits a
`dockerfile --contains--> file:app` edge plus, via the
directory-walk bridge, `file:app --contains--> file:app/lib.py`
(and siblings) so reverse-BFS from `app/lib.py` reaches the
dockerfile through the `file:app` directory node. Without the
walk bridge, the only contains-edge would land on the directory
node and the chain would disconnect at the file boundary.

## Cross-framework chain pinned

A change to `app/lib.py` must reverse-traverse to:

1. `app/main.py` (Python `from app.lib import greet`)
2. `file:app/lib_test` (`test_peer` filename probe)
3. `build-target:app/lib`, `build-target:app/main`,
   `test-target:app/lib_test` (Layer C1 srcs/deps edges)
4. `file:app` (Layer C2 directory-walk bridge: the Dockerfile
   `COPY ./app` emits both `dockerfile --contains--> file:app`
   and `file:app --contains--> file:app/lib.py`, so the reverse
   BFS hops file:app/lib.py -> file:app -> dockerfile)
5. `dockerfile:Dockerfile` (Layer C2)
6. `service:default:api` (Layer C2: compose service builds
   from the dockerfile)

This is the user's "deterministic blast radius across every
supported framework" headline. If any of these layers regress
this fixture's golden mismatches.

## Documented seeds

- `impact_lib` (`target.input = app/lib.py`, depth=4): the
  cross-framework headline scenario. Depth 4 is required to
  cross from `lib.py` to the compose service through the
  `file:app -> dockerfile -> service` chain plus the symbol-level
  `calls` edges from `python_callgraph`.
- `impact_dockerfile` (`target.input = dockerfile:Dockerfile`,
  depth=2): editing the Dockerfile reaches every compose service
  that builds from it. Mirror of the `dockerfile_compose`
  fixture's `impact_dockerfile` seed for redundant determinism.
- `impact_compose` (`target.input = docker-compose.yml`, depth=1):
  editing the compose file reaches the `compose:default` config
  node only -- a one-hop sanity check that the compose strategy
  emits a config node and not just edges.

## Layer C3 caveat

The `test_peer` filename probe is Python-only today. Layer C3 will
generalise it; goldens may need regen at that time. The text
above will be updated alongside C3 to describe the multi-language
behaviour explicitly.
