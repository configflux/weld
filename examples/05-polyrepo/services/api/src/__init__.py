"""API service package marker.

Marks ``services/api/src`` as a regular package so ``server`` can be
imported as ``services.api.src.server`` when the service is launched via
``uvicorn services.api.src.server:app``.
"""
