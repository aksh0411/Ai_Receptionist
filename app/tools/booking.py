"""Booking tools the agent can call.

Contract: every function takes (db, business_id, ...) plus tool arguments as keyword
arguments, returns a JSON-serializable dict, and never raises for expected failures
(they are reported as {"success": False, "error": ...}).

The LLM only proposes actions — these functions are the authority that talks to the
database. A booking is only "confirmed" after its COMMIT succeeds here.
"""

import secrets
from datetime import date as date_cls, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Appointment,
    Business,
    BusinessHour,
    BusinessRule,
    ConversationLog,
    Customer,
    Service,
    ServiceStaff,
    Staff,
)

BOOKING_RULES_KEY = "booking"

DEFAULT_RULES = {
    "min_notice_minutes": 60,
    "max_advance_days": 30,
    "slot_granularity_minutes": 30,
    "buffer_minutes": 0,
}

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _date_anchor_error(date_str: str, now: datetime) -> dict:
    """Error payload that lets the model self-correct a bad date immediately."""
    return {
        "success": False,
        "error": (
            f"invalid date '{date_str}'. Today is {now.date().isoformat()}; "
            f"tomorrow is {(now.date() + timedelta(days=1)).isoformat()}."
        ),
    }


def _normalize_phone(raw: str) -> str:
    """Indian-mobile normalization: digits only, last 10, so '+91 98765 01234' and
    '9876501234' are the same customer."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def _customer_by_phone(db: Session, business_id: int, phone: str) -> Customer | None:
    digits = _normalize_phone(phone)
    if len(digits) < 10:
        return None
    return db.scalar(
        select(Customer).where(
            Customer.business_id == business_id,
            func.regexp_replace(Customer.phone, r"\D", "", "g").like(f"%{digits}"),
        )
    )


def _appointments_for_customer(
    db: Session, business_id: int, customer_id: int, now: datetime
) -> list[dict]:
    appointments = db.scalars(
        select(Appointment)
        .where(
            Appointment.business_id == business_id,
            Appointment.customer_id == customer_id,
            Appointment.status == "booked",
        )
        .order_by(Appointment.start_at)
    ).all()
    out = []
    for a in appointments:
        service = db.get(Service, a.service_id)
        staff = db.get(Staff, a.staff_id)
        out.append(
            {
                "booking_reference": a.booking_reference,
                "service": service.name,
                "staff": staff.name,
                "date": a.start_at.strftime("%Y-%m-%d"),
                "time": a.start_at.strftime("%H:%M"),
                "status": a.status,
                "is_past": a.start_at < now,
            }
        )
    return out


def lookup_appointments(db: Session, business_id: int, phone: str) -> dict:
    """Find a customer's active appointments by phone — lets customers cancel or
    reschedule without knowing their booking reference."""
    customer = _customer_by_phone(db, business_id, phone)
    if not customer:
        return {
            "success": True,
            "customer": None,
            "appointments": [],
            "message": "No customer found with that phone number.",
        }
    appointments = _appointments_for_customer(db, business_id, customer.id, datetime.now())
    return {
        "success": True,
        "customer": {"name": customer.name, "phone": customer.phone},
        "appointments": appointments,
        "message": (
            f"{len(appointments)} active appointment(s) for {customer.name}."
            if appointments
            else f"Found {customer.name}, but no active appointments."
        ),
    }


def lookup_customer(db: Session, business_id: int, phone: str) -> dict:
    """Customer profile + appointments, for greeting returning patients."""
    customer = _customer_by_phone(db, business_id, phone)
    if not customer:
        return {
            "success": True,
            "customer": None,
            "message": "No customer found with that phone number.",
        }
    appointments = _appointments_for_customer(db, business_id, customer.id, datetime.now())
    return {
        "success": True,
        "customer": {
            "name": customer.name,
            "phone": customer.phone,
            "email": customer.email,
            "notes": customer.notes,
            "first_seen": customer.created_at.strftime("%Y-%m-%d") if customer.created_at else None,
        },
        "appointments": appointments,
    }


def get_default_business(db: Session) -> Business | None:
    """Single-clinic dev mode: everything operates on the first business row."""
    return db.scalar(select(Business).order_by(Business.id).limit(1))


def get_rules(db: Session, business_id: int) -> dict:
    rule = db.scalar(
        select(BusinessRule).where(
            BusinessRule.business_id == business_id,
            BusinessRule.key == BOOKING_RULES_KEY,
        )
    )
    return {**DEFAULT_RULES, **(rule.value if rule else {})}


def _find_service(db: Session, business_id: int, name: str) -> Service | None:
    services = db.scalars(
        select(Service).where(Service.business_id == business_id, Service.is_active.is_(True))
    ).all()
    target = name.strip().lower()
    for service in services:
        if service.name.lower() == target:
            return service
    for service in services:
        if target in service.name.lower():
            return service
    return None


def _hours_for_date(db: Session, business_id: int, d: date_cls) -> list[tuple[time, time]]:
    windows = db.scalars(
        select(BusinessHour).where(
            BusinessHour.business_id == business_id,
            BusinessHour.day_of_week == d.weekday(),
            BusinessHour.is_closed.is_(False),
        )
    ).all()
    return [(w.opens_at, w.closes_at) for w in windows]


def _staff_for_service(db: Session, business_id: int, service_id: int) -> list[Staff]:
    return list(
        db.execute(
            select(Staff)
            .join(ServiceStaff, ServiceStaff.staff_id == Staff.id)
            .where(
                ServiceStaff.service_id == service_id,
                ServiceStaff.business_id == business_id,
                Staff.is_active.is_(True),
            )
        ).scalars()
    )


def _booked_intervals(
    db: Session,
    business_id: int,
    staff_ids: list[int],
    d: date_cls,
    exclude_appointment_id: int | None = None,
) -> dict[int, list[tuple[datetime, datetime]]]:
    """Active appointments on date d per staff id, padded by buffer minutes."""
    day_start = datetime.combine(d, time.min)
    q = select(Appointment).where(
        Appointment.business_id == business_id,
        Appointment.staff_id.in_(staff_ids),
        Appointment.status == "booked",
        Appointment.start_at >= day_start,
        Appointment.start_at < day_start + timedelta(days=1),
    )
    if exclude_appointment_id:
        q = q.where(Appointment.id != exclude_appointment_id)
    appointments = db.scalars(q).all()
    services = {
        s.id: s
        for s in db.scalars(select(Service).where(Service.business_id == business_id)).all()
    }
    global_buffer = timedelta(minutes=get_rules(db, business_id)["buffer_minutes"])
    occupied: dict[int, list[tuple[datetime, datetime]]] = {}
    for a in appointments:
        svc = services.get(a.service_id)
        pad = max(timedelta(minutes=svc.buffer_minutes if svc else 0), global_buffer)
        occupied.setdefault(a.staff_id, []).append((a.start_at - pad, a.end_at + pad))
    return occupied


def _compute_slots(
    db: Session, business_id: int, service: Service, d: date_cls
) -> list[datetime]:
    """All start times on date d where the service fits opening hours AND at least
    one assigned staff member is free."""
    windows = _hours_for_date(db, business_id, d)
    staff = _staff_for_service(db, business_id, service.id)
    if not windows or not staff:
        return []
    duration = timedelta(minutes=service.duration_minutes)
    gran = timedelta(minutes=get_rules(db, business_id)["slot_granularity_minutes"])
    occupied = _booked_intervals(db, business_id, [s.id for s in staff], d)

    slots: set[datetime] = set()
    for member in staff:
        occ = occupied.get(member.id, [])
        for opens_at, closes_at in windows:
            t = datetime.combine(d, opens_at)
            close = datetime.combine(d, closes_at)
            while t + duration <= close:
                if all(t >= end or t + duration <= start for start, end in occ):
                    slots.add(t)
                t += gran
    return sorted(slots)


def check_availability(
    db: Session,
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

    service = _find_service(db, business_id, service_name)
    if not service:
        return {"success": False, "error": f"unknown service '{service_name}'"}

    rules = get_rules(db, business_id)
    if d < now.date():
        return {"success": False, "error": "that date is in the past"}
    if d > now.date() + timedelta(days=rules["max_advance_days"]):
        return {
            "success": False,
            "error": f"bookings are only accepted up to {rules['max_advance_days']} days ahead",
        }

    if not _hours_for_date(db, business_id, d):
        return {
            "success": True,
            "service": service.name,
            "date": date_str,
            "open": False,
            "available_times": [],
            "message": f"The clinic is closed on {WEEKDAYS[d.weekday()]}s.",
        }

    slots = [s for s in _compute_slots(db, business_id, service, d)
             if s >= now + timedelta(minutes=rules["min_notice_minutes"])]
    payload = {
        "success": True,
        "service": service.name,
        "date": date,
        "open": True,
        "duration_minutes": service.duration_minutes,
        "price_inr": service.price_inr,
        "available_times": [s.strftime("%H:%M") for s in slots],
    }
    if preferred_time:
        try:
            pref = datetime.combine(d, datetime.strptime(preferred_time, "%H:%M").time())
        except ValueError:
            payload["preferred_time_note"] = "preferred_time must be HH:MM"
            return payload
        payload["requested_time"] = preferred_time
        payload["requested_time_available"] = any(s == pref for s in slots)
        payload["alternatives"] = [s.strftime("%H:%M") for s in slots if s != pref][:5]
    return payload


def _generate_reference(db: Session, business: Business) -> str:
    prefix = "".join(w[0] for w in business.slug.split("-")[:2]).upper() or "BK"
    for _ in range(10):
        ref = f"{prefix}-{secrets.token_hex(2).upper()}"
        if not db.scalar(select(Appointment.id).where(Appointment.booking_reference == ref)):
            return ref
    raise RuntimeError("could not generate a unique booking reference")


def book_appointment(
    db: Session,
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
        start = datetime.combine(d, datetime.strptime(start_time, "%H:%M").time())
    except ValueError:
        return _date_anchor_error(date, now)

    service = _find_service(db, business_id, service_name)
    if not service:
        return {"success": False, "error": f"unknown service '{service_name}'"}

    staff_list = _staff_for_service(db, business_id, service.id)
    if not staff_list:
        return {"success": False, "error": "no staff member is assigned to this service"}
    if staff_name:
        wanted = staff_name.strip().lower()
        staff_list = [s for s in staff_list if wanted in s.name.lower()]
        if not staff_list:
            return {"success": False, "error": f"no staff named '{staff_name}' performs {service.name}"}

    rules = get_rules(db, business_id)
    end = start + timedelta(minutes=service.duration_minutes)
    if start < now + timedelta(minutes=rules["min_notice_minutes"]):
        return {"success": False, "error": "that start time violates the minimum-notice rule"}
    if d > now.date() + timedelta(days=rules["max_advance_days"]):
        return {"success": False, "error": "that date is beyond the booking window"}

    # Re-validate against live data right before writing — never trust the model's claim.
    windows = _hours_for_date(db, business_id, d)
    if not any(datetime.combine(d, o) <= start and end <= datetime.combine(d, c) for o, c in windows):
        return {"success": False, "error": "requested time is outside opening hours"}
    occupied = _booked_intervals(db, business_id, [s.id for s in staff_list], d)
    free = [s for s in staff_list
            if all(start >= e or end <= st for st, e in occupied.get(s.id, []))]
    if not free:
        return {
            "success": False,
            "error": "that slot is not available anymore",
            "suggestion": "call check_availability again and offer alternatives",
        }

    digits = _normalize_phone(customer_phone)
    if len(digits) < 10:
        return {"success": False, "error": "customer_phone looks invalid — need a 10-digit number"}
    customer = _customer_by_phone(db, business_id, digits)
    if not customer:
        customer = Customer(business_id=business_id, name=customer_name.strip(), phone=digits)
        db.add(customer)
        db.flush()

    business = db.get(Business, business_id)
    staff = free[0]
    appointment = Appointment(
        business_id=business_id,
        customer_id=customer.id,
        staff_id=staff.id,
        service_id=service.id,
        start_at=start,
        end_at=end,
        status="booked",
        source="ai_chat",
        booking_reference=_generate_reference(db, business),
    )
    db.add(appointment)
    db.commit()  # the commit IS the booking — nothing is "confirmed" before this succeeds

    return {
        "success": True,
        "booking_reference": appointment.booking_reference,
        "summary": (
            f"{service.name} on {d.isoformat()} at {start.strftime('%H:%M')} "
            f"with {staff.name}, Rs.{service.price_inr}"
        ),
        "customer": customer.name,
        "staff": staff.name,
        "service": service.name,
        "date": d.isoformat(),
        "time": start.strftime("%H:%M"),
        "price_inr": service.price_inr,
    }


def cancel_appointment(db: Session, business_id: int, booking_reference: str) -> dict:
    appointment = db.scalar(
        select(Appointment).where(
            Appointment.business_id == business_id,
            Appointment.booking_reference == booking_reference.strip().upper(),
        )
    )
    if not appointment:
        return {"success": False, "error": "no appointment found with that booking reference"}
    if appointment.status != "booked":
        return {"success": False, "error": f"appointment is already {appointment.status}"}
    appointment.status = "cancelled"
    db.commit()
    return {"success": True, "booking_reference": appointment.booking_reference, "status": "cancelled"}


def reschedule_appointment(
    db: Session,
    business_id: int,
    booking_reference: str,
    new_date: str,
    new_time: str,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    try:
        d = date_cls.fromisoformat(new_date)
        start = datetime.combine(d, datetime.strptime(new_time, "%H:%M").time())
    except ValueError:
        return _date_anchor_error(new_date, now)

    appointment = db.scalar(
        select(Appointment).where(
            Appointment.business_id == business_id,
            Appointment.booking_reference == booking_reference.strip().upper(),
        )
    )
    if not appointment:
        return {"success": False, "error": "no appointment found with that booking reference"}
    if appointment.status != "booked":
        return {"success": False, "error": f"cannot reschedule an appointment that is {appointment.status}"}

    service = db.get(Service, appointment.service_id)
    rules = get_rules(db, business_id)
    end = start + timedelta(minutes=service.duration_minutes)
    if start < now + timedelta(minutes=rules["min_notice_minutes"]):
        return {"success": False, "error": "that start time violates the minimum-notice rule"}

    windows = _hours_for_date(db, business_id, d)
    if not any(datetime.combine(d, o) <= start and end <= datetime.combine(d, c) for o, c in windows):
        return {"success": False, "error": "requested time is outside opening hours"}
    occupied = _booked_intervals(
        db, business_id, [appointment.staff_id], d, exclude_appointment_id=appointment.id
    )
    if not all(start >= e or end <= st for st, e in occupied.get(appointment.staff_id, [])):
        return {
            "success": False,
            "error": "the new slot is not available for this staff member",
            "suggestion": "call check_availability and offer alternatives",
        }

    old = f"{appointment.start_at:%Y-%m-%d %H:%M}"
    appointment.start_at = start
    appointment.end_at = end
    db.commit()
    return {
        "success": True,
        "booking_reference": appointment.booking_reference,
        "summary": f"moved from {old} to {d.isoformat()} {start.strftime('%H:%M')}",
    }


def get_business_info(db: Session, business_id: int, topic: str = "all") -> dict:
    business = db.get(Business, business_id)
    out = {
        "success": True,
        "name": business.name,
        "address": business.address,
        "phone": business.phone,
    }
    if topic in ("hours", "all"):
        rows = db.scalars(
            select(BusinessHour)
            .where(BusinessHour.business_id == business_id)
            .order_by(BusinessHour.day_of_week, BusinessHour.opens_at)
        ).all()
        hours: dict[str, list[str]] = {}
        for r in rows:
            hours.setdefault(WEEKDAYS[r.day_of_week], []).append(
                "closed" if r.is_closed else f"{r.opens_at:%H:%M}-{r.closes_at:%H:%M}"
            )
        out["hours"] = hours
    if topic in ("services", "all"):
        out["services"] = [
            {
                "name": s.name,
                "duration_minutes": s.duration_minutes,
                "price_inr": s.price_inr,
            }
            for s in db.scalars(
                select(Service)
                .where(Service.business_id == business_id, Service.is_active.is_(True))
                .order_by(Service.name)
            ).all()
        ]
    if topic in ("rules", "all"):
        out["rules"] = get_rules(db, business_id)
    return out


def escalate_to_human(
    db: Session,
    conversation_id: int,
    reason: str = "",
    business_id: int | None = None,
) -> dict:
    conversation = db.get(ConversationLog, conversation_id)
    if conversation:
        conversation.escalated = True
        db.commit()
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
