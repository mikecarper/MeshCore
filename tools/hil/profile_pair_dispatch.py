"""Deploy the reviewed three-radio bundle; no RF test yet."""
from pathlib import Path
import subprocess
root=Path.home()/'hwtest/runs/pair-sf8-32-20260914'
raise SystemExit(subprocess.run(['python3','-u',str(root/'profile_pair_deploy.py')],cwd=root).returncode)
