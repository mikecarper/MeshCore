#!/usr/bin/env python3
"""Run matched HIL workloads through the persistent local soak logger."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import subprocess
import time

ROOT=Path.home()/'hwtest/runs/s3-memory-soak'
DEVICES=('indicator','v4')
CONFIG=None

def rpc(**request):
    if CONFIG and CONFIG.get('control_tcp_port'):
        request['token']=CONFIG['control_token']
        stream=socket.create_connection(('127.0.0.1',CONFIG['control_tcp_port']),timeout=40)
    else:
        stream=socket.socket(socket.AF_UNIX)
        stream.settimeout(40);stream.connect(str(ROOT/'control.sock'))
    with stream:
        stream.sendall(json.dumps(request).encode()+b'\n')
        response=json.loads(stream.makefile('rb').readline())
        if not response['ok']: raise RuntimeError(response.get('error','RPC failed'))
        return response['result']

def command(device,value):
    return rpc(device=device,command=value,timeout=25)['reply']

def sample(device):
    value=rpc(operation='sample',device=device)
    if value['uptime_decreased']: raise AssertionError(device+' unexpected reset')
    return value

def healthy(device,timeout=360):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            value=sample(device)
            if value['wifi']==3 and 'custom (ok)' in value['mqtt_status']: return value
        except (OSError,RuntimeError): pass
        time.sleep(2)
    raise RuntimeError(device+' did not recover WiFi and MQTT')

def together(function):
    with ThreadPoolExecutor(max_workers=2) as pool:
        return dict(zip(DEVICES,pool.map(function,DEVICES)))

def restart_bridge(device):
    setting='mqtt.enabled' if device=='indicator' else 'bridge.enabled'
    for value in ('off','on'):
        if 'OK' not in command(device,'set '+setting+' '+value):
            raise RuntimeError(device+' bridge toggle failed')
    return healthy(device)

def run(phase, resume=False, broker_outage=True):
    result={'phase':phase,'started':time.time(),'workloads':{},'passed':False}
    output=ROOT/(phase+'-stress.json')
    if resume:
        result=json.loads(output.read_text())
        assert result['workloads']['ota_context_cycles_per_board']==200
        assert len(result['mqtt_restarts'])==4
        result['resumed_after_display_check']=time.time()
        result['workloads']['mqtt_restarts_per_board']=4
    def save(): output.write_text(json.dumps(result,indent=2))
    try:
        rpc(operation='phase',phase=phase+'-stress')
        if not resume: result['before']=together(restart_bridge);save()
        def contexts(device):
            for i in range(200):
                reply=command(device,'soak ota-cycle')
                if 'OK - OTA context cycle' not in reply: raise RuntimeError(device+' OTA context failed')
                if (i+1)%50==0: print(phase,device,'OTA_CYCLES',i+1,flush=True)
            return sample(device)
        if not resume: result['after_contexts']=together(contexts)
        result['workloads']['ota_context_cycles_per_board']=200;save()
        restarts=[]
        for i in range(0 if resume else 4):
            values=together(restart_bridge)
            restarts.append({'iteration':i+1,'samples':values})
            print(phase,'MQTT_RESTART',i+1,json.dumps(values),flush=True)
            result['mqtt_restarts']=restarts;save()
        result['workloads']['mqtt_restarts_per_board']=4
        wifi=[]
        # Preserve the production reconnect ladder: it deliberately grows to
        # five minutes during rapid flapping and resets after two stable minutes.
        # One link fault plus one broker fault avoids mistaking that backoff for
        # a leak, while four clean bridge restarts exercise repeated TLS teardown.
        for i in range(1):
            start=time.monotonic()
            def reconnect(device):
                try: command(device,'soak wifi-drop')
                except (OSError,RuntimeError): pass # TCP is expected to close.
                time.sleep(3)
                return healthy(device)
            values=together(reconnect)
            wifi.append({'iteration':i+1,'seconds':time.monotonic()-start,'samples':values})
            print(phase,'WIFI_RECONNECT',i+1,json.dumps(values),flush=True)
            time.sleep(3)
        result['wifi_reconnects']=wifi
        result['workloads']['wifi_reconnects_per_board']=1;save()
        outages=[]
        for i in range(1 if broker_outage else 0):
            subprocess.run(['sudo','systemctl','stop','meshcore-soak-broker'],check=True)
            try: time.sleep(5)
            finally: subprocess.run(['sudo','systemctl','start','meshcore-soak-broker'],check=True)
            start=time.monotonic()
            time.sleep(3)
            values=together(healthy)
            outages.append({'iteration':i+1,'recovery_seconds':time.monotonic()-start,'samples':values})
            print(phase,'BROKER_OUTAGE',i+1,json.dumps(values),flush=True)
        result['broker_outages']=outages
        result['workloads']['broker_outages']=len(outages)
        time.sleep(15)
        result['after']=together(healthy)
        result['logger']=rpc(operation='status')
        result['passed']=True
    except Exception as error:
        result['error']=str(error)
        raise
    finally:
        result['finished']=time.time();save()
        print('STRESS_RESULT',json.dumps(result),flush=True)
        rpc(operation='phase',phase=phase+'-settled')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=('A','B'))
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--config',type=Path,help='Logger config for local authenticated TCP control')
    parser.add_argument('--skip-broker-outage',action='store_true',help='For hosts that do not own the lab broker')
    args=parser.parse_args()
    if args.config:
        CONFIG=json.loads(args.config.read_text())
        ROOT=Path(CONFIG['directory'])
        DEVICES=tuple(CONFIG['devices'])
    run(args.phase,args.resume,not args.skip_broker_outage)
