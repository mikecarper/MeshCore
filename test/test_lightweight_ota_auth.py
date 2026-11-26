"""Exercise the production lightweight OTA protocol and bundled upload page."""
import gzip
import json
from pathlib import Path
import re
import subprocess
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start as wifi_start

ROOT = Path(__file__).resolve().parents[1]


class LightweightOtaAuthTest(unittest.TestCase):
    compile_and_run = wifi_start.WiFiOtaStartTest.compile_and_run

    def test_authenticated_identity_and_bound_upload(self):
        source = (ROOT / "src/helpers/ESP32Board.cpp").read_text()
        implementation = source[source.index("class LightweightOTAServer {"):]
        server = extract_braced(implementation, "class LightweightOTAServer {").replace(
            "class LightweightOTAServer {", "class LightweightOTAServer {\npublic:", 1)
        fixture = (ROOT / "test/fixtures/wifi_ota_binding.cpp").read_text()
        fixture = (fixture.replace("@SERVER@", server)
                   .replace("@IDENTITY@", extract_braced(implementation, "bool ESP32Board::getOTAIdentity("))
                   .replace("@STOP@", extract_braced(implementation, "bool ESP32Board::stopOTAUpdate(")))
        for chip in (0, 2, 5, 9, 13):
            with self.subTest(chip=chip):
                self.compile_and_run(fixture, f"-DEXPECTED_CHIP_ID={chip}", "-Wl,--no-as-needed", "-lcrypto")

    def test_browser_verifies_all_identity_fields_before_sending(self):
        page = (ROOT / "webui/pages/lightweight_ota.html").read_bytes()
        assets = (ROOT / "src/helpers/esp32/StaticHtml.h").read_text()
        embedded = re.search(r"\bMESH_HTML_LIGHTWEIGHT_OTA\[\].*?\{([^}]+)\}", assets, re.S)
        self.assertIsNotNone(embedded)
        compressed = bytes(int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]{2})", embedded[1]))
        self.assertEqual(gzip.decompress(compressed), page)
        source = page.decode("utf-8")
        script = source.split("<script>", 1)[1].split("</script>", 1)[0]
        runner = r'''
const assert = require('assert'), vm = require('vm');
const script = @SCRIPT@;
const original = {version: 1, protocol: 'lightweight', session: '0123456789abcdef0123456789abcdef',
  device: '02aabbccdd11', chip: 9, slot: 1024, hardware: 'heltec_v4'};
function check(change, size, expected, status = 200) {
  const fields = {file:{files:[{size}]}, status:{textContent:''}, upload:{disabled:true}};
  const posts = [];
  let current = {...original}, code = 200;
  vm.runInNewContext(script, {
    document: {getElementById: id => fields[id]},
    XMLHttpRequest: class {
      constructor(){this.upload={}; this.headers={};}
      open(method,url){this.method=method; this.url=url;}
      setRequestHeader(name,value){this.headers[name]=value;}
      send(image){
        if (this.method === 'GET') {
          assert.strictEqual(this.url, '/ota/info');
          this.status = code;
          this.responseText = JSON.stringify(current);
          this.onload();
        } else {
          posts.push({headers:this.headers, image, method:this.method, url:this.url});
        }
      }
    }
  });
  assert.strictEqual(fields.upload.disabled, false);
  current = {...current, ...change};
  code = status;
  fields.upload.onclick();
  assert.strictEqual(posts.length, expected);
  if (expected) {
    assert.strictEqual(posts[0].headers['X-MeshCore-OTA-Session'], original.session);
    assert.strictEqual(posts[0].headers['Content-Type'], 'application/octet-stream');
    assert.strictEqual(posts[0].url, '/update');
  } else {
    assert(!fields.status.textContent.startsWith('Ready'));
  }
}
check({}, 8, 1);
for (const change of [
  {device:'02aabbccdd22'}, {session:'fedcba9876543210fedcba9876543210'},
  {chip:5}, {slot:2048}, {hardware:'heltec_v4_tft'},
  {protocol:'legacy'}, {version:2}, {session:'not-a-session'}]) {
  check(change, 8, 0);
}
check({},0,0);
check({},2048,0);
check({},8,0,401);
'''
        result = subprocess.run(["node", "-e", runner.replace("@SCRIPT@", json.dumps(script))],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
