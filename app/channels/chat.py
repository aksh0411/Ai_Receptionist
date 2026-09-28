from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.controller import AgentController, AgentError
from app.db.base import get_db

router = APIRouter()


class ChatIn(BaseModel):
    message: str
    conversation_id: int | None = None


@router.post("/chat")
def chat(payload: ChatIn, db: Session = Depends(get_db)):
    try:
        controller = AgentController(db, conversation_id=payload.conversation_id)
        return controller.handle_user_message(payload.message.strip())
    except AgentError as e:
        return {
            "reply": f"Setup problem: {e}",
            "conversation_id": payload.conversation_id,
            "tool_trace": [],
            "escalated": False,
        }
