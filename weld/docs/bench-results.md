# Weld retrieval benchmark

Token cost comparison across retrieval modes for the prompt fixture in `weld/tests/bench/prompts.yaml`. See `weld/bench/runner.py` for the harness.

Tokenizer: `bytes/4 fallback`

| id  | category   | grep tokens | weld CLI tokens | weld MCP tokens | CLI reduction | MCP reduction |
|-----|------------|-------------|---------------|---------------|---------------|---------------|
| q01 | navigation | 91027 | 12238 | 12238 | 87% | 87% |
| q02 | navigation | 94305 | 12980 | 12980 | 86% | 86% |
| q03 | navigation | 89782 | 22533 | 22533 | 75% | 75% |
| q04 | navigation | 84433 | 20756 | 20756 | 75% | 75% |
| q05 | dependency | 89149 | 7548 | 7548 | 92% | 92% |
| q06 | dependency | 75882 | 13409 | 13409 | 82% | 82% |
| q07 | dependency | 84114 | 4221 | 4221 | 95% | 95% |
| q08 | dependency | 90094 | 13704 | 13704 | 85% | 85% |
| q09 | callgraph | 63637 | 324 | 324 | 99% | 99% |
| q10 | callgraph | 3290 | 507 | 507 | 85% | 85% |
| q11 | callgraph | 8349 | 967 | 967 | 88% | 88% |
| q12 | callgraph | 92521 | 1787 | 1787 | 98% | 98% |

## Summary

- weld CLI vs grep: median 86%, P90 98%
- weld MCP vs grep: median 86%, P90 98%

### By category

- **callgraph**: CLI median 93%, MCP median 93% (n=4)
- **dependency**: CLI median 88%, MCP median 88% (n=4)
- **navigation**: CLI median 81%, MCP median 81% (n=4)
