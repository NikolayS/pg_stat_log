#!/usr/bin/env python3
"""Additional bounded isolated tests; run only outside benchmark timing windows.
Uses correctness.py; no third-party Python dependencies. No corruption injection.
"""
import argparse
import json
import os
import select
import subprocess
import traceback
from correctness import Campaign


def boundary(c):
    # Each value uses a fresh initialized cluster: no persisted stats resize is
    # mixed into the startup-GUC oracle. Invalid extension placeholders may
    # warn and fall back to the declared default rather than abort startup.
    for capacity in [63, 64, 1048576, 1048577]:
        node = Campaign(argparse.Namespace(pg_bin=str(c.bin),
            workdir=str(c.root/f'guc-{capacity}'), hook_probe=False))
        try:
            node.run('initdb', ['-D', str(node.data), '-U', 'postgres', '--no-locale', '-A', 'trust'])
            with open(node.data/'postgresql.conf', 'a') as f:
                f.write(f"\nlisten_addresses=''\nport=55439\nunix_socket_directories='{node.sock}'\n"
                        f"shared_preload_libraries='pg_stat_log'\npg_stat_log.max_entries={capacity}\n"
                        "shared_buffers='32MB'\nautovacuum=off\nclient_min_messages=error\n")
            proc = node.run('pg_ctl', ['-D', str(node.data), '-l', str(node.root/'server.log'),
                                     '-w', '-t', '20', 'start'], check=False, timeout=25)
            node.running = proc.returncode == 0
            logfile = (node.root/'server.log').read_text()
            if capacity in [63, 1048577]:
                c.assertion(f'guc/{capacity}/range-diagnostic',
                    'outside the valid range' in logfile and 'pg_stat_log.max_entries' in logfile, True,
                    'An invalid preload-time custom GUC can warn and retain its default rather than abort startup.')
                if node.running:
                    c.assertion(f'guc/{capacity}/fallback-default', node.integer('SHOW pg_stat_log.max_entries'), 1024)
                else:
                    c.assertion(f'guc/{capacity}/startup-rejected', proc.returncode != 0, True)
            else:
                c.assertion(f'guc/{capacity}/startup-success', proc.returncode, 0)
                if node.running:
                    node.sql('CREATE EXTENSION pg_stat_log;')
                    c.assertion(f'guc/{capacity}/actual-value', node.integer('SHOW pg_stat_log.max_entries'), capacity)
                    node.reset(); node.sql(node.emit('Z8001', 2))
                    c.assertion(f'guc/{capacity}/usable', node.count('Z8001'), 2)
        finally:
            if node.running:
                node.stop()
            node.save()
            node.transcript.close()


def deadlock(c):
    c.reset()
    c.sql('CREATE TABLE extra_deadlock (id int PRIMARY KEY, n int); INSERT INTO extra_deadlock VALUES (1,0),(2,0);')
    processes = []
    scripts = []
    try:
        for first in [1, 2]:
            args = [str(c.bin/'psql'), '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                    '-h', str(c.sock), '-p', '55439', '-U', 'postgres', '-d', 'postgres']
            p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=c.env)
            processes.append(p)
            script = f"SET deadlock_timeout='100ms'; BEGIN; UPDATE extra_deadlock SET n=n+1 WHERE id={first}; SELECT 'READY';\n"
            scripts.append(script)
            p.stdin.write(script); p.stdin.flush()
            if not select.select([p.stdout], [], [], 10)[0] or p.stdout.readline().strip() != 'READY':
                raise RuntimeError('deadlock participant did not acquire its first row lock')
        for p, second in zip(processes, [2, 1]):
            script = f'UPDATE extra_deadlock SET n=n+1 WHERE id={second}; COMMIT;\n'
            scripts.append(script)
            p.stdin.write(script); p.stdin.close(); p.stdin = None
        outputs = []
        for p in processes:
            out, err = p.communicate(timeout=15)
            outputs.append(dict(returncode=p.returncode, stdout=out, stderr=err))
        (c.root/'deadlock.json').write_text(json.dumps(dict(scripts=scripts, outputs=outputs), indent=2)+'\n')
        c.assertion('deadlock/one-victim-one-survivor', sorted(p.returncode == 0 for p in processes), [False, True])
        c.assertion('deadlock/sqlstate-exact-count', c.count('40P01'), 1)
        c.assertion('deadlock/committed-survivor-only', c.val('SELECT string_agg(n::text,\',\' ORDER BY id) FROM extra_deadlock'), '1,1')
    finally:
        for p in processes:
            if p.poll() is None:
                p.kill(); p.wait(timeout=5)


