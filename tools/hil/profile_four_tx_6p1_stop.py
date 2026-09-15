"""User-requested stop of only the identified 6.1-symbol collector."""
import json
import os
from pathlib import Path
import signal
root=Path.home()/'hwtest/runs/four-tx-sf10-6p1-20260914'
control=json.loads((root/'run-control.json').read_text())
pid=control['pid']
proc=Path('/proc')/str(pid)
if not proc.exists():
    print(json.dumps({'already_exited':True,'pid':pid}),flush=True)
else:
    argv=(proc/'cmdline').read_bytes().decode().strip('\0').split('\0')
    expected=['python3','-u',str(root/'profile_four_tx.py'),'--dwell-symbols','6.1']
    if argv!=expected or proc.stat().st_uid!=os.getuid():
        raise RuntimeError('PID is not the exact owned 6.1-symbol collector; not stopping it')
    with (root/'user-stop.json').open('x') as f:
        json.dump({'reason':'User requested stop and next test at 7.7 symbols','pid':pid},f)
    os.kill(pid,signal.SIGINT)
    print(json.dumps({'stop_requested':True,'pid':pid,'signal':'SIGINT'}),flush=True)
