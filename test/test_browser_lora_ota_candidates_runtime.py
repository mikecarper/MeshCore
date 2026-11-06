#!/usr/bin/env python3
"""Real-browser in-place nRF52 candidate generation and detools verification."""
import base64
import http.server
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest

from test_compact_elegant_ota import run_until_complete
from test_webconfig_ui_runtime import BROWSER
from test_browser_lora_ota_delta import image, detools, motalib

ROOT = Path(__file__).resolve().parents[1]


class BrowserCandidatesRuntime(unittest.TestCase):
    def test_real_worker_builds_nrf_candidate_in_place(self):
        if not BROWSER or detools is None:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chrome and detools are required')
            self.skipTest('Chrome or Python detools unavailable')
        record = struct.pack('<8sBBHIII', b'mOTALay1', 1, 0, 24, 0x26000, 0xED000, 0xED000)
        body = (ROOT / 'lab/pram_scanner/scan-safe.bin').read_bytes() * 4 + record
        changed = bytearray(body)
        changed[24:27] = b'new'
        base = image(body, 0x01120001)
        target = image(bytes(changed), 0x01120002)
        samples = [base64.b64encode(x).decode('ascii') for x in (base, target)]
        page = '''<!doctype html><meta charset="utf-8">
<script src="/_javascript/lora_ota_package.js"></script>
<script src="/_javascript/lora_ota_delta.js"></script>
<script src="/_javascript/lora_ota_checks.js"></script>
<script src="/_javascript/lora_ota_plan.js"></script>
<script src="/_javascript/lora_ota_candidates.js"></script>
<script>
const un64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
const [base,target]=%s.map(un64);
(async()=>{
 const f=await MeshCoreMotaPackage.parseEndF(base);
 const c={otaConfirmed:true,nrf:true,external:false,sd:false,maxBlock:1024,codecs:[2],runtimeStorage:'internal',runtimeStageCeiling:0xED000};
 const live={...c,node:'old',version:f.fwVersion,targetId:f.targetId,hwId:f.hwId,bodyHash:LoraOtaChecks.hex(f.bodyHash)};
 const result=await LoraOtaCandidates.prepare({images:[{id:'old',bytes:base,capabilities:c},{id:'new',bytes:target}],current:live,goal:'new'});
 const checked=await LoraOtaCandidates.checkStep(result,0,live), s=result.steps[0];
 const blob=btoa(Array.from(s.package.blob,x=>String.fromCharCode(x)).join(''));
 await fetch('/result',{method:'POST',body:JSON.stringify({complete:result.complete,kind:s.kind,codec:checked.info.codecId,plan:s.edge.inplacePlan,package:blob})});
})().catch(e=>fetch('/result',{method:'POST',body:JSON.stringify({error:String(e)})}));
</script>''' % json.dumps(samples)
        results=[]
        done=threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                if self.path=='/': data=page.encode('utf-8')
                else:
                    file=(ROOT/'docs'/self.path.lstrip('/')).resolve()
                    if not file.is_relative_to((ROOT/'docs/_javascript').resolve()) or not file.is_file():
                        self.send_error(404); return
                    data=file.read_bytes()
                self.send_response(200)
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Content-Type','text/html' if self.path=='/' else 'application/javascript')
                self.end_headers(); self.wfile.write(data)
            def do_POST(self):
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'OK'); done.set()
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='meshcore-candidate-worker-',ignore_cleanup_errors=True) as folder:
                command=[BROWSER,'--headless=new','--disable-gpu','--no-first-run','--user-data-dir='+folder,
                         'http://127.0.0.1:%d/'%server.server_port]
                if os.name!='nt' and hasattr(os,'geteuid') and os.geteuid()==0: command.insert(1,'--no-sandbox')
                run_until_complete(command,done,timeout=40)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertEqual(len(results),1)
        self.assertEqual(results[0].get('kind'),'delta',results)
        self.assertEqual(results[0].get('codec'),2,results)
        self.assertEqual(results[0]['plan']['storage'],'internal')
        self.assertEqual(results[0]['plan']['app'],0x26000)
        self.assertGreater(results[0]['plan']['memorySize'],0)
        parsed=motalib.parse_container(base64.b64decode(results[0]['package']))
        self.assertEqual(motalib.verify(parsed,base_image=base),[])


if __name__ == '__main__': unittest.main()
