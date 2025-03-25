from __future__ import annotations

from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.settings import ModelSettings
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_config():
    return Config()


class Config(BaseSettings):
    api_key: str | None = None
    planner_model: Model | KnownModelName
    validator_model: Model | KnownModelName
    summarizer_model: Model | KnownModelName
    executor_model: Model | KnownModelName

    planner_system_prompt: str | None = None
    validator_system_prompt: str | None = None
    summarizer_system_prompt: str | None = None
    executor_system_prompt: str | None = None

    planner_model_settings: ModelSettings | None = None
    validator_model_settings: ModelSettings | None = None
    summarizer_model_settings: ModelSettings | None = None
    executor_model_settings: ModelSettings | None = None

    model_config = SettingsConfigDict(case_sensitive=False, frozen=True, env_file=".env")
