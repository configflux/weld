"""Tests for *which checkout* a read is answered from (ADR 0096).

One question, asked at every layer: the git plumbing that identifies a
repository and enumerates its checkouts, the resolver that turns a
working directory into a project root, the CLI wiring that must apply
that resolver in all five read parsers, the branch identity that makes a
wrong-root answer visible, and the discover path that has to leave a
usable graph behind in a fresh linked worktree.

They live here rather than inline in BUILD.bazel because that file is an
index of subjects, not a target list, and this area is still growing
(worktree seeding and the optional MCP per-request root are separate
changes that will add targets alongside these). Adding a target here
costs BUILD.bazel nothing.

Every fixture shells real ``git`` in a temp directory with its own
identity -- no ambient user config, no mocks, and no path pattern tied
to whichever tool created a worktree.
"""

load("@rules_python//python:defs.bzl", "py_test")

# Fixture modules ride in `srcs` (the `_impact_test_helpers` pattern)
# rather than becoming a py_library: they are test data, not a surface
# anything imports outside these targets. `_seed_fixture.py` holds the
# git/discover/read plumbing both modes share; each mode adds its own
# repository shape on top.
_SEED_DEPS = ["//weld:runtime", "//weld:workspace", "//weld/strategies"]
_MODE_B_SUITE = ["_seed_fixture.py", "_mode_b_fixture.py"]
_MODE_A_SUITE = ["_seed_fixture.py", "_mode_a_fixture.py"]
_REQUEST_ROOT_SUITE = _MODE_B_SUITE + ["_request_root_fixture.py"]

