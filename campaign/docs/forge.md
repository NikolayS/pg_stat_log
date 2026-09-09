# Forge methods and implementation plan for pg_stat_log

Researched 2026-09-09 from a fresh public GitLab clone. This is research guidance, not a claim that an experiment ran.

## Pins and branch selection

- Repository: https://gitlab.com/postgres-ai/forge
- Public `main` is only the initial GitLab README template: `64dc18c5173e775dad205766310df8adb88eaeb0`.
- Actual active harness: `feat/forge-bootstrap`, pinned `fc7284fa74a1286fe302c82f3512187b9f4160f0`.
- Local detached checkout: `/home/tars/clawd-max/pg-stat-log-study/forge`.
- Useful source paths at that pin: `METHODOLOGY.md`, `CLAUDE.md`, `HARDWARE.md`, `bin/forge`, `lib/collect_env.py`, `lib/collect_pg.sh`, `lib/observe_system.sh`, `lib/claims.py`, `lib/ledger.py`, `tools/host_release.py`, `cases/slru-subtrans-overflow-01/`.
- `bin/forge help` ran successfully. No database experiment, provisioning or external mutation performed here.

## Reuse, not reinvention

The runner supports case validation; per-run hardware and Postgres fingerprints; baseline/after counters; interrupt/incomplete markers; server-restart invalidation; exclusive-host/process evidence; low-rate OS observers; bounded ledger artifacts; typed claims with `holds`, `refuted`, `inconclusive`, `insufficient_data`; and case collection.

A case owns `case.yaml`, `README.md`, `repro.sh`, `assert.sql`, with optional fixture/export SQL. `repro.sh` receives FORGE_DSN, FORGE_ARTIFACTS, FORGE_RUN_ID, FORGE_CASE_DIR, FORGE_SWEEP_AXIS, FORGE_SWEEP_VALUE and FORGE_DELTA. Measurement CSV example columns are `case_id,run_id,delta,sweep_point,trial,metric,value,unit,source`. Preserve every trial, not only aggregates. Source `cases/slru-subtrans-overflow-01/export.sql` is the concrete export contract.

Suggested command shape ON THE EXCLUSIVE VM (password-free local socket using peer auth):

```bash
export FORGE_DSN='host=/run/postgresql dbname=forge user=bench'
export FORGE_EXEC=/usr/bin/env
export FORGE_TRIALS=5
export FORGE_OBSERVE_PERF=0
export FORGE_SCRATCH_ROOT=/var/lib/pg-stat-log-study/artifacts
bin/forge case validate obs-pg-stat-log-overhead-01
bin/forge case run obs-pg-stat-log-overhead-01 --record --non-interactive --timeout 3600
```

Case must actually be authored first; the example ID does not exist yet. Set FORGE_EXEC explicitly on VM: unset causes hardware_source=client-host, not server-exec, making records inadmissible for cross-run merges. Pin the resolved current Postgres master SHA, extension SHA, compiler/configure flags and binary hashes in the manifest before execution; “latest master” means latest checked now, then frozen.

Do not restart Postgres inside one Forge run: postmaster start-time guard correctly invalidates such a run. Restart-required preload baselines belong in separate recorded runs/blocks and external paired analysis, not a hidden state transition inside the sweep. Do not misuse `case merge`: it accepts disjoint environment sweep points with strict inputs/claim/hardware compatibility, not arbitrary repeated A/B arms.

## Hardware and claim limits

Dedicated 8-vCPU CCX is preferable to shared CX/CPX under user's frugality limit. Hardware registry already contains dedicated Hetzner CCX examples. Register actual new hardware, not an existing unrelated reference. Collect CPU topology, memory, kernel, storage, virtualization, governor, THP, mount options, steal time and client/server placement. Postgres and pgbench competing on the same VM is a known confounder; reserve explicit disjoint CPU sets if feasible and publish affinity.

There is a documentary conflict: METHODOLOGY.md says virtualized multi-user timing inadmissible, while HARDWARE.md explicitly registers dedicated CCX cloud nodes. Do not quietly claim bare-metal proof. Report exclusive dedicated-cloud A/B evidence and its host limitation; synthetic exact-count correctness claims remain valid independently of timing. No extra approval is needed to perform the user's explicitly authorized VM experiment.

