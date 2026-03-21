-- Add delivered_at column for delivery latency tracking
ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;
