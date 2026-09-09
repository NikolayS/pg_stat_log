#!/usr/bin/env python3
"""Fresh-cluster restart controls and explicit pg_monitor privilege assertions.

Run only on the dedicated test host, outside benchmark timing windows. Uses
correctness.Campaign; preserves failures as failures, including desired-contract
expectations about unrelated core counters on extension capacity changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import traceback

from correctness import Campaign


RESTART_ASSERTIONS = [
    'initial-capacity', 'core-counter-established', 'extension-counter-established',
    'capacity-after', 'core-counter-after', 'extension-counter-after',
    'extension-counter-new-writes', 'second-restart-capacity',
    'second-restart-extension-counter',
]
MONITOR_ASSERTIONS = [
    'membership-active', 'read-allowed', 'reset-denied-sqlstate',
    'reset-denied-message', 'counter-not-reset',
]


def restart_case(c, initial, final):
    if initial != 64:
        c.stop()
        with (c.data / 'postgresql.conf').open('a') as f:
            f.write(f'\npg_stat_log.max_entries={initial}\n')
        c.start()
    c.assertion('initial-capacity', c.integer('SHOW pg_stat_log.max_entries'), initial)
    c.reset()
    c.sql('CREATE TABLE restart_control_core(i integer); '
          'INSERT INTO restart_control_core SELECT generate_series(1,100); '
          'SELECT pg_stat_force_next_flush();')
    counter = "SELECT n_tup_ins FROM pg_stat_user_tables WHERE relname='restart_control_core'"
    deadline = time.monotonic() + 10
    before = c.integer(counter)
    while before != 100 and time.monotonic() < deadline:
        time.sleep(0.05)
        before = c.integer(counter)
    c.assertion('core-counter-established', before, 100)
    if before != 100:
        raise RuntimeError('Core counter prerequisite failed; restart inference is not valid')
    c.sql(c.emit('Z9101', 7))
    c.assertion('extension-counter-established', c.count('Z9101'), 7)
    c.stop()
    with (c.data / 'postgresql.conf').open('a') as f:
        f.write(f'\npg_stat_log.max_entries={final}\n')
    c.start()
    c.assertion('capacity-after', c.integer('SELECT max_entries FROM pg_stat_log_info()'), final)
    c.assertion('core-counter-after', c.integer(counter), 100,
                'Desired contract: unrelated table counters survive a clean restart. '
                'A changed-capacity loss remains a failed expectation, not a pass.',
                'persistence-contract')
    expected = 7 if initial == final else 0
    c.assertion('extension-counter-after', c.count('Z9101'), expected,
                'Same-size clean restart preserves counts; changed-size restore discards them.')
    c.sql(c.emit('Z9101', 5))
    c.assertion('extension-counter-new-writes', c.count('Z9101'), expected + 5)
    c.stop()
    c.start()
    c.assertion('second-restart-capacity', c.integer('SELECT max_entries FROM pg_stat_log_info()'), final)
    c.assertion('second-restart-extension-counter', c.count('Z9101'), expected + 5)


def monitor_case(c):
    c.reset()
    c.sql('CREATE ROLE restart_control_monitor; '
          'GRANT pg_monitor TO restart_control_monitor;')
    c.sql(c.emit('Z9102', 11))
    membership = c.sql("SET ROLE restart_control_monitor; "
                       "SELECT pg_has_role(current_user,'pg_monitor','USAGE');").stdout.strip()
    c.assertion('membership-active', membership, 't')
    read = c.sql('SET ROLE restart_control_monitor; '
                 "SELECT coalesce(sum(count),0) FROM pg_stat_log_data() WHERE sqlerrcode='Z9102';",
                 check=False)
    c.assertion('read-allowed', [read.returncode, read.stdout.strip()], [0, '11'])
    denied = c.sql('\\set VERBOSITY verbose\n'
                   'SET ROLE restart_control_monitor; SELECT pg_stat_log_reset();', check=False)
    c.assertion('reset-denied-sqlstate',
                denied.returncode == 3 and '42501' in denied.stderr, True,
                'An arbitrary SQL or connection failure cannot satisfy permission denial.')
    c.assertion('reset-denied-message',
                'permission denied for function pg_stat_log_reset' in denied.stderr, True)
    c.assertion('counter-not-reset', c.count('Z9102'), 11)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', required=True)
    parser.add_argument('--workdir', required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('must run as a non-root OS user')
    root = Path(args.workdir).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    definitions = [
        ('same-64-64', RESTART_ASSERTIONS, lambda c: restart_case(c, 64, 64)),
        ('grow-64-128', RESTART_ASSERTIONS, lambda c: restart_case(c, 64, 128)),
        ('shrink-128-64', RESTART_ASSERTIONS, lambda c: restart_case(c, 128, 64)),
        ('pg-monitor', MONITOR_ASSERTIONS, monitor_case),
    ]
    inventory = {name: {'status': 'not_run', 'expected_assertions': expected}
                 for name, expected, _ in definitions}
    evidence = {'assertions': [], 'cases': inventory, 'provenance': {
        'pg_bin': str(Path(args.pg_bin).resolve()),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'campaign_sha256': hashlib.sha256(Path(__file__).with_name('correctness.py').read_bytes()).hexdigest(),
    }}

    def save():
        evidence['summary'] = {status: sum(r['status'] == status for r in evidence['assertions'])
                               for status in ['pass', 'fail', 'error', 'not_run']}
        temporary = root / 'results.json.tmp'
        temporary.write_text(json.dumps(evidence, indent=2) + '\n')
        temporary.replace(root / 'results.json')

    save()
    for name, expected_names, function in definitions:
        c = None
        inventory[name]['status'] = 'running'
        save()
        try:
            c = Campaign(argparse.Namespace(pg_bin=args.pg_bin,
                         workdir=str(root / name), hook_probe=False))
            c.setup()
            function(c)
            inventory[name]['status'] = 'completed'
        except Exception as exc:
            inventory[name]['status'] = 'error'
            evidence['assertions'].append({'name': name + '/execution', 'status': 'error',
                'error': str(exc), 'traceback': traceback.format_exc()})
        finally:
            if c is not None:
                try:
                    if c.running:
                        c.stop()
                except Exception as exc:
                    inventory[name]['status'] = 'error'
                    evidence['assertions'].append({'name': name + '/cleanup', 'status': 'error',
                                                  'error': str(exc)})
                finally:
                    c.save()
                    c.transcript.close()
                for row in c.rows:
                    evidence['assertions'].append(dict(row, name=name + '/' + row['name']))
            observed = {r['name'] for r in evidence['assertions']}
            for assertion in expected_names:
                if name + '/' + assertion not in observed:
                    evidence['assertions'].append({'name': name + '/' + assertion,
                        'status': 'not_run', 'note': 'Case did not reach this planned assertion.'})
            save()
    print(json.dumps(evidence['summary']), flush=True)
    return int(any(r['status'] != 'pass' for r in evidence['assertions']))


if __name__ == '__main__':
    raise SystemExit(main())