Multi-user manifests need at least one deterministic claim and a frozen bottleneck panel (`panel: v1`, `calibration_ref: null`, justified exemptions only). Do not raise panel thresholds or suppress saturated-client/steal evidence to secure a pass.

## Proposed experiments

Keep correctness and performance cases separate. Validate exact invariants first, with negative tests proving assertions fail: records created for controlled statements; expected fields/units and nullability; transaction failures and retries; multiple users/databases; permissions; rotation/truncation/retention behavior; buffer/disk-full or write-failure behavior according to actual extension design; worker restart/shutdown; reset/read races. Confirm details against extension source before final case authoring.

Performance factor is the observation mechanism, not an uncontrolled configuration bundle:

1. Preload absent baseline (separate cluster startup/run).
2. Preloaded but recording disabled, if supported.
3. pg_stat_log enabled at its defaults.
4. Enabled with explicitly stated sample/flush configuration only if implemented.
5. Traditional logging comparison only as separately defined secondary question.

For each fixed workload/concurrency, compare arms with identical data, Postgres build, protocol, GUCs, client placement and warmed state. Cheap initial workloads: simple SELECT 1/high statement rate, pgbench select-only on memory-resident dataset, then read/write pgbench. Avoid uncontrolled checkpoint overlap; either force/schedule identical checkpoint state before each arm or make checkpoint behavior part of the declared long-run workload. Keep durability on for primary results.

## Schedule and statistics

Forge invokes `sweep.values` in declared order; it does NOT automatically randomize arms. Register a seeded balanced arm-order schedule before measuring and save it. Use 5 paired blocks initially, alternating/randomizing AB versus BA (more arms: balanced rotations/random permutations). Each measured arm gets a discarded 20–30 s warmup followed by 60–120 s measurement; avoid building/installing/profiling concurrently. Expand repeats to 9–10 only for noisy/near-threshold results under a preregistered rule, not until significant. Run short pilot separately; pilot numbers do not become confirmation results.

A straightforward implementation is an outer persisted schedule driving one standalone Forge run per arm/block, with a case containing only that observed environment point. Keep run IDs in a paired-analysis manifest. This avoids false built-in randomization claims and invalid restart-in-run records. Alternatively a case-owned paired loop is possible only for runtime GUC arms, but resulting CSV/claim semantics must be validated.

Publish per-run throughput, errors/retries, latency mean and p50/p95/p99 from sampled pgbench transaction logs where available, CPU seconds/transaction, RSS, context switches, runqueue/steal, output bytes/transaction, extension loss/drop counters if available, WAL bytes and pg_stat_io deltas. Quantile summaries are unavailable without source logs; do not derive p99 from means. Report absolute values and paired ratios, median paired effect and range, optionally a seeded bootstrap CI over independent blocks; do not treat transactions as independent experimental replicates.

Forge decisive effects require separating observed trial ranges; nonseparating results remain inconclusive rather than no overhead. Pre-register a practical bound (e.g. 5% overhead is a question, not a prior claim) and exact refutation criteria. Distinguish empirical CI from Forge's conservative range-based verdict.

## Observer cost and evidence

No model or network calls in reproduction. Do not set log_min_duration_statement=0 by default; Forge documents substantial observer cost. Disable perf in primary timings; do one separate diagnostic profile only after a reproducible regression. Collect OS metrics at a low fixed rate identical across arms. Never call XID-assigning/lock-heavy probes in the measurement interval. Reset counters only between arms; fail on reset timestamp movement. Read back effective settings in the session rather than relying on config files or EXPLAIN SETTINGS alone.

Retain source pins, commands, schedules, failed pilot artifacts, exact settings, raw transaction logs, per-arm logs, postmaster restart checks and machine fingerprints. Ledger cap is 64 KiB/6 tracked files per run; full streams must stay separate and hash-linked, not silently truncated. Publish synthetic sanitized source and derived analysis under the user's authorized extension fork/Pages brief; Forge itself distinguishes private raw machinery from publishable analysis, so do not expose unrelated Forge corpus or consulting artifacts.

Before destroying the explicitly authorized temporary host: verify all jobs finished, copy all run/build/patch/settings artifacts, validate digests locally, push the authorized synthetic artifacts, resolve server ID to expected name and remove only the owned server/key/firewall. `tools/host_release.py` is an existing fail-closed reference.
