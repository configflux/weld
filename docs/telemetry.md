<!-- markdownlint-disable MD013 -->
# Local telemetry

Weld records one JSON line per `wd` invocation and per MCP tool call to a
local file. Nothing is sent over the network. There is no remote endpoint,
no opt-in upload, no background sender. Sharing happens only when **you**
choose to copy the file into a bug report (`wd telemetry export`).

This page describes the recording format, where the file lives, what is
guaranteed never to be recorded, how to opt out, and the `wd telemetry`
subcommand surface.

## Why it is on by default

When something misbehaves in `wd` or in the weld MCP server, a
per-invocation record is what makes a bug actually reproducible. The
default is **on** so the file is already populated when a customer hits
something worth reporting; the file is local-only and the schema is a
strict allowlist (below) so the on-disk artifact is safe to share.

## Where the file lives

Resolution order, top wins:

1. **Polyrepo workspace root.** Walk up from the current directory looking
   for `.weld/workspaces.yaml`. If found, write to
   `<workspace_root>/.weld/telemetry.jsonl`. A `wd query` run inside a
   child repo and a `wd discover` run at the workspace root therefore both
   append to the same file — one shareable artifact per workspace.
2. **Single-repo root.** Walk up looking for any `.weld/` directory that
   contains `discover.yaml` or `graph.json`. Write to
   `<repo>/.weld/telemetry.jsonl`.
3. **No project context** (e.g. `wd --version` from `/tmp`). Fall back to
   `${XDG_STATE_HOME:-~/.local/state}/weld/telemetry.jsonl`.

`.weld/telemetry.jsonl` is gitignored, so the file cannot be committed by
accident.

`wd telemetry path` prints the resolved file path for the current
directory.

## Event schema (v1)

JSONL, one event per line. Example:

```json
{
  "schema_version": 1,
  "ts": "2026-04-28T14:03:11Z",
  "weld_version": "0.12.1",
  "surface": "cli",
  "command": "discover",
  "outcome": "ok",
  "exit_code": 0,
  "duration_ms": 482,
  "error_kind": null,
  "python_version": "3.12.3",
  "platform": "linux",
  "flags": ["--output", "--scope"]
}
```

Field-by-field:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | Literal `1`. Bumped on any breaking schema change. |
| `ts` | string | UTC ISO-8601, second precision. |
| `weld_version` | string | The installed `wd` version. |
| `surface` | enum | `"cli"` or `"mcp"`. |
| `command` | string | Subcommand (CLI) or MCP tool name. Validated against an allowlist; unknown values coerce to `"unknown"`. |
| `outcome` | enum | `"ok"`, `"error"`, or `"interrupted"`. |
| `exit_code` | int | Process exit code. Sentinel `-1` for MCP (no exit-code concept). |
| `duration_ms` | int | Derived from `time.monotonic_ns()`. |
| `error_kind` | string \| null | Exception class name only (`type(exc).__name__`). Capped at 64 chars and matched against `^[A-Za-z_][A-Za-z0-9_]{0,63}$`. **Never** `str(exc)`. |
| `python_version` | string | `f"{major}.{minor}.{micro}"` only. |
| `platform` | string | `sys.platform` (e.g. `"linux"`). Not `platform.platform()` — that string can embed hostname-flavored data. |
| `flags` | list[string] | Sorted, deduplicated list of long/short flag *names* the user passed. Filtered through an allowlist. |

## What is never recorded

The following are asserted by tests with regex matchers — if upstream
code forgets to sanitize, a defensive validator drops the event:

- File paths.
- Query / search terms.
- Node IDs, symbol names, positional arguments, flag values.
- Exception messages.
- Hostnames, usernames, current working directory, environment-variable
  values.
- Anything matching email regex.
- Anything path-like.
- Any string longer than 96 characters.
- Any digit run of length ≥ 5 in a non-numeric field.

## Opting out

Resolution order, top wins:

1. **`--no-telemetry` CLI flag** for a single invocation. Stripped from
   `argv` before dispatch, so subcommand parsers never see it.
2. **`WELD_TELEMETRY` environment variable.**
   - `off`, `0`, `false`, `no`, `disabled` → telemetry off.
   - `on`, `1`, `true`, `yes`, `enabled` → telemetry on.
3. **`.weld/telemetry.disabled` sentinel file** at the resolved root.
   Created by `wd telemetry disable`, removed by `wd telemetry enable`.
4. **Default**: on.

`wd telemetry status` prints which step decided the outcome, so a user
puzzled by "why is this still on?" can see whether the env var, the
sentinel file, or the default settled it.

A first-run notice prints once per resolved path, on stderr, when the
file does not yet exist: a single line announcing that local telemetry
is on, naming the file, and listing the three opt-out mechanisms.
Subsequent invocations stay silent. After `wd telemetry clear` the next
event re-prints the notice — a deliberate UX choice so wiping the file
always produces a fresh confirmation.

## The `wd telemetry` subcommand

```text
wd telemetry status               enabled/disabled, decision source, path,
                                  event count, file size
wd telemetry show [--last=N] [--json]
                                  pretty-print the last 20 events
wd telemetry path                 print the resolved file path
wd telemetry export --output=FILE
                                  copy the file to a destination (refuses
                                  to write into any .weld/)
wd telemetry clear [--yes]        delete the file (prompts unless --yes)
wd telemetry disable              create .weld/telemetry.disabled
wd telemetry enable               remove .weld/telemetry.disabled
```

`export` deliberately refuses to write into any `.weld/` directory: the
exported copy is meant to leave the project, and conflating it with the
live file is a footgun.

## Failure isolation

The recorder is wrapped in `try/except BaseException: pass` at every
entry point — both the context manager around the CLI dispatcher and
the per-tool wrapper around the MCP server's dispatch. **A bug in
telemetry must never crash a `wd` command, change an exit code, or
alter output.** A test monkey-patches the writer to raise and asserts
the inner command's return value is preserved. This is non-negotiable:
telemetry is a debugging aid, never a failure mode.

## Rotation policy

The file rotates when its size reaches **1 MiB**. The writer reads the
file, keeps the trailing **500** events (~125 KiB at ~250 bytes per
line), and atomically rewrites it. Steady-state appends use raw
`os.write` under `fcntl.flock`; only the rotation rewrite goes through
atomic-write. This bounds disk usage in long-lived MCP sessions and
keeps the file small enough to paste the whole thing into a GitHub
issue.

## Sharing the file

When you file a bug:

```bash
wd telemetry export --output=/tmp/weld-bug.jsonl
```

`export` copies the live file to your chosen destination. The copy is
the JSONL described above — the same allowlist, the same redaction
guarantees. Inspect it before you attach it; nothing leaves your
machine without your action.
