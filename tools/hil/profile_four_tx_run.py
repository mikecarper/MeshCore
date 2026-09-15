"""Run the current reviewed collector through the configured MercerMesh bridge."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import signal

root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
code=base64.b64decode('__PAYLOAD_B64__',validate=True)
path=root/'profile_four_tx_v2.py'
with path.open('xb') as f:f.write(code)
print(json.dumps({'collector':path.name,'sha256':hashlib.sha256(code).hexdigest()}),flush=True)
mm=subprocess.run(['systemctl','is-active','--quiet','ModemManager.service']).returncode==0
child=None
try:
    if mm:subprocess.run(['sudo','-n','systemctl','stop','ModemManager.service'],check=True)
    child=subprocess.Popen(['python3','-u',str(path)],cwd=root)
    (root/'run-control.json').write_text(json.dumps({'pid':child.pid,'collector':path.name,'modemmanager_before':mm}))
    rc=child.wait(timeout=6000)
finally:
    if child is not None and child.poll() is None:
        child.send_signal(signal.SIGINT) # Let the collector close/reboot its exact ports.
        try:child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
    if mm:subprocess.run(['sudo','-n','systemctl','start','ModemManager.service'],check=True)
raise SystemExit(rc)
