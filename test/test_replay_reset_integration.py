#!/usr/bin/env python3
"""Compile the production replay-reset handler and verify its transport wiring."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/replay_reset_integration"
SOURCE = ROOT / "examples/simple_repeater/MyMesh.cpp"
HEADER = ROOT / "examples/simple_repeater/MyMesh.h"
MAIN = ROOT / "examples/simple_repeater/main.cpp"


def extract_braced(source, signature):
    """Return an actual C++ definition/block, ignoring quoted/comment braces."""
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    token = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]')
    for match in token.finditer(source, opening):
        if match.group() == "{":
            depth += 1
        elif match.group() == "}":
            depth -= 1
            if depth == 0:
                return source[start:match.end()]
    raise AssertionError(f"unterminated C++ block: {signature}")


class ReplayResetIntegrationTest(unittest.TestCase):
    def test_executable_production_handler(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        source = SOURCE.read_text(encoding="utf-8")
        handler = extract_braced(source, "bool MyMesh::handleReplayResetCommand(")
        epoch_check = extract_braced(source, "static bool clockSyncEpochIsValid(")
        guard = extract_braced(source, "if (!replay_command && sender_timestamp > client->last_timestamp)")
        prepare = re.search(r"const bool replay_prepare\s*=[\s\S]*?;", source).group()
        cache_gate = re.search(r"const bool cached_retry\s*=\s*!replay_prepare[\s\S]*?;", source).group()
        generated = epoch_check + "\n" + handler + "\n" + """
static void apply_actual_receive_guard(ClientInfo* client, const char* command,
                                       uint32_t sender_timestamp) {
  mesh::ReplayResetRequest replay_request;
  const bool replay_command = mesh::parseReplayResetCommand(command, replay_request)
      != mesh::ReplayResetKind::NotReplay;
""" + guard + "\n}\n" + """
static bool apply_actual_receive_cache_gate(const char* command, bool cache_hit,
                                            int& lookup_calls) {
  mesh::ReplayResetRequest replay_request;
  mesh::parseReplayResetCommand(command, replay_request);
  ClientInfo sender;
  ClientInfo* client = &sender;
  uint32_t request_id = 1, command_fingerprint = 2;
  const char* cached_response = nullptr;
  CountingReplyCache remote_cli_reply_cache{cache_hit, lookup_calls};
""" + prepare + "\n" + cache_gate + "\nreturn cached_retry;\n}\n"
        with tempfile.TemporaryDirectory(prefix=".tmp-replay-integration-", dir=ROOT) as directory:
            work = Path(directory)
            (work / "production_handler.inc").write_text(generated, encoding="utf-8")
            binary = work / "replay-reset-integration.exe"
            compiled = subprocess.run([
                compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                f"-I{work}", f"-I{ROOT / 'src'}",
                str(FIXTURE / "test_replay_reset_integration.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("18 replay-reset integration checks passed", checked.stdout)
            self.assertEqual(checked.stdout.count("PASS:"), 18)

    def test_only_physical_console_grants_usb_origin(self):
        header = HEADER.read_text(encoding="utf-8")
        main = MAIN.read_text(encoding="utf-8")
        self.assertIn("bool usb_origin = false", header)
        usb = extract_braced(header, "void handleUsbCommand(")
        self.assertIn("handleCommand(0, NULL, command, reply, -1, 1, true)", usb)
        null_sender = extract_braced(header, "void handleCommand(uint32_t sender_timestamp, char* command, char* reply)")
        self.assertIn("handleCommand(sender_timestamp, NULL, command, reply)", null_sender)
        console_start = main.index("if (line_complete)")
        ethernet_start = main.index("if (ethernet_read_line(", console_start)
        self.assertIn("the_mesh.handleUsbCommand(command, reply)", main[console_start:ethernet_start])
        ethernet = extract_braced(main, "if (ethernet_read_line(")
        self.assertNotIn("handleUsbCommand", ethernet)
        self.assertIn("the_mesh.handleCommand(0, ethernet_command, reply)", ethernet)
        # No web/internal callback is allowed to adopt the physical entry point.
        cpp = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("handleUsbCommand(", cpp)

    def test_receiver_keeps_authentication_and_blocks_replay_floor_mutation(self):
        source = SOURCE.read_text(encoding="utf-8")
        cli_start = source.index("else if (type == PAYLOAD_TYPE_TXT_MSG && len > 5")
        cli_end = source.index("void MyMesh::", cli_start)
        cli = source[cli_start:cli_end]
        self.assertIn("client->isAdmin() || client->isRegionMgr() || client->isFilterMgr()", cli)
        parser = cli.index("mesh::parseReplayResetCommand(command, replay_request)")
        stale = cli.index("sender_timestamp < client->last_timestamp && !cached_retry")
        assignment = cli.index("client->last_timestamp = sender_timestamp;")
        self.assertLess(parser, stale)
        self.assertLess(stale, assignment)
        guarded = extract_braced(cli, "if (!replay_command && sender_timestamp > client->last_timestamp)")
        self.assertIn("client->last_timestamp = sender_timestamp;", guarded)
        self.assertEqual(cli.count("client->last_timestamp = sender_timestamp;"), 1)
        self.assertIn("const bool replay_prepare = replay_request.kind == mesh::ReplayResetKind::ExactKey;", cli)
        self.assertRegex(cli, r"const bool cached_retry\s*=\s*!replay_prepare\s*&&\s*remote_cli_reply_cache\.lookup\(")
        self.assertLess(cli.index("const bool cached_retry"), stale)
        handler = extract_braced(source, "bool MyMesh::handleReplayResetCommand(")
        self.assertNotIn("remote_cli_reply_cache.clear", handler)
        self.assertLess(handler.index("replay_reset_nonce.consume("),
                        handler.index("acl.clampLoginReplayTimestamps("))

    def test_recovery_dispatch_precedes_other_command_handlers(self):
        source = SOURCE.read_text(encoding="utf-8")
        handler = extract_braced(source, "void MyMesh::handleCommand(uint32_t sender_timestamp, ClientInfo* sender,")
        recovery = handler.index("handleReplayResetCommand(sender, command, reply, usb_origin)")
        self.assertLess(recovery, handler.index("_cli.handleCommand("))
        self.assertNotIn("sender_timestamp == 0", handler[:recovery])


if __name__ == "__main__":
    unittest.main()
