"""RAW phone SSH/SFTP fixture contract; no root, services, sockets or radios."""
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/hil/meshcore_ip_ssh_phone_bench.sh"


class MeshCoreIpPhoneFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text()
        cls.validator = re.search(r"python3 - \"\$endpoint\" \"\$script_dir\" \"\$expect_serial\" <<'PY'\n(.*?)\nPY", cls.source, re.S)[1]

    def test_endpoint_validator_only_explicit_private_numeric_companion_tcp5000(self):
        for endpoint in ("tcp://192.168.1.87:5000", "tcp://10.1.2.3:5000", "tcp://172.20.1.2:5000/",
                         "serial:///dev/serial/by-id/usb-RAK_USB_TEST_SERIAL-if00"):
            result = subprocess.run([sys.executable, "-B", "-c", self.validator, endpoint, str(ROOT / "tools/hil")],
                capture_output=True, timeout=3, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        for endpoint in ("tcp://8.8.8.8:5000", "tcp://127.0.0.1:5000", "tcp://radio.local:5000",
                         "tcp://192.168.1.87:5003", "tcp://user:secret@192.168.1.87:5000",
                         "serial:///dev/ttyACM0", "tcp://192.168.1.87:5000/path",
                         "tcp://192.168.1.87:5000?x=1", "tcp://192.168.1.0:5000"):
            result = subprocess.run([sys.executable, "-B", "-c", self.validator, endpoint, str(ROOT / "tools/hil")],
                capture_output=True, timeout=3, check=False)
            self.assertNotEqual(result.returncode, 0, endpoint)

    def test_raw_point_ip_configuration_and_actual_handshake_readiness(self):
        self.assertIn('--interface mcip0 --local-address 10.90.5.2 --remote-address 10.90.5.1', self.source)
        self.assertIn("--path-hex '' --path-hash-width 1 --return-path-byte-count 0 --mtu 156", self.source)
        self.assertIn('--ready-after-handshake', self.source)
        self.assertIn("state.get('state') == 'ready'", self.source)
        self.assertIn("state.get('mtu') == 156", self.source)
        self.assertIn('Companion APP_START/SelfInfo handshake did not become ready.', self.source)
        self.assertLess(self.source.index('((ready))'), self.source.index("printf 'READY:"))
        self.assertNotIn('ethernet_tap_relay.py --endpoint', self.source)

    def test_serial_identity_and_explicit_line_options_forwarded_without_kiss(self):
        for required in ('--baud) baud=$2', '--serial-dtr) serial_dtr=$2', '--expect-serial) expect_serial=$2',
                         'serial_dtr=off', 'serial_options=(--baud "$baud" --serial-dtr "$serial_dtr")',
                         'serial_options+=(--expect-serial "$expect_serial")', '"${serial_options[@]}"'):
            self.assertIn(required, self.source)

    def test_account_private_keys_and_internal_sftp_not_system_ssh(self):
        for required in ('--account) account=$2', '$(id -u "$account") -ne 0',
                         'ssh_user=${ssh_user:-$account}', '"$ssh_user" == "$account"',
                         'chown "$account": "$run_dir"', 'chmod 700 -- "$run_dir"',
                         'runuser -u "$account" -- ssh-keygen', '-o "AllowUsers=$ssh_user"',
                         "-o 'Subsystem=sftp internal-sftp'", '-o ListenAddress=10.90.5.2',
                         '-o AuthenticationMethods=publickey', '-o PasswordAuthentication=no',
                         '-o DisableForwarding=yes', 'options=(-f /dev/null'):
            self.assertIn(required, self.source)
        self.assertIn('umask 077', self.source)
        self.assertNotIn('chown mikec', self.source)
        self.assertNotIn('AllowUsers=mikec', self.source)
        for forbidden in ('systemctl', 'service ssh', 'useradd', 'usermod', 'passwd ', '/etc/ssh/sshd_config',
                          'iptables', 'nft ', 'route add default', 'ip_forward'):
            self.assertNotIn(forbidden, self.source)

    def test_cleanup_is_owned_namespace_inode_and_explicit_children_only_with_key_removal(self):
        for required in ('^mcphone-', '"$duration" -ge 60', '"$duration" -le 1800',
                         'Namespace already exists; refusing to reuse it.',
                         'mktemp -d -- "$bench_dir/meshcore-phone.XXXXXXXX"',
                         'trap cleanup EXIT', "trap 'exit 130' INT", "trap 'exit 143' TERM HUP",
                         '"$observed" == "$inode"', '"$parent" == "$$"',
                         'for child in "$relay_pid" "$sshd_pid"', 'for _ in {1..20}',
                         'rm -f -- "$run_dir/host_key" "$run_dir/client_key"'):
            self.assertIn(required, self.source)
        self.assertEqual(self.source.count('ip netns delete "$namespace"'), 1)
        self.assertNotIn('pkill', self.source)
        self.assertNotIn('killall', self.source)
        self.assertNotIn('rm -rf', self.source)
        self.assertNotIn('cat "$run_dir/client_key"', self.source)
        self.assertNotIn('cat "$run_dir/host_key"', self.source)
        self.assertIn('Server helper PID: %s', self.source)


if __name__ == "__main__":
    unittest.main()
