"""Read-only inventory for the user-authorized MercerMesh four-TX bench."""
import json
import os
from pathlib import Path
import socket
import subprocess

print(json.dumps(dict(host=socket.gethostname(),user=os.environ.get('USER'),home=str(Path.home()))),flush=True)
for path in (Path.home()/'AGENTS.md',Path.home()/'hwtest'/'AGENTS.md'):
    if path.is_file(): print(str(path)+'\n'+path.read_text(),flush=True)
for command in (['id'],['lsusb'],['lsusb','-t'],['ls','-l','/dev/serial/by-id'],
                ['systemctl','list-units','--type=service','--state=running','--no-pager','--plain']):
    done=subprocess.run(command,capture_output=True,text=True,timeout=15)
    print(json.dumps(dict(command=command,rc=done.returncode,stdout=done.stdout,stderr=done.stderr)),flush=True)
try:
    from serial.tools import list_ports
    ports=list(list_ports.comports())
    print(json.dumps(dict(ports=[dict(device=p.device,vid=p.vid,pid=p.pid,serial=p.serial_number,
                                     product=p.product,manufacturer=p.manufacturer,interface=p.interface,
                                     location=p.location) for p in ports])),flush=True)
    for p in ports:
        done=subprocess.run(['fuser','-v',p.device],capture_output=True,text=True,timeout=10)
        print(json.dumps(dict(port=p.device,owners=done.stdout,details=done.stderr,rc=done.returncode)),flush=True)
except ImportError:
    print('System Python lacks pyserial; USB inventory above remains read-only.',flush=True)
