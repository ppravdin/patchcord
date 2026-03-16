-- Multi-recipient messaging: fan-out with shared group_id
ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS group_id UUID;
ALTER TABLE agent_messages ADD COLUMN IF NOT EXISTS recipients TEXT[];
CREATE INDEX IF NOT EXISTS idx_messages_group_id ON agent_messages(group_id);
