"""Exclusive serial owner for the remote CAD test board; local Unix socket only."""
import json
import os
from pathlib import Path
import serial
import socketserver
import time
import sys

LOCAL=os.name=='nt'
ROOT=Path('C:/git/MeshCore/out/cad-scan') if LOCAL else Path.home()/'hwtest/runs/cad-scan'
ROOT.mkdir(parents=True,exist_ok=True)
os.umask(0o077)
PORT='COM31' if LOCAL else '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_44:1B:F6:69:CF:98-if00'
port=serial.Serial();port.port=PORT;port.baudrate=115200;port.timeout=.05;port.dtr=False;port.rts=False;port.open()
time.sleep(3)
pending=bytearray()
def read_until(timeout, predicate):
    result=[];end=time.monotonic()+timeout
    while time.monotonic()<end:
        while b'\n' not in pending and time.monotonic()<end:
            pending.extend(port.read(max(port.in_waiting,1)))
        if b'\n' not in pending:break
        raw,_,rest=pending.partition(b'\n');pending[:]=rest
        line=raw.decode(errors='replace').strip()
        if not line:continue
        with (ROOT/'serial.jsonl').open('a') as f:f.write(json.dumps({'time':time.time(),'line':line})+'\n')
        try:value=json.loads(line)
        except ValueError:continue
        result.append(value)
        if predicate(value):break
    return result
read_until(3,lambda x:False)
class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        request=json.loads(self.rfile.readline())
        command=request['command']
        if not isinstance(command,str) or len(command)>80 or '\n' in command:raise ValueError('bad command')
        port.write((command+'\n').encode());port.flush()
        if command.startswith('tx '):
            seq=int(command.split()[2]);predicate=lambda x:x.get('tx')==seq or 'error' in x
        elif command=='stats':predicate=lambda x:'stats' in x or 'error' in x
        else:predicate=lambda x:'ok' in x or 'error' in x
        result=read_until(5,predicate)
        self.wfile.write(json.dumps(result).encode()+b'\n')
sock=ROOT/'control.sock'
sock.unlink(missing_ok=True)
server_class=socketserver.TCPServer if LOCAL else socketserver.UnixStreamServer
address=('127.0.0.1',38476) if LOCAL else str(sock)
with server_class(address,Handler) as server:
    (ROOT/'serial-server.pid').write_text(str(os.getpid()))
    server.serve_forever()
