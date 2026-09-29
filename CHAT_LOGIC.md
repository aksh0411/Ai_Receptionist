# Chat Logic — how the receptionist agent thinks, acts, and what it must never do

This document describes the chat agent's design: the message flow, the tools it can
call, the behavioral rules it is prompted with, and the guarantees enforced in code
regardless of what the model says. Read this before changing `app/agent/` — most of
it is a contract, not a suggestion.

## Message flow (one user turn)

```
customer message
      |
      v
POST /chat  (app/channels/chat.py)
      |
      v
AgentController.handle_user_message  (app/agent/controller.py)
      |  - loads conversation history from the JSON store
      |  - rebuilds the system prompt from LIVE store data (fresh every turn)
      |  - appends the user message
      v
LLM round-trip loop  (up to AGENT_MAX_TOOL_ROUNDS = 6 rounds)
      |  model replies with EITHER:
      |    a final text answer  -> loop ends, reply returned
      |    one or more tool calls -> executor runs each one
      |
      v
execute_tool  (app/agent/executor.py)
      |  - parses the model's JSON arguments
      |  - dispatches to the real function in app/tools/booking.py
      |  - injects business_id / conversation_id ONLY into tools that declare them
      |  - logs every attempt (args, result, success, latency) to the store
      |  - never raises: tool bugs become {"success": false, "error": ...}
      v
tool result flows back to the model as a "tool" message
      |
      +--> model may call more tools (next round) or produce the final reply
      v
full message history (incl. tool calls + results) saved to the store
reply + tool_trace + escalated flag returned to the chat page
```

Generation settings: `temperature=0.4`, `max_tokens=700`, `tool_choice="auto"`.
If all 6 rounds are spent on tool calls without a final answer, the customer gets a
fallback apology instead of a broken silence.

## The iron rule

**The model never touches the data store.** It can only *propose* tool calls;
`executor.py` runs them against the JSON store, and only the tool result flowing
back tells the model whether something actually happened. A booking exists only
after `book_appointment` succeeds and returns a booking reference (e.g. `SD-9A73`).
The model is instructed to never claim a booking that did not happen — and even if
it hallucinated one, there would be no row in the store to back it up.

## System prompt: assembled, never hardcoded

`app/agent/prompts.py` rebuilds the system prompt on **every user turn** from live
`data/clinic.json`:

- **Date anchors** — yesterday / today / tomorrow / day-after as real dates. The
  model is told to use these directly instead of doing its own date arithmetic
  (live testing showed models inventing dates like 2026-09-31).
- **Opening hours** per weekday (24h), **services with duration + price**, clinic
  address and phone, and the **booking rules** (minimum notice, max advance days).

Consequence: to change the clinic's hours or prices, edit `data/clinic.json` (while
the server is stopped) — no prompt editing, no redeploy.

## The 8 tools (`app/agent/tool_specs.py`)

| Tool | Purpose |
|---|---|
| `check_availability` | Open slots for a service on a date; returns alternatives when the requested time is taken. Must run before any booking talk. |
| `book_appointment` | Create a booking; the returned `booking_reference` is the only proof it exists. |
| `cancel_appointment` | Cancel by booking reference. |
| `reschedule_appointment` | Move a booking to a new date/time by reference. |
| `lookup_appointments` | Find a customer's active bookings by phone — for cancel/reschedule without a reference. |
| `lookup_customer` | Customer profile by phone — used to greet returning patients by name. |
| `get_business_info` | Clinic facts (hours / services / rules) on demand instead of guessing. |
| `escalate_to_human` | Flag the conversation for human staff. |

## Behavioral constraints (the prompt contract)

These live in the "HOW TO WORK" section of the system prompt. They were each added
in response to a real failure observed in live testing:

1. **No promised times before checking.** Never suggest, promise, or agree to a
   specific time before `check_availability` has returned for that date+service.
   If the customer names a time, the model must call the tool first.
2. **AM/PM display only.** Tools return 24h values plus `*_display` twins; the
   customer is shown AM/PM times, never "20:30". The 24h values are for tool calls
   only. The customer should never have to ask "is that AM or PM?"
3. **Complete alternatives.** When a slot is taken, offer the nearest slots
   (sorted by proximity to the request) and describe the full day picture from
   `windows_summary` ("morning 9 AM–1 PM and evening 4–8 PM") — never a partial
   list presented as everything.
4. **Explicit confirmation before booking.** Collect service, date/time, name, and
   phone; summarize the full booking including price; get an explicit "yes" before
   calling `book_appointment`.
5. **"Confirmed" only after a reference.** The word confirmed may only follow a
   successful `book_appointment` with a booking reference in hand.
6. **Cancel/reschedule without a reference.** Ask for the phone number, call
   `lookup_appointments`, confirm the exact appointment with the customer, then act
   using its reference. If several bookings come back, never pick one — ask.
7. **Returning patients.** When a phone number appears, call `lookup_customer`,
   greet by name, mention upcoming appointments.
8. **Error discipline.** On a tool error, read it, change what it points at, retry —
   never repeat the identical call with identical arguments.
9. **Escalation triggers.** Emergency symptoms (heavy bleeding, severe trauma,
   swelling), anger, an explicit request for a human, or anything the agent cannot
   answer → `escalate_to_human`.
10. **No medical advice.** No diagnoses, treatment opinions, or clinical judgment.
11. **Language matching.** Reply in the customer's language — English, Hindi, or
    Hinglish — and keep replies chat-length, not essays.

## Guarantees enforced in code (model-independent)

Even if the model ignores the prompt, the backend holds these lines:

- **Double-booking guard** (`app/tools/booking.py`): the slot is re-validated
  against live store data immediately before save; one active booking per
  staff/slot. A race or a hallucinated time fails at the store, not in production.
- **Booking rules**: minimum notice (minutes) and max advance (days) are enforced
  by the tool layer from the store's rules — the prompt stating them is a courtesy.
- **Phone normalization**: every phone is reduced to its last 10 digits, so
  `+91 98765 01234`, `9876501234`, and `098765 01234` are the same customer.
- **Context injection is safe**: `business_id` / `conversation_id` are injected
  only into tools whose signatures declare them; the model's own arguments are
  always passed through untouched.
- **Tool bugs don't kill the chat**: `execute_tool` catches everything and returns
  a structured error the model can read and react to.
- **Round cap**: `AGENT_MAX_TOOL_ROUNDS` bounds the loop, so a confused model
  can't spin on tool calls forever.

## Logging = the future training set

Every conversation (`conversations`) and every tool call (`tool_calls` — args,
result, success/error, latency) is persisted in the JSON store. This is deliberate:
the next milestone is an eval harness (60–80 scenarios) and a fine-tune of Qwen3-8B
on these logged transcripts. If you change prompts or tools, the logs remain the
before/after evidence.

## Provider notes (OpenAI-compatible interface)

- The agent talks to **any OpenAI-compatible endpoint** via the `openai` client:
  base URL + key + model all come from `.env` (`LLM_BASE_URL`, `LLM_API_KEY`,
  `LLM_MODEL`). Currently Google Gemini (`gemini-flash-lite-latest` through the
  `.../v1beta/openai/` adapter); previously Groq-hosted Qwen.
- **Gemini 3.x quirk**: when replaying an assistant message that contains tool
  calls, Gemini requires its `thought_signature` to be echoed back. The controller
  preserves these provider extras via `model_extra` so replay works on any
  provider that adds such fields.
- Known provider behavioral difference to watch in evals: Gemini booked without an
  extra confirmation turn when all details arrived in one message (Qwen asked
  first). Constraint 4 is the thing to test when swapping providers.
