"""Read-only exact-fixture check before the two-profile preamble experiment."""
import json
from pathlib import Path
import subprocess
import sys

root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
sys.path.insert(0,str(root))
from profile_four_tx_fixture import TXS,RX
from profile_four_tx import open_radio
from profile_switch import exchange

if subprocess.run(['systemctl','is-active','--quiet','meshcore-memory-soak.service']).returncode==0:
    raise RuntimeError('Receiver is in use by the soak test')
for target in (TXS[0],TXS[3],RX):
    port,info=open_radio(target)
    try:
        if info.get('board')!=target['board']:raise RuntimeError('Board identity mismatch')
        row={'board':target['board'],'serial':target['serial'],'port':port.port,'info':info}
        if target is RX:
            row['status']=exchange(port,'scanstatus 1','result',3,1,cached=True)
            if row['status'].get('active') or row['status'].get('channels')!=0:
                raise RuntimeError('Receiver already scanning')
        elif info.get('prepared') or info.get('packet_count') or info.get('failed'):
            raise RuntimeError('Transmitter is not idle')
        print(json.dumps(row),flush=True)
    finally:port.close()
