"""No network namespaces or radio access: production TCP evidence parser tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "hil"))
import ethernet_tcp_stats as stats


class TcpStatsTests(unittest.TestCase):
    def test_linux_snmp_counter_pair_and_handshake_inclusive_delta(self):
        before = stats.parse_tcp("Ip: ignored\nTcp: RtoAlgorithm CurrEstab InSegs OutSegs RetransSegs OutRsts\nTcp: 1 0 20 30 2 0\n")
        after = stats.parse_tcp("Tcp: RtoAlgorithm CurrEstab InSegs OutSegs RetransSegs OutRsts\nTcp: 1 1 40 80 7 1\n")
        report = stats.summarize(before, after)
        self.assertEqual(report["delta"], dict(InSegs=20, OutSegs=50, RetransSegs=5, OutRsts=1))
        self.assertEqual(report["out_segment_retransmission_ratio"], .1)
        self.assertNotIn("CurrEstab", report["delta"])
        self.assertIn("total only", report["retransmission_scope"])

    def test_ambiguous_missing_negative_or_reset_counters_are_not_evidence(self):
        for text in ("", "Tcp: InSegs OutSegs\nTcp: 1 2\n", "Tcp: InSegs OutSegs RetransSegs\nTcp: 1 2 -1\n",
                     "Tcp: InSegs OutSegs RetransSegs\nTcp: 1 2 0\nTcp: 2 3 0\n"):
            with self.assertRaises(ValueError):
                stats.parse_tcp(text)
        with self.assertRaises(ValueError):
            stats.summarize(dict(InSegs=2, OutSegs=2, RetransSegs=0), dict(InSegs=1, OutSegs=2, RetransSegs=0))

    def test_zero_out_segments_has_no_invented_ratio(self):
        value = dict(InSegs=0, OutSegs=0, RetransSegs=0)
        self.assertIsNone(stats.summarize(value, value)["out_segment_retransmission_ratio"])

    def test_missing_snapshot_produces_explicit_unavailable_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(stats.main(["--directory", directory]), 0)
            report = json.loads((Path(directory) / "tcp-stats.json").read_text())
            self.assertFalse(report["available"])

    def test_shell_default_mode_bounds_namespace_ownership_and_evidence_paths(self):
        source = (Path(__file__).resolve().parents[1] / "tools/hil/ethernet_ssh_flutter_bench.sh").read_text()
        self.assertIn("test_name=rf; test_timeout=210", source)
        self.assertIn('--test-name) test_name=$2', source)
        self.assertIn('10#$2 <= 180', source)
        self.assertIn('--phase-timeout)', source)
        self.assertIn('LORA_SSH_SPEED_TIMEOUT_SECONDS="$phase_timeout"', source)
        self.assertIn('LORA_SSH_SPEED_REPORT="$run_dir/speed.json"', source)
        self.assertIn('capture_tcp after || true', source)
        self.assertIn('wait "$test_pid" || test_result=$?', source)
        self.assertIn('exit "$test_result"', source)
        self.assertIn('local remaining=300', source)
        self.assertIn('"$observed" == "$inode"', source)
        self.assertNotIn('cat /proc/net/snmp > /', source)


if __name__ == "__main__":
    unittest.main()
