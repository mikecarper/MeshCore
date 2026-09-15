"""Copy a reviewed local deployment-script revision; keep the original bundle."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
data=base64.b64decode('__PAYLOAD_B64__',validate=True)
previous=root/'deployment.json'
if previous.exists():
    report=json.loads(previous.read_text())
    if report.get('tx') or report.get('rx',{}).get('write_started'):
        raise RuntimeError('Prior deployment reached a write; inspect before retrying')
    previous.rename(root/'deployment-preflight-v2.json')
path=root/'profile_four_tx_deploy_v3.py'
with path.open('xb') as f:f.write(data)
print(json.dumps({'script':path.name,'sha256':hashlib.sha256(data).hexdigest()}),flush=True)
raise SystemExit(subprocess.run(['python3','-u',str(path)],cwd=root).returncode)
