from contextlib import asynccontextmanager

from fastapi import FastAPI

from delamain.routers import routers


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def hello():
    return {"message": "Welcome to Delamain! View our project at https://github.com/ai-zerolab/Delamain"}


for router in routers:
    app.include_router(router)
