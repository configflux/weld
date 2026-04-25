"""Token issuance router.

Declares ``POST /tokens`` -- the cross-repo match target for the sibling
``services-api`` child's outbound ``httpx.post`` call.
"""

from __future__ import annotations

from fastapi import APIRouter

from shared_models.models import Token, TokenRequest

router = APIRouter()


@router.post("/tokens", response_model=Token)
def create_token(req: TokenRequest) -> Token:
    """Return a fresh token for the given subject."""
    return Token(subject=req.subject, value=f"tok-{req.subject}")
