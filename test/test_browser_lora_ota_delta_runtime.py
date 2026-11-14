#!/usr/bin/env python3
"""Exercise browser-built differential OTA with a real worker and fake USB/RF."""
import http.server
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

from test_browser_lora_ota_runtime import BROWSER, HARNESS, ROOT, run_until_complete
from test_browser_lora_ota_routed_runtime import one_replace


def delta_harness():
    harness = one_replace(HARNESS,
        "let folder=false, mid='', state='idle', installed=false, newHash='', job='', jobResult='';",
        "let folder=false, mid='', state='idle', installed=false, newHash='', baseHash='', job='', jobResult='', codec=-1, total=0, served=[];")
    harness = one_replace(harness,
        "const enc=new TextEncoder(), dec=new TextDecoder();",
        "const enc=new TextEncoder(), dec=new TextDecoder();\n"
        "  let workerCount=0;const RealWorker=window.Worker;window.Worker=class extends RealWorker{constructor(...args){super(...args);workerCount++}};")
    harness = one_replace(harness,
        "(installed?newHash:'1122334455667788')",
        "(installed?newHash:baseHash)")
    harness = one_replace(harness,
        "if(cmd==='ver') return installed?'v1.18.0.1':'v1.17.1.9';",
        "if(cmd==='get bootloader.ver')return 'Error: unsupported';\n"
        "      if(cmd==='ver') return installed?'v1.18.0.1':'v1.17.1.9';")
    harness = one_replace(harness,
        "if(cmd.startsWith('ota pull ')){if(!mid||!cmd.toLowerCase().includes(mid))throw Error('wrong pull');state='fetching';queueMicrotask(()=>this.push(req(3,[0,0,0,0,0,4,0])));return 'OK';}",
        "if(cmd.startsWith('ota pull ')){if(!mid||!cmd.toLowerCase().includes(mid))throw Error('wrong pull');state='fetching';queueMicrotask(()=>this.nextRead());return 'OK';}")
    harness = one_replace(harness,
        "    async write(bytes){",
        """    nextRead(){
      const n=Math.min(192,total-served.length), off=served.length;
      if(n<=0)throw Error('invalid source length');
      this.push(req(3,[0,off&255,(off>>>8)&255,(off>>>16)&255,(off>>>24)&255,n&255,(n>>>8)&255]));
    }
    async write(bytes){""")
    harness = one_replace(harness,
        "if(bytes[2]===2&&bytes[3]===0)mid=hex(bytes.slice(4,8));",
        "if(bytes[2]===2&&bytes[3]===0){mid=hex(bytes.slice(4,8));codec=bytes[16];total=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength).getUint32(18,true);}")
    harness = one_replace(harness,
        "if(bytes[2]===3&&bytes[3]===0){if(dec.decode(bytes.slice(4,8))!=='mOTA')throw Error('wrong package bytes');state='ready';}",
        """if(bytes[2]===3&&bytes[3]===0){
          served.push(...bytes.slice(4,-1));
          if(served.length<total)queueMicrotask(()=>this.nextRead());
          else {if(served.length!==total||dec.decode(Uint8Array.from(served.slice(0,4)))!=='mOTA')throw Error('wrong package bytes');state='ready';}
        }""")
    harness = one_replace(harness,
        "const dt=new DataTransfer();dt.items.add(new File([image],'firmware.bin'));el('file').files=dt.files;",
        """const base=image.slice();base[0]=42;
      const baseDigest=new Uint8Array(await crypto.subtle.digest('SHA-256',base.slice(0,at))).slice(0,8);
      base.set(baseDigest,at+8);baseHash=hex(baseDigest);new DataView(base.buffer).setUint32(at+16,0x01110109,true);
      const dt=new DataTransfer();dt.items.add(new File([image],'firmware.bin'));el('file').files=dt.files;
      const bt=new DataTransfer();bt.items.add(new File([base],'installed.bin'));el('base').files=bt.files;""")
    harness = one_replace(harness,
        "frames:port.frames,folder,buttons:",
        "frames:port.frames,folder,codec,total,served,workerCount,baseHash,newHash,buttons:")
    return harness


class BrowserLoraOtaDeltaRuntimeTest(unittest.TestCase):
    def test_worker_builds_and_transfers_complete_sequential_delta(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')
        markdown = (ROOT / 'docs/browser_lora_ota.md').read_text(encoding='utf-8')
        div = re.search(r'<div data-lora-ota>.*?</div>', markdown, re.S).group()
        scripts = re.findall(r'<script src="[^"]+"></script>', markdown)
        page = '<!doctype html><meta charset="utf-8">' + div + delta_harness() + ''.join(scripts)
        done = threading.Event()
        results, requested, missing_scripts = [], [], []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                requested.append(self.path)
                path = urlsplit(self.path).path
                if path in ('/', '/browser_lora_ota/'):
                    body = page.encode('utf-8')
                elif re.fullmatch(r'/_javascript/lora_ota_[a-z_]+\.js', path) or re.fullmatch(
                        r'/_javascript/vendor/detools-js/[a-z-]+\.js', path):
                    body = (ROOT / 'docs' / path.lstrip('/')).read_bytes()
                else:
                    if path.startswith('/_javascript/') and path.endswith('.js'):
                        missing_scripts.append(self.path)
                    self.send_error(404); return
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'text/html; charset=utf-8' if path.endswith('/') else 'application/javascript; charset=utf-8')
                self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                if self.path != '/__result': self.send_error(404); return
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'OK'); done.set()

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='meshcore-lora-delta-', ignore_cleanup_errors=True) as folder:
                command = [BROWSER, '--headless=new', '--disable-gpu', '--no-first-run',
                           '--user-data-dir='+folder, 'http://127.0.0.1:%d/browser_lora_ota/' % server.server_port]
                if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0: command.insert(1, '--no-sandbox')
                run_until_complete(command, done, timeout=45)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertFalse(missing_scripts, f'Browser scripts did not load: {missing_scripts}')
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertNotIn('error', result, result)
        self.assertIn('Preparing the differential package', result['status'])
        self.assertIn('differential package', result['status'])
        self.assertIn('Transfer complete.', result['status'])
        self.assertIn('Installed and verified: board', result['status'])
        self.assertNotIn('Error:', result['status'])
        self.assertEqual(result['buttons'], [True, True, True])
        self.assertEqual(result['journal']['phase'], 'installed')
        self.assertEqual(result['journal']['bodyHash'], result['newHash'])
        self.assertEqual(result['codec'], 1)
        self.assertEqual(result['workerCount'], 1)
        self.assertIn('/_javascript/lora_ota_delta_worker.js', requested)
        self.assertIn('/_javascript/vendor/detools-js/bsdiff.js', requested)
        self.assertEqual(result['total'], len(result['served']))
        self.assertEqual(bytes(result['served'][:4]), b'mOTA')
        self.assertEqual(result['served'][64], 1)
        self.assertEqual(bytes(result['served'][97:105]).hex(), result['baseHash'])
        self.assertFalse(result['folder'])
        self.assertIn('remote get bootloader.ver', result['cmds'])
        self.assertEqual(sum(c.startswith('remote ota pull ') for c in result['cmds']), 1)
        self.assertEqual(sum(bool(re.fullmatch(r'remote ota installhash [0-9a-f]{64}', c)) for c in result['cmds']), 1)
        self.assertNotIn('remote ota install', result['cmds'])


if __name__ == '__main__': unittest.main()
