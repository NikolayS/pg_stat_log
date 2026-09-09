# pg_stat_log PostgreSQL master validation campaign

## Scope

Validate current external upstream and the submitted contrib proposal for a factual, reproducible -hackers report. Prior findings: NikolayS/pg_stat_log #1, #5, #6. Do not assume old findings remain reproducible. Do not equate a test pass with proof of absence of bugs.

Pinned starting points (2026-09-09):
- PostgreSQL master: 86f7c82cf1023e3599f40f939727791a7090cd44.
- Extension upstream main: 0a8b782 (full SHA recorded in manifests).
- Forge active harness branch: feat/forge-bootstrap (pin recorded in manifests).
- Session: https://hacking.postgres.tv/sessions/Et6WSCdR3Yw/

## Gates and sequence

1. Read session and active -hackers thread; compare submitted contrib code with external upstream.
2. Build stock master with debug symbols and assertions; run upstream regression/TAP and adversarial exact-assertion tests. Separate optimized non-assert benchmark build to avoid conflating instrumentation with production overhead.
3. Correctness: severities and logging floor, disabled mode, privilege matrix, attribution, swallowed errors, precise concurrent counts, full capacity/drop accounting, reset/reclaim, restart/resize/crash behavior, hook chaining and recursion, snapshot semantics. Record unsupported or untested cases explicitly.
4. Benchmark on one isolated 8 dedicated-vCPU host. Pin compiler, flags, SHAs, hardware, kernel and GUCs. Compare no preload, preload disabled, enabled filtered and enabled recording using identical workloads. Include quiet traffic, one hot key, varying cardinality and full-table misses, reader/reset contention, and SQL workload baselines. Keep logging identical across compared treatments.
5. Warm up; randomize/block treatment order; repeat independently. Record all repeats, not just best runs. Report paired ratios/differences and uncertainty, latency and throughput, failure/drop counts, host CPU/steal and disk pressure. Explain when client/log I/O limits the result. Explore concurrency 1/4/8/16 on 8 vCPUs.
6. Reproduce findings independently and separate benchmark artifacts from extension behavior. Add focused regressions for confirmed defects; avoid silently benchmarking a fix as upstream.
7. Publish raw measurements, scripts, provenance, exact commands and an HTML brief on fork GitHub Pages; open a reviewable PR and update tracker. Prepare -hackers-ready text; do not email the list in this task.
8. Copy artifacts back and delete only this campaign's VM and temporary cloud resources. Record elapsed billable estimate and deletion receipt.

## Resource guardrails

One temporary dedicated 8-vCPU Hetzner VM, no backups or volumes. Current fsn1 price ~EUR 0.2219/hour plus IPv4. Aim for a single overnight campaign and terminate when artifacts are safe. Never publish cloud tokens, SSH private keys, raw credential-bearing environment, or unrelated host information.

## Evidence rules

Claims must name the tested SHA/build and workload. Mark limitations (single VM, confidence interval precision, untested versions/platforms, fault-injection coverage). Data generation is not a pass: assert results and retain failures. All planned cells must resolve to completed, failed, or explicitly justified not-run.

## Resumed campaign schedule (Max, September 9)

Reuse the existing eight-dedicated-vCPU instance and pinned upstream; no second host. Existing correctness results are recovered, not presented as fresh executions. Supplemental restart controls, boundary tests and crash backtrace precede any timing. No correctness/build workload runs alongside timing.

1. Broad exploratory pilot: one block, three five-second trials, three-second warmup; clients 1/4/8/16; baseline/disabled/filtered/on; quiet/hot/cardinality/full_miss. About 19 minutes timed lower bound, excluding Forge overhead. No confidence claims from one block.
2. Preselected confirmation: five independent blocks, three 30-second trials, ten-second warmup; clients 1/8; baseline/disabled/on; quiet/hot. About 100 minutes measured lower bound.
3. Saturation confirmation: same five-block protocol, clients 8/16, baseline/on, full_miss (about 33 minutes lower bound).
4. Optional-path diagnostic: one exploratory block, clients 1/8, all four treatments, reader/readwrite, three five-second trials. Report diagnostic scope, not confirmed performance.

Each arm must finish native Forge checks and exact message accounting. Failed artifacts retained, never merged with repaired runs. Pilot does not determine which preselected primary claims get published. Extend independent blocks for ambiguous primary intervals only with an explicitly recorded amended schedule, not until a favorable result appears. At roughly EUR 0.222/hour plus IPv4, budget several hours rather than a second/larger host. Always publish distributions and cloud/client/log-I/O limitations.
