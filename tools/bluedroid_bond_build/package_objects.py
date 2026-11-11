"""Package only the three rebuilt objects; never redistribute the whole SDK."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

NAMES = ('btc_dm.c.obj', 'bta_dm_act.c.obj', 'btm_sec.c.obj')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ar', required=True)
    p.add_argument('--stock', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    with tempfile.TemporaryDirectory(prefix='mesh-bt-package-') as temp:
        root = Path(temp)
        for name, archive in [('stock', a.stock), ('candidate', a.candidate)]:
            dest = root/name
            dest.mkdir()
            subprocess.run([a.ar, 'x', str(archive.resolve())], cwd=dest, check=True)
        stock = {f.name: sha(f) for f in (root/'stock').iterdir()}
        candidate = {f.name: sha(f) for f in (root/'candidate').iterdir()}
        if stock.keys() != candidate.keys() or set(n for n in stock if stock[n] != candidate[n]) != set(NAMES):
            raise ValueError('unexpected archive member changes')
        a.output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(a.output, 'x', compression=ZIP_DEFLATED) as z:
            for n in NAMES:
                entry = ZipInfo(n, (2026, 1, 1, 0, 0, 0))
                entry.compress_type = ZIP_DEFLATED
                z.writestr(entry, (root/'candidate'/n).read_bytes())
        print(json.dumps({'stock_sha256': sha(a.stock), 'patched_sha256': sha(a.candidate),
                         'package_sha256': sha(a.output), 'objects': {n:candidate[n] for n in NAMES}}, indent=2))

if __name__ == '__main__':
    main()
