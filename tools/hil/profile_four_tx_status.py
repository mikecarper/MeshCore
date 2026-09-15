"""Compact read-only snapshot of the current MercerMesh packet experiment."""
import json
from pathlib import Path
import statistics
root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
p=root/'results.json'
if not p.exists():
    print(json.dumps({'capture_exists':False}),flush=True)
else:
    r=json.loads(p.read_text())
    def signals(trials):
        packets=[t['received'] for t in trials if t['received'].get('valid') or t['received'].get('payload_valid')]
        return {k:dict(n=len(values),mean=statistics.mean(values),min=min(values),max=max(values))
                for k in ('rssi','snr') if (values:=[p[k] for p in packets if k in p])}
    def rates(trials):
        n=len(trials);strict=sum(t['valid'] for t in trials)
        payload=sum(t['valid'] or t['received'].get('payload_valid') is True for t in trials)
        return dict(attempted=n,strict=strict,payload=payload,offchannel=payload-strict,
                    timeouts=sum(t['received'].get('timeout') is True for t in trials))
    print(json.dumps({'phase':r.get('phase'),'complete':r['complete'],'error':r.get('error'),
          'baseline':[dict(channel=b['channel'],attempted=len(b['trials']),
                           received=sum(t['valid'] for t in b['trials']),signals=signals(b['trials'])) for b in r['baseline']],
          'scan':[dict(attempted=l['attempted'],received=l['received'],per_channel=l['per_channel'],
                       status=l.get('status'),rates=l.get('error_rates') or rates(l['trials'])) for l in r.get('levels',[])]}),flush=True)
