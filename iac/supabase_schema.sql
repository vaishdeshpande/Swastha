-- iac/supabase_schema.sql
-- Run this in the Supabase SQL Editor to bootstrap the schema from scratch.
-- SQLAlchemy's create_all() handles this automatically on first boot,
-- but this file is useful for manual inspection, resetting via Supabase UI,
-- or CI pre-provisioning.

-- Enable UUID extension (Supabase enables it by default; safe to run again)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── patients ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patients (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(200) NOT NULL,
    phone           VARCHAR(15) NOT NULL UNIQUE,
    age             INTEGER,
    lang_pref       VARCHAR(10) DEFAULT 'hi-IN',
    blood_group     VARCHAR(5),
    medical_history JSONB DEFAULT '[]',
    is_new          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients(phone);

-- ── doctors ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS doctors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(200) NOT NULL,
    department      VARCHAR(100) NOT NULL,
    qualification   VARCHAR(200),
    phone           VARCHAR(15),
    available_days  JSONB DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_doctors_department ON doctors(department);

-- ── appointments ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS appointments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id  UUID REFERENCES patients(id),
    doctor_id   UUID NOT NULL REFERENCES doctors(id),
    doctor_name VARCHAR(200),
    department  VARCHAR(100) NOT NULL,
    slot_date   VARCHAR(10) NOT NULL,   -- "YYYY-MM-DD"
    slot_time   VARCHAR(10) NOT NULL,   -- "HH:MM"
    status      VARCHAR(20) DEFAULT 'open',   -- open | booked | cancelled | completed
    confirmed   BOOLEAN DEFAULT FALSE,
    booked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_appointments_dept_date_status
    ON appointments(department, slot_date, status);

-- ── prescriptions ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prescriptions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id  UUID NOT NULL REFERENCES patients(id),
    doctor_id   UUID NOT NULL REFERENCES doctors(id),
    doctor_name VARCHAR(200),
    medicines   JSONB NOT NULL,
    notes_en    TEXT,
    issued_date TIMESTAMPTZ DEFAULT NOW(),
    refill_date TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_prescriptions_patient ON prescriptions(patient_id);

-- ── discharge_followups ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS discharge_followups (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id              UUID NOT NULL REFERENCES patients(id),
    discharge_date          TIMESTAMPTZ NOT NULL,
    diagnosis               VARCHAR(500),
    medications_prescribed  JSONB,
    due_at                  TIMESTAMPTZ NOT NULL,
    status                  VARCHAR(20) DEFAULT 'pending',  -- pending | completed | escalated | unreachable
    outcome_json            JSONB,
    completed_at            TIMESTAMPTZ,
    job_type                VARCHAR(20) DEFAULT 'followup'  -- followup | confirmation | rx_reminder
);
CREATE INDEX IF NOT EXISTS idx_followups_due ON discharge_followups(due_at, status);

-- ── lab_reports ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lab_reports (
    report_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id        UUID NOT NULL REFERENCES patients(id),
    test_name         VARCHAR(200) NOT NULL,
    status            VARCHAR(20) DEFAULT 'pending',  -- pending | ready | dispatched
    ordered_at        TIMESTAMPTZ DEFAULT NOW(),
    ready_at          TIMESTAMPTZ,
    result_summary_en VARCHAR(500)
);
CREATE INDEX IF NOT EXISTS idx_lab_patient_status ON lab_reports(patient_id, status);

-- ── bills ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bills (
    bill_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id   UUID NOT NULL REFERENCES patients(id),
    amount_due   NUMERIC(10,2) NOT NULL,
    status       VARCHAR(20) DEFAULT 'unpaid',  -- unpaid | partial | paid
    items_json   JSONB DEFAULT '[]',
    payment_link TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bills_patient_status ON bills(patient_id, status);

-- ── call_logs ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS call_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID REFERENCES patients(id),
    call_id         VARCHAR(100),
    lang_code       VARCHAR(10),
    recording_path  TEXT,
    analytics_json  JSONB,
    duration_sec    INTEGER,
    call_outcome    JSONB,
    agents_used     JSONB,
    escalated       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_call_logs_call_id ON call_logs(call_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_created ON call_logs(created_at);
