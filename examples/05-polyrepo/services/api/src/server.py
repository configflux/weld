"""API service: serves users, calls auth for token issuance.

The outbound ``httpx.post`` call below is the cross-repo match source.
It targets ``http://services-auth:8080/tokens`` so the ``service_graph``
resolver links this call site to the ``POST /tokens`` route exposed by
the sibling ``services-auth`` child.

Run locally from the polyrepo root::

    uvicorn services.api.src.server:app --port 8000
"""

from __future__ import annotations

import sys
from pathlib import Path

# Demo-only: make the sibling ``libs/shared-models/src`` importable so
# ``shared_models.models`` resolves when running the service via uvicorn
# from the polyrepo root. A real polyrepo would publish ``shared-models``
# as an installable package; this example keeps it path-based to stay
# self-contained.
_SHARED_MODELS_SRC = (
    Path(__file__).resolve().parents[3] / "libs" / "shared-models" / "src"
)
if _SHARED_MODELS_SRC.is_dir():
    shared_str = str(_SHARED_MODELS_SRC)
    if shared_str not in sys.path:
        sys.path.insert(0, shared_str)

import httpx  # noqa: E402  (import after sys.path bootstrap)
from fastapi import FastAPI  # noqa: E402

from shared_models.models import Token, TokenRequest  # noqa: E402

app = FastAPI(title="api")


@app.post("/login")
def login(username: str) -> dict:
    """Exchange a username for a token by calling the auth service."""
    req = TokenRequest(subject=username)
    # Cross-repo call: host ``services-auth`` names a sibling child,
    # so ``service_graph`` emits a ``cross_repo:calls`` edge here.
    response = httpx.post(
        "http://services-auth:8080/tokens",
        json=req.model_dump(),
        timeout=5.0,
    )
    response.raise_for_status()
    return Token(**response.json()).model_dump()
