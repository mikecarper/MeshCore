"""Recheck the fully flashed fixture after the asynchronous boot greeting."""
import json
from pathlib import Path
import sys
root=Path.home()/'hwtest/runs/four-tx-sf10-20260914'
sys.path.insert(0,str(root))
from profile_four_tx_fixture import TXS,RX
from profile_four_tx import open_radio

report=json.loads((root/'deployment.json').read_text())
checks=[]
for t in (*TXS,RX):
    p,info=open_radio(t)
    p.close();checks.append({'target':t['board'],'info':info})
    print(json.dumps(checks[-1]),flush=True)
    if t is RX:
        if info.get('board')!='Heltec V4' or info.get('rx_gain_reg')!=0x94:
            raise RuntimeError('V4 live hardware/gain mismatch')
        report['rx']['after']['info']=info;report['rx']['complete']=True
    elif info.get('sd_fwid')!=t['fwid'] or info.get('board')!=t['board'] or info.get('prepared'):
        raise RuntimeError('TX identity/ready state mismatch')
report['previous_error']=report.pop('error',None)
report['complete']=True;report['post_boot_verification']=checks
(root/'deployment.json').write_text(json.dumps(report,indent=2)+'\n')
