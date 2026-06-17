const state = {
  cy: null,
  summary: null,
  selected: null,
  pathA: null,
  pathB: null,
  lastSlice: null,
  nodeId: null,
  depth: null,
  // bd h6z0.15: minimap toggle. `false` by default; `nav` holds the
  // cytoscape-navigator instance once the user first opens the panel
  // so subsequent toggles only flip visibility (no re-bind cost).
  minimap: false,
  nav: null,
};

// State keys persisted in location.hash (bd h6z0.4). Order is stable so URLs
// stay diff-friendly. List values join with "," to match /api/slice.
// "layout" was appended in bd h6z0.12 -- extends the same schema rather than
// introducing a parallel persistence path. "minimap" (bd h6z0.15) follows
// the same rule: it is a boolean appended to the existing schema, not a
// parallel store.
const HASH_KEYS = [
  "scope", "q", "node_id", "depth", "node_types",
  "edge_types", "hide_origins", "limit", "pathA", "pathB",
  "layout", "minimap",
];
const HASH_LIST_KEYS = new Set(["node_types", "edge_types", "hide_origins"]);
let suppressHashUpdate = false;

const colors = {
  agent: "#1f7a8c",
  command: "#344054",
  config: "#667085",
  repo: "#118c8b",
  service: "#2f855a",
  package: "#2f855a",
  file: "#667085",
  symbol: "#7c3aed",
  route: "#b54747",
  rpc: "#b54747",
  channel: "#be3f83",
  entity: "#b7791f",
  contract: "#b7791f",
  boundary: "#1f7a8c",
  hook: "#be3f83",
  instruction: "#4f46e5",
  "mcp-server": "#118c8b",
  permission: "#b54747",
  platform: "#2f855a",
  prompt: "#7c3aed",
  scope: "#b7791f",
  skill: "#7c3aed",
  subagent: "#1f7a8c",
  tool: "#344054",
  workflow: "#b7791f",
  default: "#4b5563",
};

function $(id) {
  return document.getElementById(id);
}

function paramsFromControls(extra = {}) {
  const params = new URLSearchParams();
  const scope = $("scope-select").value || "root";
  const limit = $("limit-input").value || "300";
  const nodeTypes = selectedValues($("node-type-select"));
  const edgeTypes = selectedValues($("edge-type-select"));
  const hideOrigins = selectedHideOrigins();
  params.set("scope", scope);
  params.set("max_nodes", limit);
  params.set("max_edges", "1500");
  if (nodeTypes.length) params.set("node_types", nodeTypes.join(","));
  if (edgeTypes.length) params.set("edge_types", edgeTypes.join(","));
  if (hideOrigins.length) params.set("hide_origins", hideOrigins.join(","));
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, value);
  }
  return params;
}

function selectedHideOrigins() {
  const origins = [];
  if ($("hide-stdlib-check") && $("hide-stdlib-check").checked) origins.push("stdlib");
  if ($("hide-external-check") && $("hide-external-check").checked) origins.push("external");
  return origins;
}

function selectedValues(select) {
  return Array.from(select.selectedOptions).map((option) => option.value);
}

