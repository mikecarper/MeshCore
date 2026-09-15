"""Frame an already-built firmware bundle for the configured SSH transport."""
import hashlib
from pathlib import Path
import struct

root=Path(__file__).resolve().parents[2]/'out/profile-four-tx-20260914'
data=(root/'bundle.zip').read_bytes();digest=hashlib.sha256(data).digest()
for offset in range(0,len(data),32768):
    (root/f'chunk-{offset//32768:03d}.bin').write_bytes(struct.pack('<II',offset,len(data))+digest+data[offset:offset+32768])
print('Framed',len(data),'bytes')
