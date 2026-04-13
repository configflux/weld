# Security Policy

## Reporting a Vulnerability

Report security vulnerabilities by opening a
[GitHub Security Advisory](https://github.com/configflux/cortex/security/advisories/new)
on this repository. Do not open a public GitHub issue for vulnerabilities.

Please include:

- Description of the vulnerability and its potential impact
- Steps to reproduce
- Any suggested mitigations

You will receive a response within 72 hours. We ask that you allow us
reasonable time to investigate and patch before public disclosure.

## Supported Versions

Only the latest release is actively supported with security fixes.

## Security Considerations

configflux-cortex is a static analysis and discovery tool. It reads source files
and produces a knowledge graph. It does not execute discovered code, open
network connections, or process untrusted input beyond the files in the
repository it is pointed at.

Key areas of concern:

- **Strategy plugins**: Project-local strategies (`.cortex/strategies/`) are
  Python modules loaded at discovery time. Only run `cortex discover` on
  repositories you trust.
- **External adapters**: The external JSON adapter reads files from paths
  specified in `discover.yaml`. Ensure these paths are within the project
  directory.
