#!/usr/bin/env python3
"""Connect the real UI over HTTPS to the real loopback bridge and a fake TCP radio."""
import asyncio
from datetime import datetime, timedelta, timezone
import http.server
import importlib.util
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

from test_browser_lora_ota_runtime import BROWSER, ROOT, run_until_complete


def framed(body):
    return b'>' + len(body).to_bytes(2, 'little') + body


class WifiHttpsBridgeTest(unittest.TestCase):
    def check_secure_page(self, production_origin):
        if not BROWSER:
            self.skipTest('Chromium unavailable')
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
            from websockets.asyncio.server import serve
        except ImportError:
            self.skipTest('cryptography and websockets >= 13 are required')
        module_path = ROOT / 'tools/lora_ota/browser_wifi_bridge.py'
        spec = importlib.util.spec_from_file_location('browser_wifi_bridge_test', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        done, started, stop = threading.Event(), threading.Event(), threading.Event()
        results, errors, requests = [], [], []
        token = 'testcapability0123456789abcdef'
        bridge_url = 'ws://127.0.0.1:8765/' + token
        markdown = (ROOT / 'docs/browser_lora_ota.md').read_text(encoding='utf-8')
        div = re.search(r'<div data-lora-ota>.*?</div>', markdown, re.S).group()
        scripts = ''.join(re.findall(r'<script src="[^"]+"></script>', markdown))
        harness = '''<script>
          const el=n=>document.querySelector('[data-role="'+n+'"]');
          async function report(value){await fetch('/__result',{method:'POST',body:JSON.stringify(value)});}
          window.addEventListener('error',e=>report({error:e.message}));
          window.addEventListener('unhandledrejection',e=>report({error:String(e.reason)}));
          window.addEventListener('load',async()=>{try{
            el('connection').value='wifi';el('connection').dispatchEvent(new Event('change'));
            el('wifi-url').value=BRIDGE_URL;el('connect').click();
            const until=Date.now()+25000;
            while(Date.now()<until && el('source-info').hidden && !el('status').textContent.includes('Error:'))
              await new Promise(r=>setTimeout(r,25));
            const connected=!el('source-info').hidden;
            const text=el('workflow-status').textContent+' '+el('contact-status').textContent+' '+el('status').textContent;
            await report({connected,text,inspectDisabled:el('inspect').disabled});
          }catch(e){await report({error:String(e)});}});
        </script>'''.replace('BRIDGE_URL', json.dumps(bridge_url))
        page = '<!doctype html><meta charset="utf-8">' + div + harness + scripts

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                path = urlsplit(self.path).path
                if path in ('/', '/browser_lora_ota/'):
                    body = page.encode()
                elif re.fullmatch(r'/_javascript/[a-z_]+\.js', path):
                    target = ROOT / 'docs' / path.lstrip('/')
                    if not target.is_file(): self.send_error(404); return
                    body = target.read_bytes()
                else:
                    self.send_error(404); return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8' if path.endswith('/') else 'application/javascript')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                results.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'OK')
                done.set()

        async def radio(reader, writer):
            try:
                while True:
                    header = await reader.readexactly(3)
                    if header[0] != 60: raise ValueError('bad frame')
                    command = await reader.readexactly(int.from_bytes(header[1:], 'little'))
                    requests.append(command[0])
                    if command[0] == 1:
                        body = bytearray(58); body[0] = 5; body[4:36] = bytes([0xaa])*32
                        body[48:52] = (910525).to_bytes(4, 'little'); body[52:56] = (62500).to_bytes(4, 'little')
                        body[56:58] = bytes((7,5)); writer.write(framed(body))
                    elif command[0] == 22:
                        body = bytearray(82); body[0] = 13; body[1] = 13
                        body[20:25] = b'W12 X'; body[60:69] = b'v1.18.0.2'; writer.write(framed(body))
                    elif command[0] == 66:
                        value = command[1:].decode()
                        response = {'get ota.direct':'> ota.direct v=2 active=0 busy=0',
                                    'get ota.direct.capabilities':'> ota.direct.cap guarded_cli=1 guarded_radio=1 guarded_wifi=1',
                                    'get usb.raw.max':'> 188', 'ver':'v1.18.0.2-test',
                                    'get tempradio':'TempRadio active: 910.525,62.5,7,5,120',
                                    'get radio':'> 910.525,62.5,7,5'}.get(value)
                        if response is None: raise ValueError('unexpected local command: '+value)
                        writer.write(framed(b'\x1d'+response.encode()))
                    elif command[0] == 4:
                        writer.write(framed(bytes((2,0,0,0,0))) + framed(bytes((4,0,0,0,0))))
                    else: raise ValueError('unexpected command')
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            except Exception as exc:
                errors.append(str(exc))
            finally:
                writer.close(); await writer.wait_closed()

        with tempfile.TemporaryDirectory(prefix='meshcore-wifi-https-', ignore_cleanup_errors=True) as folder:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
            now = datetime.now(timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
                    .not_valid_after(now+timedelta(days=1)).sign(key, hashes.SHA256()))
            cert_path, key_path = Path(folder)/'cert.pem', Path(folder)/'key.pem'
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
            class SecureServer(http.server.ThreadingHTTPServer):
                # Chromium preloads many script tags concurrently; do not let
                # the default tiny accept queue drop dependencies on startup.
                request_queue_size = 64
                daemon_threads = True
            server = SecureServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert_path, key_path)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            origin = module.ORIGIN if production_origin else 'https://127.0.0.1:' + str(server.server_port)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()

            def run_bridge():
                async def main():
                    try:
                        async with await asyncio.start_server(radio, '127.0.0.1', 0) as radio_server:
                            port = radio_server.sockets[0].getsockname()[1]
                            bridge = module.Bridge('127.0.0.1', port, token)
                            async with serve(bridge.handle, '127.0.0.1', 8765, origins=[origin], compression=None):
                                started.set()
                                while not stop.is_set(): await asyncio.sleep(0.05)
                    except Exception as exc:
                        errors.append(str(exc)); started.set()
                asyncio.run(main())
            bridge_thread = threading.Thread(target=run_bridge, daemon=True); bridge_thread.start()
            try:
                self.assertTrue(started.wait(5)); self.assertFalse(errors, errors)
                command = [BROWSER, '--headless=new', '--disable-gpu', '--ignore-certificate-errors',
                           '--no-first-run', '--no-proxy-server', '--user-data-dir='+str(Path(folder)/'profile')]
                if production_origin:
                    command.append('--host-resolver-rules=MAP mikecarper.github.io 127.0.0.1:'+str(server.server_port))
                command.append(origin+'/browser_lora_ota/')
                if os.name != 'nt' and hasattr(os, 'geteuid') and os.geteuid() == 0: command.insert(1, '--no-sandbox')
                run_until_complete(command, done, timeout=40)
            finally:
                stop.set(); bridge_thread.join(5); server.shutdown(); server.server_close(); thread.join(5)
        self.assertFalse(errors, errors)
        self.assertEqual(len(results), 1, results)
        self.assertNotIn('error', results[0], results[0])
        self.assertTrue(results[0]['connected'], results[0])
        self.assertNotIn(token, results[0]['text'])
        self.assertIn(1, requests); self.assertIn(4, requests)

    def test_secure_local_page_connects_to_local_bridge(self):
        self.check_secure_page(False)

    def test_production_origin_connects_to_local_bridge(self):
        self.check_secure_page(True)


if __name__ == '__main__':
    unittest.main()
