"""Read-only MercerMesh tool, privilege and service preflight."""
import json
import os
import shutil
import subprocess
from pathlib import Path

for name in ('python3', 'esptool', 'adafruit-nrfutil', 'mount', 'fuser', 'lsblk'):
    paths = [shutil.which(name), str(Path.home()/'.local/bin'/name)]
    print(json.dumps({'tool': name, 'paths': [p for p in paths if p and os.path.exists(p)]}), flush=True)
for command in (['sudo', '-n', 'true'],
                ['systemctl', 'show', 'meshcore-memory-soak.service', '-p', 'ActiveState', '-p', 'ExecStart'],
                ['lsblk', '-J', '-o', 'NAME,PATH,FSTYPE,MOUNTPOINTS,TYPE'],
                ['ls', '-la', '/home/mikec/hwtest']):
    r = subprocess.run(command, text=True, capture_output=True, timeout=15)
    print(json.dumps({'command': command, 'rc': r.returncode, 'out': r.stdout, 'err': r.stderr}), flush=True)
