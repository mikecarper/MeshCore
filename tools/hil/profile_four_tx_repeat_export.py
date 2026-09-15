"""Export only the 7.7-symbol experiment records, without radio I/O."""
import base64
import json
from pathlib import Path
import zlib
root=Path.home()/'hwtest/runs/four-tx-sf10-7p7-20260914'
names=('results.json','deployment.json','manifest.json','launch.json','cleanup.json')
records={name:(root/name).read_text() for name in names}
print('FOUR_TX_7P7_CAPTURE '+base64.b64encode(zlib.compress(json.dumps(records).encode(),9)).decode(),flush=True)
