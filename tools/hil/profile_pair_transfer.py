"""Receive bounded chunks through the already configured SSH bridge."""
import base64
import hashlib
import json
from pathlib import Path
import struct
frame=base64.b64decode('__PAYLOAD_B64__',validate=True)
offset,total=struct.unpack_from('<II',frame);digest=frame[8:40].hex();chunk=frame[40:]
root=Path.home()/'hwtest/runs/pair-sf8-32-20260914'
root.mkdir(mode=0o700,parents=True,exist_ok=True)
if (root/'manifest.json').exists():raise RuntimeError('Already unpacked')
archive=root/'bundle.zip';size=archive.stat().st_size if archive.exists() else 0
if len(chunk)>32768 or offset+len(chunk)>total or total>2000000:raise RuntimeError('Chunk bounds')
if size==offset:
    with archive.open('ab') as f:f.write(chunk)
elif size==offset+len(chunk):
    with archive.open('rb') as f:f.seek(offset);prior=f.read(len(chunk))
    if prior!=chunk:raise RuntimeError('Mismatched duplicate chunk')
else:raise RuntimeError('Out of order chunk')
if archive.stat().st_size==total and hashlib.sha256(archive.read_bytes()).hexdigest()!=digest:
    raise RuntimeError('Wrong complete bundle hash')
print(json.dumps({'uploaded':archive.stat().st_size,'total':total,'complete':archive.stat().st_size==total}),flush=True)
