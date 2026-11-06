#!/usr/bin/env python3
"""Cross-check browser-generated deltas with the reference detools decoder."""
import hashlib
import base64
import http.server
import json
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from test_compact_elegant_ota import run_until_complete
from test_webconfig_ui_runtime import BROWSER

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/mota'))
try:
    import detools
    import motalib
except ImportError:
    detools = motalib = None


def image(body, version):
    return (body + b'EndF' + struct.pack('<I', len(body)) + hashlib.sha256(body).digest()[:8]
            + struct.pack('<II', version, 0x12345678) + b'test_board'.ljust(32, b'\0'))


class BrowserDeltaTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable')
    def test_in_place_detools_and_exact_flash_geometry(self):
        if detools is None: self.skipTest('Python detools unavailable')
        fixture = (ROOT/'lab/pram_scanner/scan-safe.bin').read_bytes()*6
        layouts=[(0,0xED000,0xED000,'internal'),(2,0xD4000,0xD4000,'internal'),(16,0xED000,0xED000,'internal'),
                 (32,0xED000,0xED000,'internal'),(48,0xED000,0xED000,'internal'),
                 (4,0xED000,0xED000,'qspi'),(12,0xE0000,0xED000,'qspi'),(32,0xED000,0xED000,'qspi')]
        cases=[]
        for flags, end, stage, storage in layouts:
            record=struct.pack('<8sBBHIII',b'mOTALay1',1,flags,24,0x26000,end,stage)
            cases.append((fixture+record,stage,storage))
        path=os.environ.get('MESHCORE_NRF_DELTA_FIRMWARE')
        if path:
            raw=Path(path).read_bytes(); parsed_layout=motalib.parse_nrf52_layout(raw)
            self.assertIsNotNone(parsed_layout)
            cases.append((raw[:-56],parsed_layout.stage_ceiling,'internal'))
        with tempfile.TemporaryDirectory() as td:
            tmp=Path(td)
            for body,stage,storage in cases:
                with self.subTest(size=len(body),stage=hex(stage),storage=storage):
                    target_body=bytearray(body)
                    for at in range(61,len(target_body)-24,max(107,len(target_body)//31)):target_body[at]^=0x5a
                    base=image(body,0x01120001); target=image(bytes(target_body),0x01120002)
                    (tmp/'base.bin').write_bytes(base);(tmp/'target.bin').write_bytes(target)
                    script='''const fs=require('node:fs'),d=require('./docs/_javascript/lora_ota_delta.js');
d.buildInPlaceDelta(fs.readFileSync(process.argv[1]),fs.readFileSync(process.argv[2]),{runtimeStageCeiling:Number(process.argv[4]),runtimeStorage:process.argv[5],runtimeQspiApply:true,runtimeStorageBytes:2097152})
.then(r=>fs.writeFileSync(process.argv[3],r.blob)).catch(e=>{console.error(e);process.exitCode=1});'''
                    subprocess.run(['node','-e',script,str(tmp/'base.bin'),str(tmp/'target.bin'),str(tmp/'update.mota'),str(stage),storage],cwd=ROOT,check=True,timeout=120)
                    parsed=motalib.parse_container((tmp/'update.mota').read_bytes())
                    self.assertEqual(parsed.manifest.codec_id,2)
                    self.assertEqual(motalib.verify(parsed,base_image=base),[])
                    kind,geometry=detools.patch_info(__import__('io').BytesIO(parsed.payload))
                    self.assertEqual(kind,'in-place');self.assertEqual(geometry[4],4096)
                    if storage=='internal':
                        self.assertLessEqual(geometry[3]+((len((tmp/'update.mota').read_bytes())+4095)//4096)*4096,stage-0x26000)
                    else:
                        self.assertLessEqual(geometry[3],motalib.parse_nrf52_layout(base).linked_app_end-0x26000)

    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable')
    def test_detools_reconstructs_browser_packages(self):
        if detools is None:
            if os.environ.get('MESHCORE_REQUIRE_DETOOLS') == '1': self.fail('detools is required')
            self.skipTest('Python detools is unavailable')
        rng = random.Random(8624)
        # Real compiled LR2021 scanner code, enlarged with deterministic data;
        # an optional local firmware image tests an actual full-size build.
        firmware = (ROOT / 'lab/pram_scanner/scan-safe.bin').read_bytes()
        fixture = firmware * max(1, 65536 // len(firmware)) + rng.randbytes(8192)
        cases = [b'A' * 512, rng.randbytes(4096), fixture]
        path = os.environ.get('MESHCORE_DELTA_FIRMWARE')
        if path: cases.append(Path(path).read_bytes())
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for i, body in enumerate(cases):
                with self.subTest(bytes=len(body)):
                    # Relocated byte ranges and changed code expose bad seek and
                    # offset encoding, while mostly unchanged flash exercises CRLE.
                    modified = bytearray(body)
                    for at in range(71, len(modified), max(113, len(modified) // 37)):
                        modified[at] ^= 0x5a
                    if len(modified) > 2000: modified[1200:1200] = bytes(range(64))
                    base = image(body, 0x01120001); target = image(bytes(modified), 0x01120002)
                    (tmp/'base.bin').write_bytes(base); (tmp/'target.bin').write_bytes(target)
                    script = '''const fs=require('node:fs');const d=require('./docs/_javascript/lora_ota_delta.js');
d.buildSequentialDelta(fs.readFileSync(process.argv[1]),fs.readFileSync(process.argv[2])).then(r=>fs.writeFileSync(process.argv[3],r.blob)).catch(e=>{console.error(e);process.exitCode=1});'''
                    subprocess.run(['node', '-e', script, str(tmp/'base.bin'), str(tmp/'target.bin'), str(tmp/'update.mota')],
                                   cwd=ROOT, check=True, timeout=120)
                    parsed = motalib.parse_container((tmp/'update.mota').read_bytes())
                    self.assertFalse(parsed.manifest.is_full)
                    self.assertEqual(parsed.manifest.codec_id, 1)
                    self.assertEqual(parsed.manifest.base_hash, hashlib.sha256(body).digest()[:8])
                    self.assertEqual(motalib.verify(parsed, base_image=base), [])
                    import io
                    rebuilt = io.BytesIO()
                    detools.apply_patch(io.BytesIO(base), io.BytesIO(parsed.payload), rebuilt)
                    self.assertEqual(rebuilt.getvalue(), target)
                    if len(body) > 4096: self.assertLess(len(parsed.payload), len(target) // 2)

    def test_real_browser_worker(self):
        if not BROWSER or detools is None:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chrome and detools are required')
            self.skipTest('Chrome or Python detools unavailable')
        record=struct.pack('<8sBBHIII',b'mOTALay1',1,0,24,0x26000,0xED000,0xED000)
        body = (ROOT / 'lab/pram_scanner/scan-safe.bin').read_bytes() * 4 + record
        newer = bytearray(body); newer[24:27] = b'new'; newer[160:160] = b'insert'
        base, target = image(body, 0x01120001), image(bytes(newer), 0x01120002)
        qrecord=struct.pack('<8sBBHIII',b'mOTALay1',1,4,24,0x26000,0xED000,0xED000)
        qbase=image(body[:-24]+qrecord,0x01120001);qtarget=image(bytes(newer[:-24])+qrecord,0x01120002)
        samples = [base64.b64encode(x).decode('ascii') for x in (base, target,qbase,qtarget)]
        page = '''<!doctype html><meta charset="utf-8"><script src="/_javascript/lora_ota_delta.js"></script><script>
const un64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
const [a,b,q,r]=%s.map(un64);let progress=false;
MeshCoreLoraDelta.buildInWorker(a,b,{blockSize:1024,progress:()=>progress=true}).then(async d=>{
 const pkg=btoa(Array.from(d.blob,x=>String.fromCharCode(x)).join(''));
 const n=await MeshCoreLoraDelta.buildInWorker(a,b,{blockSize:1024,mode:'in-place',runtimeStageCeiling:0xED000});
 const ip=btoa(Array.from(n.blob,x=>String.fromCharCode(x)).join(''));
 const z=await MeshCoreLoraDelta.buildInWorker(q,r,{blockSize:1024,mode:'in-place',runtimeStorage:'qspi',runtimeQspiApply:true,runtimeStorageBytes:2097152});
 const qspi=btoa(Array.from(z.blob,x=>String.fromCharCode(x)).join(''));
 fetch('/result',{method:'POST',body:JSON.stringify({phase:'complete',package:pkg,inplace:ip,qspi,progress,baseLength:a.length,targetLength:b.length})});
}).catch(e=>fetch('/result',{method:'POST',body:JSON.stringify({error:String(e)})}));</script>''' % json.dumps(samples)
        results=[]; done=threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                if self.path=='/': data=page.encode('utf-8')
                else:
                    file=(ROOT/'docs'/self.path.lstrip('/')).resolve()
                    if not file.is_relative_to((ROOT/'docs/_javascript').resolve()) or not file.is_file(): self.send_error(404); return
                    data=file.read_bytes()
                self.send_response(200); self.send_header('Content-Length',str(len(data)))
                self.send_header('Content-Type','text/html' if self.path=='/' else 'application/javascript');self.end_headers();self.wfile.write(data)
            def do_POST(self):
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))));self.send_response(200)
                self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'OK');done.set()
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='meshcore-delta-worker-',ignore_cleanup_errors=True) as folder:
                command=[BROWSER,'--headless=new','--disable-gpu','--no-first-run','--user-data-dir='+folder,
                         'http://127.0.0.1:%d/'%server.server_port]
                if os.name!='nt' and hasattr(os,'geteuid') and os.geteuid()==0: command.insert(1,'--no-sandbox')
                run_until_complete(command,done,timeout=30)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
        self.assertEqual(len(results),1); self.assertEqual(results[0].get('phase'),'complete',results)
        self.assertTrue(results[0]['progress']);self.assertEqual(results[0]['baseLength'],len(base));self.assertEqual(results[0]['targetLength'],len(target))
        parsed=motalib.parse_container(base64.b64decode(results[0]['package']))
        self.assertEqual(motalib.verify(parsed,base_image=base),[])
        parsed=motalib.parse_container(base64.b64decode(results[0]['inplace']))
        self.assertEqual(parsed.manifest.codec_id,2);self.assertEqual(motalib.verify(parsed,base_image=base),[])
        parsed=motalib.parse_container(base64.b64decode(results[0]['qspi']))
        self.assertEqual(parsed.manifest.codec_id,2);self.assertEqual(motalib.verify(parsed,base_image=qbase),[])


if __name__ == '__main__': unittest.main()
