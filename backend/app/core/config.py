"""Application settings loaded from environment via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
