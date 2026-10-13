#!/usr/bin/env python3
"""Compile production lightweight OTA identity/HTTP code against peripheral fakes."""
from pathlib import Path
import json
import subprocess
import unittest

from test_replay_reset_integration import extract_braced
import test_wifi_ota_start

ROOT = Path(__file__).resolve().parents[1]


class WiFiOtaBindingTest(unittest.TestCase):
    compile_and_run = test_wifi_ota_start.WiFiOtaStartTest.compile_and_run
    def test_bound_identity_and_http_no_write_gates_on_each_esp_chip(self):
        source = (ROOT / "src/helpers/ESP32Board.cpp").read_text()
        supported = source[source.index("class LightweightOTAServer {"):]
        server = extract_braced(supported, "class LightweightOTAServer {").replace(
            "class LightweightOTAServer {", "class LightweightOTAServer {\npublic:", 1)
        identity = extract_braced(supported, "bool ESP32Board::getOTAIdentity(")
        stop = extract_braced(supported, "bool ESP32Board::stopOTAUpdate(")
        fixture = (ROOT / "test/fixtures/wifi_ota_binding.cpp").read_text()
        fixture = fixture.replace("@SERVER@", server).replace("@IDENTITY@", identity).replace("@STOP@", stop)
        for chip, code in (("ESP32", 0), ("ESP32S2", 2), ("ESP32S3", 9), ("ESP32C3", 5), ("ESP32C6", 13)):
            with self.subTest(chip=chip):
                self.compile_and_run(fixture, "-DCONFIG_IDF_TARGET_" + chip + "=1",
                    "-DEXPECTED_CHIP_ID=" + str(code), "-Wl,--no-as-needed", "-lcrypto")

    def test_legacy_uploader_never_returns_a_binding_token(self):
        source = (ROOT / "src/helpers/ESP32Board.cpp").read_text()
        unsupported = extract_braced(source, "bool ESP32Board::getOTAIdentity(")
        fixture = """
#include <cassert>
#include <cstdio>
#include <cstring>
struct ESP32Board { bool getOTAIdentity(char*, size_t); };
@IDENTITY@
int main() {
  ESP32Board board; char reply[160] = {};
  assert(!board.getOTAIdentity(reply, sizeof(reply)));
  assert(strstr(reply, "unavailable") && !strstr(reply, "OTA1"));
}
"""
        self.compile_and_run(fixture.replace("@IDENTITY@", unsupported))

    def test_browser_requires_verified_identity_and_session_header(self):
        source = (ROOT / "webui/pages/lightweight_ota.html").read_text()
        self.assertIn("fetch('/ota/info'", source)
        self.assertIn("X-MeshCore-OTA-Session", source)
        self.assertIn("i.device!==identity.device", source)
        self.assertIn("i.session!==identity.session", source)
        script = source.split("<script>", 1)[1].split("</script>", 1)[0]
        result = subprocess.run(["node", "--check"], input=script,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_browser_refuses_node_session_and_slot_changes_before_post(self):
        source = (ROOT / "webui/pages/lightweight_ota.html").read_text()
        script = source.split("<script>", 1)[1].split("</script>", 1)[0]
        runner = r"""
const assert=require('assert'),vm=require('vm');
const script=@SCRIPT@;
const original={version:1,protocol:'lightweight',session:'0123456789abcdef0123456789abcdef',
  device:'02aabbccdd11',chip:9,slot:1024,hardware:'heltec_v4'};
async function check(change,size,expected){
  const fields={file:{files:[{size}]},status:{textContent:''},upload:{disabled:true},node:{textContent:''}};
  let current={...original};const posts=[];
  const context={document:{getElementById:id=>fields[id]},
    fetch:async()=>({ok:true,json:async()=>({...current})}),
    XMLHttpRequest:class{
      constructor(){this.upload={};this.headers={}}
      open(method,url){this.method=method;this.url=url}
      setRequestHeader(name,value){this.headers[name]=value}
      send(file){posts.push({headers:this.headers,file,method:this.method,url:this.url})}
    }};
  vm.runInNewContext(script,context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.strictEqual(fields.upload.disabled,false);
  current={...current,...change};
  await fields.upload.onclick();
  assert.strictEqual(posts.length,expected);
  if(expected){assert.strictEqual(posts[0].headers['X-MeshCore-OTA-Session'],original.session);
    assert.strictEqual(posts[0].url,'/update')}
  else assert(fields.status.textContent.length>0);
}
(async()=>{
  await check({},8,1);
  for(const change of [
    {device:'02aabbccdd22'},{session:'fedcba9876543210fedcba9876543210'},
    {chip:5},{slot:2048},{hardware:'heltec_v4_tft'},
    {protocol:'legacy'},{version:2},{session:'not-a-session'}])
    await check(change,8,0);
  await check({},0,0);await check({},2048,0);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(["node", "-e", runner.replace("@SCRIPT@", json.dumps(script))],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
