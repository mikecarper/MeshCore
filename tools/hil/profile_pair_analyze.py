"""Validate and summarize saved pair captures without touching any hardware."""
import hashlib
import json
from pathlib import Path
import statistics
from profile_pair import PHASES,PLAN,check_scan,check_sent,require

PREFIX=Path(__file__).with_name('profile_pair_sf8_32_')

def read(name):return json.loads(Path(str(PREFIX)+name).read_bytes())

def signals(rows):
    return {key:dict(n=len(values),mean=statistics.mean(values),min=min(values),max=max(values))
            for key in ('rssi','snr')
            if (values:=[r['received'][key] for r in rows if r['valid'] and key in r['received']])}

def rates(rows):
    return [dict(channel=ch,attempted=len(chosen),received=sum(t['valid'] for t in chosen),
                 signals=signals(chosen)) for ch in (0,1)
            if (chosen:=[t for t in rows if t['channel']==ch])]

def main(phases=PHASES,deployment_writes=3):
    output=Path(str(PREFIX)+'validated.json')
    if output.exists():raise RuntimeError('Preserve existing validation')
    export=read('export.json')
    for name,digest in export['remote_sha256'].items():
        if hashlib.sha256(Path(str(PREFIX)+name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Local capture does not match remote bytes')
    r=read('results.json')
    require(r,dict(complete=True,phase='complete',preamble=32,nominal_switch_us=600,
                   normal_rx_gain=True,samples_per_profile=100,rf_retries=0,profiles=list(PLAN)),'run incomplete')
    if r.get('error'):raise RuntimeError('Collector error')
    require(read('run-exit.json'),dict(collector_exit=0,modemmanager_restored=True),'wrapper cleanup')
    require(export['services_after'],{'ModemManager.service':'active','mctomqtt.service':'active',
        'meshcore-host-cli.service':'active','meshcore-memory-soak.service':'inactive'},'service health')
    deployment=read('deployment.json')
    if not deployment['complete'] or len(deployment['writes'])!=deployment_writes or not all(w['complete'] for w in deployment['writes']):
        raise RuntimeError('Unverified deployment')
    if deployment_writes==3:
        if read('deployment-prewrite-usb.json')['writes']:raise RuntimeError('Unexpected pre-revision firmware write')
    else:
        require(r,dict(requested_phases=[list(p) for p in phases]),'Requested follow-up case')
        if len(deployment.get('unchanged_transmitters',[]))!=2:raise RuntimeError('Missing unchanged TX verification')
        require(deployment['writes'][0],dict(board='Heltec V4',serial='44:1B:F6:69:CF:98',offset='0x10000'),'Wrong follow-up write target')
    if len(r['baseline'])!=2 or len(r['scans'])!=len(phases):raise RuntimeError('Missing phases')
    all_rows=[]
    for ch,b in enumerate(r['baseline']):
        require(b,dict(channel=ch,complete=True,received=100),'baseline incomplete')
        if len(b['trials'])!=100 or not all(t['valid'] and t['channel']==ch for t in b['trials']):
            raise RuntimeError('Stationary controls did not pass')
        require(b['status'],dict(received=100,missed=0,rf_commands=0,failures=0,device_errors=0),'baseline hardware errors')
        all_rows.extend(b['trials'])
    timing_keys=('switch','pair_switch_to_0','pair_switch_to_1','pair_idle_0','pair_idle_1','idle_cycle')
    scans=[]
    for (loop,extra),s in zip(phases,r['scans']):
        require(s,dict(complete=True,loop_us=loop,slow_extra_us=extra,dwell_us=[8397+extra,23171-loop-extra]),'scan phase')
        if len(s['trials'])!=200 or any(sum(t['channel']==ch for t in s['trials'])!=100 for ch in (0,1)):
            raise RuntimeError('Scan missing samples')
        check_scan(s['timing_before'],loop,extra,[])
        check_scan(s['status'],loop,extra,s['trials'])
        for t in s['trials']:
            if t['valid']:
                require(t['received'],dict(sf_at_read=PLAN[t['channel']]['sf'],
                    bw_khz_at_read=PLAN[t['channel']]['bw_khz'],accepted=True,payload_valid=True),'RX modulation/payload')
        all_rows.extend(s['trials'])
        scans.append(dict(loop_us=loop,slow_extra_us=extra,dwell_us=s['dwell_us'],rates=rates(s['trials']),
            timing_before={k:s['timing_before'][k] for k in timing_keys},
            timing_all={k:s['status'][k] for k in timing_keys},
            missing=[dict(sequence=t['sequence'],channel=t['channel'],received=t['received']) for t in s['trials'] if not t['valid']]))
    per_tx=100*(1+len(phases));total=2*per_tx
    if len(all_rows)!=total or len({t['sequence'] for t in all_rows})!=total:raise RuntimeError('Missing or duplicate probes')
    for ch in (0,1):
        rows=[t for t in all_rows if t['channel']==ch]
        if len(rows)!=per_tx:raise RuntimeError('TX lifetime count')
        for count,t in enumerate(rows,1):
            check_sent(t['sent'],ch,count)
            require(t['sent'],dict(sent=t['sequence']),'TX sequence')
            require(t['received'],dict(received=t['sequence']),'RX sequence')
            if t['valid']:
                require(t['received'],dict(valid=True,channel=ch,payload_channel=ch,len=16),'decoded payload')
                expected=int(PLAN[ch]['freq_khz']*(1<<25)/32000)
                if abs(t['received']['rf_word_at_read']-expected)>4:raise RuntimeError('RX frequency')
            else:
                require(t['received'],dict(valid=False,timeout=True,device_errors=0,rx_mode=1),'unexplained RF failure')
    if len(r['sender_final'])!=2:raise RuntimeError('Final TX telemetry missing')
    for row in r['sender_final']:require(row,dict(packet_count=per_tx,prepared=True,failed=False),'final TX')
    if len(r['cleanup'])!=3 or not all(c.get('idle') for c in r['cleanup']):raise RuntimeError('Cleanup incomplete')
    for c in r['cleanup']:
        if 'status' in c:require(c['status'],dict(active=False,channels=0),'RX idle')
        else:require(c['info'],dict(prepared=False,packet_count=0,failed=False),'TX idle')
    result=dict(validated=True,raw_sha256=export['remote_sha256']['results.json'],
        started_utc=r['started_utc'],finished_utc=r['finished_utc'],unique_packets=total,
        baseline=[dict(channel=b['channel'],rates=rates(b['trials'])) for b in r['baseline']],
        scans=scans,services_after=export['services_after'],all_radios_idle=True,
        transport_replays=sum(bool(t[side].get('transport_recovered')) for t in all_rows for side in ('received','sent')))
    with output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=('original','4p6'),default='original')
    args=parser.parse_args()
    if args.case=='4p6':
        PREFIX=Path(__file__).with_name('profile_pair_sf8_32_4p6_300us_')
        main(phases=((300,1024),),deployment_writes=1)
    else:main()
