# Voice channel — LiveKit ("AI caller")

Voice is a **channel, not a second brain**: mic audio → LiveKit room → STT →
**the same `AgentController`** (9 tools, propose/confirm consent tokens,
per-customer dedup, dialogue policy, full logging) → TTS → the room. Every
safety rail from chat works identically over voice.

```text
Browser mic (voice.html) → LiveKit room (WebRTC)
      → STT (Deepgram nova-2 multi / Groq whisper-large-v3)
      → AgentController.handle_user_message()   ← the only brain
      → TTS (Edge-TTS, hi-IN voice, free)
      → room → customer's speakers
```

## Setup (all free tiers)

1. **LiveKit Cloud** (free tier): create a project at https://cloud.livekit.io →
   copy `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` into `.env`.
2. **Speech-to-text** — either:
   - **Deepgram** (recommended): https://console.deepgram.com → free $200 credit,
     `nova-2` with `language="multi"` handles Hindi/Hinglish well → `DEEPGRAM_API_KEY`
   - **Groq**: your existing Groq key hosts `whisper-large-v3` free → `GROQ_API_KEY`
3. **TTS**: Edge-TTS — nothing to configure, no key, free neural voices.
4. Install the voice dependencies (already in `requirements.txt`):
   `.venv/Scripts/pip install -r requirements.txt`
5. Run: **`run_voice.bat`** (or `uvicorn app.main:app` + `python app/voice/agent.py start`).
   Open http://127.0.0.1:8000/voice/page → Start talking.

## Measured targets (per-turn latency is logged by the worker)

| Metric | Target | Notes |
|---|---|---|
| End-to-end reply latency | < 1.5 s | flash-lite ≈0.4–0.6 s + Edge-TTS ≈0.3–0.5 s + STT tail |
| Reliability | no dropped turns | every hop retried; a failed turn never kills the call |
| Task completion | booking completable by voice | same consent flow: propose → "yes" → reference spoken back |

Latency per turn is printed by the worker (`voice turn latency_ms=…`) so regressions show up immediately.

## Architecture notes

- **v1 is a turn-based bridge**: finalized transcripts go through
  `AgentController.handle_user_message`; LiveKit handles VAD + turn-taking.
  This reuses 100% of the agent stack. Migrating to a native LiveKit
  `AgentSession` LLM loop (tools declared to LiveKit) is the v2 path if
  bridge latency ever matters — the tool layer and consent gate port directly.
- **One conversation per room**: each caller gets their own conversation
  (`channel="voice"`), same as chat.
- **Phone calls (Twilio SIP → LiveKit)** are the next step and need **zero
  rework**: same room, same worker — only the inbound trunk is new.
- Voice adds these `.env` keys: `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
  `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY` (or `GROQ_API_KEY`). Without them,
  chat keeps working exactly as before.
