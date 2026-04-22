# Copyright (c) 2026, PlanetScale Inc.

# Test pg_stat_log extension
#
# Verifies:
# - Log messages are counted correctly
# - Filtering by min_error_level works
# - Enable/disable toggle works
# - Stats persist across clean restart
# - Stats are lost after crash recovery
# - Reset zeroes the counters

use strict;
use warnings FATAL => 'all';

use PostgreSQL::Test::Cluster;
use PostgreSQL::Test::Utils;
use Test::More;

my $node = PostgreSQL::Test::Cluster->new('main');
$node->init;
$node->append_conf('postgresql.conf',
	"shared_preload_libraries = 'pg_stat_log'");
$node->append_conf('postgresql.conf',
	"pg_stat_log.min_error_level = 'warning'");
$node->start;

$node->safe_psql('postgres', q(CREATE EXTENSION pg_stat_log));

# ---------------------------------------------------------------
# Test 1: Generate warnings and check counts
# ---------------------------------------------------------------

# Generate some warnings via DO blocks
$node->safe_psql('postgres', q(
	DO $$ BEGIN RAISE WARNING 'test warning 1'; END $$;
));
$node->safe_psql('postgres', q(
	DO $$ BEGIN RAISE WARNING 'test warning 2'; END $$;
));
$node->safe_psql('postgres', q(
	DO $$ BEGIN RAISE WARNING 'test warning 3'; END $$;
));

# Force stats flush
$node->safe_psql('postgres', q(SELECT pg_stat_force_next_flush()));

my $result = $node->safe_psql('postgres', q(
	SELECT count FROM pg_stat_log_data()
	WHERE elevel = 'WARNING' AND sqlerrcode = '01000'
));
# Should have at least 3 warnings
ok($result >= 3, "warning count is at least 3");

# ---------------------------------------------------------------
# Test 2: Errors are tracked
# ---------------------------------------------------------------

# Generate an error (will be rolled back but still logged)
$node->psql('postgres', q(SELECT 1/0));

$node->safe_psql('postgres', q(SELECT pg_stat_force_next_flush()));

$result = $node->safe_psql('postgres', q(
	SELECT count FROM pg_stat_log_data()
	WHERE elevel = 'ERROR' AND sqlerrcode = '22012'
));
is($result, "1", "division by zero error counted");

# ---------------------------------------------------------------
# Test 3: pg_stat_log view works (with database/user names)
# ---------------------------------------------------------------

$result = $node->safe_psql('postgres', q(
	SELECT count(*) FROM pg_stat_log WHERE count > 0
));
ok($result > 0, "pg_stat_log view returns rows");

# ---------------------------------------------------------------
# Test 4: Disable via GUC stops counting
# ---------------------------------------------------------------

$node->safe_psql('postgres', q(ALTER SYSTEM SET pg_stat_log.enabled = off));
$node->safe_psql('postgres', q(SELECT pg_reload_conf()));

# Wait briefly for reload
sleep(1);

# Record the current warning count
my $count_before = $node->safe_psql('postgres', q(
	SELECT COALESCE(sum(count), 0) FROM pg_stat_log_data()
	WHERE elevel = 'WARNING'
));

# Generate more warnings while disabled
$node->safe_psql('postgres', q(
	DO $$ BEGIN RAISE WARNING 'should not be counted'; END $$;
));

$node->safe_psql('postgres', q(SELECT pg_stat_force_next_flush()));

my $count_after = $node->safe_psql('postgres', q(
	SELECT COALESCE(sum(count), 0) FROM pg_stat_log_data()
	WHERE elevel = 'WARNING'
));
is($count_after, $count_before, "no new counts while disabled");

# Re-enable
$node->safe_psql('postgres', q(ALTER SYSTEM SET pg_stat_log.enabled = on));
$node->safe_psql('postgres', q(SELECT pg_reload_conf()));
sleep(1);

# ---------------------------------------------------------------
# Test 5: min_error_level filtering
# ---------------------------------------------------------------

