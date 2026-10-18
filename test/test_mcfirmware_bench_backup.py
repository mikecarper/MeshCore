"""Offline backup ownership guard; never inspect or open a real serial port."""
from pathlib import Path
import re
import subprocess
import unittest

TOOL = Path(__file__).resolve().parents[1] / "tools/hil/mcfirmware_bench_backup.sh"


class BackupOwnershipTests(unittest.TestCase):
    def guard(self, status, stdout="", stderr=""):
        source = TOOL.read_text()
        guard = re.search(r"^backup_assert_unowned\(\) \{\n.*?^\}", source, re.M | re.S).group()
        # sudo/fuser are inert functions: an unprivileged fallback fails this
        # contract, including when its quiet return code resembles no owner.
        script = r'''
set -eu
backup_die() { printf '%s\n' "$*" >&2; exit 9; }
fuser() { printf 'unguarded_fuser_called\n' >&2; return 1; }
sudo() {
    [ "$#" = 3 ] && [ "$1" = -n ] && [ "$2" = fuser ] && [ "$3" = /dev/ttyACM999 ] || exit 8
    printf '%s' "$FAKE_STDOUT"
    printf '%s' "$FAKE_STDERR" >&2
    return "$FAKE_STATUS"
}
'''
        return subprocess.run(["bash", "-c", script + guard + "\nbackup_assert_unowned /dev/ttyACM999\n"],
                              env={"FAKE_STATUS": str(status), "FAKE_STDOUT": stdout,
                                   "FAKE_STDERR": stderr}, capture_output=True, text=True)

    def test_root_capable_quiet_no_owner_probe_is_accepted(self):
        result = self.guard(1)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_owner_permission_failure_or_ambiguous_probe_refuses(self):
        for status, stdout, stderr in ((0, "12345", ""), (1, "", "sudo: a password is required"),
                                       (1, "", "Usage: fuser"), (1, "12345", ""), (2, "", "")):
            with self.subTest(status=status, has_stdout=bool(stdout), has_stderr=bool(stderr)):
                result = self.guard(status, stdout, stderr)
                self.assertEqual(result.returncode, 9)


if __name__ == "__main__":
    unittest.main()
