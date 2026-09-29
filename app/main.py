from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.channels.chat import router as chat_router
from app.db import json_store as store

CHAT_PAGE = Path(__file__).parent / "static" / "chat.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init()  # loads data/clinic.json or seeds it — no database needed
    yield


app = FastAPI(title="FrontDesk AI", lifespan=lifespan)
app.include_router(chat_router)


@app.get("/")
def index():
    return FileResponse(CHAT_PAGE)


@app.get("/health")
def health():
    return {"status": "ok", "store": str(store.DATA_FILE)}
