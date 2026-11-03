"""Exercise the compact OTA page against the existing ElegantOTA wire format."""
from email import policy
from email.parser import BytesParser
import gzip
import hashlib
import http.server
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest

from test_webconfig_ui_runtime import BROWSER, run_browser


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "webui/vendor/spark-md5-3.0.2.min.js"
HEADER = ROOT / "src/helpers/esp32/StaticHtml.h"
SYMBOL = "MESH_HTML_ELEGANT_OTA_COMPACT"


def embedded_page():
    source = HEADER.read_text(encoding="utf-8")
    length = int(re.search(SYMBOL + r"_LEN = (\d+);", source).group(1))
    array = source.split("const uint8_t " + SYMBOL + "[] PROGMEM = {", 1)[1].split("};", 1)[0]
    data = bytes(int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", array))
    if len(data) != length:
        raise AssertionError("compressed OTA page has a bad length")
    return gzip.decompress(data).decode("utf-8")


class CompactElegantOtaTests(unittest.TestCase):
    def test_vendored_incremental_md5_vectors(self):
        self.assertEqual(hashlib.sha256(VENDOR.read_bytes()).hexdigest(),
                         "d80e84c820cc5587a0ba3c8a20652099ea3fa7fc43944e812e56d449c1d9f1c9")
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is unavailable")
        script = r'''
const SparkMD5 = require(process.argv[1]);
const crypto = require('crypto');
if (SparkMD5.hash('') !== 'd41d8cd98f00b204e9800998ecf8427e' ||
    SparkMD5.hash('abc') !== '900150983cd24fb0d6963f7d28e17f72') process.exit(1);
for (const size of [0, 3, 64, 262144, 700123]) {
  const input = Buffer.alloc(size);
  for (let i=0;i<size;i++) input[i] = i % 251;
  const digest = new SparkMD5.ArrayBuffer();
  for (let i=0;i<size;i+=65537) {
    const b=input.subarray(i,Math.min(size,i+65537));
    digest.append(b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength));
  }
  if (digest.end() !== crypto.createHash('md5').update(input).digest('hex')) process.exit(1);
}
'''
        subprocess.run([node, "-e", script, str(VENDOR)], check=True, timeout=30)

    def test_only_tbeam_observers_select_the_page(self):
        source = (ROOT / "arch/esp32/AsyncElegantOTA/src/AsyncElegantOTA.h").read_text()
        self.assertIn("MESH_COMPACT_ELEGANT_OTA", source)
        self.assertIn("#include \"elegantWebpage.h\"", source)
        target = (ROOT / "variants/lilygo_tbeam_universal/platformio.ini").read_text()
        selected = re.findall(r"\[env:([^]]+)\]([^\[]*)", target)
        enabled = [name for name, section in selected if "-D MESH_COMPACT_ELEGANT_OTA=1" in section]
        self.assertEqual(enabled, ["Tbeam_repeater_observer_mqtt", "Tbeam_room_server_observer_mqtt"])
        self.assertEqual(embedded_page().count("MESH_SPARK_MD5"), 0)
        self.assertIn("SparkMD5", embedded_page())

    @unittest.skipUnless(BROWSER, "Chromium-family browser is unavailable")
    def test_real_browser_streams_firmware_and_filesystem_with_correct_md5(self):
        payload = bytes(i % 251 for i in range(700123))  # three FileReader chunks
        requests = []
        page = embedded_page().replace("</body>", r'''
<script>
window.addEventListener('load',function(){
 var bytes=new Uint8Array(700123);for(var i=0;i<bytes.length;i++)bytes[i]=i%251;
 var input=document.getElementById('file'),button=document.getElementById('upload');
 function send(kind){
   document.querySelector('input[value="'+kind+'"]').checked=true;
   var selection=new DataTransfer();selection.items.add(new File([bytes],kind+'.bin'));
   input.files=selection.files;button.click();
 }
 var phase=0;send('firmware');
 document.body.setAttribute('data-test-busy',button.disabled&&input.disabled&&document.querySelector('input[value="filesystem"]').disabled?'yes':'no');
 var timer=setInterval(function(){
   var text=document.getElementById('status').textContent;
   if(phase===2){
     if(text.indexOf('Upload failed (HTTP 500)')<0)return;
     clearInterval(timer);document.body.setAttribute('data-test-done',!button.disabled&&!input.disabled&&!document.querySelector('input[value="filesystem"]').disabled?'yes':'no');
     document.body.setAttribute('data-test-identity',document.getElementById('identity').textContent);return;
   }
   if(text.indexOf('Update complete')<0)return;
   if(phase++===0){send('filesystem');return;}
   send('firmware');
 },30);
});
</script></body>''')

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                body = (b'{"id":"test node","hardware":"ESP32"}' if self.path == "/update/identity"
                        else page.encode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json" if self.path == "/update/identity" else "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                message = BytesParser(policy=policy.default).parsebytes(
                    b"Content-Type: " + self.headers["Content-Type"].encode("ascii")
                    + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
                parts = {p.get_param("name", header="Content-Disposition"): p for p in message.iter_parts()}
                requests.append((self.path, parts["MD5"].get_payload(decode=True).decode(),
                                 [(name, part.get_filename(), part.get_payload(decode=True))
                                  for name, part in parts.items() if name != "MD5"]))
                failure = len(requests) == 3
                response = b"invalid image" if failure else b"OK"
                self.send_response(500 if failure else 200)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="meshcore-ota-browser-") as folder:
                command = [BROWSER, "--headless=new", "--disable-gpu", "--no-first-run",
                           "--user-data-dir=" + folder, "--virtual-time-budget=15000", "--dump-dom",
                           "http://127.0.0.1:%d/update" % server.server_port]
                if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
                    command.insert(1, "--no-sandbox")
                result = run_browser(command)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertIn('data-test-busy="yes"', result.stdout.decode(errors="replace"))
            self.assertIn('data-test-done="yes"', result.stdout.decode(errors="replace"))
            self.assertIn('data-test-identity="test node · ESP32"', result.stdout.decode(errors="replace"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        digest = hashlib.md5(payload).hexdigest()
        self.assertEqual(requests, [("/update", digest, [(name, name, payload)])
                                    for name in ("firmware", "filesystem", "firmware")])


if __name__ == "__main__":
    unittest.main()
