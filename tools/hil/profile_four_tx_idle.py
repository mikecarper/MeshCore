"""After the collector exits, reboot only the five selected bench radios idle."""
import json
from pathlib import Path
import subprocess
import sys
import time
root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
sys.path.insert(0,str(root))
from profile_four_tx_fixture import TXS,RX
from profile_four_tx import open_radio
from profile_switch import exchange
control=json.loads((root/'run-control.json').read_text())
if Path(f"/proc/{control['pid']}").exists():raise RuntimeError('Collector still running; do not interrupt it')
for t in (*TXS,RX):
    p,info=open_radio(t)
    try:
        p.write(b'reboot\n');p.flush();time.sleep(.7)
    except OSError:pass
    finally:p.close()
time.sleep(3)
report={'radios':[],'services':{}}
for t in (*TXS,RX):
    p,info=open_radio(t)
    try:
        entry={'serial':t['serial'],'board':t['board'],'info':info}
        if t is RX:
            entry['status']=exchange(p,'scanstatus 1','result',3,1,cached=True)
            if entry['status'].get('active') or entry['status'].get('channels')!=0:
                raise RuntimeError('RX did not return idle')
        elif info.get('prepared') or info.get('packet_count')!=0:
            raise RuntimeError('TX did not return idle')
        report['radios'].append(entry)
    finally:p.close()
for name in ('ModemManager.service','meshcore-memory-soak.service','mctomqtt.service','meshcore-host-cli.service'):
    report['services'][name]=subprocess.run(['systemctl','is-active',name],capture_output=True,text=True).stdout.strip()
if report['services']['meshcore-memory-soak.service']!='inactive':raise RuntimeError('Soak should be paused on bench RX')
if any(report['services'][name]!='active' for name in ('ModemManager.service','mctomqtt.service','meshcore-host-cli.service')):
    raise RuntimeError('Expected original gateway/service state not restored')
(root/'cleanup.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report),flush=True)
