#!/usr/bin/env python3
from pathlib import Path
import re, subprocess, tempfile, struct
root = Path(__file__).resolve().parents[2]
lab = root / 'lab/pram_scanner'
def run(args):
    subprocess.run(args, cwd=root, check=True)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    source = (lab/'scan.c').read_text()
    case = (lab/'test_pause_guard.cpp').read_text()
    pattern = r'static int lab_can_pause\([^}]+\}'
    assert re.search(pattern, source).group() == re.search(pattern, case).group()
    run(['g++','-std=c++17','-I','src',str(lab/'test_codec.cpp'),'-o',str(tmp/'codec')])
    run([str(tmp/'codec')])
    run(['g++','-std=c++17',str(lab/'test_pause_guard.cpp'),'-o',str(tmp/'guard')])
    run([str(tmp/'guard')])
    run(['g++','-std=c++17','-DMESH_W12_PRAM_EXPERIMENT=1','-I','test/mocks','-I','src',
         str(lab/'test_packet.cpp'),'src/Packet.cpp','-o',str(tmp/'packet')])
    run([str(tmp/'packet')])
    run(['python3',str(lab/'test_handoff.py')])
    run(['python3',str(lab/'test_dispatcher.py')])
    run(['python3',str(lab/'test_rxps_driver.py')])
    run(['python3',str(lab/'test_idle_admission.py')])
    blob = (lab/'scan-safe.bin').read_bytes()
    words = re.findall(r'0x([0-9a-fA-F]{8})u', (root/'src/helpers/radiolib/W12PramScannerBlob.h').read_text())
    assert len(blob) % 4 == 0 and b''.join(struct.pack('<I',int(w,16)) for w in words) == blob
print('PASS native scanner tests and source/blob consistency')
