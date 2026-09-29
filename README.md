# FrontDesk AI — v0.2 (JSON store)

AI receptionist backend for a dental clinic: **any OpenAI-compatible LLM as the brain
(currently Gemini flash-lite via .env), a human-readable JSON store as the authority**. The model
proposes actions through tool calls; the backend executes them. A booking only exists
after the store save returns a booking reference.

**No database needed.** All data lives in `data/clinic.json` — open it to watch bookings
appear, hand-edit it while the server is stopped, or delete it to factory-reset.
PostgreSQL returns when the product goes multi-user (the tool layer hides the storage).

## Layout

```
app/
  config.py            # env-driven settings
  main.py              # FastAPI app (loads/seeds the JSON store on startup)
  db/json_store.py     # THE store: locked, atomic writes, seed definitions
  tools/booking.py     # check_availability, book, cancel, reschedule, lookups, info, escalate
  agent/
    tool_specs.py      # tool schemas the model sees
    prompts.py         # system prompt built from live store data
    executor.py        # runs tool calls, logs every attempt
    controller.py      # the agent loop (LLM <-> tools)
  channels/chat.py     # POST /chat
  static/chat.html     # test page
scripts/
  seed_minimal.py      # seeds data/clinic.json if missing (idempotent)
  smoke_test.py        # full booking-stack test, no LLM needed
```

## Run it

Double-click **`run.bat`** — it creates the venv, installs dependencies, seeds the
clinic (once), opens the browser, and starts the server.

Or manually:

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
python scripts/seed_minimal.py          # once
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000 — chat page. API: `POST /chat {"message": "...", "conversation_id": null}`.

`.env` holds `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` — any
OpenAI-compatible provider works (Gemini, Groq, xAI, OpenAI, or a locally served model);
swapping is one .env edit, no code change. See [`CHAT_LOGIC.md`](CHAT_LOGIC.md) for how the
chat agent works: message flow, tools, and the constraints it operates under.

## Notes

- Every conversation is stored in the JSON store (`conversations`, `tool_calls`) — these
  are the future fine-tuning dataset and eval inputs.
- The double-booking guard is enforced in code (slot re-validation right before save;
  one active booking per staff/slot is checked against live data every time).
- Times: tools return 24h values plus AM/PM `*_display` fields; the prompt makes the
  model show customers AM/PM only.
- The LLM account needs a valid key with quota before the agent will answer.
