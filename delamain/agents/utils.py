import os

import boto3
from botocore.config import Config
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider


def get_model(model_name: str) -> Model:
    aws_read_timeout = 600
    aws_connect_timeout = 60

    if model_name.startswith("bedrock:"):
        # https://github.com/pydantic/pydantic-ai/pull/1259
        model_name = model_name[len("bedrock:") :].strip()
        return BedrockConverseModel(
            model_name,
            provider=BedrockProvider(
                bedrock_client=boto3.client(
                    "bedrock-runtime",
                    config=Config(
                        read_timeout=aws_read_timeout
                        or float(os.getenv("AWS_READ_TIMEOUT", 300)),  # Need more time for long tool call
                        connect_timeout=aws_connect_timeout
                        or float(os.getenv("AWS_CONNECT_TIMEOUT", 60)),  # Boto3 use 60 as default
                    ),
                )
            ),
        )

    return infer_model(model_name)
