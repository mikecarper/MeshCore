#!/usr/bin/env python3

import gzip
from pathlib import Path
import re
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


if __name__ == "__main__":
    unittest.main()
