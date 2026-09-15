"""Retrieve the finished capture through the user's already-configured bridge."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import zlib
root=Path(__file__).resolve().parents[2]
p=subprocess.run(['pwsh','-NoProfile','-File',r'C:\git\Adafruit_nRF52_Bootloader_OTAFIX\.tmp_mercermesh_bridge.ps1',
                  '-ScriptPath',str(Path(__file__).with_name('profile_four_tx_export.py')),'-DeadlineSeconds','90'],
                 capture_output=True,text=True,cwd=root,timeout=100,check=True)
lines=[line.removeprefix('FOUR_TX_CAPTURE ') for line in p.stdout.splitlines() if line.startswith('FOUR_TX_CAPTURE ')]
if len(lines)!=1 or 'MERCERMESH_OPERATION_EXIT 0' not in p.stdout:raise RuntimeError('Incomplete capture export')
data=json.loads(zlib.decompress(base64.b64decode(lines[0],validate=True)))
for name,record in data.items():
    target=root/'tools/hil'/('profile_four_tx_sf10_single_'+name)
    with target.open('x',encoding='utf8') as f:json.dump(record,f,indent=2);f.write('\n')
    print(json.dumps({'file':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}))
