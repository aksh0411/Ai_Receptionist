from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.channels.chat import router as chat_router
from app.db.base import Base, engine

CHAT_PAGE = Path(__file__).parent / "static" / "chat.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)  # dev-mode schema sync; Alembic comes later
    yield


app = FastAPI(title="FrontDesk AI", lifespan=lifespan)
app.include_router(chat_router)


@app.get("/")
def index():
    return FileResponse(CHAT_PAGE)


@app.get("/health")
def health():
    return {"status": "ok"}
