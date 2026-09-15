"""Export only this finished synthetic-packet test; no radio access or writes."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import zlib

root=Path.home()/'hwtest/runs/pair-sf8-32-20260914'
names=('results.json','deployment.json','deployment-prewrite-usb.json',
       'deployment-revision.json','manifest.json','launch.json','run-control.json','run-exit.json')
if not (root/'run-exit.json').exists():raise RuntimeError('Collector still running')
files={}
for name in names:
    raw=(root/name).read_bytes()
    files[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':base64.b64encode(raw).decode()}
services={name:subprocess.run(['systemctl','is-active',name],capture_output=True,text=True,timeout=5).stdout.strip()
          for name in ('ModemManager.service','mctomqtt.service','meshcore-host-cli.service','meshcore-memory-soak.service')}
data={'files':files,'services_after':services}
print('PAIR_CAPTURE '+base64.b64encode(zlib.compress(json.dumps(data).encode(),9)).decode(),flush=True)