async function getJson(path, params = null) {
  const suffix = params ? `?${params.toString()}` : "";
  const response = await fetch(`${path}${suffix}`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function stateToHash(view) {
  // Pure: view object -> "#k=v&..." string; empties omitted so canonical
  // empty view yields "" and unmodified URLs stay short.
  const params = new URLSearchParams();
  for (const key of HASH_KEYS) {
    const value = view[key];
    if (value === undefined || value === null || value === "") continue;
    if (HASH_LIST_KEYS.has(key)) {
      if (!value.length) continue;
      params.set(key, value.join(","));
    } else {
      params.set(key, String(value));
    }
  }
  const encoded = params.toString();
  return encoded ? `#${encoded}` : "";
}

function hashToState(hash) {
  // Inverse of stateToHash. Accepts leading "#" or a bare query string.
  const params = new URLSearchParams((hash || "").replace(/^#/, ""));
  const view = {};
  for (const key of HASH_KEYS) {
    if (!params.has(key)) continue;
    const value = params.get(key);
    view[key] = HASH_LIST_KEYS.has(key)
      ? (value ? value.split(",").filter(Boolean) : [])
      : value;
  }
  return view;
}

function readViewState() {
  return {
    scope: $("scope-select").value || "",
    q: $("search-input").value.trim(),
    node_id: state.nodeId || "",
    depth: state.depth || "",
    node_types: selectedValues($("node-type-select")),
    edge_types: selectedValues($("edge-type-select")),
    hide_origins: selectedHideOrigins(),
    limit: $("limit-input").value || "",
    pathA: state.pathA || "",
    pathB: state.pathB || "",
    layout: $("layout-select").value || "",
    // bd h6z0.15: only serialize "minimap=1" when the corner panel is
    // open. Default off => empty string so an unmodified URL stays clean.
    minimap: state.minimap ? "1" : "",
  };
}

function applyViewState(view) {
  // Write a view onto DOM controls + transient state. Selects must be
  // populated (populateControls) before this runs.
  if (view.scope !== undefined) setSelectValue($("scope-select"), view.scope);
  if (view.q !== undefined) $("search-input").value = view.q;
  if (view.limit) $("limit-input").value = view.limit;
  if (Array.isArray(view.node_types)) setSelectValues($("node-type-select"), view.node_types);
  if (Array.isArray(view.edge_types)) setSelectValues($("edge-type-select"), view.edge_types);
  if (Array.isArray(view.hide_origins)) {
    const set = new Set(view.hide_origins);
    $("hide-stdlib-check").checked = set.has("stdlib");
    $("hide-external-check").checked = set.has("external");
  }
  state.nodeId = view.node_id || null;
  state.depth = view.depth ? Number(view.depth) || null : null;
  state.pathA = view.pathA || null;
  state.pathB = view.pathB || null;
  if (view.layout) setSelectValue($("layout-select"), view.layout);
  // bd h6z0.15: rehydrate the minimap preference. "1" => open; anything
  // else (absent, "0", empty) => closed. DOM application is deferred to
  // applyMinimapVisibility(), which runs after initCy() so the
  // cytoscape-navigator extension has a live state.cy to attach to.
  state.minimap = view.minimap === "1";
}

function setSelectValue(select, value) {
  for (const option of select.options) option.selected = option.value === value;
}

function setSelectValues(select, values) {
  const wanted = new Set(values);
  for (const option of select.options) option.selected = wanted.has(option.value);
}

function updateHash() {
  // bd h6z0.11: every loadSlice / filter / pill / layout / minimap commit
  // routes through this single helper. pushState records a new history
  // entry so the browser back/forward stack walks through the recorded
  // view-state stream (init() seeds the very first entry via
  // history.replaceState so the user is never offered a "back" target
  // that predates the visualizer chrome).
  if (suppressHashUpdate) return;
  const hash = stateToHash(readViewState());
  if (hash && window.location.hash === hash) return;
  if (!hash && !window.location.hash) return;
  const target = hash || window.location.pathname + window.location.search;
  history.pushState(null, "", target);
}

// bd h6z0.11: popstate rehydrate. The browser fires "popstate" on
// back/forward (including the toolbar buttons that delegate to
// history.back / history.forward). We reparse the active hash, push it
// back onto the DOM controls + transient state via applyViewState, then
// re-run loadSlice() so the canvas matches. suppressHashUpdate stops
// loadSlice's tail call to updateHash from layering a fresh entry on
// top of the one the user just popped to.
async function rehydrateFromHash() {
  suppressHashUpdate = true;
  try {
    applyViewState(hashToState(window.location.hash));
    await loadSlice();
    applyMinimapVisibility();
    if (state.pathA && state.pathB) await runPathQuery();
  } finally {
    suppressHashUpdate = false;
  }
}

async function init() {
  state.summary = await getJson("/api/summary");
  populateControls(state.summary);
  populateStaleBanner(state.summary);
  initCy();
  // Rehydrate the URL hash before the first paint; suppress the inverse
  // write so an unmodified URL stays unmodified.
  suppressHashUpdate = true;
  applyViewState(hashToState(window.location.hash));
  await loadSlice();
  // bd h6z0.11: seed the first history entry with replaceState so
  // popstate has a canonical target to land on without polluting
  // back-navigation. All subsequent commits route through updateHash()
  // which uses pushState (so back / forward can walk the stack).
  const initialHash = stateToHash(readViewState());
  const initialTarget = initialHash || window.location.pathname + window.location.search;
  history.replaceState(null, "", initialTarget);
  suppressHashUpdate = false;
  window.addEventListener("popstate", () => {
    rehydrateFromHash().catch((error) => setStatus(error.message || String(error)));
  });
  bindEvents();
  // bd h6z0.15: apply the rehydrated minimap preference once the canvas
  // (state.cy) exists, before paint settles. No-op when minimap=false
  // so the panel never instantiates unless the user opens it.
  applyMinimapVisibility();
  if (state.pathA && state.pathB) await runPathQuery();
  // ADR 0073: seed the inspector with project entry points on the cold
  // open so the first screen is orienting. Skipped when the rehydrated
  // view already has a node selected/expanded (state.nodeId set).
  if (!state.nodeId) seedInspectorEntryPoints().catch(() => {});
}

const STALE_DISMISS_KEY = "weld-viz-stale-dismissed";

// bd ugqa: the staleness payload carries two orthogonal signals (the
// full graph.stale() dict, ADR 0017): sha_behind -- the recorded graph
// commit is genuinely behind HEAD -- and source_stale -- a tracked
// source file changed, which also trips on an *uncommitted* edit while
// the working tree stays at commits_behind:0. Branch the copy on the
// cause so a dirty tree no longer renders the contradictory "0 commits
// behind" sentence. Both causes are cleared by `wd discover` (it
// re-stamps the graph against the current working tree, dirty edits
// included), so recommending it never points at an unclearable action.
function staleBannerMessage(stale) {
  if (stale.sha_behind) {
    const behind = stale.commits_behind || 0;
    return `Graph is ${behind} commits behind HEAD. Run \`wd discover\` to refresh.`;
  }
  // source_stale at commits_behind:0 -- uncommitted edits to a tracked
  // source since the last discover. No "0 commits behind" claim.
  return "Graph is out of date: the working tree has uncommitted source " +
    "changes since the last `wd discover`. Run `wd discover` to refresh.";
}

function populateStaleBanner(summary) {
  const banner = $("stale-banner");
  if (!banner) return;
  const stale = summary && summary.stale;
  const dismissed = sessionStorage.getItem(STALE_DISMISS_KEY) === "1";
  if (!stale || !stale.stale || dismissed) {
    banner.hidden = true;
    return;
  }
  $("stale-banner-text").textContent = staleBannerMessage(stale);
  banner.hidden = false;
  $("stale-banner-dismiss").addEventListener("click", () => {
    sessionStorage.setItem(STALE_DISMISS_KEY, "1");
    banner.hidden = true;
  }, { once: true });
}

function populateControls(summary) {
  $("graph-title").textContent = summary.title || "Weld Graph";
  $("status").textContent = `${summary.counts.total_nodes} nodes / ${summary.counts.total_edges} edges`;
  fillSelect($("scope-select"), summary.scopes || ["root"], false);
  fillSelect($("node-type-select"), Object.keys(summary.counts.nodes_by_type || {}).sort(), true);
  fillSelect($("edge-type-select"), Object.keys(summary.counts.edges_by_type || {}).sort(), true);
  populateOriginCounts(summary.counts.nodes_by_origin || {});
  applyExportMenuVisibility(summary);
  renderLegend(summary);
}

// The Export view menu (bd h6z0.14) wraps weld.export, which only
// reads the code graph (.weld/graph.json). Under `wd agents viz`
// (summary.graph_kind === "agent") /api/export 400s rather than
// returning an empty document, so hide the menu entirely instead of
// shipping a UI affordance that cannot succeed. Default to visible
// when graph_kind is missing so older summary payloads keep working.
function applyExportMenuVisibility(summary) {
  const wrap = document.querySelector(".export-wrap");
  if (!wrap) return;
  const kind = (summary && summary.graph_kind) || "code";
  wrap.hidden = kind !== "code";
}

function renderLegend(summary) {
  const host = $("legend");
  if (!host) return;
  const byType = (summary && summary.counts && summary.counts.nodes_by_type) || {};
  const active = new Set(selectedValues($("node-type-select")));
  const rows = Object.entries(byType).sort(([a], [b]) => a.localeCompare(b));
  host.innerHTML = rows.map(([type, count]) => {
    const dim = active.size && !active.has(type) ? " dim" : "";
    const swatch = `<span class="legend-swatch" style="background:${colorFor(type)}"></span>`;
    return `<div class="legend-row${dim}">${swatch}<span class="legend-label">${escapeHtml(type)}</span><span class="legend-count">${count}</span></div>`;
  }).join("");
}

function populateOriginCounts(byOrigin) {
  const stdlib = byOrigin.stdlib || 0;
  const external = byOrigin.external || 0;
  $("hide-stdlib-count").textContent = stdlib ? `(${stdlib})` : "";
  $("hide-external-count").textContent = external ? `(${external})` : "";
}

function fillSelect(select, values, multi) {
  select.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    if (!multi && value === "root") option.selected = true;
    select.appendChild(option);
  });
}

function initCy() {
  state.cy = cytoscape({
    container: $("cy"),
    elements: [],
    minZoom: 0.05,
    maxZoom: 4,
    wheelSensitivity: 0.18,
    boxSelectionEnabled: true,
    selectionType: "single",
    style: [
      {
        // ADR 0073: legible labels for the curated cold-open overview.
        // The default view is now a small (~tens-of-nodes) curated slice,
        // so labels can be larger and stay readable without zooming in;
        // a higher min-zoomed-font-size keeps them legible when the user
        // zooms the canvas out to fit.
        selector: "node",
        style: {
          "background-color": (ele) => colorFor(ele.data("type")),
          "border-color": "#ffffff",
          "border-width": 1,
          "color": "#1f2328",
          "font-size": 12,
          "height": "mapData(degree, 0, 25, 22, 58)",
          "label": "data(label)",
          "min-zoomed-font-size": 11,
          "overlay-opacity": 0,
          "shape": "ellipse",
          "text-background-color": "#ffffff",
          "text-background-opacity": 0.92,
          "text-background-padding": 2,
          "text-max-width": 140,
          "text-valign": "bottom",
          "text-wrap": "ellipsis",
          "width": "mapData(degree, 0, 25, 22, 58)",
        },
      },
      {
        selector: "edge",
        style: {
          "curve-style": "bezier",
          "line-color": "#aeb7c2",
          "opacity": 0.62,
          "target-arrow-color": "#aeb7c2",
          "target-arrow-shape": "triangle",
          "width": 1.4,
        },
      },
      {
        selector: "node:selected",
        style: {
          "border-color": "#111827",
          "border-width": 3,
          "text-background-opacity": 1,
        },
      },
      {
        selector: "edge:selected",
        style: {
          "label": "data(label)",
          "font-size": 9,
          "line-color": "#111827",
          "target-arrow-color": "#111827",
          "width": 3,
          "opacity": 1,
        },
      },
      {
        selector: ".path",
        style: {
          "line-color": "#b54747",
          "target-arrow-color": "#b54747",
          "background-color": "#b54747",
          "width": 4,
          "opacity": 1,
        },
      },
      {
        selector: ".faded",
        style: {
          "opacity": 0.16,
        },
      },
      {
        selector: ".path-a",
        style: { "border-color": "#118c8b", "border-width": 5, "opacity": 1 },
      },
      {
        selector: ".path-b",
        style: { "border-color": "#b54747", "border-width": 5, "opacity": 1 },
      },
      // bd h6z0.9: diff tint classes paint the node background so
      // added/removed/modified nodes pop on the canvas when picked from
      // the Changes tab. Border colors mirror the section accents.
      {
        selector: ".diff-added",
        style: { "background-color": "#2f855a", "border-color": "#1f5e3f", "border-width": 4, "opacity": 1 },
      },
      {
        selector: ".diff-removed",
        style: { "background-color": "#b54747", "border-color": "#7a2f2f", "border-width": 4, "opacity": 1 },
      },
      {
        selector: ".diff-modified",
        style: { "background-color": "#b7791f", "border-color": "#7a5113", "border-width": 4, "opacity": 1 },
      },
      // bd h6z0.10: trace provenance edges. /api/trace returns a slice
      // tagged with the contract's "trace" bucket; we paint those edges
      // dashed teal so the trace surface is visually distinct from the
      // default slice + path highlights.
      {
        selector: ".trace-edge",
        style: {
          "line-color": "#118c8b",
          "line-style": "dashed",
          "target-arrow-color": "#118c8b",
          "width": 2,
          "opacity": 1,
        },
      },
    ],
  });

  state.cy.on("tap", "node", (event) => {
    state.selected = event.target;
    showNode(event.target.data());
  });
  state.cy.on("tap", "edge", (event) => {
    state.selected = event.target;
    showEdge(event.target.data());
  });
  state.cy.on("tap", (event) => {
    if (event.target === state.cy) clearInspector();
  });
}

function colorFor(type) {
  return colors[type] || colors.default;
}

function bindEvents() {
  $("search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    hideSuggest();
    const q = $("search-input").value.trim();
    await loadSlice(q ? { q } : {});
  });
  $("search-input").addEventListener("input", () => scheduleSuggest());
  $("search-input").addEventListener("focus", () => scheduleSuggest());
  $("search-input").addEventListener("blur", () => {
    // Delay so click handlers on the dropdown items can fire first.
    setTimeout(hideSuggest, 150);
  });
  $("search-suggest").addEventListener("mousedown", (event) => {
    // mousedown (not click) so the input's blur doesn't swallow the choice.
    const item = event.target.closest("[data-suggest-id]");
    if (!item) return;
    event.preventDefault();
    chooseSuggestion(item.getAttribute("data-suggest-id"));
  });
  $("fit-button").addEventListener("click", () => state.cy.fit(undefined, 40));
  $("layout-button").addEventListener("click", runLayout);
  // bd h6z0.11: visible toolbar back/forward buttons mirror the browser
  // back/forward stack. They reuse the same history.back / history.forward
  // calls as the "[" / "]" shortcuts (bd h6z0.16) so the dispatch surface
  // stays single -- popstate is what actually triggers the rehydrate.
  $("history-back-button").addEventListener("click", () => history.back());
  $("history-forward-button").addEventListener("click", () => history.forward());
  // bd h6z0.12: layout select. Changing the layout re-runs it immediately
  // (the user expectation is "pick and see") and persists the choice into
  // the URL hash so a refresh/copy-paste restores the same layout.
  $("layout-select").addEventListener("change", () => {
    runLayout();
    updateHash();
  });
  $("clear-button").addEventListener("click", async () => {
    state.pathA = null;
    state.pathB = null;
    state.nodeId = null;
    state.depth = null;
    $("search-input").value = "";
    clearInspector();
    await loadSlice();
  });
  // bd h6z0.7: every filter control routes through scheduleFilterReload so
  // behavior is consistent (debounced 250ms). loadSlice() calls updateHash on
  // completion, so this also keeps the URL hash current. Path-A/B handlers
  // call updateHash directly.
  $("scope-select").addEventListener("change", () => scheduleFilterReload());
  $("node-type-select").addEventListener("change", () => scheduleFilterReload());
  $("edge-type-select").addEventListener("change", () => scheduleFilterReload());
  $("hide-stdlib-check").addEventListener("change", () => scheduleFilterReload());
  $("hide-external-check").addEventListener("change", () => scheduleFilterReload());
  $("limit-input").addEventListener("input", () => scheduleFilterReload());
  $("expand-button").addEventListener("click", () => {
    const data = selectedNodeData();
    if (data) loadSlice({ node_id: data.id, depth: 1 });
  });
  $("trace-button").addEventListener("click", () => {
    const data = selectedNodeData();
    if (data) runTrace(data.id);
  });
  $("path-a-button").addEventListener("click", () => setPathEndpoint("A"));
  $("path-b-button").addEventListener("click", () => setPathEndpoint("B"));
  $("path-swap-button").addEventListener("click", () => swapPathEndpoints());
  // Delegated handler for the per-pill "x" clear buttons (bd h6z0.6).
  // Each pill can be cleared independently without touching the other.
  $("path-pills").addEventListener("click", (event) => {
    const target = event.target.closest(".path-pill-clear");
    if (!target) return;
    clearPathEndpoint(target.getAttribute("data-which"));
  });
  // Delegated handler for inspector neighbor links (bd h6z0.5). One
  // listener resolves clicks on any .inspector-link rendered into the
  // inspector body and drills into a 1-hop neighborhood for that id.
  $("inspect-body").addEventListener("click", (event) => {
    const anchor = event.target.closest(".inspector-link");
    if (!anchor) return;
    event.preventDefault();
    const id = anchor.getAttribute("data-node-id");
    if (id) loadSlice({ node_id: id, depth: 1 });
  });
  // bd h6z0.14: Export view menu. The button toggles the dropdown, a
  // single delegated handler resolves clicks on individual menu items
  // by reading data-format, and document-level clicks close the menu.
  $("export-button").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleExportMenu();
  });
  $("export-menu").addEventListener("click", (event) => {
    const item = event.target.closest("[data-format]");
    if (!item) return;
    event.preventDefault();
    const format = item.getAttribute("data-format");
    closeExportMenu();
    handleExport(format);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".export-wrap")) closeExportMenu();
  });
  // bd h6z0.15: minimap toggle. Flips state.minimap, applies visibility
  // through the single helper so init() and the toggle share a path,
  // then persists into the URL hash.
  $("minimap-toggle").addEventListener("click", () => {
    state.minimap = !state.minimap;
    applyMinimapVisibility();
    updateHash();
  });
}

