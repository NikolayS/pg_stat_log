#!/usr/bin/env python3
"""Isolated, assertion-oriented pg_stat_log adversarial campaign.
Requires installed extension and PostgreSQL bin directory, standard library only.
Run as non-root: correctness.py --pg-bin /opt/pgsql/bin --workdir /tmp/unique-dir
Never attaches to an existing database; refuses an existing work directory.
Failures are preserved in results.json with expected/actual and semantics rationale.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import time
import traceback


class Campaign:
    def __init__(self, args):
        self.bin = Path(args.pg_bin).resolve()
        self.root = Path(args.workdir).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.data = self.root / 'data'
        self.sock = self.root / 'socket'
        self.sock.mkdir()
        self.rows = []
        self.running = False
        self.hook_probe_enabled = args.hook_probe
        self.env = dict(os.environ, LC_ALL='C', PGCONNECT_TIMEOUT='5')
        # Ambient connection/service/options variables must not redirect tests.
        for key in list(self.env):
            if key.startswith('PG'):
                del self.env[key]
        self.env['PGCONNECT_TIMEOUT'] = '5'
        self.transcript = open(self.root / 'commands.jsonl', 'a', buffering=1)

    def run(self, tool, args, sql=None, timeout=45, check=True):
        start = time.monotonic()
        proc = subprocess.run([str(self.bin / tool), *args], input=sql,
                              text=True, capture_output=True, env=self.env, timeout=timeout)
        self.transcript.write(json.dumps(dict(tool=tool, args=args, sql=sql,
            returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
            elapsed=time.monotonic()-start))+'\n')
        if check and proc.returncode:
            raise RuntimeError(f'{tool} failed: {proc.stderr[-3000:]}')
        return proc

    def sql(self, text, check=True, db='postgres'):
        return self.run('psql', ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-h',
            str(self.sock), '-p', '55439', '-U', 'postgres', '-d', db], text,
            check=check)

    def val(self, sql):
        return self.sql(sql).stdout.strip()

    def integer(self, sql):
        return int(self.val(sql))

    def assertion(self, name, actual, expected, note='', category='correctness'):
        row = dict(name=name, status='pass' if actual == expected else 'fail',
                   actual=actual, expected=expected, note=note, category=category)
        self.rows.append(row)
        print(json.dumps(row), flush=True)
        self.save()

    def save(self):
        (self.root / 'results.json').write_text(json.dumps(dict(
            assertions=self.rows, summary={s: sum(r['status']==s for r in self.rows)
            for s in ['pass', 'fail', 'error']}), indent=2)+'\n')

    def start(self):
        self.run('pg_ctl', ['-D', str(self.data), '-l', str(self.root/'server.log'),
                           '-w', '-t', '30', 'start'])
        self.running = True

    def stop(self, mode='fast'):
        self.run('pg_ctl', ['-D', str(self.data), '-w', '-t', '30', '-m', mode, 'stop'])
        self.running = False

    def reset(self):
        self.sql('SELECT pg_stat_log_reset();')

    def count(self, code):
        return self.integer("SELECT coalesce(sum(count),0) FROM pg_stat_log_data() "
                            f"WHERE sqlerrcode='{code}';")

    @staticmethod
    def emit(code, n=1, level='WARNING'):
        return (f"DO $$ BEGIN FOR i IN 1..{n} LOOP RAISE {level} 'campaign' "
                f"USING ERRCODE='{code}'; END LOOP; END $$;")

    def setup(self):
        self.run('initdb', ['-D', str(self.data), '-U', 'postgres', '--no-locale', '-A', 'trust'])
        with open(self.data/'postgresql.conf', 'a') as f:
            f.write(f"\nlisten_addresses=''\nport=55439\nunix_socket_directories='{self.sock}'\n"
                    "shared_preload_libraries='pg_stat_log'\npg_stat_log.max_entries=64\n"
                    "log_min_messages=warning\nclient_min_messages=error\n"
                    "autovacuum=off\nmax_connections=32\nshared_buffers='32MB'\n")
        self.start()
        self.sql('CREATE EXTENSION pg_stat_log;')
        (self.root/'version.txt').write_text(self.val('SELECT version();')+'\n')

    def severity(self):
        # LOG ranks between ERROR and FATAL for server-log filtering, not by its
        # integer elog.h enum value. Mirror documented server threshold semantics.
        levels = ['debug1', 'info', 'notice', 'warning', 'error', 'log']
        rank = {v:i for i,v in enumerate(levels)}
        for floor in ['warning', 'debug1']:
            for threshold in levels:
                for level in levels:
                    code = 'Z1001'
                    self.reset()
                    raiselevel = 'DEBUG' if level == 'debug1' else ('EXCEPTION' if level == 'error' else level.upper())
                    self.sql(f"SET log_min_messages={floor}; SET pg_stat_log.min_error_level={threshold}; "
                             + self.emit(code, level=raiselevel), check=(level!='error'))
                    expected = int(rank[level] >= rank[floor] and rank[level] >= rank[threshold])
                    self.assertion(f'severity/{floor}/{threshold}/{level}', self.count(code), expected,
                        'Expected threshold ordering follows PostgreSQL server log severity, including LOG.',
                        'severity-contract')
        self.reset()
        self.sql("SET pg_stat_log.enabled=off; "+self.emit('Z1002', 7))
        self.assertion('disabled/no-count', self.count('Z1002'), 0)
        self.sql("SET pg_stat_log.enabled=off; SET pg_stat_log.enabled=on; "+self.emit('Z1002', 3))
        self.assertion('enabled/session-toggle', self.count('Z1002'), 3)
        self.sql("DO $$ BEGIN RAISE EXCEPTION 'caught' USING ERRCODE='Z1003'; EXCEPTION WHEN OTHERS THEN NULL; END $$;")
        self.assertion('caught-exception/not-logged', self.count('Z1003'), 0,
                       'Documented limitation: caught errors never reach emit_log_hook.')

    def exact_and_identity(self):
        self.reset()
        self.sql(self.emit('Z2001', 1000))
        self.assertion('single-writer/exact', self.count('Z2001'), 1000)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.sql(self.emit('Z2002', 1000)), range(8)))
        self.assertion('concurrent-writers/exact', self.count('Z2002'), 8000)
        self.sql('CREATE ROLE campaign_user LOGIN; CREATE DATABASE campaign_db;')
        self.sql('CREATE EXTENSION pg_stat_log;', db='campaign_db')
        self.sql('SET ROLE campaign_user; '+self.emit('Z2003', 5), db='campaign_db')
        self.assertion('identity/role-database-backend', self.val("SELECT database_name||'|'||user_name||'|'||backend_type||'|'||count FROM pg_stat_log WHERE sqlerrcode='Z2003'"),
                       'campaign_db|campaign_user|client backend|5')
        self.assertion('unknown-sqlstate/name-null', self.val("SELECT sqlerrcode_name IS NULL FROM pg_stat_log WHERE sqlerrcode='Z2003'"), 't')
        self.sql('SELECT 1/0;', check=False)
        self.assertion('known-sqlstate/name', self.val("SELECT sqlerrcode_name FROM pg_stat_log WHERE sqlerrcode='22012'"), 'division_by_zero')
        for operation in ['SELECT * FROM pg_stat_log_data()', 'SELECT * FROM pg_stat_log',
                          'SELECT * FROM pg_stat_log_info()', 'SELECT pg_stat_log_reset()',
                          'SET pg_stat_log.enabled=off', 'SET pg_stat_log.min_error_level=panic']:
            out = self.sql('SET ROLE campaign_user; '+operation, check=False)
            self.assertion('privilege/denied/'+operation, out.returncode != 0 and 'permission denied' in out.stderr, True)
        self.sql('GRANT pg_read_all_stats TO campaign_user;')
        for operation in ['SELECT * FROM pg_stat_log_data()', 'SELECT * FROM pg_stat_log', 'SELECT * FROM pg_stat_log_info()']:
            self.assertion('privilege/monitor/'+operation,
                self.sql('SET ROLE campaign_user; '+operation, check=False).returncode, 0)
        out = self.sql('SET ROLE campaign_user; SELECT pg_stat_log_reset()', check=False)
        self.assertion('privilege/monitor-cannot-reset', out.returncode != 0, True)

    def fatal_and_partitioning(self):
        self.reset()
        out = self.sql('SELECT pg_terminate_backend(pg_backend_pid());', check=False)
        self.assertion('fatal/connection-terminated', out.returncode != 0, True)
        self.assertion('fatal/count-and-attribution', self.val(
            "SELECT elevel||'|'||backend_type||'|'||database_name||'|'||user_name||'|'||count "
            "FROM pg_stat_log WHERE sqlerrcode='57P01'"),
            'FATAL|client backend|postgres|postgres|1')
        self.reset()
        # One SQLSTATE, distinct users and databases must remain separate keys.
        self.sql(self.emit('Z2010', 2))
        self.sql('SET ROLE campaign_user; '+self.emit('Z2010', 3))
        self.sql(self.emit('Z2010', 5), db='campaign_db')
        self.sql('SET ROLE campaign_user; '+self.emit('Z2010', 7), db='campaign_db')
        self.assertion('identity/distinct-composite-keys', self.val(
            "SELECT string_agg(database_name||'|'||user_name||'|'||count, ',' ORDER BY database_name,user_name) "
            "FROM pg_stat_log WHERE sqlerrcode='Z2010'"),
            'campaign_db|campaign_user|7,campaign_db|postgres|5,postgres|campaign_user|3,postgres|postgres|2')

    def parallel_workers(self):
        self.reset()
        self.sql("CREATE TABLE campaign_parallel WITH (parallel_workers=4) AS SELECT i FROM generate_series(1,10000) s(i); "
                 "ANALYZE campaign_parallel; "
                 "CREATE FUNCTION campaign_parallel_warning(i integer) RETURNS integer LANGUAGE plpgsql PARALLEL SAFE AS $$ "
                 "BEGIN RAISE WARNING 'parallel campaign' USING ERRCODE='Z2020'; RETURN i; END $$;")
        out = self.sql("SET max_parallel_workers_per_gather=4; SET min_parallel_table_scan_size=0; "
                       "SET parallel_setup_cost=0; SET parallel_tuple_cost=0; "
                       "EXPLAIN (ANALYZE, FORMAT JSON) SELECT sum(campaign_parallel_warning(i)) FROM campaign_parallel;")
        plan = json.loads(out.stdout)
        (self.root/'parallel_plan.json').write_text(json.dumps(plan, indent=2)+'\n')
        parallel_rows = self.val("SELECT coalesce(json_agg(d), '[]'::json) FROM pg_stat_log_data() d WHERE sqlerrcode='Z2020'")
        (self.root/'parallel_rows.json').write_text(parallel_rows+'\n')
        def launched(node):
            return node.get('Workers Launched', 0) + sum(launched(n) for n in node.get('Plans', []))
        workers = launched(plan[0]['Plan'])
        self.assertion('parallel/workers-launched', workers > 0, True)
        self.assertion('parallel/exact-message-conservation', self.count('Z2020'), 10000)
        self.assertion('parallel/worker-attribution', self.integer(
            "SELECT count(*) FROM pg_stat_log_data() WHERE sqlerrcode='Z2020' AND backend_type='parallel worker'"), 1,
            'README promises parallel worker label; implementation uses generic background worker. See parallel_rows.json; this assertion does not imply lost counts.', 'documentation-contract')

    def snapshots(self):
        for consistency in ['none', 'cache', 'snapshot']:
            self.reset()
            self.sql(self.emit('Z3001'))
            q = "SELECT coalesce(sum(count),0) FROM pg_stat_log_data() WHERE sqlerrcode='Z3001';"
            result = self.sql(f'BEGIN; SET stats_fetch_consistency={consistency}; '+q+
                              self.emit('Z3001')+q+'SELECT pg_stat_clear_snapshot(); '+q+'COMMIT;')
            nums = [int(x) for x in result.stdout.splitlines() if x.strip().isdigit()]
            self.assertion('snapshot/'+consistency, nums, [1, 2 if consistency=='none' else 1, 2],
                           'Within a transaction, cached fixed-stat snapshots remain stable until explicitly cleared.')

    def capacity_and_restart(self):
        self.reset()
        self.sql("DO $$ BEGIN FOR i IN 1..100 LOOP RAISE WARNING 'overflow' USING ERRCODE='Z'||lpad(i::text,4,'0'); END LOOP; END $$;")
        self.assertion('capacity/exact-slots-drops', self.val('SELECT num_entries||\'|\'||n_dropped FROM pg_stat_log_info()'), '64|36')
        self.sql(self.emit('Z0001', 11)+self.emit('Z0100', 13))
        self.assertion('capacity/tracked-still-increments', self.count('Z0001'), 12)
        self.assertion('capacity/repeated-untracked-drops', self.integer('SELECT n_dropped FROM pg_stat_log_info()'), 49)
        timestamp = self.val('SELECT stats_reset FROM pg_stat_log_info()')
        all_counts_sql = "SELECT string_agg(sqlerrcode||':'||count, ',' ORDER BY sqlerrcode) FROM pg_stat_log_data() WHERE sqlerrcode BETWEEN 'Z0001' AND 'Z0064'"
        before_counts = self.val(all_counts_sql)
        self.stop(); self.start()
        self.assertion('restart/all-saturated-entries-preserved', self.val(all_counts_sql), before_counts)
        self.assertion('restart/clean-count-preserved', self.count('Z0001'), 12)
        self.sql("DO $$ BEGIN FOR i IN 1..64 LOOP RAISE WARNING 'persisted lookup' USING ERRCODE='Z'||lpad(i::text,4,'0'); END LOOP; END $$;")
        self.assertion('restart/persisted-hash-links-usable', self.integer("SELECT sum(count) FROM pg_stat_log_data() WHERE sqlerrcode BETWEEN 'Z0001' AND 'Z0064'"), 139)
        self.assertion('restart/metadata-drops-reset', self.integer('SELECT n_dropped FROM pg_stat_log_info()'), 0)
        self.assertion('restart/reset-timestamp-advanced', self.val('SELECT stats_reset FROM pg_stat_log_info()') > timestamp, True)
        self.reset()
        self.assertion('reset/reclaims-capacity', self.val("SELECT num_entries||'|'||n_dropped FROM pg_stat_log_info()"), '0|0')
        self.sql(self.emit('Z4001', 7))
        self.assertion('reset/new-key-counted', self.count('Z4001'), 7)
        self.stop('immediate'); self.start()
        self.assertion('crash/counts-discarded', self.count('Z4001'), 0)
        for size in [128, 1024, 64]:
            self.reset()
            old_capacity = self.integer('SELECT max_entries FROM pg_stat_log_info()')
            self.sql(self.emit('Z4002', 3))
            self.sql("DO $$ BEGIN FOR i IN 1.."+str(old_capacity-1)+
                     " LOOP RAISE WARNING 'resize saturated' USING ERRCODE='X'||lpad(i::text,4,'0'); END LOOP; END $$;")
            self.assertion(f'resize/{size}/old-capacity-saturated', self.integer('SELECT num_entries FROM pg_stat_log_info()'), old_capacity)
            self.stop()
            with open(self.data/'postgresql.conf', 'a') as f:
                f.write(f'\npg_stat_log.max_entries={size}\n')
            self.start()
            self.assertion(f'resize/{size}/new-capacity', self.integer('SELECT max_entries FROM pg_stat_log_info()'), size)
            self.assertion(f'resize/{size}/old-data-discarded', self.count('Z4002'), 0)
            self.sql(self.emit('Z4003', 9))
            self.assertion(f'resize/{size}/new-writes', self.count('Z4003'), 9)

    def concurrent_capacity(self):
        self.reset()
        def writer(worker):
            # Eight writers each contribute 100 distinct keys, one message/key.
            self.sql("DO $$ BEGIN FOR i IN 1..100 LOOP RAISE WARNING 'parallel overflow' "
                     f"USING ERRCODE='Y'||lpad(({worker}*100+i)::text,4,'0'); END LOOP; END $$;")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(writer, range(8)))
        self.assertion('concurrent-capacity/slots', self.integer('SELECT num_entries FROM pg_stat_log_info()'), 64)
        self.assertion('concurrent-capacity/drops', self.integer('SELECT n_dropped FROM pg_stat_log_info()'), 736)
        self.assertion('concurrent-capacity/conservation',
            self.integer('SELECT coalesce(sum(count),0) FROM pg_stat_log_data()')+
            self.integer('SELECT n_dropped FROM pg_stat_log_info()'), 800)
        self.assertion('concurrent-capacity/no-duplicate-keys', self.integer(
            "SELECT count(*) FROM (SELECT backend_type,database_oid,user_oid,elevel,sqlerrcode "
            "FROM pg_stat_log_data() GROUP BY 1,2,3,4,5 HAVING count(*)>1) d"), 0)

    def hook_chain(self):
        if not self.hook_probe_enabled:
            return
        for order in ['campaign_hook_probe,pg_stat_log', 'pg_stat_log,campaign_hook_probe']:
            self.stop()
            with open(self.data/'postgresql.conf', 'a') as f:
                f.write(f"\nshared_preload_libraries='{order}'\n")
            self.start()
            self.sql('CREATE EXTENSION IF NOT EXISTS campaign_hook_probe;')
            for setting, expected_count in [('on', 1), ('off', 0)]:
                self.reset()
                out = self.sql(f'SET pg_stat_log.enabled={setting}; '+self.emit('Z7001')+
                               'SELECT campaign_hook_probe_calls();')
                self.assertion(f'hook/{order}/{setting}/forwards', out.stdout.strip(), '1')
                self.assertion(f'hook/{order}/{setting}/count', self.count('Z7001'), expected_count)
            self.reset()
            out = self.sql('SET pg_stat_log.min_error_level=panic; '+self.emit('Z7001')+
                           'SELECT campaign_hook_probe_calls();')
            self.assertion(f'hook/{order}/filtered/forwards', out.stdout.strip(), '1')
            self.assertion(f'hook/{order}/filtered/count', self.count('Z7001'), 0)
            self.reset()
            out = self.sql('SET campaign_hook_probe.nested=on; '+self.emit('Z7001')+
                           'SELECT campaign_hook_probe_calls();')
            self.assertion(f'hook/{order}/nested/forwards', out.stdout.strip(), '2',
                           'Candidate integration limitation: reentrant callbacks may be skipped by recursion guard.', 'hook-contract')
            self.assertion(f'hook/{order}/nested/outer-count', self.count('Z7001'), 1)
            self.assertion(f'hook/{order}/nested/inner-count', self.count('Z7002'), 1,
                           'Both warnings reach server log; guarded nested warning may be intentionally uncounted.', 'hook-contract')
            self.reset()
            before = (self.root/'server.log').stat().st_size
            self.sql("SET campaign_hook_probe.suppress=on; DO $$ BEGIN RAISE WARNING "
                     "'campaign suppressed hook marker' USING ERRCODE='Z7003'; END $$;")
            with open(self.root/'server.log') as f:
                f.seek(before)
                newlog = f.read()
            self.assertion(f'hook/{order}/suppressed/not-logged', 'campaign suppressed hook marker' in newlog, False)
            self.assertion(f'hook/{order}/suppressed/not-counted', self.count('Z7003'), 0,
                           'Candidate contract mismatch: pg_stat_log counts hook events even when another hook suppresses server output.', 'hook-contract')

    def reset_race(self):
        self.reset()
        def resetter(_):
            for i in range(30):
                self.reset()
        def writer(_):
            self.sql(self.emit('Z5001', 3000))
        def reader(_):
            for i in range(25):
                self.sql('SELECT count(*) FROM pg_stat_log_data(); SELECT * FROM pg_stat_log_info();')
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            jobs = [pool.submit(resetter, 0), pool.submit(reader, 0)]
            jobs += [pool.submit(writer, i) for i in range(4)]
            for job in jobs:
                job.result(timeout=60)
        self.assertion('concurrent-reset/bounded-surviving-count', 0 <= self.count('Z5001') <= 12000, True,
                       'No exact count claimed across resets; operations must finish without crash/deadlock.')
        self.reset()
        self.sql(self.emit('Z5002', 17))
        self.assertion('concurrent-reset/post-race-integrity', self.count('Z5002'), 17)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pg-bin', required=True)
    p.add_argument('--workdir', required=True)
    p.add_argument('--hook-probe', action='store_true', help='Requires installed campaign/hook_probe PGXS extension')
    args = p.parse_args()
    if os.geteuid() == 0:
        p.error('must run as a non-root OS user')
    c = Campaign(args)
    try:
        c.setup()
        for method in [c.severity, c.exact_and_identity, c.fatal_and_partitioning, c.parallel_workers, c.snapshots,
                       c.capacity_and_restart, c.concurrent_capacity, c.reset_race, c.hook_chain]:
            try:
                method()
            except Exception as exc:
                c.rows.append(dict(name=method.__name__, status='error', error=str(exc), traceback=traceback.format_exc()))
                c.save()
                raise
    finally:
        if c.running:
            c.stop()
        c.save()
        c.transcript.close()
    return int(any(r['status'] != 'pass' for r in c.rows))

if __name__ == '__main__':
    raise SystemExit(main())
