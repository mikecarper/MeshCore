"""Enter USB DFU on four explicitly selected test TXs; read boot metadata only.

No erase, DFU payload, hub reset or bootloader update. Mounts are read-only.
The RAK3401 gateway and V4 receiver are deliberately excluded.
"""
import json
import subprocess
import tempfile
import time
import struct
from pathlib import Path
import serial
from serial.tools import list_ports

TARGETS = (
    ('RAK4631', '9AB3B64C641BA927', '1-1.3.1'),
    ('HT-n5262G', '651F8E496197F882', '1-1.2.3'),
    ('HT-n5262', '9352162A72082314', '1-1.2.2'),
    ('T1000-E', '34A9141999729D5D', '1-1.2.1'),
)

def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=20).stdout

def usb_parent(path):
    for p in path.resolve().parents:
        if (p/'idVendor').exists() and (p/'serial').exists():
            return p
    return None

def matches(path, serial_number, topology):
    p = usb_parent(path)
    return p is not None and p.name == topology and (p/'serial').read_text().strip() == serial_number

run('sudo', '-n', 'true')
mm_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'ModemManager.service']).returncode == 0
try:
    if mm_active: run('sudo', '-n', 'systemctl', 'stop', 'ModemManager.service')
    for model, sn, topo in TARGETS:
        ports = [p for p in list_ports.comports() if p.serial_number == sn
                 and matches(Path('/sys/class/tty')/Path(p.device).name/'device', sn, topo)]
        ports.sort(key=lambda p: p.location or '')
        if not ports: raise RuntimeError(f'{model}: exact serial/topology missing')
        port = ports[0]
        already_msc = any(matches(p/'device',sn,topo) for p in Path('/sys/class/block').iterdir())
        if not already_msc and port.pid not in (0x0029, 0x002A, 0x4404, 0x0071, 0x0057):
            if model not in (port.product or ''): raise RuntimeError(f'Unexpected product {port.product}')
            busy = subprocess.run(['fuser', port.device], capture_output=True, text=True)
            if busy.returncode != 1: raise RuntimeError(f'{port.device} is owned or ownership check failed')
            print(json.dumps({'enter_dfu': model, 'serial': sn, 'port': port.device, 'topology': topo}), flush=True)
            try:
                with serial.Serial(port.device, 115200, timeout=.2, write_timeout=2) as s:
                    s.dtr = True
                    if model in ('HT-n5262G', 'T1000-E'):
                        s.write(b'+++MESHCORE-TERM-STOP\r\n+++MESHCORE-TERM-START\r\n')
                        s.flush();time.sleep(.7);s.reset_input_buffer()
                    s.write(b'uf2reset\r\n');s.flush()
                    time.sleep(.5)
            except (OSError, serial.SerialException):
                pass # Disconnect is expected; exact boot enumeration below is mandatory.
        deadline = time.monotonic()+25
        blocks = []
        while time.monotonic() < deadline:
            blocks = [p for p in Path('/sys/class/block').iterdir()
                      if matches(p/'device', sn, topo)]
            if len(blocks) == 1: break
            time.sleep(.3)
        if len(blocks) != 1: raise RuntimeError(f'{model}: no unique UF2 block device')
        dev = '/dev/'+blocks[0].name
        directory = Path(tempfile.mkdtemp(prefix='four-tx-bootinfo-'))
        mounted = False
        try:
            run('sudo', '-n', 'mount', '-t', 'vfat', '-o', 'ro,nosuid,nodev,noexec', dev, str(directory))
            mounted = True
            names={p.name.upper():p for p in directory.iterdir()}
            print(json.dumps({'mounted':dev,'files':list(names)}),flush=True)
            if not names:
                bpb=subprocess.run(['sudo','-n','dd','if='+dev,'bs=512','count=1','status=none'],check=True,capture_output=True,timeout=10).stdout
                root=struct.unpack_from('<H',bpb,14)[0]+bpb[16]*struct.unpack_from('<H',bpb,22)[0]
                rawroot=subprocess.run(['sudo','-n','dd','if='+dev,'bs=512','count=2','skip='+str(root),'status=none'],check=True,capture_output=True,timeout=10).stdout
                print(json.dumps({'bpb_hex':bpb.hex(),'root_sector':root,'root_hex':rawroot.hex()}),flush=True)
                continue
            info = names['INFO_UF2.TXT'].read_text()
            # Read only the beginning of the synthetic flash view, not Mesh identities.
            sd = None
            with names['CURRENT.UF2'].open('rb') as current:
                for _ in range(64):
                    block=current.read(512)
                    if len(block)!=512: break
                    magic0,magic1,flags,addr,size=struct.unpack_from('<5I',block)
                    if magic0!=0x0A324655 or magic1!=0x9E5D5157 or size!=256:
                        raise RuntimeError('Malformed readback UF2')
                    if addr<=0x3008 and addr+size>0x300d:
                        sd={'app_base':struct.unpack_from('<I',block,32+0x3008-addr)[0],
                            'fwid':struct.unpack_from('<H',block,32+0x300c-addr)[0]}
                        break
            print(json.dumps({'model': model, 'serial': sn, 'topology': topo, 'block': dev,
                              'info_uf2': info, 'installed_softdevice':sd}), flush=True)
        finally:
            if mounted: run('sudo', '-n', 'umount', str(directory))
            directory.rmdir()
finally:
    if mm_active: run('sudo', '-n', 'systemctl', 'start', 'ModemManager.service')
