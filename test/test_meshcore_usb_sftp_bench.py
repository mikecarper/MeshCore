"""Non-hardware contract checks for the matched USB SFTP runner."""
from pathlib import Path
import shutil
import subprocess
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "tools/hil/meshcore_usb_sftp_bench.sh"


class UsbSftpBenchContract(unittest.TestCase):
    def test_bash_syntax(self):
        if not shutil.which("bash"):
            self.skipTest("bash is unavailable")
        # Parse stdin so Windows' WSL bash never interprets a C:\ path as a
        # Linux filename (and no host-specific path conversion is required).
        result = subprocess.run(["bash", "-n"], input=SCRIPT.read_text().encode("utf-8"),
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_distinct_fixed_transports_and_real_mtu_check(self):
        source = SCRIPT.read_text()
        self.assertIn("mtu=156; interface=mcip0", source)
        self.assertIn("mtu=230; interface=lora0", source)
        self.assertIn("meshcore_ip_relay.py", source)
        self.assertIn("ethernet_tap_relay.py", source)
        self.assertIn("--ready-after-handshake", source)
        self.assertIn("--kiss-port 1 --kiss-modem", source)
        self.assertIn('[[ "$actual_mtu" == "$mtu" ]]', source)
        self.assertIn('ip -n "$prefix" -j link show dev "$interface"', source)

    def test_cleanup_scope_and_no_host_route_or_firewall(self):
        source = SCRIPT.read_text()
        self.assertIn('[[ -n "$inode" && "$observed" == "$inode" ]]', source)
        self.assertIn('[[ "$parent" == "$$" ]]', source)
        self.assertIn('ip netns delete "$prefix"', source)
        self.assertNotIn("chown -R", source)
        self.assertNotIn("iptables", source)
        self.assertNotIn("nft ", source)
        self.assertNotIn("route add default", source)
        self.assertNotIn("rm -rf", source)


if __name__ == "__main__":
    unittest.main()
