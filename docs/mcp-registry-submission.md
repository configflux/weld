# MCP Registry Submission (Draft)

This document is the local draft of Weld's submission to the public Model
Context Protocol server registry. **It is held until launch.** Do not open
a pull request to the upstream registry, and do not call any registry API
from this draft -- the artifacts here are inputs for the maintainer who
opens the submission by hand once Weld's launch checklist is green.

The structured payload that this draft will be transformed into lives at
[`docs/mcp-registry-payload.yaml`](mcp-registry-payload.yaml). Treat the
YAML file as the source of truth for field values; treat this document as
the prose explaining what the submission contains and why.

## Purpose

The MCP registry is a discoverability surface: it lets agents and humans
browse known MCP servers, see how to install them, and confirm their
capabilities before configuring a client. A registry entry is a small,
public metadata blob -- not code -- so it has the same care budget as a
PyPI description: short, accurate, and stable enough that we can point
people at it without revising weekly.

This submission lists the **stdio MCP server bundled with the
`configflux-weld` Python package** -- launched as a Python module.
Configuration generation (`wd mcp config`) ships in the default install;
the stdio server itself requires the optional `mcp` extra documented
below.

## Install command

The single canonical install path the submission advertises is:

```bash
pip install configflux-weld
python -m weld.mcp_server
```

Notes for the submission reviewer:

- `pip install configflux-weld` installs the package and the `wd` CLI but
  does **not** pull the optional MCP SDK. To run the stdio server the
  user also needs the `mcp` extra (the runtime dependency for stdio
  transport). The recommended one-liner that matches the install hint
  printed by the server is:

  ```bash
  pip install 'configflux-weld[mcp]'
  python -m weld.mcp_server
  ```

  Both commands are correct. The plain `pip install configflux-weld`
  form is what the registry asks for as the minimum install command;
  the `[mcp]` form is what users typically run, and it is what the
  install hint surfaces when the SDK is missing.

- `python -m weld.mcp_server` is invoked over stdio by an MCP client.
  It does not bind a network socket and it does not execute any
  application code from the analyzed repository. See the
  [`docs/mcp.md`](mcp.md) "Trust model" section.

- `uv tool install "configflux-weld[mcp]"` is supported as well and is
  the preferred installation route in the project's own README. The
  registry submission still standardises on the `pip install` form for
  parity with other Python-package entries.

The install contract -- "install the package, then run
`python -m weld.mcp_server`" -- is exercised end-to-end by the smoke
test linked under [Smoke-test evidence](#smoke-test-evidence). If that
test breaks, this submission is not safe to send.

## Server identity and capabilities

| Field | Value |
|---|---|
| Server name (registry slug) | `weld` |
| Display name | Weld |
| Description (one line) | Local codebase and agent graph for AI coding workflows, exposed as MCP tools. |
| Runtime | Python 3.10+ (stdio MCP transport) |
| License | Apache-2.0 |
| Repository | https://github.com/configflux/weld |
| Homepage / docs | https://github.com/configflux/weld#readme |
| PyPI project | https://pypi.org/project/configflux-weld/ |
| Tags | code-graph, codebase, structure, discovery, agent, ide, polyrepo |

The full set of exposed MCP tools is the same surface listed in
[`docs/mcp.md`](mcp.md) -- 13 tools, names pinned for tests. The payload
file enumerates them; this document defers to it so tool changes need
only one edit.

## Smoke-test evidence

The install/launch contract advertised above is held to by an automated
test in the package's own test suite:

- Path: `weld/tests/weld_mcp_install_smoke_test.py`
- What it pins:
  1. The MCP server's missing-graph contract is actionable -- every
     graph-backed tool returns a structured `error_code: graph_missing`
     payload with `error`, `hint`, and `retry` strings.
  2. The stale-graph response shape from `weld_stale` is a documented
     wire contract (`stale`, `source_stale`, `sha_behind`, `graph_sha`,
     `current_sha`, `commits_behind`).
  3. **The MCP server boots from an installed package, not from the
     source tree.** The wheel is built, installed into an isolated
     `--prefix`, and `python -m weld.mcp_server --help` is run from
     that prefix. The test asserts that the `--help` output prints the
     `Usage: python -m weld.mcp_server` banner *and* the
     `configflux-weld[mcp]` install hint -- exactly what a registry
     visitor following the install command will see.

Point the registry maintainer at this file when reviewing the entry. A
broken install path or a drifted help banner causes this test to fail
before any submission is opened.

## Held-until-launch policy

The submission is intentionally not in flight. Hold reasons:

- Weld is finishing public-readiness work (PyPI README alignment,
  installed-wheel smoke coverage, platform support claims, feedback
  channel posture). The registry entry should not advertise an install
  path that is still being hardened.
- The registry submission should reference a stable PyPI version.
  `python -m weld.mcp_server` is exposed by every `configflux-weld`
  release that ships the `mcp` extra, but the registry visitor will
  install whatever PyPI currently serves. Submit only after the next
  patch release is out and verified.
- The MCP tool surface listed in the registry should match what the
  bundled server registers. The MCP doc explicitly notes the surface
  is "still settling" -- so we hold the registry submission until a
  version we are willing to keep stable.

When the maintainer opens the submission, they will:

1. Open the upstream MCP registry pull request from a personal account
   (the registry expects human submissions, not bot accounts).
2. Translate the YAML payload into whatever schema/format the registry
   asks for at submission time. As of this draft the upstream schema
   is still evolving -- the YAML is intentionally close to the common
   field shape but is not pinned to a specific schema version.
3. Link this document and the smoke-test path in the PR description as
   evidence.
4. Update this draft if the registry maintainers ask for changes
   during review, so the local draft stays a faithful record of what
   was sent.

## Fields the maintainer fills at submission time

The payload is mostly stable, but a few fields are deliberately left to
the maintainer because their value depends on what is current on the
day the submission is opened:

- **`version`** in the payload -- set to the latest published
  `configflux-weld` PyPI version on submission day. The draft shows
  the current development version; do not freeze it here.
- **`maintainers`** -- contact handles for the registry's review
  thread. Kept out of the draft to avoid promoting individual
  maintainers in reusable submission material.
- **`screenshot_urls` / `demo_url`** -- if the registry asks for
  screenshots or a hosted demo, these should be stable public URLs
  selected at submission time, not historical dev links. Leave blank
  in the draft.
- **Submission account** -- the upstream PR author. Left out of the
  draft entirely.

Everything else (name, description, install command, runtime, repo,
license, tags, capabilities, evidence) is fixed by the project and
should not need editing at submission time.

## See also

- [`docs/mcp.md`](mcp.md) -- runtime reference for the MCP server,
  tool list, client configuration, trust model, and troubleshooting.
- [`docs/launch.md`](launch.md) -- launch-copy source of truth (one-line
  pitch, longer pitch, demo commands, comparisons, known limitations).
- [`docs/release.md`](release.md) -- release runbook the maintainer
  follows before flipping this submission from "held" to "in flight".
- [`docs/mcp-registry-payload.yaml`](mcp-registry-payload.yaml) -- the
  structured payload this prose describes.
- `weld/tests/weld_mcp_install_smoke_test.py` -- the install/launch
  contract test referenced under
  [Smoke-test evidence](#smoke-test-evidence).
