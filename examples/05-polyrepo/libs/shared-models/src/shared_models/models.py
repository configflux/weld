"""Shared domain models used by both services in the polyrepo demo.

This module is a stand-in for a library that would be published and
consumed by siblings. In a real polyrepo it would be a separate package;
here it lives as a third child so the workspace contains more than two
repos and has a clear non-service counterpart.
"""

from __future__ import annotations

from pydantic import BaseModel


class TokenRequest(BaseModel):
    """Input payload for ``POST /tokens``."""

    subject: str


class Token(BaseModel):
    """A minimal bearer token returned by the auth service."""

    subject: str
    value: str
