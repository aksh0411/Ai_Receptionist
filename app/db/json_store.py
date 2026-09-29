"""JSON-file store: the single source of truth for all FrontDesk data.

Replaces PostgreSQL for the dev build. Every read/write goes through this module;
data lives in data/clinic.json — human-readable, safe to hand-edit while the
server is stopped, delete the file to factory-reset (it reseeds on next start).

Concurrency: one in-process lock plus atomic writes (temp file + os.replace), so
a crash mid-write can't corrupt the file. This is a single-process dev guard;
a real database returns when the product goes multi-user.
"""

import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "clinic.json"

_LOCK = threading.RLock()
_STATE: dict | None = None

DEFAULT_RULES = {
    "min_notice_minutes": 60,
    "max_advance_days": 30,
    "slot_granularity_minutes": 30,
    "buffer_minutes": 0,
}

SEED_BUSINESS = {
    "name": "SmileCare Dental",
    "slug": "smilecare-dental",
    "timezone": "Asia/Kolkata",
    "phone": "+91 90000 00000",
    "whatsapp_number": None,
    "address": "Shop 4, Sunrise Plaza, MG Road",
    "website_url": None,
}

SEED_HOURS = [  # Mon-Sat 09:00-13:00 and 16:00-20:00; Sunday closed
    {"day_of_week": d, "opens_at": o, "closes_at": c, "is_closed": False}
    for d in range(6)
    for o, c in (("09:00", "13:00"), ("16:00", "20:00"))
]

SEED_STAFF = [
    {"name": "Dr. Aditi Patel", "role": "dentist", "specialty": "root canals, fillings"},
    {"name": "Dr. Rahul Rao", "role": "dentist", "specialty": "extractions, surgery"},
    {"name": "Priya Sharma", "role": "hygienist", "specialty": "cleaning, whitening"},
]

SEED_SERVICES = [
    ("Consultation", 30, 500),
    ("Dental Cleaning (Scaling)", 45, 1200),
    ("Tooth Filling", 60, 2000),
    ("Root Canal Treatment", 90, 5000),
    ("Tooth Extraction", 60, 2500),
    ("Teeth Whitening", 60, 6000),
    ("Braces Consultation", 30, 800),
    ("Follow-up Visit", 15, 200),
]

SEED_ASSIGNMENTS = {
    "Consultation": [0, 1, 2],
    "Dental Cleaning (Scaling)": [0, 2],
    "Tooth Filling": [0, 1],
    "Root Canal Treatment": [0],
    "Tooth Extraction": [1],
    "Teeth Whitening": [2],
    "Braces Consultation": [0, 1],
    "Follow-up Visit": [0, 1],
}

SEED_RULES = {"key": "booking", "value": dict(DEFAULT_RULES)}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_phone(raw: str) -> str:
    """Indian-mobile normalization: digits only, last 10, so '+91 98765 01234' and
    '9876501234' are the same customer."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def init() -> None:
    """Load the store; seed it from SEED_* constants if the file doesn't exist."""
    global _STATE
    with _LOCK:
        if DATA_FILE.exists():
            _STATE = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        else:
            _STATE = _build_seed()
            _write()
            print(f"Seeded fresh store at {DATA_FILE}")


def _build_seed() -> dict:
    business = {"id": 1, "created_at": _now_iso(), **SEED_BUSINESS}
    staff = [
        {"id": i + 1, "business_id": 1, "is_active": True, **s}
        for i, s in enumerate(SEED_STAFF)
    ]
    services = [
        {"id": i + 1, "business_id": 1, "description": None, "buffer_minutes": 0, "is_active": True,
         "name": n, "duration_minutes": d, "price_inr": p}
        for i, (n, d, p) in enumerate(SEED_SERVICES)
    ]
    service_staff = []
    for service in services:
        for staff_idx in SEED_ASSIGNMENTS[service["name"]]:
            service_staff.append(
                {"id": len(service_staff) + 1, "business_id": 1,
                 "service_id": service["id"], "staff_id": staff[staff_idx]["id"]}
            )
    return {
        "businesses": [business],
        "business_hours": [{"id": i + 1, "business_id": 1, **h} for i, h in enumerate(SEED_HOURS)],
        "staff": staff,
        "services": services,
        "service_staff": service_staff,
        "customers": [],
        "appointments": [],
        "business_rules": [{"id": 1, "business_id": 1, **SEED_RULES}],
        "conversations": [],
        "tool_calls": [],
    }


def _write() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_STATE, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def save() -> None:
    """Persist current state. Call after any mutation (mutations hold the lock)."""
    with _LOCK:
        _write()


def _next_id(collection: list) -> int:
    return max((item.get("id", 0) for item in collection), default=0) + 1


# ---------- catalog reads ----------

def get_business() -> dict | None:
    with _LOCK:
        businesses = _STATE["businesses"]
        return businesses[0] if businesses else None


