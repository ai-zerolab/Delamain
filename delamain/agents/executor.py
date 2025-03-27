from collections.abc import AsyncIterator
from copy import deepcopy

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponseStreamEvent,
    SystemPromptPart,
    UserPromptPart,
)
from pydantic_ai.models import (
    KnownModelName,
    Model,
    ModelRequestParameters,
    infer_model,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import Usage


class Executor:
    def __init__(
        self,
        model: Model | KnownModelName | None,
        system_prompt: str | None = None,
        model_settings: ModelSettings | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_call_only: bool = False,
    ):
        self.model = infer_model(model)
        self.system_prompt = system_prompt
        self.model_settings = model_settings
        self.tools = tools or []
        self._usage = Usage()
        self._messages = None
        self._tool_call_only = tool_call_only

    def prepare_messages(self, prompt: str, messages: list[ModelMessage]) -> list[ModelMessage]:
        copied_messages = deepcopy(messages)

        # Change first message's system prompt
        for part in copied_messages[0].parts:
            if isinstance(part, SystemPromptPart):
                part.content = self.system_prompt

        # Append user prompt
        if prompt:
            if isinstance(copied_messages[-1], ModelRequest):
                copied_messages[-1].parts.append(UserPromptPart(content=prompt))
            else:
                copied_messages.append(ModelRequest(parts=[UserPromptPart(content=prompt)]))

        return copied_messages

    async def run(self, prompt: str | None, messages: list[ModelMessage]) -> AsyncIterator[ModelResponseStreamEvent]:
        messages = self.prepare_messages(prompt, messages)
        model_request_parameters = ModelRequestParameters(
            function_tools=self.tools, allow_text_result=not self._tool_call_only, result_tools=[]
        )
        async with self.model.request_stream(messages, self.model_settings, model_request_parameters) as response:
            async for message in response:
                if not message:
                    continue
                yield message
            self._messages = [*messages, response.get()]
            self._usage.incr(response.usage(), requests=1)

    def usage(self) -> Usage:
        return self._usage

    def all_messages(self) -> list[ModelMessage]:
        return self._messages
