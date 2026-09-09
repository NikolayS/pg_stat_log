# Independent read-only audit of existing correctness campaign

Inspected 2026-09-09. Source: `/home/tars/clawd-samjr/projects/pg-stat-log-campaign/pg_stat_log/campaign`. No database was started, no campaign-owned file modified. This is a harness/evidence audit, not a new execution or certification.

## Evidence inventory verified

- `correctness-release/results.json`: 150 assertions, 131 pass, 19 fail, zero error.
- `correctness-assert/results.json`: same names/totals, with improved classification and saved parallel plan/rows.
- `extra-operational-assert/results.json`: 16 assertions, 15 pass, one failed persistence-contract expectation.
- Local results tree has no saved fresh-boundary run or `resize_check.py` run. Parent may recover these from VM, but scripts existing is not execution evidence.
- The assertion transcript's intentional severity EXCEPTION sample exits psql status 3 and contains the expected campaign error. I did not replay it.

## Findings requiring presentation/harness fixes

### 1. Report omits a material observed consequence (high presentation priority)

`report.py:19–21` reads only the two original correctness result files. Its hardcoded correctness narrative does not include `extra-operational-assert` or the observed **100 → 0 unrelated table insertion counter across extension-capacity change**. Publishing that generated HTML as the complete correctness outcome would omit a potentially important operational limitation, even though `CORRECTNESS_FINDINGS.md` documents it correctly.

Action: include every executed suite with per-suite provenance; prominently include the unrelated-core-counter loss and any recovered boundary/large-shrink results. Clearly distinguish desired contract, actual observation, and whether cause is core fixed-stat persistence framing versus extension memory corruption.

### 2. Static pass claims are not derived from inputs (medium/high presentation priority)

`report.py` emits fixed claims of core 240/240, external 18 TAP on both builds, and contrib 18 TAP independently of supplied input evidence. It also unconditionally says the submitted patch's counting/hash/reporting paths are identical and it was independently built/tested. Those statements may be true elsewhere, but this generator cannot validate them and can retain stale claims under a different result directory.

Action: require verified build/check/patch-comparison manifests or render missing evidence as unavailable. Link exact check logs and provenance. Do not infer arbitrary adversarial coverage of contrib from only its upstream regression/TAP.

### 3. Completion oracle is weaker than the campaign promise (medium)

`correctness.py:336–343` aborts remaining methods at the first method exception; skipped later methods get no explicit `not_run` record. `hook_chain` silently returns when `--hook-probe` is absent. Setup errors can leave an empty zero-error summary, although the process exits nonzero. Stop/cleanup errors can also prevent the final save. Present results are complete 150-row runs, so this does **not** invalidate those rows; it is a reproducibility/reporting trap for subsequent attempts.

Action: persist requested methods/options, exact script hashes, expected assertion inventory and top-level exit status; classify not-run methods after an abort. Never certify based only on zero `error` rows or pass/fail totals.

### 4. Some negative assertions can pass for the wrong failure (medium harness hardening)

Severity ERROR cells use `check=False` then inspect only the extension count. If a setup/GUC/syntax failure occurs before the intended marker, an expected-zero cell can falsely pass. `monitor-cannot-reset` checks only nonzero exit, unlike the earlier precise permission assertions. Some other expected connection failures likewise rely partly on nonzero status.

Action: require the expected SQLSTATE/marker and error class (e.g. psql verbose errors), especially for intentional ERROR and permission tests. Positive-count cells already catch many such failures; all-silent expectation cells need explicit stimulus validation. Current transcript sample shows the intended EXCEPTION did execute; this is not evidence the existing 14 threshold mismatches are false.

### 5. Parallel-label expectation has a scheduling dependency (low/medium)

The harness asserts workers launched and exactly 10,000 messages, then expects exactly one `parallel worker` key. Workers launching does not itself prove any worker processed a row; on a different schedule the leader could do the work. Saved assertion run has actual background-worker rows, so the current documentation mismatch is supported.

Action: explicitly assert positive generic worker count or use `parallel_leader_participation=off` for this particular attribution fixture. Preserve variable leader/worker counts as observations, not deterministic expected splits.

### 6. Narrow stress pass must retain its narrow wording

`reset_race` asserts completion, a broad 0..12000 surviving count, and a fresh exact 17-count afterward. It does not establish linearizable concurrent snapshot/reset semantics, nor conservation while reset intentionally discards counts. Existing findings document this limitation correctly; keep it in HTML rather than saying the entire race semantics are proved.

### 7. Runtime isolation is logical, not OS-user isolation

Fresh socket path plus no TCP avoids accidentally attaching to production. However initdb uses local trust and directories are created with ambient umask, not explicitly 0700. On a multi-user host another local OS user could connect to that socket as postgres. This dedicated disposable-host fixture should not be advertised as a security-hardening example. A private 0700 parent/socket directory or peer authentication gives a stronger isolation contract without changing test purpose.

## Classification audit: existing 19 failures are not 19 bugs

The detailed findings classify these sensibly:

- 14 cells represent one numeric LOG ordering mismatch against the proposed server-log threshold contract.
- Two suppressed-output observations are one chained-hook accounting-contract issue.
- Two nested-hook expectation failures describe one guarded recursion/load-order limitation. Do **not** remove the recursion guard merely to satisfy the oracle; that would revive an old crash class.
- One label mismatch is a documentation/granularity issue, not lost parallel counts.

The old release artifact still calls its parallel row a generic correctness failure; the assert artifact and report explicitly correct the interpretation. Preserve original rows and add a classification overlay, rather than rewriting historical evidence.

The unrelated core counter reset is separately supported by its own 100-before/0-after assertion. It is an operational consequence; this alone does not prove out-of-bounds memory writes. Large resize/startup and sanitizer investigations need their own evidence.

## Priority execution gaps for parent

1. Recover or run revised **fresh-initdb** boundary checks (63,64,1048576,1048577), retaining actual SHOW and diagnostic evidence. Invalid preloaded custom GUC fallback is not automatically startup failure.
2. Recover/run `resize_check.py` maximum-to-minimum transition, and strengthen it to verify new writes, old-data policy, subsequent restart, unrelated core stats. The current script asserts only restart success/final capacity after transition.
3. Add an unchanged-capacity restart control beside the 64→128 unrelated-core-stats loss, then repeat the changed-capacity result. This isolates extension-layout change from general persistence loss on this host.
4. If advertising contrib-specific adversarial validation, execute the harness against its independent binaries; otherwise retain the claim boundary (build/upstream tests plus source comparison only).
5. Add `pg_monitor` reset denial specifically: current matrix grants only `pg_read_all_stats`, so it does not directly revalidate the historically broader `pg_monitor` grant bug. The SQL can be inspected too, but a runtime acceptance matrix should label which role was actually tested.
6. Unsupported coverage to retain explicitly: cassert is not ASAN/UBSAN; arbitrary corrupted statistics files, ERROR thrown inside previous hooks, PANIC paths, all auxiliary backend types, all auth methods, Windows/EXEC_BACKEND, long soak and cross-platform alignment are not covered by these saved suites.

## Bottom line

The original assertion counts and stated 19-failure classification are internally consistent and preserve failures honestly. The most urgent problem is **incomplete final-report ingestion**, followed by missing recovered boundary/large-resize evidence. Do not reset the campaign or duplicate the completed 150-row matrices merely to add these targeted checks.
