"""Isolated KISS SSH/SFTP fixture contract; no services, namespaces or radios."""
from pathlib import Path
import re
import shlex
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/hil/ethernet_ssh_phone_bench.sh"


def sshd_arguments(source):
    block = re.search(r"^options=\((.*?)\)\s*$", source, re.M | re.S)
    if block is None:
        raise AssertionError("Missing explicit isolated sshd option array")
    return shlex.split(block[1])


class EthernetPhoneSftpFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text()

    def test_dev_null_configuration_explicitly_enables_internal_sftp(self):
        arguments = sshd_arguments(self.source)
        self.assertEqual(arguments[:2], ["-f", "/dev/null"])
        self.assertIn("Subsystem=sftp internal-sftp", arguments)
        position = arguments.index("Subsystem=sftp internal-sftp")
        self.assertEqual(arguments[position - 1], "-o")
        self.assertEqual(arguments.count("Subsystem=sftp internal-sftp"), 1)
        # With -f /dev/null the previous array inherited no system subsystem.
        old = self.source.replace("    -o 'Subsystem=sftp internal-sftp'", "")
        self.assertNotIn("Subsystem=sftp internal-sftp", sshd_arguments(old))

    def test_server_remains_private_key_only_and_namespace_scoped(self):
        arguments = sshd_arguments(self.source)
        for option in ("ListenAddress=10.90.5.2", "AllowUsers=mikec",
                       "PermitRootLogin=no", "UsePAM=no",
                       "PasswordAuthentication=no", "KbdInteractiveAuthentication=no",
                       "AuthenticationMethods=publickey", "DisableForwarding=yes",
                       "PermitUserEnvironment=no", "X11Forwarding=no"):
            self.assertIn(option, arguments)
        self.assertIn('chmod 700 -- "$run_dir"', self.source)
        self.assertIn('ip netns exec "$namespace" /usr/sbin/sshd -D -e "${options[@]}"', self.source)
        self.assertIn('/usr/sbin/sshd -t "${options[@]}"', self.source)
        for forbidden in ("systemctl", "service ssh", "/etc/ssh/sshd_config",
                          "useradd", "usermod", "iptables", "nft ", "ip_forward"):
            self.assertNotIn(forbidden, self.source)

    def test_sftp_change_preserves_kiss_mtu_and_owned_cleanup(self):
        for required in ('mtu 230 up', '--interface lora0',
                         '"$observed" == "$inode"', '"$parent" == "$$"',
                         'rm -f -- "$run_dir/host_key" "$run_dir/client_key"',
                         'Namespace already exists; refusing to reuse it.'):
            self.assertIn(required, self.source)
        self.assertNotIn("killall", self.source)
        self.assertNotIn("pkill", self.source)
        self.assertNotIn("rm -rf", self.source)


if __name__ == "__main__":
    unittest.main()
