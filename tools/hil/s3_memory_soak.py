#!/usr/bin/env python3
"""Hold lab transports open and log memory without resetting USB between polls.

Run on the hardware gateway. The Unix socket is owner-only; commands and replies
are never written to the telemetry log. Firmware-changing/erase operations are
outside this logger. Production builds do not expose the `get soak` diagnostic.
"""
import argparse
import json
import hmac
import os
import re
from pathlib import Path
import socket
import socketserver
import subprocess
import threading
import time


class Device:
    def __init__(self, name, endpoint, expected):
        self.name, self.endpoint, self.expected = name, endpoint, expected
        self.is_serial = endpoint.startswith('/dev/') or bool(re.fullmatch(r'COM\d+',endpoint,re.I))
        self.stream = None
        self.lock = threading.RLock()
        self.last_uptime = None
        self.connections = 0
        self.last_sample_time = None

    def close(self):
        if self.stream:
            self.stream.close()
        self.stream = None

    def read(self, deadline, marker=None):
        result = bytearray()
        while time.monotonic() < deadline:
            try:
                if self.is_serial:
                    chunk = self.stream.read(max(1, min(self.stream.in_waiting, 4096)))
                else:
                    chunk = self.stream.recv(4096)
                    if not chunk:
                        raise ConnectionError('TCP peer closed')
            except socket.timeout:
                continue
            if chunk:
                result.extend(chunk)
                if marker and marker in result:
                    break
        return result.decode(errors='replace')

    def write(self, data):
        if self.is_serial:
            self.stream.write(data)
        else:
            self.stream.sendall(data)

    def connect(self):
        if self.stream:
            return
        if self.is_serial:
            import serial
            stream = serial.Serial()
            stream.port, stream.baudrate, stream.timeout = self.endpoint, 115200, .05
            # Windows HWCDC needs asserted DTR to keep the host session active.
            # Keep RTS low; a held handle avoids repeated reset transitions.
            stream.dtr = os.name == 'nt'
            stream.rts = False
            stream.open()
            self.stream = stream
            self.read(time.monotonic()+20)
            self.write(b'\r')
            self.read(time.monotonic()+.3)
        else:
            host, port = self.endpoint.rsplit(':', 1)
            self.stream = socket.create_connection((host, int(port)), timeout=5)
            self.stream.settimeout(.05)
            self.read(time.monotonic()+2, b'> ')
        self.connections += 1
        reply = self._command('board')
        if self.expected not in reply:
            self.close()
            raise RuntimeError('Hardware identity does not match configured board')

    def _command(self, command, timeout=5):
        self.write(command.encode()+(b'\r' if self.is_serial else b'\n'))
        marker = b'  -> ' if self.is_serial else b'\r\n> '
        reply = self.read(time.monotonic()+timeout, marker)
        if self.is_serial and marker in reply.encode():
            reply += self.read(time.monotonic()+.15)
        return reply

    def command(self, command, timeout=5):
        if '\n' in command or '\r' in command or len(command)>540:
            raise ValueError('One bounded CLI command is required')
        with self.lock:
            try:
                self.connect()
                return self._command(command, timeout)
            except Exception:
                self.close()
                raise

    def sample(self):
        # A scheduled probe and an RPC probe can arrive together. Keep their
        # uptime bookkeeping and paired MQTT reads in the same order as USB.
        with self.lock:
            return self._sample_locked()

    def _sample_locked(self):
        reply = self.command('get soak')
        start, end = reply.find('{'), reply.rfind('}')
        if start < 0 or end < start:
            raise RuntimeError('No soak telemetry received')
        sample = json.loads(reply[start:end+1])
        sample['transport_connections'] = self.connections
        now = time.monotonic()
        decreased = self.last_uptime is not None and sample['up'] < self.last_uptime
        # The firmware exposes millis()/1000. A normal 49.7-day millis wrap
        # must not be reported as a crash during a multi-month run.
        wrapped = bool(decreased and self.last_sample_time is not None
                       and self.last_uptime > 4294000 and sample['up'] < 1000
                       and abs(sample['up'] + 4294967.296 - self.last_uptime
                               - (now-self.last_sample_time)) < 3)
        sample['uptime_wrapped'] = wrapped
        sample['uptime_decreased'] = decreased and not wrapped
        self.last_uptime = sample['up']
        self.last_sample_time = now
        # Deliberately omit mqtt.stats: its outbox getter takes the client lock
        # and can block the firmware CLI behind an in-progress TLS handshake.
        for name in ('status',):
            reply = self.command('get mqtt.'+name)
            # Only retain diagnostic reply lines, never unsolicited logs or
            # echoed commands (which could contain private setup material).
            lines = [re.sub(r'^\s*(?:->\s*)?>\s*','',line).strip() for line in reply.splitlines()
                     if re.match(r'^\s*(?:->\s*)?>\s*(?:msgs:|Free=|\(bridge)', line)]
            sample['mqtt_'+name] = ' '.join(lines)
        return sample


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    directory=Path(config['directory'])
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    devices={name:Device(name, entry['endpoint'], entry['expected']) for name,entry in config['devices'].items()}
    state={'phase':config.get('phase','baseline'), 'latest':{}, 'mqtt_received':{}}
    state_lock=threading.Lock()
    log_lock=threading.Lock()
    socket_path=directory/'control.sock'
    tcp_port=config.get('control_tcp_port')
    if tcp_port and len(config.get('control_token',''))<24:
        raise ValueError('Loopback control requires a private token')
    if not tcp_port and socket_path.exists():
        socket_path.unlink()

    def record(name, sample):
        entry={'time':time.time(),'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'phase':state['phase'],'device':name,**sample}
        with log_lock:
            with (directory/'telemetry.jsonl').open('a') as stream:
                stream.write(json.dumps(entry,separators=(',',':'))+'\n')
        with state_lock:
            state['latest'][name]=entry
            temporary=directory/'latest.tmp'
            temporary.write_text(json.dumps(state,indent=2))
            temporary.replace(directory/'latest.json')

    def monitor(name, device):
        while True:
            try:
                record(name,device.sample())
            except Exception as error:
                record(name,{'error':type(error).__name__})
            time.sleep(config.get('interval_seconds',60))

    def receive_mqtt():
        broker = config['broker']
        while True:
            process = subprocess.Popen(['mosquitto_sub','-h',broker['host'],
                '-p',str(broker['port']),'--cafile',broker['ca_file'],
                '-t','meshcore-soak/#','-F','%t'],stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,text=True)
            for topic in process.stdout:
                parts=topic.strip().split('/')
                if len(parts)<3 or parts[1] not in devices: continue
                with state_lock:
                    entry=state['mqtt_received'].setdefault(parts[1],{'count':0})
                    entry['count']+=1
                    entry['last_time']=time.time()
            process.wait()
            time.sleep(5)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                request=json.loads(self.rfile.readline(8192))
                if tcp_port and not hmac.compare_digest(str(request.get('token','')),config['control_token']):
                    raise ValueError('Unauthorized local request')
                operation=request.get('operation','command')
                if operation=='status':
                    with state_lock: result=dict(state)
                elif operation=='phase':
                    phase=request['phase']
                    if not isinstance(phase,str) or len(phase)>80: raise ValueError('Invalid phase')
                    with state_lock: state['phase']=phase
                    result={'phase':phase}
                elif operation=='sample':
                    name=request['device']; result=devices[name].sample(); record(name,result)
                elif operation=='command':
                    result={'reply':devices[request['device']].command(request['command'],min(float(request.get('timeout',5)),30))}
                else:
                    raise ValueError('Unknown operation')
                response={'ok':True,'result':result}
            except Exception as error:
                response={'ok':False,'error':type(error).__name__}
            self.wfile.write(json.dumps(response).encode()+b'\n')

    server_base=socketserver.ThreadingTCPServer if tcp_port else socketserver.ThreadingUnixStreamServer
    class Server(server_base):
        daemon_threads=True
        allow_reuse_address=True
    server=Server(('127.0.0.1',int(tcp_port)) if tcp_port else str(socket_path),Handler)
    if not tcp_port: os.chmod(socket_path,0o600)
    for name,device in devices.items():
        threading.Thread(target=monitor,args=(name,device),daemon=True).start()
    if config.get('broker'):
        threading.Thread(target=receive_mqtt,daemon=True).start()
    server.serve_forever()


if __name__=='__main__':
    main()
