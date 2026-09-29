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

## 2026-09-30 — Realism upgrades + one-click run.bat

- New tools: `lookup_appointments(phone)` and `lookup_customer(phone)` — customers can now cancel/reschedule without knowing a booking reference, and returning patients get greeted by name. Phone numbers normalized (digits, last 10) everywhere, so `+91 98765 01234` = `9876501234`.
- Prompts updated for both flows (ask for phone → look up → confirm exact appointment → act).
- "New conversation" button on the test page.
- `run.bat`: double-click → installs deps, seeds (once), opens browser, starts server.
- Smoke test now state-adaptive (picks the first free slot) + asserts lookups; 7/7 passing.
- Live-verified: "cancel my appointment tomorrow" → asks phone → finds SD-9A73 → confirms → cancelled (checked in DB); greeting flow greeted Rahul by name and noted nothing upcoming.

**Next:** eval harness (60–80 scenarios) → fine-tune Qwen3-8B on logged conversations → WhatsApp → voice.
