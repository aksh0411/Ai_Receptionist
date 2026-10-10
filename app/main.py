from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

from app.channels.chat import router as chat_router
from app.voice.api import router as voice_router
from app.db import json_store as store

CHAT_PAGE = Path(__file__).parent / "static" / "chat.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init()  # loads data/clinic.json or seeds it — no database needed
    yield


app = FastAPI(title="FrontDesk AI", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(voice_router)


@app.get("/")
def index():
    return FileResponse(CHAT_PAGE)


@app.get("/health")
@app.get("/api/health")
def health():
    return {"status": "ok", "store": str(store.DATA_FILE)}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
