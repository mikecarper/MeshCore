"""Run the finite pair test with explicit ModemManager restoration."""
import argparse
import json
from pathlib import Path
import signal
import subprocess

parser=argparse.ArgumentParser()
parser.add_argument('--collector',choices=('profile_pair.py','profile_pair_4p6_collect.py'),default='profile_pair.py')
args=parser.parse_args()
root=Path(__file__).resolve().parent
if (root/'run-control.json').exists():raise RuntimeError('Already started')
if subprocess.run(['systemctl','is-active','--quiet','meshcore-memory-soak.service']).returncode==0:
    raise RuntimeError('Soak active')
mm=subprocess.run(['systemctl','is-active','--quiet','ModemManager.service']).returncode==0
stopped=False;child=None;rc=1
try:
    if mm:
        subprocess.run(['sudo','-n','systemctl','stop','ModemManager.service'],check=True,timeout=15);stopped=True
    child=subprocess.Popen(['python3','-u',str(root/args.collector)],cwd=root)
    (root/'run-control.json').write_text(json.dumps({'pid':child.pid,'collector':args.collector,'modemmanager_before':mm}))
    rc=child.wait(timeout=2400)
finally:
    if child is not None and child.poll() is None:
        child.send_signal(signal.SIGINT)
        try:child.wait(timeout=30)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=10)
    if stopped:subprocess.run(['sudo','-n','systemctl','start','ModemManager.service'],check=True,timeout=15)
    (root/'run-exit.json').write_text(json.dumps({'collector_exit':rc,'modemmanager_restored':not mm or stopped}))
raise SystemExit(rc)
