"""Verify the real LR2021 RX entry uses RXPS hold bookkeeping."""
from pathlib import Path
import subprocess
import tempfile
from test_lr2021_apply_transaction import method
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'src/helpers/radiolib/CustomLR2021Wrapper.h').read_text()
entry=method(source,'int startReceiveMode()').replace(' override','')
code=r"""
#include <cassert>
#include <cstdint>
#define MESH_W12_MULTISF_EXPERIMENT 1
#define MESH_LR2021_RXPS_AVAILABLE 1
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
namespace mesh {
namespace w12multisf { enum class Admission {Ready}; struct Plan{}; }
namespace lr2021 { struct ApplyStatus { enum class Stage {Standby,Receive}; }; }
}
struct Chip { int starts=0; int standby(){return 0;} int startReceive(){++starts;return 0;} };
struct Fake {
 bool _scan_retune=false,_multi_sf_enabled=true,_rx_ps_enabled=true,_rx_ps_armed=false,_rx_ps_continuous_fallback=false;
 int ps_calls=0; Chip chip,*_radio=&chip;
 void invalidateScanTuple() {}
 int recordApply(mesh::lr2021::ApplyStatus::Stage,int value) { return value; }
 mesh::w12multisf::Admission multiSfPlan(mesh::w12multisf::Plan&) { return mesh::w12multisf::Admission::Ready; }
 bool applyMultiSfReceiveTuple(const mesh::w12multisf::Plan&) { return true; }
 int startReceivePowerSaving() { ++ps_calls; _rx_ps_continuous_fallback=_multi_sf_enabled && _rx_ps_enabled;
   _rx_ps_armed=!_multi_sf_enabled && _rx_ps_enabled; return 0; }
 @ENTRY@
};
int main() {
 Fake f; assert(f.startReceiveMode()==0 && f.ps_calls==1);
 assert(f._rx_ps_enabled && f._rx_ps_continuous_fallback && !f._rx_ps_armed && f.chip.starts==0);
 f._multi_sf_enabled=false; assert(f.startReceiveMode()==0 && f.ps_calls==2);
 assert(f._rx_ps_armed && !f._rx_ps_continuous_fallback);
}
""".replace('@ENTRY@',entry)
with tempfile.TemporaryDirectory() as directory:
 p=Path(directory); (p/'test.cpp').write_text(code)
 subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('PASS LR2021 RX entry preserves RXPS hold and rearm')
