from contextlib import asynccontextmanager

from fastapi import FastAPI

from delamain.routers import routers


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def hello():
    return {"message": "Hello World"}


for router in routers:
    app.include_router(router)
