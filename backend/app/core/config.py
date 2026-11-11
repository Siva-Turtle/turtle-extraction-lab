"""Application settings loaded from environment via pydantic-settings."""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


def _backend_dir():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _env_file():
    """Server secrets first (MEM_SECRETS_DIR, default
    ~/.config/mem/secrets/turtle-extraction-lab/backend/.env on Linux),
    then the local backend/.env (laptop flow)."""
    d = os.environ.get("MEM_SECRETS_DIR")
    if d:
        return os.path.join(d, "backend", ".env")
    default = os.path.join(
        os.path.expanduser("~"), ".config", "mem", "secrets",
        "turtle-extraction-lab", "backend", ".env",
    )
    if os.path.exists(default):
        return default
    return os.path.join(_backend_dir(), ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_env_file(), extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/turtle_agent_lab"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    cors_origins: str = "http://localhost:5175"
    mongodb_uri: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        v = (self.cors_origins or "").strip()
        if not v:
            return []
        if v.startswith("["):
            import json

            return json.loads(v)
        return [o.strip() for o in v.split(",") if o.strip()]


settings = Settings()
