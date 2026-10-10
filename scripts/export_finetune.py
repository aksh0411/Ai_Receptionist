"""Export the conversation logs as a fine-tuning dataset (TRL chat format).

The store already logs every conversation (with the versioned system prompt at
messages[0] — policy version included) and every tool call with success flags.
This script filters to training-worthy flows, masks customer phone numbers
(PII), and writes JSONL the Colab QLoRA notebook consumes directly.

Run:  .venv/Scripts/python.exe scripts/export_finetune.py
Out:  training/dataset_policy-v<policy>.jsonl  (+ stats on stdout)

Filter: conversations with >=1 successful tool call, no error replies, and at
least 4 messages. Failed/aborted conversations are excluded — the fine-tune
learns how a GOOD receptionist talks, not how it fails.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import policy  # noqa: E402  (loads + validates dialogue_policy.json)

STORE = Path(__file__).resolve().parents[1] / "data" / "clinic.json"
OUT_DIR = Path(__file__).resolve().parents[1] / "training"

# Any standalone 10-digit Indian mobile (with optional +91/0 prefix, separators).
PHONE_RE = re.compile(r"(?:\+?91[\s-]?|0)?([6-9]\d{4})[\s-]?(\d{5})(?!\d)")
MASK = "XXXXXXXXXX"


def mask_phones(text: str) -> str:
    """Mask phone numbers but keep the surrounding format (e.g. '+91 XXXXXXXXXX'),
    so the model keeps number-formatting behaviour without learning real PII."""
    return PHONE_RE.sub(lambda m: m.group(0).replace(m.group(1), "XXXXX").replace(m.group(2), "XXXXX"), text)


def walk_mask(obj):
    if isinstance(obj, str):
        return mask_phones(obj)
    if isinstance(obj, list):
        return [walk_mask(x) for x in obj]
    if isinstance(obj, dict):
        return {k: walk_mask(v) for k, v in obj.items()}
    return obj


def is_training_worthy(conversation: dict, tool_calls: list[dict]) -> tuple[bool, str]:
    messages = conversation.get("messages") or []
    if len(messages) < 4:
        return False, "too short"
    for m in messages:
        if m.get("role") == "assistant":
            content = m.get("content") or ""
            if any(tag in content for tag in ("Agent error", "LLM is not configured", "Setup problem")):
                return False, "error reply"
    successful = [t for t in tool_calls if t["conversation_id"] == conversation["id"] and t["success"]]
    if not successful:
        return False, "no successful tool call"
    return True, f"{len(successful)} successful tool calls"


def main() -> None:
    policy_version = policy.version()
    store = json.loads(STORE.read_text(encoding="utf-8"))
    conversations = store.get("conversations", [])
    tool_calls = store.get("tool_calls", [])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"dataset_policy-v{policy_version}.jsonl"

    kept, skipped = 0, {}
    with out.open("w", encoding="utf-8") as f:
        for conversation in conversations:
            ok, reason = is_training_worthy(conversation, tool_calls)
            if not ok:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            messages = walk_mask(conversation["messages"])
            f.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")
            kept += 1

    n_bookings = sum(
        1 for t in tool_calls if t["tool_name"] == "book_appointment" and t["success"]
    )
    print(f"policy version : v{policy_version}")
    print(f"conversations  : {len(conversations)} total -> {kept} exported")
    print(f"skipped        : {skipped or 'none'}")
    print(f"successful bookings in store: {n_bookings}")
    print(f"dataset        : {out}")
    if kept < 50:
        print("\nNOTE: <50 examples is enough to smoke-test the pipeline, not to train —")
        print("collect live conversations first (eval battery runs also count).")


if __name__ == "__main__":
    main()
