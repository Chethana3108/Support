-- Migration: User Identity Deduplication
-- Adds phone and updated_at columns to users table
-- Creates unique partial indexes on email and phone for identity lookup

-- Add new columns
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;

-- Unique partial indexes: only index non-empty values
-- This allows multiple NULL/empty rows but prevents duplicate real identifiers
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique 
  ON users (email) WHERE email IS NOT NULL AND email != '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique 
  ON users (phone) WHERE phone IS NOT NULL AND phone != '';
