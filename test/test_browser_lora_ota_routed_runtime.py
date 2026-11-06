#!/usr/bin/env python3
"""Run the real browser interface through a simulated one-hop USB/RF update."""
import http.server
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest

from test_browser_lora_ota_runtime import BROWSER, HARNESS, ROOT, run_until_complete


def one_replace(text, old, new):
    if text.count(old) != 1:
        raise AssertionError('Expected exactly one harness insertion point: ' + old)
    return text.replace(old, new, 1)


def routed_harness():
    harness = one_replace(HARNESS,
        "const source='a'.repeat(64), target='b'.repeat(64), cmds=[];",
        "const source='a'.repeat(64), target='b'.repeat(64), relay='c'.repeat(64), cmds=[]; let path='off';")
    harness = one_replace(harness,
        "const config='ota config: autofetch=off autoinstall=off hops=0';",
        "const config='ota config: autofetch=off autoinstall=off hops=1';")
    harness = one_replace(harness,
        "remote(cmd){\n      cmds.push('remote '+cmd);",
        "remote(cmd,peer){\n      cmds.push('remote '+peer+' '+cmd);")
    harness = one_replace(harness,
        "if(cmd==='get radio.rxps')return 'off,1000,1000';",
        "if(cmd==='get radio.rxps')return 'off,1000,1000';\n"
        "      if(['get radio2','get tempradio2','get radio2.cross'].includes(cmd))return 'off';\n"
        "      if(cmd==='get repeat')return 'on';\n"
        "      if(cmd==='ota stats')return 'fetch idle';")
    harness = one_replace(harness,
        "'ota.peer capabilities':'OTApeer known_repeater=1 routed_path=1','ota.peer path':'OTApeer path=off',",
        "'ota.peer capabilities':'OTApeer known_repeater=1 routed_path=1','ota.peer path':'OTApeer path='+path,\n"
        "        'ota.peer radio':'OTApeer radio=radio', 'get radio2':'off', 'get tempradio2':'off', 'get radio2.cross':'off',")
    harness = one_replace(harness,
        "if(cmd==='ota folder')reply='folder='+(folder?'on':'off');",
        "if(cmd.startsWith('ota.peer path ')){path=cmd.slice('ota.peer path '.length);reply='OTApeer path='+path;}\n"
        "      if(cmd==='ota folder')reply='folder='+(folder?'on':'off');")
    harness = one_replace(harness,
        "job='01020304';jobResult=cmd.startsWith('ota.peer acl ')?'OK':this.remote(cmd.slice(('ota.peer cmd.acl '+target+' ').length));",
        """const acl=cmd.startsWith('ota.peer acl '), prefix=acl?'ota.peer acl ':'ota.peer cmd.acl ';
        const rest=cmd.slice(prefix.length), peer=rest.slice(0,64), remoteCommand=rest.slice(65);
        if(peer!==relay&&peer!==target)throw Error('unexpected destination identity');
        if(path!==(peer===relay?'off':'CCCCCC'))throw Error('wrong route for '+peer.slice(0,6)+': '+path);
        job='01020304';jobResult=acl?'OK':this.remote(remoteCommand,peer);""")
    harness = one_replace(harness,
        "el('key').value=target;",
        "el('key').value=target;el('relays').value=relay;")
    harness = one_replace(harness,
        "frames:port.frames,folder,buttons:",
        "frames:port.frames,folder,path,relay,target,buttons:")
    return harness


class BrowserLoraOtaRoutedRuntimeTest(unittest.TestCase):
    def test_one_hop_acl_transfer_install_and_route_cleanup(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')
        markdown = (ROOT / 'docs/browser_lora_ota.md').read_text(encoding='utf-8')
        div = re.search(r'<div data-lora-ota>.*?</div>', markdown, re.S).group()
        scripts = re.findall(r'<script src="[^"]+"></script>', markdown)
        page = '<!doctype html><meta charset="utf-8">' + div + routed_harness() + ''.join(scripts)
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
            with tempfile.TemporaryDirectory(prefix='meshcore-lora-routed-', ignore_cleanup_errors=True) as folder:
                command = [BROWSER, '--headless=new', '--disable-gpu', '--no-first-run',
                           '--user-data-dir='+folder, 'http://127.0.0.1:%d/browser_lora_ota/' % server.server_port]
                if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0: command.insert(1, '--no-sandbox')
                run_until_complete(command, done, timeout=45)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertNotIn('error', result, result)
        self.assertIn('Transfer complete.', result['status'])
        self.assertIn('Installed and verified: board', result['status'])
        self.assertNotIn('Error:', result['status'])
        self.assertEqual(result['buttons'], [True, True, True])
        self.assertEqual(result['journal']['phase'], 'installed')
        self.assertEqual(result['path'], 'off')
        self.assertFalse(result['folder'])
        commands, target, relay = result['cmds'], result['target'], result['relay']
        self.assertIn('local ota.peer acl ' + relay, commands)
        self.assertIn('local ota.peer acl ' + target, commands)
        self.assertIn('local ota.peer path CCCCCC', commands)
        self.assertIn('local ota.peer path off', commands)
        self.assertEqual(sum(c.startswith('remote '+target+' ota pull ') for c in commands), 1)
        self.assertEqual(sum(bool(re.fullmatch(r'remote '+target+r' ota installhash [0-9a-f]{64}', c)) for c in commands), 1)
        self.assertNotIn('remote '+target+' ota install', commands)
        self.assertTrue(any(f[:8] == [109, 115, 3, 0, 109, 79, 84, 65] for f in result['frames']))


if __name__ == '__main__': unittest.main()
