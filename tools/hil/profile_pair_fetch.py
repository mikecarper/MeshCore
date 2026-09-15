"""Preserve exact remote capture bytes and hashes through the configured bridge."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import zlib

root=Path(__file__).resolve().parents[2]
prefix=root/'tools/hil/profile_pair_sf8_32_'
proc=subprocess.run(['pwsh','-NoProfile','-File',r'C:\git\Adafruit_nRF52_Bootloader_OTAFIX\.tmp_mercermesh_bridge.ps1',
    '-ScriptPath',str(Path(__file__).with_name('profile_pair_export.py')),'-DeadlineSeconds','45'],
    capture_output=True,text=True,cwd=root,timeout=55,check=True)
lines=[line.removeprefix('PAIR_CAPTURE ') for line in proc.stdout.splitlines() if line.startswith('PAIR_CAPTURE ')]
if len(lines)!=1 or 'MERCERMESH_OPERATION_EXIT 0' not in proc.stdout:raise RuntimeError('Incomplete capture export')
data=json.loads(zlib.decompress(base64.b64decode(lines[0],validate=True)))
expected={'results.json','deployment.json','deployment-prewrite-usb.json','deployment-revision.json',
          'manifest.json','launch.json','run-control.json','run-exit.json'}
if set(data['files'])!=expected:raise RuntimeError('Unexpected capture file set')
decoded={}
for name,record in data['files'].items():
    raw=base64.b64decode(record['bytes'],validate=True)
    if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise RuntimeError('Capture hash mismatch')
    json.loads(raw)
    decoded[Path(str(prefix)+name)]=raw
decoded[Path(str(prefix)+'export.json')]=(json.dumps({'services_after':data['services_after'],
    'remote_sha256':{n:r['sha256'] for n,r in data['files'].items()}},indent=2)+'\n').encode()
if any(p.exists() for p in decoded):raise RuntimeError('Preserve existing capture; refusing overwrite')
for path,raw in decoded.items():
    with path.open('xb') as stream:stream.write(raw)
    print(json.dumps({'file':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}))
