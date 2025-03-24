from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


def get_config():
    return Config()


class Config(BaseSettings):
    api_key: str | None = None

    model_config = SettingsConfigDict(case_sensitive=False, frozen=True, env_file=".env")
