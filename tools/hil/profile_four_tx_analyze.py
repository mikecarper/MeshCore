"""Summarize the immutable full-sample capture; never connect to radios."""
import argparse
import json
import ast
import hashlib
from pathlib import Path
import statistics
from profile_four_tx import check_scan,check_sent
from profile_four_tx_fixture import TXS
from profile_switch_channels import packet_error_summary,channel_dwell_us

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input',type=Path,default=Path(__file__).with_name('profile_four_tx_sf10_single_results.json'))
parser.add_argument('--output',type=Path)
args=parser.parse_args()
p=args.input
output=args.output or p.with_name(p.stem.removesuffix('_results')+'_validated.json')
if output.exists():parser.error('Output already exists; preserve previous validation')
r=json.loads(p.read_text())
level=r['levels'][0]
trials=level['trials']
if (r['sf']!=10 or r['bw_khz']!=125 or r['preamble_symbols']!=32
        or r['dwell_us']!=channel_dwell_us(10,r['dwell_symbols'])
        or level['config']['dwell_us']!=r['dwell_us']):
    raise RuntimeError('Requested and acknowledged radio/dwell settings disagree')
if len(trials)!=400 or any(c['attempted']!=100 for c in level['per_channel']):
    raise RuntimeError('Not a complete 400-packet capture')
if len({t['sequence'] for t in trials})!=400:raise RuntimeError('Duplicate probe sequence')
recovery=None
status=level.get('status')
if status is None:
    prefix='Actual RX command counts/policies/counts disagree: '
    if not r.get('error','').startswith(prefix):raise RuntimeError('No complete final telemetry')
    status=ast.literal_eval(r['error'][len(prefix):])
    recovery='Final status preserved verbatim in original collector error; zero-modulation assertion was overly strict for post-packet RX staging.'
check_scan(status,400,sum(t['valid'] for t in trials))
all_trials=[t for b in r['baseline'] for t in b['trials']]+trials
if len(all_trials)!=800 or len({t['sequence'] for t in all_trials})!=800:raise RuntimeError('Incomplete/duplicate baseline or scan probes')
for ch,target in enumerate(TXS):
    selected=[t for t in all_trials if t['channel']==ch]
    if len(selected)!=200:raise RuntimeError('Missing transmitter probes')
    for count,trial in enumerate(selected,1):
        check_sent(trial['sent'],target,count)
        if trial['sent']['sent']!=trial['sequence'] or trial['received']['received']!=trial['sequence']:
            raise RuntimeError('Response sequence mismatch')
        if trial['valid'] and (trial['received']['channel']!=ch or trial['received']['payload_channel']!=ch
                               or trial['received']['len']!=16):
            raise RuntimeError('Accepted payload mismatch')
for b in r['baseline']:
    if len(b['trials'])!=100 or not b['complete'] or b['received']!=100:
        raise RuntimeError('Stationary control did not fully pass')
if r['complete']:
    if (r.get('phase')!='complete' or len(r.get('sender_final',[]))!=4
            or any(i.get('packet_count')!=200 or not i.get('prepared') or i.get('failed')
                   for i in r['sender_final'])):
        raise RuntimeError('Final transmitter lifetime checks missing or mismatched')

def signals(ts):
    rows=[t['received'] for t in ts if t['received'].get('valid') or t['received'].get('payload_valid')]
    return {k:dict(n=len(v),mean=statistics.mean(v),min=min(v),max=max(v))
            for k in ('rssi','snr') if (v:=[x[k] for x in rows if k in x])}

summary=dict(sampling_complete=True,post_run_validation_passed=True,original_collector_complete=r['complete'],
             raw_capture_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),validation_note=recovery,
             started_utc=r['started_utc'],finished_utc=r.get('finished_utc'),
             dwell_symbols=r['dwell_symbols'],dwell_us=r['dwell_us'],rates=packet_error_summary(trials,4),
             status=status,baseline=[dict(channel=b['channel'],received=b['received'],
                signals=signals(b['trials']),status=b['status']) for b in r['baseline']],
             per_tx=[dict(channel=ch,signals=signals([t for t in trials if t['channel']==ch])) for ch in range(4)],
             missing=[dict(attempt=i+1,round=t['sample_round'],channel=t['channel'],received=t['received'],
                           pause_ms=t['pause_ms'],tx=t['sent']) for i,t in enumerate(trials) if not t['valid']],
             unique_tx_sequences=len({t['sequence'] for b in r['baseline'] for t in b['trials']}|{t['sequence'] for t in trials}),
             tx_configuration_commands_during_packets=sum(t['sent']['rf_commands']+t['sent']['modulation_commands']
                              for t in trials+[t for b in r['baseline'] for t in b['trials']]),
             transport_replays=sum(bool(t['received'].get('transport_recovered'))+bool(t['sent'].get('transport_recovered'))
                              for t in trials+[t for b in r['baseline'] for t in b['trials']]))
with output.open('x') as f:json.dump(summary,f,indent=2);f.write('\n')
print(json.dumps(summary,indent=2))
