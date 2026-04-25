# Community

Weld is maintainer-led today. This document describes how community
feedback is organized and where to send different kinds of input.

## Feedback channel: GitHub Issues

GitHub Issues is the single feedback channel for the public weld
repository at `configflux/weld`. Both concrete bug reports / feature
requests **and** open-ended feedback (architecture ideas, setup
show-and-tell, MCP-client integration questions, strategy requests,
polyrepo patterns) belong in Issues today. Maintainers triage open-ended
threads the same way they triage scoped requests.

Use the closest issue template:

- **Bug report** -- a defect in `wd`, discovery, the MCP server, or the
  installer.
- **Feature request** -- a proposed enhancement, including open-ended
  ideas about CLI verbs, defaults, or workflows.
- **Strategy request** -- new language or ecosystem AST coverage.
- **Demo feedback** -- friction or results from running weld on a real
  repo, including show-and-tell.
- **Security question (low severity)** -- posture, hardening, or
  safe-usage questions. Vulnerability reports go to a private GitHub
  Security Advisory instead -- see [`SECURITY.md`](../SECURITY.md).

If none of the templates fit, file under **Feature request** and
explain the context; maintainers will relabel during triage.

## Vulnerability reports

Do **not** file vulnerabilities as public issues. Use a private GitHub
Security Advisory at
`https://github.com/configflux/weld/security/advisories/new`. See
[`SECURITY.md`](../SECURITY.md) for the disclosure policy and response
expectations.
