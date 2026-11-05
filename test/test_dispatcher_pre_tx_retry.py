from pathlib import Path
import os,subprocess,tempfile
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as d:
 exe=Path(d)/'dispatcher'
 flags=['-std=c++17','-DMESH_W12_PRAM_SEQ_TX_LAB=1']
 if os.name!='nt': flags+=['-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
 files=['src/Dispatcher.cpp','src/Packet.cpp','src/helpers/StaticPoolPacketManager.cpp','test/fixtures/pre_tx_retry/main.cpp']
 subprocess.run(['g++',*flags,'-I',str(root/'test/mocks'),'-I',str(root/'src'),*[str(root/f) for f in files],'-o',str(exe)],check=True,capture_output=True)
 subprocess.run([str(exe)],check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
print('PASS actual dispatcher busy fairness, bounded retry, and ambiguous retry limit')
