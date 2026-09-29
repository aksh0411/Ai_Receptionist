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

## 2026-09-30 — v0.2: JSON store (Postgres disconnected) + 3 chat fixes

- All data moved to `data/clinic.json` (readable, hand-editable, delete = factory reset). SQLAlchemy/psycopg removed; `app/db/json_store.py` is the new authority (locked + atomic writes). Same tool behavior, same smoke tests — 8/8 passing.
- Fixed the 3 failures from the user's pasted chat: (1) `alternatives` now sorted by PROXIMITY to the requested time (asked 11 PM -> 7:30 PM first, not 9:00 AM); (2) all human-facing times have AM/PM `*_display` twins; (3) prompt now forbids suggesting any time before check_availability runs, and requires the full windows picture instead of a partial list.
- Replayed the exact failing conversation ("tooth filling on 3rd Oct at 8 pm"): agent now checks first, offers 7:00 PM down to 5:00 PM, states "9:00 AM-1:00 PM and 4:00 PM-8:00 PM", books correctly (SD-6146 verified inside clinic.json).
- Booking, greeting, and lookup flows re-verified live.

## 2026-09-30 — Provider switched: Groq -> Gemini

- New Google key in `.env`: `LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`, model `gemini-flash-lite-latest`.
- Discovery: `gemini-2.5-flash` is retired for new accounts; `gemini-3.8-flash` exists but was at capacity (503) + the free tier allows only ~5 requests/min per model; flash-lite has its own quota bucket and worked.
- Code fix (provider-agnostic): Gemini 3.x requires its `thought_signature` (returned in `extra_content`) to be echoed back when replaying assistant tool-call messages — the controller now preserves provider extras via `model_extra`. Without this, every tool call 400s on the second round.
- Live-verified end-to-end on Gemini: availability check -> booking -> reference SD-38AE in the JSON store.
- Behavior note: Gemini booked without an extra confirmation turn when all details came in one message (Qwen asked first) — prompt-adherence difference to watch in evals.

**Next:** eval harness (60-80 scenarios) -> fine-tune Qwen3-8B on logged conversations -> WhatsApp -> voice.
