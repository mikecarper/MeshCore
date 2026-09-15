"""Bounded transfer/control for the named follow-up only, via configured bridge."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
import zlib

request=json.loads(base64.b64decode('__PAYLOAD_B64__',validate=True))
root=Path.home()/'hwtest/runs/pair-sf8-32-4p6-300us-20260914'
action=request['action']
if action=='upload':
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if (root/'manifest.json').exists():raise RuntimeError('Already unpacked')
    archive=root/'bundle.zip';size=archive.stat().st_size if archive.exists() else 0
    offset,total=request['offset'],request['total'];data=base64.b64decode(request['data'],validate=True)
    if len(data)>16384 or offset<0 or offset+len(data)>total or total>2000000:raise RuntimeError('Invalid chunk')
    if size==offset:
        with archive.open('ab') as stream:stream.write(data)
    elif size==offset+len(data):
        with archive.open('rb') as stream:stream.seek(offset);prior=stream.read(len(data))
        if prior!=data:raise RuntimeError('Mismatched repeated chunk')
    else:raise RuntimeError('Out of order chunk')
    if archive.stat().st_size==total and hashlib.sha256(archive.read_bytes()).hexdigest()!=request['sha256']:
        raise RuntimeError('Complete bundle hash mismatch')
    print(json.dumps({'uploaded':archive.stat().st_size,'total':total}),flush=True)
elif action=='unpack':
    if (root/'manifest.json').exists():raise RuntimeError('Already unpacked')
    if hashlib.sha256((root/'bundle.zip').read_bytes()).hexdigest()!=request['sha256']:raise RuntimeError('Bundle hash')
    with zipfile.ZipFile(root/'bundle.zip') as archive:
        manifest=json.loads(archive.read('manifest.json'))
        if set(archive.namelist())!=set(manifest['files'])|{'manifest.json'}:raise RuntimeError('Entry set')
        for name in archive.namelist():
            if Path(name).name!=name or name in ('.','..'):raise RuntimeError('Unsafe bundle entry')
            raw=archive.read(name)
            if name!='manifest.json' and hashlib.sha256(raw).hexdigest()!=manifest['files'][name]['sha256']:
                raise RuntimeError('Artifact hash')
            with (root/name).open('xb') as stream:stream.write(raw)
    print(json.dumps({'unpacked':str(root)}),flush=True)
elif action=='deploy':
    raise SystemExit(subprocess.run(['python3','-u',str(root/'profile_pair_4p6_deploy.py')],cwd=root).returncode)
elif action=='launch':
    if (root/'launch.json').exists() or (root/'results.json').exists():raise RuntimeError('Already launched')
    if not json.loads((root/'deployment.json').read_text()).get('complete'):raise RuntimeError('Deployment incomplete')
    manifest=json.loads((root/'manifest.json').read_text())
    for name,meta in manifest['files'].items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=meta['sha256']:raise RuntimeError('Changed artifact')
    with (root/'collector.log').open('x') as stream:
        proc=subprocess.Popen(['python3','-u',str(root/'profile_pair_run.py'),'--collector','profile_pair_4p6_collect.py'],
            cwd=root,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    record={'pid':proc.pid,'path':str(root),'collector':'profile_pair_4p6_collect.py','phases':[[300,1024]]}
    (root/'launch.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record),flush=True)
elif action=='status':
    if (root/'results.json').exists():
        result=json.loads((root/'results.json').read_text())
        stages=[]
        for stage in result['baseline']+result['scans']:
            stages.append({'channel':stage.get('channel'),'dwell_us':stage.get('dwell_us'),'complete':stage['complete'],
                'rates':[{'channel':ch,'attempted':len(rows),'received':sum(t['valid'] for t in rows)}
                    for ch in (0,1) if (rows:=[t for t in stage['trials'] if t['channel']==ch])],
                'switch':stage.get('status',stage.get('timing_before',{})).get('switch')})
        print(json.dumps({'phase':result['phase'],'complete':result['complete'],'error':result.get('error'),
            'stages':stages,'cleanup':[{k:r[k] for k in ('board','idle','error') if k in r} for r in result.get('cleanup',[])]}),flush=True)
    if (root/'run-exit.json').exists():print((root/'run-exit.json').read_text(),flush=True)
    if (root/'collector.log').exists():print((root/'collector.log').read_text()[-1200:],flush=True)
elif action=='export':
    if not (root/'run-exit.json').exists():raise RuntimeError('Collector still running')
    files={}
    for name in ('results.json','deployment.json','manifest.json','launch.json','run-control.json','run-exit.json'):
        raw=(root/name).read_bytes()
        files[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':base64.b64encode(raw).decode()}
    services={name:subprocess.run(['systemctl','is-active',name],capture_output=True,text=True,timeout=5).stdout.strip()
        for name in ('ModemManager.service','mctomqtt.service','meshcore-host-cli.service','meshcore-memory-soak.service')}
    data={'files':files,'services_after':services}
    print('PAIR_CAPTURE '+base64.b64encode(zlib.compress(json.dumps(data).encode(),9)).decode(),flush=True)
else:raise ValueError('Unknown action')
