"""LiveKit voice worker: STT -> THE SAME AgentController -> TTS.

Voice is a channel, not a second brain: every turn's transcript goes through
AgentController.handle_user_message, so the 9 tools, propose/confirm consent
tokens, per-customer dedup, policy, and logging all carry over unchanged.

Design note (v1): turn-based bridge. LiveKit handles mic capture, VAD, and
turn-taking; the finalized transcript is sent to the controller and the reply
is spoken. Per-turn latency is logged so regressions are visible. A native
AgentSession LLM loop is the v2 path if bridge latency ever matters.

Run (after .env has LIVEKIT_* keys):
    .venv/Scripts/python.exe app/voice/agent.py start
"""

import asyncio
import logging
import time

from app.agent.controller import AgentController

logger = logging.getLogger("voice-agent")

# Language of the voice replies: TTS voice per language. hi-IN voice keeps
# Hinglish replies natural.
TTS_VOICE = "hi-IN-SwaraNeural"


def build_components():
    """STT/TTS/VAD factories — free-tier first, all swappable via one place."""
    from livekit.plugins import silero

    stt = tts = None
    if _env("DEEPGRAM_API_KEY"):
        from livekit.plugins import deepgram

        stt = deepgram.STT(model="nova-2", language="multi")  # free $200 credit, strong Hindi
    elif _env("GROQ_API_KEY"):
        from livekit.plugins.openai import STT

        stt = STT.with_openai(
            base_url="https://api.groq.com/openai/v1", api_key=_env("GROQ_API_KEY"),
            model="whisper-large-v3", language="hi",
        )
    if stt is None:
        raise RuntimeError("voice STT not configured: set DEEPGRAM_API_KEY (recommended) or GROQ_API_KEY in .env")

    try:
        from livekit.plugins import edge  # community plugin: free neural TTS, good Hindi

        tts = edge.TTS(voice=TTS_VOICE)
    except ImportError as e:
        raise RuntimeError("edge TTS plugin missing: pip install livekit-plugins-edge") from e

    return stt, tts, silero.VAD.load()


def _env(key: str) -> str:
    import os

    return (os.getenv(key) or "").strip()


class ReceptionistBridge:
    """One AgentController conversation per room; turns STT text into speech."""

    def __init__(self) -> None:
        self.controller: AgentController | None = None
        self.session = None  # livekit AgentSession, set on entrypoint

    def ensure_controller(self) -> AgentController:
        if self.controller is None:
            self.controller = AgentController(channel="voice")
        return self.controller

    async def on_final_transcript(self, text: str) -> None:
        if not text.strip() or self.session is None:
            return
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(self.ensure_controller().handle_user_message, text)
            latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info("voice turn latency_ms=%d tools=%s", latency_ms,
                        [t["tool"] for t in result.get("tool_trace", [])])
            await self.session.say(result["reply"])
        except Exception as e:  # a failed turn must never kill the call
            logger.exception("voice turn failed")
            await self.session.say("Sorry, something went wrong. Could you say that again?")


async def entrypoint(ctx):
    """LiveKit job entrypoint: one room = one receptionist conversation."""
    from livekit.agents import AgentSession, RoomInputOptions
    from livekit.plugins.openai import LLM  # noqa: F401  (session requires an llm slot; unused in bridge mode)

    stt, tts, vad = build_components()
    bridge = ReceptionistBridge()

    session = AgentSession(stt=stt, tts=tts, vad=vad)
    bridge.session = session

    @session.on("user_input_transcribed")
    def _on_transcript(event):
        if getattr(event, "is_final", False):
            asyncio.ensure_future(bridge.on_final_transcript(event.transcript))

    await session.start(
        room=ctx.room,
        room_input_options=RoomInputOptions(),  # default: participant mic
    )
    await session.say("Hello! Welcome to SmileCare Dental. How can I help you today?")


if __name__ == "__main__":
    from livekit.agents import cli, WorkerOptions

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="receptionist"))
