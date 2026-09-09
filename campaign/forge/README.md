# Native Forge integration

`../benchmark.py` runs the pinned upstream Forge executable, not an imitation of
its command interface. It creates an isolated corpus under the output directory,
validates typed exact-count claims, takes native exclusivity locks, collects
baseline/after PostgreSQL snapshots and OS observations, and writes real mint
receipts, ledger records and claim verdicts with `case run --record`.

Required tools: the optimized PostgreSQL build (including pgbench), Bash,
Python 3, Go yq v4.44.3, sysstat, procps, and the complete Forge checkout pinned
at `fc7284fa74a1286fe302c82f3512187b9f4160f0`. The upstream default branch is
only scaffolding; this commit comes from `feat/forge-bootstrap`.

Run on the dedicated benchmark host as its unprivileged `bench` user:

```bash
python3 campaign/benchmark.py \
  --pg-bin /home/bench/pg-release/bin \
  --forge /home/bench/forge/bin/forge \
  --output /home/bench/pgsl-primary
```

Defaults: five independently randomized treatment blocks, baseline/disabled/on,
quiet SQL/hot warnings/64-key warnings/full-table misses, clients 1/8/16, three
10-second subtrials per cell and a discarded three-second warmup. Full-table
misses use the default 1024-entry capacity. Logging writes identical formatted
server stderr to a regular file in every treatment. It is not a collector-on
production-log benchmark. The default matrix has 90 minutes of timed work,
plus setup, warmup and native instrumentation overhead.

The driver owns only its newly created output/pgdata cluster. It refuses an
existing output directory, creates a Unix-socket-only server, and stops it on
exit. It verifies assertions are disabled, installed binary hashes, source SHA,
and effective GUCs. Do not run other database or build work on the VM during
measurements. The load generator shares the VM with PostgreSQL; CPU competition
is an explicit limitation to inspect in the retained system observations.

Setup, extension reset, and full-table prefill occur before each native run.
PostgreSQL restarts only between native runs. Each trial reads counter deltas,
checks successful transaction counts and exact recorded-plus-dropped warning
accounting, and aborts on mismatch. Deterministic claims do not claim absence
of performance overhead: the paired block analysis is separate.

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
Five blocks, not fifteen subtrials, are the independent replication units.
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
`--clients 1,4,8,16` adds the intermediate concurrency point. Retain actual
arguments and avoid pooling distinct campaigns or logging sinks.

Smoke runs use `--blocks 1 --clients 1 --duration 1 --warmup 1` and are harness
validation only, not admissible performance results.
