"""The agent loop: user message -> LLM -> tool calls -> backend executes -> reply.

Iron rule enforced here: the model never touches the data store. It proposes tool
calls; executor.py runs them against the JSON store; only the tool result flowing
back tells the model whether something actually happened.
"""

import json

from app.agent.executor import execute_tool
from app.agent.llm_client import make_client
from app.agent.prompts import build_system_prompt
from app.agent.tool_specs import TOOL_SPECS
from app.config import AGENT_MAX_TOOL_ROUNDS, LLM_MODEL
from app.db import json_store as store


class AgentError(Exception):
    """Raised for setup/API problems the user should see as a clear message."""


# Messages actually SENT to the model, beyond the system prompt. The full history
# stays persisted; only what is sent is trimmed.
CONTEXT_WINDOW_MESSAGES = 24


def _trimmed_for_llm(messages: list[dict]) -> list[dict]:
    """System prompt + the most recent messages that fit the budget.

    Trims on tool-call GROUP boundaries: an assistant message with tool_calls and
    its tool results are one unit and never split, so the provider never sees a
    dangling half of a tool-call exchange.
    """
    if len(messages) <= CONTEXT_WINDOW_MESSAGES:
        return list(messages)
    head, body = messages[0], messages[1:]

    units: list[list[dict]] = []
    i = 0
    while i < len(body):
        m = body[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            group = [m]
            i += 1
            while i < len(body) and body[i].get("role") == "tool":
                group.append(body[i])
                i += 1
            units.append(group)
        else:
            units.append([m])
            i += 1

    kept: list[list[dict]] = []
    budget = CONTEXT_WINDOW_MESSAGES - 1  # minus the system prompt
    for unit in reversed(units):
        if budget - len(unit) < 0 and kept:
            break
        kept.insert(0, unit)
        budget -= len(unit)
    return [head] + [m for unit in kept for m in unit]


def _tool_call_to_dict(tc) -> dict:
    """Serialize a tool call, preserving provider extras (Gemini 3.x requires its
    thought_signature — returned in extra_content — to be echoed back on replay)."""
    out = {
        "id": tc.id,
        "type": tc.type or "function",
        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
    }
    extra = getattr(tc, "model_extra", None) or {}
    for key in ("extra_content", "thought_signature"):
        if key in extra and extra[key] is not None:
            out[key] = extra[key]
    return out


class AgentController:
    def __init__(self, conversation_id: int | None = None, channel: str = "web_chat"):
        self.business = store.get_business()
        if self.business is None:
            raise AgentError("no business configured — run scripts/seed_minimal.py first")

        self.conversation = store.get_conversation(conversation_id) if conversation_id else None
        if self.conversation is None:
            self.conversation = store.create_conversation(self.business["id"], channel)

        self.client = make_client()

    def handle_user_message(self, text: str) -> dict:
        # Turn counter gives consent tokens their validity window (executor.py):
        # an offer made this turn or the last may be booked; older ones cannot.
        self.conversation["turn_count"] = self.conversation.get("turn_count", 0) + 1
        messages = list(self.conversation["messages"] or [])
        system_prompt = build_system_prompt(self.business)
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": system_prompt}
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": text})

        trace: list[dict] = []
        if self.client is None:
            reply = "LLM is not configured: set LLM_API_KEY in .env and restart the server."
        else:
            try:
                reply, trace = self._run_loop(messages)
            except AgentError as e:
                reply = f"Agent error: {e}"

        self.conversation["messages"] = messages
        store.save_conversation(self.conversation)
        return {
            "reply": reply,
            "conversation_id": self.conversation["id"],
            "tool_trace": trace,
            "escalated": self.conversation["escalated"],
        }

    def _run_loop(self, messages: list[dict]) -> tuple[str, list[dict]]:
        trace: list[dict] = []
        for _ in range(AGENT_MAX_TOOL_ROUNDS):
            try:
                response = self.client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=_trimmed_for_llm(messages),
                    tools=TOOL_SPECS,
                    tool_choice="auto",
                    temperature=0.4,
                    max_tokens=700,
                )
            except Exception as e:
                raise AgentError(f"LLM API call failed: {e}") from e

            message = response.choices[0].message
            if not message.tool_calls:
                reply = message.content or "(empty response)"
                messages.append({"role": "assistant", "content": reply})
                return reply, trace

            assistant_msg = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [_tool_call_to_dict(tc) for tc in message.tool_calls],
            }
            msg_extra = getattr(message, "model_extra", None) or {}
            if msg_extra.get("thought_signature"):
                assistant_msg["thought_signature"] = msg_extra["thought_signature"]
            messages.append(assistant_msg)
            for tc in message.tool_calls:
                result, summary = execute_tool(
                    tc.function.name, tc.function.arguments,
                    self.business["id"], self.conversation["id"],
                )
                trace.append({"tool": tc.function.name, "summary": summary, "ok": bool(result.get("success"))})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
        return "Sorry, I couldn't complete that. Please try again or call the clinic directly.", trace
