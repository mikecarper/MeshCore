#!/usr/bin/env python3
"""Run actual room catch-up planning against production client and post layouts."""

from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/room_catchup.cpp"


def date_reference():
    rows = ['{"0", 0}', '{"4294967295", UINT32_MAX}',
            '{"2106-02-07T06:28:15Z", UINT32_MAX}']
    for year in range(1970, 2107):
        for month, day in ((1, 1), (2, 28), (3, 1), (12, 31)):
            date = datetime(year, month, day, tzinfo=timezone.utc)
            timestamp = int(date.timestamp())
            if timestamp <= 0xFFFFFFFF:
                rows.append('{"' + date.strftime("%Y-%m-%d") + '", ' + str(timestamp) + '}')
                later = date.replace(hour=12, minute=34, second=56)
                if int(later.timestamp()) <= 0xFFFFFFFF:
                    rows.append('{"' + later.strftime("%Y-%m-%dT%H:%M:%SZ") + '", '
                                + str(int(later.timestamp())) + '}')
        if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
            date = datetime(year, 2, 29, tzinfo=timezone.utc)
            rows.append('{"' + date.strftime("%Y-%m-%d") + '", ' + str(int(date.timestamp())) + '}')
    return ",\n    ".join(rows)


def production_source():
    room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
    room_header = (ROOT / "examples/simple_room_server/MyMesh.h").read_text()
    acl = (ROOT / "src/helpers/ClientACL.h").read_text()
    constants = "\n".join(line for line in acl.splitlines()
                          if line.startswith(("#define PERM_ACL_", "#define OUT_PATH_")))
    text_length = re.search(r"^#define\s+MAX_POST_TEXT_LEN\s+.*$", room_header, re.MULTILINE)
    if not text_length:
        raise AssertionError("production post text capacity not found")
    constants += "\n" + text_length[0]
    replacements = {
        "@CONSTANTS@": constants,
        "@CLIENT_INFO@": extract_braced(acl, "struct ClientInfo {"),
        "@POST_INFO@": extract_braced(room_header, "struct PostInfo {"),
        "@PROCESS_ACK@": extract_braced(room, "bool MyMesh::processAck("),
        "@VALID_DATES@": date_reference(),
    }
    source = FIXTURE.read_text()
    for marker, replacement in replacements.items():
        source = source.replace(marker, replacement)
    return source


class RoomCatchUpTests(unittest.TestCase):
    @classmethod
    def build(cls, name, helper=None):
        work = Path(cls.directory.name)
        include = work / name
        include.mkdir()
        if helper is not None:
            (include / "helpers").mkdir()
            (include / "helpers/RoomCatchUp.h").write_text(helper, encoding="ascii")
        source = include / "test.cpp"
        source.write_text(cls.generated, encoding="ascii")
        binary = include / "catchup"
        command = [cls.compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                   "-I" + str(include), "-I" + str(ROOT / "src"), str(source), "-o", str(binary)]
        if sys.platform.startswith("linux"):
            command[1:1] = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                            "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        checked = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if checked.returncode:
            raise AssertionError(checked.stdout + checked.stderr)
        return binary

    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise AssertionError("a host C++17 compiler is required")
        cls.directory = tempfile.TemporaryDirectory(prefix="room-catchup-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.generated = production_source()
        cls.binary = cls.build("production")

    def run_case(self, case, expected):
        checked = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=20)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn(expected, checked.stdout)

    def test_before_skipnext_keepnewest_ring_order_and_author_exclusion(self):
        self.run_case("counts", "all modes order cyclic retention, exclude own posts, and bound future selections passed")

    def test_epoch_limit_empty_retention_and_duplicate_timestamps(self):
        self.run_case("bounds", "uint32 limits, empty retention, bounded input, and equal-timestamp accounting passed")

    def test_actual_client_and_ack_handler_preserve_topic_and_quotas(self):
        self.run_case("pending", "actual client application clears skipped posts, preserves topics and quotas, and rejects stale ACKs passed")

    def test_strict_dates_match_python_utc_reference_and_reject_invalid_input(self):
        self.run_case("dates", "strict UTC Gregorian and uint32 parsing match independent epoch references passed")

    def test_randomized_plans_match_independent_ordered_reference(self):
        self.run_case("random", "15000 plans match independent ordered reference without mutating retention passed")

    def test_negative_controls_detect_self_posts_date_boundary_and_uncleared_ack(self):
        helper = (ROOT / "src/helpers/RoomCatchUp.h").read_text()
        controls = (
            ("self-posts", "&& !post.author.matches(client.id)", "", "counts"),
            ("date-boundary", "post.post_timestamp < value", "post.post_timestamp <= value", "counts"),
            ("pending-ack", "client.extra.room.pending_ack = 0;", "(void)client.extra.room.pending_ack;", "pending"),
        )
        for name, before, after, case in controls:
            with self.subTest(control=name):
                self.assertIn(before, helper, "update the negative control to the production seam")
                binary = self.build(name, helper.replace(before, after))
                checked = subprocess.run([str(binary), case], capture_output=True, text=True, timeout=20)
                self.assertNotEqual(checked.returncode, 0, "negative control missed " + name)
                self.assertIn("Assertion", checked.stderr, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
