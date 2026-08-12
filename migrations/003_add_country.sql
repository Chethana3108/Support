-- Migration: Add 'country' column to lead_state table
-- This column stores the user's country (ERP field name: country)
ALTER TABLE lead_state ADD COLUMN IF NOT EXISTS country TEXT DEFAULT '';
