"""Seeded generator for small multi-glob Python repos (ADR 0139 mechanism 5).

The ``incremental_*_equivalence_test`` family grows one post-mortem at a time:
every member is a fixture somebody hand-wrote *after* a divergence escaped, so
the suite enumerates the shapes we already know about and is silent on the ones
we do not. ADR 0139 mechanism 5 is the answer -- equivalence gets a generator,
and its findings get pinned back into the enumerative family per ADR 0113. This
module is the generator half; :mod:`weld.tests._equivalence_sweep` runs a case
through discovery and diffs the two graphs.

Three dimensions, all drawn from one ``random.Random(seed)``:

* **import shape** -- the seven ways a first-party call/inherit/decorate target
  gets named. Each one is a resolution path with its own history in this family
  (``from_submodule`` is bd ``yhz70``'s shape, ``class_attr`` is bd ``vrdcj``'s,
  ``reexport`` is the ``weld._graph_closure_reexport`` retarget, ``decorates``
  is bd ``q4t3d``'s).
* **glob split** -- two or three declared globs, packages distributed over them,
  and the declaration order itself shuffled. A caller and its target landing in
  different globs is the only way to reach the merged-view derivations, and the
  order matters because ``python_callgraph`` publishes a run-level module union
  as it goes.
* **edit/delete round** -- what changes between the seeding discover and the
  incremental one. Deletion is where provenance purge, the endpoint-membership
  floor, and the closure passes' undos all have to agree with a full discover.

Everything here is stdlib. ``hypothesis`` was refused deliberately: it would be
a new third-party dependency on a test tree whose deps are the runtime, the
strategies, and pinned grammars. The cost is that shrinking is manual -- a
divergence is reproduced from its seed and narrowed by hand -- which ADR 0113's
pinning loop wants anyway, since the artifact of a finding is a pinned case in
the enumerative family and not a generator run.

Generation order is explicit at every draw. ``.bazelrc``'s ``PYTHONHASHSEED=0``
is *not* treated as a substitute: it makes set and dict iteration reproducible
within one interpreter's rules, and says nothing about the order this module
asks the RNG for values in. Anything derived from a set here is sorted before
it reaches a draw.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

#: Glob roots, in the order they are populated. A case uses a prefix of this.
ROOT_NAMES = ("alpha", "beta", "gamma")

#: The nested subpackage name, used to put two packages inside one glob.
SUB_NAME = "sub"

#: How a caller names its target. Each is a distinct resolution path through
#: ``python_callgraph`` plus the ``close_graph`` passes that run after it.
IMPORT_SHAPES = (
    "from_symbol",       # from <pkg>.core import fn    ; fn()
    "from_submodule",    # from <pkg> import core       ; core.fn()
    "dotted_module",     # import <pkg>.core            ; <pkg>.core.fn()
    "class_attr",        # from <pkg>.core import Thing ; Thing.build()
    "inherits",          # from <pkg>.core import Thing ; class Sub(Thing)
    "decorates",         # from <pkg>.core import mark  ; @mark
    "reexport",          # from <pkg> import fn         ; fn()   (facade)
    "stdlib_decorates",  # from dataclasses import dataclass ; @dataclass
)

#: What changes between the seeding discover and the incremental one.
ROUNDS = (
    "edit_caller",
    "edit_definer",
    "delete_definer",
    "delete_caller",
    "delete_package",
)

_EDIT_MARK = "\n# edited\n"


@dataclass(frozen=True)
class Package:
    """One generated package: a directory with ``__init__``/``core``/``use``."""

    root: str
    parts: tuple[str, ...]

    @property
    def dotted(self) -> str:
        return ".".join(self.parts)

    @property
    def dir(self) -> str:
        return "/".join(self.parts)

    @property
    def token(self) -> str:
        """Symbol-name suffix, unique per package and a valid identifier."""
        return "_".join(self.parts)

    def path(self, name: str) -> str:
        return f"{self.dir}/{name}.py"


@dataclass(frozen=True)
class Case:
    """One generated repo plus the round applied to it, keyed by its seed."""

    seed: int
    roots: tuple[str, ...]
    declared: tuple[str, ...]
    packages: tuple[Package, ...]
    links: tuple[tuple[int, int, str], ...]
    round: str
    target: str

    def summary(self) -> str:
        """One stable line naming every drawn dimension of this case."""
        links = " ".join(
            f"{self.packages[caller].dotted}->{self.packages[target].dotted}:{shape}"
            for caller, target, shape in self.links
        )
        return (
            f"globs={','.join(self.declared)} "
            f"packages={','.join(p.dotted for p in self.packages)} "
            f"links=[{links}] round={self.round} target={self.target}"
        )


def generate_case(seed: int) -> Case:
    """Draw one case. Same seed in, identical case out, on any interpreter."""
    rng = random.Random(seed)
    roots = ROOT_NAMES[: rng.randint(2, 3)]

    packages: list[Package] = []
    for root in roots:
        packages.append(Package(root, (root,)))
        if rng.random() < 0.5:
            packages.append(Package(root, (root, SUB_NAME)))

    links: list[tuple[int, int, str]] = []
    for caller in range(len(packages)):
        links.append(
            (caller, rng.randrange(len(packages)), rng.choice(IMPORT_SHAPES))
        )

    declared = list(roots)
    rng.shuffle(declared)

    round_ = rng.choice(ROUNDS)
    target = _draw_target(rng, packages, links, round_)
    return Case(
        seed=seed,
        roots=tuple(roots),
        declared=tuple(declared),
        packages=tuple(packages),
        links=tuple(links),
        round=round_,
        target=target,
    )


def _draw_target(
    rng: random.Random,
    packages: list[Package],
    links: list[tuple[int, int, str]],
    round_: str,
) -> str:
    """The path (or package dir) the round applies to, drawn from *rng*."""
    if round_ in ("edit_caller", "delete_caller"):
        return packages[rng.choice(links)[0]].path("use")
    if round_ in ("edit_definer", "delete_definer"):
        return packages[rng.choice(links)[1]].path("core")
    if round_ == "delete_package":
        # Only a leaf, so the round is exactly "this directory stopped
        # existing" and never "a parent took its children with it".
        leaves = [p for p in packages if _is_leaf(p, packages)]
        return rng.choice(leaves).dir
    raise ValueError(f"unknown round: {round_}")


def _is_leaf(package: Package, packages: list[Package]) -> bool:
    prefix = package.parts
    return not any(
        other.parts[: len(prefix)] == prefix and len(other.parts) > len(prefix)
        for other in packages
    )


def sources_yaml(case: Case) -> str:
    """The ``.weld/discover.yaml`` body: the canonical Python trio per glob."""
    lines = ["sources:"]
    for root in case.declared:
        glob = f'"{root}/**/*.py"'
        for node_type, strategy in (
            ("file", "python_module"),
            ("symbol", "python_callgraph"),
            ("package", "python_package"),
        ):
            lines.append(f"  - glob: {glob}")
            lines.append(f"    type: {node_type}")
            lines.append(f"    strategy: {strategy}")
    return "\n".join(lines) + "\n"


def _core_body(package: Package) -> str:
    token = package.token
    return (
        f"def fn_{token}():\n"
        f"    return {len(token)}\n"
        "\n"
        "\n"
        f"class Thing_{token}:\n"
        "    @classmethod\n"
        "    def build(cls):\n"
        "        return cls()\n"
        "\n"
        "\n"
        f"def mark_{token}(func):\n"
        "    return func\n"
    )


def _use_body(caller: Package, target: Package, shape: str) -> str:
    token = target.token
    runner = f"run_{caller.token}"
    if shape == "from_symbol":
        return (
            f"from {target.dotted}.core import fn_{token}\n\n\n"
            f"def {runner}():\n    return fn_{token}()\n"
        )
    if shape == "from_submodule":
        return (
            f"from {target.dotted} import core\n\n\n"
            f"def {runner}():\n    return core.fn_{token}()\n"
        )
    if shape == "dotted_module":
        return (
            f"import {target.dotted}.core\n\n\n"
            f"def {runner}():\n    return {target.dotted}.core.fn_{token}()\n"
        )
    if shape == "class_attr":
        return (
            f"from {target.dotted}.core import Thing_{token}\n\n\n"
            f"def {runner}():\n    return Thing_{token}.build()\n"
        )
    if shape == "inherits":
        return (
            f"from {target.dotted}.core import Thing_{token}\n\n\n"
            f"class Sub_{caller.token}(Thing_{token}):\n    pass\n"
        )
    if shape == "decorates":
        return (
            f"from {target.dotted}.core import mark_{token}\n\n\n"
            f"@mark_{token}\ndef {runner}():\n    return 1\n"
        )
    if shape == "reexport":
        return (
            f"from {target.dotted} import fn_{token}\n\n\n"
            f"def {runner}():\n    return fn_{token}()\n"
        )
    if shape == "stdlib_decorates":
        # The one shape whose decorator is NOT first-party. It is here because
        # it materialises the node class bd ``q4t3d`` reports: a placeholder
        # anchored only by OUTBOUND ``decorates`` edges, which both zero-inbound
        # purge rules read as dead. The first-party link rides along so the
        # cross-glob call is still drawn and the case is not spent on the
        # decorator alone.
        return (
            "from dataclasses import dataclass\n"
            f"from {target.dotted}.core import fn_{token}\n\n\n"
            f"@dataclass\nclass Rec_{caller.token}:\n    value: int\n\n\n"
            f"def {runner}():\n    return fn_{token}()\n"
        )
    raise ValueError(f"unknown import shape: {shape}")


def _init_body(package: Package, case: Case) -> str:
    """A facade only where some link actually imports through it."""
    reexports = any(
        shape == "reexport" and case.packages[target] == package
        for _, target, shape in case.links
    )
    return f"from .core import fn_{package.token}\n" if reexports else ""


def files_before(case: Case) -> dict[str, str]:
    """The intact tree: what the seeding full discover walks."""
    files: dict[str, str] = {}
    for package in case.packages:
        files[package.path("__init__")] = _init_body(package, case)
        files[package.path("core")] = _core_body(package)
    for caller, target, shape in case.links:
        files[case.packages[caller].path("use")] = _use_body(
            case.packages[caller], case.packages[target], shape
        )
    return files


def files_after(case: Case) -> dict[str, str]:
    """The mutated tree: what both the full and the incremental round walk."""
    files = dict(files_before(case))
    if case.round in ("edit_caller", "edit_definer"):
        files[case.target] = files[case.target] + _EDIT_MARK
    elif case.round in ("delete_caller", "delete_definer"):
        files.pop(case.target, None)
    elif case.round == "delete_package":
        prefix = f"{case.target}/"
        for path in [p for p in files if p.startswith(prefix)]:
            files.pop(path)
    else:  # pragma: no cover -- ROUNDS and this dispatch are one list
        raise ValueError(f"unknown round: {case.round}")
    return files