def root_resolution_tests():
    # Git plumbing: common-dir repository identity, the toplevel used as
    # the walk ceiling, the tracked-graph probe, worktree enumeration.
    # Real `git worktree add` and bare-clone layouts -- mocking git here
    # would only test the mock, and the claim under test is precisely
    # that pure plumbing behaves across layouts no path pattern covers.
    py_test(
        name = "weld_git_worktree_test",
        srcs = ["weld_git_worktree_test.py"],
        deps = ["//weld:git"],
    )

    # Resolver semantics: precedence, and the ceiling that stops an
    # upward .weld/ walk at the worktree boundary so a nested worktree
    # can never answer from the outer checkout's graph (= wrong branch).
    # Also the server-side same-repo bound and its non-oracle rejection.
    py_test(
        name = "weld_root_resolver_test",
        srcs = ["weld_root_resolver_test.py"],
        deps = ["//weld:runtime"],
    )

    # Wiring, black-box through weld.cli.main: query/brief/trace/impact/
    # diff each own a parser, so a missing resolve call in any one of
    # them is a silent wrong-root read there.
    py_test(
        name = "weld_cli_root_resolution_test",
        srcs = ["weld_cli_root_resolution_test.py"],
        deps = ["//weld:contract", "//weld:runtime"],
        env = {"PYTHONHASHSEED": "0"},
    )

    # Branch identity (ADR 0096 sec. 3): git_branch stays volatile-only
    # so graph.json is byte-identical across two branches at one commit
    # (ADR 0065), `wd stale` reports live + recorded, and the freshness
    # object carries the LIVE branch.
    py_test(
        name = "weld_freshness_branch_test",
        srcs = ["weld_freshness_branch_test.py"],
        deps = ["//weld:contract", "//weld:runtime", "//weld/strategies"],
        env = {"PYTHONHASHSEED": "0"},
    )

    # A bare `wd discover` must persist .weld/graph.json so query/stats
    # resolve in a fresh checkout or linked worktree (real git-worktree
    # repro, no mocks).
    py_test(
        name = "discover_worktree_canonical_graph_test",
        srcs = ["discover_worktree_canonical_graph_test.py"],
        deps = ["//weld:runtime", "//weld:workspace", "//weld/strategies"],
        env = {"PYTHONHASHSEED": "0"},
    )

    # Mode B bootstrap (ADR 0096 sec. 2, gates 1-4): a tracked graph
    # arrives without its gitignored sidecar, so the first read must
    # synthesize the staleness basis instead of paying a full rediscover.
    # Real `git clone` / `git worktree add` of a real --track-graphs repo;
    # the only mock counts discovery, because "ran zero discovery" is the
    # payoff and no artifact proves that negative directly.
    #
    # Split in two so each file stays a single subject (and under the
    # line cap): end-to-end behaviour here, the gate/copy-rule matrix
    # next to it. `_mode_b_fixture.py` rides in srcs of both, the
    # `_impact_test_helpers` pattern.
    py_test(
        name = "weld_mode_b_sidecar_synthesis_test",
        srcs = _MODE_B_SUITE + ["weld_mode_b_sidecar_synthesis_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    py_test(
        name = "weld_worktree_seed_gates_test",
        srcs = _MODE_B_SUITE + ["weld_worktree_seed_gates_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # bd r7d7: gate 4 also derives the inventory Mode B gitignores, so ADR
    # 0101's coverage probe is not inert in a clone. A third file rather
    # than a third class in either of the two above, because the subject is
    # what the derived record *refuses* to claim -- no content hash, no
    # config, no declined set -- and every one of those is a negative about
    # a file on disk that a behavioural suite can only observe indirectly.
    py_test(
        name = "weld_mode_b_coverage_inventory_test",
        srcs = _MODE_B_SUITE + ["weld_mode_b_coverage_inventory_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # bd nwbn: file-index.json / file-index-state.json meta.git_sha recorded
    # the commit the file is written UNDER, then got committed INTO the next
    # commit -- so the tracked bytes always named their own parent and every
    # single no-change discover in a Mode B repo restamped both files,
    # forever (the residual bd emyk's graph.json skip-rewrite could not
    # reach, because nothing here pins on mtime -- the bytes themselves
    # carried the self-reference). Real commit, real discover, real `git
    # status`: the repro this issue was filed from.
    py_test(
        name = "weld_mode_b_file_index_no_restamp_test",
        srcs = _MODE_B_SUITE + ["weld_mode_b_file_index_no_restamp_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # Mode A copy-seed (ADR 0096 sec. 2, gate 5): a linked worktree has no
    # graph at all, so the first read copies one from a sibling checkout
    # and reconciles it to this branch. Split by subject -- the promise,
    # where the seed comes from, what happens under concurrency, and how a
    # basis the branch outran settles -- so each file stays one story and
    # a failure names which claim broke.
    py_test(
        name = "weld_worktree_seed_test",
        srcs = _MODE_A_SUITE + ["weld_worktree_seed_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    py_test(
        name = "weld_seed_source_resolution_test",
        srcs = _MODE_A_SUITE + ["weld_seed_source_resolution_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # bd 6osw: the suites above pin that `wd query` seeds; this one pins
    # WHICH surfaces do. ADR 0096 sec. 2 put seeding at "the single funnel
    # all graph-backed read CLIs pass through", but wired it into
    # ensure_graph_exists, whose membership answers a different question --
    # who gets first-run guidance. So `wd stale` reported "no graph" and
    # `wd find` reported "no matches" about a file on disk, in a worktree
    # where `wd query` seeded and answered correctly. Separate file because
    # the subject is funnel membership, not the seed's own behaviour.
    py_test(
        name = "weld_seed_read_surface_test",
        srcs = _MODE_A_SUITE + ["weld_seed_read_surface_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # bd yw4b: ADR 0101's coverage reasoning applied to the file index,
    # which cannot borrow the graph's answer -- the index surface is every
    # repo-visible file the allow-list accepts, the graph's scope is
    # discover.yaml's globs, and the first is the broader set. Every file in
    # the gap left `wd find` answering "no matches" about it permanently.
    py_test(
        name = "weld_file_index_coverage_test",
        srcs = _MODE_A_SUITE + ["weld_file_index_coverage_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    py_test(
        name = "weld_worktree_seed_race_test",
        srcs = _MODE_A_SUITE + ["weld_worktree_seed_race_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # bd nwyq: the seed proves its copy came from one generation of the source,
    # but not that the generation was coherent. A source whose inventory sits
    # ahead of its own graph must not hand a worktree a basis it cannot honour.
    py_test(
        name = "weld_worktree_seed_unproven_source_test",
        srcs = _MODE_A_SUITE + ["weld_worktree_seed_unproven_source_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # bd wq9i: the same divergence from the other end. Every checkout here is
    # coherent; the copy manufactures the mismatch, because it keeps a state
    # file already present at the destination while landing the source's graph
    # beside it. A worktree that lost only its graph.json therefore holds a
    # proven inventory for a body nobody has any more.
    py_test(
        name = "weld_worktree_seed_replaced_body_test",
        srcs = _MODE_A_SUITE + ["weld_worktree_seed_replaced_body_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    py_test(
        name = "weld_staleness_divergent_branch_test",
        srcs = _MODE_A_SUITE + ["weld_staleness_divergent_branch_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # The optional MCP consumer (ADR 0096 sec. 4): a per-request root that
    # re-exposes `wd --root` over a long-lived server, bounded to checkouts
    # of the server's own repository. Rides the Mode B fixture because the
    # claims -- which checkout answered, and that dispatch seeds it the way
    # the CLI choke point does -- both need real sibling checkouts.
    #
    # Split the same way as the seed suites: what an accepted root does,
    # then what a request root may not do (an untrusted-input bound, worth
    # failing under its own name). `_request_root_fixture.py` carries the
    # checkouts both need.
    py_test(
        name = "weld_mcp_request_root_test",
        srcs = _REQUEST_ROOT_SUITE + ["weld_mcp_request_root_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    py_test(
        name = "weld_mcp_root_bound_test",
        srcs = _REQUEST_ROOT_SUITE + ["weld_mcp_root_bound_test.py"],
        deps = _SEED_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )
