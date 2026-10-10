"""System prompt assembled from live store state — never hardcoded business facts."""

from datetime import datetime, timedelta

from app.agent import policy
from app.db import json_store as store
from app.tools.booking import WEEKDAYS


def build_system_prompt(business: dict, today: datetime | None = None) -> str:
    today = today or store.clinic_now()
    business_id = business["id"]
    rules = store.get_rules(business_id)

    service_lines = [
        f"- {s['name']}: {s['duration_minutes']} min, Rs.{s['price_inr']}"
        for s in sorted(store.get_services(business_id), key=lambda s: s["name"])
    ]

    by_day: dict[int, list] = {}
    for h in store.get_all_hours(business_id):
        by_day.setdefault(h["day_of_week"], []).append(h)
    hour_lines = []
    for day in range(7):
        windows = by_day.get(day)
        if not windows:
            hour_lines.append(f"{WEEKDAYS[day]}: closed")
        else:
            hour_lines.append(
                f"{WEEKDAYS[day]}: " + ", ".join(f"{w['opens_at']}-{w['closes_at']}" for w in windows)
            )

    return (
        f"You are the front-desk receptionist for {business['name']}, chatting with customers.\n\n"
        f"DATE ANCHORS (clinic local time): yesterday {today.date() - timedelta(days=1):%Y-%m-%d}, "
        f"today {today.date():%Y-%m-%d} ({today.strftime('%A')}), "
        f"tomorrow {today.date() + timedelta(days=1):%Y-%m-%d}, "
        f"day after tomorrow {today.date() + timedelta(days=2):%Y-%m-%d}. "
        "When the customer says today/tomorrow/etc., use these anchors directly — do not do "
        "date arithmetic yourself. Pass dates to tools as YYYY-MM-DD.\n\n"
        f"ADDRESS: {business.get('address') or 'not set'}\n"
        f"CLINIC PHONE: {business.get('phone') or 'not set'}\n\n"
        "OPENING HOURS (24h):\n" + "\n".join(hour_lines) + "\n\n"
        "SERVICES:\n" + ("\n".join(service_lines) or "- none configured") + "\n\n"
        "BOOKING RULES: minimum notice "
        f"{rules['min_notice_minutes']} minutes; bookings accepted up to "
        f"{rules['max_advance_days']} days ahead.\n\n"
        # Behavior comes from dialogue_policy.json (versioned, validated at load):
        # editing how the agent talks is a JSON edit, never a code change.
        f"HOW TO WORK (dialogue policy v{policy.version()}):\n{policy.rendered()}"
    )
