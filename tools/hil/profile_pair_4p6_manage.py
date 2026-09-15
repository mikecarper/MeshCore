"""Package or operate the bounded 4.6/300-us follow-up through the configured bridge."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile
import zlib

root=Path(__file__).resolve().parents[2]
dest=root/'out/pair-sf8-32-4p6-300us-20260914'
prefix=root/'tools/hil/profile_pair_sf8_32_4p6_300us_'
parser=argparse.ArgumentParser()
parser.add_argument('action',choices=('prepare','deploy','launch','status','fetch'))
args=parser.parse_args()

def remote(request,deadline=45):
    with tempfile.TemporaryDirectory(prefix='pair-4p6-') as folder:
        payload=Path(folder)/'request.json';payload.write_text(json.dumps(request))
        proc=subprocess.run(['pwsh','-NoProfile','-File',r'C:\git\Adafruit_nRF52_Bootloader_OTAFIX\.tmp_mercermesh_bridge.ps1',
            '-ScriptPath',str(root/'tools/hil/profile_pair_4p6_remote.py'),'-PayloadPath',str(payload),
            '-DeadlineSeconds',str(deadline)],capture_output=True,text=True,cwd=root,timeout=deadline+15)
    if proc.returncode or 'MERCERMESH_OPERATION_EXIT 0' not in proc.stdout:
        print(proc.stdout,flush=True);print(proc.stderr,flush=True);raise RuntimeError('Bridge operation failed')
    return proc.stdout

if args.action=='prepare':
    if dest.exists():raise RuntimeError('Bundle exists; preserve prior artifacts')
    dest.mkdir(parents=True)
    files={};manifest={'scope':'V4 application-only 4.6 chirps / 300 us reserve; TX images unchanged',
        'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'files':{},
        'prior_tx_manifest_sha256':hashlib.sha256((root/'tools/hil/profile_pair_sf8_32_manifest.json').read_bytes()).hexdigest()}
    def add(name,path):
        raw=path.read_bytes();files[name]=raw
        manifest['files'][name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    for name in ('firmware.bin','partitions.bin'):add('rx-'+name,root/'.pio/build/profile_four_tx_v4_rx'/name)
    for name in ('profile_pair.py','profile_pair_4p6_collect.py','profile_pair_run.py','profile_pair_4p6_deploy.py',
                 'profile_switch.py','profile_four_tx_fixture.py','ProfilePairPlan.h','ProfileSwitchUsb.h',
                 'profile_switch.cpp','profile_switch.ini','profile_switch_channels.h','profile_switch_experiments.h',
                 'profile_stationary_baseline.h','ProfileChannelVisitClock.h','ProfileChannelTrace.h','ProfileFrequencyOffset.h'):
        add(name,root/'tools/hil'/name)
    for name in ('RadioProfiles.h','helpers/radiolib/CustomSX1262.h','helpers/radiolib/RadioLibWrappers.cpp',
                 'helpers/radiolib/RadioLibWrappers.h','helpers/radiolib/SX1262ProfileSwitchState.h'):
        add('source_'+name.replace('/','_'),root/'src'/name)
    with zipfile.ZipFile(dest/'bundle.zip','x',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in files.items():archive.writestr(name,raw)
        archive.writestr('manifest.json',json.dumps(manifest,indent=2)+'\n')
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    raw=(dest/'bundle.zip').read_bytes();digest=hashlib.sha256(raw).hexdigest()
    (dest/'bundle.sha256').write_text(digest+'\n')
    print(json.dumps({'bundle':str(dest/'bundle.zip'),'bytes':len(raw),'sha256':digest}),flush=True)
    for offset in range(0,len(raw),16384):
        print(remote({'action':'upload','offset':offset,'total':len(raw),'sha256':digest,
                      'data':base64.b64encode(raw[offset:offset+16384]).decode()}),flush=True)
    print(remote({'action':'unpack','sha256':digest}),flush=True)
elif args.action=='fetch':
    text=remote({'action':'export'})
    lines=[line.removeprefix('PAIR_CAPTURE ') for line in text.splitlines() if line.startswith('PAIR_CAPTURE ')]
    if len(lines)!=1:raise RuntimeError('Incomplete export')
    data=json.loads(zlib.decompress(base64.b64decode(lines[0],validate=True)))
    expected={'results.json','deployment.json','manifest.json','launch.json','run-control.json','run-exit.json'}
    if set(data['files'])!=expected:raise RuntimeError('Unexpected export set')
    decoded={}
    for name,record in data['files'].items():
        raw=base64.b64decode(record['bytes'],validate=True);json.loads(raw)
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise RuntimeError('Capture hash mismatch')
        decoded[Path(str(prefix)+name)]=raw
    decoded[Path(str(prefix)+'export.json')]=(json.dumps({'services_after':data['services_after'],
        'remote_sha256':{n:r['sha256'] for n,r in data['files'].items()}},indent=2)+'\n').encode()
    if any(p.exists() for p in decoded):raise RuntimeError('Preserve existing captures')
    for path,raw in decoded.items():
        with path.open('xb') as stream:stream.write(raw)
        print(json.dumps({'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}))
else:print(remote({'action':args.action},240 if args.action=='deploy' else 45),flush=True)
