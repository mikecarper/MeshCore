#!/usr/bin/env python3
"""Use Chrome's native DEFLATE stream to read application release ZIPs."""
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import zipfile

from test_browser_lora_ota_runtime import BROWSER, ROOT, run_until_complete


def image():
    body = bytes((i * 17) & 255 for i in range(1800))
    trailer = bytearray(56)
    trailer[:4] = b'EndF'
    struct.pack_into('<I', trailer, 4, len(body))
    trailer[8:16] = hashlib.sha256(body).digest()[:8]
    struct.pack_into('<II', trailer, 16, 0x01120001, 0x12345678)
    trailer[24:29] = b'board'
    return body + trailer


def archive(method, invalid_path=False):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=method) as z:
        z.writestr('manifest.json', json.dumps({'manifest': {'application': {
            'bin_file':'../firmware.bin' if invalid_path else 'firmware.bin',
            'dat_file':'firmware.dat'}, 'dfu_version':0.5}}))
        z.writestr('firmware.dat', bytes(14))
        z.writestr('../firmware.bin' if invalid_path else 'firmware.bin', image())
    return result.getvalue()


class BrowserLoraOtaZipRuntimeTest(unittest.TestCase):
    def test_real_chrome_loads_nordic_zip_and_rejects_traversal(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')
        fixtures = {'/stored.zip':archive(zipfile.ZIP_STORED),
                    '/deflated.zip':archive(zipfile.ZIP_DEFLATED),
                    '/traversal.zip':archive(zipfile.ZIP_STORED, invalid_path=True)}
        page = b'''<!doctype html><script src="/_javascript/lora_ota_package.js"></script>
<script src="/_javascript/lora_ota_zip.js"></script><script>
(async()=>{try{
const result=[];
for(const path of ['/stored.zip','/deflated.zip']){
 const bytes=new Uint8Array(await (await fetch(path)).arrayBuffer());
 const image=await MeshCoreLoraOtaZip.loadApplication(bytes,{targetId:0x12345678,hwId:'board'});
 result.push({length:image.image.length,member:image.member,bodyHash:Array.from(image.bodyHash,x=>x.toString(16).padStart(2,'0')).join('')});
}
let rejected=false;try{await MeshCoreLoraOtaZip.loadApplication(new Uint8Array(await (await fetch('/traversal.zip')).arrayBuffer()));}catch(e){rejected=/path/i.test(e.message);}
await fetch('/__result',{method:'POST',body:JSON.stringify({result,rejected})});
}catch(e){await fetch('/__result',{method:'POST',body:JSON.stringify({error:String(e)})});}})();
</script>'''
        done = threading.Event()
        results = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                if self.path == '/': body=page
                elif self.path in fixtures: body=fixtures[self.path]
                elif self.path in ('/_javascript/lora_ota_zip.js','/_javascript/lora_ota_package.js'):
                    body=(ROOT / 'docs' / self.path.lstrip('/')).read_bytes()
                else: self.send_error(404); return
                self.send_response(200);self.send_header('Content-Length',str(len(body)))
                self.send_header('Content-Type','application/javascript' if self.path.endswith('.js') else 'text/html')
                self.end_headers();self.wfile.write(body)
            def do_POST(self):
                if self.path != '/__result': self.send_error(404); return
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'OK');done.set()

        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='meshcore-zip-browser-',ignore_cleanup_errors=True) as folder:
                command=[BROWSER,'--headless=new','--disable-gpu','--no-first-run','--user-data-dir='+folder,
                         'http://127.0.0.1:%d/' % server.server_port]
                if os.name!='nt' and hasattr(os,'geteuid') and os.geteuid()==0:command.insert(1,'--no-sandbox')
                run_until_complete(command,done,timeout=30)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
        self.assertEqual(len(results),1)
        result=results[0]
        self.assertNotIn('error',result,result)
        self.assertTrue(result['rejected'])
        self.assertEqual([row['length'] for row in result['result']],[len(image()),len(image())])
        self.assertEqual([row['member'] for row in result['result']],['firmware.bin','firmware.bin'])
        self.assertEqual(result['result'][0]['bodyHash'],hashlib.sha256(image()[:-56]).hexdigest()[:16])
        self.assertEqual(result['result'][1]['bodyHash'],result['result'][0]['bodyHash'])


if __name__ == '__main__': unittest.main()
