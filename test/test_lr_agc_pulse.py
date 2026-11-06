"""Compile actual LR chip commands/FSM and PRAM admission against fake hardware."""
from pathlib import Path
import os,re,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]

STATUS_TRANSPORT = r'''
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <RadioLib.h>
struct Hal : RadioLibHal {
 Hal():RadioLibHal(0,1,0,1,1,0){}
 Module* mod=nullptr;bool busy=false;uint32_t time=0;unsigned transfers=0;
 uint8_t status=4,chipmode=8;uint32_t irq=0x00040020;
 Module::BitWidth_t observed=Module::BITS_16;
 void pinMode(uint32_t,uint32_t)override{}
 void digitalWrite(uint32_t,uint32_t)override{}
 uint32_t digitalRead(uint32_t)override{return busy;}
 void attachInterrupt(uint32_t,void(*)(),uint32_t)override{}
 void detachInterrupt(uint32_t)override{}
 void delay(RadioLibTime_t ms)override{time+=ms;}
 void delayMicroseconds(RadioLibTime_t)override{}
 RadioLibTime_t millis()override{return time;}
 RadioLibTime_t micros()override{return time*1000;}
 void yield()override{++time;}
 long pulseIn(uint32_t,uint32_t,RadioLibTime_t)override{return 0;}
 void spiBegin()override{}
 void spiBeginTransaction()override{}
 void spiTransfer(uint8_t* out,size_t size,uint8_t* in)override{
  assert(size==6 || size==7 || size==8);for(size_t i=0;i<size;++i)assert(out[i]==0);
  observed=mod->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS];
  memset(in,0,size);in[0]=status;in[1]=chipmode;
  in[2]=uint8_t(irq>>24);in[3]=uint8_t(irq>>16);in[4]=uint8_t(irq>>8);in[5]=uint8_t(irq);
  ++transfers;
 }
 void spiEndTransaction()override{}
 void spiEnd()override{}
};
struct Radio {
 Module* mod;
 @GET_STATUS@
 static @STATUS_PARSER@
 @PULSE_STATUS@
};
static void layout(const Module& mod,const Module::SPIConfig_t& before){
 for(unsigned i=0;i<3;++i)assert(mod.spiConfig.widths[i]==before.widths[i]);
 for(unsigned i=0;i<4;++i)assert(mod.spiConfig.cmds[i]==before.cmds[i]);
 assert(mod.spiConfig.statusPos==before.statusPos);
 assert(mod.spiConfig.parseStatusCb==before.parseStatusCb);
 assert(mod.spiConfig.checkStatusCb==before.checkStatusCb);
 assert(mod.spiConfig.timeout==before.timeout && mod.spiConfig.stream==before.stream);
}
int main(){
 Hal hal;Module mod(&hal,1,2,3,4);hal.mod=&mod;
 const auto initialWidth=Module::BitWidth_t(TEST_LR_STATUS_WIDTH);
 mod.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS]=initialWidth;
 mod.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_CMD]=Module::BITS_16;
 mod.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_ADDR]=Module::BITS_24;
 mod.spiConfig.cmds[RADIOLIB_MODULE_SPI_COMMAND_NOP]=0;
 mod.spiConfig.statusPos=0;mod.spiConfig.parseStatusCb=Radio::SPIparseStatus;
 mod.spiConfig.checkStatusCb=nullptr;mod.spiConfig.timeout=5;
 Radio radio{&mod};uint8_t first=0,second=0;uint32_t irq=0;
 // Reproduce each family's pinned raw-reader displacement at its real default.
 assert(radio.getStatus(&first,&second,&irq)==0);
 assert(hal.observed==initialWidth);
 if(initialWidth==Module::BITS_16)assert(first==0 && second==4);
 else assert(first==8 && second==0);
 struct Case{uint8_t status;int16_t result;};
 for(Case c:{Case{4,0},{5,0},{6,0},{7,0},
             {0,RADIOLIB_ERR_SPI_CMD_FAILED},{2,RADIOLIB_ERR_SPI_CMD_INVALID},
             {0xff,RADIOLIB_ERR_CHIP_NOT_FOUND},
             {8,RADIOLIB_ERR_SPI_CMD_FAILED},{10,RADIOLIB_ERR_SPI_CMD_FAILED},
             {12,RADIOLIB_ERR_SPI_CMD_FAILED},{14,RADIOLIB_ERR_SPI_CMD_FAILED}}){
  hal.status=c.status;
  for(Module::BitWidth_t width:{Module::BITS_0,Module::BITS_8,Module::BITS_16,Module::BITS_24,Module::BITS_32}){
   mod.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS]=width;
   const auto before=mod.spiConfig;const unsigned count=hal.transfers;uint8_t mode=0;
   assert(radio.agcPulseStatus(irq,mode)==c.result);
   assert(hal.transfers==count+1 && hal.observed==Module::BITS_0);
   assert(irq==hal.irq && mode==4);layout(mod,before);
  }
 }
 hal.status=4;hal.busy=true;
 for(Module::BitWidth_t width:{Module::BITS_0,Module::BITS_16}){
  mod.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS]=width;
  const auto before=mod.spiConfig;const unsigned count=hal.transfers;uint8_t mode=0;
  assert(radio.agcPulseStatus(irq,mode)==RADIOLIB_ERR_SPI_CMD_TIMEOUT);
  assert(hal.transfers==count);layout(mod,before);
 }
}
'''

