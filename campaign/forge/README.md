# Native Forge integration

`../benchmark.py` runs the pinned upstream Forge executable, not an imitation of
its command interface. It creates an isolated corpus under the output directory,
validates typed exact-count claims, takes native exclusivity locks, collects
baseline/after PostgreSQL snapshots and OS observations, and writes real mint
receipts, ledger records and claim verdicts with `case run --record`.

Required tools: the optimized PostgreSQL build (including pgbench), Bash,
Python 3, Go yq v4.44.3, sysstat, procps, and the complete Forge checkout pinned
at `fc7284fa74a1286fe302c82f3512187b9f4160f0`. The upstream default branch is
only scaffolding; this commit comes from `feat/forge-bootstrap`. The driver
rejects a different Forge HEAD or staged/unstaged tracked changes before starting
a cluster and records this check in `plan.json`.

## Run the preregistered batch

Run on the dedicated benchmark host as its unprivileged `bench` user, with
exclusive use of the VM. From the extension checkout, use the existing fixed
schedule rather than the driver's broader defaults:

```bash
bash campaign/run_confirmation_batch.sh "$PWD" /home/bench/pgsl-confirmation
```

The output root must not already exist. The batch fixes the PostgreSQL binaries
at `/home/bench/pg-release/bin`, PostgreSQL source at `/home/bench/postgres`, and
Forge executable at `/home/bench/forge/bin/forge`. It stops on the first failed
phase and records phase starts/completions in `batch-status.tsv`.

The three confirmation phases each use five independently randomized blocks,
three 30-second measured subtrials per cell, and a discarded ten-second warmup:

- `primary`: quiet SQL and hot warnings, clients 1/8, baseline/disabled/on,
  regular-file logging; 100 minutes including warmup.
- `saturation`: full-table misses, clients 8/16, baseline/on, regular-file
  logging; 33 minutes 20 seconds including warmup.
- `devnull`: hot warnings, clients 8/16, baseline/disabled/on, `/dev/null`
  logging; 50 minutes including warmup. This is a separate sink sensitivity.
- `optional_paths`: **pilot only**, reader/readwrite, clients 1/8,
  baseline/disabled/filtered/on; one block, three five-second subtrials and a
  three-second warmup; 4 minutes 48 seconds including warmup.

The batch's workload-and-warmup lower bound is **3 hours 8 minutes 8 seconds**
(2 hours 49 minutes of measured intervals). Actual wall time also includes
cluster setup/restarts, native instrumentation, reader completion, and object
resets. The optional-path pilot is not confirmation-quality performance evidence.

## Direct driver invocation and defaults

For an independently selected campaign, a complete direct invocation is:

```bash
python3 campaign/benchmark.py \
  --pg-bin /home/bench/pg-release/bin \
  --postgres-source /home/bench/postgres \
  --forge /home/bench/forge/bin/forge \
  --output /home/bench/pgsl-separate-campaign
```

The driver defaults are broader than the batch: five blocks,
baseline/disabled/on, quiet SQL/hot warnings/64-key warnings/full-table misses,
clients 1/4/8/16, three 30-second subtrials and a ten-second discarded warmup.
They require **6 hours 40 minutes** of workload plus warmup (six hours measured),
before setup and native instrumentation overhead. Confirmation mode requires at
least five blocks, 30-second trials, and ten-second warmup; all modes require at
least three measured trials for the native exact-count claims.

Full-table misses use the default 1024-entry capacity. File-sink campaigns write
identically formatted server stderr to a regular file in every treatment. This
is not a collector-on production-log benchmark.

Correction (2026-09-09): this document previously described the older
10-second/three-second, clients 1/8/16 defaults as 90 minutes of timed work and
omitted the now-required PostgreSQL source argument. The figures above describe
the current driver and the separate preregistered batch.

The driver owns only its newly created output/pgdata cluster. It refuses an
existing output directory, creates a Unix-socket-only server, and stops it on
exit. It verifies assertions are disabled, source SHA, and effective GUCs, and records
installed binary hashes. Do not run other database or build work on the VM during
measurements. The load generator shares the VM with PostgreSQL; CPU competition
is an explicit limitation to inspect in the retained system observations.

Setup, extension reset, and full-table prefill occur before each native run.
PostgreSQL restarts only between native runs. Each trial reads counter deltas,
checks successful transaction counts and exact recorded-plus-dropped warning
accounting, and aborts on mismatch. Deterministic claims do not claim absence
of performance overhead: the paired block analysis is separate. Native run
selection requires exactly one newly created run record and a newly created mint
receipt for that invocation; an older successful record cannot stand in for a
failed invocation.

## Evidence and inference

- `plan.json`: randomized schedule, source/harness pins, binary hashes and build
  configuration. Each arm also retains its exact manifest and execution input.
- `raw/<forge-run-id>/cN-trialN/`: pgbench stdout/stderr, workload SQL and result
  JSON; trial zero is discarded warmup. This is outside the native artifact
  directory because upstream Forge rejects unknown artifact producer names.
- `corpus/cases/*/runs/`: real Forge run records, mint receipts and narrow TSVs.
- `corpus/cases/*/artifacts/`: native bounded evidence and verdicts.
- `scratch/`: unbounded raw OS observation streams; retain alongside the corpus.
- `trials.json`: all measured trial rows. `summary.json`: block-paired treatment
  ratios with percentile bootstrap confidence intervals (20,000 deterministic
  whole-block resamples), and Student-t log-ratio intervals as sensitivity.

Three subtrials are reduced to their median within each treatment/block/cell.
For confirmation phases, five blocks, not fifteen subtrials, are the independent
replication units. The one-block pilot cannot estimate between-block uncertainty.
Five observations provide limited interval precision; bootstrap bounds are not
proof of no effect. Every cell is reported, including negative overhead.

Forge's minimal claim parser requires flow-style nested lists; the driver emits
ordinary YAML with `invariant_across: [workload]`. JSON is accepted by shell
validation but not by the Python claim parser, so a JSON-named-YAML file is not
used. This integration preserves upstream code and works within its contract.

## Targeted campaigns

Use unique output directories. `--workloads reader,readwrite` covers a concurrent
10 ms statistics reader and small indexed read/write transactions. `--treatments
baseline,disabled,filtered,on` adds the enabled-but-filtered path. `--log-sink
devnull` is an explicitly different sink sensitivity, not a primary result.
Use `--clients` to select concurrency explicitly; the direct driver already
defaults to 1/4/8/16. Retain actual arguments and avoid pooling distinct campaigns
or logging sinks.

The read/write workload truncates, repopulates, vacuums, and checkpoints its
100,000-row table before **every** trial. This prevents carryover of prior-arm
bloat, but also resets the object after the discarded warmup: measured trials
are fresh-object intervals, not warmed steady-state write performance. They are
a custom indexed transaction workload, not the standard pgbench read/write mix.

Smoke runs add `--phase pilot --blocks 1 --clients 1 --duration 1 --warmup 1`
to the complete direct invocation above, with a unique output directory. They
retain the three-trial minimum and are harness validation only, not admissible
performance results.
