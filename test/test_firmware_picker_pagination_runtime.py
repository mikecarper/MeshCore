#!/usr/bin/env python3
"""Exercise the actual picker DOM with paginated release responses in Chrome."""
import http.server
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from test_compact_elegant_ota import run_until_complete
from test_webconfig_ui_runtime import BROWSER

ROOT = Path(__file__).resolve().parents[1]


class PickerPaginationRuntime(unittest.TestCase):
    def test_later_page_repeater_appears_in_picker(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chrome is required')
            self.skipTest('Chrome unavailable')
        family = 'v1.18.0.1-halo-keymind-cascade-dev-03214a16'
        previews = [{'tag_name': f'preview-{i}', 'prerelease': True} for i in range(100)]
        main = {'tag_name': family, 'name': 'Stable', 'published_at': '2026-10-24'}
        name = 'Heltec_t1_repeater_lora_ota_no_external_sensors-full-ota-' + family + '.uf2'
        chunk = {'tag_name': 'lora-ota-' + family, 'assets': [{'name': name,
                 'browser_download_url': 'https://github.com/mikecarper/MeshCore/releases/download/x/' + name}]}
        bootstrap = '''<script>
const requests=[];
const errors=[];
const pages=%s;
window.addEventListener('error', event => errors.push(String(event.message)));
window.addEventListener('unhandledrejection', event => errors.push(String(event.reason)));
window.fetch=async function(url) {
  requests.push(String(url));
  if (String(url).includes('/releases?')) {
    const first=String(url).endsWith('page=1');
    // A later page can be slower than the initial document load.
    if (!first) await new Promise(resolve=>setTimeout(resolve, 1200));
    return {ok:true,json:async()=>first?pages[0]:pages[1]};
  }
  return {ok:false,status:404,json:async()=>null};
};
window.addEventListener('DOMContentLoaded',()=>{
  const deadline=Date.now()+20000;
  function check() {
    const root=document.querySelector('[data-firmware-picker]');
    const role=root && root.querySelector('[data-field="role"]');
    const status=root && root.querySelector('[data-role="release-set"]');
    const result={requests,errors,role:role ? role.textContent : '',
      status:status ? status.textContent : ''};
    if ((requests.some(url=>url.endsWith('page=2')) && result.status.includes('Stable') &&
         result.role.includes('Repeater')) || errors.length || Date.now()>=deadline) {
      const xhr=new XMLHttpRequest(); xhr.open('POST','/result'); xhr.send(JSON.stringify(result));
    } else setTimeout(check,50);
  }
  check();
});
</script>''' % json.dumps([previews, [main, chunk]])
        html = '<!doctype html><meta charset="utf-8">' + bootstrap + (ROOT / 'docs/firmware_picker.md').read_text(encoding='utf8') + '<script src="/picker.js"></script>'
        done = threading.Event()
        results = []
        visits = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                visits.append(self.path)
                content = (ROOT/'docs/_javascript/firmware_picker.js').read_bytes() if self.path.startswith('/picker.js') else html.encode()
                self.send_response(200); self.send_header('Content-Length', str(len(content)))
                self.send_header('Content-Type', 'application/javascript' if self.path.startswith('/picker.js') else 'text/html')
                self.end_headers(); self.wfile.write(content)
            def do_POST(self):
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'OK'); done.set()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='picker-pagination-', ignore_cleanup_errors=True) as folder:
                cmd = [BROWSER, '--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir='+folder,
                       'http://127.0.0.1:%s/?s=1.hHeltec_t1' % server.server_port]
                if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0: cmd.insert(1, '--no-sandbox')
                try:
                    run_until_complete(cmd, done, timeout=60)
                except AssertionError as error:
                    raise AssertionError('Picker did not report completion; local requests: ' +
                                         repr(visits) + '\n' + str(error)) from error
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['errors'], [], results)
        self.assertTrue(any(u.endswith('page=2') for u in results[0]['requests']), results)
        self.assertIn('Stable', results[0]['status'])
        self.assertIn('Repeater', results[0]['role'])

    def test_unavailable_saved_storage_never_recommends_the_other_image(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chrome is required')
            self.skipTest('Chrome unavailable')
        controls = json.loads((ROOT/'docs/_data/firmware_controls.json').read_text())
        family = controls['familyTag']
        internal = 'WioTrackerL1-1W_repeater_lora_ota_no_external_sensors-full'
        external = 'WioTrackerL1-1W_repeater_lora_ota-full'
        targets = [internal, external, internal.replace('-full', '-reduced'), external.replace('-full', '-reduced')]
        controls['profiles'] = {key: controls['profiles'][key] for key in targets}
        del controls['profiles'][internal]['updateRequirements']['lora']['storage']
        assets = []
        for target in targets:
            name = target + '-ota-' + family + '.uf2'
            assets.append({'name': name, 'size': 1000000, 'browser_download_url':
                           'https://github.com/mikecarper/MeshCore/releases/download/' + family + '/' + name})
        release = {'tag_name': family, 'name': 'Stable', 'published_at': '2026-11-13',
                   'draft': False, 'prerelease': False, 'assets': assets}
        bootstrap = '''<script>
const release=%s, controls=%s, errors=[];
window.addEventListener('error', event => errors.push(String(event.message)));
window.addEventListener('unhandledrejection', event => errors.push(String(event.reason)));
window.fetch=async function(url) {
  const path=String(url);
  if (path.includes('/releases?')) return {ok:true,json:async()=>[release]};
  if (path.includes('firmware_controls.json')) return {ok:true,json:async()=>controls};
  return {ok:false,status:404,json:async()=>null};
};
window.addEventListener('DOMContentLoaded',()=>{
  const deadline=Date.now()+20000;
  function check() {
    const root=document.querySelector('[data-firmware-picker]');
    const status=root && root.querySelector('[data-role="status"]');
    if (status && status.textContent.includes('linked choice is unavailable')) {
      const result={errors, blockedResultHidden:root.querySelector('[data-role="result"]').hidden,
        blockedBootloaderHidden:root.querySelector('[data-role="bootloader"]').hidden,
        copyDisabled:root.querySelector('[data-action="copy-link"]').disabled,
        originalQuery:location.search, status:status.textContent,
        linkStatus:root.querySelector('[data-role="link-status"]').textContent,
        profileVisible:!root.querySelector('[data-field="firmwareProfile"]').closest('fieldset').hidden};
      const role=root.querySelector('input[data-choice-field="role"][value="repeater"]');
      if (role) role.dispatchEvent(new Event('change', {bubbles:true}));
      result.afterOtherChoiceHidden=root.querySelector('[data-role="result"]').hidden;
      const explicit=Array.from(root.querySelectorAll('input[data-choice-field="firmwareProfile"]'))
        .find(input=>input.value && !input.disabled);
      result.hasExplicitProfile=Boolean(explicit);
      result.explicitChoice=explicit && {value:explicit.value,checked:explicit.checked,hidden:explicit.hidden};
      if (explicit) explicit.click();
      result.afterProfileStatus=status.textContent;
      root.querySelector('[data-action="clear"]').click();
      result.afterClearStatus=status.textContent;
      result.afterClearResultHidden=root.querySelector('[data-role="result"]').hidden;
      result.afterClearResultTitle=root.querySelector('[data-role="result-title"]').textContent;
      const xhr=new XMLHttpRequest(); xhr.open('POST','/result'); xhr.send(JSON.stringify(result));
    } else if (errors.length || Date.now()>=deadline) {
      const xhr=new XMLHttpRequest(); xhr.open('POST','/result');
      xhr.send(JSON.stringify({errors, status:status && status.textContent}));
    } else setTimeout(check,50);
  }
  check();
});
</script>''' % (json.dumps(release), json.dumps(controls))
        html = '<!doctype html><meta charset="utf-8">' + bootstrap + (ROOT/'docs/firmware_picker.md').read_text(encoding='utf8') + '<script src="/picker.js"></script>'
        done = threading.Event()
        results = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                content = (ROOT/'docs/_javascript/firmware_picker.js').read_bytes() if self.path.startswith('/picker.js') else html.encode()
                self.send_response(200); self.send_header('Content-Length', str(len(content)))
                self.send_header('Content-Type', 'application/javascript' if self.path.startswith('/picker.js') else 'text/html')
                self.end_headers(); self.wfile.write(content)
            def do_POST(self):
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'OK'); done.set()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        query = '?s=1.hWioTrackerL1-1W.rrepeater.olora-receiver.ffull.vdefault'
        try:
            with tempfile.TemporaryDirectory(prefix='picker-old-link-', ignore_cleanup_errors=True) as folder:
                cmd = [BROWSER, '--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir='+folder,
                       'http://127.0.0.1:%s/' % server.server_port + query]
                if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0: cmd.insert(1, '--no-sandbox')
                run_until_complete(cmd, done, timeout=60)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result['errors'], [], result)
        self.assertTrue(result.get('blockedResultHidden'), result)
        self.assertTrue(result.get('blockedBootloaderHidden'), result)
        self.assertTrue(result.get('copyDisabled'), result)
        self.assertEqual(result.get('originalQuery'), query, result)
        self.assertIn('unavailable', result.get('linkStatus', ''), result)
        self.assertTrue(result.get('profileVisible'), result)
        self.assertTrue(result.get('afterOtherChoiceHidden'), result)
        self.assertTrue(result.get('hasExplicitProfile'), result)
        self.assertNotIn('linked choice is unavailable', result.get('afterProfileStatus', ''), result)
        self.assertNotIn('linked choice is unavailable', result.get('afterClearStatus', ''), result)
        self.assertNotEqual(result.get('afterClearResultTitle'), 'Recommended download', result)

    def test_only_unverified_storage_pair_shows_error_without_recommendation(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chrome is required')
            self.skipTest('Chrome unavailable')
        controls = json.loads((ROOT/'docs/_data/firmware_controls.json').read_text())
        family = controls['familyTag']
        internal = 'WioTrackerL1-1W_repeater_lora_ota_no_external_sensors-full'
        external = 'WioTrackerL1-1W_repeater_lora_ota-full'
        controls['profiles'] = {key: controls['profiles'][key] for key in (internal, external)}
        del controls['profiles'][internal]['updateRequirements']['lora']['storage']
        assets = []
        for target in (internal, external):
            name = target + '-ota-' + family + '.uf2'
            assets.append({'name': name, 'size': 1000000, 'browser_download_url':
                           'https://github.com/mikecarper/MeshCore/releases/download/' + family + '/' + name})
        release = {'tag_name': family, 'name': 'Stable', 'published_at': '2026-11-13',
                   'draft': False, 'prerelease': False, 'assets': assets}
        bootstrap = '''<script>
const release=%s, controls=%s, errors=[];
window.addEventListener('error', event => errors.push(String(event.message)));
window.addEventListener('unhandledrejection', event => errors.push(String(event.reason)));
window.fetch=async function(url) {
  const path=String(url);
  if (path.includes('/releases?')) return {ok:true,json:async()=>[release]};
  if (path.includes('firmware_controls.json')) return {ok:true,json:async()=>controls};
  return {ok:false,status:404,json:async()=>null};
};
window.addEventListener('DOMContentLoaded',()=>{
  const deadline=Date.now()+20000;
  function check() {
    const root=document.querySelector('[data-firmware-picker]');
    const status=root && root.querySelector('[data-role="status"]');
    if ((status && status.textContent.includes('storage or update metadata could not be verified')) || errors.length || Date.now()>=deadline) {
      const result={errors,status:status && status.textContent,
        resultHidden:root && root.querySelector('[data-role="result"]').hidden,
        copyDisabled:root && root.querySelector('[data-action="copy-link"]').disabled};
      const xhr=new XMLHttpRequest(); xhr.open('POST','/result'); xhr.send(JSON.stringify(result));
    } else setTimeout(check,50);
  }
  check();
});
</script>''' % (json.dumps(release), json.dumps(controls))
        html = '<!doctype html><meta charset="utf-8">' + bootstrap + (ROOT/'docs/firmware_picker.md').read_text(encoding='utf8') + '<script src="/picker.js"></script>'
        done = threading.Event()
        results = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                content = (ROOT/'docs/_javascript/firmware_picker.js').read_bytes() if self.path.startswith('/picker.js') else html.encode()
                self.send_response(200); self.send_header('Content-Length', str(len(content)))
                self.send_header('Content-Type', 'application/javascript' if self.path.startswith('/picker.js') else 'text/html')
                self.end_headers(); self.wfile.write(content)
            def do_POST(self):
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'OK'); done.set()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='picker-unverified-', ignore_cleanup_errors=True) as folder:
                cmd = [BROWSER, '--headless=new', '--disable-gpu', '--no-first-run', '--user-data-dir='+folder,
                       'http://127.0.0.1:%s/?s=1.hWioTrackerL1-1W.rrepeater.olora-receiver.ffull.vdefault' % server.server_port]
                if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0: cmd.insert(1, '--no-sandbox')
                run_until_complete(cmd, done, timeout=60)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result['errors'], [], result)
        self.assertIn('storage or update metadata could not be verified', result.get('status', ''), result)
        self.assertTrue(result.get('resultHidden'), result)
        self.assertTrue(result.get('copyDisabled'), result)


if __name__ == '__main__': unittest.main()
