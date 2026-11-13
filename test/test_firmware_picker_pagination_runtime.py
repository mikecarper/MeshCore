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


if __name__ == '__main__': unittest.main()
