# PostgreSQL master validation campaign

Tracking: [issue #7](https://github.com/NikolayS/pg_stat_log/issues/7), [PR #8](https://github.com/NikolayS/pg_stat_log/pull/8).

Read [PLAN.md](PLAN.md) and [RESEARCH.md](RESEARCH.md) for scope and source provenance.

## Reproduce

Use an isolated disposable host. Never point this harness at production. Build/test as a non-root user. All clusters are private Unix-socket instances. Existing output directories are rejected, preserving prior evidence.

```sh
git clone https://github.com/postgres/postgres.git postgres
git -C postgres checkout 86f7c82cf1023e3599f40f939727791a7090cd44
# See build.sh header for OS packages. Absolute directories recommended.
bash campaign/build.sh "$PWD/postgres" "$PWD/builds"
python3 campaign/correctness.py --pg-bin "$PWD/builds/pg-assert/bin" \
  --workdir "$PWD/correctness-assert" --hook-probe
```

The adversarial suite deliberately asserts documented/expected contracts, not just existing implementation behavior. A nonzero exit can therefore represent a reproduced semantic discrepancy. Consult each assertion's category, expected/actual fields, the server log, and the findings document; do not call every failure a crash or silently bless it.

## Native DBLab Forge

Obtain `https://gitlab.com/postgres-ai/forge`, checkout `feat/forge-bootstrap` at the SHA in the run plan (main was scaffolding at campaign start). Install Bash, Python 3, Mike Farah `yq` v4, `sysstat`, `procps` and PostgreSQL client tools. Preserve Git metadata for recorded provenance.

```sh
python3 campaign/benchmark.py --pg-bin "$PWD/builds/pg-release/bin" \
  --forge /absolute/forge/bin/forge --postgres-source /absolute/postgres --output "$PWD/primary"
```

The driver generates native Forge cases and runs them through `forge case run --record --non-interactive`. PostgreSQL restarts happen between native runs, never within one. Cluster configuration, exact-count checks and randomized schedule are recorded. The statistical unit is the independent treatment block; within-block trials are summarized by their median. Confidence intervals resample paired blocks, not individual transactions. A Forge invariant pass is not proof of negligible performance overhead.

Primary timing uses real server-log files, same sink and warning-generating SQL across treatments. `--log-sink devnull` is a diagnostic sensitivity, not realistic storage behavior. `--workloads reader,readwrite` adds observer and SQL read/write sensitivity; `--treatments baseline,filtered,on` checks filtering-path overhead. All campaign processes share one VM, so pgbench client cost and virtual-machine limitations remain explicit.

```sh
python3 campaign/benchmark.py --summarize /absolute/primary
```

Inspect `plan.json`, `status.json`, `trials.json`, `summary.json`, `hardware.json`, native Forge corpus and per-trial raw artifacts. Counters must account for each successful warning as counted or dropped; failed transactions invalidate a cell. Timing alone never verifies workload correctness.

## Contrib proposal

Download the exact v1 attachment linked in RESEARCH.md, record its SHA256, then:

```sh
bash campaign/verify_contrib.sh /absolute/postgres /absolute/v1.patch /absolute/contrib-check
```

This builds/tests an independent patched tree and does not overwrite baseline binaries. It must not run concurrently with timed benchmarks.

## Scope limits

One Linux/x86-64 dedicated-vCPU VM is not a portability, bare-metal, long-soak, crash-fuzzing, or production-SLO certification. We preserve semantic failures and report limitations explicitly. New build/timing failures must be diagnosed and failed artifacts retained, not overwritten or substituted with guessed values.
