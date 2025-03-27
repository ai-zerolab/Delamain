import base64
import json
import uuid
from typing import Any

from anthropic.types import (
    MessageParam as AnthropicMessage,
)
from anthropic.types import (
    ToolParam,
)
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pydantic_ai.messages import (
    AgentStreamEvent,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.tools import ToolDefinition
from sse_starlette import EventSourceResponse

from delamain.agents.mas import DelamainMAS
from delamain.agents.react import DelamainReAct
from delamain.config import Config, get_config
from delamain.log import logger

router = APIRouter(
    tags=["Anthropic"],
    prefix="/anthropic/v1",
)


class SystemObject(BaseModel):
    text: str


class MessageRequest(BaseModel):
    messages: list[dict[str, Any]]
    tools: list[ToolParam] | None = None
    system: str | list[SystemObject] | None = None


@router.post("/messages")
async def anthropic_messages(
    anthropic_request: MessageRequest, config: Config = Depends(get_config)
) -> EventSourceResponse:
    executor_tools = [
        ToolDefinition(
            name=tool["name"],
            description=tool["description"],
            parameters_json_schema=tool["input_schema"],
        )
        for tool in anthropic_request.tools or []
    ]
    logger.info(f"Found {len(executor_tools)} executor tools")
    messages = map_messages(anthropic_request.system, anthropic_request.messages)
    if config.mode == "mas":
        agent = DelamainMAS.from_config(
            messages=messages,
            executor_tools=executor_tools,
        )
    elif config.mode == "re-act":
        agent = DelamainReAct.from_config(
            messages=messages,
            executor_tools=executor_tools,
        )
    else:
        raise TypeError(f"Unknown mode: {config.mode}")

    async def _():
        # Send message_start event
        message_id = f"msg_{uuid.uuid4().hex}"
        message_start_data = {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": "delamain",  # Using a placeholder model name
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "content": [],
                "stop_reason": None,
            },
        }
        # JSON serialize the data to ensure proper formatting
        yield {
            "event": "message_start",
            "data": json.dumps(message_start_data, default=lambda o: None if o is None else o),
        }

        # Track content blocks to send stop events
        active_blocks: set[int] = set()

        # Process agent events
        async for event in agent.run():
            event_type, data = map_agent_event(event)

            # Track active blocks for stop events
            if event_type == "content_block_start":
                # map one start into start+delta
                active_blocks.add(data["index"])
                text_data = None
                if data["content_block"] and data["content_block"]["type"] == "text":
                    text_data = data["content_block"]["text"]
                    data["content_block"]["text"] = ""
                # JSON serialize the data to ensure proper formatting
                yield {
                    "event": event_type,
                    "data": json.dumps(data, default=lambda o: None if o is None else o),
                }
                if text_data:
                    event_type = "content_block_delta"
                    data = {
                        "type": "content_block_delta",
                        "index": data["index"],
                        "delta": {"type": "text_delta", "text": text_data},
                    }
            yield {
                "event": event_type,
                "data": json.dumps(data, default=lambda o: None if o is None else o),
            }

        # Send content_block_stop events for any remaining active blocks
        for index in active_blocks:
            content_block_stop_data = {"type": "content_block_stop", "index": index}
            yield {
                "event": "content_block_stop",
                "data": json.dumps(content_block_stop_data, default=lambda o: None if o is None else o),
            }

        # Send message_delta with usage information
        message_delta_data = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {
                "input_tokens": agent.usage.request_tokens or 0,
                "output_tokens": agent.usage.response_tokens or 0,
            },
        }
        yield {
            "event": "message_delta",
            "data": json.dumps(message_delta_data, default=lambda o: None if o is None else o),
        }

        # Send message_stop event
        yield {
            "event": "message_stop",
            "data": json.dumps({"type": "message_stop"}, default=lambda o: None if o is None else o),
        }

    return EventSourceResponse(
        _(),
        ping=True,
    )


