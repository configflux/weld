# Community

Weld is maintainer-led today. This document describes how community
feedback channels are organized and what maintainers need to do to enable
them. Community operations (Discussions, templates, triage rotation) are a
**post-launch** concern; this page captures the plan so the first
maintainer to pick it up has a runbook.

## GitHub Discussions

GitHub Discussions is the preferred channel for open-ended feedback that
does not yet belong in a tracked issue: architecture ideas, setup
show-and-tell, and questions about how to use weld with specific tools.

### Intended categories

When Discussions is enabled on the `configflux/weld` repository, the
following categories should exist. Each category's purpose is listed so
new maintainers can answer "where does this post belong?" consistently.

| Category | Format | Purpose |
|---|---|---|
| **Ideas** | Open-ended | Proposals for new features, CLI verbs, or changes to defaults. Graduates to a `bd` issue once scoped. |
| **Show your repo setup** | Open-ended | Users posting screenshots or snippets of their `.weld/discover.yaml`, custom strategies, or graph-query workflows. |
| **MCP clients** | Q&A | Questions about integrating weld's MCP server with Claude Code, Codex, Copilot, or other MCP-capable clients. |
| **Strategy requests** | Open-ended | Requests for new AST strategies (new languages, frameworks, or file types). Maintainers triage into `bd` issues when actionable. |
| **Polyrepo patterns** | Open-ended | Discussion of multi-repo workspaces, federation config, and cross-repo resolver recipes. |

Bug reports and feature requests with a concrete scope belong in GitHub
Issues, not Discussions -- see `CONTRIBUTING.md`.

### Enablement runbook (maintainer)

GitHub Discussions categories cannot be created via the REST API with
full fidelity in all accounts, so this is a one-time UI task for a repo
admin:

1. Navigate to `https://github.com/configflux/weld/settings` and scroll to
   the **Features** section. Tick **Discussions** if it is not already on.
2. Open the new **Discussions** tab on the repo page, then click the
   pencil icon next to **Categories** in the right sidebar.
3. For each category in the table above, either rename an existing
   default or click **New category**. Use the name and format from the
   table. Paste the **Purpose** text into the category description so
   posters see it when choosing a category.
4. Delete or archive any default categories that do not map to one of the
   five above (e.g. "Polls", "General") unless a clear need emerges.
5. Pin a short welcome post that links to `CONTRIBUTING.md` and this file.

### Status

As of the current launch window, Discussions is **not yet enabled**. This
document is the source of truth for the intended configuration; the
enablement click-through is tracked as a post-launch maintainer task.
