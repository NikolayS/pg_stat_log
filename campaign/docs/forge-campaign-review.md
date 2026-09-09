# Recovered campaign harness review

2026-09-09. Read-only review of `/home/tars/clawd-samjr/projects/pg-stat-log-campaign/pg_stat_log/campaign/benchmark.py`, `build.sh`, `forge/repro.sh`, `forge/assert.sql`, README and PLAN. No benchmark ran in this review. Line references refer to the recovered pre-review version.

## Native Forge integration

The claim of native Forge execution is supported: outer driver invokes `forge case run --record --non-interactive` (lines 419–422); generated repro execs deterministic Python `--point`; output uses expected measurement columns; FORGE_EXEC is explicit; cluster restarts occur before runner invocation; record steps, restart state, validity and exact-count verdicts are checked. Runner reset_stats.sql does not call pg_stat_log_reset, so the intentional full-capacity prefill survives normal Forge resets.

This is not complete standalone case portability: generated repro requires CAMPAIGN_DRIVER/CAMPAIGN_CONFIG and setup.sql is a placeholder. Reproduction must publish/preserve the outer driver, config, build inputs and schedule together. That is acceptable if stated, not if presented as independently replayable generated case alone. `assert.sql` only checks database liveness; warning invariants are asserted in Python and mechanically evaluated through Forge's CSV claims.

Timing is not in the typed Forge claims: only exact warning accounting and no failed transactions. Consequently a green run does NOT prove bottleneck panel clearance for timing claims. Existing summary caveat correctly says so. Keep CPU/steal/I/O evidence and classify performance conclusions independently.

## Findings to handle before the relevant workload

1. **Read/write state differs by randomized client history.** `campaign_kv` is reset/vacuumed once per arm (near line 382), while autovacuum is off. Each trial and later client point performs more writes to the same object. Randomized client order means a given client count sees different accumulated update/bloat/cache history across treatments. Reset/prepare/vacuum/checkpoint outside each trial's measured interval, with identical policy across arms; or exclude readwrite from confirmation and say why. Generated manifest incorrectly says no data table for all workloads (line 194), and setup.sql incorrectly says setup occurs before every trial (line 410). Fix those descriptions with implementation.

2. **Reader can silently fail.** run_reader returns Popen immediately (lines 89–97); the main path does not check readiness, liveness or completed transactions. It later terminates/waits but never rejects an early nonzero exit. A failed auxiliary client can leave the writer arm green without contention. Require successful startup/query evidence and expected liveness throughout measured interval; save final status/transaction count. Prove negative test by deliberately invalidating reader SQL. Default workloads do not include reader, so explicitly omit until fixed if needed.

3. **Confirmation durations need explicit override.** Defaults are 3 s warmup/10 s measurement (near lines 284–285), appropriate pilot but weak confirmation. Use 20–30 s warmup and at least 60 s measurements for primary repeated blocks. Save exact command; do not silently relabel pilot. Larger intervals particularly matter for log filesystem pressure and scheduling noise.

4. **Build SHA provenance is declared, not fully verified here.** PG_SHA hardcoded at file top; extension C SHA compared to upstream source and installed binary hashes captured, but a binary hash alone does not bind it to declared PG SHA. Read build provenance manifest / verify source clean SHA and build logs before accepting. build.sh uses supplied source directory and git archive HEAD; retain archive/source commit and object build logs with installed hashes. Current session may already have this evidence elsewhere.

## Statistical assessment

Reasonable parts: seeded randomized schedule written before execution; pairing by workload/client/block; median within-block trials; geometric mean of block ratios; uncertainty based on blocks, not transactions; invalid runs excluded. Bootstrap resamples paired block ratios, preserving primary dependence. Five blocks make intervals coarse and bootstrap coverage fragile; publish all block ratios plus Student-t sensitivity, not just a narrow percentile CI. These are closed-loop throughput/mean-latency results, not p95/p99 or latency-SLO evidence.

