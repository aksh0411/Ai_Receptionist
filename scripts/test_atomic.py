"""Store-level tests for the atomic booking operations.

Proves: concurrent booking of one staff slot has exactly one winner (race closed),
a customer cannot hold two overlapping appointments (dedup), double-submits are
safe, reschedules exclude themselves but respect staff/customer conflicts.

Run:  .venv/Scripts/python.exe scripts/test_atomic.py
Uses a temp data file — never touches data/clinic.json.
"""

import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import json_store as store

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "OK  " if cond else "FAIL"
    print(f"{mark} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    store.DATA_FILE = tmp / "clinic.json"
    store.init()  # seeds a fresh store in the temp location

    start = datetime(2026, 10, 6, 9, 0)
    end = start + timedelta(minutes=30)

    # 1. race: 8 threads, ONE staff member, same slot -> exactly 1 booking
    results: list[dict] = []
    lock = threading.Lock()

    def book_race(i: int) -> None:
        r = store.try_book(1, customer_id=100 + i, service_id=1, start=start, end=end,
                           candidate_staff_ids=[1])
        with lock:
            results.append(r)

    threads = [threading.Thread(target=book_race, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r["ok"]]
    check("concurrent single-staff booking -> exactly 1 winner", len(winners) == 1,
          f"{len(winners)}/8 succeeded")
    ref1 = winners[0]["appointment"]["booking_reference"]

    # 2. double-submit: same customer, 8 racing requests, 3 staff candidates -> exactly 1
    results.clear()

    def book_same(i: int) -> None:
        r = store.try_book(1, customer_id=999, service_id=1,
                           start=start.replace(hour=16), end=start.replace(hour=16) + timedelta(minutes=30),
                           candidate_staff_ids=[1, 2, 3])
        with lock:
            results.append(r)

    threads = [threading.Thread(target=book_same, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r["ok"]]
    check("concurrent same-customer double-submit -> exactly 1 booking", len(winners) == 1,
          f"{len(winners)}/8 succeeded")

    # 3. per-customer dedup: same customer, overlapping time, DIFFERENT staff -> rejected
    r = store.try_book(1, customer_id=100, service_id=1,
                       start=start + timedelta(minutes=15), end=start + timedelta(minutes=45),
                       candidate_staff_ids=[2])
    check("same customer overlapping slot on other staff rejected",
          not r["ok"] and r["reason"] == "customer_busy", str(r.get("reason")))

    # 4. other customer, same overlapping slot on that other staff -> allowed
    r = store.try_book(1, customer_id=200, service_id=1,
                       start=start + timedelta(minutes=15), end=start + timedelta(minutes=45),
                       candidate_staff_ids=[2])
    check("different customer overlapping slot allowed", r["ok"], str(r.get("reason")))
    ref2 = r["appointment"]["booking_reference"]

    # 5. reschedule overlapping its OWN old time -> allowed (self-excluded)
    r = store.try_reschedule(ref1, new_start=start + timedelta(minutes=10),
                             new_end=start + timedelta(minutes=40))
    check("reschedule overlapping own old time allowed", r["ok"], str(r.get("reason")))

    # 6. reschedule onto another booking on the same staff -> slot_taken
    r = store.try_book(1, customer_id=300, service_id=1,
                       start=start.replace(hour=10), end=start.replace(hour=10) + timedelta(minutes=30),
                       candidate_staff_ids=[1])
    check("setup: second booking on staff 1", r["ok"], str(r.get("reason")))
    ref3 = r["appointment"]["booking_reference"]
    # ref1 now sits at 9:10-9:40 on staff 1; move ref3 (10:00) into 9:20-9:50 -> clash
    r = store.try_reschedule(ref3, new_start=start + timedelta(minutes=20),
                             new_end=start + timedelta(minutes=50))
    check("reschedule onto occupied staff slot rejected",
          not r["ok"] and r["reason"] == "slot_taken", str(r.get("reason")))

    # 7. reschedule into the customer's OTHER appointment (staff otherwise free) -> customer_busy
    r = store.try_book(1, customer_id=200, service_id=1,
                       start=start.replace(hour=11), end=start.replace(hour=11) + timedelta(minutes=30),
                       candidate_staff_ids=[3])  # staff 3 is free at 11:00
    check("setup: customer 200 second booking on staff 3", r["ok"], str(r.get("reason")))
    ref4 = r["appointment"]["booking_reference"]
    # move to 9:20-9:50: staff 3 free there, but customer 200 holds 9:15-9:45 (ref2)
    r = store.try_reschedule(ref4, new_start=start + timedelta(minutes=20),
                             new_end=start + timedelta(minutes=50))
    check("reschedule into own customer's other appointment rejected",
          not r["ok"] and r["reason"] == "customer_busy", str(r.get("reason")))

    # 8. reference uniqueness sanity
    appts = store.get_appointments(1)
    refs = {a["booking_reference"] for a in appts}
    check("booking references unique", len(refs) == len(appts))

    # 9. backup rotation: every write leaves a one-generation .bak beside the data file
    bak = store.DATA_FILE.parent / (store.DATA_FILE.name + ".bak")
    check("backup file created on write", bak.exists())

    # 10. retention caps trim oldest-first
    store.MAX_TOOL_CALLS = 5
    for i in range(7):
        store.add_tool_call(1, "check_availability", {}, {"success": True}, True, None, 1)
    check("tool_calls capped", len(store._STATE["tool_calls"]) <= 5,
          f"{len(store._STATE['tool_calls'])} kept of 7")
    store.MAX_CONVERSATIONS = 3
    for i in range(5):
        store.create_conversation(1, "test")
    check("conversations capped", len(store._STATE["conversations"]) <= 3,
          f"{len(store._STATE['conversations'])} kept of 5")

    # 11. name correction on phone reuse (shared/family phones)
    c1 = store.upsert_customer(1, "Original Name", "9000011111")
    c2 = store.upsert_customer(1, "Corrected Name", "+91 90000 11111")
    check("name updated on phone reuse", c1["id"] == c2["id"] and c2["name"] == "Corrected Name",
          f"id={c2['id']} name={c2['name']}")

    # 12. clinic_now returns naive wall-clock time in the clinic's timezone
    now = store.clinic_now()
    check("clinic_now returns naive datetime", isinstance(now, datetime) and now.tzinfo is None,
          str(now))

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} test(s): {', '.join(FAILURES)}")
        sys.exit(1)
    print("ALL ATOMIC STORE TESTS PASSED")


if __name__ == "__main__":
    main()