# Set min level to ERROR — warnings should be ignored
$node->safe_psql('postgres',
	q(ALTER SYSTEM SET pg_stat_log.min_error_level = 'error'));
$node->safe_psql('postgres', q(SELECT pg_reload_conf()));
sleep(1);

$count_before = $node->safe_psql('postgres', q(
	SELECT COALESCE(sum(count), 0) FROM pg_stat_log_data()
	WHERE elevel = 'WARNING'
));

$node->safe_psql('postgres', q(
	DO $$ BEGIN RAISE WARNING 'filtered out'; END $$;
));

$node->safe_psql('postgres', q(SELECT pg_stat_force_next_flush()));

$count_after = $node->safe_psql('postgres', q(
	SELECT COALESCE(sum(count), 0) FROM pg_stat_log_data()
	WHERE elevel = 'WARNING'
));
is($count_after, $count_before,
	"warnings not counted with min_error_level = error");

# Restore default
$node->safe_psql('postgres',
	q(ALTER SYSTEM SET pg_stat_log.min_error_level = 'warning'));
$node->safe_psql('postgres', q(SELECT pg_reload_conf()));
sleep(1);

# ---------------------------------------------------------------
# Test 6: Stats persist across clean restart
# ---------------------------------------------------------------

$result = $node->safe_psql('postgres', q(
	SELECT count FROM pg_stat_log_data()
	WHERE elevel = 'ERROR' AND sqlerrcode = '22012'
));
my $error_count_pre_restart = $result;

$node->stop;
$node->start;

$result = $node->safe_psql('postgres', q(
	SELECT count FROM pg_stat_log_data()
	WHERE elevel = 'ERROR' AND sqlerrcode = '22012'
));
is($result, $error_count_pre_restart,
	"error count persists after clean restart");

# ---------------------------------------------------------------
# Test 7: Stats lost after crash recovery
# ---------------------------------------------------------------

$node->stop('immediate');
$node->start;

$result = $node->safe_psql('postgres', q(
	SELECT COALESCE(sum(count), 0) FROM pg_stat_log_data()
));
is($result, "0", "all counts are zero after crash recovery");

# ---------------------------------------------------------------
# Test 8: Reset zeroes counters
# ---------------------------------------------------------------

# Generate some data first
$node->safe_psql('postgres', q(
	DO $$ BEGIN RAISE WARNING 'after crash'; END $$;
));
$node->safe_psql('postgres', q(SELECT pg_stat_force_next_flush()));

$result = $node->safe_psql('postgres', q(
	SELECT COALESCE(sum(count), 0) FROM pg_stat_log_data()
));
ok($result > 0, "have counts before reset");

$node->safe_psql('postgres', q(SELECT pg_stat_log_reset()));

$result = $node->safe_psql('postgres', q(
	SELECT COALESCE(sum(count), 0) FROM pg_stat_log_data()
));
is($result, "0", "all counts are zero after reset");

# ---------------------------------------------------------------
# Test 9: pg_stat_log_info() basics
# ---------------------------------------------------------------

# One row, max_entries matches the GUC default (1024), num_entries is 0 after
# the previous reset.
my $info_rows = $node->safe_psql('postgres', q(
	SELECT count(*) FROM pg_stat_log_info()
));
is($info_rows, "1", "pg_stat_log_info() returns one row");

my $max_entries = $node->safe_psql('postgres', q(
	SELECT max_entries FROM pg_stat_log_info()
));
my $guc_max = $node->safe_psql('postgres',
	q(SHOW pg_stat_log.max_entries));
is($max_entries, $guc_max,
	"pg_stat_log_info.max_entries matches GUC pg_stat_log.max_entries");

my $num_entries = $node->safe_psql('postgres', q(
	SELECT num_entries FROM pg_stat_log_info()
));
is($num_entries, "0", "num_entries is 0 after reset");

my $n_dropped = $node->safe_psql('postgres', q(
	SELECT n_dropped FROM pg_stat_log_info()
));
is($n_dropped, "0", "n_dropped is 0 by default");

