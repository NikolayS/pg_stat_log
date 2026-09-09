CREATE FUNCTION campaign_hook_probe_calls() RETURNS bigint
AS 'MODULE_PATHNAME', 'campaign_hook_probe_calls' LANGUAGE C;
REVOKE ALL ON FUNCTION campaign_hook_probe_calls() FROM PUBLIC;
