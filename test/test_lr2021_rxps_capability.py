"""Qualified LR2021 board timing must drive the native duty-cycle command."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ROOT / "src"


def compile_cpp(source):
    with tempfile.TemporaryDirectory() as td:
        cpp = Path(td) / "test.cpp"
        exe = Path(td) / "test"
        cpp.write_text(source)
        result = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-I", str(INCLUDE),
                                 str(cpp), "-o", str(exe)], capture_output=True, text=True)
        if result.returncode == 0:
            result = subprocess.run([str(exe)], capture_output=True, text=True)
        return result


class LR2021RxpsCapabilityTest(unittest.TestCase):
    def test_cr4_armed_airtime_uses_cached_tuple_without_spi(self):
        source = (INCLUDE / 'helpers/radiolib/CustomLR2021Wrapper.h').read_text()
        guard = source.index('_rx_ps_armed && _params_valid && _active_profile < 2')
        start = source.rfind('  uint32_t getEstAirtimeFor(', 0, guard)
        end = source.index('\n  }', guard) + len('\n  }')
        method = source[start:end]
        harness = r'''
#include <cassert>
#include <cstdint>
#include <initializer_list>
struct RadioLibWrapper {
  unsigned driver_calls=0;
  virtual uint32_t getEstAirtimeFor(int) { ++driver_calls; return 99; }
};
struct Wrapper : RadioLibWrapper {
  bool _rx_ps_armed=true, _params_valid=true;
  uint8_t _active_profile=0, _cur_cr=4;
  unsigned profile_calls=0;
  uint32_t getProfileAirtime(uint8_t p,int len,uint8_t cr) {
    assert(p==0 && len==255 && cr==_cur_cr); ++profile_calls; return 1772;
  }
@METHOD@
};
int main() {
  Wrapper w;
  for (uint8_t cr : {4,5,6,7,8}) {
    w._cur_cr=cr;
    assert(w.getEstAirtimeFor(255)==1772 && w.driver_calls==0);
  }
  assert(w.profile_calls==5);
  for (uint8_t cr : {0,3,9,255}) {
    w._cur_cr=cr; assert(w.getEstAirtimeFor(255)==99);
  }
  w._cur_cr=4; w._params_valid=false;
  assert(w.getEstAirtimeFor(255)==99);
  w._params_valid=true; w._active_profile=2;
  assert(w.getEstAirtimeFor(255)==99);
  w._active_profile=0; w._rx_ps_armed=false;
  assert(w.getEstAirtimeFor(255)==99 && w.profile_calls==5 && w.driver_calls==7);
}
'''.replace('@METHOD@', method)
        result = compile_cpp(harness)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_board_opt_in_requires_qualified_transition(self):
        include = '#include <helpers/radiolib/LR2021RxpsCapability.h>\n'
        cases = [
            ('#define MESH_TRACKER_X1 1\n#define USE_LR2021 1\n', 'static_assert(!MESH_LR2021_RXPS_AVAILABLE);', True),
            ('#define MESHNOLOGY_W12 1\n#define MESH_W12_RXPS_EXPERIMENT 1\n#define MESH_W12_PRAM_EXPERIMENT 1\n',
             'static_assert(MESH_LR2021_RXPS_AVAILABLE && MESH_LR2021_RXPS_TRANSITION_US == 1000);', True),
            ('#define MESH_LR2021_RXPS_ENABLE 1\n', '', False),
            ('#define MESH_LR2021_RXPS_ENABLE 1\n#define MESH_LR2021_RXPS_QUALIFIED 1\n', '', False),
            ('#define MESH_LR2021_RXPS_ENABLE 1\n#define MESH_LR2021_RXPS_QUALIFIED 1\n#define MESH_LR2021_RXPS_TRANSITION_US 999\n', '', False),
            ('#define MESH_LR2021_RXPS_ENABLE 1\n#define MESH_LR2021_RXPS_QUALIFIED 1\n#define MESH_LR2021_RXPS_TRANSITION_US 6000\n',
             'static_assert(MESH_LR2021_RXPS_AVAILABLE && MESH_LR2021_RXPS_HOLD_AVAILABLE);', True),
        ]
        for defines, assertion, ok in cases:
            with self.subTest(defines=defines):
                result = compile_cpp(defines + include + assertion + '\nint main() {}')
                self.assertEqual(result.returncode == 0, ok, result.stderr)

    def test_native_driver_uses_selected_transition_without_pram(self):
        source = (INCLUDE / 'helpers/radiolib/CustomLR2021.h').read_text()
        method = source[source.index('    int16_t labStartRxDutyCycle('):
                        source.index('#endif // MESH_LR2021_RXPS_AVAILABLE', source.index('    int16_t labStartRxDutyCycle('))]
        harness = r'''
#define MESH_LR2021_RXPS_ENABLE 1
#define MESH_LR2021_RXPS_QUALIFIED 1
#define MESH_LR2021_RXPS_TRANSITION_US 6000
#include <helpers/radiolib/LR2021RxpsCapability.h>
#include <helpers/radiolib/W12RxDutyTiming.h>
#include <cassert>
#include <cstddef>
using RadioLibIrqFlags_t = uint32_t;
constexpr int RADIOLIB_ERR_NONE=0, RADIOLIB_ERR_INVALID_RX_PERIOD=-1;
constexpr int RADIOLIB_LR2021_RX_TIMEOUT_INF=0xFFFFFF;
constexpr int RADIOLIB_RADIO_MODE_RX=1, RADIOLIB_LR2021_CMD_SET_RX_DUTY_CYCLE=0x0210;
struct RadioModeConfig_t { struct { uint32_t timeout=0, irqFlags=0, irqMask=0; } receive; };
struct Driver {
  int calls=0; uint8_t bytes[7]={};
  int stageMode(int, RadioModeConfig_t*) { return 0; }
  int SPIcommand(int, bool, uint8_t* data, size_t n) { ++calls; for (size_t i=0;i<n;++i) bytes[i]=data[i]; return 0; }
METHOD
};
int main() {
  Driver d;
  assert(d.labStartRxDutyCycle(65625,6000,1,1)==-1 && d.calls==0);
  assert(d.labStartRxDutyCycle(65625,60000,1,1)==0 && d.calls==1);
  mesh::w12rxps::Timing expected;
  assert(mesh::w12rxps::encode(65625,60000,6000,expected));
  uint32_t cycle=(uint32_t(d.bytes[3])<<16)|(uint32_t(d.bytes[4])<<8)|d.bytes[5];
  assert(cycle==expected.cycle_ticks);
}
'''.replace('METHOD', method)
        result = compile_cpp(harness)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