// bd h6z0.15: minimap visibility helper. Single dispatch point so the
// rehydrate (init) and toggle (click) flows share one code path. The
// first time the panel is opened we lazily call cy.navigator() so the
// extension never instantiates unless the user actually opts in.
//
// cytoscape-navigator's _initPanel() only honors the `container` option
// when it is a CSS-selector string -- passing a DOM element triggers the
// fallback branch that creates a fresh <div> on document.body. So we
// pass the id selector "#cy-minimap" and let upstream resolve it.
function applyMinimapVisibility() {
  const panel = $("cy-minimap");
  const toggle = $("minimap-toggle");
  if (!panel || !toggle || !state.cy) return;
  if (state.minimap) {
    panel.hidden = false;
    if (!state.nav && typeof state.cy.navigator === "function") {
      state.nav = state.cy.navigator({ container: "#cy-minimap" });
    }
  } else {
    panel.hidden = true;
  }
  toggle.classList.toggle("active", state.minimap);
  toggle.setAttribute("aria-pressed", state.minimap ? "true" : "false");
}

// bd h6z0.7: consistent filter apply. All filter controls (scope,
// node-type, edge-type, hide-stdlib, hide-external, limit) route through
// this 250ms debounce so rapid edits coalesce into a single /api/slice
// call. Replaces the old Apply button; behavior is now uniform across
// every filter regardless of input style.
let filterReloadTimer = null;

