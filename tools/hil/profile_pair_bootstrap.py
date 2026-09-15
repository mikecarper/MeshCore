"""Unpack a hash-checked pair bundle; deploy only after the separate review step."""
import base64
import hashlib
import json
from pathlib import Path
import zipfile
root=Path.home()/'hwtest/runs/pair-sf8-32-20260914'
expected=base64.b64decode('__PAYLOAD_B64__',validate=True).decode().strip()
if hashlib.sha256((root/'bundle.zip').read_bytes()).hexdigest()!=expected:raise RuntimeError('Wrong bundle')
if (root/'manifest.json').exists():raise RuntimeError('Already unpacked')
with zipfile.ZipFile(root/'bundle.zip') as z:
    manifest=json.loads(z.read('manifest.json'))
    if set(z.namelist())!=set(manifest['files'])|{'manifest.json'}:raise RuntimeError('Bundle entry set')
    for name in z.namelist():
        if Path(name).name!=name or name in ('.','..'):raise RuntimeError('Unsafe entry')
        data=z.read(name)
        if name!='manifest.json' and hashlib.sha256(data).hexdigest()!=manifest['files'][name]['sha256']:
            raise RuntimeError('Artifact hash mismatch')
        with (root/name).open('xb') as f:f.write(data)
print(json.dumps({'unpacked':str(root),'sha256':expected}),flush=True)
