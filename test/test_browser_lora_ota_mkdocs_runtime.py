#!/usr/bin/env python3
"""Navigate the built MkDocs site and exercise its updater in real Chrome."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

from test_browser_lora_ota_delta_runtime import delta_harness
from test_browser_lora_ota_runtime import BROWSER, ROOT, run_until_complete


class BrowserLoraOtaMkdocsTest(unittest.TestCase):
    def test_navigation_and_simulated_update_on_built_site(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')
        with tempfile.TemporaryDirectory(prefix='meshcore-ota-mkdocs-', ignore_cleanup_errors=True) as directory:
            site = Path(directory) / 'site'
            env = dict(os.environ, MKDOCS_SITE_URL='https://example.invalid/MeshCore/')
            result = subprocess.run([sys.executable, '-m', 'mkdocs', 'build', '--site-dir', str(site)],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            done = threading.Event()
            results, requests, failures = [], [], []
            class Handler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=str(site), **kwargs)
                def log_message(self, *_): pass
                def send_error(self, code, *args, **kwargs):
                    failures.append((self.path, code))
                    super().send_error(code, *args, **kwargs)
                def translate_path(self, path):
                    path = urlsplit(path).path
                    if path.startswith('/MeshCore/'):
                        path = path[len('/MeshCore'):]
                    return super().translate_path(path)
                def send_head(self):
                    path = urlsplit(self.path).path
                    requests.append(path)
                    if path == '/MeshCore/':
                        html = (site / 'index.html').read_text(encoding='utf-8')
                        inject = '''<script>window.addEventListener("load",()=>{
const links=[...document.querySelectorAll('a[href]')];
const item=links.find(a=>new URL(a.href).pathname.endsWith('/browser_lora_ota/'));
if(!item)throw Error('Updater navigation link missing');item.click();});</script>'''
                    elif path == '/MeshCore/browser_lora_ota/':
                        html = (site / 'browser_lora_ota/index.html').read_text(encoding='utf-8')
                        inject = delta_harness()
                    else:
                        return super().send_head()
                    data = html.replace('</head>', inject + '</head>').encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    import io
                    return io.BytesIO(data)
                def do_POST(self):
                    if self.path != '/__result': self.send_error(404); return
                    results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                    self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers()
                    self.wfile.write(b'OK'); done.set()
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with tempfile.TemporaryDirectory(prefix='meshcore-mkdocs-browser-', ignore_cleanup_errors=True) as profile:
                    command = [BROWSER, '--headless=new', '--disable-gpu', '--no-first-run',
                               # The updater and simulated target are local. Optional
                               # CDN fonts/maps must not stall the page's load event
                               # when a CI runner cannot reach the public internet.
                               '--disable-background-networking',
                               '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                               '--user-data-dir=' + profile, 'http://127.0.0.1:%d/MeshCore/' % server.server_port]
                    if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0:
                        command.insert(1, '--no-sandbox')
                    try:
                        run_until_complete(command, done, timeout=45)
                    except AssertionError as error:
                        self.fail(f'{error}\nLocal requests: {requests}\nHTTP failures: {failures}')
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=5)
            self.assertEqual(len(results), 1)
            self.assertNotIn('error', results[0], results[0])
            self.assertIn('Transfer complete.', results[0]['status'])
            self.assertIn('Installed and verified: board', results[0]['status'])
            self.assertEqual(results[0]['journal']['phase'], 'installed')
            self.assertIn('/MeshCore/browser_lora_ota/', requests)
            for name in ('lora_ota_browser.js', 'lora_ota_delta_worker.js',
                         'vendor/detools-js/bsdiff.js', 'vendor/detools-js/crle.js'):
                self.assertIn('/MeshCore/_javascript/' + name, requests)
            self.assertFalse([entry for entry in failures if '/_javascript/' in entry[0]], failures)


if __name__ == '__main__': unittest.main()
