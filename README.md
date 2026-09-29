# FrontDesk AI — v0.1 skeleton

AI receptionist backend for a dental clinic: **any OpenAI-compatible LLM as the brain
(Groq-hosted Qwen by default), PostgreSQL as the authority**. The model proposes actions
through tool calls; the backend executes them against the database. A booking only
exists after its DB commit returns a booking reference.

## Layout

```
app/
  config.py            # env-driven settings
  main.py              # FastAPI app (creates tables on startup)
  db/                  # engine + SQLAlchemy models (10 tables)
  tools/booking.py     # check_availability, book, cancel, reschedule, info, escalate
  agent/
    tool_specs.py      # tool schemas the model sees
    prompts.py         # system prompt built from live DB data
    executor.py        # runs tool calls, logs every attempt
    controller.py      # the agent loop (Grok <-> tools)
  channels/chat.py     # POST /chat
  static/chat.html     # test page
scripts/
  seed_minimal.py      # one minimal clinic (re-runnable)
  smoke_test.py        # full booking-stack test, no LLM needed
```

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
python scripts/seed_minimal.py          # once
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000 — chat page. API: `POST /chat {"message": "...", "conversation_id": null}`.

`.env` holds `DATABASE_URL`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` — any
OpenAI-compatible provider works (Groq, xAI, OpenAI, or a locally served model);
swapping is one .env edit, no code change.

## Notes

- Every conversation is stored in `conversation_logs`, every tool attempt in
  `tool_call_logs` — these are the future fine-tuning dataset and eval inputs.
- The DB blocks double-booking at the schema level (partial unique index on active
  bookings per staff/slot), and `book_appointment` re-validates the slot right before
  committing.
- The Grok account needs credits before the agent will answer (console.x.ai).
