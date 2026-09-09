\set ON_ERROR_STOP on
-- Per-trial exact accounting is asserted by the deterministic Python driver.
-- This independent SQL assertion confirms the cluster and database remain live.
do $$ begin assert current_database() = 'postgres'; end $$;
