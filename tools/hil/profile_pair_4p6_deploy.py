"""Update only the exact idle V4 application; leave both fixed TX images intact."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
from profile_pair import TARGETS,RX,open_radio,require,wait_enumeration
from profile_four_tx_fixture import resolve
from profile_switch import exchange

root=Path(__file__).resolve().parent
path=root/'deployment.json'
if path.exists():raise RuntimeError('Deployment exists; no automatic reflash')
manifest=json.loads((root/'manifest.json').read_text())
for name,meta in manifest['files'].items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=meta['sha256']:raise RuntimeError('Artifact changed')

def active(name):return subprocess.run(['systemctl','is-active','--quiet',name]).returncode==0
def run(args,timeout=30):
    proc=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=timeout)
    print(json.dumps({'command':list(map(str,args)),'rc':proc.returncode,'out':proc.stdout,'err':proc.stderr}),flush=True)
    if proc.returncode:raise RuntimeError('Deployment command failed')
    return proc.stdout+proc.stderr

if active('meshcore-memory-soak.service'):raise RuntimeError('Receiver owned by soak')
report={'complete':False,'writes':[],'unchanged_transmitters':[]}
for target in (*TARGETS,RX):
    port,info=open_radio(target)
    try:
        if target is RX:
            require(exchange(port,'scanstatus 1','result',3,1,cached=True),dict(active=False,channels=0),'RX not idle')
        else:
            require(info,dict(prepared=False,packet_count=0,failed=False),'TX not idle')
            report['unchanged_transmitters'].append({'serial':target['serial'],'info':info})
    finally:port.close()
mm=active('ModemManager.service');stopped=False
report['modemmanager_before']=mm
def save():path.write_text(json.dumps(report,indent=2)+'\n')
save()
try:
    if mm:run(['sudo','-n','systemctl','stop','ModemManager.service'],15);stopped=True
    name=resolve(RX);tool=Path.home()/'.local/bin/esptool'
    identity=run([tool,'--chip','esp32s3','--port',name,'--baud','115200','flash-id'])
    if RX['serial'].lower() not in identity.lower() or '16MB' not in identity.replace(' ',''):
        raise RuntimeError('V4 MAC/flash mismatch')
    expected=(root/'rx-partitions.bin').read_bytes();partition=root/'rx-partitions-readback.bin'
    run([tool,'--chip','esp32s3','--port',name,'--baud','115200','read-flash','0x8000',str(len(expected)),partition])
    if partition.read_bytes()!=expected:raise RuntimeError('Partition layout mismatch; no application write')
    entry={'board':RX['board'],'serial':RX['serial'],'application_write_started':True,'offset':'0x10000'}
    report['writes'].append(entry);save()
    run([tool,'--chip','esp32s3','--port',name,'--baud','115200','write-flash','0x10000',root/'rx-firmware.bin'],180)
    time.sleep(3);wait_enumeration(RX)
    port,entry['info']=open_radio(RX)
    try:
        require(exchange(port,'pairinfo','pair_plan',3),dict(case_4p6_300us=True),'New receiver capability')
        require(exchange(port,'scanstatus 1','result',3,1,cached=True),dict(active=False,channels=0),'New RX idle')
    finally:port.close()
    entry['complete']=True;report['complete']=True
except BaseException as error:report['error']=repr(error);raise
finally:
    if stopped:run(['sudo','-n','systemctl','start','ModemManager.service'],15)
    save()
