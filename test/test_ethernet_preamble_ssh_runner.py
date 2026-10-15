"""Physical SSH-runner protocol/ownership tests: no SSH, WSL, RF or key reads."""
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/hil"))
import ethernet_preamble_ssh_runner as runner


def request(**changes):
    return dict(index=1, case_id="case-001-p128", preamble=128, frequency_mhz=909.5,
                bandwidth_khz=500, sf=5, cr=5, **changes)


def config(directory):
    return dict(ssh_host="bench@example.test", ssh_key=str(Path(directory) / "ssh_key"),
                pi_bench="/home/mikec/bench", server_endpoint="tcp://192.168.1.84:5003",
                client_endpoint="tcp://192.168.1.87:5003", wsl_workspace="/home/mcarper/app",
                flutter="/home/mcarper/flutter/bin/flutter", account="mcarper", out_root=directory)


def ready_text(config, pid=123):
    return (f"READY: gateway={config['server_endpoint']} local=10.90.5.1 remote=10.90.5.2 SSH=2222 user=mikec\n"
            "Ephemeral client private key file (never print contents): /home/mikec/bench/ethernet-phone.A1234567/client_key\n"
            f"256 SHA256:{'a' * 43} root@host (ED25519)\n"
            "Artifacts: /home/mikec/bench/ethernet-phone.A1234567\n"
            f"Server helper PID: {pid}\n")


