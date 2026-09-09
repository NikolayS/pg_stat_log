# Adversarial correctness campaign

## Scope and reproducibility

- PostgreSQL master `86f7c82cf1023e3599f40f939727791a7090cd44`.
- Extension upstream `0a8b782c0695883a2708ac60ebf32e1a95250952` (the new index-chained hash implementation).
- Separate release (`-O2`, debug symbols) and assertion-enabled (`-O1`, `--enable-cassert`) builds.
- Disposable non-root clusters, isolated Unix sockets, no TCP listener, 64 initial slots.
- `correctness.py` uses only Python standard library and installed PostgreSQL executables. It refuses an existing work directory and never attaches to an existing database.
- Optional PGXS helper `hook_probe` is test-only and exercises existing hook integration; it is not extension production code.

```sh
make -C campaign/hook_probe PG_CONFIG=/path/to/pgsql/bin/pg_config install
python3 campaign/correctness.py \
  --pg-bin /path/to/pgsql/bin \
  --workdir /tmp/pg-stat-log-correctness-unique \
  --hook-probe
```

Each run produced **150 assertions: 131 pass, 19 failed contract expectations, zero harness errors**. The same assertion names failed on both builds. These are not 19 independent implementation bugs. Classification: 14 threshold mismatches, two suppressed-output mismatches, two recursion/load-order limitations, one documentation/label mismatch. No runtime assertion failure, deadlock, or server crash was observed, except the deliberately requested immediate shutdown and self-termination. This is bounded functional evidence, **not a memory-safety proof**.

Raw assertions, exact command/SQL transcripts and server logs are in `results/correctness-release/` and `results/correctness-assert/`. The assertion build additionally retains `parallel_plan.json` and `parallel_rows.json`; instrumentation was added after investigating the first run, without changing test expectations. The original release assertion labels remain unedited as an audit trail.

## 1. LOG severity ordering: confirmed semantic mismatch

The extension compares `edata->elevel >= pg_stat_log_min_elevel` (`pg_stat_log.c:438`). PostgreSQL's server log filtering instead gives LOG a special rank between ERROR and FATAL (`elog.c:is_log_level_output`). `server_message_level_options` is shared with the server GUC, but numeric comparison does not implement that ordering.

Fourteen cells fail in the 72-cell matrix (six message levels × six extension thresholds × two server-log floors):

- A LOG message is not counted with extension threshold INFO, NOTICE, WARNING, or ERROR, although the message reaches the server log. This repeats with server floor WARNING and DEBUG1: eight failures.
- Extension threshold LOG counts WARNING and ERROR at server floor WARNING; it also counts INFO and NOTICE at server floor DEBUG1: six failures.

Minimal example, issuing the statements as separate commands where appropriate:

```sql
SET log_min_messages = warning;
SET pg_stat_log.min_error_level = warning;
SELECT pg_stat_log_reset();
DO $$ BEGIN RAISE LOG 'severity test' USING ERRCODE = 'Z1001'; END $$;
SELECT coalesce(sum(count), 0)
FROM pg_stat_log_data() WHERE sqlerrcode = 'Z1001';
-- Observed 0; server-log severity ordering implies 1.
```

Recommendation for review: implement the server log ordering explicitly (or document a deliberately different ordering). Do not silently treat PostgreSQL's LOG enum value as its server-log severity rank.

## 2. Other hooks can suppress output that is still counted

In both preload orders, the test helper sets `edata->output_to_server = false` for one marker WARNING. The marker is verified absent from the new server-log bytes, yet `pg_stat_log` records count 1. Both runs reproduce this.

`pg_stat_log` forwards to its previous hook before counting, but does not recheck `output_to_server`. The observed result contradicts a literal interpretation of the README statement that counters represent messages which actually reach the server log. It does not imply ordinary log counting is broken without a suppressing hook.

Recommendation: decide whether the intended contract is “hook events eligible for logging” or “messages still destined for the log after chained hooks”; document it and, for the latter contract, honor the flag. Hook ordering can matter if a later hook changes the flag *after* invoking the preceding chain.

## 3. Guarded nested hook: load-order-dependent limitation

When the helper loads first and `pg_stat_log` second, the helper emits a guarded nested WARNING while inside the extension's outer callback. The nested warning reaches the server log, but the extension's recursion guard returns before forwarding or counting it. Observed:

- Helper callback calls: 1 rather than 2.
- Outer warning counter: 1.
- Nested warning counter: 0 rather than 1.

With the reverse preload order, callback calls are 2 and both counters are 1. Ordinary forwarding passes in **both** orders when collection is on, off, or severity-filtered.

This is classified as an **integration limitation/design trade-off**, not an unsafe demand to remove the recursion guard. Avoiding recursive error-path failure is important. The unqualified source/README hook-forwarding wording should account for guarded reentrancy.

## 4. Parallel workers are labelled “background worker”

The README promises `backend_type = 'parallel worker'`. The implementation stores `MyBackendType` and renders `GetBackendTypeDesc`; on tested master parallel workers are the generic background-worker backend enum.

