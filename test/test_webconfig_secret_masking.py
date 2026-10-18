#!/usr/bin/env python3

import gzip
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "src" / "helpers" / "esp32" / "WebConfigServer.cpp"
KEYS = ROOT / "src" / "helpers" / "WebConfigKeys.h"
UI = ROOT / "webui" / "index.html"
EMBEDDED_UI = ROOT / "src" / "helpers" / "esp32" / "WebConfigHtml.h"


def function_body(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end, source.index(start))]


def embedded_ui() -> str:
    header = EMBEDDED_UI.read_text(encoding="utf-8")
    length = int(re.search(r"WEBCONFIG_HTML_GZ_LEN = (\d+);", header).group(1))
    array = header.split(
        "const uint8_t WEBCONFIG_HTML_GZ[] PROGMEM = {", 1
    )[1]
    blob = bytes(
        int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", array)
    )[:length]
    return gzip.decompress(blob).decode("utf-8")


class WebConfigSecretMaskingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = SERVER.read_text(encoding="utf-8")
        cls.keys = KEYS.read_text(encoding="utf-8")
        cls.ui = UI.read_text(encoding="utf-8")
        match = re.search(
            r'static const char SECRET_SENTINEL\[\]\s*=\s*"([^"]*)"\s*;',
            cls.server,
        )
        if match is None:
            raise AssertionError("WebConfigServer has no secret sentinel")
        cls.sentinel = match.group(1)

    def test_server_and_captive_portal_use_exactly_eight_stars(self):
        self.assertEqual(self.sentinel, "********")
        self.assertEqual(len(self.sentinel), 8)

        browser = re.search(r'var SENTINEL\s*=\s*"([^"]*)"\s*;', self.ui)
        self.assertIsNotNone(browser)
        self.assertEqual(browser.group(1), self.sentinel)

        captive_portal = re.search(
            r'var SENTINEL\s*=\s*"([^"]*)"\s*;', embedded_ui()
        )
        self.assertIsNotNone(captive_portal)
        self.assertEqual(captive_portal.group(1), self.sentinel)

    def test_config_get_masks_only_nonempty_wifi_password(self):
        get_handler = function_body(
            self.server,
            "void WebConfigServer::handleConfigGet(",
            "void WebConfigServer::handleConfigPost(",
        )
        self.assertRegex(
            get_handler,
            r'wifi\["pwd"\]\s*=\s*_wifi_password\[0\]\s*'
            r'\?\s*SECRET_SENTINEL\s*:\s*""\s*;',
        )
        self.assertEqual(get_handler.count('wifi["pwd"]'), 1)
        self.assertNotRegex(
            get_handler,
            r'wifi\["pwd"\]\s*=\s*(?:\(const char\s*\*\)\s*)?'
            r'_wifi_password\s*;',
        )

    def test_config_post_skips_only_exact_sentinel_for_secret_keys(self):
        post_handler = function_body(
            self.server,
            "void WebConfigServer::handleConfigPost(",
            "void WebConfigServer::handleConfigResult(",
        )
        skip = re.search(
            r"if\s*\(\s*isSecretKey\(key\)\s*&&\s*"
            r"strcmp\(val,\s*SECRET_SENTINEL\)\s*==\s*0\s*\)\s*continue\s*;",
            post_handler,
        )
        self.assertIsNotNone(skip)

        # The equality guard must run before a BatchEntry/CLI command is built;
        # otherwise an unchanged masked field could persist the placeholder.
        batch_entry = post_handler.index("BatchEntry& e")
        pre_persistence = post_handler[
            post_handler.index("int count = 0;") : batch_entry
        ]
        self.assertEqual(pre_persistence.count("continue;"), 1)
        self.assertLess(skip.start(), batch_entry)
        self.assertLess(skip.start(), post_handler.index("count++;"))

        # strcmp equality means close values remain real password updates, not
        # wildcard matches. Keep representative boundary cases explicit.
        def skipped(key: str, value: str) -> bool:
            return key == "wifi.pwd" and value == self.sentinel

        self.assertTrue(skipped("wifi.pwd", "********"))
        for value in ("", "*******", "*********", "********x", "x********"):
            self.assertFalse(skipped("wifi.pwd", value), value)
        self.assertFalse(skipped("wifi.ssid", "********"))

    def test_wifi_password_is_classified_as_a_secret_key(self):
        secret_classifier = function_body(
            self.keys,
            "static inline bool wcIsSecretKey(",
            "static inline bool wcIsSecretReadCommand(",
        )
        self.assertIn('strcmp(key, "wifi.pwd") == 0', secret_classifier)

    def test_explicit_local_cli_reads_are_not_config_snapshots(self):
        # Both terminal APIs require the authenticated LAN, and neither applies
        # the config form's secret masking to an explicit command reply.
        batch = function_body(self.server, "void WebConfigServer::drainBatch(",
                              "void WebConfigServer::attachRoutes(")
        self.assertIn("execAdminCommand(e.cmd, e.reply)", batch)
        self.assertNotIn("wcMaskSecretReply", batch)
        for start, end in [
            ("void WebConfigServer::handleCliPost(", "void WebConfigServer::handleCliResult("),
            ("bool WebConfigServer::checkTerminalAccess(", "void WebConfigServer::handleTerminalPost("),
        ]:
            handler = function_body(self.server, start, end)
            self.assertIn("_mode != MODE_LAN", handler)
            self.assertIn("checkAuth(req)", handler)

    def password_reply_fixture(self):
        batch = function_body(self.server, "void WebConfigServer::drainBatch(",
                              "void WebConfigServer::attachRoutes(")
        start = batch.index("      _cb->execCommand(e.cmd, e.reply);")
        snippet = batch[start:batch.index("      // Keep the response snapshot", start)]
        return r'''
#include <cassert>
#include <cstring>
#include <helpers/WebConfigBatch.h>
struct Callback {
  const char* result;
  void execCommand(char*, char* reply) { strcpy(reply, result); }
};
struct Fixture {
  Callback cb{}, *_cb = &cb;
  bool _admin_pwd_set = false;
  struct { char cmd[160] = "password new-secret"; char reply[160] = {}; } e;
  bool run(const char* reply) {
    cb.result = reply;
    _admin_pwd_set = false;
    const bool admin_pwd = true;
@SNIPPET@
    if (e.reply[0] == 0) strcpy(e.reply, "OK");
    return WebConfigBatch::nextAllOk(true, strncmp(e.reply, "OK", 2) == 0);
  }
};
int main() {
  Fixture fixture;
  assert(fixture.run("password now: new-secret"));
  assert(!strcmp(fixture.e.reply, "OK") && fixture._admin_pwd_set);
  assert(fixture.run("OK"));
  for (const char* error : {"Error: save unconfirmed; reboot may differ",
                            "Error: unsupported command", ""}) {
    const bool saved = fixture.run(error);
    assert(!saved && !fixture._admin_pwd_set);
    assert(strncmp(fixture.e.reply, "Error:", 6) == 0);
    assert(WebConfigBatch::finishRebootAt(true, saved, 10) == 0);
    if (error[0]) assert(!strcmp(fixture.e.reply, error));
  }
}
'''.replace("@SNIPPET@", snippet)

    def compile_password_reply_fixture(self, fixture, *, reject=False):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="webconfig-password-reply-") as tmp:
            path = Path(tmp)
            source = path / "test.cpp"
            binary = path / "test"
            source.write_text(fixture)
            result = subprocess.run([compiler, "-std=c++17", "-include", "initializer_list",
                                     "-I", str(ROOT / "src"), str(source), "-o", str(binary)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if reject:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Assertion", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_password_form_masks_success_but_preserves_failed_save(self):
        self.compile_password_reply_fixture(self.password_reply_fixture())

    def test_old_unconditional_password_mask_fails_regression(self):
        fixture = self.password_reply_fixture()
        old = fixture.replace("if (admin_pwd && WebConfigBatch::cliPasswordChanged(e.reply)) {",
                              "if (admin_pwd) {", 1)
        self.assertNotEqual(old, fixture)
        self.compile_password_reply_fixture(old, reject=True)


if __name__ == "__main__":
    unittest.main()
