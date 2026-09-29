"""End-to-end test of the booking stack WITHOUT the LLM.

Proves: availability math, booking commits, a slot fills up once every staff member
who performs the service is busy (no staff double-booking), phone lookups with messy
formats, NEAREST-alternative sorting, AM/PM display, reschedule and cancel.
Adapts to whatever bookings already exist in the store.

Run:  python scripts/smoke_test.py
"""

import sys
from datetime import date as date_cls, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import json_store as store
from app.tools import booking


def next_open_day(start: date_cls) -> date_cls:
    d = start
    while d.weekday() == 6:  # Sunday closed
        d += timedelta(days=1)
    return d


def main() -> None:
    store.init()
    business = store.get_business()
    if business is None:
        print("FAIL: no business in store")
        sys.exit(1)
    business_id = business["id"]
    target = next_open_day(date_cls.today() + timedelta(days=1))
    print(f"Testing against '{business['name']}', date {target.isoformat()} ({target.strftime('%A')})\n")

    r = booking.check_availability(business_id, "Consultation", target.isoformat())
    assert r["success"] and r["open"] and r["available_times"], r
    assert r["available_times_display"], r
    assert r["windows_summary"], r
    slot = r["available_times"][0]
    print(f"1. availability            OK  ({len(r['available_times'])} slots; testing {slot}; "
          f"e.g. display '{r['available_times_display'][0]}'; windows: {r['windows_summary']})")

    # NEAREST alternatives: request a time at the very end of the day; the top
    # alternative must be the chronologically LAST slot, not the first of the day.
    r = booking.check_availability(business_id, "Consultation", target.isoformat(), preferred_time="23:00")
    assert r["success"], r
    assert r["requested_time_available"] is False, r
    last_slot = r["available_times"][-1]
    assert r["alternatives"][0] == last_slot, (r["alternatives"], last_slot)
    assert r["alternatives_display"][0].endswith(("AM", "PM")), r
    print(f"2. nearest alternatives    OK  (asked 11:00 PM -> top alternative {r['alternatives_display'][0]}, "
          f"not the first slot of the day)")

    # Book the same slot repeatedly: each booking must go to a different staff
    # member, and once all consultation-capable staff are busy the slot must close.
    booked_staff: list[str] = []
    first_ref: str | None = None
    for i in range(1, 6):
        r = booking.book_appointment(
            business_id, "Consultation", target.isoformat(), slot, f"Smoke Tester {i}", f"999999999{i}"
        )
        if r["success"]:
            assert r["staff"] not in booked_staff, f"staff double-booked! {r}"
            assert r["time_display"].endswith(("AM", "PM")), r
            booked_staff.append(r["staff"])
            first_ref = first_ref or r["booking_reference"]
            print(f"3. booking #{i}               OK  ({r['staff']}, ref={r['booking_reference']})")
        else:
            assert booked_staff, "first booking should always succeed"
            print(f"3. booking #{i}               OK  (rejected once slot full: '{r['error']}')")
            break
    else:
        raise AssertionError("slot never filled up — overbooking guard not working")
    print(f"   -> slot filled after {len(booked_staff)} booking(s); no staff double-booked")

    assert first_ref
    r = booking.lookup_appointments(business_id, "+91 99999 99991")
    assert r["success"] and r["customer"], r
    assert any(a["booking_reference"] == first_ref for a in r["appointments"]), r
    print("4. lookup by phone        OK  ('+91 99999 99991' matched stored digits)")

    r = booking.lookup_customer(business_id, "9-9-9-9-9-9-9-9-9-9-1")
    assert r["success"] and r["customer"]["name"].startswith("Smoke Tester"), r
    print("5. lookup_customer        OK  (noisy phone format normalized)")

    r = booking.lookup_appointments(business_id, "1111122222")
    assert r["success"] and r["customer"] is None and not r["appointments"], r
    print("6. unknown phone          OK  (clean empty result)")

    alt_day = next_open_day(target + timedelta(days=1))
    r = booking.reschedule_appointment(business_id, first_ref, alt_day.isoformat(), "09:00")
    assert r["success"], r
    print(f"7. reschedule              OK  ({r['summary']})")

    r = booking.cancel_appointment(business_id, first_ref)
    assert r["success"], r
    assert store.get_appointment(first_ref)["status"] == "cancelled"
    print("8. cancel                  OK  (status=cancelled in JSON store)")

    print("\nALL SMOKE TESTS PASSED — booking stack works end-to-end on the JSON store.")


if __name__ == "__main__":
    main()
