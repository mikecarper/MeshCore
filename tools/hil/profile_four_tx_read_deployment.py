import json
from pathlib import Path
root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
print((root/'deployment.json').read_text(),flush=True)