def connection_failures(c):
    c.reset()
    p = c.sql('SELECT 1;', db='extra_nonexistent_db', check=False)
    c.assertion('connect/nonexistent-db-fails', p.returncode != 0, True)
    c.assertion('connect/nonexistent-db-count', c.count('3D000'), 1)
    c.assertion('connect/nonexistent-db-null-database', c.val("SELECT database_oid IS NULL FROM pg_stat_log_data() WHERE sqlerrcode='3D000'"), 't')
    c.sql('CREATE ROLE extra_reject LOGIN;')
    hba = c.data/'pg_hba.conf'
    original = hba.read_text()
    try:
        hba.write_text('local all extra_reject reject\n'+original)
        c.run('pg_ctl', ['-D', str(c.data), 'reload'])
        c.sql('SELECT pg_sleep(0.2);')
        p = c.run('psql', ['-X', '-qAt', '-h', str(c.sock), '-p', '55439', '-U', 'extra_reject', '-d', 'postgres', '-c', 'SELECT 1'], check=False)
        c.assertion('connect/hba-rejection-fails', p.returncode != 0 and 'rejects connection' in p.stderr, True)
        c.assertion('connect/hba-rejection-count', c.count('28000'), 1)
        # Attribution can be incomplete before InitPostgres binds role/database;
        # preserve evidence instead of assuming every authentication path's OIDs.
        rows = c.val("SELECT json_agg(d) FROM pg_stat_log_data() d WHERE sqlerrcode IN ('28000','3D000')")
        (c.root/'connection_failure_rows.json').write_text(rows+'\n')
    finally:
        hba.write_text(original)
        c.run('pg_ctl', ['-D', str(c.data), 'reload'])


def lifecycle(c):
    c.reset()
    c.sql(c.emit('Z8002', 9))
    c.sql('DROP EXTENSION pg_stat_log;')
    c.sql(c.emit('Z8002', 7))
    c.sql('CREATE EXTENSION pg_stat_log;')
    c.assertion('extension/drop-recreate-keeps-preloaded-collection', c.count('Z8002'), 16)
    c.sql('CREATE ROLE extra_drop_role; CREATE DATABASE extra_drop_db;')
    role_oid = c.integer("SELECT oid FROM pg_roles WHERE rolname='extra_drop_role'")
    db_oid = c.integer("SELECT oid FROM pg_database WHERE datname='extra_drop_db'")
    c.sql('SET ROLE extra_drop_role; '+c.emit('Z8003'))
    c.sql(c.emit('Z8004'), db='extra_drop_db')
    c.sql('DROP ROLE extra_drop_role; DROP DATABASE extra_drop_db;')
    c.assertion('identity/dropped-role-oid-survives-name-null',
                c.val("SELECT user_oid||'|'||(user_name IS NULL) FROM pg_stat_log WHERE sqlerrcode='Z8003'"), f'{role_oid}|true')
    c.assertion('identity/dropped-db-oid-survives-name-null',
                c.val("SELECT database_oid||'|'||(database_name IS NULL) FROM pg_stat_log WHERE sqlerrcode='Z8004'"), f'{db_oid}|true')
    c.sql('CREATE TABLE extra_core_stats(i int); INSERT INTO extra_core_stats SELECT generate_series(1,100); SELECT pg_stat_force_next_flush();')
    before = c.integer("SELECT n_tup_ins FROM pg_stat_user_tables WHERE relname='extra_core_stats'")
    c.assertion('resize/core-stats-established', before, 100)
    c.stop()
    with open(c.data/'postgresql.conf', 'a') as f:
        f.write('\npg_stat_log.max_entries=128\n')
    c.start()
    c.assertion('resize/unrelated-core-stats-preserved', c.integer("SELECT n_tup_ins FROM pg_stat_user_tables WHERE relname='extra_core_stats'"), before,
                'Contract probe: changing extension capacity should ideally not discard unrelated core cumulative counters.', 'persistence-contract')
    c.stop()
    with open(c.data/'postgresql.conf', 'a') as f:
        f.write("\nshared_preload_libraries=''\n")
    c.start()
    c.assertion('preload/removal-server-healthy', c.integer('SELECT 1'), 1)
    c.assertion('preload/removal-config', c.val('SHOW shared_preload_libraries'), '')
    c.stop()
    with open(c.data/'postgresql.conf', 'a') as f:
        f.write("\nshared_preload_libraries='pg_stat_log'\n")
    c.start()
    c.reset(); c.sql(c.emit('Z8005', 5))
    c.assertion('preload/readd-collection-healthy', c.count('Z8005'), 5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', required=True)
    parser.add_argument('--workdir', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--skip-boundary', action='store_true')
    mode.add_argument('--only-boundary', action='store_true')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('must run as a non-root OS user')
    args.hook_probe = False
    c = Campaign(args)
    try:
        c.setup()
        tests = [boundary] if args.only_boundary else ([deadlock, connection_failures, lifecycle] if args.skip_boundary else [boundary, deadlock, connection_failures, lifecycle])
        for test in tests:
            try:
                test(c)
            except Exception as exc:
                c.rows.append(dict(name=test.__name__, status='error', error=str(exc), traceback=traceback.format_exc()))
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
