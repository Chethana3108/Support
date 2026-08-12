-- Add metadata JSONB column to website_chunks for tagging chunk types (e.g., case_study)
ALTER TABLE website_chunks ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;
