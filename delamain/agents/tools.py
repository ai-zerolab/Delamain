from typing import Annotated

from pydantic import Field
from pydantic_ai import Tool


def get_internal_tools() -> list[Tool]:
    return [_think_tool()]


def _think_tool() -> Tool:
    async def _(
        thought: Annotated[str, Field(description="A thought to think about.")],
    ) -> dict[str, str]:
        return {
            "thought": thought,
        }

    return Tool(
        _,
        name="think",
        description="Use the tool to think about something. It will not obtain new information or change the database, but just append the thought to the log. Use it when complex reasoning or some cache memory is needed. You should say the response in the following format: <Thinking>{{ thought }}</Thinking>",
    )
