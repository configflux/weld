const state = {
  cy: null,
  summary: null,
  selected: null,
  pathA: null,
  pathB: null,
  lastSlice: null,
};

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
  params.set("scope", scope);
  params.set("max_nodes", limit);
  params.set("max_edges", "1500");
  if (nodeTypes.length) params.set("node_types", nodeTypes.join(","));
  if (edgeTypes.length) params.set("edge_types", edgeTypes.join(","));
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, value);
  }
  return params;
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

async function init() {
  state.summary = await getJson("/api/summary");
  populateControls(state.summary);
  initCy();
  await loadSlice();
  bindEvents();
}

function populateControls(summary) {
  $("graph-title").textContent = summary.title || "Weld Graph";
  $("status").textContent = `${summary.counts.total_nodes} nodes / ${summary.counts.total_edges} edges`;
  fillSelect($("scope-select"), summary.scopes || ["root"], false);
  fillSelect($("node-type-select"), Object.keys(summary.counts.nodes_by_type || {}).sort(), true);
  fillSelect($("edge-type-select"), Object.keys(summary.counts.edges_by_type || {}).sort(), true);
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
        selector: "node",
        style: {
          "background-color": (ele) => colorFor(ele.data("type")),
          "border-color": "#ffffff",
          "border-width": 1,
          "color": "#1f2328",
          "font-size": 9,
          "height": "mapData(degree, 0, 25, 20, 54)",
          "label": "data(label)",
          "min-zoomed-font-size": 9,
          "overlay-opacity": 0,
          "shape": "ellipse",
          "text-background-color": "#ffffff",
          "text-background-opacity": 0.86,
          "text-background-padding": 2,
          "text-max-width": 120,
          "text-valign": "bottom",
          "text-wrap": "ellipsis",
          "width": "mapData(degree, 0, 25, 20, 54)",
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
    const q = $("search-input").value.trim();
    await loadSlice(q ? { q } : {});
  });
  $("fit-button").addEventListener("click", () => state.cy.fit(undefined, 40));
  $("layout-button").addEventListener("click", runLayout);
  $("clear-button").addEventListener("click", async () => {
    state.pathA = null;
    state.pathB = null;
    clearInspector();
    await loadSlice();
  });
  $("apply-filters").addEventListener("click", () => loadSlice());
  $("scope-select").addEventListener("change", () => loadSlice());
  $("expand-button").addEventListener("click", () => {
    const data = selectedNodeData();
    if (data) loadSlice({ node_id: data.id, depth: 1 });
  });
  $("path-a-button").addEventListener("click", () => setPathEndpoint("A"));
  $("path-b-button").addEventListener("click", () => setPathEndpoint("B"));
}

async function loadSlice(extra = {}) {
  setStatus("Loading");
  const payload = await getJson("/api/slice", paramsFromControls(extra));
  state.lastSlice = payload;
  renderGraph(payload);
  renderWarnings(payload);
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

function runLayout() {
  const count = state.cy.nodes().length;
  const layout = count > 450
    ? { name: "grid", fit: true, padding: 36, avoidOverlap: true }
    : { name: "cose", fit: true, padding: 42, animate: false, nodeRepulsion: 6500, idealEdgeLength: 72 };
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

function showNode(data) {
  $("inspect-title").textContent = data.label || data.display_id || data.id;
  $("inspect-kind").textContent = `${data.type} node`;
  $("inspect-body").innerHTML = [
    field("ID", data.id),
    field("Display", data.display_id),
    data.file ? field("File", data.file) : "",
    field("Degree", data.degree),
    field("Properties", `<pre>${escapeHtml(JSON.stringify(data.props || {}, null, 2))}</pre>`),
  ].join("");
}

function showEdge(data) {
  $("inspect-title").textContent = data.label || data.type;
  $("inspect-kind").textContent = "edge";
  $("inspect-body").innerHTML = [
    field("From", data.from_display || data.source),
    field("To", data.to_display || data.target),
    field("Type", data.type),
    field("Properties", `<pre>${escapeHtml(JSON.stringify(data.props || {}, null, 2))}</pre>`),
  ].join("");
}

function clearInspector() {
  state.selected = null;
  state.cy.elements().unselect();
  $("inspect-title").textContent = "Nothing selected";
  $("inspect-kind").textContent = "Graph";
  $("inspect-body").innerHTML = state.summary ? summaryMarkup(state.summary) : "";
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

function selectedNodeData() {
  if (!state.selected || !state.selected.isNode || !state.selected.isNode()) return null;
  return state.selected.data();
}

async function setPathEndpoint(which) {
  const data = selectedNodeData();
  if (!data) return;
  if (which === "A") state.pathA = data.id;
  if (which === "B") state.pathB = data.id;
  $("inspect-kind").textContent = `Path ${state.pathA ? "A" : "."} ${state.pathB ? "B" : "."}`;
  if (state.pathA && state.pathB) {
    const payload = await getJson("/api/path", paramsFromControls({ from_id: state.pathA, to_id: state.pathB }));
    renderGraph(payload);
    highlightPath(payload.path || []);
  }
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

init().catch((error) => {
  setStatus(error.message || String(error));
  $("inspect-body").innerHTML = `<pre>${escapeHtml(error.stack || error.message || String(error))}</pre>`;
});
