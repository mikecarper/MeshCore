from pathlib import Path
import subprocess, sys, tempfile
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'test'))
from test_lr2021_apply_transaction import method
source=(ROOT/'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
sigs=['bool labScannerSetRxPreamble(', 'mesh::RadioParamApplyResult labPauseScanner(', 'bool labQuiesceForReset()', 'bool labScannerStop()',
      'mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t p, bool reply)']
parts=[method(source,x).replace(' override','') for x in sigs]
code=r'''
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/W12PramScannerCodec.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_UNKNOWN -1
#define P_LORA_DIO_1 14
#define HIGH 1
static bool pin_high=false; int digitalRead(int){return pin_high ? HIGH : 0;}
static uint32_t tick=100; uint32_t micros(){return tick+=10;} uint32_t millis(){return tick;}
namespace mesh {enum class RadioParamApplyResult {APPLIED,BUSY,FAILED};}
struct RadioLibWrapper {
  mesh::RadioParamApplyResult prepareTransmitProfile(uint8_t,bool){return mesh::RadioParamApplyResult::APPLIED;}
};
struct Fake: RadioLibWrapper {
  bool _lab_scan_active=true,_lab_uncertain=false,_lab_resume_pending=false,_lab_session_ldo=true; uint8_t _lab_rx_preamble[2]={};
  bool pending=false,chipbusy=false,warm=true,reinit=true; int state=1, recoveries=0, calls=0; uint8_t admission=0;
  int16_t command_error=0; int target=-1; uint32_t _lab_pause_last=0,_lab_pause_min=UINT32_MAX,_lab_pause_max=0,_lab_tx_pauses=0,_lab_resume_at=0;
  mesh::w12pram::Status reply={};
  struct {bool canTransmit(uint8_t,bool){return true;}} _profiles;
  bool hasPendingRadioInterrupt(){return pending;} bool isChipBusy(){return chipbusy;}
  int16_t labScannerCommand(uint8_t op,const uint8_t*,size_t,mesh::w12pram::Status& out){assert(op==6);calls++;out=reply;return command_error;}
  bool labWarmRestore(uint8_t p){target=p;if(warm)_lab_scan_active=false;return warm;}
  void labMarkUncertain(){_lab_uncertain=true;}
  uint8_t beginReconfigure(){return admission;}
  bool labReinitialize(){return reinit;}
  @RXPRE@
  @PAUSE@
  @QUIESCE@
  @STOP@
  @PREPARE@
};
int main(){using R=mesh::RadioParamApplyResult;
  {Fake f; assert(!f.labScannerSetRxPreamble(1,32)); f._lab_scan_active=false; assert(f.labScannerSetRxPreamble(2,32) && f._lab_rx_preamble[1]==32); assert(f.labScannerSetRxPreamble(2,0)); assert(!f.labScannerSetRxPreamble(0,32) && !f.labScannerSetRxPreamble(1,7) && !f.labScannerSetRxPreamble(1,256)); f._lab_resume_pending=true; assert(!f.labScannerSetRxPreamble(1,32));}
  {Fake f; f.pending=true;assert(f.labPauseScanner()==R::BUSY && f.calls==0 && f.recoveries==0);}
  {Fake f; f.chipbusy=true;assert(f.labPauseScanner()==R::BUSY && f.calls==0);}
  {Fake f; f.command_error=-705;assert(f.labPauseScanner()==R::FAILED && f.recoveries==0 && f._lab_uncertain);}
  {Fake f; f.reply.hooked=1;f.reply.state=2;assert(f.labPauseScanner()==R::BUSY && f.recoveries==0);}
  {Fake f; f.reply.hooked=0;f.reply.state=2;assert(f.labPauseScanner()==R::FAILED && f.recoveries==0 && f._lab_uncertain);}
  {Fake f; f.reply.stopped=true;f.reply.hooked=1;assert(f.labPauseScanner()==R::FAILED && f.recoveries==0 && f._lab_uncertain);}
  {Fake f; f.reply.stopped=true;f.warm=false;f._lab_uncertain=true;assert(f.labPauseScanner()==R::BUSY && f.recoveries==0);}
  {Fake f; f.reply.stopped=true;f.warm=false;assert(f.labPauseScanner()==R::FAILED && f.recoveries==0 && f._lab_uncertain);}
  {Fake f; f.reply.stopped=true;assert(f.prepareTransmitProfile(1,false)==R::APPLIED);assert(!f._lab_scan_active&&f.target==1&&f._lab_resume_pending&&f._lab_tx_pauses==1&&f._lab_pause_last>0);}
  {Fake f; f._lab_uncertain=true;assert(f.prepareTransmitProfile(0,false)==R::BUSY && f.calls==0);}
  {Fake f; f.state=3;f._lab_resume_pending=true;assert(!f.labScannerStop()&&!f._lab_resume_pending&&f.calls==0);}
  {Fake f; f.admission=2;assert(!f.labQuiesceForReset()&&!f._lab_uncertain);}
  {Fake f; f.pending=true;assert(!f.labQuiesceForReset()&&f._lab_uncertain);}
  {Fake f; pin_high=true;assert(!f.labQuiesceForReset()&&f._lab_uncertain);pin_high=false;}
  {Fake f; assert(f.labQuiesceForReset()&&!f._lab_uncertain);}
}
'''
for name,body in zip(['@RXPRE@','@PAUSE@','@QUIESCE@','@STOP@','@PREPARE@'],parts):code=code.replace(name,body)
with tempfile.TemporaryDirectory() as d:
    p=Path(d);(p/'main.cpp').write_text(code)
    r=subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-I',str(ROOT/'src'),str(p/'main.cpp'),'-o',str(p/'test')],capture_output=True,text=True)
    if r.returncode: raise SystemExit(r.stderr)
    subprocess.run([str(p/'test')],check=True)
print('PASS actual host pause/prepare/stop decisions including no-reset uncertainty')
