-- ─────────────────────────────────────────────────────────────────────────────
-- AI Attendance System – Supabase Schema  (v2 – run in SQL Editor)
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Students table
CREATE TABLE IF NOT EXISTS students (
    id         BIGSERIAL PRIMARY KEY,
    usn        TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL,
    email      TEXT UNIQUE NOT NULL,
    mobile     TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- The registration form only requires name, USN, and email. Existing mobile
-- values are preserved; new registrations may leave this legacy field empty.
ALTER TABLE public.students ALTER COLUMN mobile DROP NOT NULL;

-- Store registration and capture/upload state separately. Defaults are safe for
-- existing student rows; the API reconciles capture/upload values from Storage.
ALTER TABLE public.students
    ADD COLUMN IF NOT EXISTS registration_status TEXT NOT NULL DEFAULT 'completed',
    ADD COLUMN IF NOT EXISTS face_capture_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS dataset_upload_status TEXT NOT NULL DEFAULT 'pending';

-- 2. Face dataset upload log
CREATE TABLE IF NOT EXISTS face_datasets (
    id          BIGSERIAL PRIMARY KEY,
    usn         TEXT REFERENCES students(usn) ON DELETE CASCADE,
    image_count INT DEFAULT 5,
    zip_path    TEXT,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    uploaded_at TIMESTAMPTZ DEFAULT now()
);

-- Add the processing state when upgrading an existing project.
ALTER TABLE public.face_datasets
ADD COLUMN IF NOT EXISTS processing_status TEXT NOT NULL DEFAULT 'pending';

-- 3. Attendance records
--    usn + date is UNIQUE so a student can only be marked once per day.
CREATE TABLE IF NOT EXISTS attendance (
    id     BIGSERIAL PRIMARY KEY,
    usn    TEXT REFERENCES students(usn) ON DELETE CASCADE,
    date   TEXT NOT NULL,          -- e.g. "2026-11-01"
    time   TEXT NOT NULL,          -- e.g. "09:45:00"
    status TEXT DEFAULT 'Present',
    UNIQUE(usn, date)
);

-- 4. Storage bucket for face images
--    Run this block once; skip if the bucket already exists.
INSERT INTO storage.buckets (id, name, public)
VALUES ('face-datasets', 'face-datasets', false)
ON CONFLICT (id) DO NOTHING;

-- 5. Storage RLS policy – allow the service-role key full access
DROP POLICY IF EXISTS "service role full access" ON storage.objects;
CREATE POLICY "service role full access"
ON storage.objects
FOR ALL
TO service_role
USING (bucket_id = 'face-datasets')
WITH CHECK (bucket_id = 'face-datasets');
