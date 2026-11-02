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

The Vercel upload step stores five pose images (front, left, right, up, down) and marks face processing as
pending. Embedding generation, duplicate-face review, and attendance need a
separate processing stage and are not enabled by this lightweight deployment.
If this project already has the Supabase tables, rerun `backend/schema.sql` in
the Supabase SQL Editor once to add the `processing_status` column.
