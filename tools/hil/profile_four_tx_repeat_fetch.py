"""Retrieve 7.7-symbol experiment records without changing earlier captures."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import zlib
root=Path(__file__).resolve().parents[2]
p=subprocess.run(['pwsh','-NoProfile','-File',r'C:\git\Adafruit_nRF52_Bootloader_OTAFIX\.tmp_mercermesh_bridge.ps1',
                  '-ScriptPath',str(Path(__file__).with_name('profile_four_tx_repeat_export.py')),'-DeadlineSeconds','60'],
                 capture_output=True,text=True,cwd=root,timeout=65,check=True)
prefix='FOUR_TX_7P7_CAPTURE '
lines=[line.removeprefix(prefix) for line in p.stdout.splitlines() if line.startswith(prefix)]
if len(lines)!=1 or 'MERCERMESH_OPERATION_EXIT 0' not in p.stdout:raise RuntimeError('Incomplete capture export')
records=json.loads(zlib.decompress(base64.b64decode(lines[0],validate=True)))
names={'results.json','deployment.json','manifest.json','launch.json','cleanup.json'}
if set(records)!=names:raise RuntimeError('Unexpected record names')
targets={name:root/'tools/hil'/('profile_four_tx_sf10_7p7_'+name) for name in names}
if any(path.exists() for path in targets.values()):raise RuntimeError('Preserve existing local captures')
for name,record in records.items():
    json.loads(record)
    data=record.encode('utf8')
    with targets[name].open('xb') as f:f.write(data)
    print(json.dumps({'file':str(targets[name]),'sha256':hashlib.sha256(data).hexdigest()}))
