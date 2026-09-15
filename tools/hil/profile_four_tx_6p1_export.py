"""Export only the user-stopped 6.1-symbol experiment, without radio I/O."""
import base64
import json
from pathlib import Path
import zlib
root=Path.home()/'hwtest/runs/four-tx-sf10-6p1-20260914'
names=('results.json','deployment.json','manifest.json','launch.json','cleanup.json','user-stop.json')
records={name:(root/name).read_text() for name in names}
print('FOUR_TX_6P1_CAPTURE '+base64.b64encode(zlib.compress(json.dumps(records).encode(),9)).decode(),flush=True)
