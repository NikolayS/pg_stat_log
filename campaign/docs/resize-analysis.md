# Capacity-change persistence analysis

Read-only source analysis, 2026-09-09. No extension or core changes and no database execution. Sources pinned to external extension `0a8b782c0695883a2708ac60ebf32e1a95250952` and Postgres master `86f7c82cf1023e3599f40f939727791a7090cd44`.

## Why unrelated core counters disappear

The immediate mechanism is **global statistics-file rejection**, not a SQL reset request against each table.

1. Extension `_PG_init` computes `stats_block_size = offsetof(PgStatLog,data) + max_entries * (sizeof(PgStatLogSlot) + sizeof(int32))`, registers that as `shared_data_len`, and enables fixed-kind persistence. Changing the GUC changes the serialized body length. [Extension lines 528–546](https://github.com/fabriziomello/pg_stat_log/blob/0a8b782c0695883a2708ac60ebf32e1a95250952/pg_stat_log.c#L528).
2. Core writes each fixed entry as an entry tag, kind ID, and exactly `shared_data_len` raw bytes. It does **not** record a per-entry payload length or per-custom-layout version. [Core writer lines 1712–1736](https://github.com/postgres/postgres/blob/86f7c82cf1023e3599f40f939727791a7090cd44/src/backend/utils/activity/pgstat.c#L1712).
3. Reader selects the currently registered kind descriptor and reads the CURRENT `shared_data_len` directly into its shared-memory body. It has no old-length field to skip incompatible payloads safely. [Reader lines 1947–2001](https://github.com/postgres/postgres/blob/86f7c82cf1023e3599f40f939727791a7090cd44/src/backend/utils/activity/pgstat.c#L1947).
4. On growth, it can consume later entries as part of this body or encounter early EOF. On shrink, it leaves part of the old body to be interpreted as another record tag. The usual resulting short read, invalid tag, or invalid kind follows the file's global error path. Particular byte patterns may affect exactly where rejection occurs; do not assume every malformed body fails at the first byte.
5. That path logs file corruption and calls `pgstat_reset_after_failure`. This calls every registered fixed-kind reset callback and drops ALL variable-kind entries, including relation statistics. [Error and reset lines 2190–2221](https://github.com/postgres/postgres/blob/86f7c82cf1023e3599f40f939727791a7090cd44/src/backend/utils/activity/pgstat.c#L2190).

Therefore the observed `pg_stat_user_tables.n_tup_ins` 100→0 after a 64→128 clean restart is consistent with the global recovery path. This is broader than loss of pg_stat_log's own counters. An unchanged-capacity restart control is still appropriate to make the runtime comparison causal; source inspection is not a replacement for that control.

## Responsibility boundary

Core's fixed-stat file format assumes the registered fixed body layout matches the file; global reset is its defensive response to a broken file. The extension makes that body configuration-dependent while promising supported capacity changes. The compatibility failure is at this interface, not evidence that ordinary relation-stat persistence independently fails on unchanged configurations.

The desired property—discard only this extension's incompatible data while preserving other kinds—cannot be achieved merely by repairing its header in a later backend callback. The reader has already consumed the stream using the wrong length. A core design permitting independently framed/versioned custom payloads or an extension design with a stable persisted layout would address that boundary. Such design choices need review; this report does not apply a fix.

Master exposes a `finish` callback, but its documented timing is **after all entries were processed**, and the error branch invokes global reset BEFORE finish callbacks. A finish-only header check cannot prevent either the earlier global reset or unsafe reset operations. [Callback declaration lines 343–352](https://github.com/postgres/postgres/blob/86f7c82cf1023e3599f40f939727791a7090cd44/src/include/utils/pgstat_internal.h#L343), [dispatch lines 2178–2197](https://github.com/postgres/postgres/blob/86f7c82cf1023e3599f40f939727791a7090cd44/src/backend/utils/activity/pgstat.c#L2178).

## Separate safety concern: stale persisted capacity used during reset

This is stronger than the counter-loss observation and merits targeted dynamic confirmation. Current code still uses the persisted capacity during an error-path reset:

- `PgStatLog` persists `max_entries` at the start of the copied body. A partial restore replaces the initialized current value with the old one. [Extension lines 91–96](https://github.com/fabriziomello/pg_stat_log/blob/0a8b782c0695883a2708ac60ebf32e1a95250952/pg_stat_log.c#L91).
- `pg_stat_log_heads(s)` calculates its address using `sizeof(PgStatLogSlot) * s->max_entries`. [Lines 147–150](https://github.com/fabriziomello/pg_stat_log/blob/0a8b782c0695883a2708ac60ebf32e1a95250952/pg_stat_log.c#L147).
- `reset_all_cb` sets only `num_entries=0`, then memsets heads using that SAME restored `s->max_entries`; it does not normalize capacity to the current allocation. [Lines 292–310](https://github.com/fabriziomello/pg_stat_log/blob/0a8b782c0695883a2708ac60ebf32e1a95250952/pg_stat_log.c#L292).
- The later `init_backend_cb` corrects capacity before its own memset, but it is not called inside the core reader between body copy and corruption/reset. [Extension lines 203–225](https://github.com/fabriziomello/pg_stat_log/blob/0a8b782c0695883a2708ac60ebf32e1a95250952/pg_stat_log.c#L203); [core backend initialization lines 686–702](https://github.com/postgres/postgres/blob/86f7c82cf1023e3599f40f939727791a7090cd44/src/backend/utils/activity/pgstat.c#L686).

For a typical tested x86-64 layout with slot size 32 and body header 8, a 64-slot allocation has body size `8 + 64*(32+4) = 2312` bytes. If a persisted 128 is copied into that body's header, reset computes heads at offset `8+128*32 = 4104` and writes `128*4 = 512` bytes. That lies beyond the allocated 64-slot body. The wrapper does not create extra space after the body. **This is an out-of-bounds address calculation on the described path**, not just a harmless stale count. Exact C layout must be confirmed on the diagnostic build; the relative argument also holds symbolically for these structures.

There is an earlier possible route: the reader's WARNING diagnostics can themselves reach this extension's logging hook while the restored body is inconsistent. The count path computes heads and bucket using restored capacity and follows raw slot indices without bounds validation. [Count path lines 362–370](https://github.com/fabriziomello/pg_stat_log/blob/0a8b782c0695883a2708ac60ebf32e1a95250952/pg_stat_log.c#L362), [enabled/threshold dispatch lines 438–439](https://github.com/fabriziomello/pg_stat_log/blob/0a8b782c0695883a2708ac60ebf32e1a95250952/pg_stat_log.c#L438). Thus a crash may precede the reset callback; a backtrace is needed to distinguish which path actually fired. Do not claim a particular crash site from arithmetic alone.

## Evidence language for public reporting

- Supported now: source explanation of global reset plus existing runtime observation of unrelated-counter loss.
- Supported statically: stale-capacity address calculation in reset can exceed the current allocation after shrink, with no intervening correction in this control flow.
- Needs dynamic confirmation: which error path executes first for a chosen capacity/fixture, actual affected address/backtrace, sanitizer or bounds instrumentation evidence, and behavior under repeated startup.
- A small shrink run that starts successfully does not disprove this safety issue; adjacent mapped memory may absorb an out-of-bounds write without an immediate signal.
- A large-shrink crash alone does not uniquely prove reset-callback corruption; use the stack and the restored/current values to identify the real path.

Primary raw source snapshots saved alongside this report as `pgstat-86f7c82.c`, `pgstat_internal-86f7c82.h`, `pgstat_shmem-86f7c82.c`, and `xlog-86f7c82.c`. They are research artifacts, not modified build sources.
