from fastapi import APIRouter

router = APIRouter(
    tags=["OpenAI"],
    prefix="/openai/v1",
)
