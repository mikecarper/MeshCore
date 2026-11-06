#!/usr/bin/env python3
"""Run the research OTAFIX WASM Worker in a real browser."""
import http.server
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest

from test_browser_lora_ota_nrf_c_decoder import ROOT, firmware
from test_browser_lora_ota_runtime import BROWSER, run_until_complete

TOOLS = ROOT / 'tools/lora_ota/otafix_wasm'


class OtafixBrowserWorkerTest(unittest.TestCase):
    def test_real_browser_worker_with_both_adaptive_internal_profiles(self):
        node = shutil.which('node')
        if not BROWSER or not node or not (TOOLS / 'build/otafix-2.4.12-rak4631-internal.wasm').is_file():
            if os.environ.get('MESHCORE_REQUIRE_OTAFIX_WASM') == '1':
                self.fail('Chrome, Node and both OTAFIX WASM builds are required')
            self.skipTest('Chrome, Node or the OTAFIX WASM builds are unavailable')
        record = struct.pack('<8sBBHIII', b'mOTALay1', 1, 32, 24, 0x26000, 0xED000, 0xED000)
        body = bytearray((ROOT / 'lab/pram_scanner/scan-safe.bin').read_bytes() * 6)
        base = firmware(bytes(body) + record, 0x01120001)
        for offset in range(73, len(body), 701): body[offset] ^= 0x5a
        target = firmware(bytes(body) + record, 0x01120002)
        for offset in range(41, len(body), 719): body[offset] ^= 0xa5
        final = firmware(bytes(body) + record, 0x01120003)
        page = b'''<!doctype html><script>
(async()=>{try{
 const base=await (await fetch('/base')).arrayBuffer(), target=await (await fetch('/target')).arrayBuffer(),
       pkg=await (await fetch('/delta')).arrayBuffer(), final=await (await fetch('/final')).arrayBuffer(),
       pkg2=await (await fetch('/delta2')).arrayBuffer();
 const worker=new Worker('/worker.js');
 const send=(profile,b,ceiling,t=target,p=pkg)=>new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error('Worker timed out')),10000);
   worker.onmessage=e=>{clearTimeout(timer);resolve(e.data)};
   worker.onerror=e=>{clearTimeout(timer);reject(Error(e.message))};
   worker.postMessage({profile,base:b,package:p,target:t,ceiling});
 });
 const bad=base.slice(0);new Uint8Array(bad)[17]^=1;
 const results=[];
 for(const version of ['2.4.3','2.4.6','2.4.9','2.4.11','2.4.12'])
   for(const board of ['rak3401','rak4631']) {
     results.push(await send(version+'-'+board,base,0xD4000));
     results.push(await send(version+'-'+board,target,0xD4000,final,pkg2));
   }
 results.push(await send('2.4.12-rak4631',bad,0xD4000),await send('2.4.12-rak4631',base,0xDEAD0),
   await send('unknown',base,0xD4000),await send('2.4.12-rak3401',base,0xD4000,base));
 worker.terminate(); await fetch('/result',{method:'POST',body:JSON.stringify({results})});
}catch(e){await fetch('/result',{method:'POST',body:JSON.stringify({error:String(e)})});}})();
</script>'''
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); old=work/'base.bin'; new=work/'new.bin'; package=work/'delta.mota'
            final_path=work/'final.bin'; package2=work/'delta2.mota'
            old.write_bytes(base); new.write_bytes(target); final_path.write_bytes(final)
            script = '''const fs=require('node:fs'),d=require('./docs/_javascript/lora_ota_delta.js');
d.buildInPlaceDelta(fs.readFileSync(process.argv[1]),fs.readFileSync(process.argv[2]),
 {runtimeStageCeiling:0xD4000,runtimeStorage:'internal'})
.then(r=>fs.writeFileSync(process.argv[3],r.blob)).catch(e=>{console.error(e);process.exitCode=1});'''
            subprocess.run([node, '-e', script, str(old), str(new), str(package)], cwd=ROOT, check=True, timeout=120)
            subprocess.run([node, '-e', script, str(new), str(final_path), str(package2)], cwd=ROOT, check=True, timeout=120)
            content={'/':page, '/base':base, '/target':target, '/delta':package.read_bytes(),
                     '/final':final, '/delta2':package2.read_bytes(),
                     '/worker.js':(TOOLS/'worker.js').read_bytes()}
            for version in ('2.4.3','2.4.6','2.4.9','2.4.11','2.4.12'):
                for board in ('3401','4631'):
                    name='otafix-'+version+'-rak'+board+'-internal.wasm'
                    content['/build/'+name]=(TOOLS/'build'/name).read_bytes()
            done=threading.Event(); results=[]
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self,*_): pass
                def do_GET(self):
                    if self.path not in content: self.send_error(404); return
                    data=content[self.path];self.send_response(200)
                    self.send_header('Content-Length',str(len(data)))
                    self.send_header('Content-Type', 'text/html' if self.path == '/' else
                                     'text/javascript' if self.path.endswith('.js') else 'application/octet-stream')
                    self.end_headers();self.wfile.write(data)
                def do_POST(self):
                    if self.path!='/result':self.send_error(404);return
                    results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                    self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'OK');done.set()
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with tempfile.TemporaryDirectory(prefix='otafix-worker-',ignore_cleanup_errors=True) as profile:
                    command=[BROWSER,'--headless=new','--disable-gpu','--no-first-run','--user-data-dir='+profile,
                             'http://127.0.0.1:%d/' % server.server_port]
                    if os.name!='nt' and hasattr(os,'geteuid') and os.geteuid()==0:command.insert(1,'--no-sandbox')
                    run_until_complete(command,done,timeout=30)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
        self.assertEqual(len(results),1)
        self.assertNotIn('error',results[0],results[0])
        self.assertEqual([item['ok'] for item in results[0]['results']],[True]*20+[False]*4,results[0])


if __name__ == '__main__':unittest.main()
