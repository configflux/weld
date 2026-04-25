"""Auth service package marker.

Marks ``services/auth/src`` as a regular package so the relative import in
``app.py`` (``from .routers.tokens import ...``) is unambiguous when the
service is launched via ``uvicorn services.auth.src.app:app``.
"""
