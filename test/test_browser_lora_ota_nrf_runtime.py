#!/usr/bin/env python3
"""Exercise in-place nRF52 browser OTA and safety gates in real Chromium."""
import http.server
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

from test_browser_lora_ota_runtime import BROWSER, ROOT, run_until_complete
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_delta_runtime import delta_harness


def nrf_harness(*, mismatch=False, no_codec=False):
    harness = delta_harness()
    harness = one_replace(harness,
        "let workerCount=0;const RealWorker=window.Worker;window.Worker=class extends RealWorker{constructor(...args){super(...args);workerCount++}};",
        "let workerCount=0,workerOptions=null;const RealWorker=window.Worker;window.Worker=class extends RealWorker{constructor(...args){super(...args);workerCount++}postMessage(msg,...rest){workerOptions={mode:msg.mode,runtimeStageCeiling:msg.runtimeStageCeiling,runtimeStorage:msg.runtimeStorage};return super.postMessage(msg,...rest)}};")
    harness = one_replace(harness,
        "'OTA hw=board | target:12345678 | maxblk:1024 | '",
        "'OTA hw=board | target:12345678 | maxblk:1024 | bl:apply | '")
    harness = one_replace(harness,
        "(installed?newHash:baseHash)+' | maxblk:1024'",
        "(installed?newHash:" + ("'0000000000000000'" if mismatch else "baseHash") + ")+' | maxblk:1024 | bootloader: apply OK (abi=3 codecs=" + ("0x1" if no_codec else "0x4") + ")'")
    harness = one_replace(harness,
        "if(cmd==='get bootloader.ver')return 'Error: unsupported';",
        "if(cmd==='get bootloader.ver')return '> 0.11.0-OTAFIX2.4.12';")
    harness = one_replace(harness,
        "image.set(enc.encode('EndF'),at);v.setUint32(at+4,at,true);",
        """image.set(enc.encode('EndF'),at);v.setUint32(at+4,at,true);
      const layout=at-24;image.set(enc.encode('mOTALay1'),layout);
      v.setUint8(layout+8,1);v.setUint8(layout+9,0);v.setUint16(layout+10,24,true);
      v.setUint32(layout+12,0x26000,true);v.setUint32(layout+16,0xED000,true);v.setUint32(layout+20,0xED000,true);""")
    harness = one_replace(harness,
        "frames:port.frames,folder,codec,total,served,workerCount,baseHash,newHash,buttons:",
        "frames:port.frames,folder,codec,total,served,workerCount,workerOptions,baseHash,newHash,buttons:")
    if mismatch or no_codec:
        harness = one_replace(harness,
            "el('inspect').click();await wait(()=>!el('transfer').disabled);",
            "el('inspect').click();await wait(()=>el('status').textContent.includes('Error:'));"
            "await report({status:el('status').textContent,workerCount,cmds,folder,transferDisabled:el('transfer').disabled});return;")
    return harness


def qspi_harness(*, bad_capacity=False, capacity_changes=False):
    harness = nrf_harness()
    harness = one_replace(harness, "maxblk:1024 | bl:apply |", "maxblk:1024 | bl:QSPI |");
    harness = one_replace(harness, "bootloader: apply OK (abi=3 codecs=0x4)",
                          "bootloader: QSPI apply OK (abi=3 codecs=0x4)")
    harness = one_replace(harness, "v.setUint8(layout+8,1);v.setUint8(layout+9,0);", "v.setUint8(layout+8,1);v.setUint8(layout+9,4);")
    harness = one_replace(harness, 'let workerCount=0,workerOptions=null;', 'let workerCount=0,workerOptions=null,qspiQueries=0;')
    harness = one_replace(harness,
        'runtimeStorage:msg.runtimeStorage};',
        'runtimeStorage:msg.runtimeStorage,runtimeQspiApply:msg.runtimeQspiApply,runtimeStorageBytes:msg.runtimeStorageBytes};')
    size = "'0'" if bad_capacity else "(qspiQueries>=3?'0':'2048')" if capacity_changes else "'2048'"
    harness = one_replace(harness,
        "if(cmd==='get bootloader.ver')return '> 0.11.0-OTAFIX2.4.12';",
        "if(cmd==='ota qspi'){qspiQueries++;return 'QSPI jedec=856015 size='+"+size+"+'K sr1=00 stage=jedec';}\n"
        "      if(cmd==='get bootloader.ver')return '> 0.11.0-OTAFIX2.4.12';")
    if bad_capacity:
        harness = one_replace(harness,
            "el('inspect').click();await wait(()=>!el('transfer').disabled);",
            "el('inspect').click();await wait(()=>el('status').textContent.includes('Error:'));"
            "await report({status:el('status').textContent,workerCount,cmds,folder,qspiQueries,transferDisabled:el('transfer').disabled});return;")
    if capacity_changes:
        harness = one_replace(harness,
            "el('transfer').click();await wait(()=>!el('install').disabled);",
            "el('transfer').click();await wait(()=>el('status').textContent.includes('Error:'));"
            "await report({status:el('status').textContent,workerCount,workerOptions,cmds,folder,qspiQueries,installDisabled:el('install').disabled});return;")
    return harness


