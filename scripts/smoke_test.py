"""End-to-end test of the booking stack WITHOUT the LLM.

Proves: tables exist, availability math works, booking commits, a slot fills up once
every staff member who performs the service is busy (same staff can never be
double-booked), reschedule and cancel work. Run:  python scripts/smoke_test.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal
from app.db.models import Appointment
from app.tools import booking


def next_open_day(start: date) -> date:
    d = start
    while d.weekday() == 6:  # Sunday closed
        d += timedelta(days=1)
    return d


def main() -> None:
    db = SessionLocal()
    try:
        business = booking.get_default_business(db)
        if business is None:
            print("FAIL: no business in DB — run scripts/seed_minimal.py first")
            sys.exit(1)
        target = next_open_day(date.today() + timedelta(days=1))
        target_str = target.isoformat()
        print(f"Testing against '{business.name}', date {target_str} ({target.strftime('%A')})\n")

        r = booking.check_availability(db, business.id, "Consultation", target_str, preferred_time="11:00")
        assert r["success"] and r["open"], r
        assert "11:00" in r["available_times"], r
        print(f"1. availability            OK  ({len(r['available_times'])} slots, 11:00 free)")

        # Book the same slot repeatedly: each booking must go to a different staff
        # member, and once all consultation-capable staff are busy the slot must close.
        booked_staff: list[str] = []
        first_ref: str | None = None
        for i in range(1, 6):
            r = booking.book_appointment(
                db, business.id, "Consultation", target_str, "11:00", f"Smoke Tester {i}", f"999999999{i}"
            )
            if r["success"]:
                assert r["staff"] not in booked_staff, f"staff double-booked! {r}"
                booked_staff.append(r["staff"])
                first_ref = first_ref or r["booking_reference"]
                print(f"2. booking #{i}               OK  ({r['staff']}, ref={r['booking_reference']})")
            else:
                assert booked_staff, "first booking should always succeed"
                print(f"2. booking #{i}               OK  (rejected once slot full: '{r['error']}')")
                break
        else:
            raise AssertionError("slot never filled up — overbooking guard not working")
        print(f"   -> slot filled after {len(booked_staff)} booking(s); no staff double-booked")

        assert first_ref
        r = booking.reschedule_appointment(db, business.id, first_ref, target_str, "12:00")
        assert r["success"], r
        print(f"3. reschedule              OK  ({r['summary']})")

        r = booking.cancel_appointment(db, business.id, first_ref)
        assert r["success"], r
        row = db.query(Appointment).filter_by(booking_reference=first_ref).one()
        assert row.status == "cancelled"
        print("4. cancel                  OK  (status=cancelled)")

        print("\nALL SMOKE TESTS PASSED — booking stack works end-to-end.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
