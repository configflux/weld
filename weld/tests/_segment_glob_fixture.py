"""The repo ``weld_strategy_segment_glob_e2e_test`` discovers (bd b9xgd).

Split from the probe so each file says one thing: this one is *what the tree
and the config are*, and the probe beside it is *what must be true of the run*.
The probe's assertions are the part a reader comes for, and they were sharing a
file with a hundred lines of fixture bodies.

Every path is named here, and every strategy under test is wired twice -- once
with a wildcard in a directory segment (``api/*/routers/*.py``), once with
``**`` -- because the defect is in the shape of the pattern, not in the
strategy. ``ROUTERS``/``CONTRACTS`` are the segment shape, ``DEEP_*`` the
recursive one, and ``COMPOSE`` carries both plus the literal-directory control
that must keep resolving exactly as it always did.

The two paths that must stay *out* of every result are the point of the
``compose``/``events`` half. ``DECOY`` sits at the repository root, which is
where the ``parent = root`` fallback looked; ``BURIED`` sits one directory
below the literal-directory glob, which is what a fix that over-widened the
flat branch would sweep in.
"""

from __future__ import annotations

__all__ = [
    "BURIED",
    "COMPOSE",
    "CONFIG",
    "CONTRACTS",
    "DECOY",
    "DEEP_CONTRACT",
    "DEEP_ROUTER",
    "PYTHON_INPUTS",
    "ROUTERS",
    "TREE",
    "router",
]


def router(tag: str, path: str) -> str:
    return (
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n\n"
        f'@router.get("{path}")\n'
        f"def {tag}_index():\n"
        f'    return {{"tag": "{tag}"}}\n'
    )


def _app_module() -> str:
    return "from fastapi import FastAPI\n\napp = FastAPI()\n"


def _contract(name: str) -> str:
    return (
        "from pydantic import BaseModel\n\n\n"
        f"class {name}(BaseModel):\n"
        "    identifier: str\n"
    )


def _compose_file(service: str, topic: str) -> str:
    """One compose file whose service name and topic literal are unique.

    Uniqueness is what makes the per-file assertions possible on both sides:
    ``compose`` nodes carry ``props.file``, but ``channel`` nodes do not, so
    the topic literal is the only thing that says which file declared them.
    """
    return (
        "services:\n"
        f"  {service}:\n"
        "    image: alpine:3\n"
        "    environment:\n"
        f"      KAFKA_{service.upper()}_TOPIC: {topic}\n"
    )


#: Router files named by ``api/*/routers/*.py`` -- one wildcard segment.
ROUTERS = ("api/orders/routers/orders.py", "api/users/routers/users.py")
#: Router file named by ``svc/**/routers/*.py`` -- the recursive shape.
DEEP_ROUTER = "svc/edge/inner/routers/edge.py"
#: Contract files, same two shapes.
CONTRACTS = ("api/orders/contracts/order.py", "api/users/contracts/user.py")
DEEP_CONTRACT = "svc/edge/inner/contracts/edge.py"
#: Compose files every configured glob names, across both shapes plus the
#: literal-directory control.
COMPOSE = (
    "deploy/a/docker-compose.alpha.yml",
    "deploy/b/docker-compose.bravo.yml",
    "infra/east/docker-compose.delta.yml",
    "flat/docker-compose.flatly.yml",
)
#: Named by nothing. Today the ``parent = root`` fallback picks it up.
DECOY = "docker-compose.decoy.yml"
#: Named by nothing either: one directory below the literal-directory glob,
#: so a fix that routed every pattern through the recursive walker sees it.
BURIED = "flat/deep/docker-compose.buried.yml"

PYTHON_INPUTS = (*ROUTERS, DEEP_ROUTER, *CONTRACTS, DEEP_CONTRACT)

TREE: dict[str, str] = {
    # One app module per service directory: the FastAPI boundary lookup must
    # find *this* directory's, never a sibling's.
    "api/orders/main.py": _app_module(),
    "api/users/main.py": _app_module(),
    ROUTERS[0]: router("orders", "/orders"),
    ROUTERS[1]: router("users", "/users"),
    DEEP_ROUTER: router("edge", "/edge"),
    CONTRACTS[0]: _contract("OrderContract"),
    CONTRACTS[1]: _contract("UserContract"),
    DEEP_CONTRACT: _contract("EdgeContract"),
    COMPOSE[0]: _compose_file("alpha", "alpha-topic"),
    COMPOSE[1]: _compose_file("bravo", "bravo-topic"),
    COMPOSE[2]: _compose_file("delta", "delta-topic"),
    COMPOSE[3]: _compose_file("flatly", "flatly-topic"),
    DECOY: _compose_file("decoy", "decoy-topic"),
    BURIED: _compose_file("buried", "buried-topic"),
}

CONFIG = """version: 1
sources:
  - glob: "api/*/routers/*.py"
    type: file
    strategy: fastapi
  - glob: "svc/**/routers/*.py"
    type: file
    strategy: fastapi
  - glob: "api/*/contracts/*.py"
    type: file
    strategy: pydantic
  - glob: "svc/**/contracts/*.py"
    type: file
    strategy: pydantic
  - glob: "deploy/*/docker-compose.*.yml"
    type: config
    strategy: compose
  - glob: "infra/**/docker-compose.*.yml"
    type: config
    strategy: compose
  - glob: "flat/docker-compose.*.yml"
    type: config
    strategy: compose
  - glob: "deploy/*/docker-compose.*.yml"
    type: config
    strategy: events
    kind: compose_env
  - glob: "infra/**/docker-compose.*.yml"
    type: config
    strategy: events
    kind: compose_env
  - glob: "flat/docker-compose.*.yml"
    type: config
    strategy: events
    kind: compose_env
  - glob: "api/*/main.py"
    type: file
    strategy: boundary_entrypoint
"""
