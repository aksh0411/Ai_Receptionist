"""Booking tools the agent can call.

Contract: every function takes keyword arguments matching the LLM-facing tool spec,
returns a JSON-serializable dict, and never raises for expected failures (reported
as {"success": False, "error": ...}).

The LLM only proposes actions — these functions are the authority that reads and
writes data/store (data/clinic.json). A booking is only "confirmed" after its save
succeeds here.

Times: machine-facing values are 24h "HH:MM"; every human-facing list also carries
a *_display twin in 12-hour AM/PM so the model never has to convert.
"""

from datetime import date as date_cls, datetime, time as time_cls, timedelta

from app.db import json_store as store
from app.db.json_store import normalize_phone

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _fmt_display(hhmm: str) -> str:
    """'09:00' -> '9:00 AM', '14:30' -> '2:30 PM', '00:15' -> '12:15 AM'."""
    hour, minute = map(int, hhmm.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def _date_anchor_error(date_str: str, now: datetime) -> dict:
    """Error payload that lets the model self-correct a bad date immediately."""
    return {
        "success": False,
        "error": (
            f"invalid date '{date_str}'. Today is {now.date().isoformat()}; "
            f"tomorrow is {(now.date() + timedelta(days=1)).isoformat()}."
        ),
    }


def _windows_summary(windows: list[dict]) -> str:
    return ", ".join(
        f"{_fmt_display(w['opens_at'])}-{_fmt_display(w['closes_at'])}" for w in windows
    ) or "closed"


# ---------- availability ----------

def _hours_for_date(business_id: int, d: date_cls) -> list[dict]:
    return store.get_hours(business_id, d.weekday())


def _service_buffer(service: dict, rules: dict) -> timedelta:
    return timedelta(minutes=max(service.get("buffer_minutes", 0), rules["buffer_minutes"]))


def _occupied_for_staff(
    business_id: int, staff_ids: list[int], d: date_cls,
    service_buffers: dict[int, timedelta], exclude_reference: str | None = None,
) -> dict[int, list[tuple[datetime, datetime]]]:
    """Active appointments on date d per staff id, padded by each service's buffer."""
    occupied: dict[int, list[tuple[datetime, datetime]]] = {}
    for a in store.get_appointments(business_id):
        if a["status"] != "booked" or a["staff_id"] not in staff_ids:
            continue
        if exclude_reference and a["booking_reference"] == exclude_reference:
            continue
        start = datetime.fromisoformat(a["start_at"])
        if start.date() != d:
            continue
        end = datetime.fromisoformat(a["end_at"])
        pad = service_buffers.get(a["service_id"], timedelta())
        occupied.setdefault(a["staff_id"], []).append((start - pad, end + pad))
    return occupied


def _compute_slots(
    business_id: int, service: dict, d: date_cls,
    exclude_reference: str | None = None,
) -> list[datetime]:
    """All start times on date d where the service fits opening hours AND at least
    one assigned staff member is free."""
    windows = _hours_for_date(business_id, d)
    staff = store.get_staff_for_service(business_id, service["id"])
    if not windows or not staff:
        return []
    rules = store.get_rules(business_id)
    duration = timedelta(minutes=service["duration_minutes"])
    gran = timedelta(minutes=rules["slot_granularity_minutes"])
    service_buffers = {
        s["id"]: _service_buffer(s, rules) for s in store.get_services(business_id)
    }
    occupied = _occupied_for_staff(
        business_id, [s["id"] for s in staff], d, service_buffers, exclude_reference
    )

    slots: set[datetime] = set()
    for member in staff:
        occ = occupied.get(member["id"], [])
        for window in windows:
            t = datetime.combine(d, time_cls.fromisoformat(window["opens_at"]))
            close = datetime.combine(d, time_cls.fromisoformat(window["closes_at"]))
            while t + duration <= close:
                if all(t >= end or t + duration <= start for start, end in occ):
                    slots.add(t)
                t += gran
    return sorted(slots)


def _slot_payload(business_id: int, service: dict, d: date_cls, slots: list[datetime]) -> dict:
    windows = _hours_for_date(business_id, d)
    return {
        "available_times": [s.strftime("%H:%M") for s in slots],
        "available_times_display": [_fmt_display(s.strftime("%H:%M")) for s in slots],
        "open_windows": [
            {"opens": w["opens_at"], "closes": w["closes_at"], "display": f"{_fmt_display(w['opens_at'])}-{_fmt_display(w['closes_at'])}"}
            for w in windows
        ],
        "windows_summary": _windows_summary(windows),
    }


def check_availability(
    business_id: int,
    service_name: str,
    date: str,
    preferred_time: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    try:
        d = date_cls.fromisoformat(date)
    except ValueError:
        return _date_anchor_error(date, now)

    service = store.find_service(business_id, service_name)
    if not service:
        return {"success": False, "error": f"unknown service '{service_name}'"}

    rules = store.get_rules(business_id)
    if d < now.date():
        return {"success": False, "error": "that date is in the past"}
    if d > now.date() + timedelta(days=rules["max_advance_days"]):
        return {
            "success": False,
            "error": f"bookings are only accepted up to {rules['max_advance_days']} days ahead",
        }

    if not _hours_for_date(business_id, d):
        return {
            "success": True,
            "service": service["name"],
            "date": date,
            "open": False,
            "available_times": [],
            "available_times_display": [],
            "message": f"The clinic is closed on {WEEKDAYS[d.weekday()]}s.",
        }

    slots = [s for s in _compute_slots(business_id, service, d)
             if s >= now + timedelta(minutes=rules["min_notice_minutes"])]
    payload = {
        "success": True,
        "service": service["name"],
        "date": date,
        "open": True,
        "duration_minutes": service["duration_minutes"],
        "price_inr": service["price_inr"],
        **_slot_payload(business_id, service, d, slots),
        "message": (
            f"Open {_windows_summary(_hours_for_date(business_id, d))} on {WEEKDAYS[d.weekday()]}; "
            f"{len(slots)} slot(s) free for {service['name']}."
        ),
    }
    if preferred_time:
        try:
            pref = datetime.combine(d, time_cls.fromisoformat(preferred_time))
        except ValueError:
            payload["preferred_time_note"] = "preferred_time must be 24h HH:MM"
            return payload
        payload["requested_time"] = preferred_time
        payload["requested_time_display"] = _fmt_display(preferred_time)
        payload["requested_time_available"] = any(s == pref for s in slots)
        # The NEAREST alternatives — sorted by distance from the requested time,
        # not just the first few of the day.
        nearest = sorted(slots, key=lambda s: abs((s - pref).total_seconds()))[:5]
        payload["alternatives"] = [s.strftime("%H:%M") for s in nearest]
        payload["alternatives_display"] = [_fmt_display(s.strftime("%H:%M")) for s in nearest]
    return payload


# ---------- booking ----------

def book_appointment(
    business_id: int,
    service_name: str,
    date: str,
    start_time: str,
    customer_name: str,
    customer_phone: str,
    staff_name: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    try:
        d = date_cls.fromisoformat(date)
        start = datetime.combine(d, time_cls.fromisoformat(start_time))
    except ValueError:
        return _date_anchor_error(date, now)

    service = store.find_service(business_id, service_name)
    if not service:
        return {"success": False, "error": f"unknown service '{service_name}'"}

    staff_list = store.get_staff_for_service(business_id, service["id"])
    if not staff_list:
        return {"success": False, "error": "no staff member is assigned to this service"}
    if staff_name:
        wanted = staff_name.strip().lower()
        staff_list = [s for s in staff_list if wanted in s["name"].lower()]
        if not staff_list:
            return {"success": False, "error": f"no staff named '{staff_name}' performs {service['name']}"}

    rules = store.get_rules(business_id)
    end = start + timedelta(minutes=service["duration_minutes"])
    if start < now + timedelta(minutes=rules["min_notice_minutes"]):
        return {"success": False, "error": "that start time violates the minimum-notice rule"}
    if d > now.date() + timedelta(days=rules["max_advance_days"]):
        return {"success": False, "error": "that date is beyond the booking window"}

    # Re-validate against live data right before writing — never trust the model's claim.
    windows = _hours_for_date(business_id, d)
    if not any(
        datetime.combine(d, time_cls.fromisoformat(w["opens_at"])) <= start
        and end <= datetime.combine(d, time_cls.fromisoformat(w["closes_at"]))
        for w in windows
    ):
        return {"success": False, "error": "requested time is outside opening hours"}
    service_buffers = {s["id"]: _service_buffer(s, rules) for s in store.get_services(business_id)}
    occupied = _occupied_for_staff(business_id, [s["id"] for s in staff_list], d, service_buffers)
    free = [s for s in staff_list
            if all(start >= e or end <= st for st, e in occupied.get(s["id"], []))]
    if not free:
        return {
            "success": False,
            "error": "that slot is not available anymore",
            "suggestion": "call check_availability again and offer alternatives",
        }

    digits = normalize_phone(customer_phone)
    if len(digits) < 10:
        return {"success": False, "error": "customer_phone looks invalid — need a 10-digit number"}
    customer = store.upsert_customer(business_id, customer_name, digits)

    business = store.get_business()
    staff = free[0]
    appointment = store.add_appointment({
        "business_id": business_id,
        "customer_id": customer["id"],
        "staff_id": staff["id"],
        "service_id": service["id"],
        "start_at": start.isoformat(timespec="seconds"),
        "end_at": end.isoformat(timespec="seconds"),
        "status": "booked",
        "source": "ai_chat",
        "booking_reference": store.generate_booking_reference(business["slug"]),
        "created_at": now.isoformat(timespec="seconds"),
    })  # add_appointment saves — the save IS the booking

    return {
        "success": True,
        "booking_reference": appointment["booking_reference"],
        "summary": (
            f"{service['name']} on {d.isoformat()} at {start.strftime('%H:%M')} "
            f"with {staff['name']}, Rs.{service['price_inr']}"
        ),
        "customer": customer["name"],
        "staff": staff["name"],
        "service": service["name"],
        "date": d.isoformat(),
        "time": start.strftime("%H:%M"),
        "time_display": _fmt_display(start.strftime("%H:%M")),
        "price_inr": service["price_inr"],
    }


# ---------- cancel / reschedule ----------

def cancel_appointment(business_id: int, booking_reference: str) -> dict:
    appointment = store.get_appointment(booking_reference)
    if not appointment or appointment["business_id"] != business_id:
        return {"success": False, "error": "no appointment found with that booking reference"}
    if appointment["status"] != "booked":
        return {"success": False, "error": f"appointment is already {appointment['status']}"}
    store.update_appointment(appointment["booking_reference"], status="cancelled")
    return {"success": True, "booking_reference": appointment["booking_reference"], "status": "cancelled"}


def reschedule_appointment(
    business_id: int,
    booking_reference: str,
    new_date: str,
    new_time: str,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    try:
        d = date_cls.fromisoformat(new_date)
        start = datetime.combine(d, time_cls.fromisoformat(new_time))
    except ValueError:
        return _date_anchor_error(new_date, now)

    appointment = store.get_appointment(booking_reference)
    if not appointment or appointment["business_id"] != business_id:
        return {"success": False, "error": "no appointment found with that booking reference"}
    if appointment["status"] != "booked":
        return {"success": False, "error": f"cannot reschedule an appointment that is {appointment['status']}"}

    service = next(
        (s for s in store.get_services(business_id) if s["id"] == appointment["service_id"]), None
    )
    rules = store.get_rules(business_id)
    end = start + timedelta(minutes=service["duration_minutes"])
    if start < now + timedelta(minutes=rules["min_notice_minutes"]):
        return {"success": False, "error": "that start time violates the minimum-notice rule"}

    windows = _hours_for_date(business_id, d)
    if not any(
        datetime.combine(d, time_cls.fromisoformat(w["opens_at"])) <= start
        and end <= datetime.combine(d, time_cls.fromisoformat(w["closes_at"]))
        for w in windows
    ):
        return {"success": False, "error": "requested time is outside opening hours"}
    service_buffers = {s["id"]: _service_buffer(s, rules) for s in store.get_services(business_id)}
    occupied = _occupied_for_staff(
        business_id, [appointment["staff_id"]], d, service_buffers,
        exclude_reference=appointment["booking_reference"],
    )
    if not all(start >= e or end <= st for st, e in occupied.get(appointment["staff_id"], [])):
        return {
            "success": False,
            "error": "the new slot is not available for this staff member",
            "suggestion": "call check_availability and offer alternatives",
        }

    old = datetime.fromisoformat(appointment["start_at"]).strftime("%Y-%m-%d %H:%M")
    store.update_appointment(
        appointment["booking_reference"],
        start_at=start.isoformat(timespec="seconds"),
        end_at=end.isoformat(timespec="seconds"),
    )
    return {
        "success": True,
        "booking_reference": appointment["booking_reference"],
        "summary": f"moved from {old} to {d.isoformat()} {start.strftime('%H:%M')}",
    }


# ---------- lookups ----------

def _appointment_cards(appointments: list[dict], now: datetime) -> list[dict]:
    services = {s["id"]: s for s in store.get_services(appointments[0]["business_id"])} if appointments else {}
    staff_by_id = {}
    cards = []
    for a in appointments:
        if a["staff_id"] not in staff_by_id:
            staff_by_id[a["staff_id"]] = store.get_staff_by_id(a["staff_id"])
        start = datetime.fromisoformat(a["start_at"])
        cards.append({
            "booking_reference": a["booking_reference"],
            "service": services.get(a["service_id"], {}).get("name", "unknown"),
            "staff": staff_by_id[a["staff_id"]]["name"] if staff_by_id[a["staff_id"]] else "unknown",
            "date": start.strftime("%Y-%m-%d"),
            "time": start.strftime("%H:%M"),
            "time_display": _fmt_display(start.strftime("%H:%M")),
            "status": a["status"],
            "is_past": start < now,
        })
    return cards


def lookup_appointments(business_id: int, phone: str) -> dict:
    """Find a customer's active appointments by phone — lets customers cancel or
    reschedule without knowing their booking reference."""
    customer = store.get_customer_by_phone(business_id, phone)
    if not customer:
        return {
            "success": True,
            "customer": None,
            "appointments": [],
            "message": "No customer found with that phone number.",
        }
    appointments = [
        a for a in store.get_appointments(business_id)
        if a["customer_id"] == customer["id"] and a["status"] == "booked"
    ]
    appointments.sort(key=lambda a: a["start_at"])
    return {
        "success": True,
        "customer": {"name": customer["name"], "phone": customer["phone"]},
        "appointments": _appointment_cards(appointments, datetime.now()),
        "message": (
            f"{len(appointments)} active appointment(s) for {customer['name']}."
            if appointments
            else f"Found {customer['name']}, but no active appointments."
        ),
    }


def lookup_customer(business_id: int, phone: str) -> dict:
    """Customer profile + appointments, for greeting returning patients."""
    customer = store.get_customer_by_phone(business_id, phone)
    if not customer:
        return {
            "success": True,
            "customer": None,
            "message": "No customer found with that phone number.",
        }
    appointments = [
        a for a in store.get_appointments(business_id)
        if a["customer_id"] == customer["id"] and a["status"] == "booked"
    ]
    appointments.sort(key=lambda a: a["start_at"])
    return {
        "success": True,
        "customer": {
            "name": customer["name"],
            "phone": customer["phone"],
            "email": customer["email"],
            "notes": customer["notes"],
            "first_seen": customer.get("created_at", "")[:10] or None,
        },
        "appointments": _appointment_cards(appointments, datetime.now()),
    }


# ---------- info / escalation ----------

def get_business_info(business_id: int, topic: str = "all") -> dict:
    business = store.get_business()
    out = {"success": True, "name": business["name"], "address": business["address"], "phone": business["phone"]}
    if topic in ("hours", "all"):
        hours: dict[str, list[str]] = {}
        for h in store.get_all_hours(business_id):
            hours.setdefault(WEEKDAYS[h["day_of_week"]], []).append(
                "closed" if h["is_closed"] else f"{h['opens_at']}-{h['closes_at']}"
            )
        out["hours"] = hours
    if topic in ("services", "all"):
        out["services"] = [
            {"name": s["name"], "duration_minutes": s["duration_minutes"], "price_inr": s["price_inr"]}
            for s in sorted(store.get_services(business_id), key=lambda s: s["name"])
        ]
    if topic in ("rules", "all"):
        out["rules"] = store.get_rules(business_id)
    return out


def escalate_to_human(
    conversation_id: int,
    reason: str = "",
    business_id: int | None = None,
) -> dict:
    conversation = store.get_conversation(conversation_id)
    if conversation:
        conversation["escalated"] = True
        store.save_conversation(conversation)
    return {
        "success": True,
        "handoff": True,
        "message": (
            "Flagged for staff follow-up. Tell the customer the team will contact them "
            "shortly; if it sounds like a medical emergency, advise them to visit the "
            "nearest hospital immediately."
        ),
        "reason": reason,
    }


def get_default_business_id() -> int | None:
    business = store.get_business()
    return business["id"] if business else None
