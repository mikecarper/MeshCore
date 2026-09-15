"""Reuse the verified five-radio firmware for the requested 7.7-symbol repeat.

Executed on the Pi through the existing configured bridge. The only payload
is the reviewed collector; dependencies and image provenance are hash-checked
against the previous bench bundle. No flashing or hub reset is performed.
"""
import base64
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

previous=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
root=Path.home()/'hwtest/runs/four-tx-sf10-7p7-20260914'
dwell_symbols=7.7
if root.exists():raise RuntimeError('Run directory already exists; no implicit rerun')
manifest=json.loads((previous/'manifest.json').read_text())
deployment=json.loads((previous/'deployment.json').read_text())
if not deployment.get('complete'):raise RuntimeError('Previous deployment not verified')
if subprocess.run(['systemctl','is-active','--quiet','meshcore-memory-soak.service']).returncode==0:
    raise RuntimeError('V4 soak active; do not interrupt another test')
dependencies=('profile_four_tx_fixture.py','profile_switch.py','profile_switch_channels.py',
              'profile_switch_packets.py','profile_switch_sweep.py')
files={name:(previous/name).read_bytes() for name in dependencies}
for name,data in files.items():
    if hashlib.sha256(data).hexdigest()!=manifest['files'][name]['sha256']:
        raise RuntimeError('Changed bench dependency: '+name)
code=base64.b64decode('__PAYLOAD_B64__',validate=True)
root.mkdir()
for name,data in files.items():
    with (root/name).open('xb') as f:f.write(data)
with (root/'profile_four_tx.py').open('xb') as f:f.write(code)
for name in ('deployment.json','manifest.json'):
    with (root/name).open('xb') as f:f.write((previous/name).read_bytes())
launch=dict(previous_run=str(previous),run=str(root),firmware_reused=True,dwell_symbols=dwell_symbols,
            collector_sha256=hashlib.sha256(code).hexdigest(),
            started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
with (root/'launch.json').open('x') as f:json.dump(launch,f,indent=2);f.write('\n')
print(json.dumps(launch),flush=True)
mm=subprocess.run(['systemctl','is-active','--quiet','ModemManager.service']).returncode==0
child=None
rc=1
cleanup={'radios':[],'services':{},'errors':[]}
try:
    if mm:subprocess.run(['sudo','-n','systemctl','stop','ModemManager.service'],check=True)
    child=subprocess.Popen(['python3','-u',str(root/'profile_four_tx.py'),'--dwell-symbols',str(dwell_symbols)],cwd=root)
    with (root/'run-control.json').open('x') as f:
        json.dump({'pid':child.pid,'collector':'profile_four_tx.py','modemmanager_before':mm},f)
    rc=child.wait(timeout=6000)
finally:
    if child is not None and child.poll() is None:
        child.send_signal(signal.SIGINT)
        try:child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
    # Independently verify idle after the collector closes its ports. Keep
    # each reboot session open long enough for the native USB command to land.
    sys.path.insert(0,str(root))
    from profile_four_tx_fixture import TXS,RX
    from profile_four_tx import open_radio
    from profile_switch import exchange
    time.sleep(3)
    for target in ((*TXS,RX) if child is not None else ()):
        try:
            p,info=open_radio(target)
            try:p.write(b'reboot\n');p.flush();time.sleep(.7)
            finally:p.close()
        except Exception as error:cleanup['errors'].append(target['board']+': '+str(error))
    time.sleep(3)
    for target in ((*TXS,RX) if child is not None else ()):
        try:
            p,info=open_radio(target)
            try:
                row={'board':target['board'],'serial':target['serial'],'info':info}
                if info.get('board')!=target['board']:raise RuntimeError('Wrong live board')
                if target is RX:
                    row['status']=exchange(p,'scanstatus 1','result',3,1,cached=True)
                    if row['status'].get('active') or row['status'].get('channels')!=0:
                        raise RuntimeError('RX not idle')
                elif info.get('prepared') or info.get('packet_count')!=0 or info.get('failed') or info.get('autonomous_tx'):
                    raise RuntimeError('TX not idle')
                cleanup['radios'].append(row)
            finally:p.close()
        except Exception as error:cleanup['errors'].append(target['board']+': '+str(error))
    if mm:
        restored=subprocess.run(['sudo','-n','systemctl','start','ModemManager.service'])
        if restored.returncode:cleanup['errors'].append('ModemManager restore failed')
    for name in ('ModemManager.service','meshcore-memory-soak.service','mctomqtt.service','meshcore-host-cli.service'):
        cleanup['services'][name]=subprocess.run(['systemctl','is-active',name],capture_output=True,text=True).stdout.strip()
    cleanup['verified_idle']=len(cleanup['radios'])==5 and not cleanup['errors']
    with (root/'cleanup.json').open('x') as f:json.dump(cleanup,f,indent=2);f.write('\n')
    print(json.dumps({'collector_exit':rc,'cleanup':cleanup}),flush=True)
if not cleanup['verified_idle']:raise RuntimeError('Bench cleanup requires attention')
raise SystemExit(rc)
