"""LiveKit voice worker: STT -> THE SAME AgentController -> TTS.

Voice is a channel, not a second brain: every turn's transcript goes through
AgentController.handle_user_message, so the 9 tools, propose/confirm consent
tokens, per-customer dedup, dialogue policy, and logging all carry over unchanged.

Design note (v1): turn-based bridge. LiveKit handles mic capture, VAD, and
turn-taking; the finalized transcript is sent to the controller and the reply
is spoken. Per-turn latency is logged so regressions are visible. A native
AgentSession LLM loop is the v2 path if bridge latency ever matters.

Run (after .env has LIVEKIT_* keys and DEEPGRAM_API_KEY or GROQ_API_KEY):
    .venv/Scripts/python.exe app/voice/agent.py start
"""

import asyncio
import logging
import time
import uuid

from livekit import agents
from livekit.agents import AgentSession, RoomInputOptions, cli, WorkerOptions
from livekit.agents.types import APIConnectOptions
from livekit.agents.tts import AudioEmitter, ChunkedStream, TTSCapabilities

from app.agent.controller import AgentController

logger = logging.getLogger("voice-agent")

TTS_VOICE = "hi-IN-SwaraNeural"  # free Edge-TTS neural voice; keeps Hinglish natural
TTS_SAMPLE_RATE = 24000
_CONN_OPTIONS = APIConnectOptions(max_retry=3, retry_interval=2.0, timeout=30.0)


def _env(key: str) -> str:
    import os

    return (os.getenv(key) or "").strip()


class EdgeTTS(agents.tts.TTS):
    """Free Edge-TTS (Microsoft neural voices, no API key) as a LiveKit TTS plugin.

    edge-tts streams MP3 chunks; AudioEmitter decodes them, so this plugin only
    forwards bytes. Non-streaming: one HTTP render per sentence group.
    """

    def __init__(self, voice: str = TTS_VOICE) -> None:
        super().__init__(
            capabilities=TTSCapabilities(streaming=False),
            sample_rate=TTS_SAMPLE_RATE,
            num_channels=1,
        )
        self._voice = voice

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = _CONN_OPTIONS) -> ChunkedStream:
        return _EdgeChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class _EdgeChunkedStream(ChunkedStream):
    def __init__(self, *, tts: EdgeTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)

    async def _run(self, output_emitter: AudioEmitter) -> None:
        import edge_tts

        output_emitter.initialize(
            request_id=str(uuid.uuid4()),
            sample_rate=TTS_SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/mpeg",
        )
        communicate = edge_tts.Communicate(self.input_text, self._tts._voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                output_emitter.push(chunk["data"])
        output_emitter.flush()


def build_stt():
    """STT factory — free-tier first, one place to swap."""
    if _env("DEEPGRAM_API_KEY"):
        from livekit.plugins import deepgram

        return deepgram.STT(model="nova-2", language="multi")  # strong Hindi/Hinglish
    if _env("GROQ_API_KEY"):
        from livekit.plugins.openai import STT as OpenAISTT

        return OpenAISTT(
            model="whisper-large-v3",
            api_key=_env("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
    raise RuntimeError(
        "voice STT not configured: set DEEPGRAM_API_KEY (recommended) or GROQ_API_KEY in .env"
    )


class ReceptionistBridge:
    """One AgentController conversation per room; turns STT text into speech."""

    def __init__(self) -> None:
        self.controller: AgentController | None = None
        self.session: AgentSession | None = None

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
            logger.info(
                "voice turn latency_ms=%d tools=%s",
                latency_ms,
                [t["tool"] for t in result.get("tool_trace", [])],
            )
            await self.session.say(result["reply"])
        except Exception:  # a failed turn must never kill the call
            logger.exception("voice turn failed")
            await self.session.say("Sorry, something went wrong. Could you say that again?")


async def entrypoint(ctx):
    """LiveKit job entrypoint: one room = one receptionist conversation."""
    from livekit.plugins import silero

    bridge = ReceptionistBridge()
    session = AgentSession(stt=build_stt(), tts=EdgeTTS(), vad=silero.VAD.load())
    bridge.session = session

    @session.on("user_input_transcribed")
    def _on_transcript(event):
        if getattr(event, "is_final", False) and getattr(event, "transcript", ""):
            asyncio.ensure_future(bridge.on_final_transcript(event.transcript))

    await session.start(room=ctx.room, room_input_options=RoomInputOptions())
    await session.say("Hello! Welcome to SmileCare Dental. How can I help you today?")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="receptionist"))