def get_rules(business_id: int) -> dict:
    with _LOCK:
        for rule in _STATE["business_rules"]:
            if rule["business_id"] == business_id and rule["key"] == "booking":
                return {**DEFAULT_RULES, **rule["value"]}
        return dict(DEFAULT_RULES)


def find_service(business_id: int, name: str) -> dict | None:
    target = name.strip().lower()
    with _LOCK:
        active = [s for s in _STATE["services"] if s["business_id"] == business_id and s["is_active"]]
    for service in active:
        if service["name"].lower() == target:
            return service
    for service in active:
        if target in service["name"].lower():
            return service
    return None


def get_services(business_id: int) -> list[dict]:
    with _LOCK:
        return [s for s in _STATE["services"] if s["business_id"] == business_id and s["is_active"]]


def get_hours(business_id: int, day_of_week: int) -> list[dict]:
    with _LOCK:
        return [
            h for h in _STATE["business_hours"]
            if h["business_id"] == business_id
            and h["day_of_week"] == day_of_week
            and not h["is_closed"]
        ]


def get_all_hours(business_id: int) -> list[dict]:
    with _LOCK:
        return [h for h in _STATE["business_hours"] if h["business_id"] == business_id]


def get_staff_for_service(business_id: int, service_id: int) -> list[dict]:
    with _LOCK:
        links = {l["staff_id"] for l in _STATE["service_staff"] if l["service_id"] == service_id}
        return [
            s for s in _STATE["staff"]
            if s["business_id"] == business_id and s["id"] in links and s["is_active"]
        ]


def get_staff_by_id(staff_id: int) -> dict | None:
    with _LOCK:
        return next((s for s in _STATE["staff"] if s["id"] == staff_id), None)


# ---------- customers ----------

def get_customer_by_phone(business_id: int, phone: str) -> dict | None:
    digits = normalize_phone(phone)
    if len(digits) < 10:
        return None
    with _LOCK:
        for customer in _STATE["customers"]:
            if customer["business_id"] == business_id and normalize_phone(customer["phone"]).endswith(digits):
                return customer
    return None


def upsert_customer(business_id: int, name: str, phone: str) -> dict:
    with _LOCK:
        customer = get_customer_by_phone(business_id, phone)
        if customer is None:
            customer = {
                "id": _next_id(_STATE["customers"]),
                "business_id": business_id,
                "name": name.strip(),
                "phone": normalize_phone(phone),
                "email": None,
                "notes": None,
                "created_at": _now_iso(),
            }
            _STATE["customers"].append(customer)
        return customer


# ---------- appointments ----------

def get_appointments(business_id: int) -> list[dict]:
    with _LOCK:
        return [a for a in _STATE["appointments"] if a["business_id"] == business_id]


def get_appointment(booking_reference: str) -> dict | None:
    ref = booking_reference.strip().upper()
    with _LOCK:
        for a in _STATE["appointments"]:
            if a["booking_reference"] == ref:
                return a
    return None


def booking_reference_taken(reference: str) -> bool:
    return get_appointment(reference) is not None


def generate_booking_reference(business_slug: str) -> str:
    prefix = "".join(w[0] for w in business_slug.split("-")[:2]).upper() or "BK"
    for _ in range(10):
        ref = f"{prefix}-{secrets.token_hex(2).upper()}"
        if not booking_reference_taken(ref):
            return ref
    raise RuntimeError("could not generate a unique booking reference")


def add_appointment(appt: dict) -> dict:
    with _LOCK:
        appt = {"id": _next_id(_STATE["appointments"]), **appt}
        _STATE["appointments"].append(appt)
        _write()
        return appt


def update_appointment(booking_reference: str, **fields) -> dict | None:
    with _LOCK:
        appt = get_appointment(booking_reference)
        if appt is None:
            return None
        appt.update(fields)
        _write()
        return appt


# ---------- conversations & tool logs ----------

def create_conversation(business_id: int | None, channel: str) -> dict:
    with _LOCK:
        conversation = {
            "id": _next_id(_STATE["conversations"]),
            "business_id": business_id,
            "channel": channel,
            "customer_id": None,
            "messages": [],
            "escalated": False,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        _STATE["conversations"].append(conversation)
        _write()
        return conversation


def get_conversation(conversation_id: int) -> dict | None:
    with _LOCK:
        for c in _STATE["conversations"]:
            if c["id"] == conversation_id:
                return c
    return None


def save_conversation(conversation: dict) -> None:
    with _LOCK:
        conversation["updated_at"] = _now_iso()
        _write()


def add_tool_call(
    conversation_id: int, tool_name: str, arguments: dict,
    result: dict, success: bool, error: str | None, latency_ms: int,
) -> None:
    with _LOCK:
        _STATE["tool_calls"].append({
            "id": _next_id(_STATE["tool_calls"]),
            "conversation_id": conversation_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "success": success,
            "error": error,
            "latency_ms": latency_ms,
            "created_at": _now_iso(),
        })
        _write()