Potential refinement, not a blocking correctness defect: schedule groups all workloads by treatment inside a block. Pairing can span minutes and suffer temporal drift. For longer matrix, shuffle workload/client blocks first and balance treatment order within each such block, or retain current approach and make blocks short with explicit temporal plots. Existing seeded random shuffle is not guaranteed balanced with five blocks. No statistical significance stopping rule is implemented; decide extra repeat criteria before observing primary results.

The t-critical lookup defaults to df=10 for every df>9. With default five blocks it is correct (df=4); for more than 11 blocks it becomes conservative rather than exact. State supported block range or implement quantiles if extending. No need to change for the default.

## Non-findings and scope limits

- Full-miss fill invariant is checked at 1024 entries; Forge does not erase it.
- Warnings use common actual stderr sink across treatments; primary file logging captures real baseline logging overhead, with devnull a separately labeled sensitivity. Do not call enabled-minus-baseline pure hook cost if storage saturation masks it.
- Optional filtered treatment is supported but excluded by default. Primary command should include it if plan says all four arms were covered.
- Current expected settings check verifies extension state and logging floor, but not every common nondefault GUC. Captured full settings enables later comparison; assert a fixed subset including shared_buffers/jit/autovacuum/durability if extending tests.
- No actual values or runtime findings were inferred from source review.

## Implemented follow-up in recovered copy only

Parent authorized implementation after initial read-only review. Changed `/home/tars/clawd-max/pg-stat-log-study/recovered/extension/campaign/benchmark.py` and added `campaign/test_benchmark.py`; original recovered source under clawd-samjr untouched. No commits/push/remote workload.

- Read/write table now reset, vacuumed, checkpointed and exact 100000 zero-valued rows verified before every trial, outside timing.
- Auxiliary reader SQL is preflighted; argv retained; early failure rejected; natural successful completion and nonzero transaction count required; cleanup escalates from terminate to kill if needed. Reader tail excluded from recorded wall elapsed.
- Default duration 30 s, warmup 10 s, clients 1/4/8/16. Explicit `--phase pilot` permits short exploratory runs; confirmation requires >=5 blocks, >=3 trials, >=30 s duration, >=10 s warmup. Plan records phase, stopping rule, timed lower bound and statistical unit before measurements.
- Required `--postgres-source` checks actual clean tracked source HEAD against campaign PG_SHA. Still retain build logs to bind binary hashes to source; this alone is not an installed-binary attestation.
- Effective jit/autovacuum/fsync/synchronous_commit values now verified with each point. Manifest object description and setup timing comment corrected.
- Five focused unit tests RED against original driver (missing validity/reset primitives), GREEN after changes; log files alongside this note. These are guard unit tests, not live reader failure or read/write performance validation. Parent should run one short optional-workload pilot if those workloads will be included.

### Frugal schedule

Default full confirmation across 4 workloads ×4 client points ×3 arms ×5 blocks ×(3×30+10) seconds is 24000 s =6h40, plus startup/Forge overhead; not 2–3h.

Recommended two-stage fixed plan:

1. Broad pilot: `--phase pilot --blocks 1 --trials 3 --duration 5 --warmup 3 --clients 1,4,8,16 --workloads quiet,hot,cardinality,full_miss --treatments baseline,disabled,on` =864 s≈14.4min plus overhead.
2. Focused confirmation: `--phase confirmation --blocks 5 --trials 3 --duration 30 --warmup 10 --clients 1,8 --workloads quiet,hot --treatments baseline,disabled,on` =6000 s=100min plus overhead.

This confirms the preselected quiet-hook and hot-counter cases rather than selecting only dramatic pilot effects. Treat cardinality/full_miss pilot as exploratory, or add a separate explicitly scheduled confirmation if it produces a correctness issue or qualitatively different mechanism. Filtered/reader/readwrite remain explicit coverage decisions, not presumed completed. Commands also need --output, --pg-bin, --forge and new --postgres-source paths. Budget around 2–3h with overhead, monitor actual trial progress, never truncate queued measurements and call complete.
