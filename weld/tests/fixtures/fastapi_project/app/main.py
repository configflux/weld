"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.v1 import router as api_v1_router

app = FastAPI(
    title="My FastAPI App",
    description="A realistic FastAPI project fixture",
    version="0.1.0",
)

app.include_router(api_v1_router, prefix="/api/v1")

@app.get("/health")
def health_check():
    return {"status": "ok"}
