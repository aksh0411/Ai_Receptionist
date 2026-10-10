"""Voice API: LiveKit access tokens + the voice page.

The voice worker (app/voice/agent.py) joins the same rooms; this router only
hands out short-lived tokens so the browser can connect its microphone.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.config import LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL

router = APIRouter(prefix="/voice")

VOICE_PAGE = Path(__file__).resolve().parents[1] / "static" / "voice.html"


@router.get("/page")
def page():
    return FileResponse(VOICE_PAGE)


@router.get("/token")
def token(room: str = "receptionist", identity: str = "guest"):
    """Short-lived LiveKit access token for the browser mic."""
    if not (LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        return {"error": "voice not configured: set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in .env"}
    from livekit.api import AccessToken, VideoGrants

    access = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET).with_identity(identity).with_grants(
        VideoGrants(room_join=True, room=room)
    )
    return {"url": LIVEKIT_URL, "token": access.to_jwt()}
