from __future__ import annotations

import enum
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel
from pydantic_ai import ModelRetry
from pydantic_ai.agent import Agent
from pydantic_ai.messages import (
    AgentStreamEvent,
    FinalResultEvent,
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    SystemPromptPart,
    ToolCallPart,
    ToolCallPartDelta,
    UserPromptPart,
)
from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import Usage

from delamain.agents.executor import Executor
from delamain.agents.tools import get_internal_tools
from delamain.config import get_config
from delamain.log import logger

_HERE = Path(__file__).parent
env = Environment(loader=FileSystemLoader(_HERE / "prompts"), autoescape=True)

DEFAULT_PLANNER_SYSTEM_PROMPT_TEMPLATE = "planner_system_prompt.md"
DEFAULT_VALIDATOR_SYSTEM_PROMPT_TEMPLATE = "validator_system_prompt.md"
DEFAULT_SUMMARIZER_SYSTEM_PROMPT_TEMPLATE = "summarizer_system_prompt.md"
DEFAULT_EXECUTOR_SYSTEM_PROMPT_TEMPLATE = "executor_system_prompt.md"


class UnableToProcess(TypeError):
    pass


def render_template(template_file_name: str, **kwargs):
    template = env.get_template(template_file_name)
    return template.render(**kwargs)


class AgentResponse(BaseModel):
    prompt_for_next_state: str
    next_state: State


