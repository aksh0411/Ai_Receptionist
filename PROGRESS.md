# Progress Log

## 2026-09-29 — v0.1 skeleton built

- Created isolated git repo inside `Receptionist/` (old repo root was the home directory — unsafe).
- Created `frontdesk` database on local PostgreSQL 18 (password in `.env`, gitignored).
- Built the backend: 10 tables, booking tools (availability / book / cancel / reschedule / info / escalate), agent loop (model proposes, backend executes), `POST /chat` endpoint, chat test page.
- Seeded one minimal clinic (SmileCare Dental) so tools have real data.
- Smoke test passed: booking commits, slot fills up once all staff busy, no double-booking, reschedule + cancel work.
- Committed `4249833`. `.env` (API key) excluded from git.

## 2026-09-30 — Live LLM connected (Groq + Qwen)

- xAI/Grok account has 0 credits → switched to a **Groq** key (free tier).
- Config renamed to provider-neutral `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`; model = `qwen/qwen3.8-27b` (same family as the future fine-tune; Groq also hosts Whisper + Orpheus TTS for the voice phase later).
- Fixed 3 bugs the live tests exposed:
  1. executor passed `conversation_id` into tools that don't accept it → context keys now injected only into tools that declare them.
  2. model computed "tomorrow" as a nonexistent date (2026-09-31) → date anchors (yesterday/today/tomorrow/day-after) now injected into the system prompt + tool date-errors self-correct with anchors.
  3. spec/function argument mismatch (`date` vs `date_str`) → function signatures now match the LLM-facing tool names.
- **Full live booking flow verified end-to-end:** "book a dental cleaning tomorrow at 11am" → availability check → asks name/phone → summary → "yes" → `book_appointment` → reference `SD-9A73` verified in PostgreSQL.

**Run:** `.venv/Scripts/python -m uvicorn app.main:app --reload` → http://127.0.0.1:8000

**Next:** eval harness (60–80 scenarios) → fine-tune Qwen3-8B on logged conversations → WhatsApp → voice.
