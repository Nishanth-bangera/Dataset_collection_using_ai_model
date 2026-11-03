# Dataset_collection_using_ai_model

## Vercel deployment

This repository is configured as one Vercel FastAPI project. The root
`vercel_api.py` entry point serves the `frontend/` files on the same origin and exposes registration and
dataset-upload endpoints. Face embeddings are intentionally not generated in
the Vercel function; dataset images are uploaded directly to the private
Supabase Storage bucket using short-lived signed upload tokens.

In Vercel, connect this repository on the `main` branch and use the repository
root as the project root. Select the FastAPI framework preset. Add these server
environment variables in the Vercel project settings:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY` (a server-side secret/service-role key)
- `SUPABASE_ANON_KEY` (the browser-safe publishable/anon key)

Never expose `SUPABASE_SERVICE_KEY` in frontend code. The browser receives only
the publishable key from `/public-config`.

The API and static site share a Vercel origin, so production requests use
relative paths. For local development, run `run_backend.ps1` and open
`http://127.0.0.1:8000/`.

The portal uses Supabase Auth email OTP. Configure email OTP and SMTP/rate
limits in Supabase Auth, and make sure the email template displays
`{{ .Token }}` so students receive a numeric code. The browser receives only `SUPABASE_ANON_KEY`; the
backend validates each resulting access token and checks that its verified
email matches the student record before returning status or issuing upload
permissions. OTP expiration and abuse throttling are governed by Supabase Auth.

Apply `backend/schema.sql` in the Supabase SQL Editor. The schema update keeps
existing student mobile values and makes the legacy `mobile` field optional to
match the three-field registration form. Existing students and images are not
deleted. Keep the `face-datasets` bucket private. Uploaded objects use
`USN/student_details.json` and `USN/images/{front,left,right,up,down}.jpg`.

The Vercel API verifies all five object names in private Storage before
recording the dataset or reporting completion. Embedding generation,
duplicate-face review, and attendance need a separate processing stage and are
not enabled by this lightweight deployment. Camera access requires HTTPS (or
localhost during development).
