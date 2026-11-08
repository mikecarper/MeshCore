"""Expose the precise last LR2021 device-fault word in diagnostic builds."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from cpp_source import body
from test_lr2021_receiver_reset import HARNESS

ROOT=Path(__file__).resolve().parents[1]
MAIN=r"""
int main() {
  frequencies(); failures(); frontend_coverage();
  using Device=mesh::lr2021::CalibrationDeviceStatus;
  auto& device=mesh::lr2021::calibrationDeviceStatus();
  AGCTimingDiagnostics timing;
  char reply[160];
  for(unsigned phase=1;phase<=3;++phase) {
    CustomLR2021 chip;chip.errors={0,0,0};
    chip.errors[phase-1]=0x0001;
    assert(chip.resetReceiverCalibration()==RADIOLIB_ERR_FRONTEND_CALIBRATION_FAILED);
    assert(device.errors==0x0001 && device.phase==phase && device.valid);
    timing.format(2,reply,sizeof(reply));
    assert(strstr(reply,"dev=0001") && strstr(reply,"dvalid=1"));
    char expected[16];snprintf(expected,sizeof(expected),"dphase=%u",phase);
    assert(strstr(reply,expected));
  }
  CustomLR2021 unreadable;unreadable.fail_at=0;
  assert(unreadable.resetReceiverCalibration()==-706);
  assert(device.phase==Device::Before && !device.valid);
  timing.format(2,reply,sizeof(reply));assert(strstr(reply,"dvalid=0"));
  CustomLR2021 good;assert(good.resetReceiverCalibration()==0);
  assert(device.errors==0 && device.phase==Device::AfterFrontEnd && device.valid);
  timing.format(2,reply,sizeof(reply));
  assert(strstr(reply,"dev=0000,dphase=3,dvalid=1"));
  for(size_t capacity=1;capacity<=sizeof(reply);++capacity) {
    char guarded[162];memset(guarded,0x5a,sizeof(guarded));
    timing.format(2,guarded+1,capacity);
    assert(guarded[0]==0x5a && guarded[capacity+1]==0x5a);
    assert(memchr(guarded+1,0,capacity));
  }
}
"""

class LR2021CalibrationDeviceDiagnosticsTests(unittest.TestCase):
    def test_device_fault_phase_validity_and_bounded_cli_reply(self):
        helper=body((ROOT/'src/helpers/radiolib/CustomLR2021.h').read_text(),
                    'int16_t resetReceiverCalibration(')
        source=('#include <cstring>\n#include <cstdio>\n'
                '#include <helpers/radiolib/AGCTimingDiagnostics.h>\n'+
                HARNESS.replace('@HELPER@',helper).replace(
                    'int main(){frequencies();failures();frontend_coverage();}',MAIN))
        with tempfile.TemporaryDirectory(prefix='lr2021-device-diag-') as directory:
            root=Path(directory);(root/'test.cpp').write_text(source)
            binary=root/'test'
            built=subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror',
                '-DMESH_AGC_TIMING_DIAGNOSTICS=1','-DUSE_LR2021=1',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',
                '-I',str(ROOT/'src'),str(root/'test.cpp'),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            ran=subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)

if __name__=='__main__':unittest.main()