def changed_internal_harness(kind):
    """Change live target metadata after the first successful preflight."""
    harness = nrf_harness()
    harness = one_replace(harness, 'let workerCount=0,workerOptions=null;',
                          'let workerCount=0,workerOptions=null,selfQueries=0;')
    original = ("if(cmd==='ota self') return 'self body=44 image=100 base_hash='"
                "+(installed?newHash:baseHash)+' | maxblk:1024 | "
                "bootloader: apply OK (abi=3 codecs=0x4)';")
    if kind == 'invalid_stage':
        tail = "' | maxblk:1024 | bootloader: apply OK (abi=3 codecs=0x4 stage=dead0)'"
        body = '(installed?newHash:baseHash)+' + tail
    elif kind == 'changed_stage':
        tail = "' | maxblk:1024 | bootloader: apply OK (abi=3 codecs=0x4 stage='+(selfQueries>=3?'dead0':'d4000')+')'"
        body = '(installed?newHash:baseHash)+' + tail
    elif kind == 'changed_base':
        tail = "' | maxblk:1024 | bootloader: apply OK (abi=3 codecs=0x4)'"
        body = "(installed?newHash:(selfQueries>=3?'0000000000000000':baseHash))+" + tail
    else:
        raise ValueError(kind)
    harness = one_replace(harness, original,
        "if(cmd==='ota self'){selfQueries++;return 'self body=44 image=100 base_hash='+" + body + ';}')
    if kind == 'invalid_stage':
        harness = one_replace(harness,
            "el('inspect').click();await wait(()=>!el('transfer').disabled);",
            "el('inspect').click();await wait(()=>el('status').textContent.includes('Error:'));"
            "await report({status:el('status').textContent,workerCount,cmds,folder,selfQueries,transferDisabled:el('transfer').disabled});return;")
    else:
        harness = one_replace(harness,
            "el('transfer').click();await wait(()=>!el('install').disabled);",
            "el('transfer').click();await wait(()=>el('status').textContent.includes('Error:'));"
            "await report({status:el('status').textContent,workerCount,cmds,folder,selfQueries,installDisabled:el('install').disabled});return;")
    return harness


def run_harness(harness, timeout=45):
    markdown = (ROOT / 'docs/browser_lora_ota.md').read_text(encoding='utf-8')
    div = re.search(r'<div data-lora-ota>.*?</div>', markdown, re.S).group()
    scripts = re.findall(r'<script src="[^"]+"></script>', markdown)
    page = '<!doctype html><meta charset="utf-8">' + div + harness + ''.join(scripts)
    done = threading.Event()
    results, missing_scripts = [], []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ('/', '/browser_lora_ota/'):
                body = page.encode('utf-8')
            elif (re.fullmatch(r'/_javascript/lora_ota_[a-z_]+\.js', path) or re.fullmatch(
                    r'/_javascript/vendor/detools-js/[a-z-]+\.js', path) or
                    re.fullmatch(r'/_javascript/otafix_sim/worker\.js', path) or
                    re.fullmatch(r'/_javascript/otafix_sim/build/otafix-2\.4\.(?:3|6|9|11|12)-rak(?:3401|4631)-internal\.wasm', path)):
                body = (ROOT / 'docs' / path.lstrip('/')).read_bytes()
            else:
                if path.startswith('/_javascript/') and path.endswith('.js'):
                    missing_scripts.append(self.path)
                self.send_error(404); return
            self.send_response(200); self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', 'text/html; charset=utf-8' if path.endswith('/') else
                             'application/wasm' if path.endswith('.wasm') else 'application/javascript; charset=utf-8')
            self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            if self.path != '/__result': self.send_error(404); return
            results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'OK'); done.set()

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='meshcore-lora-nrf-', ignore_cleanup_errors=True) as folder:
            command = [BROWSER, '--headless=new', '--disable-gpu', '--no-first-run',
                       '--user-data-dir='+folder, 'http://127.0.0.1:%d/browser_lora_ota/' % server.server_port]
            if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0: command.insert(1, '--no-sandbox')
            run_until_complete(command, done, timeout=timeout)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    if missing_scripts: raise AssertionError(f'Browser scripts did not load: {missing_scripts}')
    if len(results) != 1: raise AssertionError('Expected one browser result')
    return results[0]


class BrowserLoraOtaNrfRuntimeTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')

    def test_in_place_delta_with_legacy_stage_fallback(self):
        result = run_harness(nrf_harness())
        self.assertNotIn('error', result, result)
        self.assertNotIn('Error:', result['status'])
        self.assertIn('Transfer complete.', result['status'])
        self.assertIn('Installed and verified: board', result['status'])
        self.assertEqual(result['journal']['phase'], 'installed')
        self.assertEqual(result['journal']['bodyHash'], result['newHash'])
        self.assertEqual(result['codec'], 2)
        self.assertEqual(result['served'][64], 2)
        self.assertEqual(bytes(result['served'][97:105]).hex(), result['baseHash'])
        self.assertEqual(result['total'], len(result['served']))
        self.assertEqual(result['workerCount'], 1)
        self.assertEqual(result['workerOptions'], {'mode':'in-place', 'runtimeStageCeiling':0xD4000, 'runtimeStorage':'internal'})
        # ota self reports the bootloader's live ABI and supported codecs;
        # the negative capability test below verifies those are enforced.
        self.assertGreaterEqual(result['cmds'].count('remote ota self'), 2)
        self.assertEqual(sum(bool(re.fullmatch(r'remote ota installhash [0-9a-f]{64}', c)) for c in result['cmds']), 1)
        self.assertNotIn('remote ota install', result['cmds'])
        self.assertFalse(result['folder'])

    def test_wrong_running_hash_blocks_worker_and_transfer(self):
        result = run_harness(nrf_harness(mismatch=True))
        self.assertNotIn('error', result, result)
        self.assertIn('does not exactly match the target', result['status'])
        self.assertEqual(result['workerCount'], 0)
        self.assertTrue(result['transferDisabled'])
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))
        self.assertFalse(any(c.startswith('remote ota installhash ') or c == 'remote ota install' for c in result['cmds']))

    def test_missing_in_place_capability_blocks_worker_and_transfer(self):
        result = run_harness(nrf_harness(no_codec=True))
        self.assertNotIn('error', result, result)
        self.assertIn('in-place', result['status'])
        self.assertEqual(result['workerCount'], 0)
        self.assertTrue(result['transferDisabled'])
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))
        self.assertFalse(any(c.startswith('remote ota installhash ') or c == 'remote ota install' for c in result['cmds']))

    def test_unsupported_internal_stage_blocks_generation(self):
        result = run_harness(changed_internal_harness('invalid_stage'))
        self.assertNotIn('error', result, result)
        self.assertIn('Unsupported nRF52 staging ceiling', result['status'])
        self.assertEqual(result['workerCount'], 0)
        self.assertTrue(result['transferDisabled'])
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))

    def test_internal_stage_change_blocks_transfer_after_generation(self):
        result = run_harness(changed_internal_harness('changed_stage'))
        self.assertNotIn('error', result, result)
        self.assertIn('Unsupported nRF52 staging ceiling', result['status'])
        self.assertEqual(result['workerCount'], 1)
        self.assertGreaterEqual(result['selfQueries'], 3)
        self.assertTrue(result['installDisabled'])
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))

    def test_changed_base_blocks_transfer_after_generation(self):
        result = run_harness(changed_internal_harness('changed_base'))
        self.assertNotIn('error', result, result)
        self.assertIn('does not match the exact installed firmware', result['status'])
        self.assertEqual(result['workerCount'], 1)
        self.assertGreaterEqual(result['selfQueries'], 3)
        self.assertTrue(result['installDisabled'])
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))

    def test_qspi_in_place_delta_through_browser(self):
        result = run_harness(qspi_harness())
        self.assertNotIn('error', result, result)
        self.assertNotIn('Error:', result['status'])
        self.assertIn('Transfer complete.', result['status'])
        self.assertIn('Installed and verified: board', result['status'])
        self.assertEqual(result['codec'], 2)
        self.assertEqual(result['served'][64], 2)
        self.assertEqual(result['workerCount'], 1)
        self.assertEqual(result['workerOptions'], {'mode':'in-place', 'runtimeStorage':'qspi',
                                                  'runtimeQspiApply':True, 'runtimeStorageBytes':2048*1024})
        self.assertGreaterEqual(result['cmds'].count('remote ota qspi'), 3)
        self.assertEqual(sum(bool(re.fullmatch(r'remote ota installhash [0-9a-f]{64}', c)) for c in result['cmds']), 1)
        self.assertNotIn('remote ota install', result['cmds'])
        self.assertFalse(result['folder'])

    def test_qspi_unknown_capacity_blocks_generation_and_sending(self):
        result = run_harness(qspi_harness(bad_capacity=True))
        self.assertNotIn('error', result, result)
        self.assertIn('QSPI capacity is unavailable', result['status'])
        self.assertEqual(result['workerCount'], 0)
        self.assertTrue(result['transferDisabled'])
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))

    def test_qspi_capacity_change_blocks_sending_after_generation(self):
        result = run_harness(qspi_harness(capacity_changes=True))
        self.assertNotIn('error', result, result)
        self.assertIn('QSPI capacity is unavailable', result['status'])
        self.assertEqual(result['workerCount'], 1)
        self.assertGreaterEqual(result['qspiQueries'], 3)
        self.assertTrue(result['installDisabled'])
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))
        self.assertFalse(any(c.startswith('remote ota installhash ') or c == 'remote ota install' for c in result['cmds']))


if __name__ == '__main__': unittest.main()
