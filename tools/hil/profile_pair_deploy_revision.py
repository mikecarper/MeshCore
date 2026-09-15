"""Preserve a no-write preflight failure and dispatch a reviewed USB-personality fix."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
root=Path.home()/'hwtest/runs/pair-sf8-32-20260914'
old=json.loads((root/'deployment.json').read_text())
if old.get('writes') or old.get('complete'):raise RuntimeError('Prior deployment wrote firmware; no replay')
code=base64.b64decode('__PAYLOAD_B64__',validate=True)
dest=root/'profile_pair_deploy_v2.py'
with dest.open('xb') as f:f.write(code)
(root/'deployment.json').rename(root/'deployment-prewrite-usb.json')
record={'script':dest.name,'sha256':hashlib.sha256(code).hexdigest(),'reason':'Verified CDC-only bootloader identity after reset disconnect'}
(root/'deployment-revision.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
raise SystemExit(subprocess.run(['python3','-u',str(dest)],cwd=root).returncode)
