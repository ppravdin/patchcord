-- Add encrypted boolean column to agent_messages.
-- New messages with encryption enabled will set encrypted=TRUE.
-- Old plaintext messages remain encrypted=FALSE and are read as-is.

ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS encrypted BOOLEAN DEFAULT FALSE;
