from __future__ import annotations

import enum
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from jinja2 import Environment, FileSystemLoader
from pydantic_ai import ModelRetry
from pydantic_ai.agent import Agent
from pydantic_ai.messages import AgentStreamEvent, ModelMessage
from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import Tool, ToolDefinition

from delamain.agents.executor import Executor
from delamain.agents.tools import get_internal_tools
from delamain.config import get_config

_HERE = Path(__file__).parent
env = Environment(loader=FileSystemLoader(_HERE / "prompts"), autoescape=True)

DEFAULT_PLANNER_SYSTEM_PROMPT_TEMPLATE = "planner_system_prompt.md"
DEFAULT_VALIDATOR_SYSTEM_PROMPT_TEMPLATE = "validator_system_prompt.md"
DEFAULT_SUMMARIZER_SYSTEM_PROMPT_TEMPLATE = "summarizer_system_prompt.md"
DEFAULT_EXECUTOR_SYSTEM_PROMPT_TEMPLATE = "executor_system_prompt.md"


def render_template(template_file_name: str, **kwargs):
    template = env.get_template(template_file_name)
    return template.render(**kwargs)


class State(str, enum.Enum):
    plan = "plan"
    validate = "validate"
    summarize = "summarize"
    execute = "execute"
    exit = "exit"

    def transfer(self, to_state: State) -> State:
        if to_state.value not in self._allow[self]:
            raise ValueError(f"Invalid transition from {self} to {to_state}, allowed: {self._allow[self]}")
        return to_state

    @property
    def _allow(self):
        return {
            State.plan: [State.validate, State.execute, State.exit],
            State.validate: [State.summarize, State.execute, State.exit],
            State.execute: [],
            State.summarize: [],
            State.exit: [],
        }


class DelamainAgent:
    @classmethod
    def from_config(
        cls,
        messages: list[ModelMessage],
        executor_tools: list[ToolDefinition] | None = None,
    ):
        config = get_config()
        return cls(
            messages,
            executor_tools=executor_tools,
            palnner_model=config.planner_model,
            validator_model=config.validator_model,
            summarizer_model=config.summarizer_model,
            executor_model=config.executor_model,
            planner_system_prompt=config.planner_system_prompt,
            validator_system_prompt=config.validator_system_prompt,
            summarizer_system_prompt=config.summarizer_system_prompt,
            executor_system_prompt=config.executor_system_prompt,
            planner_model_settings=config.planner_model_settings,
            validator_model_settings=config.validator_model_settings,
            summarizer_model_settings=config.summarizer_model_settings,
            executor_model_settings=config.executor_model_settings,
        )

    def __init__(
        self,
        messages: list[ModelMessage],
        executor_tools: list[ToolDefinition] | None = None,
        palnner_model: Model | KnownModelName | None = None,
        validator_model: Model | KnownModelName | None = None,
        summarizer_model: Model | KnownModelName | None = None,
        executor_model: Model | KnownModelName | None = None,
        *,
        planner_system_prompt: str | None = None,
        validator_system_prompt: str | None = None,
        summarizer_system_prompt: str | None = None,
        executor_system_prompt: str | None = None,
        planner_model_settings: ModelSettings | None = None,
        validator_model_settings: ModelSettings | None = None,
        summarizer_model_settings: ModelSettings | None = None,
        executor_model_settings: ModelSettings | None = None,
    ):
        self.messages = messages

        self.planner = Agent(
            palnner_model,
            model_settings=planner_model_settings,
            system_prompt=planner_system_prompt or render_template(DEFAULT_PLANNER_SYSTEM_PROMPT_TEMPLATE),
            tools=self._get_planner_tools(),
        )
        self.validator = Agent(
            validator_model,
            model_settings=validator_model_settings,
            system_prompt=validator_system_prompt or render_template(DEFAULT_VALIDATOR_SYSTEM_PROMPT_TEMPLATE),
            tools=self._get_validator_tools(),
        )
        self.summarizer = Agent(
            summarizer_model,
            model_settings=summarizer_model_settings,
            system_prompt=summarizer_system_prompt or render_template(DEFAULT_SUMMARIZER_SYSTEM_PROMPT_TEMPLATE),
            result_type=str,
        )

        self.executor = Executor(
            executor_model,
            system_prompt=executor_system_prompt or render_template(DEFAULT_EXECUTOR_SYSTEM_PROMPT_TEMPLATE),
            model_settings=executor_model_settings,
            tools=executor_tools or [],
        )

        self.state: State = State.plan
        self.state_to_agent = {
            State.plan: self.planner,
            State.validate: self.validator,
            State.summarize: self.summarizer,
        }

    def _state_transfer_tool(self) -> Tool:
        def _(to_state: Annotated[State, "The state to transfer to"]):
            try:
                self.state = self.state.transfer(to_state)
            except ValueError as e:
                raise ModelRetry(str(e)) from e

        return Tool(
            _,
            name="transfer_state",
            description="Transfer current state to another state",
            max_retries=3,
        )

    def _get_planner_tools(self):
        return [
            self._state_transfer_tool(),
            *get_internal_tools(),
        ]

    def _get_validator_tools(self):
        return [
            self._state_transfer_tool(),
            *get_internal_tools(),
        ]

    def __repr__(self):
        return f"DelamainAgent(planner={self.planner.model.model_name}, validator={self.validator.model.model_name}, summarizer={self.summarizer.model.model_name}, executor={self.executor.model.model_name})"

    async def run(self) -> AsyncIterator[AgentStreamEvent]:
        yield "hello"


if __name__ == "__main__":
    import asyncio

    from delamain.config import get_config

    async def main():
        delamain = DelamainAgent.from_config([])
        async for event in delamain.run():
            print(event)

    asyncio.run(main())
