#!/usr/bin/env python3
"""Black-box report validity checks; all synthetic data stays in temporary dirs."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPORT = Path(__file__).with_name('report.py')


class ReportValidity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pg-stat-log-report-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.results = self.root / 'results'
        self.results.mkdir()
        self.output = self.root / 'site'
        self.matrix = self.results / 'synthetic-only'

    def write(self, name, data):
        self.matrix.mkdir(exist_ok=True)
        (self.matrix / name).write_text(json.dumps(data))

    def valid_fixture(self):
        # Two treatment arms, two measured subtrials per arm. These values are
        # solely generator-test inputs, never benchmark observations.
        arms = [dict(block=1, treatment=t, workload='fixture', clients=[1])
                for t in ('baseline', 'on')]
        self.write('plan.json', dict(arguments=dict(trials=2), schedule=arms))
        self.write('status.json', [dict(arm, forge_exit_code=0,
                   native_operationally_valid=True) for arm in arms])
        trials = [dict(block=1, treatment=t, workload='fixture', clients=1,
                       trial=n, tps=100.0, failed=0, count_mismatch=0,
                       warmup_discarded=False, native_operationally_valid=True)
                  for t in ('baseline', 'on') for n in (1, 2)]
        self.write('trials.json', trials)
        estimates = [dict(workload='fixture', clients=1, treatment=t,
                          geomean_tps_ratio=1.0, throughput_loss_pct=0.0,
                          independent_blocks=1,
                          ratio_ci95_t_log_blocks=None,
                          ratio_ci95_bootstrap_paired_blocks=None)
                     for t in ('baseline', 'on')]
        self.write('summary.json', dict(results=estimates))
        return trials, estimates

    def generate(self, gates=None):
        command = [sys.executable, str(REPORT), '--results', str(self.results),
                   '--output', str(self.output)]
        if gates is not None:
            ledger = self.root / 'gates.json'
            ledger.write_text(json.dumps(gates))
            command += ['--gates', str(ledger)]
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.html = (self.output / 'index.html').read_text()
        return json.loads((self.output / 'evidence-manifest.json').read_text())

    def assert_invalid_matrix(self, manifest):
        self.assertEqual(manifest['matrices'][0]['state'],
                         'PARTIAL / NOT VALIDATED')
        self.assertEqual(manifest['benchmark_state'], 'BENCHMARKS INCOMPLETE')
        self.assertIn('PARTIAL / NOT VALIDATED', self.html)
        self.assertNotIn('SUPPLIED MATRICES COMPLETE', self.html)

    def test_public_report_excludes_email_draft(self):
        self.output.mkdir()
        (self.output / 'hackers-draft.txt').write_text('stale private draft')
        self.generate()
        self.assertFalse((self.output / 'hackers-draft.txt').exists())
        self.assertNotIn('hackers-draft', self.html)
        self.assertNotIn('Draft text', self.html)

    def test_missing_timing_is_pending(self):
        manifest = self.generate()
        self.assertEqual(manifest['benchmark_state'], 'BENCHMARKS PENDING')
        self.assertEqual(manifest['matrices'], [])
        self.assertIn('No timed matrix supplied', self.html)
        self.assertNotIn('SUPPLIED MATRICES COMPLETE', self.html)

    def test_missing_gate_log_downgrades_reported_pass(self):
        missing = 'evidence/build-evidence/missing.log'
        manifest = self.generate([dict(name='Synthetic gate', status='pass',
                                       evidence=missing, note='Fixture only')])
        gate = manifest['gates'][0]
        self.assertEqual(gate['status'], 'unknown')
        self.assertEqual(gate['reported_status'], 'pass')
        self.assertFalse(gate['evidence_available'])
        self.assertNotIn('evidence', gate)
        self.assertNotIn(f'href="{missing}"', self.html)
        self.assertIn('UNKNOWN', self.html)

    def test_complete_valid_fixture_is_complete(self):
        self.valid_fixture()
        manifest = self.generate()
        self.assertEqual(manifest['benchmark_state'], 'SUPPLIED MATRICES COMPLETE')
        self.assertEqual(manifest['matrices'][0]['state'], 'COMPLETE')
        self.assertIn('SUPPLIED MATRICES COMPLETE', self.html)
        self.assertTrue((self.output / 'synthetic-only-comparisons.csv').is_file())

    def test_completed_explicit_pilot_keeps_confirmation_pending(self):
        for phase_container in ('arguments', 'preregistration', 'prereg'):
            with self.subTest(phase_container=phase_container):
                self.valid_fixture()
                path = self.matrix / 'plan.json'
                plan = json.loads(path.read_text())
                plan.setdefault(phase_container, {})['phase'] = 'pilot'
                self.write('plan.json', plan)
                manifest = self.generate()
                self.assertEqual(manifest['matrices'][0]['state'], 'COMPLETE')
                self.assertEqual(manifest['benchmark_state'],
                                 'PILOT COMPLETE — CONFIRMATION PENDING')
                self.assertIn('PILOT COMPLETE — CONFIRMATION PENDING', self.html)
                self.assertNotIn('SUPPLIED MATRICES COMPLETE', self.html)
                self.assertFalse((self.output / 'hackers-draft.txt').exists())

    def test_pilot_receipts_export_without_raw_corpus(self):
        self.valid_fixture()
        receipts = {
            'collection-verification.json': {'hashed_files': 4, 'hash_failures': [],
                'fullmiss_enabled_trials_including_warmup': 2, 'fullmiss_path_failures': []},
            'compression-manifest.json': {'codec': 'gzip', 'files': []},
            'observation-summary.json': {'runs': []},
            'full-miss-path-validation.json': {'synthetic_fixture_only': True},
        }
        for name, value in receipts.items():
            self.write(name, value)
        (self.matrix / 'server-arm1.log').write_text('Synthetic raw log, not a Pages asset.')
        manifest = self.generate()
        paths = {a['path'] for a in manifest['artifacts']}
        for name in receipts:
            relative = 'evidence/synthetic-only/' + name
            self.assertIn(relative, paths)
            self.assertTrue((self.output / relative).is_file())
        self.assertFalse((self.output / 'evidence/synthetic-only/server-arm1.log').exists())
        self.assertIn('One independent randomized block', self.html)
        self.assertIn('no between-block confidence interval is estimable', self.html)
        self.assertIn('not synchronized per-trial attribution', self.html)
        self.assertIn('/tree/testing/master-forge-20260909/campaign/results/synthetic-only', self.html)

    def test_missing_measured_trial_is_invalid(self):
        trials, _ = self.valid_fixture()
        self.write('trials.json', trials[:-1])
        self.assert_invalid_matrix(self.generate())

    def test_wrong_trial_identity_with_same_count_is_invalid(self):
        trials, _ = self.valid_fixture()
        trials[-1]['trial'] = 3  # Planned trial 2 missing; row count unchanged.
        self.write('trials.json', trials)
        self.assert_invalid_matrix(self.generate())

    def test_duplicate_trial_replacing_missing_trial_is_invalid(self):
        trials, _ = self.valid_fixture()
        trials[-1] = copy.deepcopy(trials[-2])  # Still four rows, not four trials.
        self.write('trials.json', trials)
        self.assert_invalid_matrix(self.generate())

    def test_missing_summary_cell_is_invalid(self):
        _, estimates = self.valid_fixture()
        self.write('summary.json', dict(results=estimates[:1]))
        self.assert_invalid_matrix(self.generate())

    def test_successful_exit_without_native_validity_is_invalid(self):
        self.valid_fixture()
        path = self.matrix / 'status.json'
        statuses = json.loads(path.read_text())
        statuses[-1]['native_operationally_valid'] = False
        self.write('status.json', statuses)
        self.assert_invalid_matrix(self.generate())


if __name__ == '__main__':
    unittest.main()
