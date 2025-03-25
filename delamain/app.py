from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from delamain.config import get_config
from delamain.routers import routers


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def hello():
    return {"message": "Welcome to Delamain! View our project at https://github.com/ai-zerolab/Delamain"}


@app.middleware("http")
async def verify_token(request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    if request.url.path == "/" or request.url.path == "/docs" or request.url.path == "/openapi.json":
        return await call_next(request)

    config = get_config()
    if not config.api_key:
        return await call_next(request)
    if request.headers.get("Authorization") == f"Bearer {config.api_key}":
        # OpenAI
        return await call_next(request)
    if request.headers.get("x-api-key") == f"{config.api_key}":
        # Anthropic
        return await call_next(request)

    return Response(
        status_code=401,
        content="Unauthorized. Check environment API_KEY.",
    )


for router in routers:
    app.include_router(router)
