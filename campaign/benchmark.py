#!/usr/bin/env python3
"""Blocked pg_stat_log experiments, executed by the pinned native Forge runner."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
TREATMENTS = ['baseline', 'disabled', 'filtered', 'on']
WORKLOADS = ['quiet', 'hot', 'cardinality', 'full_miss', 'reader', 'readwrite']
PG_SHA = '86f7c82cf1023e3599f40f939727791a7090cd44'
FORGE_SHA = 'fc7284fa74a1286fe302c82f3512187b9f4160f0'


def command(argv, *, env=None, input=None, check=True, timeout=180):
    result = subprocess.run([str(x) for x in argv], input=input, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'{argv[0]} exited {result.returncode}: {result.stderr[-2000:]}')
    return result


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def verify_forge(executable):
    checkout = executable.resolve().parent.parent
    observed = command(['git', '-C', checkout, 'rev-parse', 'HEAD']).stdout.strip()
    if observed != FORGE_SHA:
        raise RuntimeError('Forge source does not match declared campaign SHA')
    # HEAD comparison covers staged as well as unstaged tracked changes.
    command(['git', '-C', checkout, 'diff', '--exit-code', 'HEAD'])
    return observed


def new_native_record(runs, previous_paths):
    # Never attach a prior successful arm after a preflight or recording failure.
    candidates = set(runs.glob('*.json')) - previous_paths
    if not candidates:
        return None
    if len(candidates) != 1:
        raise RuntimeError('Forge invocation produced multiple new native records')
    record = candidates.pop()
    receipt = record.with_suffix('.mint')
    if receipt in previous_paths or not receipt.is_file() or receipt.is_symlink():
        raise RuntimeError('Forge native record lacks a new mint receipt')
    return record


def sql(text, env=None):
    result = command(['psql', '--no-psqlrc', '--tuples-only', '--no-align',
                      '--set=ON_ERROR_STOP=1', '--file=-'], env=env, input=text)
    return result.stdout.strip()


def capture_settings(env):
    return json.loads(sql("select json_object_agg(name, setting) from pg_settings;", env))


def stats(env, treatment):
    if treatment == 'baseline':
        return None
    return json.loads(sql("""select json_build_object(
      'warning_count', (select coalesce(sum(count),0) from pg_stat_log_data()
                         where elevel='WARNING'),
      'info', (select row_to_json(i) from pg_stat_log_info() i));""", env))


def assert_warning_path(measured, before, after, treatment, workload):
    if treatment != 'on':
        return
    counted = measured['counted_warnings']
    dropped = measured['dropped_warnings']
    expected = measured['expected_warnings']
    if workload == 'full_miss':
        if (before['info']['num_entries'] != 1024
                or after['info']['num_entries'] != 1024
                or counted != 0 or dropped != expected):
            raise RuntimeError('full-miss path invariant failed')
    elif counted != expected or dropped != 0:
        raise RuntimeError('non-full warning path unexpectedly dropped records')


def parse_pgbench(text):
    parsed = {}
    for name, pattern in {
        'transactions': r'number of transactions actually processed:\s*(\d+)',
        'failed': r'number of failed transactions:\s*(\d+)',
        'tps': r'tps = ([0-9.]+)',
        'latency_ms': r'latency average = ([0-9.]+) ms',
        'latency_stddev_ms': r'latency stddev = ([0-9.]+) ms',
    }.items():
        match = re.search(pattern, text)
        if match:
            parsed[name] = float(match.group(1)) if name not in ('transactions', 'failed') else int(match.group(1))
    if not {'transactions', 'tps', 'latency_ms'}.issubset(parsed):
        raise RuntimeError('pgbench output is missing mandatory metrics')
    parsed.setdefault('failed', 0)
    return parsed


def prepare(env, treatment, workload, artifact):
    # Arms initialize the function and counters before native Forge starts.
    scripts = {
        'quiet': 'select 1;\n',
        'hot': "select campaign_warning('P9000');\n",
        'cardinality': "\\set key random(1,64)\nselect campaign_warning('P' || lpad(:key::text,4,'0'));\n",
        'full_miss': "select campaign_warning('P9000');\n",
        'reader': "select campaign_warning('P9000');\n",
        'readwrite': '\\set key random(1,100000)\nbegin;\nselect value from campaign_kv where id=:key;\nupdate campaign_kv set value=value+1 where id=:key;\ncommit;\n',
    }
    script = artifact / 'workload.sql'
    script.write_text(scripts[workload])
    return script


def reset_readwrite(env, artifact):
    # Every timed interval starts from the same object, not prior-arm bloat.
    setup = """truncate campaign_kv;
