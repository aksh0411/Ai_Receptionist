"""Create tables + one minimal clinic so the agent has real data to operate on.

Run:  python scripts/seed_minimal.py
Safe to re-run — skips if the business already exists.
"""

import sys
from datetime import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base, SessionLocal, engine
from app.db.models import Business, BusinessHour, BusinessRule, Service, ServiceStaff, Staff

SLUG = "smilecare-dental"

SERVICES = [
    ("Consultation", 30, 500),
    ("Dental Cleaning (Scaling)", 45, 1200),
    ("Tooth Filling", 60, 2000),
    ("Root Canal Treatment", 90, 5000),
    ("Tooth Extraction", 60, 2500),
    ("Teeth Whitening", 60, 6000),
    ("Braces Consultation", 30, 800),
    ("Follow-up Visit", 15, 200),
]

# (staff name, role, specialty)
STAFF = [
    ("Dr. Aditi Patel", "dentist", "root canals, fillings"),
    ("Dr. Rahul Rao", "dentist", "extractions, surgery"),
    ("Priya Sharma", "hygienist", "cleaning, whitening"),
]

# service name -> staff indexes that can perform it
ASSIGNMENTS = {
    "Consultation": [0, 1, 2],
    "Dental Cleaning (Scaling)": [0, 2],
    "Tooth Filling": [0, 1],
    "Root Canal Treatment": [0],
    "Tooth Extraction": [1],
    "Teeth Whitening": [2],
    "Braces Consultation": [0, 1],
    "Follow-up Visit": [0, 1],
}


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = db.query(Business).filter_by(slug=SLUG).one_or_none()
        if existing:
            print(f"Business '{SLUG}' already exists (id={existing.id}) — nothing to do.")
            return

        business = Business(
            name="SmileCare Dental",
            slug=SLUG,
            phone="+91 90000 00000",
            address="Shop 4, Sunrise Plaza, MG Road",
            timezone="Asia/Kolkata",
        )
        db.add(business)
        db.flush()

        # Mon–Sat 09:00–13:00 and 16:00–20:00; Sunday closed
        for day in range(6):
            for opens, closes in (("09:00", "13:00"), ("16:00", "20:00")):
                db.add(
                    BusinessHour(
                        business_id=business.id,
                        day_of_week=day,
                        opens_at=time.fromisoformat(opens),
                        closes_at=time.fromisoformat(closes),
                    )
                )

        staff = [
            Staff(business_id=business.id, name=name, role=role, specialty=specialty)
            for name, role, specialty in STAFF
        ]
        db.add_all(staff)
        db.flush()

        services = {
            name: Service(business_id=business.id, name=name, duration_minutes=d, price_inr=p)
            for name, d, p in SERVICES
        }
        db.add_all(services.values())
        db.flush()

        for service_name, staff_indexes in ASSIGNMENTS.items():
            for idx in staff_indexes:
                db.add(
                    ServiceStaff(
                        business_id=business.id,
                        service_id=services[service_name].id,
                        staff_id=staff[idx].id,
                    )
                )

        db.add(
            BusinessRule(
                business_id=business.id,
                key="booking",
                value={
                    "min_notice_minutes": 60,
                    "max_advance_days": 30,
                    "slot_granularity_minutes": 30,
                    "buffer_minutes": 0,
                },
            )
        )
        db.commit()
        print(f"Seeded '{business.name}' (id={business.id}): {len(SERVICES)} services, {len(STAFF)} staff, Mon–Sat hours.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