function scheduleFilterReload() {
  if (filterReloadTimer) clearTimeout(filterReloadTimer);
  filterReloadTimer = setTimeout(() => {
    filterReloadTimer = null;
    loadSlice();
  }, 250);
}

async function loadSlice(extra = {}) {
  setStatus("Loading");
  if ("node_id" in extra) state.nodeId = extra.node_id || null;
  if ("depth" in extra) state.depth = extra.depth ?? null;
  // Replay node_id/depth/q from state/DOM when the caller didn't override.
  const merged = { ...extra };
  if (!("node_id" in extra) && state.nodeId) {
    merged.node_id = state.nodeId;
    if (state.depth) merged.depth = state.depth;
  }
  if (!("q" in extra)) {
    const q = $("search-input").value.trim();
    if (q) merged.q = q;
  }
  const payload = await getJson("/api/slice", paramsFromControls(merged));
  state.lastSlice = payload;
  renderGraph(payload);
  renderWarnings(payload);
  renderLegend(state.summary);
  // bd h6z0.10: leaving a trace view (or any non-trace navigation)
  // clears the bucket tray so stale counts never linger over the new
  // canvas.
  hideTraceTray();
  // Re-apply A/B endpoint rings after each slice swap so the markers
  // persist across loadSlice() calls until cleared (bd h6z0.6).
  markPathEndpoints();
  renderPathPills();
  updateHash();
  // bd h6z0.8: empty-state hint -- when the slice has zero visible
  // nodes, surface the top-5 most-connected entry points in the
  // inspector so the user is never stranded.
  await maybeRenderEmptyState(payload);
}

function renderGraph(payload) {
  const cy = state.cy;
  cy.elements().remove();
  cy.add(payload.elements.nodes);
  cy.add(payload.elements.edges);
  runLayout();
  const visible = `${payload.stats.visible_nodes} nodes / ${payload.stats.visible_edges} edges`;
  const suffix = payload.truncated.nodes || payload.truncated.edges ? " capped" : "";
  setStatus(`${visible}${suffix}`);
}

// bd h6z0.12: manual layout control. The toolbar's #layout-select drives
// the dispatch; per-name option overrides keep each layout tuned without
// reintroducing the old node-count heuristic.
const LAYOUT_OPTIONS = {
  cose: { animate: false, nodeRepulsion: 6500, idealEdgeLength: 72 },
  dagre: { rankDir: "TB", nodeSep: 36, rankSep: 56 },
  concentric: { minNodeSpacing: 18 },
  grid: { avoidOverlap: true },
  breadthfirst: { directed: true, spacingFactor: 1.2 },
};

// ADR 0073: the unconfigured cold-open overview is a curated, hierarchical
// architecture slice (packages -> files; commands/agents as roots), so it
// reads best under dagre. We default the *applied* layout to dagre for that
// view only -- the #layout-select keeps cose as its stored default value, and
// an explicit user choice (persisted as `layout=` in the URL hash) always
// wins. Any non-overview slice keeps the select-driven dispatch unchanged.
function isOverviewView() {
  return (
    !state.nodeId &&
    !$("search-input").value.trim() &&
    !state.pathA &&
    !state.pathB
  );
}

