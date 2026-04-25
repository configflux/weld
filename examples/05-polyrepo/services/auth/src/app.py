"""Auth service: FastAPI app wiring. Mounts the routers under ``src/routers/``.

The token router (``src/routers/tokens.py``) declares ``POST /tokens``,
which is the cross-repo match target for the sibling ``services-api``
child's outbound HTTP call.

Run locally from the polyrepo root::

    uvicorn services.auth.src.app:app --port 8001
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

from fastapi import FastAPI  # noqa: E402  (import after sys.path bootstrap)

from .routers.tokens import router as tokens_router  # noqa: E402

app = FastAPI(title="auth")
app.include_router(tokens_router)
