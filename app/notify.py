"""Escalation notifications: the human side of escalate_to_human.

Every escalation is appended to data/escalations.log (auditable even with no
integrations). If NOTIFY_WEBHOOK_URL is set — a Telegram bot webhook, Slack
incoming hook, or any JSON endpoint — a short message is POSTed there too.
Notification failure never propagates: worst case, the log line is the record.
"""

import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parents[1] / "data" / "escalations.log"


def notify_escalation(conversation_id: int | None, reason: str) -> None:
    entry = json.dumps(
        {
            "at": datetime.now().isoformat(timespec="seconds"),
            "conversation_id": conversation_id,
            "reason": reason,
        },
        ensure_ascii=False,
    )
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass  # logging must never break the chat

    url = os.getenv("NOTIFY_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"text": f"⚠ Escalation (conversation {conversation_id}): {reason}"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # webhook down ≠ customer-facing failure
