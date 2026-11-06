"""Compile actual LR chip commands/FSM and PRAM admission against fake hardware."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]

def method(source,name):
 start=source.index(name);start=source.rfind('\n',0,start)+1
 brace=source.index('{',start);depth=1;i=brace+1
 while depth:
  depth+=(source[i]=='{')-(source[i]=='}');i+=1
 return source[start:i]

class Tests(unittest.TestCase):
 def test_actual_PRAM_owner_reentry_preserves_late_packet(self):
  text=(ROOT/'src/helpers/radiolib/W12PramScannerLab.inc').read_text()
  branch=method(text,'if(_agc_pram_restore_owned)')
  source='''#include <cassert>
#define RADIOLIB_ERR_NONE 0
struct Owner {
 bool _agc_pram_restore_owned=true,busy=false,irq=false,receiving=false,late=false;
 int calls=0,error=0;
 bool isChipBusy(){return busy;}
 bool hasPendingRadioInterrupt(){return irq;}
 bool isReceivingPacket(){return receiving;}
 int standby(){++calls;if(late)irq=true;return error;}
 bool admit(){auto* r=this; @BRANCH@ return true;}
};
int main(){
 {Owner o;assert(o.admit()&&o.calls==1);}
 {Owner o;o.busy=true;assert(!o.admit()&&!o.calls);}
 {Owner o;o.irq=true;assert(!o.admit()&&!o.calls);}
 {Owner o;o.receiving=true;assert(!o.admit()&&!o.calls);}
 {Owner o;o.late=true;assert(!o.admit()&&o.irq&&o.calls==1);}
 {Owner o;o.error=-9;assert(!o.admit()&&o.calls==1);}
}
'''.replace('@BRANCH@',branch)
  with tempfile.TemporaryDirectory()as tmp:
   cpp=Path(tmp)/'test.cpp';cpp.write_text(source);binary=Path(tmp)/'test'
   subprocess.run(['g++','-std=c++17',str(cpp),'-o',str(binary)],check=True,capture_output=True,text=True)
   subprocess.run([str(binary)],check=True,capture_output=True,text=True)

 def test_actual_LR11xx_and_LR2021_command_FSM_PRAM_ownership(self):
  folder=ROOT/'src/helpers/radiolib'
  production=(folder/'LRxxxxPulseWrapper.h').read_text().replace('#include "RadioLibWrappers.h"','')
  wrapper=(folder/'CustomLR2021Wrapper.h').read_text()
  fixture=(ROOT/'test/fixtures/lr_agc_pulse/main.cpp').read_text().replace('#include "ACTUAL_LR_WRAPPER"',production)
  fixture=fixture.replace(' ACTUAL_PRAM_PERIODIC',method(wrapper,'mesh::AGCMaintenanceResult periodicAGCMaintenance('))
  fixture=fixture.replace(' ACTUAL_PRAM_FINISH',method(wrapper,'int16_t finishAGCPulseOwner('))
  with tempfile.TemporaryDirectory()as tmp:
   source=Path(tmp)/'test.cpp';source.write_text(fixture)
   for family in(0,1):
    with self.subTest(LR2021=family):
     binary=Path(tmp)/('run'+str(family))
     subprocess.run(['g++','-std=c++17','-fsanitize=address,undefined,float-cast-overflow','-fno-omit-frame-pointer','-g','-I'+str(folder),'-DMC_LR_AGC_2021='+str(family),str(source),'-o',str(binary)],check=True,capture_output=True,text=True)
     subprocess.run([str(binary)],check=True,capture_output=True,text=True)

if __name__=='__main__':unittest.main()
