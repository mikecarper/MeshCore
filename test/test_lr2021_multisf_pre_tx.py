"""Exercise definite single-profile pre-transmit failures and ambiguous start."""
from pathlib import Path
import subprocess
import tempfile
from test_lr2021_apply_transaction import method
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'src/helpers/radiolib/CustomLR2021Wrapper.h').read_text()
start=method(source,'bool startSendRaw(').replace(' override','')
code=r"""
#include <cassert>
#include <cstdint>
#define MESH_W12_PRAM_EXPERIMENT 1
#define MESH_W12_PRAM_SEQUENTIAL_LAB 1
#define MESH_W12_PRAM_SEQ_TX_LAB 1
#define MESH_W12_MULTISF_EXPERIMENT 1
#define RADIOLIB_ERR_NONE 0
namespace mesh {
enum class PreTransmitBlock { None, Busy, Error };
namespace w12multisf { enum class Admission { Ready, Unsupported }; struct Plan { uint8_t tx_sf=7; }; }
}
struct RadioLibWrapper {
 bool result=true; int starts=0;
 bool startSendRaw(const uint8_t*,int) { ++starts; return result; }
};
struct CustomLR2021 {
 bool bad=false;
 int setSideDetector(void*,int) { return bad ? -1 : 0; }
 int setSpreadingFactor(uint8_t) { return 0; }
 int setPreambleLength(uint16_t) { return 0; }
};
struct Fake : RadioLibWrapper {
 bool _lab_seq_requested=false,_lab_scan_active=false,_lab_uncertain=false,_multi_sf_enabled=true;
 bool _profile_refresh_required=false,_profile_repair_required=false;
 bool plan_ok=true, restored=true;
 int resume=1;
 uint32_t _lab_seq_tx_busy=0;
 mesh::PreTransmitBlock _lab_seq_tx_block=mesh::PreTransmitBlock::None;
 CustomLR2021 chip,*_radio=&chip;
 bool labSequentialTxClear() { return true; }
 void invalidateScanTuple() {}
 mesh::w12multisf::Admission multiSfPlan(mesh::w12multisf::Plan&) const {
   return plan_ok ? mesh::w12multisf::Admission::Ready : mesh::w12multisf::Admission::Unsupported;
 }
 uint8_t beginReconfigure() { return resume; }
 uint16_t currentPreambleLength() { return 32; }
 bool restoreAfterDeepInit() { return restored; }
 void endReconfigure(uint8_t) {}
 bool isInRecvMode() { return true; }
 bool hasPendingRadioInterrupt() { return false; }
 void startRecv() {}
 @START@
};
int main() {
 uint8_t packet[1]={0};
 Fake plan; plan.plan_ok=false; assert(!plan.startSendRaw(packet,1));
 assert(plan._lab_seq_tx_block==mesh::PreTransmitBlock::Error && plan.starts==0);
 Fake busy; busy.resume=2; assert(!busy.startSendRaw(packet,1));
 assert(busy._lab_seq_tx_block==mesh::PreTransmitBlock::Busy && busy.starts==0);
 Fake fail; fail.chip.bad=true; assert(!fail.startSendRaw(packet,1));
 assert(fail._lab_seq_tx_block==mesh::PreTransmitBlock::Error && fail.starts==0);
 Fake ambiguous; ambiguous.result=false; assert(!ambiguous.startSendRaw(packet,1));
 assert(ambiguous._lab_seq_tx_block==mesh::PreTransmitBlock::None && ambiguous.starts==1);
 Fake ok; assert(ok.startSendRaw(packet,1));
 assert(ok._lab_seq_tx_block==mesh::PreTransmitBlock::None && ok.starts==1);
}
""".replace('@START@',start)
with tempfile.TemporaryDirectory() as directory:
    p=Path(directory); (p/'test.cpp').write_text(code)
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS single-profile pre-TX classification and ambiguous start')
