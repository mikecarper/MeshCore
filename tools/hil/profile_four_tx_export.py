"""Return only this bench's synthetic-packet capture and deployment records."""
import base64
import json
from pathlib import Path
import zlib
root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
data={name:json.loads((root/name).read_text()) for name in ('results.json','deployment.json','manifest.json','cleanup.json')}
print('FOUR_TX_CAPTURE '+base64.b64encode(zlib.compress(json.dumps(data).encode(),9)).decode(),flush=True)
