# Security Policy

## Reporting a Vulnerability

Report security vulnerabilities by opening a
[GitHub Security Advisory](https://github.com/configflux/weld/security/advisories/new)
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

configflux-weld is a static analysis and discovery tool. Bundled discovery
reads source files and produces a connected structure. It does not execute
discovered application code or open network connections as part of bundled
discovery.

Key areas of concern:

- **Strategy plugins**: Project-local strategies (`.weld/strategies/`) are
  Python modules loaded at discovery time. Only run `wd discover` on
  repositories you trust.
- **External adapters**: `strategy: external_json` executes the configured
  command from `discover.yaml` with the repository root as its working
  directory. Only enable external adapters from repositories you trust.