class EthernetPreambleSshRunnerTests(unittest.TestCase):
    def test_config_uses_explicit_hosts_private_port5003_and_absolute_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            value = config(directory); Path(value["ssh_key"]).touch()
            self.assertEqual(runner.validate_config(value), value)
            for field, bad in (("ssh_host", "-oProxyCommand=evil"), ("account", "root"),
                               ("server_endpoint", "tcp://192.168.1.84:5002"),
                               ("server_endpoint", "tcp://127.0.0.1:5003"),
                               ("client_endpoint", value["server_endpoint"]),
                               ("pi_bench", "/"), ("wsl_workspace", "/home/../etc"),
                               ("flutter", "relative"), ("out_root", "relative")):
                changed = dict(value); changed[field] = bad
                with self.subTest(field=field, value=bad), self.assertRaises((ValueError, TypeError)):
                    runner.validate_config(changed)

    def test_requests_accept_sf5_through_sf9_without_changing_case_identifier(self):
        for bandwidth in (125, 500):
            for sf in range(5, 10):
                value = request(); value.update(sf=sf, bandwidth_khz=bandwidth)
                with self.subTest(bandwidth=bandwidth, sf=sf):
                    self.assertIs(runner.validate_request(value), value)
                    self.assertEqual(value["case_id"], "case-001-p128")

    def test_requests_only_allow_fixed_lab_frequency_bandwidth_cr_and_safe_case_id(self):
        for field, bad in (("frequency_mhz", 910.525), ("bandwidth_khz", 250),
                           ("cr", 7), ("preamble", 31), ("index", True),
                           ("case_id", "../escape")):
            changed = request(); changed[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.validate_request(changed)

    def test_request_radio_fields_reject_wrong_types_and_invalid_sf(self):
        invalid = {
            "frequency_mhz": (True, False, "909.5", None),
            "bandwidth_khz": (True, False, 125.0, 500.0, "125", "500", None),
            "sf": (True, False, 4, 10, 5.0, 9.0, "5", None),
            "cr": (True, False, 5.0, "5", None),
        }
        for field, values in invalid.items():
            for bad in values:
                changed = request(); changed[field] = bad
                with self.subTest(field=field, value=bad), self.assertRaises(ValueError):
                    runner.validate_request(changed)

    def test_windows_drive_mapping_never_expands_shell_metacharacters(self):
        self.assertEqual(runner.wsl_path(r"C:\git\out with spaces\file.key"), "/mnt/c/git/out with spaces/file.key")
        self.assertEqual(runner.wsl_path("/mnt/c/git/key"), "/mnt/c/git/key")
        for path in (r"\\host\share\key", "relative", "/", "/tmp/../etc/key"):
            with self.assertRaises(ValueError): runner.wsl_path(path)

    def test_ready_requires_exact_endpoint_one_fingerprint_pid_and_scoped_artifacts(self):
        value = config("/tmp/fixture")
        ready = runner.parse_server_ready(ready_text(value), value)
        self.assertEqual(ready["pid"], 123)
        self.assertEqual(ready["fingerprint"], "SHA256:" + "a" * 43)
        for text in (ready_text(value).replace("192.168.1.84", "192.168.1.87"),
                     ready_text(value).replace("Server helper PID: 123\n", ""),
                     ready_text(value) + "Server helper PID: 234\n",
                     ready_text(value) + "256 SHA256:" + "b" * 43 + " (ED25519)\n",
                     ready_text(value).replace("Artifacts: /home/mikec/bench/", "Artifacts: /etc/")):
            with self.assertRaises(ValueError): runner.parse_server_ready(text, value)

    def test_read_ready_waits_past_artifact_line_for_pid(self):
        value = config("/tmp/fixture")
        process = SimpleNamespace(stdout=io.StringIO(ready_text(value)))
        self.assertEqual(runner.read_ready(process, value, .5)["pid"], 123)
        with self.assertRaises(RuntimeError):
            runner.read_ready(SimpleNamespace(stdout=io.StringIO(ready_text(value).split("Server helper PID")[0])), value, .5)

    def test_flutter_artifact_path_must_be_exact_child_of_selected_workspace(self):
        self.assertEqual(runner.artifact_dir("Flutter RF artifacts: /home/test/app/ethernet-flutter.Ab123456\n",
            "/home/test/app", "Flutter RF artifacts"), "/home/test/app/ethernet-flutter.Ab123456")
        for text in ("Flutter RF artifacts: /tmp/ethernet-flutter.Ab123456\n",
                     "Flutter RF artifacts: /home/test/app/sub/ethernet-flutter.Ab123456\n",
                     "Flutter RF artifacts: /home/test/app/ethernet-flutter.short\n"):
            with self.assertRaises(ValueError): runner.artifact_dir(text, "/home/test/app", "Flutter RF artifacts")

    def test_speed_normalization_uses_actual_time_and_both_sha256_checks(self):
        speed = dict(success=True, host_verified=True, handshake_ms=7000, total_elapsed_ms=11000,
                     download=dict(bytes=8192, elapsed_ms=2000, kbps=9999, sha256_valid=True,
                                   data_elapsed_ms=1000, data_kbps=65.536),
                     upload=dict(bytes=8192, elapsed_ms=4000, sha256_valid=True))
        result = runner.normalized_speed(speed, 0)
        self.assertTrue(result["verified"])
        self.assertEqual(result["downlink"]["kbps"], 32.768)
        self.assertEqual(result["uplink"]["elapsed_seconds"], 4)
        self.assertEqual(result["downlink"]["data_kbps"], 65.536)
        for field in ("success", "host_verified"):
            changed = dict(speed); changed[field] = False
            self.assertFalse(runner.normalized_speed(changed, 0)["verified"])
        self.assertFalse(runner.normalized_speed(speed, 1)["verified"])
        for bad in (None, dict(bytes=8192, elapsed_ms=0, sha256_valid=True),
                    dict(bytes=8192, elapsed_ms=float("nan"), sha256_valid=True),
                    dict(bytes=8192, elapsed_ms=2000, sha256_valid=False)):
            changed = dict(speed); changed["download"] = bad
            self.assertFalse(runner.normalized_speed(changed, 0)["verified"])

    def test_remote_stop_checks_whole_pid_argv_script_and_unique_prefix_before_signal(self):
        value = config("/tmp/fixture")
        command = runner.stop_server_args(value, 123, "ethphone-speed-abcdef01")
        self.assertIn("BatchMode=yes", command); self.assertIn("StrictHostKeyChecking=yes", command)
        remote = shlex.split(command[-1])
        self.assertEqual(remote[:4], ["sudo", "-n", "python3", "-c"])
        self.assertEqual(remote[-3:], ["123", "/home/mikec/bench/ethernet_ssh_phone_bench.sh", "ethphone-speed-abcdef01"])
        code = remote[4]
        self.assertIn("script not in args", code)
        self.assertIn("args[args.index('--prefix')+1] != prefix", code)
        self.assertIn("os.kill(pid,signal.SIGTERM)", code)
        self.assertNotIn("pkill", code)
        for pid, prefix in ((1, "ethphone-speed-abcdef01"), (123, "ethphone-speed-abcdef01;killall")):
            with self.assertRaises(ValueError): runner.stop_server_args(value, pid, prefix)

    def test_failed_startup_still_removes_only_owned_key_and_owned_popen_child(self):
        with tempfile.TemporaryDirectory() as directory:
            value = config(directory); Path(value["ssh_key"]).touch()
            process = Mock(); process.stdout = io.StringIO("")
            process.wait.side_effect = [subprocess.TimeoutExpired("ssh", 3), None]
            with patch.object(runner.subprocess, "Popen", return_value=process), \
                    patch.object(runner.subprocess, "run") as run:
                result = runner.execute(value, request())
            self.assertFalse(result["verified"])
            process.kill.assert_called_once()
            run.assert_not_called()  # No WSL key was created; don't remove one.
            self.assertFalse(any(path.name == "client_key.ephemeral" for path in Path(directory).rglob("*")))

    def test_full_fake_case_normalizes_copies_evidence_and_cleans_only_its_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            value = config(directory); Path(value["ssh_key"]).touch()
            process = Mock(); process.stdout = io.StringIO(ready_text(value))
            tcp_count = 0
            commands = []

            def run(argv, **kwargs):
                nonlocal tcp_count
                commands.append(argv)
                self.assertFalse(kwargs["shell"])
                self.assertNotIn("PRIVATE_KEY_CONTENT", " ".join(argv))
                if argv[0] == "scp":
                    Path(argv[-1]).write_text("PRIVATE_KEY_CONTENT")
                if argv[0] == "ssh" and "/proc/net/snmp" in argv[-1]:
                    tcp_count += 1
                    return SimpleNamespace(stdout=("Tcp: InSegs OutSegs RetransSegs\n"
                        f"Tcp: {tcp_count * 10} {tcp_count * 20} {tcp_count - 1}\n"), returncode=0)
                if any(str(argument).endswith("/ethernet_ssh_flutter_bench.sh") for argument in argv):
                    return SimpleNamespace(stdout="Flutter RF artifacts: /home/mcarper/app/ethernet-flutter.Ab123456\n", returncode=0)
                if argv[0] == "wsl" and "-c" in argv and "src=$1" in argv[argv.index("-c") + 1]:
                    output = next(Path(directory).glob("case-001-p128-*"))
                    (output / "speed.json").write_text(json.dumps(dict(success=True, host_verified=True,
                        handshake_ms=7000, total_elapsed_ms=11000,
                        download=dict(bytes=8192, elapsed_ms=2000, sha256_valid=True),
                        upload=dict(bytes=8192, elapsed_ms=2000, sha256_valid=True))))
                    (output / "tcp-stats.json").write_text(json.dumps(dict(available=True, delta=dict(OutSegs=40, RetransSegs=2))))
                return SimpleNamespace(stdout="", returncode=0)

            with patch.object(runner.subprocess, "Popen", return_value=process), \
                    patch.object(runner.subprocess, "run", side_effect=run):
                result = runner.execute(value, request())
            self.assertTrue(result["verified"], result.get("error"))
            self.assertEqual(result["downlink"]["kbps"], 32.768)
            self.assertEqual(result["tcp_server"]["delta"]["RetransSegs"], 1)
            self.assertEqual(result["tcp_client"]["delta"]["RetransSegs"], 2)
            self.assertTrue(any(argv[:7] == ["wsl", "--user", "root", "--exec", "rm", "-f", "--"] for argv in commands))
            self.assertTrue(any(argv[0] == "ssh" and "os.kill(pid,signal.SIGTERM)" in argv[-1] for argv in commands))
            self.assertFalse((Path(result["artifacts"]["directory"]) / "client_key.ephemeral").exists())
            self.assertNotIn("PRIVATE_KEY_CONTENT", json.dumps(result))
            process.kill.assert_not_called()

    def test_source_has_no_network_mutation_or_key_content_copy_in_evidence_allowlist(self):
        source = Path(runner.__file__).read_text()
        self.assertNotIn("shell=True", source)
        self.assertNotIn("pkill", source)
        self.assertNotIn("killall", source)
        self.assertNotIn("client_key", runner.ARTIFACTS)
        self.assertNotIn("host_key", runner.ARTIFACTS)
        self.assertNotIn("iptables", source)
        self.assertIn('"--duration", "300"', source)
        self.assertIn('deadline = time.monotonic() + 225', source)


if __name__ == "__main__":
    unittest.main()