class State(str, enum.Enum):
    plan = "plan"
    validate = "validate"
    summarize = "summarize"
    execute = "execute"
    exit = "exit"

    def transfer(self, to_state: State | str) -> State:
        to_state = State(to_state)
        if to_state.value not in self._allow[self]:
            raise ValueError(f"Invalid transition from {self} to {to_state}, allowed: {self._allow[self]}")
        return to_state

    @property
    def _allow(self):
        return {
            State.plan: [State.validate, State.execute, State.exit],
            State.validate: [State.summarize, State.execute, State.exit],
            State.execute: [],
            State.summarize: [State.exit],
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
        self.executor_tools = executor_tools or []

        self.planner = Agent(
            palnner_model,
            model_settings=planner_model_settings,
            system_prompt=planner_system_prompt
            or render_template(
                DEFAULT_PLANNER_SYSTEM_PROMPT_TEMPLATE,
                **{"executor_tools": self.executor_tools},
            ),
            tools=self._get_planner_tools(),
            result_type=AgentResponse | str,
            result_tool_name="transfer_state",
            result_tool_description="Transfer to the next state",
        )
        self.validator = Agent(
            validator_model,
            model_settings=validator_model_settings,
            system_prompt=validator_system_prompt
            or render_template(
                DEFAULT_VALIDATOR_SYSTEM_PROMPT_TEMPLATE,
                **{"executor_tools": self.executor_tools},
            ),
            tools=self._get_validator_tools(),
            result_type=AgentResponse | str,
            result_tool_name="transfer_state",
            result_tool_description="Transfer to the next state",
        )
        self.summarizer = Agent(
            summarizer_model,
            model_settings=summarizer_model_settings,
            system_prompt=summarizer_system_prompt
            or render_template(
                DEFAULT_SUMMARIZER_SYSTEM_PROMPT_TEMPLATE,
            ),
            result_type=AgentResponse | str,
            result_tool_name="transfer_state",
            result_tool_description="Transfer to the next state",
            result_retries=3,
            end_strategy="exhaustive",
        )

        self.executor = Executor(
            executor_model,
            system_prompt=executor_system_prompt or render_template(DEFAULT_EXECUTOR_SYSTEM_PROMPT_TEMPLATE),
            model_settings=executor_model_settings,
            tools=executor_tools or [],
        )

        self.state: State = State.plan
        self.usage = Usage()
        self.next_state_prompt = ""
        self.state_to_agent = {
            State.plan: self.planner,
            State.validate: self.validator,
            State.summarize: self.summarizer,
        }

        @self.planner.result_validator
        async def _(response: AgentResponse | str) -> AgentResponse:
            if isinstance(response, str):
                response = AgentResponse(
                    prompt_for_next_state="",
                    next_state=State.exit,
                )

            try:
                self.state = self.state.transfer(response.next_state)
                logger.debug(f"Transfered to {self.state}")
                self.next_state_prompt = response.prompt_for_next_state
            except ValueError as e:
                raise ModelRetry(str(e)) from e
            return response

        @self.validator.result_validator
        async def _(response: AgentResponse | str) -> AgentResponse:
            if isinstance(response, str):
                response = AgentResponse(
                    prompt_for_next_state="",
                    next_state=State.exit,
                )

            try:
                self.state = self.state.transfer(response.next_state)
                logger.debug(f"Transfered to {self.state}")
                self.next_state_prompt = response.prompt_for_next_state
            except ValueError as e:
                raise ModelRetry(str(e)) from e
            return response

        @self.summarizer.result_validator
        async def _(response: AgentResponse | str) -> AgentResponse:
            if isinstance(response, str):
                response = AgentResponse(
                    prompt_for_next_state="",
                    next_state=State.exit,
                )

            try:
                self.state = self.state.transfer(response.next_state)
                logger.debug(f"Transfered to {self.state}")
                self.next_state_prompt = response.prompt_for_next_state
            except ValueError as e:
                raise ModelRetry(str(e)) from e
            return response

    def _get_planner_tools(self):
        return [
            *get_internal_tools(),
        ]

    def _get_validator_tools(self):
        return [
            *get_internal_tools(),
        ]

    def __repr__(self):
        return f"DelamainAgent(planner={self.planner.model.model_name}, validator={self.validator.model.model_name}, summarizer={self.summarizer.model.model_name}, executor={self.executor.model.model_name})"

    def prepare_messages(self, agent: Agent, custom_user_prompt: str | None = None) -> tuple[str, list[ModelMessage]]:
        copied_messages = deepcopy(self.messages)
        if not isinstance(copied_messages[-1], ModelRequest):
            raise UnableToProcess(f"Last message is not a request: {copied_messages[-1]}")

        user_prompt = ""
        for p in copied_messages[-1].parts:
            if isinstance(p, UserPromptPart):
                user_prompt = p.content
        # Drop user prompt of message history
        copied_messages[-1].parts = [p for p in copied_messages[-1].parts if not isinstance(p, UserPromptPart)]

        if not user_prompt:
            user_prompt = custom_user_prompt

        # Change first message's system prompt
        for part in copied_messages[0].parts:
            if isinstance(part, SystemPromptPart):
                part.content, *_ = agent._system_prompts

        return user_prompt, copied_messages

    def patch_index(self, event: AgentStreamEvent, last_index: int) -> AgentStreamEvent:
        copied_event = deepcopy(event)
        copied_event.index += last_index
        return copied_event

    async def run(self) -> AsyncIterator[AgentStreamEvent]:  # noqa: C901
        last_index = 0
        while True:
            if self.state == State.exit:
                return

            if self.state == State.execute:
                async for event in self.executor.run(self.next_state_prompt, self.messages):
                    event = self.patch_index(event, last_index)
                    yield event
                last_index = event.index + 1
                self.usage += self.executor.usage()
                return

            assert self.state in self.state_to_agent  # noqa: S101
            agent = self.state_to_agent[self.state]

            if not isinstance(self.messages[-1], ModelRequest):
                return

            user_prompt, message_history = self.prepare_messages(agent, self.next_state_prompt)
            logger.debug(f"Context: {user_prompt} {message_history}")
            async with agent.iter(user_prompt, message_history=message_history) as run:
                async for node in run:
                    if Agent.is_user_prompt_node(node) or Agent.is_end_node(node):
                        continue

                    if Agent.is_call_tools_node(node):
                        continue

                    if Agent.is_model_request_node(node):
                        async with node.stream(run.ctx) as request_stream:
                            last_yield_index = None
                            async for event in request_stream:
                                if (
                                    (isinstance(event, PartStartEvent) and isinstance(event.part, ToolCallPart))
                                    or (
                                        isinstance(event, PartDeltaEvent) and isinstance(event.delta, ToolCallPartDelta)
                                    )
                                    or isinstance(event, FinalResultEvent)
                                ):
                                    logger.debug(f"Skipping event: {event}")
                                    continue
                                event = self.patch_index(event, last_index)
                                yield event
                                last_yield_index = event.index
                            last_index = last_yield_index + 1 if last_yield_index is not None else last_index
                            logger.debug(f"Last index: {last_index}")

            self.messages = run.result.all_messages()
            self.usage += run.result.usage()
            logger.debug(f"Messages: {self.messages}")
