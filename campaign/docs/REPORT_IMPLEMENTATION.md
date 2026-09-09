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
