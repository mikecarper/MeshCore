#!/usr/bin/env python3
"""Exercise the real browser UI, package parser, session, and serial protocol."""
import http.server
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest

from test_compact_elegant_ota import run_until_complete
from test_webconfig_ui_runtime import BROWSER

ROOT = Path(__file__).resolve().parents[1]

# Fake only the USB serial device. All UI, mOTA, and session logic is real.
HARNESS = r'''<script>
(() => {
  const originalTimer=window.setTimeout;
  window.setTimeout=(fn,ms,...a)=>originalTimer(fn,ms===20000?5:ms,...a);
  const enc=new TextEncoder(), dec=new TextDecoder();
  const source='a'.repeat(64), target='b'.repeat(64), cmds=[];
  let folder=false, mid='', state='idle', installed=false, newHash='', job='', jobResult='';
  const radio='910.525,62.5,7,5,120';
  const config='ota config: autofetch=off autoinstall=off hops=0';
  const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
  const req=(op,args=[])=>{let c=op;for(const b of args)c^=b;return Uint8Array.of(77,83,op,...args,c)};
  class Port {
    constructor(){this.q=[];this.pending=null;this.frames=[]}
    push(v){if(this.pending){const r=this.pending;this.pending=null;r({value:v,done:false})}else this.q.push(v)}
    async open(){
      this.readable={getReader:()=>({read:()=>this.q.length?Promise.resolve({value:this.q.shift(),done:false}):new Promise(r=>this.pending=r),
        cancel:async()=>{if(this.pending){this.pending({done:true});this.pending=null}},releaseLock(){}})};
      this.writable={getWriter:()=>({write:async b=>this.write(b),close:async()=>{},releaseLock(){}})};
    }
    async close(){}
    remote(cmd){
      cmds.push('remote '+cmd);
      if(cmd==='ota status') return 'OTA hw=board | target:12345678 | maxblk:1024 | '+(state==='idle'?'no download':'download: '+(state==='ready'?'ready to install':'fetching')+' id='+mid);
      if(cmd==='ota self') return 'self body=44 image=100 base_hash='+(installed?newHash:'1122334455667788')+' | maxblk:1024';
      if(cmd==='ver') return installed?'v1.18.0.1':'v1.17.1.9';
      if(cmd==='get radio')return radio.split(',').slice(0,4).join(',');
      if(cmd==='get tempradio')return radio;
      if(cmd==='get radio.rxps')return 'off,1000,1000';
      if(cmd==='ota config')return config;
      if(cmd==='ota ls')return mid||'No updates seen yet';
      if(cmd.startsWith('ota pull ')){if(!mid||!cmd.toLowerCase().includes(mid))throw Error('wrong pull');state='fetching';queueMicrotask(()=>this.push(req(3,[0,0,0,0,0,4,0])));return 'OK';}
      if(cmd==='ota installhash')return 'ERR usage: ota installhash <64-hex-container-sha256>';
      if(/^ota installhash [0-9a-f]{64}$/.test(cmd)){installed=true;return 'OK';}
      throw Error('Unexpected remote: '+cmd);
    }
    async write(bytes){
      if(bytes[0]===109&&bytes[1]===115){
        this.frames.push(Array.from(bytes));
        if(bytes[2]===2&&bytes[3]===0)mid=hex(bytes.slice(4,8));
        if(bytes[2]===3&&bytes[3]===0){if(dec.decode(bytes.slice(4,8))!=='mOTA')throw Error('wrong package bytes');state='ready';}
        return;
      }
      const cmd=dec.decode(bytes).trim();cmds.push('local '+cmd);
      let reply=({'get public.key':source,'board':'Test board','ota.peer status':'OTApeer v=1 session=0 busy=0 peers=1',
        'ota.peer capabilities':'OTApeer known_repeater=1 routed_path=1','ota.peer path':'OTApeer path=off',
        'ota.peer close':'OTApeer closed','get tempradio':radio,'get radio':radio,
        'get radio.rxps':'off,1000,1000','ota config':config,'ota stats':'fetch idle'})[cmd];
      if(cmd==='ota folder')reply='folder='+(folder?'on':'off');
      if(cmd==='ota folder on'){folder=true;reply='OK folder attached';}
      if(cmd==='ota folder off'){folder=false;reply='OK folder detached';}
      if(cmd==='clock'){const n=new Date();reply=`${n.getUTCHours()}:${String(n.getUTCMinutes()).padStart(2,'0')} - ${n.getUTCDate()}/${n.getUTCMonth()+1}/${n.getUTCFullYear()} UTC`;}
      if(cmd.startsWith('ota.peer acl ')||cmd.startsWith('ota.peer cmd.acl ')){
        job='01020304';jobResult=cmd.startsWith('ota.peer acl ')?'OK':this.remote(cmd.slice(('ota.peer cmd.acl '+target+' ').length));
        reply='OTApeer job='+job+' state=pending';
      }
      if(cmd.startsWith('ota.peer result')){
        const offset=Number(cmd.split(' ')[2]||0), data=enc.encode(jobResult), part=data.slice(offset,offset+48);
        reply=`OTApeer job=${job} state=ok bytes=${data.length} off=${offset} data=${part.length?hex(part):'-'}`;
      }
      if(!reply)throw Error('Unexpected local: '+cmd);
      queueMicrotask(()=>{this.push(enc.encode(cmd+'\r\n-> '+reply+'\r\n'));if(cmd==='ota folder on')this.push(req(2,[0]));});
    }
  }
  const port=new Port(); Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port}, configurable:true});
  const el=n=>document.querySelector('[data-role="'+n+'"]');
  const wait=async f=>{let end=Date.now()+15000;while(Date.now()<end){if(f())return;await new Promise(r=>originalTimer(r,10))}throw Error('Timed out. Status: '+el('status').textContent)};
  const report=async data=>{await fetch('/__result',{method:'POST',body:JSON.stringify(data)});};
  window.addEventListener('error',e=>report({error:e.message,cmds}));
  window.addEventListener('unhandledrejection',e=>report({error:String(e.reason),cmds}));
  window.addEventListener('load',async()=>{
    el('source').value='repeater';el('source').dispatchEvent(new Event('change'));
    try {
      const image=new Uint8Array(100), v=new DataView(image.buffer), at=44;
      image.set(enc.encode('EndF'),at);v.setUint32(at+4,at,true);
      const bh=new Uint8Array(await crypto.subtle.digest('SHA-256',image.slice(0,at))).slice(0,8);
      image.set(bh,at+8);newHash=hex(bh);v.setUint32(at+16,0x01120001,true);v.setUint32(at+20,0x12345678,true);image.set(enc.encode('board'),at+24);
      const dt=new DataTransfer();dt.items.add(new File([image],'firmware.bin'));el('file').files=dt.files;
      el('key').value=target;
      if(!el('inspect').disabled||!el('transfer').disabled||!el('install').disabled)throw Error('initial buttons not disabled');
      el('connect').click();await wait(()=>!el('inspect').disabled);
      el('inspect').click();await wait(()=>!el('transfer').disabled);
      if(!el('install').disabled)throw Error('install available before transfer');
      el('transfer').click();await wait(()=>!el('install').disabled);
      el('install').click();await wait(()=>el('status').textContent.includes('Installed and verified:'));
      const beforeDisconnect=el('status').textContent;
      el('disconnect').click();await wait(()=>!el('connect').disabled);
      await report({status:beforeDisconnect,cmds,frames:port.frames,folder,buttons:[el('inspect').disabled,el('transfer').disabled,el('install').disabled],journal:JSON.parse(localStorage.getItem('meshcore-browser-lora-ota'))});
    } catch(e){await report({error:String(e),status:el('status').textContent,cmds,frames:port.frames});}
  });
})();
</script>'''


