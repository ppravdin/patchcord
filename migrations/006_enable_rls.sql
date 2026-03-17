-- Enable RLS on all tables. The server uses the service_role key which
-- bypasses RLS, so this changes nothing functionally. But it prevents
-- data exposure if the anon key is ever leaked.

ALTER TABLE agent_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_auth_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_access_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_limit_bans ENABLE ROW LEVEL SECURITY;
ALTER TABLE bearer_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_namespaces ENABLE ROW LEVEL SECURITY;
