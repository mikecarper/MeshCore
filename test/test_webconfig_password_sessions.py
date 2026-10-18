#!/usr/bin/env python3
"""Execute production credential-generation and AP-rekey decisions offline."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class WebConfigPasswordSessionsTest(unittest.TestCase):
    def compile_run(self, source, expect_rejection=False, includes=()):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory(prefix="webconfig-password-sessions-") as directory:
            work = Path(directory)
            generated = work / "fixture.cpp"
            generated.write_text(source)
            binary = work / "fixture"
            sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                          "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra",
                *sanitizer, "-I" + str(ROOT / "src"),
                *("-I" + str(path) for path in includes), str(generated), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if expect_rejection:
                self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn("Assertion", checked.stderr)
            else:
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def rekey_fixture(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        method = extract_braced(source, "void WebConfigServer::serviceSetupPassword(")
        return (ROOT / "test/fixtures/webconfig_password_sessions.cpp").read_text().replace("@METHODS@", method)

    def test_rekey_receipt_upload_and_failure_lifecycle(self):
        self.compile_run(self.rekey_fixture())

    def test_failed_rekey_must_remove_old_key_access_point(self):
        fixture = self.rekey_fixture()
        # An early platform failure leaves the old AP intact unless the owner
        # explicitly removes it. Prove the fixture detects that original bug.
        old = fixture.replace("    WiFi.softAPdisconnect(true);\n", "", 1)
        self.assertNotEqual(old, fixture)
        self.compile_run(old, expect_rejection=True)

    def batch_fixture(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "static bool fixedTimeEquals(",
            "void WebConfigServer::clearSessionLocked(",
            "void WebConfigServer::beginBatchAuthorizationLocked(",
            "void WebConfigServer::cancelStaleBatchLocked("))
        drain = extract_braced(source, "void WebConfigServer::drainBatch(")
        start = drain[drain.index("  if (_batch_next == 0)"):drain.index("  } else if")]
        methods += "\nvoid WebConfigServer::startBatch(uint32_t now) {\n" + start + "\n  }\n}\n"
        first_guard = drain.index("    WCLock prefs_lock", drain.index("  } else if"))
        guard = drain[first_guard:drain.index("    BatchEntry& e", first_guard)]
        methods += "\nvoid WebConfigServer::beforeCommand(uint32_t now) {\n" + guard + "\n++executed;\n}\n"
        post = drain[drain.index("    mesh::esp32::managementPassword::Snapshot committed;"):drain.index("    _batch_last_cmd =")]
        methods += "\nvoid WebConfigServer::afterCommand(bool password_command, const char* requested, const char* reply, uint32_t now) {\nchar requested_password[16] = {}; strcpy(requested_password, requested);\nBatchEntry e = {}; strcpy(e.reply, reply);\n" + post + "\npost_completed = true;\n}\n"
        return (ROOT / "test/fixtures/webconfig_password_batch.cpp").read_text().replace("@METHODS@", methods)

    def test_committed_password_revokes_session_and_stale_queue(self):
        self.compile_run(self.batch_fixture())

    def test_successful_commit_must_revoke_general_session(self):
        fixture = self.batch_fixture()
        old = fixture.replace("        clearSessionLocked();", "", 1)
        self.assertNotEqual(old, fixture)
        self.compile_run(old, expect_rejection=True)

    def receipt_fixture(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        config = extract_braced(source, "void WebConfigServer::handleConfigResult(")
        cli = extract_braced(source, "void WebConfigServer::handleCliResult(")
        config_loop = extract_braced(config, "  for (int i = 0;")
        cli_loop = extract_braced(cli, "  for (int i = from;")
        return (ROOT / "test/fixtures/webconfig_password_receipt.cpp").read_text().replace(
            "@CONFIG_LOOP@", config_loop).replace("@CLI_LOOP@", cli_loop)

    def receipt_json_include(self):
        header = next((ROOT / ".pio/libdeps").glob("*/ArduinoJson/src/ArduinoJson.h"), None)
        self.assertIsNotNone(header, "native dependency headers are required")
        return (header.parent,)

    def test_old_cookie_receipt_sanitizes_every_arbitrary_reply(self):
        self.compile_run(self.receipt_fixture(), includes=self.receipt_json_include())

    def test_unfiltered_cli_receipt_fails_executable_regression(self):
        fixture = self.receipt_fixture()
        old = fixture.replace('r["reply"] = !receipt_only ?', 'r["reply"] = true ?', 1)
        self.assertNotEqual(old, fixture)
        self.compile_run(old, expect_rejection=True, includes=self.receipt_json_include())

    def command_fixture(self):
        source = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        methods = "\n".join(extract_braced(source, signature) for signature in (
            "static bool fixedTimeEquals(", "static inline bool wcCliEchoesSecret(",
            "static inline bool wcIsDeferredReboot(", "static const char* wcCliUnavailable(",
            "static bool isAllowedSetKey(", "static bool isSecretKey(",
            "void WebConfigServer::clearSessionLocked(",
            "void WebConfigServer::beginBatchAuthorizationLocked(",
            "void WebConfigServer::cancelStaleBatchLocked(",
            "void WebConfigServer::handleCliPost("))
        drain = extract_braced(source, "void WebConfigServer::drainBatch(")
        # Run actual command dispatch, masking, and committed-generation hook.
        # Subsequent WiFi/reboot handoff is covered by its dedicated fixtures.
        methods += "\n" + drain[:drain.index("  if (_standalone_wifi_dirty)")] + "\n}\n"
        config = extract_braced(source, "void WebConfigServer::handleConfigPost(")
        config_loop = extract_braced(config[config.index("  int count = 0;"):], "  for (JsonPair kv : set)")
        cli = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        start = cli.index('    } else if (memcmp(command, "password ", 9) == 0) {')
        end = cli.index('    } else if (memcmp(command, "clear stats",', start)
        setter = cli[start:end].replace('} else if (', 'if (', 1) + '\n    }\n'
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        start = companion.index('bool MyMesh::handleLocalControlCommand(')
        end = companion.index('  if (mesh::powerdiag::handleCommand(', start)
        companion = companion[start:end] + '  return false;\n}\n'
        return (ROOT / "test/fixtures/webconfig_password_command.cpp").read_text().replace(
            "@METHODS@", methods).replace("@CONFIG_LOOP@", config_loop).replace(
            "@SETTER@", setter).replace("@COMPANION@", companion)

    def test_actual_parser_drain_and_role_setters_preserve_password_bytes(self):
        self.compile_run(self.command_fixture(), includes=self.receipt_json_include())

    def test_old_cli_suffix_trimming_fails_executable_regression(self):
        fixture = self.command_fixture()
        old = fixture.replace('    if (wcCliEchoesSecret(e.cmd)) {',
            "    while (pos > 0 && (e.cmd[pos - 1] == ' ' || e.cmd[pos - 1] == '\\t')) --pos;\n"
            "    e.cmd[pos] = 0;\n    if (wcCliEchoesSecret(e.cmd)) {", 1)
        self.assertNotEqual(old, fixture)
        self.compile_run(old, expect_rejection=True, includes=self.receipt_json_include())


if __name__ == "__main__":
    unittest.main()
