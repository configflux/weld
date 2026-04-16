"""Application configuration."""

from pydantic import BaseModel

class Settings(BaseModel):
    app_name: str = "My FastAPI App"
    database_url: str = "postgresql://localhost/myapp"
    debug: bool = False
    secret_key: str = "changeme"

settings = Settings()
