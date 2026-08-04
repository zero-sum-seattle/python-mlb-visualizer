"""Application configuration via Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "mlb-stats-visualizer"
    environment: str = "development"
    debug: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