function layoutPinnedInHash() {
  return new URLSearchParams((window.location.hash || "").replace(/^#/, "")).has("layout");
}

function effectiveLayoutName() {
  if (!layoutPinnedInHash() && isOverviewView()) return "dagre";
  return $("layout-select").value || "cose";
}

function runLayout() {
  const name = effectiveLayoutName();
  const layout = { name, fit: true, padding: 36, ...(LAYOUT_OPTIONS[name] || {}) };
  state.cy.layout(layout).run();
}

function renderWarnings(payload) {
  if (payload.warnings && payload.warnings.length) {
    $("status").textContent = payload.warnings[0];
  }
}

function setStatus(text) {
  $("status").textContent = text;
}

function isKnownNodeId(id) {
  // Look up in the live Cytoscape graph: only currently-loaded nodes
  // are clickable. Ids not in the current slice render as plain text
  // so the inspector never offers a click that would 404.
  if (!id) return false;
  return Boolean(state.cy && state.cy.getElementById(id).nonempty());
}

function nodeLink(id, displayText) {
  // Render a clickable anchor for a known node id. The delegated handler
  // in bindEvents() reads data-node-id and dispatches loadSlice. Both
  // the visible text and the attribute are escaped so a malicious id
  // (with quotes or angle brackets) cannot break out of the markup.
  const label = escapeHtml(displayText ?? id);
  if (!isKnownNodeId(id)) return label;
  return `<a href="#" class="inspector-link" data-node-id="${escapeHtml(id)}">${label}</a>`;
}

function showNode(data) {
  $("inspect-title").textContent = data.label || data.display_id || data.id;
  $("inspect-kind").textContent = `${data.type} node`;
  $("inspect-body").innerHTML = [
    field("ID", nodeLink(data.id, data.id)),
    field("Display", nodeLink(data.id, data.display_id)),
    data.file ? field("File", escapeHtml(data.file)) : "",
    openInEditorMarkup(data),
    field("Degree", data.degree),
    field("Properties", renderProperties(data.props)),
  ].join("");
  updateTraceButtonVisibility(data.type);
}

// bd h6z0.10: trace-eligible node types. /api/trace's anchor BFS only
// produces useful slices for these kinds, so the Trace button is hidden
// otherwise to avoid offering a dead action.
const TRACE_ELIGIBLE_TYPES = new Set([
  "service", "contract", "boundary", "interface", "hook", "route", "rpc",
]);

function updateTraceButtonVisibility(nodeType) {
  const button = $("trace-button");
  if (!button) return;
  button.hidden = !TRACE_ELIGIBLE_TYPES.has(nodeType);
}

// bd h6z0.13: open-in-editor links. file/symbol nodes carrying
// props.file get two affordances in the inspector:
//   1. vscode://file/<abs-path>:<line>   (local editor jump)
//   2. <remote_url>/blob/<sha>/<file>#L<line>   (browser jump)
// Both URLs are escaped before render; props.line is optional.
function openInEditorMarkup(data) {
  const relPath = (data.props && data.props.file) || data.file || "";
  if (!relPath) return "";
  if (data.type !== "file" && data.type !== "symbol") return "";
  const summary = state.summary || {};
  const line = (data.props && data.props.line) || null;
  const lineSuffix = line ? `:${line}` : "";
  const hashSuffix = line ? `#L${line}` : "";
  const absRoot = summary.abs_root || "";
  const absPath = absRoot
    ? `${absRoot.replace(/\/$/, "")}/${relPath}`
    : relPath;
  const vscode = `vscode://file/${absPath}${lineSuffix}`;
  const links = [
    `<a class="open-in-editor" href="${escapeHtml(vscode)}">Open in VS Code</a>`,
  ];
  if (summary.remote_url && summary.head_sha) {
    const remote = `${summary.remote_url}/blob/${summary.head_sha}/${relPath}${hashSuffix}`;
    links.push(
      `<a class="open-in-editor" href="${escapeHtml(remote)}" target="_blank" rel="noopener">Open on remote</a>`,
    );
  }
  return field("Open", links.join(" "));
}

function showEdge(data) {
  $("inspect-title").textContent = data.label || data.type;
  $("inspect-kind").textContent = "edge";
  $("inspect-body").innerHTML = [
    field("From", nodeLink(data.source, data.from_display || data.source)),
    field("To", nodeLink(data.target, data.to_display || data.target)),
    field("Type", data.type),
    field("Properties", renderProperties(data.props)),
  ].join("");
  updateTraceButtonVisibility(null);
}

function clearInspector() {
  state.selected = null;
  state.cy.elements().unselect();
  $("inspect-title").textContent = "Nothing selected";
  $("inspect-kind").textContent = "Graph";
  $("inspect-body").innerHTML = state.summary ? summaryMarkup(state.summary) : "";
  updateTraceButtonVisibility(null);
  // ADR 0073: orient the cold open. With nothing selected, seed the
  // inspector with the real project entry points (CLI commands, MCP
  // tools, top packages) so the user always has a starting point even
  // before touching the canvas. Advisory: never blocks on failure.
  seedInspectorEntryPoints().catch(() => {});
}

// ADR 0073: fetch the top project entry points (the same `q=""`
// search-suggest set the empty-state hint uses) and render them as
// clickable links under the default "Nothing selected" inspector
// panel. No-op while a node/edge is selected so we never clobber an
// active inspection. Reuses the inspector-link delegated handler.
async function seedInspectorEntryPoints() {
  if (state.selected) return;
  const params = new URLSearchParams({ q: "", limit: "8" });
  const result = await getJson("/api/search-suggest", params);
  const items = result.suggestions || [];
  if (!items.length || state.selected) return;
  const links = items.map((item) => {
    const id = escapeHtml(item.id);
    const label = escapeHtml(item.label || item.id);
    const type = escapeHtml(item.type || "");
    return `<li><a href="#" class="inspector-link" data-node-id="${id}">${label}</a>` +
      `<span class="entry-point-type">${type}</span></li>`;
  }).join("");
  const summary = state.summary ? summaryMarkup(state.summary) : "";
  $("inspect-body").innerHTML = summary +
    `<div class="field"><div class="key">Entry points</div>` +
    `<div class="value"><ul class="entry-points">${links}</ul></div></div>`;
}

function summaryMarkup(summary) {
  const nodeTypes = Object.entries(summary.counts.nodes_by_type || {})
    .map(([key, value]) => `<span class="pill">${escapeHtml(key)} ${value}</span>`).join("");
  return [
    field("Root", summary.root),
    field("Graph", summary.graph_exists ? summary.graph_path : "missing"),
    `<div class="pill-row">${nodeTypes}</div>`,
  ].join("");
}

function field(key, value) {
  return `<div class="field"><div class="key">${escapeHtml(key)}</div><div class="value">${value}</div></div>`;
}

// bd 1bfc: readable Properties view. The inspector used to dump the raw
// props object as a `<pre>${JSON.stringify(...)}` wall, which is fine for
// power users but unreadable as the default. renderProperties() turns an
// arbitrary props object into labelled key/value rows (humanized keys,
// scalars as text, arrays as chips, nested objects as nested rows) and
// demotes the full JSON behind a collapsed <details> "Raw" disclosure so
// no information is lost. Every key and value is escaped (escapeHtml /
// the chip + row builders below) before it reaches innerHTML, so a prop
// carrying angle brackets or quotes cannot inject markup.
function renderProperties(props) {
  const entries = props && typeof props === "object" ? Object.entries(props) : [];
  if (!entries.length) {
    return `<div class="prop-empty">No properties</div>`;
  }
  const rows = entries
    .map(([key, value]) =>
      `<div class="prop-row"><div class="prop-key">${escapeHtml(humanizePropKey(key))}` +
      `</div><div class="prop-val">${renderPropValue(value)}</div></div>`,
    )
    .join("");
  // Raw JSON stays one click away (collapsed) so power users keep the
  // exact serialized shape. escapeHtml() guards the serialized text.
  const raw = escapeHtml(JSON.stringify(props, null, 2));
  return (
    `<div class="prop-rows">${rows}</div>` +
    `<details class="prop-raw"><summary>Raw JSON</summary><pre>${raw}</pre></details>`
  );
}

// bd 1bfc: humanize a prop key for display -- "imports_from" -> "Imports
// From", "line_count" -> "Line Count". Splits on underscores/hyphens and
// title-cases each token; leaves already-spaced labels intact.
function humanizePropKey(key) {
  return String(key)
    .split(/[_-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

// bd 1bfc: render one prop value by shape. `depth` bounds nested-object
// recursion so a pathological deeply-nested prop cannot blow the stack;
// past the cap we fall back to an escaped JSON snippet. Returns escaped
// HTML in every branch -- scalars via escapeHtml, arrays as escaped chip
// spans, nested objects as escaped nested rows.
function renderPropValue(value, depth = 0) {
  if (value === null || value === undefined) {
    return `<span class="prop-empty">&mdash;</span>`;
  }
  if (Array.isArray(value)) {
    if (!value.length) return `<span class="prop-empty">none</span>`;
    const chips = value
      .map((item) => {
        const text = item && typeof item === "object"
          ? JSON.stringify(item)
          : String(item);
        return `<span class="prop-chip">${escapeHtml(text)}</span>`;
      })
      .join("");
    return `<div class="prop-chips">${chips}</div>`;
  }
  if (typeof value === "object") {
    if (depth >= 2) {
      return `<span class="prop-scalar">${escapeHtml(JSON.stringify(value))}</span>`;
    }
    const inner = Object.entries(value)
      .map(([key, val]) =>
        `<div class="prop-row prop-nested"><div class="prop-key">` +
        `${escapeHtml(humanizePropKey(key))}</div><div class="prop-val">` +
        `${renderPropValue(val, depth + 1)}</div></div>`,
      )
      .join("");
    return inner || `<span class="prop-empty">none</span>`;
  }
  return `<span class="prop-scalar">${escapeHtml(String(value))}</span>`;
}

function selectedNodeData() {
  if (!state.selected || !state.selected.isNode || !state.selected.isNode()) return null;
  return state.selected.data();
}

async function setPathEndpoint(which) {
  const data = selectedNodeData();
  if (!data) return;
  if (which === "A") state.pathA = data.id;
  if (which === "B") state.pathB = data.id;
  renderPathPills();
  markPathEndpoints();
  updateHash();
  if (state.pathA && state.pathB) await runPathQuery();
}

async function runPathQuery() {
  const payload = await getJson(
    "/api/path",
    paramsFromControls({ from_id: state.pathA, to_id: state.pathB }),
  );
  renderGraph(payload);
  highlightPath(payload.path || []);
  // Re-apply A/B endpoint rings on top of the path highlight so the
  // user can still see which side is which after the path renders.
  markPathEndpoints();
}

function highlightPath(pathIds) {
  state.cy.elements().removeClass("path faded");
  if (!pathIds.length) return;
  state.cy.elements().addClass("faded");
  pathIds.forEach((id) => state.cy.getElementById(id).removeClass("faded").addClass("path"));
  state.cy.edges().forEach((edge) => {
    if (pathIds.includes(edge.data("source")) && pathIds.includes(edge.data("target"))) {
      edge.removeClass("faded").addClass("path");
    }
  });
}

function pathEndpointLabel(id) {
  // Resolve to the loaded node's label when present so the pill shows
  // something human-readable. Fall back to the raw id (which may still
  // be useful even when the endpoint sits outside the current slice).
  if (!id) return "";
  const node = state.cy && state.cy.getElementById(id);
  if (node && node.nonempty()) return node.data("label") || node.data("display_id") || id;
  return id;
}

function renderPathPill(which, baseClass, id) {
  // Build one pill. ``baseClass`` is the literal pill class
  // (``path-pill-a`` / ``path-pill-b``) -- keeping them inline keeps
  // the static-asset audit honest about which classes the UI emits.
  const label = id ? escapeHtml(pathEndpointLabel(id)) : "empty";
  const cls = `path-pill ${baseClass}${id ? "" : " empty"}`;
  const clear = id
    ? `<button type="button" class="path-pill-clear" data-which="${which}" title="Clear ${which}" aria-label="Clear ${which}">x</button>`
    : "";
  return `<span class="${cls}"><span class="path-pill-key">${which}:</span> <span class="path-pill-label">${label}</span>${clear}</span>`;
}

function renderPathPills() {
  // Single render pass paints both pills from state. Empty endpoints
  // still render so the slot is visible -- this is the "discoverability"
  // half of the acceptance criterion.
  const host = $("path-pills");
  if (!host) return;
  host.innerHTML = [
    renderPathPill("A", "path-pill-a", state.pathA),
    renderPathPill("B", "path-pill-b", state.pathB),
  ].join("");
}

function markPathEndpoints() {
  // Apply the ring classes to the live A/B endpoints. Runs after every
  // graph render so the rings persist across loadSlice() calls until
  // cleared. Cytoscape silently no-ops on missing ids -- endpoints that
  // fall outside the current slice just leave no ring this frame.
  if (!state.cy) return;
  state.cy.nodes().removeClass("path-a path-b");
  if (state.pathA) state.cy.getElementById(state.pathA).addClass("path-a");
  if (state.pathB) state.cy.getElementById(state.pathB).addClass("path-b");
}

async function swapPathEndpoints() {
  const next = state.pathB;
  state.pathB = state.pathA;
  state.pathA = next;
  renderPathPills();
  markPathEndpoints();
  updateHash();
  if (state.pathA && state.pathB) await runPathQuery();
}

async function clearPathEndpoint(which) {
  if (which === "A") state.pathA = null;
  if (which === "B") state.pathB = null;
  renderPathPills();
  markPathEndpoints();
  updateHash();
}

// bd h6z0.14: Export view. Five formats route through one dispatcher.
// Mermaid / DOT / D2 are server-side via /api/export so the rendered
// artefact reuses weld.export's serializers as-is. PNG is rasterized
// client-side via cytoscape's cy.png(). JSON dumps the last slice
// payload so the user gets the exact graph the canvas is showing
// (filters, depth, scope applied). Filenames include the focused
// node id so concurrent exports stay distinguishable.
const EXPORT_EXTENSIONS = {
  mermaid: "mmd",
  dot: "dot",
  d2: "d2",
  png: "png",
  json: "json",
};

function exportFilename(format) {
  const nodeId = state.nodeId || "";
  const safe = nodeId ? `weld-graph-${sanitizeFilenamePart(nodeId)}` : "weld-graph";
  return `${safe}.${EXPORT_EXTENSIONS[format] || "txt"}`;
}

function sanitizeFilenamePart(value) {
  return String(value).replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "");
}

function toggleExportMenu() {
  const menu = $("export-menu");
  const button = $("export-button");
  if (!menu || !button) return;
  const isHidden = menu.hidden;
  menu.hidden = !isHidden;
  button.setAttribute("aria-expanded", isHidden ? "true" : "false");
}

function closeExportMenu() {
  const menu = $("export-menu");
  const button = $("export-button");
  if (menu) menu.hidden = true;
  if (button) button.setAttribute("aria-expanded", "false");
}

async function handleExport(format) {
  if (format === "mermaid" || format === "dot" || format === "d2") {
    await downloadServerExport(format);
    return;
  }
  if (format === "png") {
    downloadPng();
    return;
  }
  if (format === "json") {
    downloadJson();
    return;
  }
}

async function downloadServerExport(format) {
  const params = new URLSearchParams({ format });
  if (state.nodeId) {
    params.set("node_id", state.nodeId);
    if (state.depth) params.set("depth", String(state.depth));
  }
  const response = await fetch(`/api/export?${params.toString()}`);
  if (!response.ok) {
    setStatus(`Export failed (${response.status})`);
    return;
  }
  const blob = await response.blob();
  triggerDownload(blob, exportFilename(format));
}

function downloadPng() {
  if (!state.cy) return;
  // cy.png() returns a data URI; use it directly as the anchor href
  // so the download flow is identical to the blob path.
  const dataUri = state.cy.png({ full: true, scale: 2, bg: "#ffffff" });
  triggerDownloadFromHref(dataUri, exportFilename("png"));
}

function downloadJson() {
  const payload = state.lastSlice || { elements: { nodes: [], edges: [] } };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  triggerDownload(blob, exportFilename("json"));
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  triggerDownloadFromHref(url, filename);
  // Defer revoke so Firefox/Safari have time to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function triggerDownloadFromHref(href, filename) {
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
}

// bd h6z0.10: Trace-this. Calls /api/trace with the selected node and
// depth=2 (the acceptance contract), then renders the returned slice
// in place. The post-render hook tags edges that came back in the
// trace payload with the .trace-edge class so they paint dashed teal,
// and the bucket counts fill the inspector's trace-tray.
async function runTrace(nodeId) {
  if (!nodeId) return;
  setStatus("Tracing");
  const params = paramsFromControls({ node_id: nodeId, depth: 2 });
  const payload = await getJson("/api/trace", params);
  state.lastSlice = payload.graph;
  renderGraph(payload.graph);
  markTraceEdges(payload.graph);
  renderTraceTray(payload.trace);
  renderWarnings(payload.graph);
  // Keep A/B rings + neighbor links consistent with the rest of the
  // graph-swap flow (loadSlice does the same).
  markPathEndpoints();
  renderPathPills();
}

function markTraceEdges(slice) {
  // Every edge in a /api/trace payload is part of the provenance slice,
  // so tag them all with .trace-edge. The cytoscape selector paints them
  // dashed teal regardless of edge.type.
  if (!state.cy || !slice || !slice.elements) return;
  state.cy.edges().addClass("trace-edge");
}

function renderTraceTray(trace) {
  // Bucket counts live in the inspector trace tray. The five buckets
  // are the canonical service / interface / contract / boundary /
  // verification set exposed by trace.py.
  const tray = $("trace-tray");
  if (!tray) return;
  const buckets = [
    ["services", "Services"],
    ["interfaces", "Interfaces"],
    ["contracts", "Contracts"],
    ["boundaries", "Boundaries"],
    ["verifications", "Verifications"],
  ];
  const pills = buckets.map(([key, label]) => {
    const count = ((trace && trace[key]) || []).length;
    return `<span class="trace-pill"><span class="trace-pill-key">${escapeHtml(label)}</span><span class="trace-pill-count">${count}</span></span>`;
  }).join("");
  tray.innerHTML = pills;
  tray.hidden = false;
}

function hideTraceTray() {
  const tray = $("trace-tray");
  if (!tray) return;
  tray.hidden = true;
  tray.innerHTML = "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// bd h6z0.8: search-suggest dropdown. Debounced input handler hits
// /api/search-suggest and renders [{id, label, type}] under the
// search box. Choosing an item drives loadSlice(q=label) so the
// graph repaints around the picked node.
let suggestTimer = null;

function scheduleSuggest() {
  if (suggestTimer) clearTimeout(suggestTimer);
  suggestTimer = setTimeout(runSuggest, 200);
}

async function runSuggest() {
  const q = $("search-input").value.trim();
  try {
    const params = new URLSearchParams({ q, limit: "20" });
    const payload = await getJson("/api/search-suggest", params);
    renderSuggest(payload.suggestions || []);
  } catch (_error) {
    // Suggestions are advisory; never break input on failure.
    hideSuggest();
  }
}

function renderSuggest(items) {
  const host = $("search-suggest");
  if (!host) return;
  if (!items.length) {
    hideSuggest();
    return;
  }
  host.innerHTML = items.map((item) => {
    const id = escapeHtml(item.id);
    const label = escapeHtml(item.label || item.id);
    const type = escapeHtml(item.type || "");
    return `<li role="option" class="search-suggest-item" data-suggest-id="${id}"><span class="suggest-label">${label}</span><span class="suggest-type">${type}</span></li>`;
  }).join("");
  host.hidden = false;
  $("search-input").setAttribute("aria-expanded", "true");
}

function hideSuggest() {
  const host = $("search-suggest");
  if (!host) return;
  host.hidden = true;
  host.innerHTML = "";
  $("search-input").setAttribute("aria-expanded", "false");
}

function chooseSuggestion(id) {
  hideSuggest();
  // Drive the graph around the chosen node directly: node_id+depth=1
  // mirrors clicking a neighbor link in the inspector (bd h6z0.5) so
  // suggestions get the same drill-in semantics.
  $("search-input").value = "";
  loadSlice({ node_id: id, depth: 1 });
}

// bd h6z0.8: empty-state hint. When a slice returns zero visible
// nodes, fetch the top-5 most-connected nodes (q="" suggestions)
// and render them in the inspector body as clickable entry points
// so the user is never stuck staring at an empty canvas.
async function maybeRenderEmptyState(payload) {
  const visible = (payload.stats && payload.stats.visible_nodes) || 0;
  if (visible > 0) return;
  try {
    const params = new URLSearchParams({ q: "", limit: "5" });
    const result = await getJson("/api/search-suggest", params);
    renderEmptyStateSuggestions(result.suggestions || []);
  } catch (_error) {
    // Empty-state hint is advisory; never break on failure.
  }
}

function renderEmptyStateSuggestions(items) {
  if (!items.length) return;
  // Use inspector-link directly so the existing delegated click
  // handler on #inspect-body picks them up. nodeLink() would gate on
  // isKnownNodeId(), and after an empty slice the cytoscape graph is
  // also empty, so we'd render plain text instead of clickable links.
  const links = items.map((item) => {
    const id = escapeHtml(item.id);
    const label = escapeHtml(item.label || item.id);
    return `<li><a href="#" class="inspector-link" data-node-id="${id}">${label}</a></li>`;
  }).join("");
  $("inspect-title").textContent = "No results";
  $("inspect-kind").textContent = "Try one of these entry points";
  $("inspect-body").innerHTML = `<ul class="empty-state-suggestions">${links}</ul>`;
}

// bd h6z0.9: Changes tab. Wraps /api/diff and renders added/removed/
// modified nodes plus added/removed edges. Clicking a row jumps to the
// node (loadSlice with depth=1) and re-applies the tint class so the
// node stays distinguishable while the user explores. Empty diff drops
// in the friendly "No changes since last `wd discover`." hint.
state.diff = null;
state.activeTab = "details";

function setActiveTab(name) {
  state.activeTab = name;
  for (const tab of document.querySelectorAll(".inspect-tab")) {
    const isActive = tab.getAttribute("data-tab") === name;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
  }
  $("inspect-body").hidden = name !== "details";
  $("inspect-changes").hidden = name !== "changes";
  if (name === "changes") {
    loadDiff().catch((error) => {
      $("inspect-changes").innerHTML =
        `<div class="changes-empty">Failed to load diff: ${escapeHtml(error.message || String(error))}</div>`;
    });
  } else {
    // Leaving the Changes tab clears the canvas tint so other views
    // (path highlights, neighborhood drill-ins) are not visually
    // overloaded with leftover diff markers.
    clearDiffHighlights();
  }
}

async function loadDiff() {
  // Refetch on every tab activation so a "wd discover" run from
  // another shell shows up the next time the user opens Changes,
  // without needing a full page reload.
  state.diff = await getJson("/api/diff");
  renderChangesPanel(state.diff);
}

function renderChangesPanel(diff) {
  const host = $("inspect-changes");
  const totalChanges =
    diff.added_nodes.length + diff.removed_nodes.length +
    diff.modified_nodes.length + diff.added_edges.length +
    diff.removed_edges.length;
  if (totalChanges === 0) {
    host.innerHTML = `<div class="changes-empty">No changes since last <code>wd discover</code>.</div>`;
    return;
  }
  host.innerHTML = [
    renderChangesNodeSection("added", "Added nodes", diff.added_nodes),
    renderChangesNodeSection("removed", "Removed nodes", diff.removed_nodes),
    renderChangesNodeSection("modified", "Modified nodes", diff.modified_nodes),
    renderChangesEdgeSection("added", "Added edges", diff.added_edges),
    renderChangesEdgeSection("removed", "Removed edges", diff.removed_edges),
  ].filter(Boolean).join("");
}

function renderChangesNodeSection(kind, title, entries) {
  if (!entries.length) return "";
  const rows = entries.map((entry) => {
    const node = entry.node || entry.after || {};
    const label = node.label || entry.id;
    return `<li class="changes-row" data-node-id="${escapeHtml(entry.id)}" data-diff-kind="${kind}" tabindex="0">${escapeHtml(label)}</li>`;
  }).join("");
  return `<section class="changes-section ${kind}"><div class="changes-section-head">${escapeHtml(title)}<span class="changes-count">${entries.length}</span></div><ul class="changes-list">${rows}</ul></section>`;
}

function renderChangesEdgeSection(kind, title, edges) {
  if (!edges.length) return "";
  const rows = edges.map((edge) => {
    const text = `${edge.from} -> ${edge.to} (${edge.type})`;
    return `<li class="changes-row" data-edge-from="${escapeHtml(edge.from)}" data-edge-to="${escapeHtml(edge.to)}" data-diff-kind="${kind}" tabindex="0">${escapeHtml(text)}</li>`;
  }).join("");
  return `<section class="changes-section ${kind}"><div class="changes-section-head">${escapeHtml(title)}<span class="changes-count">${edges.length}</span></div><ul class="changes-list">${rows}</ul></section>`;
}

function clearDiffHighlights() {
  if (!state.cy) return;
  state.cy.nodes().removeClass("diff-added diff-removed diff-modified");
}

function applyDiffHighlight(nodeId, kind) {
  // Apply the tint class to the node if it is in the current slice.
  // Removed nodes typically are not loaded (since the current graph no
  // longer contains them); we still call addClass so any future slice
  // that does include the id picks up the tint immediately.
  if (!state.cy || !nodeId) return;
  clearDiffHighlights();
  const element = state.cy.getElementById(nodeId);
  if (element && element.nonempty()) {
    element.addClass(`diff-${kind}`);
    state.cy.center(element);
  }
}

async function handleChangesRowClick(row) {
  const kind = row.getAttribute("data-diff-kind");
  const nodeId = row.getAttribute("data-node-id");
  if (nodeId) {
    await loadSlice({ node_id: nodeId, depth: 1 });
    applyDiffHighlight(nodeId, kind);
    return;
  }
  // Edge row: jump to either endpoint -- pick "from" by default so the
  // user lands on the originating side of the relationship.
  const from = row.getAttribute("data-edge-from");
  if (from) {
    await loadSlice({ node_id: from, depth: 1 });
    applyDiffHighlight(from, kind);
  }
}

function bindChangesTab() {
  $("tab-details").addEventListener("click", () => setActiveTab("details"));
  $("tab-changes").addEventListener("click", () => setActiveTab("changes"));
  $("inspect-changes").addEventListener("click", (event) => {
    const row = event.target.closest(".changes-row");
    if (!row) return;
    handleChangesRowClick(row);
  });
  $("inspect-changes").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest(".changes-row");
    if (!row) return;
    event.preventDefault();
    handleChangesRowClick(row);
  });
}

// Wire the Changes tab synchronously: the script tag sits at the
// bottom of <body>, so every element bindChangesTab touches already
// exists by the time this file runs.
bindChangesTab();

// bd h6z0.16: keyboard shortcuts. A single window-scoped keydown
// listener dispatches by key. Bindings are no-ops while an INPUT,
// TEXTAREA, or SELECT has focus -- except "/" which always grabs the
// search input. "[" / "]" delegate to the browser history API so
// once h6z0.11 lands pushState the back/forward stack is walked.
// "?" toggles the cheatsheet modal; Escape (or the close button)
// closes it.
function isEditableTarget(target) {
  if (!target || !target.tagName) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

function toggleCheatsheet(open) {
  // `open` is the desired visible state. Omit to flip the current
  // state (e.g. the "?" handler), pass `true`/`false` to force a
  // specific state (e.g. the close button passes `false`).
  const modal = $("cheatsheet");
  if (!modal) return;
  const wantOpen = typeof open === "boolean" ? open : modal.hidden;
  modal.hidden = !wantOpen;
}

function bindShortcuts() {
  const closeButton = $("cheatsheet-close");
  if (closeButton) closeButton.addEventListener("click", () => toggleCheatsheet(false));
  window.addEventListener("keydown", (event) => {
    // Escape closes the modal regardless of focus so the user is
    // never trapped on top of an input that lives outside the modal.
    if (event.key === "Escape") {
      const modal = $("cheatsheet");
      if (modal && !modal.hidden) {
        event.preventDefault();
        toggleCheatsheet(false);
      }
      return;
    }
    // "/" always focuses search, even when an input has focus, so it
    // works as a universal escape. preventDefault stops the literal
    // "/" from landing in the now-focused search input.
    if (event.key === "/") {
      event.preventDefault();
      $("search-input").focus();
      $("search-input").select();
      return;
    }
    // Every other binding is a no-op while an editable surface has
    // focus so typing in the search box / limit input is unaffected.
    if (isEditableTarget(event.target)) return;
    switch (event.key) {
      case "f":
        state.cy.fit(undefined, 40);
        break;
      case "l":
        runLayout();
        break;
      case "a":
        setPathEndpoint("A");
        break;
      case "b":
        setPathEndpoint("B");
        break;
      case "[":
        history.back();
        break;
      case "]":
        history.forward();
        break;
      case "?":
        toggleCheatsheet();
        break;
    }
  });
}

bindShortcuts();

init().catch((error) => {
  setStatus(error.message || String(error));
  $("inspect-body").innerHTML = `<pre>${escapeHtml(error.stack || error.message || String(error))}</pre>`;
});
