# Vendored static assets

Third-party JavaScript bundles shipped with `wd viz`. Served loopback-only
under `/vendor/` by `weld/viz/server.py` (`_handle_static`). The viz HTTP
server has no network egress; bundles are vendored so the UI loads with no
CDN dependency.

| File | License | Source |
| --- | --- | --- |
| `cytoscape-3.33.2.min.js` | MIT | https://github.com/cytoscape/cytoscape.js (v3.33.2) |
| `cytoscape-dagre-3.0.0.min.js` | MIT | https://github.com/cytoscape/cytoscape.js-dagre (v3.0.0, self-bundled with dagre) |
| `cytoscape-navigator-2.0.2.js` | MIT | https://github.com/cytoscape/cytoscape.js-navigator (v2.0.2) |
| `cytoscape-navigator-2.0.2.css` | MIT | https://github.com/cytoscape/cytoscape.js-navigator (v2.0.2) |

Each file's verbatim upstream LICENSE is shipped alongside it. The
`cytoscape-dagre` v3.x bundle inlines its `dagre` dependency so no additional
script is required; it must load after `cytoscape-*.min.js` so it can register
the `dagre` layout name against `window.cytoscape`. The same load-order rule
applies to `cytoscape-navigator-*.js`: it self-registers against
`window.cytoscape` so cytoscape core must already be parsed. Upstream 2.0.2
ships only an unminified file (no `.min.js`); the bundle is ~30 KB and is
served loopback-only so the unminified size is not a fetch concern. The CSS
file is shipped verbatim so the bird's-eye-view panel inherits its upstream
chrome.