def map_messages(  # noqa: C901
    system_prompt: str | SystemObject | None, anthropic_messages: list[AnthropicMessage]
) -> list[ModelMessage]:
    system_prompt_text = ""
    if isinstance(system_prompt, list):
        for obj in system_prompt:
            system_prompt_text += obj.text
    elif isinstance(system_prompt, str):
        system_prompt_text = system_prompt

    messages = [
        ModelRequest(parts=[SystemPromptPart(content=system_prompt_text)]),
    ]
    for message in anthropic_messages:
        parts = []
        # Handle string content vs list of content blocks
        content_blocks = message["content"]
        if isinstance(content_blocks, str):
            # Convert string to a single TextBlockParam
            content_blocks = [{"type": "text", "text": content_blocks}]

        for content in content_blocks:
            content_type = content.get("type")

            if content_type == "text":
                if message["role"] == "user":
                    parts.append(UserPromptPart(content=content["text"]))
                else:  # assistant
                    parts.append(TextPart(content=content["text"]))

            elif content_type == "image":
                # Handle image content
                source = content["source"]
                if source["type"] == "url":
                    # Convert to ImageUrl
                    parts.append(UserPromptPart(content=[ImageUrl(url=source["url"])]))
                elif source["type"] == "base64":
                    # Convert to BinaryContent
                    if isinstance(source["data"], str):
                        # If it's a base64 string, decode it
                        binary_data = base64.b64decode(source["data"])
                    else:
                        # If it's already a file-like object, read it
                        binary_data = source["data"].read()

                    parts.append(
                        UserPromptPart(content=[BinaryContent(data=binary_data, media_type=source["media_type"])])
                    )

            elif content_type == "document":
                # Handle document content
                source = content["source"]
                if source["type"] in ["url", "url_pdf"]:
                    # Convert to DocumentUrl
                    parts.append(UserPromptPart(content=[DocumentUrl(url=source["url"])]))
                elif source["type"] == "base64":
                    # Convert to BinaryContent
                    if isinstance(source["data"], str):
                        binary_data = base64.b64decode(source["data"])
                    else:
                        binary_data = source["data"].read()

                    parts.append(
                        UserPromptPart(content=[BinaryContent(data=binary_data, media_type=source["media_type"])])
                    )
                elif source["type"] == "text":
                    # Handle plain text source
                    parts.append(UserPromptPart(content=source["data"]))

            elif content_type == "tool_use":
                # Convert to ToolCallPart
                parts.append(
                    ToolCallPart(
                        tool_name=content["name"],
                        args=content["input"],
                        tool_call_id=content.get("id"),
                    )
                )

            elif content_type == "tool_result":
                # Convert to ToolReturnPart
                parts.append(
                    ToolReturnPart(
                        tool_name="",  # We may need to look this up from a previous tool_use
                        content=content["content"],
                        tool_call_id=content.get("tool_use_id"),
                    )
                )

        if message["role"] == "assistant":
            messages.append(ModelResponse(parts=parts))
        else:  # user
            messages.append(ModelRequest(parts=parts))
    return messages


def map_agent_event(event: AgentStreamEvent) -> tuple[str, dict[str, Any]]:
    """Map a pydantic_ai AgentStreamEvent to an Anthropic-compatible SSE event.

    Returns a tuple of (event_type, data) for the SSE event.
    """

    if event.event_kind == "part_start":
        part = event.part
        if part.part_kind == "text":
            return "content_block_start", {
                "type": "content_block_start",
                "index": event.index,
                "content_block": {"type": "text", "text": part.content},
            }
        elif part.part_kind == "tool-call":
            return "content_block_start", {
                "type": "content_block_start",
                "index": event.index,
                "content_block": {
                    "type": "tool_use",
                    "id": part.tool_call_id or f"toolu_{uuid.uuid4().hex}",
                    "name": part.tool_name,
                    "input": part.args_as_dict() if part.args else {},
                },
            }

    elif event.event_kind == "part_delta":
        delta = event.delta
        if delta.part_delta_kind == "text":
            return "content_block_delta", {
                "type": "content_block_delta",
                "index": event.index,
                "delta": {"type": "text_delta", "text": delta.content_delta},
            }
        elif delta.part_delta_kind == "tool_call":
            if delta.tool_name_delta:
                return "content_block_delta", {
                    "type": "content_block_delta",
                    "index": event.index,
                    "delta": {"type": "tool_use_delta", "name": delta.tool_name_delta},
                }
            elif delta.args_delta:
                if isinstance(delta.args_delta, str):
                    return "content_block_delta", {
                        "type": "content_block_delta",
                        "index": event.index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": delta.args_delta,
                        },
                    }
                else:
                    return "content_block_delta", {
                        "type": "content_block_delta",
                        "index": event.index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(delta.args_delta),
                        },
                    }

    # Default case for other event types or if we couldn't map
    return "message_delta", {"type": "message_delta", "delta": {}}
