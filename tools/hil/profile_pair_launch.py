"""Launch the bounded pair collector only once; inspect status separately."""
import hashlib
import json
from pathlib import Path
import subprocess
root=Path.home()/'hwtest/runs/pair-sf8-32-20260914'
if (root/'launch.json').exists() or (root/'results.json').exists():raise RuntimeError('Already launched')
if not json.loads((root/'deployment.json').read_text()).get('complete'):raise RuntimeError('Deployment incomplete')
manifest=json.loads((root/'manifest.json').read_text())
for name in ('profile_pair.py','profile_pair_run.py','profile_switch.py','profile_four_tx_fixture.py'):
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=manifest['files'][name]['sha256']:
        raise RuntimeError('Collector/dependency changed')
with (root/'collector.log').open('x') as log:
    process=subprocess.Popen(['python3','-u',str(root/'profile_pair_run.py')],cwd=root,
                             stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
record={'pid':process.pid,'path':str(root),'collector_sha256':manifest['files']['profile_pair.py']['sha256']}
(root/'launch.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