insert into campaign_kv select i,0 from generate_series(1,100000) i;
vacuum analyze campaign_kv;
checkpoint;
"""
    (artifact / 'readwrite-reset.sql').write_text(setup)
    sql(setup, env)
    if sql('select count(*) from campaign_kv where value=0;', env) != '100000':
        raise RuntimeError('readwrite reset failed')


def validate_reader(proc, artifact, seconds):
    # The side workload must finish successfully; early failure is not silence.
    code = proc.wait(timeout=seconds + 15)
    if code != 0:
        raise RuntimeError(f'reader failed: {code}; see {artifact / "reader.log"}')
    metrics = parse_pgbench((artifact / 'reader.log').read_text())
    if metrics['transactions'] < 1 or metrics['failed']:
        raise RuntimeError('reader completed no successful workload')
    write_json(artifact / 'reader-result.json', metrics)


def stop_reader(proc, handle):
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
    finally:
        handle.close()


def run_reader(env, artifact, seconds):
    # Reader command is identical across treatments: baseline uses empty view.
    script = artifact / 'reader.sql'
    script.write_text('select count(*) from campaign_reader;\n\\sleep 10 ms\n')
    sql('select count(*) from campaign_reader;', env)
    argv = ['pgbench', '--no-vacuum', '--client=1', '--jobs=1',
            f'--time={seconds}', '--file=' + str(script)]
    write_json(artifact / 'reader-command.json', {'argv': argv, 'duration_s': seconds})
    handle = (artifact / 'reader.log').open('w')
    proc = subprocess.Popen(argv,
                            env=env, text=True, stdout=handle, stderr=handle)
    return proc, handle


def forge_point():
    env = os.environ.copy()
    config = json.loads(Path(env['CAMPAIGN_CONFIG']).read_text())
    native_artifact = Path(env['FORGE_ARTIFACTS'])
    artifact = Path(config['raw_root']) / env['FORGE_RUN_ID']
    artifact.mkdir(parents=True, exist_ok=True)
    clients = int(env['FORGE_SWEEP_VALUE'])
    treatment = config['treatment']
    workload = config['workload']
    settings = capture_settings(env)
    expected = {'log_connections': 'on', 'log_min_messages': 'warning', 'client_min_messages': 'error',
                'debug_assertions': 'off', 'jit': 'off', 'autovacuum': 'off',
                'synchronous_commit': 'on', 'fsync': 'on',
                'shared_preload_libraries': '' if treatment == 'baseline' else 'pg_stat_log'}
    if treatment != 'baseline':
        expected.update({'pg_stat_log.enabled': 'off' if treatment == 'disabled' else 'on',
                         'pg_stat_log.min_error_level': 'error' if treatment == 'filtered' else 'warning',
                         'pg_stat_log.max_entries': '1024'})
    mismatches = {k: [v, settings.get(k)] for k, v in expected.items() if settings.get(k) != v}
    if mismatches:
        raise RuntimeError('settings mismatch: ' + json.dumps(mismatches))
    write_json(artifact / f'settings-c{clients}.json', settings)
    rows = []
    for trial in range(0, config['trials'] + 1):
        trial_dir = artifact / f'c{clients}-trial{trial}'
        trial_dir.mkdir(exist_ok=False)
        script = prepare(env, treatment, workload, trial_dir)
        if workload == 'readwrite':
            reset_readwrite(env, trial_dir)
        seconds = config['warmup'] if trial == 0 else config['duration']
        before = stats(env, treatment)
        reader = None
        if workload == 'reader':
            reader = run_reader(env, trial_dir, seconds + 2)
            time.sleep(0.3)
            if reader[0].poll() is not None:
                stop_reader(*reader)
                raise RuntimeError('reader exited before measured workload')
        argv = ['pgbench', '--no-vacuum', f'--client={clients}',
                f'--jobs={min(clients,4)}', f'--time={seconds}', '--protocol=prepared',
                '--progress=1', '--report-per-command', '--file=' + str(script),
                '--random-seed=' + str(config['seed'] + config['block'] * 1000 + trial)]
        start = time.time_ns()
        try:
            result = command(argv, env=env, check=False, timeout=seconds + 90)
            elapsed = (time.time_ns() - start) / 1e9
            if reader:
                validate_reader(reader[0], trial_dir, seconds)
        finally:
            if reader:
                stop_reader(*reader)
        (trial_dir / 'pgbench.stdout').write_text(result.stdout)
        (trial_dir / 'pgbench.stderr').write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f'pgbench failed: {result.returncode}; see {trial_dir}')
        measured = parse_pgbench(result.stdout)
        after = stats(env, treatment)
        expected_count = measured['transactions'] if treatment == 'on' and workload not in ('quiet', 'readwrite') else 0
        counted = (after['warning_count'] - before['warning_count']) if after else 0
        dropped = (after['info']['n_dropped'] - before['info']['n_dropped']) if after else 0
        exact = counted + dropped == expected_count
        measured.update({'counted_warnings': counted, 'dropped_warnings': dropped,
                         'expected_warnings': expected_count, 'count_mismatch': int(not exact)})
        raw = {**config, **measured, 'clients': clients, 'trial': trial,
               'warmup_discarded': trial == 0, 'elapsed_s': elapsed,
               'start_unix_ns': start, 'argv': argv, 'before': before, 'after': after,
               'forge_run_id': env['FORGE_RUN_ID'], 'settings_verified': expected}
        write_json(trial_dir / 'result.json', raw)
        assert_warning_path(measured, before, after, treatment, workload)
        if not exact or measured['failed']:
            raise RuntimeError('deterministic count/transaction assertion failed: ' + str(trial_dir))
        if trial:
            for metric, unit in [('tps', 'transactions/s'), ('latency_ms', 'ms'),
                                 ('count_mismatch', 'count'), ('failed', 'count'),
                                 ('counted_warnings', 'count'), ('dropped_warnings', 'count')]:
                rows.append([env['FORGE_CASE_ID'] if 'FORGE_CASE_ID' in env else config['case_id'],
                             env['FORGE_RUN_ID'], treatment, clients, trial,
                             metric, measured[metric], unit, str(trial_dir / 'result.json')])
    target = native_artifact / 'measurements.csv'
    exists = target.exists()
    with target.open('a', newline='') as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(['case_id', 'run_id', 'delta', 'sweep_point', 'trial',
                             'metric', 'value', 'unit', 'source'])
        writer.writerows(rows)


def manifest(config, version, ext_sha, clients):
    # JSON is accepted by Forge's built-in YAML parser; no PyYAML dependency.
    return {
      'schema_version': 1, 'id': config['case_id'],
      'title': 'pg_stat_log ' + config['workload'] + ' collection overhead',
      'status': 'draft', 'class': 'multi-user', 'origin': 'synthetic',
      'inputs': {
        'environment': {'postgres': version, 'postgres_sha': PG_SHA,
                        'extension_sha': ext_sha, 'hardware_ref': 'pg-stat-log-ccx33-20260909',
                        'settings': {'shared_buffers': {'value': '1GB', 'why': 'fixed across arms'},
                                     'log_min_messages': {'value': 'warning', 'why': 'all synthetic warnings reach emit hook'},
                                     'client_min_messages': {'value': 'error', 'why': 'exclude wire warning output'}}},
        'object': {'setup': 'setup.sql', 'scale': ('100000 rows reset, vacuumed and checkpointed before each trial' if config['workload'] == 'readwrite' else 'synthetic PL/pgSQL function, no data table')},
        'workload': {'sessions': max(clients), 'script': 'generated workload.sql',
                     'duration': str(config['duration'])+'s', 'trials': config['trials'],
                     'warmup_seconds': config['warmup'], 'kind': config['workload'],
                     'protocol': 'prepared', 'pgbench_jobs': 'min(clients,4)'},
        'delta': []},
      'sweep': {'axis': 'workload', 'values': clients},
      'bottleneck': {'panel': 'v1', 'calibration_ref': None, 'exempt': []},
      'claims': [
        {'id': 'exact-warning-accounting', 'metric': 'count_mismatch', 'metric_class': 'counter',
         'statement': 'every completed synthetic warning is counted or dropped exactly once when recording is on; other arms record none',
         'assert': {'kind': 'constant', 'value': 0}, 'min_trials': 3,
         'invariant_across': ['workload'], 'provenance': 'inferred',
         'note': 'preregistered exact accounting invariant; no performance threshold is asserted'},
        {'id': 'no-transaction-failures', 'metric': 'failed', 'metric_class': 'counter',
         'statement': 'all pgbench transactions succeed',
         'assert': {'kind': 'constant', 'value': 0}, 'min_trials': 3,
         'invariant_across': ['workload'], 'provenance': 'inferred',
         'note': 'preregistered validity invariant'}],
      'prior_art': [{'url': 'https://hacking.postgres.tv/sessions/Et6WSCdR3Yw/',
                     'title': 'pg_stat_log hacking session', 'kind': 'article', 'relation': 'contextual'}]}


def summarize(root):
    statuses = json.loads((root / 'status.json').read_text()) if (root / 'status.json').exists() else []
    validity = {(s['block'], s['treatment'], s['workload']): s.get('native_operationally_valid', False) for s in statuses}
    results = []
    for path in root.glob('raw/**/result.json'):
        row = json.loads(path.read_text())
        if not row['warmup_discarded']:
            row['artifact'] = str(path.relative_to(root))
            row['native_operationally_valid'] = validity.get((row['block'], row['treatment'], row['workload']), False)
            results.append(row)
    write_json(root / 'trials.json', results)
    grouped = {}
    for row in results:
        if not row['native_operationally_valid']:
            continue
        grouped.setdefault((row['workload'], row['clients'], row['treatment'], row['block']), []).append(row['tps'])
    summaries = []
    for workload in sorted({r['workload'] for r in results}):
        for clients in sorted({r['clients'] for r in results}):
            for treatment in TREATMENTS:
                blocks = []
                for block in sorted({r['block'] for r in results}):
                    baseline = grouped.get((workload, clients, 'baseline', block))
                    variant = grouped.get((workload, clients, treatment, block))
                    if baseline and variant:
                        a, b = statistics.median(baseline), statistics.median(variant)
                        blocks.append({'block': block, 'baseline_tps': a, 'treatment_tps': b,
                                       'ratio': b/a, 'throughput_loss_pct': 100*(1-b/a)})
                if not blocks:
                    continue
                logs = [math.log(b['ratio']) for b in blocks]
                estimate = math.exp(statistics.mean(logs))
                ci = None
                if len(logs) > 1:
                    # Student-t interval over independent block log ratios.
                    critical = {1: 12.706205, 2: 4.302653, 3: 3.182446, 4: 2.776445,
                                5: 2.570582, 6: 2.446912, 7: 2.364624,
                                8: 2.306004, 9: 2.262157}.get(len(logs)-1, 2.228139)
                    half = critical * statistics.stdev(logs) / math.sqrt(len(logs))
                    ci = [math.exp(statistics.mean(logs)-half), math.exp(statistics.mean(logs)+half)]
                bootstrap = None
                if len(logs) > 1:
                    rng = random.Random(20260909)
                    distribution = sorted(math.exp(statistics.mean(rng.choices(logs, k=len(logs)))) for _ in range(20000))
                    bootstrap = [distribution[499], distribution[19499]]
                summaries.append({'workload': workload, 'clients': clients, 'treatment': treatment,
                                  'independent_blocks': len(blocks), 'blocks': blocks,
                                  'geomean_tps_ratio': estimate, 'throughput_loss_pct': 100*(1-estimate),
                                  'ratio_ci95_t_log_blocks': ci,
                                  'ratio_ci95_bootstrap_paired_blocks': bootstrap,
                                  'bootstrap_resamples': 20000})
    write_json(root / 'summary.json', {'method': 'paired treatment/baseline medians within randomized blocks; geometric mean ratios; percentile bootstrap resamples whole paired blocks (20000 deterministic draws), with Student-t CI on block log ratios as sensitivity',
              'limitations': ['single dedicated-vCPU VM, not bare metal',
                              'co-located pgbench and PostgreSQL; client CPU can limit throughput',
                              'five default independent blocks; uncertainty is based on blocks, not trial pseudoreplication',
                              'log sink is explicit per trial; devnull sensitivity excludes real log storage; primary file sink uses direct stderr, no logging collector',
                              'duration-based tests are closed-loop, not open-loop latency-SLO measurements',
                              'Forge exact-count verdicts do not assert performance or certify bottleneck absence'],
              'results': summaries})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--point', action='store_true')
    parser.add_argument('--summarize', type=Path)
    parser.add_argument('--pg-bin', type=Path)
    parser.add_argument('--forge', type=Path)
    parser.add_argument('--postgres-source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--phase', choices=['pilot', 'confirmation'], default='confirmation')
    parser.add_argument('--blocks', type=int, default=5)
    parser.add_argument('--treatments', default='baseline,disabled,on')
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--duration', type=int, default=30)
    parser.add_argument('--warmup', type=int, default=10)
    parser.add_argument('--clients', default='1,4,8,16')
    parser.add_argument('--workloads', default='quiet,hot,cardinality,full_miss')
    parser.add_argument('--seed', type=int, default=20260909)
    parser.add_argument('--port', type=int, default=55438)
    parser.add_argument('--log-sink', choices=['devnull', 'file'], default='file')
    args = parser.parse_args()
    if args.point:
        forge_point()
        return
    if args.summarize:
        summarize(args.summarize.resolve())
        return
    if not args.output or not args.pg_bin or not args.forge or not args.postgres_source:
        parser.error('--output, --pg-bin, --forge and --postgres-source are required')
    if min(args.blocks, args.trials, args.duration, args.warmup) < 1:
        parser.error('block/trial/duration/warmup must be positive')
    if args.trials < 3:
        parser.error('native Forge exact-count claims require at least three trials')
    if args.phase == 'confirmation' and (args.blocks < 5 or args.duration < 30 or args.warmup < 10):
        parser.error('confirmation requires at least five blocks, 30-second trials and 10-second warmup; use explicit --phase pilot otherwise')
    clients = [int(x) for x in args.clients.split(',')]
    workloads = args.workloads.split(',')
    selected_treatments = args.treatments.split(',')
    if not set(selected_treatments) <= set(TREATMENTS) or 'baseline' not in selected_treatments:
        parser.error('treatments must include baseline and only known treatments')
    if not set(workloads) <= set(WORKLOADS) or min(clients) < 1:
        parser.error('invalid workloads or clients')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    corpus = root / 'corpus'
    corpus.mkdir()
    data = root / 'pgdata'
    socket = root / 'socket'
    socket.mkdir()
    env = os.environ.copy()
    env.update({'PATH': str(args.pg_bin.resolve()) + ':' + env['PATH'],
                'PGHOST': str(socket), 'PGPORT': str(args.port), 'PGDATABASE': 'postgres',
                'PGAPPNAME': 'forge', 'LC_ALL': 'C', 'PAGER': 'cat',
                'FORGE_ROOT': str(corpus), 'FORGE_EXEC': '/usr/bin/env',
                'FORGE_DSN': f'host={socket} port={args.port} dbname=postgres',
                'FORGE_SCRATCH_ROOT': str(root / 'scratch'),
                'FORGE_ARTIFACTS_ROOT': str(root / 'scratch'),
                'FORGE_HOST_LOCK_FILE': str(root / 'forge.lock')})
    postgres_source = args.postgres_source.resolve()
    observed_pg_sha = command(['git', '-C', postgres_source, 'rev-parse', 'HEAD']).stdout.strip()
    if observed_pg_sha != PG_SHA:
        raise RuntimeError('Postgres source does not match declared campaign SHA')
    command(['git', '-C', postgres_source, 'diff', '--exit-code', 'HEAD'])
    version = command([args.pg_bin / 'postgres', '--version']).stdout.strip()
    harness_git_sha = command(['git', '-C', HERE.parent, 'rev-parse', 'HEAD']).stdout.strip()
    extension_sha = '0a8b782c0695883a2708ac60ebf32e1a95250952'
    extension_source_sha256 = hashlib.sha256((HERE.parent / 'pg_stat_log.c').read_bytes()).hexdigest()
    forge_sha = verify_forge(args.forge)
    canonical_c = command(['git', '-C', HERE.parent, 'show', extension_sha + ':pg_stat_log.c']).stdout
    if hashlib.sha256(canonical_c.encode()).hexdigest() != extension_source_sha256:
        raise RuntimeError('extension source does not match declared upstream SHA')
    configure = command([args.pg_bin / 'pg_config', '--configure']).stdout.strip()
    if '--enable-cassert' in configure:
        raise RuntimeError('benchmark build must not enable assertions')
    pkglibdir = Path(command([args.pg_bin / 'pg_config', '--pkglibdir']).stdout.strip())
    binary_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.pg_bin / 'postgres', args.pg_bin / 'pgbench', pkglibdir / 'pg_stat_log.so']}
    tool_versions = {'yq': command(['yq', '--version']).stdout.strip(), 'python': sys.version}
    command(['initdb', '--pgdata=' + str(data), '--no-locale', '--encoding=UTF8',
                        '--auth-local=peer', '--auth-host=scram-sha-256'], env=env)
    hardware = {name: command(argv, check=False).stdout for name, argv in {
        'lscpu': ['lscpu'], 'uname': ['uname', '-a'], 'free': ['free', '-b'],
        'lsblk': ['lsblk', '-J'], 'findmnt': ['findmnt', '-J']}.items()}
    write_json(root / 'hardware.json', hardware)
    (corpus / 'HARDWARE.md').write_text('# Reference hosts\n\n## Registry\n\n### pg-stat-log-ccx33-20260909\n\n```yaml\nhardware_ref: pg-stat-log-ccx33-20260909\nprovider: Hetzner CCX33 fsn1\ncpu:\n  dedicated: true\n  threads: 8\n```\n\nDedicated-vCPU instance; hypervisor topology is recorded in hardware.json, not bare-metal verification.\n')
    rng = random.Random(args.seed)
    schedule = []
    for block in range(1, args.blocks + 1):
        treatments = selected_treatments[:]
        rng.shuffle(treatments)
        for treatment in treatments:
            ordered = workloads[:]
            rng.shuffle(ordered)
            for workload in ordered:
                order = clients[:]
                rng.shuffle(order)
                schedule.append({'block': block, 'treatment': treatment,
                                 'workload': workload, 'clients': order})
    write_json(root / 'plan.json', {'postgres_sha': observed_pg_sha, 'postgres_source_tracked_clean': True, 'extension_sha': extension_sha,
              'forge_sha': forge_sha, 'forge_source_tracked_clean': True, 'configure': configure, 'binary_sha256': binary_hashes, 'tool_versions': tool_versions, 'harness_git_sha': harness_git_sha, 'extension_source_sha256': extension_source_sha256, 'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'version': version, 'arguments': vars(args) | {'output': str(root), 'pg_bin': str(args.pg_bin), 'forge': str(args.forge), 'postgres_source': str(postgres_source)},
              'schedule': schedule, 'preregistration': {'phase': args.phase,
              'stopping_rule': 'complete fixed schedule; abort on correctness or operational failure; no significance-based stopping',
              'timed_seconds_lower_bound': len(schedule) * len(clients) * (args.trials * args.duration + args.warmup),
              'statistical_unit': 'paired treatment block',
              'performance_hypothesis': 'estimate treatment/baseline TPS ratio; no preregistered negligible-overhead threshold'}})
    statuses = []
    started = False
    try:
        for number, arm in enumerate(schedule, 1):
            if shutil.disk_usage(root).free < 20 * 1024**3:
                raise RuntimeError('fewer than 20 GiB free; refusing to risk full log/data filesystem')
            if started:
                command(['pg_ctl', '--pgdata=' + str(data), '--mode=fast', '--wait', 'stop'], env=env)
                started = False
            treatment, workload = arm['treatment'], arm['workload']
            preload = '' if treatment == 'baseline' else 'pg_stat_log'
            with (data / 'postgresql.auto.conf').open('w') as handle:
                handle.write(f"shared_preload_libraries='{preload}'\nport={args.port}\nunix_socket_directories='{socket}'\nlisten_addresses=''\nshared_buffers='1GB'\nmax_connections=64\nlog_connections=on\nlog_min_messages=warning\nclient_min_messages=error\nlogging_collector=off\nlog_line_prefix=''\nlog_statement=none\nlog_min_duration_statement=-1\ntrack_io_timing=on\njit=off\nautovacuum=off\n")
                if treatment != 'baseline':
                    handle.write(f"pg_stat_log.enabled={'off' if treatment == 'disabled' else 'on'}\npg_stat_log.min_error_level={'error' if treatment == 'filtered' else 'warning'}\npg_stat_log.max_entries=1024\n")
            logfile = '/dev/null' if args.log_sink == 'devnull' else str(root / f'server-arm{number}.log')
            command(['pg_ctl', '--pgdata=' + str(data), '--log=' + logfile, '--wait', 'start'], env=env)
            started = True
            if workload == 'readwrite':
                sql('create table if not exists campaign_kv(id int primary key, value bigint not null) with (fillfactor=70); truncate campaign_kv; insert into campaign_kv select i,0 from generate_series(1,100000) i; vacuum analyze campaign_kv;', env)
            if treatment == 'baseline':
                sql('drop extension if exists pg_stat_log cascade; create or replace view campaign_reader as select 0::bigint as count where false;', env)
            else:
                sql('create extension if not exists pg_stat_log; create or replace view campaign_reader as select count from pg_stat_log_data();', env)
            sql("""create or replace function campaign_warning(code text) returns void
              language plpgsql as $body$ begin raise warning using errcode=code,
              message='campaign synthetic warning'; end $body$;""", env)
            if treatment != 'baseline':
                sql('select pg_stat_log_reset();', env)
            if workload == 'full_miss' and treatment == 'on':
                sql("select campaign_warning('P' || lpad(i::text,4,'0')) from generate_series(1,1024) i;", env)
                filled = stats(env, treatment)
                if filled['info']['num_entries'] != 1024 or filled['info']['n_dropped'] != 0:
                    raise RuntimeError('full_miss prefill failed: ' + json.dumps(filled))
            config = {**arm, 'seed': args.seed, 'trials': args.trials,
                      'duration': args.duration, 'warmup': args.warmup,
                      'log_sink': args.log_sink, 'raw_root': str(root / 'raw'), 'case_id': f'log-overhead-{workload.replace("_", "-")}-01'}
            case = corpus / 'cases' / config['case_id']
            case.mkdir(parents=True, exist_ok=True)
            # Save the exact per-arm manifest beside the raw results, too.
            contract = manifest(config, version, extension_sha, arm['clients'])
            pretty_yaml = command(['yq', '-P', '.', '-'], input=json.dumps(contract)).stdout
            (case / 'case.yaml').write_text(command(['yq', '.claims[].invariant_across style="flow" | .sweep.values style="flow"', '-'], input=pretty_yaml).stdout)
            shutil.copy2(HERE / 'forge' / 'repro.sh', case / 'repro.sh')
            shutil.copy2(HERE / 'forge' / 'assert.sql', case / 'assert.sql')
            (case / 'setup.sql').write_text('-- Setup is performed by campaign/benchmark.py before each arm; readwrite object reset repeats before each trial.\n')
            (case / 'README.md').write_text('# Synthetic logging experiment\n\nResearch round: R1\n\nExact warning accounting is checked independently of timing; paired block analysis lives in summary.json. Forge keeps this case draft; no lifecycle promotion or performance verdict is asserted.\n')
            config_path = root / f'arm{number:03d}.json'
            write_json(config_path, config)
            write_json(root / f'arm{number:03d}-manifest.json', contract)
            env.update({'CAMPAIGN_CONFIG': str(config_path), 'CAMPAIGN_DRIVER': str(Path(__file__).resolve()),
                        'FORGE_DELTA': treatment})
            log_path = root / f'arm{number:03d}-forge.log'
            print(f'[{number}/{len(schedule)}] {arm}', flush=True)
            runs = case / 'runs'
            previous_paths = set(runs.glob('*'))
            with log_path.open('w') as log:
                result = subprocess.run([str(args.forge.resolve()), 'case', 'run', config['case_id'],
                                         '--record', '--non-interactive'], env=env, stdout=log, stderr=log,
                                        timeout=(args.trials*args.duration+args.warmup+60)*len(clients)+600)
            latest_record_path = new_native_record(runs, previous_paths)
            native_record = json.loads(latest_record_path.read_text()) if latest_record_path else {}
            operationally_valid = (native_record.get('valid') is True
                and native_record.get('status') == 'passed'
                and native_record.get('restart_check', {}).get('state') == 'unchanged'
                and bool(native_record.get('steps'))
                and all(step.get('status') == 'ok' and step.get('exit_status') == 0 for step in native_record['steps'])
                and native_record.get('claim_verdicts', {}).get('all_hold') is True)
            statuses.append({**arm, 'number': number, 'forge_exit_code': result.returncode,
                             'native_operationally_valid': operationally_valid,
                             'native_record': str(latest_record_path.relative_to(root)) if latest_record_path else None,
                             'server_log_bytes': Path(logfile).stat().st_size if args.log_sink == 'file' else 0,
                             'disk_free_bytes': shutil.disk_usage(root).free,
                             'forge_log': log_path.name})
            write_json(root / 'status.json', statuses)
            summarize(root)
            if result.returncode or not operationally_valid:
                raise RuntimeError(f'Forge arm {number} failed ({result.returncode}); inspect {log_path}')
    finally:
        if started:
            command(['pg_ctl', '--pgdata=' + str(data), '--mode=fast', '--wait', 'stop'], env=env, check=False)
        summarize(root)
    # Raw cluster storage is not an artifact: preserve selected control/config only.
    write_json(root / 'checksums.json', {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in root.rglob('*') if p.is_file() and 'pgdata' not in p.parts and p.name != 'checksums.json'})


if __name__ == '__main__':
    main()
