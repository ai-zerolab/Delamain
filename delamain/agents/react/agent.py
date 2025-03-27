from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
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
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import Usage

from delamain.agents.executor import Executor
from delamain.agents.mas.agent import UnableToProcess
from delamain.agents.utils import get_model
from delamain.config import get_config
from delamain.log import logger

_HERE = Path(__file__).parent
env = Environment(loader=FileSystemLoader(_HERE / "prompts"), autoescape=True)

DEFAULT_REASONING_SYSTEM_PROMPT_TEMPLATE = "reasoning_system_prompt.md"
DEFAULT_EXECUTOR_SYSTEM_PROMPT_TEMPLATE = "executor_system_prompt.md"


def render_template(template_file_name: str, **kwargs):
    template = env.get_template(template_file_name)
    return template.render(**kwargs)


class DelamainReAct:
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
            reasoning_model=config.reasoning_model,
            executor_model=config.executor_model,
            reasoning_system_prompt=config.reasoning_system_prompt,
            executor_system_prompt=config.executor_system_prompt,
            reasoning_model_settings=config.reasoning_model_settings,
            executor_model_settings=config.executor_model_settings,
            custom_instructions=config.custom_instructions,
        )

    def __init__(
        self,
        messages: list[ModelMessage],
        executor_tools: list[ToolDefinition] | None = None,
        reasoning_model: Model | KnownModelName | None = None,
        executor_model: Model | KnownModelName | None = None,
        *,
        reasoning_system_prompt: str | None = None,
        executor_system_prompt: str | None = None,
        reasoning_model_settings: ModelSettings | None = None,
        executor_model_settings: ModelSettings | None = None,
        custom_instructions: str | None = None,
    ):
        self.messages = messages
        self.usage = Usage()
        self.executor_tools = executor_tools or []

        self.reasoning_agent = Agent(
            get_model(reasoning_model),
            model_settings=reasoning_model_settings,
            system_prompt=reasoning_system_prompt
            or env.get_template(DEFAULT_REASONING_SYSTEM_PROMPT_TEMPLATE).render(**{
                "executor_tools": self.executor_tools
            }),
        )

        self.executor = Executor(
            get_model(executor_model),
            system_prompt=executor_system_prompt or render_template(DEFAULT_EXECUTOR_SYSTEM_PROMPT_TEMPLATE),
            model_settings=executor_model_settings,
            tools=self.executor_tools,
            tool_call_only=False,
        )

        self.custom_instructions = custom_instructions

    def _is_tool_return_messages(self, messages: list[ModelMessage]) -> bool:
        last_message = messages[-1]
        if not isinstance(last_message, ModelRequest):
            return False

        return any(isinstance(part, ToolReturnPart) for part in last_message.parts)

    def _is_tool_call_messages(self, messages: list[ModelMessage]) -> bool:
        last_message = messages[-1]
        if isinstance(last_message, ModelRequest):
            return False

        return any(isinstance(part, ToolCallPart) for part in last_message.parts)

    def prepare_messages(
        self,
        agent,
    ) -> tuple[str, str, list[ModelMessage]]:
        copied_messages = deepcopy(self.messages)
        if not isinstance(copied_messages[-1], ModelRequest):
            raise UnableToProcess(f"Last message is not a request: {copied_messages[-1]}")

        # Move original system prompt to user prompt's project instructions
        original_system_prompt = ""
        for part in copied_messages[0].parts:
            if isinstance(part, SystemPromptPart):
                original_system_prompt = part.content
                part.content, *_ = agent._system_prompts

        user_prompt = None
        for p in copied_messages[-1].parts:
            if isinstance(p, UserPromptPart):
                original_user_prompt = p.content
                user_prompt = f"<Project Instructions>{self.custom_instructions or original_system_prompt}</Project Instructions>\n<User Prompt>{p.content}</User Prompt>"

        copied_messages[-1].parts = [p for p in copied_messages[-1].parts if not isinstance(p, UserPromptPart)]

        return user_prompt, original_user_prompt, copied_messages

    async def run(self) -> AsyncIterator[AgentStreamEvent]:
        if not self._is_tool_return_messages(self.messages):
            # Init prompt, need reasoning
            user_prompt, original_user_prompt, message_history = self.prepare_messages(self.reasoning_agent)
            async with self.reasoning_agent.iter(user_prompt, message_history=message_history) as run:
                async for node in run:
                    if Agent.is_user_prompt_node(node) or Agent.is_end_node(node) or Agent.is_call_tools_node(node):
                        continue

                    elif Agent.is_model_request_node(node):
                        async with node.stream(run.ctx) as request_stream:
                            async for event in request_stream:
                                if (
                                    not event
                                    or (isinstance(event, PartStartEvent) and isinstance(event.part, ToolCallPart))
                                    or (
                                        isinstance(event, PartDeltaEvent) and isinstance(event.delta, ToolCallPartDelta)
                                    )
                                    or isinstance(event, FinalResultEvent)
                                ):
                                    continue
                                yield event
                    else:
                        logger.warning(f"Unknown node: {node}")
            self.messages = run.result.all_messages()
            self.usage.incr(run.result.usage(), requests=1)
            logger.info(f"Reasoning done, reasoning usage: {self.usage}")
            logger.info("Prepare execution...")
            async for event in self._yield_new_line():
                yield event
            self.messages.append(
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=f"No need to ask questions, use tools to solve problems wherever possible: <User Prompt>{original_user_prompt}</User Prompt>"
                        )
                    ]
                )
            )

        # Now continue execution
        logger.info("Start execution...")
        async for event in self.executor.run(None, self.messages):
            yield event
        self.messages = self.executor.all_messages()
        self.usage.incr(self.executor.usage(), requests=1)
        logger.info(f"Execution done, executor usage: {self.executor.usage()}, total usage: {self.usage}")

    async def _yield_new_line(self) -> AsyncIterator[AgentStreamEvent]:
        async def _(*args, **kwargs) -> AsyncIterator[str]:
            yield "\n\n"

        mock_agent = Agent(FunctionModel(stream_function=_))
        async with mock_agent.iter("") as run:
            async for node in run:
                if Agent.is_user_prompt_node(node) or Agent.is_end_node(node) or Agent.is_call_tools_node(node):
                    continue

                elif Agent.is_model_request_node(node):
                    async with node.stream(run.ctx) as request_stream:
                        async for event in request_stream:
                            if (
                                not event
                                or (isinstance(event, PartStartEvent) and isinstance(event.part, ToolCallPart))
                                or (isinstance(event, PartDeltaEvent) and isinstance(event.delta, ToolCallPartDelta))
                                or isinstance(event, FinalResultEvent)
                            ):
                                continue
                            yield event
                else:
                    logger.warning(f"Unknown node: {node}")