def method(source,name):
 start=source.index(name);start=source.rfind('\n',0,start)+1
 brace=source.index('{',start);depth=1;i=brace+1
 while depth:
  depth+=(source[i]=='{')-(source[i]=='}');i+=1
 return source[start:i]

class Tests(unittest.TestCase):
 def test_LR11xx_and_LR2021_status_use_real_pinned_Module_transport(self):
  pin=re.search(r'jgromes/RadioLib.git#([0-9a-f]{40})',(ROOT/'platformio.ini').read_text()).group(1)
  candidates=sorted((ROOT/'.pio/libdeps').glob('*/RadioLib'))
  if os.environ.get('MESH_TEST_RADIOLIB_DIR'):candidates.insert(0,Path(os.environ['MESH_TEST_RADIOLIB_DIR']))
  lib=None
  for candidate in candidates:
   found=subprocess.run(['git','-C',str(candidate),'rev-parse','HEAD'],capture_output=True,text=True)
   if found.returncode==0 and found.stdout.strip()==pin:lib=candidate/'src';break
  if lib is None:self.skipTest('Install the pinned RadioLib dependency to verify its real SPI transport')
  unchanged=subprocess.run(['git','-C',str(lib.parent),'diff','--quiet','HEAD','--',
                           'src/Module.cpp','src/Hal.cpp','src/modules/LR11x0/LR_common.cpp'],capture_output=True)
  self.assertEqual(unchanged.returncode,0,'Pinned transport source was modified')
  common=(lib/'modules/LR11x0/LR_common.cpp').read_text()
  raw=method(common,'int16_t LRxxxx::getStatus(').replace('LRxxxx::','')
  parser=method(common,'int16_t LRxxxx::SPIparseStatus(').replace('LRxxxx::','')
  pulse=method((ROOT/'src/helpers/radiolib/LRxxxxAGCChip.inc').read_text(),'int16_t agcPulseStatus(')
  source=STATUS_TRANSPORT.replace('@GET_STATUS@',raw).replace('@STATUS_PARSER@',parser).replace('@PULSE_STATUS@',pulse)
  # LR11xx uses an eight-bit prefix and LR2021 a sixteen-bit prefix. Both
  # inherit this exact pinned raw reader, which must clock an unprefixed reply.
  with tempfile.TemporaryDirectory(prefix='lr-status-transport-')as tmp:
   cpp=Path(tmp)/'test.cpp';cpp.write_text(source)
   for family,width in(('LR11x0',8),('LR2021',16)):
    with self.subTest(family=family):
     setup=(lib/('modules/'+family+'/'+family+'.cpp')).read_text()
     self.assertIn('spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = Module::BITS_'+str(width)+';',setup)
     binary=Path(tmp)/family
     built=subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-O1',
       '-DRADIOLIB_GODMODE=1','-DRADIOLIB_STATIC_ONLY=1','-DTEST_LR_STATUS_WIDTH='+str(width),
       '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',
       '-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-I'+str(lib),str(cpp),
       str(lib/'Module.cpp'),str(lib/'Hal.cpp'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
     self.assertEqual(built.returncode,0,built.stdout+built.stderr)
     ran=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
     self.assertEqual(ran.returncode,0,ran.stdout+ran.stderr)

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
