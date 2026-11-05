"""Exercise real queue-reservation ownership and observer age handling."""
from pathlib import Path
import os, shutil, subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
class OutboundReservationTest(unittest.TestCase):
 def test_manager_reservations(self):
  cxx=shutil.which('g++') or shutil.which('clang++'); self.assertIsNotNone(cxx)
  flags=['-std=c++17','-Wall','-Wextra','-DMESH_W12_PRAM_SEQ_TX_LAB=1']
  if os.name!='nt': flags+=['-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
  with tempfile.TemporaryDirectory() as d:
   exe=Path(d)/'reservation'
   cmd=[cxx,*flags,'-I',str(ROOT/'test/mocks'),'-I',str(ROOT/'src'),
        str(ROOT/'src/Packet.cpp'),str(ROOT/'src/helpers/StaticPoolPacketManager.cpp'),
        str(ROOT/'test/fixtures/outbound_reservation/main.cpp'),'-o',str(exe)]
   p=subprocess.run(cmd,capture_output=True,text=True); self.assertEqual(p.returncode,0,p.stderr[-12000:])
   env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0')
   p=subprocess.run([str(exe)],capture_output=True,text=True,env=env);self.assertEqual(p.returncode,0,p.stdout+p.stderr)
if __name__=='__main__': unittest.main()

