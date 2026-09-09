#!/usr/bin/env python3
"""Render a self-contained, evidence-driven brief; absent evidence never means PASS."""
import argparse
import collections
import csv
import datetime
import hashlib
import html
import json
import math
import re
from pathlib import Path

ROOT = "https://github.com/NikolayS/pg_stat_log"
ESC = lambda value: html.escape(str(value))


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def family(row):
    name = row.get("name", "unnamed")
    if name.startswith("severity/"):
        return "LOG severity ordering"
    if name.startswith("hook/"):
        return "Nested hook forwarding/counting" if "/nested/" in name else "Hook-suppressed event accounting"
    if name == "parallel/worker-attribution":
        return "Parallel worker labeling"
    if name == "resize/unrelated-core-stats-preserved" or name.endswith("/core-counter-after"):
        return "Capacity change and unrelated core counters"
    if name == "restart-success" and row.get("category") == "restart-contract":
        return "Large capacity change: startup crash"
    return name


def table(headers, rows, ident=""):
    return '<div class="scroll"><table' + (f' id="{ident}"' if ident else '') + '><thead><tr>' + ''.join(f'<th scope="col">{ESC(x)}</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{x}</td>' for x in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def chart(rows):
    """Native inline SVG avoids remote assets and scientific plotting dependencies."""
    points = [r for r in rows if isinstance(r.get("throughput_loss_pct"), (int, float))]
    if not points:
        return ''
    bounds = [0.0]
    for r in points:
        bounds.append(r['throughput_loss_pct'])
        ci = r.get('ratio_ci95_t_log_blocks')
        if ci:
            bounds.extend([100 * (1-ci[1]), 100 * (1-ci[0])])
    lo, hi = min(bounds), max(bounds)
    padding = max(2, (hi-lo)*0.07)
    lo, hi = lo-padding, hi+padding
    scale = lambda n: 410 + (n-lo)/(hi-lo)*540
    height = 75 + 29*len(points)
    out = [f'<svg viewBox="0 0 1000 {height}" role="img" aria-label="Throughput loss with paired-block 95 percent Student-t intervals. Numeric values follow in the table." xmlns="http://www.w3.org/2000/svg"><rect width="100%" height="100%" fill="white"/>']
    for i in range(6):
        n = lo+(hi-lo)*i/5
        x = scale(n)
        out.append(f'<path d="M{x:.2f} 20V{height-35}" stroke="#e0e5eb"/><text x="{x:.2f}" y="{height-14}" text-anchor="middle" font-size="13" fill="#334155">{n:.1f}%</text>')
    out.append(f'<path d="M{scale(0):.2f} 20V{height-35}" stroke="#475569"/>')
    for i, r in enumerate(points):
        y = 30+29*i
        out.append(f'<text x="12" y="{y+5}" font-size="14" fill="#162233">{ESC(r["workload"])} · c={ESC(r["clients"])} · {ESC(r["treatment"])}</text>')
        ci = r.get('ratio_ci95_t_log_blocks')
        if ci:
            x1, x2 = scale(100*(1-ci[1])), scale(100*(1-ci[0]))
            out.append(f'<path d="M{x1:.2f} {y}H{x2:.2f} M{x1:.2f} {y-4}V{y+4} M{x2:.2f} {y-4}V{y+4}" stroke="#1959ae" stroke-width="2"/>')
        out.append(f'<circle cx="{scale(r["throughput_loss_pct"]):.2f}" cy="{y}" r="4" fill="#1959ae"/>')
    return ''.join(out)+'</svg>'


def benchmark_state(directory):
    plan = read(directory/'plan.json', {})
    statuses = read(directory/'status.json', [])
    trials = read(directory/'trials.json', [])
    summary = read(directory/'summary.json', {})
    expected = plan.get('schedule', [])
    def key(r):
        return (r.get('block'), r.get('treatment'), r.get('workload'), tuple(r.get('clients', [])))
    scheduled = collections.Counter(key(s) for s in expected)
    completed = collections.Counter(key(s) for s in statuses)
    invalid_status = [s for s in statuses if s.get('forge_exit_code') != 0 or s.get('native_operationally_valid') is not True]
    invalid_trials = [t for t in trials if t.get('count_mismatch') != 0 or t.get('failed') != 0 or t.get('native_operationally_valid') is not True]
    args = plan.get('arguments', {})
    expected_trials = sum(len(s.get('clients', [])) for s in expected)*args.get('trials', 0)
    scheduled_trials = collections.Counter((s.get('block'), s.get('treatment'), s.get('workload'), c, t)
        for s in expected for c in s.get('clients', []) for t in range(1, args.get('trials', 0)+1))
    observed_trials = collections.Counter((t.get('block'), t.get('treatment'), t.get('workload'), t.get('clients'), t.get('trial')) for t in trials)
    measurements_valid = all(isinstance(t.get('tps'), (int, float)) and math.isfinite(t['tps']) and t['tps'] > 0 and t.get('warmup_discarded') is False for t in trials)
    expected_estimates = {(s.get('workload'), c, s.get('treatment')) for s in expected for c in s.get('clients', [])}
    estimates = summary.get('results', [])
    observed_estimates = [(r.get('workload'), r.get('clients'), r.get('treatment')) for r in estimates]
    estimates_valid = (set(observed_estimates) == expected_estimates and len(observed_estimates) == len(expected_estimates)
        and all(isinstance(r.get('geomean_tps_ratio'), (int, float)) and math.isfinite(r['geomean_tps_ratio']) and r['geomean_tps_ratio'] > 0 for r in estimates))
    complete = (bool(expected and trials and estimates) and scheduled == completed
        and all(n == 1 for n in scheduled.values()) and not invalid_status and not invalid_trials
        and scheduled_trials == observed_trials and all(n == 1 for n in observed_trials.values())
        and expected_trials == len(trials) and measurements_valid and estimates_valid)
    state = 'COMPLETE' if complete else ('PARTIAL / NOT VALIDATED' if statuses or trials else 'PENDING')
    return dict(name=directory.name, state=state, plan=plan, statuses=statuses, trials=trials, summary=summary, expected_runs=len(expected), expected_trials=expected_trials, invalid_runs=len(invalid_status), invalid_trials=len(invalid_trials))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gates', type=Path, help='Optional JSON list: name, status (pass/fail/error/not_run), evidence URL, note. Only recorded status is displayed.')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    def export(path):
        relative = path.relative_to(args.results)
        target = args.output/'evidence'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        target.write_bytes(data)
        artifacts.append(dict(path=str(Path('evidence')/relative), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)))
        return str(Path('evidence')/relative)
    suites, discrepancies = [], collections.defaultdict(list)
    for path in sorted(args.results.glob('*/results.json')):
        data = read(path, {})
        if not isinstance(data.get('assertions'), list):
            continue
        counts = collections.Counter(row.get('status', 'unknown') for row in data['assertions'])
        link = export(path)
        for companion in sorted(path.parent.iterdir()):
            if companion.name in ['backtrace.txt', 'version.txt', 'server.log.gz', 'commands.jsonl.gz'] and companion.is_file():
                export(companion)
        suites.append(dict(name=path.parent.name, counts=dict(counts), link=link, assertions=data['assertions']))
        for row in data['assertions']:
            if row.get('status') == 'fail':
                discrepancies[family(row)].append(dict(suite=path.parent.name, **row))
    benchmark_dirs = sorted({p.parent for p in args.results.glob('*/plan.json') if 'schedule' in read(p, {})})
    benches = [benchmark_state(d) for d in benchmark_dirs]
    for d in benchmark_dirs:
        for name in ['plan.json', 'status.json', 'trials.json', 'summary.json', 'hardware.json']:
            if (d/name).exists():
                export(d/name)
    gates = read(args.gates, []) if args.gates else []
    for gate in gates:
        gate['note'] = re.sub(r'\band(?=\d)', 'and ', gate.get('note', ''))
        evidence = gate.get('evidence', '')
        if evidence.startswith('evidence/'):
            source = args.results / evidence[len('evidence/'):]
            if source.resolve().is_relative_to(args.results.resolve()) and source.is_file() and source.stat().st_size:
                export(source)
                gate['evidence_available'] = True
            else:
                gate['reported_status'] = gate.get('status')
                gate['status'] = 'unknown'
                gate['evidence_available'] = False
                gate['note'] += ' Evidence file missing or empty; the reported outcome is not validated by this brief.'
                gate.pop('evidence', None)
        elif not evidence.startswith('https://'):
            gate['reported_status'] = gate.get('status')
            gate['status'] = 'unknown'
            gate['note'] += ' No supported evidence link supplied.'
            gate.pop('evidence', None)
        if gate.get('status') not in ['pass', 'fail', 'error', 'not_run']:
            gate['status'] = 'unknown'
    (args.output/'build-gates.json').write_text(json.dumps(gates, indent=2)+'\n')
    total = collections.Counter()
    for suite in suites:
        total.update(suite['counts'])
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    status = 'BENCHMARKS PENDING' if not benches else ('SUPPLIED MATRICES COMPLETE' if all(b['state']=='COMPLETE' for b in benches) else 'BENCHMARKS INCOMPLETE')
    overview = f"{total['pass']} passed assertions; {total['fail']} discrepancy assertions grouped into {len(discrepancies)} themes; {total['error']} harness errors."
    corr_rows = [[ESC(s['name']), str(s['counts'].get('pass', 0)), str(s['counts'].get('fail', 0)), str(s['counts'].get('error', 0)), f'<a href="{s["link"]}">Raw assertions</a>'] for s in suites]
    corr = table(['Suite / build', 'Pass', 'Discrepancy', 'Harness error', 'Evidence'], corr_rows) if suites else '<p>No correctness assertion files supplied. Correctness is pending.</p>'
    findings = ''
    for name, occurrences in discrepancies.items():
        unique = len({r['name'] for r in occurrences})
        findings += f'<details><summary>{ESC(name)} <small>— {len(occurrences)} failed assertions; {unique} unique checks</small></summary><p>Grouping is for readability, not a claim of {len(occurrences)} separate defects or a proven common root cause.</p>'
        if name == 'Large capacity change: startup crash':
            findings += '<p><a href="https://github.com/NikolayS/pg_stat_log/issues/10">Crash reproducer and investigation: issue #10</a>. Repeated release/assertion reproductions are one observed failure theme, not three independent bugs.</p>'
        findings += table(['Suite', 'Assertion', 'Actual', 'Expected', 'Interpretation'], [[ESC(r['suite']), ESC(r['name']), ESC(json.dumps(r.get('actual'))), ESC(json.dumps(r.get('expected'))), ESC(r.get('note', ''))] for r in occurrences])+'</details>'
    gate_html = table(['Recorded build/test gate', 'Status', 'Evidence / qualification'], [[ESC(g.get('name', 'unnamed')), ESC(g['status'].upper()), (f'<a href="{ESC(g["evidence"])}">Evidence</a> ' if g.get('evidence', '').startswith(('https://', 'evidence/')) else '')+ESC(g.get('note', ''))] for g in gates]) if gates else '<p class="notice">Core regression, upstream PGXS/TAP, and submitted contrib build/check results are not asserted here: no structured gate ledger was supplied. See tracking issue and build logs. Missing evidence is not a pass.</p>'
    bench_html = ''
    for b in benches:
        name = b['name']
        comparisons = [r for r in b['summary'].get('results', []) if r.get('treatment') != 'baseline']
        fields = ['workload', 'clients', 'treatment', 'independent_blocks', 'throughput_loss_pct', 'geomean_tps_ratio', 'ratio_ci95_t_log_blocks', 'ratio_ci95_bootstrap_paired_blocks']
        with (args.output/f'{name}-comparisons.csv').open('w') as out:
            writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore'); writer.writeheader(); writer.writerows(comparisons)
        bench_html += f'<article class="card"><h3>{ESC(name)} <span class="tag">{b["state"]}</span></h3><p>{len(b["statuses"])} / {b["expected_runs"]} scheduled native runs; {len(b["trials"])} / {b["expected_trials"]} measured subtrials; {b["invalid_runs"]} unvalidated runs and {b["invalid_trials"]} invalid subtrials.</p>'
        if b['state'] != 'COMPLETE':
            bench_html += '<p class="notice">This matrix is not complete and validated. Estimates below, if any, are provisional selected-cell evidence, not the full campaign result.</p>'
        bench_html += '<details><summary>Executed arguments and source/build pins</summary><pre>'+ESC(json.dumps({k:v for k,v in b['plan'].items() if k != 'schedule'}, indent=2))+'</pre></details>'
        if comparisons:
            bench_html += chart(comparisons)
            rows=[]
            for r in comparisons:
                ci = r.get('ratio_ci95_t_log_blocks')
                interval = f'{100*(1-ci[1]):+.2f} to {100*(1-ci[0]):+.2f}%' if ci else 'not estimable'
                rows.append([ESC(r['workload']), ESC(r['clients']), ESC(r['treatment']), f'{r["throughput_loss_pct"]:+.2f}%', interval, ESC(r['independent_blocks'])])
            bench_html += table(['Workload', 'Clients', 'Treatment', 'TPS loss', '95% t interval', 'Paired blocks'], rows)
            bench_html += f'<p><a href="{ESC(name)}-comparisons.csv">Comparisons CSV</a> · <a href="evidence/{ESC(name)}/summary.json">Estimates JSON</a> · <a href="evidence/{ESC(name)}/trials.json">All measured trials</a></p>'
        bench_html += '</article>'
    if not benches:
        bench_html = '<div class="card notice"><h3>No timed matrix supplied</h3><p>No throughput, latency, overhead percentage or uncertainty estimate is claimed. The planned experiment is described below. Upstream performance claims remain hypotheses until this campaign records valid paired measurements.</p></div>'
    pins = []
    for b in benches:
        p=b['plan']
        pins.append(f'<li>{ESC(b["name"])}: Postgres <code>{ESC(p.get("postgres_sha", "unknown"))}</code>; extension <code>{ESC(p.get("extension_sha", "unknown"))}</code>; Forge <code>{ESC(p.get("forge_sha", "unknown"))}</code>.</li>')
    evidence_links = '<details><summary>Download evidence files and build logs</summary><ul>' + ''.join(f'<li><a href="{ESC(item["path"])}">{ESC(item["path"])}</a> ({item["bytes"]:,} bytes)</li>' for item in artifacts) + '</ul></details>'
    provenance = '<ul>'+''.join(pins)+'</ul>' if pins else '<p>Benchmark-executed source and binary pins are pending. Intended revisions and external-versus-contrib source comparison are documented in <a href="'+ROOT+'/blob/testing/master-forge-20260909/campaign/RESEARCH.md">research notes</a>; intended pins are not proof that a build ran.</p>'
    draft = f"Subject: pg_stat_log: independent master testing and benchmark evidence\n\nI tested pg_stat_log for the contrib discussion. This report is not an argument that performance alone determines contrib placement.\n\n{overview} Repeated checks across builds are not separate defects. See the HTML brief and raw assertion JSON for expected/actual values, build labels, and contract qualifications.\n\nBenchmark status: {status}. " + ("See per-matrix valid-run ledgers, exact binary/source pins, every retained trial, and paired-block uncertainty in the brief. " if benches else "No completed timing evidence is supplied in this report yet. ") + "\n\nQuestions for reviewers: should LOG follow server-log ordering? What is the contract for nested/suppressed emit-hook events and parallel worker labels? Should a capacity change preserve unrelated core cumulative counters? These questions must be read alongside their precise reproduced assertions, not as unqualified crash or data-loss claims.\n\nLimitations: one VM, co-located closed-loop clients, finite correctness cases, and limited independent timing blocks. No blanket production or memory-safety certification is implied.\n"
    if 'Large capacity change: startup crash' in discrepancies:
        draft += '\nThe supplied large-resize suites reproduce a startup failure; see issue #10 and the exported backtrace. Multiple build reproductions are grouped as one failure theme.\n'
    (args.output/'hackers-draft.txt').write_text(draft)
    manifest = dict(generated_utc=now, correctness_summary=dict(total), discrepancy_themes=len(discrepancies), benchmark_state=status, matrices=[{k:b[k] for k in ['name','state','expected_runs','expected_trials','invalid_runs','invalid_trials']} for b in benches], gates=gates, artifacts=artifacts)
    (args.output/'evidence-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    content = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Evidence-driven correctness and benchmark brief for pg_stat_log on Postgres master"><title>pg_stat_log · independent master study</title><style>
:root{{color-scheme:light;--ink:#17243b;--muted:#4e6077;--blue:#1459a1;--line:#d9e2ec}}*{{box-sizing:border-box}}body{{margin:0;color:var(--ink);background:#f3f6fa;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1150px;margin:auto;padding:36px 24px 70px}}header{{border-top:7px solid var(--blue);padding:24px 0 28px}}h1{{font-size:clamp(34px,6vw,58px);line-height:1.1;margin:15px 0}}h2{{font-size:27px;margin-top:44px}}h3{{font-size:20px}}a{{color:var(--blue);text-underline-offset:3px}}a:focus-visible,summary:focus-visible{{outline:3px solid #c35500;outline-offset:4px}}.eyebrow{{text-transform:uppercase;letter-spacing:.13em;font-size:12px;font-weight:750}}.lede{{font-size:21px;max-width:850px;color:var(--muted)}}.card{{background:white;border:1px solid var(--line);border-radius:12px;padding:22px;margin:20px 0}}.notice{{border-left:5px solid #a75013;background:#fffaf4;padding:14px 18px}}.tag{{display:inline-block;border:1px solid var(--line);border-radius:20px;padding:3px 10px;font-size:12px;letter-spacing:.03em}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#e9eff6}}td{{overflow-wrap:anywhere}}small,.muted{{color:var(--muted)}}code{{background:#e6edf5;padding:2px 4px;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}svg{{width:100%;height:auto;min-width:650px}}details{{margin:18px 0}}summary{{cursor:pointer;font-weight:650}}li{{margin:8px 0}}nav{{display:flex;gap:18px;flex-wrap:wrap}}.skip{{position:absolute;left:-9999px}}.skip:focus{{left:12px;top:12px;background:white;padding:10px}}@media(max-width:700px){{main{{padding:20px 14px}}.card{{padding:16px}}article{{overflow-x:auto}}}}@media print{{body{{background:white}}main{{max-width:none}}details>*{{display:block}}nav,.skip{{display:none}}}}
</style></head><body><a class="skip" href="#findings">Skip to results</a><main><header><p class="eyebrow">PostgresAI · independent evidence brief</p><h1>pg_stat_log on Postgres master</h1><p class="lede">Test the accounting contract. Measure the cost. Preserve the evidence.</p><p>A contribution to the -hackers discussion, not a blanket production certification.</p><p class="tag">{status}</p><p class="muted">Generated {now} from supplied artifacts; generation time is not test execution time.</p><nav aria-label="Brief sections"><a href="#findings">Correctness</a><a href="#performance">Performance</a><a href="#methods">Methods</a><a href="#provenance">Provenance</a><a href="#discussion">-hackers draft</a></nav></header>
<section class="card"><h2 style="margin-top:0">Decision summary</h2><p>{overview}</p><p>Assertions are individual checks, not independent defect counts. Contract discrepancies require interpretation alongside expected and actual values. A passing finite suite does not prove absence of defects.</p><p><a href="{ROOT}/issues/7">Tracking issue #7</a> · <a href="{ROOT}/pull/8">Harness PR #8</a> · <a href="https://hacking.postgres.tv/sessions/Et6WSCdR3Yw/">Session and commitment</a></p></section>
<section id="findings"><h2>Correctness and operational coverage</h2>{corr}<h3>Observed discrepancy themes</h3>{findings or '<p>No recorded discrepancy assertions. This does not mean unexecuted cases passed.</p>'}<h3>Upstream and submitted build gates</h3>{gate_html}<p><a href="build-gates.json">Recorded build-gate ledger</a></p><p>Coverage includes only the named supplied suites. Expand the JSON for every positive and negative assertion. Additional release/assert builds, sanitizers, platforms or scenarios are not implied by a pass in one suite.</p></section>
<section id="performance"><h2>Performance evidence</h2><p>Matrix completion describes the supplied schedules, not every experiment proposed in the research plan. Deferred experiments must be disclosed separately.</p>{bench_html}</section>
<section id="methods"><h2>Method and interpretation</h2><ul><li><strong>Controlled treatment:</strong> compare no preload, preload disabled, and enabled states with identical SQL, logging floor, destination and client schedule. Enabled-but-filtered and contention/saturation arms are distinct experiments. See executed arguments; planned arms must not be mistaken for completed ones.</li><li><strong>Pair within randomized blocks:</strong> take each treatment/baseline ratio from the same block, using medians of timed subtrials after excluded warmups. Aggregate block ratios geometrically. The experimental unit is the paired block, not a transaction or each subtrial.</li><li><strong>Uncertainty:</strong> tables and plots use Student-t intervals on paired block log ratios. Paired-block bootstrap intervals are retained in summary JSON. Small block counts limit precision; intervals are per comparison, not simultaneous family-wise bounds. An interval spanning zero loss neither proves a slowdown nor equivalence.</li><li><strong>Validity before speed:</strong> native Forge status, unchanged restart state, successful steps and exact warning-accounting claims must validate each run. The report requires scheduled-run identities and measured-trial counts to match. Forge validity is not a performance verdict or proof of bottleneck absence.</li><li><strong>Real log storage:</strong> primary file-sink timing must retain actual stderr logging. A devnull sensitivity experiment excludes storage and must be labeled separately, never presented as production logging performance.</li><li><strong>Build and host:</strong> timing uses optimized, non-assert binaries; assertion builds serve correctness. Exact configure flags, binary hashes, hardware and tool pins are in the plan and hardware artifacts. One virtualized host is not a cross-platform result.</li><li><strong>Limits:</strong> co-located closed-loop pgbench clients may limit CPU; file-log contention and virtualization may dominate. This is not open-loop p99/SLO measurement, production replay, exhaustive fault injection, or proof of memory safety.</li></ul></section>
<section id="provenance"><h2>Provenance and reproducibility</h2>{provenance}{evidence_links}<p><a href="evidence-manifest.json">Artifact SHA-256 manifest and status ledger</a> · <a href="{ROOT}/tree/testing/master-forge-20260909/campaign">Harness and campaign methods</a> · <a href="{ROOT}/blob/testing/master-forge-20260909/campaign/RESEARCH.md">Source comparison and research</a></p><p>The external extension and submitted v1 contrib patch have a source comparison in the research notes. Common hot paths do not substitute for a separate native in-tree build/check gate. Earlier issue findings concern older revisions and are not automatically carried forward.</p></section>
<section id="discussion"><h2>Ready for technical review, not automatic endorsement</h2><p>Performance evidence can inform contrib placement but cannot decide whether the capability belongs in core. The source session summary/timecodes and mailing-list thread were studied; a full transcript was not available, so no word-for-word video review is claimed.</p><p><a href="hackers-draft.txt">Download evidence-bound -hackers draft</a> · <a href="https://www.postgresql.org/message-id/flat/CABo-N96sjT0KwtCFVBkuMQEAfe-834Sd2zjbv9qKJORXkf=BiA@mail.gmail.com">Active discussion</a></p><details><summary>Draft text — not sent</summary><pre>{ESC(draft)}</pre></details></section><footer class="card"><strong>Reproduce. Challenge. Extend.</strong><p>Negative findings are retained, pending work stays visible, and each reported number traces to a supplied artifact.</p></footer></main></body></html>'''
    (args.output/'index.html').write_text(content)
    (args.output/'.nojekyll').touch()
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
