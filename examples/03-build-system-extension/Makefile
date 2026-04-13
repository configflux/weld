# Makefile -- Integrate cortex discovery into a build pipeline.
#
# Targets:
#   make all          Build the project and refresh the knowledge graph.
#   make build        Run the application build (placeholder).
#   make discover     Run cortex discovery, producing .cortex/graph.json.
#   make graph-check  Verify the graph is fresh (useful for CI).
#   make clean        Remove build artifacts and the generated graph.

.PHONY: all build discover graph-check clean

SOURCES      := $(wildcard *.py)
DISCOVER_CFG := .cortex/discover.yaml
GRAPH        := .cortex/graph.json

# --- Main targets ---

all: build discover

build:
	@echo "build: nothing to compile (pure Python), but your real build goes here"

# --- Cortex integration ---

# Re-run discovery only when sources or config change.
$(GRAPH): $(SOURCES) $(DISCOVER_CFG)
	cortex discover > $(GRAPH)
	@echo "discover: wrote $(GRAPH)"

discover: $(GRAPH)

# Freshness gate -- useful in CI to enforce an up-to-date graph.
graph-check: $(GRAPH)
	@cortex stale && echo "graph-check: graph is fresh" || \
		(echo "graph-check: ERROR -- graph is stale, run 'make discover'" && exit 1)

# --- Housekeeping ---

clean:
	rm -f $(GRAPH)
	@echo "clean: removed $(GRAPH)"
