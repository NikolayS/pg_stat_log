#!/usr/bin/env python3
"""Focused checks for auxiliary-reader validation and fixture reset."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

source = Path(os.environ.get('CAMPAIGN_TEST_DRIVER', Path(__file__).with_name('benchmark.py')))
spec = importlib.util.spec_from_file_location('campaign_benchmark', source)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class BenchmarkChecks(unittest.TestCase):
    def test_full_miss_cannot_pass_via_counted_path(self):
        snapshot = {'info': {'num_entries': 1024}}
        with self.assertRaisesRegex(RuntimeError, 'full-miss path'):
            bench.assert_warning_path({'counted_warnings': 10,
                'dropped_warnings': 0, 'expected_warnings': 10},
                snapshot, snapshot, 'on', 'full_miss')

    def test_full_miss_requires_saturated_snapshots(self):
        with self.assertRaisesRegex(RuntimeError, 'full-miss path'):
            bench.assert_warning_path({'counted_warnings': 0,
                'dropped_warnings': 10, 'expected_warnings': 10},
                {'info': {'num_entries': 0}}, {'info': {'num_entries': 1024}},
                'on', 'full_miss')

    def test_nonfull_cannot_pass_via_dropped_path(self):
        with self.assertRaisesRegex(RuntimeError, 'non-full warning path'):
            bench.assert_warning_path({'counted_warnings': 0,
                'dropped_warnings': 10, 'expected_warnings': 10},
                {}, {}, 'on', 'hot')

    def test_valid_full_miss_path(self):
        snapshot = {'info': {'num_entries': 1024}}
        bench.assert_warning_path({'counted_warnings': 0,
            'dropped_warnings': 10, 'expected_warnings': 10},
            snapshot, snapshot, 'on', 'full_miss')

    def test_reader_failure_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'reader failed'):
                bench.validate_reader(Mock(wait=Mock(return_value=1)), Path(directory), 1)

    def test_reader_empty_or_failed_workload_rejected(self):
        for transactions, failed in [(0, 0), (10, 1)]:
            with self.subTest(transactions=transactions, failed=failed):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory)
                    (path / 'reader.log').write_text(f'number of transactions actually processed: {transactions}\nnumber of failed transactions: {failed}\ntps = 20.1\nlatency average = 1.0 ms\n')
                    with self.assertRaisesRegex(RuntimeError, 'no successful workload'):
                        bench.validate_reader(Mock(wait=Mock(return_value=0)), path, 1)

    def test_reader_success_retains_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'reader.log').write_text('number of transactions actually processed: 10\nnumber of failed transactions: 0\ntps = 20.1\nlatency average = 1.0 ms\n')
            bench.validate_reader(Mock(wait=Mock(return_value=0)), path, 1)
            self.assertTrue((path / 'reader-result.json').exists())

    def test_reset_asserts_actual_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bench, 'sql', side_effect=['', '99999']):
                with self.assertRaisesRegex(RuntimeError, 'reset failed'):
                    bench.reset_readwrite({}, Path(directory))

    def test_reader_cleanup_escalates_on_timeout(self):
        proc = Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired('reader', 10), 0]
        handle = Mock()
        bench.stop_reader(proc, handle)
        proc.kill.assert_called_once()
        handle.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
