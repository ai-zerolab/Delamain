from __future__ import annotations

from typing import Literal

from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.settings import ModelSettings
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_config():
    return Config()


class Config(BaseSettings):
    api_key: str | None = None
    mode: Literal["re-act", "mas"] = "re-act"
    planner_model: Model | KnownModelName | None = None
    validator_model: Model | KnownModelName | None = None
    summarizer_model: Model | KnownModelName | None = None

    reasoning_model: Model | KnownModelName | None = None
    executor_model: Model | KnownModelName | None = None

    manage_system_prompt: str | None = None
    planner_system_prompt: str | None = None
    validator_system_prompt: str | None = None
    summarizer_system_prompt: str | None = None

    reasoning_system_prompt: str | None = None
    executor_system_prompt: str | None = None

    planner_model_settings: ModelSettings | None = None
    validator_model_settings: ModelSettings | None = None
    summarizer_model_settings: ModelSettings | None = None

    reasoning_model_settings: ModelSettings | None = None
    executor_model_settings: ModelSettings | None = None

    custom_instructions: str | None = None
    model_config = SettingsConfigDict(case_sensitive=False, frozen=True, env_file=".env")
