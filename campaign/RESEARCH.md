# Session, prior work, and submitted-patch research

Research date: 2026-09-09. These are source observations and test hypotheses,
not benchmark results. See campaign results for empirical findings.

## Commitment and discussion

The [September 2 session](https://hacking.postgres.tv/sessions/Et6WSCdR3Yw/)
summary assigns Nikolay independent pg_stat_log benchmarking and review.
Relevant chapters start at 59:35 (pg_stat_log) and 1:13:20 (homework).
The page and its [source data](https://github.com/NikolayS/hacking-postgres/blob/main/src/data/session-details.ts)
provide summary and timecodes, not a full transcript. A full transcript was
not available in the inspected resources; this campaign does not claim a
word-for-word review of the video.

The [pgsql-hackers thread](https://www.postgresql.org/message-id/flat/CABo-N96sjT0KwtCFVBkuMQEAfe-834Sd2zjbv9qKJORXkf=BiA@mail.gmail.com)
contains the August 18 proposal, Kirk Wolak's September 2 endorsement and
explicit expectation that Nikolay will check performance, and Daniel
Gustafsson's September 3 objection to new contrib modules. Daniel requests a
reason to put this functionality in core rather than an external extension.
Performance evidence can inform that debate but does not resolve placement.

The proposal claims low-single-digit overhead in a saturated log storm and
no measurable overhead for workloads that do not log. These are hypotheses
for independent verification, not campaign findings. Its performance case
is particularly sensitive to logging sink, table occupancy, key distribution,
client concurrency, and the denominator used to calculate overhead.

[CommitFest entry 7162](https://commitfest.postgresql.org/patch/7162/) targets
PostgreSQL 20. At inspection its latest-email metadata omitted the September
replies; use the actual mailing-list thread to establish discussion history.

## Previous work

- [Issue 1](https://github.com/NikolayS/pg_stat_log/issues/1): initial PG18.3
  code review, access-control concerns, counting tests and documentation gaps.
- [Issue 5](https://github.com/NikolayS/pg_stat_log/issues/5): persistence,
  reentrancy, metadata and performance findings.
- [Issue 6](https://github.com/NikolayS/pg_stat_log/issues/6): source-pattern
  research and proposed hardening.
- [PR 2](https://github.com/NikolayS/pg_stat_log/pull/2): named LWLock tranche.
- [PR 3](https://github.com/NikolayS/pg_stat_log/pull/3): drop telemetry and
  slot reclamation on reset.
- [PR 4](https://github.com/NikolayS/pg_stat_log/pull/4): operational caveats.

Those issues concern earlier revisions. Some claims are speculative or
internally qualified, and several defects have since been addressed. Do not
carry old severity labels or performance numbers forward without reproducing
them at the pinned campaign revision. Current code already moves recursion
protection around the previous-hook call, restricts reset privileges, caps
max_entries, removes the reset-offset block, and uses a hash rather than a
full linear scan.

## External versus submitted contrib implementation

Inputs:

- [External upstream](https://github.com/fabriziomello/pg_stat_log), pinned
  campaign source (full SHA in provenance).
- [Submitted v1 patch](https://www.postgresql.org/message-id/attachment/201905/v1-0001-Add-pg_stat_log-contrib-module.patch).
- PostgreSQL master `86f7c82cf1023e3599f40f939727791a7090cd44`.

A direct source comparison found identical counting, hash, snapshot, reset,
reporting, persistence callbacks, and emit-hook implementations. The SQL
installation script is identical; TAP differs only in copyright text.
The in-tree C adaptation removes PG18 compatibility branches and the
shmem-startup wrapper (which is a no-op except chaining previous hooks on
master), plus changes comments, identification, copyright and formatting.
It adds in-tree Make/Meson integration and SGML documentation.

`git apply --check` succeeded against all four affected existing files from
the exact master SHA; new contrib/doc files were absent as expected. This is
an applicability check, not proof of successful compilation or testing.
`verify_contrib.sh` performs the separate full in-tree assertion build and
native `make check`; its recorded exit status establishes execution results.
It deliberately uses independent source/build/temp-install trees so it does
not replace the benchmark extension or baseline PostgreSQL binaries.

One main performance matrix can therefore characterize the common hot path,
with the equivalence and build provenance made explicit. The native in-tree
build and test gate remains necessary: packaging integration is not covered
by external PGXS tests.

## Source-derived test priorities

1. **Server severity ordering.** The hook uses numeric `elevel >= threshold`;
   PostgreSQL gives LOG special server-log ordering. Exercise LOG with
   warning/error/log thresholds and distinguish documented intent from actual
   behavior. Do not call a semantic disagreement proven until reproduced.
2. **Resize and restore ordering.** Existing TAP exercises 64 to 128 but not
   shrink. Test both directions with filled tables and unrelated core stats.
   `init_backend_cb` validates capacity, but determine whether restore/reset
   paths use persisted dimensions before that validation.
3. **Hook chaining.** Test both preload orders, nested warnings and a previous
   hook that suppresses server output. The early process-state guard returns
   before invoking the previous hook, despite a comment promising forwarding.
4. **Concurrency conservation.** Assert exact tracked plus dropped message
   counts, continued hits at saturation, and reset reclamation. Stress readers,
   writers and resetters; absence of a crash alone is not accounting proof.
5. **Snapshot consistency.** Compare transaction-cached data with the live
   metadata getter; exercise explicit snapshot clearing and reset visibility.
6. **Identity and privileges.** Multiple databases/users, SET ROLE, security
   definers, authentication failures, dropped database/role OIDs; ordinary,
   monitoring and superuser access.
7. **Lifecycle.** Clean restart, crash, preload removal/re-addition, extension
   SQL drop/recreate, and changed capacity. Distinguish preserved counters
   from non-persisted drop metadata and reset timestamps.
8. **Logging floor and real errors.** Caught exceptions and client-only
   messages should not become emitted-log counts. Test real SQLSTATEs,
   custom states, deadlocks, serialization errors, WARNING, ERROR and FATAL.

## Benchmark interpretation

Compare no preload, preloaded disabled, and enabled with identical logging.
Include quiet workloads, one hot signature, diverse keys, saturation with
existing-key hits and saturation with new-key misses. Randomize blocked
repeated runs and retain every repeat. Report paired effects with uncertainty,
not only best runs. On one 8-vCPU host explore 1/4/8/16 clients and describe
oversubscription explicitly.

Run a realistic log destination as well as any low-I/O diagnostic sink. Do
not present a `/dev/null` diagnostic as a production logging result. Record
CPU, steal time, log bytes, disk pressure and client limits. Read-side
monitoring needs its own experiment: snapshots copy the entire configured
block even when few entries are live, so high capacity can amplify observer
cost. The per-count global exclusive LWLock is the primary contention
hypothesis; profiling belongs on representative regressions rather than every
run. Assertions/sanitizers are correctness tools and should not silently
replace optimized production-like benchmark builds.
