# Evidence brief implementation

`campaign/report.py` renders a standalone HTML page (inline CSS and SVG, no network scripts/fonts or matplotlib dependency) plus linked JSON/CSV evidence and a plain-text -hackers draft. It does not publish or send mail.

```sh
python3 campaign/report.py --results campaign/results --output docs/brief
# Optional independently verified build/test ledger:
python3 campaign/report.py --results campaign/results --output docs/brief --gates campaign/results/build-gates.json
```

## Inputs and truthful defaults

- Every immediate `results/*/results.json` containing `assertions` is included, including extra-operational release/assert suites. Counts derive from individual statuses, not a hardcoded summary or claimed pass total.
- Discrepancies are grouped into semantic themes. Repeated severity combinations and repeated builds do not become separate alleged defects. Full assertion names, actual/expected values and notes remain expandable.
- Every immediate `results/*/plan.json` with a `schedule` becomes a separate benchmark matrix. This supports primary and sensitivity matrices without combining incompatible sinks/workloads.
- Missing benchmark files render PENDING, never fabricated estimates. Partial results are explicitly provisional. COMPLETE requires nonempty schedule/trials/estimates, exact native arm identity coverage, unique measured trial identities, expected subtrial counts, finite positive TPS, no warmups in measured data, accounting/transaction success and native operational validity on every run/trial.
- Build/test counts are **not inferred from research prose**. Without `--gates`, upstream/core/contrib checks are labeled not asserted by this brief. Optional JSON schema is a list of objects: `name`, `status` (`pass`, `fail`, `error`, `not_run`), `evidence` (HTTPS URL or `evidence/...` relative link), `note`. Populate only from verified receipts/logs. A gate can carry exact counts and build SHA in its note. The supplied ledger is copied to `build-gates.json`.
- Exact benchmark plan pins/configure/hashes are displayed with executed arguments; intended research pins are explicitly not execution proof. Exported raw evidence is SHA-256 indexed in `evidence-manifest.json`.
- The HTML names generation time as generation time, not measurement time. Raw artifacts can predate regeneration.

## Validation performed

- Generated successfully with current correctness and extra-operational assertion files and no primary timing files: 277 pass assertions, 39 discrepancies in 5 themes, benchmarks PENDING. These numbers are an implementation checkpoint, not hardcoded output.
- Disposable synthetic generator-only fixtures exercised missing data, invalid native verdict, complete identity coverage, wrong trial identity and duplicate trial rejection. No synthetic measurements were saved in campaign evidence or presented as results.
- Desktop Chromium screenshot (1440 × 1080) visually inspected: legible header, status, overview and evidence table. A narrow headless capture was inconclusive because the window viewport differed from its screenshot width; do not claim a completed mobile-device visual test on that basis.
- Preview lives outside the repository at `/tmp/pg-stat-log-brief-preview` and is not published.

## Remaining handoff

Rerender after final matrices and operational suites arrive. Supply the verified build-gate ledger if upstream/contrib pass claims should appear. Review final plots against JSON, inspect mobile at a true emulated viewport, check source/evidence links on the published Pages URL, and only then treat delivery as verified. A complete matrix is not necessarily a complete research agenda; compare executed arguments with PLAN.md and disclose deferred experiments.

## Fresh-evidence follow-up (2026-09-09 06:06 UTC)

- Additional supplied restart controls, startup-boundary and large-resize suites now render 323 passed assertions / 44 discrepancy assertions in **6 themes**. Both `/core-counter-after` controls join the existing unrelated-core-counter theme. Three large-resize restart failures join one startup-crash theme linked to issue #10.
- Local gate assets are now copied into the output and SHA-256 manifest, including compressed core and external logs and the contrib check log. Missing/empty/path-escaping local assets downgrade a reported pass to `unknown`, preserve `reported_status`, and remove the broken link. This behavior was tested with a disposable missing-file fixture. External HTTPS references remain supplied ledger references, not network-verified by the generator.
- Correctness companions (compressed server/command logs, version, backtrace) are exported and linked in an evidence-files disclosure. All 52 local href/src targets in the fresh generated preview exist (the count can grow with new evidence).
- A true Chromium DevTools **390 × 844 mobile emulation** now verifies `innerWidth == document.documentElement.scrollWidth == 390` with the report fully loaded. Screenshot `report-mobile.png` was visually inspected: heading, navigation, status and summary wrap cleanly, with no horizontal page overflow. This supersedes the earlier inconclusive headless-window capture. It is a local preview check, not a verification of the final published URL.
- The preserved failed RemoveIPC pilot is correctly displayed as partial/not validated, not headline successful benchmarking. Completion still describes supplied schedules, not the whole research agenda.

## Actual pilot follow-up (2026-09-09 06:33 UTC)

The fresh 16-arm / 192-measured-trial pilot renders COMPLETE at the matrix level and **PILOT COMPLETE — CONFIRMATION PENDING** in the headline. Narrative explicitly calls its single randomized block exploratory, with no estimable between-block confidence interval. Three subtrials are not treated as three independent repeats. The SVG accessible description also avoids claiming intervals exist when all interval fields are absent.

Pilot collection-verification, compression-manifest and observation-summary JSON are exported with SHA-256 hashes. Optional `full-miss-path-validation.json` / `fullmiss-path-validation.json` files are exported if supplied; the current full-miss validation is recorded in collection-verification.json (16 enabled trials including warmups; no recorded path failures). Links point to the complete branch-hosted raw pilot directory without copying the large raw corpus into Pages.

CPU/disk observations are explicitly whole-window aggregates including warmup/tails and combined client/server activity, not synchronized trial attribution or bottleneck certification. No missing external/contrib suite is inferred as passing.

Ten black-box report tests pass, including a receipt export/raw-corpus exclusion test. Fresh preview is `/tmp/pg-stat-log-pilot-brief`: 61 local links resolved; 718,476-byte bundle before the small final plot caption. The actual 48-comparison pilot point plot was visually inspected at desktop width. No publication or commit performed by this reporting subtask.
