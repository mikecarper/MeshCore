#!/usr/bin/env python3
"""Exercise a board's real WiFi mOTA folder-session acquisition/release path."""
import argparse
import json
import socket
import time
from s3_memory_stress import ROOT, DEVICES, command, healthy, rpc

HOSTS={'indicator':'192.168.1.54','v4':'192.168.1.45'}

def run(phase):
    result={'phase':phase,'boards':{},'passed':False}
    try:
        # This observer repeater recipe has no TCP folder-seeder endpoint.
        # Only the Full Indicator companion exposes that transport.
        for device in ('indicator',):
            entry={'before':healthy(device),'completed':0}
            result['boards'][device]=entry
            for iteration in range(20):
                with socket.create_connection((HOSTS[device],5001),timeout=5) as stream:
                    stream.settimeout(5);request=b''
                    while len(request)<4:
                        chunk=stream.recv(4-len(request))
                        if not chunk: raise RuntimeError('Folder request closed early')
                        request+=chunk
                    assert request==b'MS\x01\x01',request.hex()
                    response=b'ms\x01\x00\x00'
                    checksum=0
                    for byte in response: checksum^=byte
                    stream.sendall(response+bytes([checksum]))
                    time.sleep(.2)
                entry['completed']+=1
                time.sleep(.3)
            time.sleep(2)
            entry['after']=healthy(device)
            if entry['after']['dyn']: assert entry['after']['active']==0,'OTA workspace remained active'
            print('FOLDER_CYCLES',device,json.dumps(entry),flush=True)
        result['passed']=True
    finally:
        (ROOT/(phase+'-folder.json')).write_text(json.dumps(result,indent=2))
        print('FOLDER_RESULT',json.dumps(result),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=('A','B'))
    run(parser.parse_args().phase)
