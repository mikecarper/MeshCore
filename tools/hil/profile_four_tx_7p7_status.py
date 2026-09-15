"""Read-only progress for the 7.7-symbol repeat; never open a radio."""
import json
from pathlib import Path
root=Path.home()/'hwtest/runs/four-tx-sf10-7p7-20260914'
p=root/'results.json'
if not p.exists():
    print(json.dumps({'capture_exists':False}))
else:
    r=json.loads(p.read_text())
    print(json.dumps({'phase':r.get('phase'),'complete':r['complete'],'error':r.get('error'),
        'dwell_symbols':r['dwell_symbols'],'dwell_us':r['dwell_us'],
        'baseline':[{'channel':b['channel'],'attempted':len(b['trials']),
                     'received':sum(t['valid'] for t in b['trials'])} for b in r['baseline']],
        'scan':[{'attempted':l['attempted'],'received':l['received'],'per_channel':l['per_channel'],
                 'status':l.get('status'),'rates':l.get('error_rates')} for l in r.get('levels',[])]}),flush=True)