class BrowserLoraOtaRuntimeTest(unittest.TestCase):
    def test_full_update_through_real_ui_and_fake_serial(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')
        markdown = (ROOT / 'docs/browser_lora_ota.md').read_text(encoding='utf-8')
        div = re.search(r'<div data-lora-ota>.*?</div>', markdown, re.S).group()
        scripts = re.findall(r'<script src="[^"]+"></script>', markdown)
        page = '<!doctype html><meta charset="utf-8">' + div + HARNESS + ''.join(scripts)
        done = threading.Event()
        results = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                if self.path in ('/', '/browser_lora_ota/'):
                    body = page.encode('utf-8')
                elif re.fullmatch(r'/_javascript/lora_ota_[a-z_]+\.js', self.path):
                    body = (ROOT / 'docs' / self.path.lstrip('/')).read_bytes()
                else:
                    self.send_error(404); return
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'text/html; charset=utf-8' if self.path.endswith('/') else 'application/javascript; charset=utf-8')
                self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                if self.path != '/__result': self.send_error(404); return
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'OK'); done.set()

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='meshcore-lora-browser-', ignore_cleanup_errors=True) as folder:
                command = [BROWSER, '--headless=new', '--disable-gpu', '--no-first-run',
                           '--user-data-dir='+folder, 'http://127.0.0.1:%d/browser_lora_ota/' % server.server_port]
                if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0: command.insert(1, '--no-sandbox')
                run_until_complete(command, done, timeout=30)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertNotIn('error', result, result)
        self.assertIn('Transfer complete.', result['status'])
        self.assertIn('Installed and verified: board', result['status'])
        stages = ['Checking the target and radio settings', 'Preparing the source radio',
                  'Waiting for the target to discover', 'Asking the target to begin receiving']
        positions = [result['status'].index(stage) for stage in stages]
        self.assertEqual(positions, sorted(positions), result)
        self.assertNotIn('Error:', result['status'])
        self.assertEqual(result['buttons'], [True, True, True])
        self.assertEqual(result['journal']['phase'], 'installed')
        self.assertFalse(result['folder'])
        self.assertEqual(sum(c.startswith('remote ota pull ') for c in result['cmds']), 1)
        self.assertEqual(sum(c.startswith('remote ota installhash ') for c in result['cmds']), 1)
        self.assertTrue(any(f[:4] == [109, 115, 2, 0] for f in result['frames']))
        self.assertTrue(any(f[:8] == [109, 115, 3, 0, 109, 79, 84, 65] for f in result['frames']))


if __name__ == '__main__': unittest.main()
