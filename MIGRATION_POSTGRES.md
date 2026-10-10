# PostgreSQL migration plan (documented now, built on trigger)

The JSON store (`app/db/json_store.py`) is the single source of truth today, and
its **function surface is the DAO contract**: `app/tools/booking.py` only calls
these functions, never touches storage directly. The migration is therefore a
drop-in replacement module, not a rewrite.

## Go-trigger (any ONE of these)

- A second process/worker runs against the store (the in-process `RLock` cannot protect across processes)
- Multi-user / multi-staff logins exist
- `data/clinic.json` passes ~10 MB (whole-file rewrite per mutation stops being free)
- The product is deployed publicly (concurrent real customers)

Below that line, JSON is the *simpler correct choice* — do not migrate early.

## Schema (10 tables = the store's 10 collections)

```sql
CREATE TABLE businesses (
  id SERIAL PRIMARY KEY, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
  phone TEXT, whatsapp_number TEXT, address TEXT, website_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE business_hours (
  id SERIAL PRIMARY KEY,
  business_id INT NOT NULL REFERENCES businesses(id),
  day_of_week INT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
  opens_at TIME NOT NULL, closes_at TIME NOT NULL, is_closed BOOLEAN NOT NULL DEFAULT false,
  CHECK (closes_at > opens_at)
);

CREATE TABLE staff (
  id SERIAL PRIMARY KEY,
  business_id INT NOT NULL REFERENCES businesses(id),
  name TEXT NOT NULL, role TEXT, specialty TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE services (
  id SERIAL PRIMARY KEY,
  business_id INT NOT NULL REFERENCES businesses(id),
  name TEXT NOT NULL, description TEXT,
  duration_minutes INT NOT NULL CHECK (duration_minutes > 0),
  buffer_minutes INT NOT NULL DEFAULT 0,
  price_inr INT NOT NULL CHECK (price_inr >= 0),
  is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE service_staff (
  id SERIAL PRIMARY KEY,
  business_id INT NOT NULL REFERENCES businesses(id),
  service_id INT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  staff_id INT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  UNIQUE (service_id, staff_id)
);

CREATE TABLE customers (
  id SERIAL PRIMARY KEY,
  business_id INT NOT NULL REFERENCES businesses(id),
  name TEXT NOT NULL, phone TEXT NOT NULL, email TEXT, notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (business_id, phone)
);

CREATE TABLE business_rules (
  id SERIAL PRIMARY KEY,
  business_id INT NOT NULL REFERENCES businesses(id),
  key TEXT NOT NULL, value JSONB NOT NULL,
  UNIQUE (business_id, key)
);

CREATE TABLE appointments (
  id SERIAL PRIMARY KEY,
  business_id INT NOT NULL REFERENCES businesses(id),
  customer_id INT NOT NULL REFERENCES customers(id),
  staff_id INT NOT NULL REFERENCES staff(id),
  service_id INT NOT NULL REFERENCES services(id),
  start_at TIMESTAMPTZ NOT NULL, end_at TIMESTAMPTZ NOT NULL,
  CHECK (end_at > start_at),
  status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','cancelled','completed','no_show')),
  source TEXT NOT NULL DEFAULT 'ai_chat',
  customer_confirmed BOOLEAN NOT NULL DEFAULT false,
  booking_reference TEXT UNIQUE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- double-booking guard at the database level, in addition to the app-level check:
CREATE UNIQUE INDEX uniq_active_staff_slot ON appointments (staff_id, start_at)
  WHERE status = 'booked';

CREATE TABLE conversations (
  id SERIAL PRIMARY KEY,
  business_id INT REFERENCES businesses(id),
  channel TEXT NOT NULL, customer_id INT REFERENCES customers(id),
  escalated BOOLEAN NOT NULL DEFAULT false,
  turn_count INT NOT NULL DEFAULT 0,
  pending_offer JSONB,
  messages JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tool_calls (
  id SERIAL PRIMARY KEY,
  conversation_id INT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  tool_name TEXT NOT NULL, arguments JSONB NOT NULL, result JSONB NOT NULL,
  success BOOLEAN NOT NULL, error TEXT, latency_ms INT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Timezone rule: store `TIMESTAMPTZ`; all slot math uses the business timezone
exactly like `clinic_now()` does today.

## The swap

1. **`app/db/pg_store.py`** — implement the same function names/signatures as
   `json_store.py` (catalog reads, customers, appointments, conversations,
   tool_calls, `clinic_now`). A connection pool (`psycopg_pool`) per process.
2. **`try_book` becomes one SQL transaction** — strictly stronger than today:
   `SELECT … FOR UPDATE` on the staff's active appointments inside the insert
   transaction, plus the `uniq_active_staff_slot` partial index as a final
   defense. Per-customer overlap stays a checked constraint inside the same
   transaction. `try_reschedule` identical, excluding itself.
3. **Wiring**: `app/config.py` gains `DATABASE_URL`; a one-line factory
   (`store = pg_store if DATABASE_URL else json_store`) keeps both backends
   runnable. Tools and agent are untouched.
4. **Data migration**: `scripts/migrate_json_to_pg.py` — reads `clinic.json`,
   inserts in dependency order (business → staff/services/links → customers →
   appointments → conversations/tool_calls), preserving ids and references.
5. **Seed**: reuse `SEED_*` constants in `scripts/seed_minimal.py` (guard on
   empty tables instead of missing file).
6. **Verification**: `scripts/test_atomic.py` + `smoke_test.py` must pass
   unchanged against `pg_store` — they only use the public surface. The eval
   battery runs once more end-to-end.

## Explicitly out of scope for the migration

- Multi-tenancy (that is a separate schema decision — RLS vs per-tenant schemas)
- ORMs (raw parameterized SQL keeps the surface honest; SQLAlchemy only if the
  codebase grows past ~20 store functions)
- Backups: standard `pg_dump` cron replaces the `.bak` rotation
