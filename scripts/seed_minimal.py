"""Ensure the JSON store exists and is seeded.

Run:  python scripts/seed_minimal.py
Idempotent — skips if data/clinic.json already exists (hand-edits survive).
Delete data/clinic.json to factory-reset.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import json_store

if __name__ == "__main__":
    json_store.init()
    business = json_store.get_business()
    services = json_store.get_services(business["id"])
    print(f"Store ready: {json_store.DATA_FILE}")
    print(f"  business: {business['name']} | services: {len(services)} | staff: {len(json_store.SEED_STAFF)}")