# stats_reset advances on reset.
my $reset_before = $node->safe_psql('postgres', q(
	SELECT extract(epoch FROM stats_reset)::numeric FROM pg_stat_log_info()
));
$node->safe_psql('postgres',
	q(SELECT pg_sleep(0.1); SELECT pg_stat_log_reset();));
my $reset_after = $node->safe_psql('postgres', q(
	SELECT extract(epoch FROM stats_reset)::numeric FROM pg_stat_log_info()
));
ok($reset_after > $reset_before,
	"stats_reset timestamp advances after pg_stat_log_reset()");

# ---------------------------------------------------------------
# Test 10: n_dropped increments and reset reclaims slots
# ---------------------------------------------------------------

# Use an immediate stop so the persisted stats file (sized for the previous
# max_entries) is discarded; otherwise, restarting with a different
# max_entries would leave the stats file and shared memory sized
# inconsistently.
$node->stop('immediate');
$node->append_conf('postgresql.conf', "pg_stat_log.max_entries = 64");
$node->start;

# Sanity: max_entries reflects the new setting, counters are fresh.
$max_entries = $node->safe_psql('postgres', q(
	SELECT max_entries FROM pg_stat_log_info()
));
is($max_entries, "64", "max_entries reflects restart-scoped GUC");

# Generate many distinct (elevel, sqlerrcode) combinations to overflow
# the 64-slot capacity. We use 100 different synthetic SQLSTATE codes.
$node->safe_psql('postgres', q{
	DO $$
	DECLARE
		i int;
		code text;
	BEGIN
		FOR i IN 1..100 LOOP
			code := 'Z' || lpad(i::text, 4, '0');
			BEGIN
				RAISE WARNING 'overflow test %', i USING ERRCODE = code;
			EXCEPTION WHEN OTHERS THEN
				NULL;
			END;
		END LOOP;
	END $$;
});
$node->safe_psql('postgres', q(SELECT pg_stat_force_next_flush()));

$num_entries = $node->safe_psql('postgres', q(
	SELECT num_entries FROM pg_stat_log_info()
));
is($num_entries, "64", "num_entries saturates at max_entries");

$n_dropped = $node->safe_psql('postgres', q(
	SELECT n_dropped FROM pg_stat_log_info()
));
ok($n_dropped > 0, "n_dropped > 0 after overflowing max_entries");

# Record a unique code that was dropped (code Z0099 should have been dropped,
# since the first 64 unique codes consumed the capacity).
my $dropped_before = $node->safe_psql('postgres', q(
	SELECT count(*) FROM pg_stat_log_data() WHERE sqlerrcode = 'Z0099'
));
is($dropped_before, "0", "Z0099 was dropped before reset (no slot free)");

# Reset should reclaim slots.
$node->safe_psql('postgres', q(SELECT pg_stat_log_reset()));

$num_entries = $node->safe_psql('postgres', q(
	SELECT num_entries FROM pg_stat_log_info()
));
is($num_entries, "0", "num_entries is 0 after reset when saturated");

$n_dropped = $node->safe_psql('postgres', q(
	SELECT n_dropped FROM pg_stat_log_info()
));
is($n_dropped, "0", "n_dropped is 0 after reset");

# Generate a NEW distinct error that wasn't tracked before and verify it
# is now counted — proving reset actually reclaimed slots.
$node->safe_psql('postgres', q{
	DO $$
	BEGIN
		RAISE WARNING 'post-reset' USING ERRCODE = 'Z9999';
	EXCEPTION WHEN OTHERS THEN
		NULL;
	END $$;
});
$node->safe_psql('postgres', q(SELECT pg_stat_force_next_flush()));

my $post_reset = $node->safe_psql('postgres', q(
	SELECT count FROM pg_stat_log_data() WHERE sqlerrcode = 'Z9999'
));
is($post_reset, "1",
	"new distinct error is tracked after reset (slots reclaimed)");

done_testing();
