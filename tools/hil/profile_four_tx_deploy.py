"""Identity/hash-gated application DFU on selected four TXs plus V4 bench RX."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time
import zipfile
from profile_four_tx_fixture import TXS,RX,resolve
from profile_switch import exchange,configure_session
import serial
from serial.tools import list_ports

ROOT=Path(__file__).resolve().parent
REPORT=ROOT/'deployment.json'
if REPORT.exists(): raise RuntimeError('A deployment report already exists; no implicit reflashing')
manifest=json.loads((ROOT/'manifest.json').read_text())
report={'complete':False,'tx':[],'rx':{},'services_before':{}}

def save():REPORT.write_text(json.dumps(report,indent=2)+'\n')
def run(args,timeout=120):
    p=subprocess.run([str(a) for a in args],capture_output=True,text=True,timeout=timeout)
    print(json.dumps({'command':args,'rc':p.returncode,'out':p.stdout,'err':p.stderr},default=str),flush=True)
    if p.returncode:raise RuntimeError('Operation failed: '+str(args[0]))
    return p.stdout+p.stderr
def service(name,action):run(['sudo','-n','systemctl',action,name],20)
def boot_port(t):
    port=resolve(t)
    p=next(p for p in list_ports.comports() if p.device==port)
    if (p.vid,p.pid)==(t['boot_vid'],t['boot_pid']):return port
    if p.vid!=0x239A or p.pid not in (0x8029,0x4405):
        raise RuntimeError('Neither identified application nor identified UF2 bootloader')
    # Enter immediately before this board's DFU, rather than assuming it stayed
    # in recovery while the other images were built/transferred.
    try:
        with serial.Serial(port,115200,timeout=.2,write_timeout=2) as stream:
            stream.dtr=True
            if t['channel']!=2:
                stream.write(b'+++MESHCORE-TERM-STOP\r\n+++MESHCORE-TERM-START\r\n')
                stream.flush();time.sleep(.7);stream.reset_input_buffer()
            stream.write(b'uf2reset\r\n');stream.flush();time.sleep(.7)
    except (OSError,serial.SerialException):pass
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        try:return resolve(t,boot=True)
        except RuntimeError:time.sleep(.3)
    raise RuntimeError('Exact bootloader did not enumerate: '+t['board'])
def ready(t):
    deadline=time.monotonic()+25
    error=None
    while time.monotonic()<deadline:
        try:
            name=resolve(t)
            p=serial.Serial();p.port=name;p.baudrate=115200;p.timeout=.2;p.write_timeout=2
            configure_session(p);p.open()
            try:
                info=exchange(p,'info','board',3)
                if info.get('bench')!='production-profile-switch-v8' or not info.get('ready'):
                    raise RuntimeError('Unready bench image')
                return dict(port=name,info=info)
            finally:p.close()
        except (OSError,RuntimeError,TimeoutError) as e:error=e;time.sleep(.3)
    raise RuntimeError('No matching running HIL on '+t['board']+': '+str(error))

run(['sudo','-n','true'],10)
for name,record in manifest['files'].items():
    if Path(name).name!=name:raise RuntimeError('Unsafe artifact name')
    b=(ROOT/name).read_bytes()
    if len(b)!=record['bytes'] or hashlib.sha256(b).hexdigest()!=record['sha256']:
        raise RuntimeError('Artifact checksum mismatch: '+name)
for t in TXS:resolve(t)
for name in ('ModemManager.service','meshcore-memory-soak.service'):
    report['services_before'][name]=subprocess.run(['systemctl','is-active','--quiet',name]).returncode==0
save()
try:
    if report['services_before']['ModemManager.service']:service('ModemManager.service','stop')
    for t in TXS:
        package=ROOT/(t['env']+'.zip')
        with zipfile.ZipFile(package) as z:
            m=json.loads(z.read('manifest.json'))['manifest']
            if set(m)!={'application','dfu_version'} or m['application']['init_packet_data']['softdevice_req']!=[t['fwid']]:
                raise RuntimeError('Not the matching application-only DFU package')
        port=boot_port(t)
        entry=dict(target=t,port_before=port,package_sha256=manifest['files'][package.name]['sha256'])
        report['tx'].append(entry);save()
        output=run([str(Path.home()/'.local/bin/adafruit-nrfutil'),'dfu','serial','--package',str(package),'-p',port,'-b','115200'],120)
        if 'Device programmed.' not in output:raise RuntimeError('DFU lacks explicit completion')
        entry['after']=ready(t)
        info=entry['after']['info']
        if any(info.get(k)!=v for k,v in dict(board=t['board'],sd_fwid=t['fwid'],app_base=t['app_base'],fixed_tx=1,prepared=False,failed=False,autonomous_tx=False).items()):
            raise RuntimeError('Installed fixed TX identity/layout mismatch')
        entry['complete']=True;save()
    if report['services_before']['meshcore-memory-soak.service']:service('meshcore-memory-soak.service','stop')
    port=resolve(RX)
    tool=str(Path.home()/'.local/bin/esptool')
    identity=run([tool,'--chip','esp32s3','--port',port,'--baud','115200','flash-id'],30)
    if RX['serial'].lower() not in identity.lower() or '16MB' not in identity.replace(' ',''):
        raise RuntimeError('V4 MAC/flash size mismatch')
    args=[tool,'--chip','esp32s3','--port',port,'--baud','115200','write-flash','--flash-size','16MB',
          '0x0',str(ROOT/'rx-bootloader.bin'),'0x8000',str(ROOT/'rx-partitions.bin'),
          '0xe000',str(ROOT/'rx-boot_app0.bin'),'0x10000',str(ROOT/'rx-firmware.bin')]
    report['rx']['write_started']=True;save()
    run(args,180)
    report['rx']['after']=ready(RX)
    if report['rx']['after']['info'].get('rx_gain_reg')!=0x94:raise RuntimeError('V4 RX gain mismatch')
    report['rx']['complete']=True
    report['complete']=True
except Exception as e:
    report['error']=str(e)
    raise
finally:
    if report['services_before']['ModemManager.service']:service('ModemManager.service','start')
    # The soak logger cannot run against a RAM-only bench image. Leave it
    # paused after V4 replacement; do not silently restore unrelated firmware.
    if not report['rx'].get('write_started') and report['services_before']['meshcore-memory-soak.service']:
        service('meshcore-memory-soak.service','start')
    save()
