"""Bounded artifact chunk receiver for the existing SSH bridge's argv limit."""
import base64
import hashlib
import json
from pathlib import Path
import struct

frame=base64.b64decode('__PAYLOAD_B64__',validate=True)
offset,total=struct.unpack_from('<II',frame)
expected=frame[8:40].hex();chunk=frame[40:]
root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
if not root.exists():root.mkdir(mode=0o700,parents=True)
if (root/'deployment.json').exists():raise RuntimeError('Deployment already exists')
archive=root/'bundle.zip'
size=archive.stat().st_size if archive.exists() else 0
if len(chunk)>32768 or offset+len(chunk)>total or total>2000000:raise ValueError('Invalid chunk bounds')
if size==offset:
    with archive.open('ab') as f:f.write(chunk)
elif size==offset+len(chunk):
    with archive.open('rb') as f:f.seek(offset);existing=f.read(len(chunk))
    if existing!=chunk:raise RuntimeError('Mismatched duplicate chunk')
else:raise RuntimeError('Out-of-order upload')
done=archive.stat().st_size==total
if done and hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise RuntimeError('Final bundle hash mismatch')
print(json.dumps({'uploaded':archive.stat().st_size,'total':total,'complete':done}),flush=True)
