"""Receive the locally built, hash-manifested bundle through the configured bridge."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
payload=(root/'bundle.zip').read_bytes()
if hashlib.sha256(payload).hexdigest()!='fad0bf8b76412e3ee362bc6632ff1e8e7bbf2746e6f51d7e3d69a8f0f95dfed0':
    raise RuntimeError('Wrong assembled bundle')
if (root/'manifest.json').exists():raise RuntimeError('Bundle already unpacked')
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    manifest=json.loads(z.read('manifest.json'))
    if set(z.namelist())!=set(manifest['files'])|{'manifest.json'}:raise RuntimeError('Unexpected bundle entries')
    for name in z.namelist():
        if Path(name).name!=name or name in ('.','..'):raise RuntimeError('Unsafe bundle entry')
        b=z.read(name)
        if name!='manifest.json' and hashlib.sha256(b).hexdigest()!=manifest['files'][name]['sha256']:
            raise RuntimeError('Bundle hash mismatch')
        (root/name).write_bytes(b)
print(json.dumps({'bundle_sha256':hashlib.sha256(payload).hexdigest(),'directory':str(root)}),flush=True)
p=subprocess.run(['python3','-u',str(root/'profile_four_tx_deploy.py')],cwd=root)
raise SystemExit(p.returncode)