The initial failure was investigated rather than interpreted as lost messages. EXPLAIN ANALYZE confirms four workers launched and partial aggregation beneath Gather. An isolated diagnostic returned 3,390 client-backend warnings plus 6,610 background-worker warnings. The assertion run returned 3,164 plus 6,836. Both totals are **exactly 10,000**, matching the generated rows. The worker counts vary with scheduling; those splits are not fixed expectations.

This is a **documentation/attribution-granularity mismatch**, not a dropped-count failure. Report generic background-worker attribution accurately, or design a separate subtype if parallel-worker distinction is required. Tests use `client_min_messages=error` to avoid client-message forwarding confounding message-conservation checks; they do not claim one physical log record per logical error under every client/log configuration.

## Passing evidence

Both builds passed:

- Exact single-writer count of 1,000 and eight concurrent writers totaling 8,000.
- Disabled collection, re-enabled collection, and uncounted caught PL/pgSQL exceptions.
- SQLSTATE-to-name mapping and NULL name for an unknown custom SQLSTATE.
- Independent database/user key attribution, active-role attribution, and self-termination FATAL/57P01 attribution.
- Unprivileged read/reset/GUC denials, monitoring-role read access, and monitoring-role reset denial.
- Transaction snapshot semantics for NONE, CACHE, SNAPSHOT and explicit snapshot clear.
- Sequential 64-slot saturation with exact drop counts; existing-key increments continue when full.
- Eight concurrent distinct-key writers: exactly 64 counted + 736 dropped = 800 messages, without duplicate keys.
- Persistence of every saturated entry across clean restart, then successful hash lookups/increments for every persisted key.
- Reset clears counts/drop metadata and reclaims slots; startup resets only non-persistent drop/timestamp metadata.
- Immediate shutdown/recovery discards old counters.
- Saturated capacity changes **64 → 128 → 1,024 → 64**, discarding old stats and accepting new writes after each restart.
- Concurrent reset/read/write completion and exact post-race write integrity. No exact intermediate count is asserted across resets.
- Hook forwarding under enabled, disabled, and severity-filtered operation in both preload orders.

## Boundaries

These tests are not exhaustive coverage of every auxiliary process type, authentication method, crash timing, corruption scenario, platform, or possible hook. PANIC fault injection, arbitrary persisted-file corruption, and adjacent-memory corruption probes were not performed. Do not infer memory safety merely because resize observations pass. Windows/EXEC_BACKEND, enormous capacities, long-duration soak behavior, and non-log-related transaction semantics need separate evidence if included in submission claims.

## Additional operational suite (assertion build)

`extra_correctness.py --skip-boundary` subsequently completed on a fresh cluster with **15 pass, one failed contract expectation, zero harness errors**. Artifacts are in `results/extra-operational-assert/`.

Additional passing checks: deterministic two-session row-lock deadlock (one victim, one survivor, exactly one `40P01` event); nonexistent database (`3D000`) with NULL database OID; HBA rejection (`28000`, no password/token involved); extension DROP/CREATE preserving preloaded collection (9 + 7 = 16); dropped-role and dropped-database OIDs preserved while names become NULL; healthy server after preload removal and collection restored after readdition.

### Unrelated core counters are lost on extension capacity change

After inserting 100 rows into an ordinary table and verifying `pg_stat_user_tables.n_tup_ins = 100`, a clean restart changing only extension capacity from 64 to 128 yielded `n_tup_ins = 0`. This is a **confirmed operational consequence**, not merely loss of extension counters. The test preserves both observed values and the restart log. The desired-contract assertion that unrelated cumulative counters survive therefore fails. Review core fixed-stat-file framing and extension layout compatibility; do not promise that only extension stats are discarded on resize.

### Boundary-test oracle correction

The first extra harness incorrectly assumed invalid custom preload GUC values must make startup fail. PostgreSQL can instead log an out-of-range warning and retain the GUC's declared default. Reusing one cluster also mixed capacity transitions into boundary checks. The revised `--only-boundary` mode gives each of 63, 64, 1,048,576 and 1,048,577 a **fresh initdb**; checks startup logs and actual `SHOW`; and accepts explicit rejection or warned default fallback for invalid values. Original failed-run artifacts are retained rather than relabelled. The operational run above excludes this boundary phase. Any crash investigation from the earlier reused-cluster run is tracked separately; it must not be misreported as ordinary rejection of an invalid GUC value.

## Fresh controls and startup backtrace (Max takeover)

Issue [#10](https://github.com/NikolayS/pg_stat_log/issues/10) records the controlled comparison: same64→64 retains ordinarytable100counter;64→128 and128→64 reset it to0. All32 controls ran:30pass,two failed preservation expectations; pg_monitor read/reset authorization tests pass.

The recovered large1048576→64 shutdown/restart logs show SIGSEGV in optimized and assertion builds. A fresh assertion-build reproduction records the same startup failure and a debugger backtrace at `pg_stat_log_count_message():374`, invoked while core reports a statsfile-restore warning. See `results/resize-repro-assert/backtrace.txt`. The observed path is counting, not proof that the separately identified reset-callback bounds concern caused this particular crash.

Fresh capacity-boundary suite passes all10 assertions. All maximum values were set through ordinary configuration; no arbitrary memory or persisted-file corruption was injected. These fresh results supersede earlier local-evidence gaps noted in the audit.
