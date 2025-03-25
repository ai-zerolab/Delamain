from collections.abc import AsyncIterator

from pydantic_ai.messages import ModelMessage, ModelResponseStreamEvent
from pydantic_ai.models import KnownModelName, Model, infer_model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition


class Executor:
    def __init__(
        self,
        model: Model | KnownModelName | None,
        system_prompt: str | None = None,
        model_settings: ModelSettings | None = None,
        tools: list[ToolDefinition] | None = None,
    ):
        self.model = infer_model(model)
        self.system_prompt = system_prompt
        self.model_settings = model_settings
        self.tools = tools or []

    async def run(self, messages: list[ModelMessage]) -> AsyncIterator[ModelResponseStreamEvent]:
        yield "world"
