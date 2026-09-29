from fastapi import APIRouter
from pydantic import BaseModel

from app.agent.controller import AgentController, AgentError

router = APIRouter()


class ChatIn(BaseModel):
    message: str
    conversation_id: int | None = None


@router.post("/chat")
def chat(payload: ChatIn):
    try:
        controller = AgentController(conversation_id=payload.conversation_id)
        return controller.handle_user_message(payload.message.strip())
    except AgentError as e:
        return {
            "reply": f"Setup problem: {e}",
            "conversation_id": payload.conversation_id,
            "tool_trace": [],
            "escalated": False,
        }
