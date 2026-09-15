"""Three exact, idle HIL radios only; application updates, no bootloader/SD writes."""
import hashlib
import errno
import json
from pathlib import Path
import subprocess
import time
import zipfile
import serial
from serial.tools import list_ports
from profile_pair import TARGETS,RX,open_radio,require,wait_enumeration
from profile_four_tx_fixture import resolve

ROOT=Path(__file__).resolve().parent
manifest=json.loads((ROOT/'manifest.json').read_text())
report={'complete':False,'writes':[]}
path=ROOT/'deployment.json'
if path.exists():raise RuntimeError('Deployment exists; no automatic reflash')
def save():path.write_text(json.dumps(report,indent=2)+'\n')
def run(args,timeout=120):
    p=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=timeout)
    print(json.dumps({'command':list(map(str,args)),'rc':p.returncode,'out':p.stdout,'err':p.stderr}),flush=True)
    if p.returncode:raise RuntimeError('Operation failed: '+str(args[0]))
    return p.stdout+p.stderr
def active(name):return subprocess.run(['systemctl','is-active','--quiet',name]).returncode==0
def boot_match(target):
    # 1200-baud touch selects CDC-only DFU, not the MSC/UF2 personality.
    pid=0x002A if target is TARGETS[0] else 0x0057
    matches=[p for p in list_ports.comports() if p.serial_number==target['serial']
             and p.location==target['topology']+':1.0'
             and p.vid==target['boot_vid'] and p.pid in (target['boot_pid'],pid)]
    if len(matches)>1:raise RuntimeError('Ambiguous bootloader')
    return matches[0] if matches else None
for name,meta in manifest['files'].items():
    data=(ROOT/name).read_bytes()
    if hashlib.sha256(data).hexdigest()!=meta['sha256']:raise RuntimeError('Artifact changed: '+name)
if active('meshcore-memory-soak.service'):raise RuntimeError('V4 owned by soak')
for target in (*TARGETS,RX):
    if target is not RX and boot_match(target):continue
    p,info=open_radio(target,pair=False)
    p.close()
    if target is not RX:require(info,dict(prepared=False,packet_count=0),'TX active')
mm=active('ModemManager.service');stopped=False
report['modemmanager_before']=mm;save()
try:
    if mm:run(['sudo','-n','systemctl','stop','ModemManager.service'],15);stopped=True
    for target in TARGETS:
        package=ROOT/(target['env']+'.zip')
        with zipfile.ZipFile(package) as z:
            m=json.loads(z.read('manifest.json'))['manifest']
            if set(m)!={'application','dfu_version'}:raise RuntimeError('Not application-only DFU')
            if m['application']['init_packet_data']['softdevice_req']!=[target['fwid']]:
                raise RuntimeError('Wrong SoftDevice requirement')
        name=resolve(target)
        # Standard first-CDC 1200-baud bootloader entry, supported by TinyUSB.
        if not boot_match(target):
            p=serial.Serial();p.port=name;p.baudrate=1200;p.timeout=.2;p.dtr=True;p.rts=False;p.open()
            try:
                time.sleep(.3);p.dtr=False
            except OSError as error:
                # Disconnect while resetting is expected only if the exact
                # DFU identity subsequently appears. Permission failures stop.
                if error.errno not in (errno.EPROTO,errno.EIO,errno.ENODEV):raise
            finally:p.close()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if boot_match(target):break
            time.sleep(.2)
        else:raise RuntimeError('Exact bootloader missing; physical double reset needed: '+target['board'])
        name=resolve(target)
        if boot_match(target).device!=name:raise RuntimeError('DFU identity changed')
        entry={'board':target['board'],'serial':target['serial'],'application_write_started':True}
        report['writes'].append(entry);save()
        output=run([Path.home()/'.local/bin/adafruit-nrfutil','dfu','serial','--package',package,'-p',name,'-b','115200'])
        if 'Device programmed.' not in output:raise RuntimeError('No explicit DFU completion')
        time.sleep(3);wait_enumeration(target)
        p,entry['info']=open_radio(target);p.close();entry['complete']=True;save()
    name=resolve(RX);tool=Path.home()/'.local/bin/esptool'
    identity=run([tool,'--chip','esp32s3','--port',name,'--baud','115200','flash-id'],30)
    if RX['serial'].lower() not in identity.lower() or '16MB' not in identity.replace(' ',''):
        raise RuntimeError('V4 MAC/flash mismatch')
    partition=ROOT/'rx-partitions-readback.bin'
    expected=(ROOT/'rx-partitions.bin').read_bytes()
    run([tool,'--chip','esp32s3','--port',name,'--baud','115200','read-flash','0x8000',str(len(expected)),partition],30)
    if partition.read_bytes()!=expected:raise RuntimeError('Existing partition layout mismatch; no write')
    entry={'board':RX['board'],'serial':RX['serial'],'application_write_started':True,'offset':'0x10000'}
    report['writes'].append(entry);save()
    run([tool,'--chip','esp32s3','--port',name,'--baud','115200','write-flash','0x10000',ROOT/'rx-firmware.bin'],180)
    time.sleep(3);wait_enumeration(RX)
    p,entry['info']=open_radio(RX);p.close();entry['complete']=True
    report['complete']=True
except BaseException as error:report['error']=repr(error);raise
finally:
    if stopped:run(['sudo','-n','systemctl','start','ModemManager.service'],15)
    save()
