# Makefile -- Integrate weld discovery into a build pipeline.
#
# Targets:
#   make all          Build the project and refresh the connected structure.
#   make build        Run the application build (placeholder).
#   make discover     Run weld discovery, producing .weld/graph.json.
#   make graph-check  Verify the graph is fresh (useful for CI).
#   make clean        Remove build artifacts and the generated graph.

.PHONY: all build discover graph-check clean

SOURCES      := $(wildcard *.py)
DISCOVER_CFG := .weld/discover.yaml
GRAPH        := .weld/graph.json

# --- Main targets ---

all: build discover

build:
	@echo "build: nothing to compile (pure Python), but your real build goes here"

# --- Weld integration ---

# Re-run discovery only when sources or config change.
$(GRAPH): $(SOURCES) $(DISCOVER_CFG)
	wd discover > $(GRAPH)
	@echo "discover: wrote $(GRAPH)"

discover: $(GRAPH)

# Freshness gate -- useful in CI to enforce an up-to-date graph.
graph-check: $(GRAPH)
	@wd stale && echo "graph-check: graph is fresh" || \
		(echo "graph-check: ERROR -- graph is stale, run 'make discover'" && exit 1)

# --- Housekeeping ---

clean:
	rm -f $(GRAPH)
	@echo "clean: removed $(GRAPH)"
