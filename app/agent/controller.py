"""The agent loop: user message -> LLM -> tool calls -> backend executes -> reply.

Iron rule enforced here: the model never touches the data store. It proposes tool
calls; executor.py runs them against the JSON store; only the tool result flowing
back tells the model whether something actually happened.
"""

import json

from openai import OpenAI

from app.agent.executor import execute_tool
from app.agent.prompts import build_system_prompt
from app.agent.tool_specs import TOOL_SPECS
from app.config import AGENT_MAX_TOOL_ROUNDS, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from app.db import json_store as store


class AgentError(Exception):
    """Raised for setup/API problems the user should see as a clear message."""


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

        self.client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL) if LLM_API_KEY else None

    def handle_user_message(self, text: str) -> dict:
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
                    messages=messages,
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
