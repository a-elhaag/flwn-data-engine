"""Data Engine application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routes import router
from clients import vector_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    vector_store.ensure_collection()
    yield


app = FastAPI(title="flwn-data-engine", lifespan=lifespan)
app.include_router(router)
