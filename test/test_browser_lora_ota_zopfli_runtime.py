#!/usr/bin/env python3
"""Exercise the actual locally served Zopfli worker and WASM in Chromium."""
import base64
import http.server
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import zlib

from test_compact_elegant_ota import run_until_complete
from test_webconfig_ui_runtime import BROWSER

ROOT = Path(__file__).resolve().parents[1]

PAGE = b'''<!doctype html><script src="/_javascript/lora_ota_zopfli.js"></script>
<script>
window.onload=async()=>{
  let result;
  try{
    const a=Uint8Array.from({length:1024},(_,i)=>(i*i+i)%251);
    const b=Uint8Array.from({length:2048},(_,i)=>i%29);
    const first=await LoraOtaZopfli.compress(a),second=await LoraOtaZopfli.compress(b);
    const controller=new AbortController();
    const pending=LoraOtaZopfli.compress(new Uint8Array(4096),{signal:controller.signal});
    setTimeout(()=>controller.abort(),1);
    let canceled=false;try{await pending;}catch(e){canceled=e.name==='AbortError';}
    const next=await LoraOtaZopfli.compress(Uint8Array.of(4,3,2,1));
    const b64=x=>btoa(Array.from(x,v=>String.fromCharCode(v)).join(''));
    result={first:b64(first),second:b64(second),next:b64(next),canceled};
    LoraOtaZopfli.dispose();
  }catch(e){result={error:String(e&&e.stack||e)};}
  await fetch('/__result',{method:'POST',body:JSON.stringify(result)});
};</script>'''


class BrowserZopfliTest(unittest.TestCase):
    def test_real_worker_compresses_and_cancels(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')
        finished = threading.Event()
        results = []
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(ROOT / 'docs'), **kwargs)
            def log_message(self, *_): pass
            def do_GET(self):
                if self.path == '/':
                    self.send_response(200); self.send_header('Content-Length',str(len(PAGE)))
                    self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(PAGE)
                else: super().do_GET()
            def do_POST(self):
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(204);self.end_headers();finished.set()
        server = http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='mesh-zopfli-browser-',ignore_cleanup_errors=True) as folder:
                command=[BROWSER,'--headless=new','--disable-gpu','--no-first-run',
                         '--user-data-dir='+folder,'http://127.0.0.1:%d/'%server.server_port]
                if os.name!='nt' and hasattr(os,'geteuid') and os.geteuid()==0: command.insert(1,'--no-sandbox')
                run_until_complete(command,finished,timeout=60)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
        self.assertEqual(len(results),1)
        result=results[0];self.assertNotIn('error',result,result)
        self.assertTrue(result['canceled'])
        for name,expected in [('first',bytes((i*i+i)%251 for i in range(1024))),
                              ('second',bytes(i%29 for i in range(2048))),
                              ('next',bytes([4,3,2,1]))]:
            self.assertEqual(zlib.decompress(base64.b64decode(result[name]),-15),expected)


if __name__=='__main__': unittest.main()
