"""Read-only progress/health snapshot; no port opens or RF operations."""
import json
from pathlib import Path
import statistics
root=Path.home()/'hwtest/runs/pair-sf8-32-20260914'
def signals(trials):
    rows=[t['received'] for t in trials if t['valid']]
    return {k:{'n':len(values),'mean':statistics.mean(values),'min':min(values),'max':max(values)}
            for k in ('rssi','snr') if (values:=[r[k] for r in rows if k in r])}
def rates(trials):
    return [{'channel':ch,'attempted':len(rows),'received':sum(t['valid'] for t in rows),'signals':signals(rows)}
            for ch in (0,1) if (rows:=[t for t in trials if t['channel']==ch])]
if (root/'deployment.json').exists():
    d=json.loads((root/'deployment.json').read_text())
    print(json.dumps({'deployment':d.get('complete'),'error':d.get('error'),
                      'writes':[{k:w.get(k) for k in ('board','complete','application_write_started')} for w in d['writes']]}),flush=True)
if (root/'results.json').exists():
    r=json.loads((root/'results.json').read_text())
    print(json.dumps({'complete':r['complete'],'phase':r['phase'],'error':r.get('error'),
      'baseline':[dict(channel=b['channel'],rates=rates(b['trials']),complete=b['complete']) for b in r['baseline']],
      'scans':[dict(loop_us=s['loop_us'],slow_extra_us=s['slow_extra_us'],dwell_us=s['dwell_us'],complete=s['complete'],
                    rates=rates(s['trials']),switch=s.get('status',s.get('timing_before',{})).get('switch'),
                    idle_cycle=s.get('status',s.get('timing_before',{})).get('idle_cycle')) for s in r['scans']],
      'cleanup':r.get('cleanup')}),flush=True)
if (root/'run-exit.json').exists():print((root/'run-exit.json').read_text(),flush=True)
if (root/'collector.log').exists():print((root/'collector.log').read_text()[-1800:],flush=True)
