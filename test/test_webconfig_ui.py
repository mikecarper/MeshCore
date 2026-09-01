#!/usr/bin/env python3

import gzip
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "webui" / "index.html"
HEADER = ROOT / "src" / "helpers" / "esp32" / "WebConfigHtml.h"


class WebConfigUiTest(unittest.TestCase):
    def test_wifi_password_fields_have_local_show_controls(self):
        page = SOURCE.read_text(encoding="utf-8")
        self.assertEqual(page.count('onclick="toggleWiFiPassword(this)"'), 2)
        self.assertEqual(page.count('class="pwrow"'), 2)
        self.assertIn('type="password" data-k="wifi.pwd" id="wz-wifi-pwd"', page)
        self.assertIn('type="password" data-k="wifi.pwd" id="app-wifi-pwd"', page)
        self.assertIn('input.type=show?"text":"password"', page)
        self.assertIn('btn.textContent=show?"Hide":"Show"', page)
        self.assertIn('btn.setAttribute("aria-pressed",show?"true":"false")', page)

    def test_generated_asset_contains_show_controls(self):
        header = HEADER.read_text(encoding="utf-8")
        length = int(re.search(r"WEBCONFIG_HTML_GZ_LEN = (\d+);", header).group(1))
        array = header.split(
            "const uint8_t WEBCONFIG_HTML_GZ[] PROGMEM = {", 1
        )[1]
        blob = bytes(
            int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", array)
        )[:length]
        page = gzip.decompress(blob).decode("utf-8")
        self.assertEqual(page.count('onclick="toggleWiFiPassword(this)"'), 2)
        self.assertIn("function toggleWiFiPassword(btn)", page)


if __name__ == "__main__":
    unittest.main()
