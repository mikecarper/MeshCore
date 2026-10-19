"""Execute owned IQ-workaround reuse, wire bytes, revocation and failures."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method
import test_sx1262_modem_cache as modem_cache


ROOT = Path(__file__).resolve().parents[1]


class ProfileIqCacheTest(unittest.TestCase):
    def test_only_qualified_recipes_opt_in(self):
        opt_ins = []
        for recipe in (ROOT / "variants").glob("*/platformio.ini"):
            if re.search(r"^\s*-D\s+MC_SX1262_FAST_IQ_CACHE=1\s*$",
                         recipe.read_text(), re.MULTILINE):
                opt_ins.append(recipe.relative_to(ROOT).as_posix())
        self.assertCountEqual(opt_ins, ["variants/sensecap_indicator-espnow/platformio.ini",
                                       "variants/lilygo_tdeck/platformio.ini"])

    def compile_run(self, source):
        with tempfile.TemporaryDirectory() as folder:
            cpp = Path(folder) / "test.cpp"
            binary = Path(folder) / ("test.exe" if os.name == "nt" else "test")
            cpp.write_text(source, encoding="ascii")
            sanitizers = [] if os.name == "nt" else [
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
            built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17",
                "-Wall", "-Wextra", *sanitizers, str(cpp), "-o", str(binary)],
                capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_owned_iq_ack_wire_payload_setters_lifecycle_and_errors(self):
        source = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text()
        state = (ROOT / "src/helpers/radiolib/SX1262ProfileSwitchState.h").read_text().replace("#pragma once", "")
        signatures = ("void setProfileSwitchOptimization(", "void beginProfileSwitch(",
                      "void endProfileSwitch(", "int16_t reset(bool", "int16_t sleep(bool",
                      "int16_t stageMode(", "int16_t setPreambleLength(", "int16_t setCRC(",
                      "int16_t implicitHeader(", "int16_t explicitHeader()", "int16_t invertIQ(",
                      "int16_t setProfileProtocol(", "int16_t writeRegister(",
                      "int16_t setPacketParams(", "int16_t writeProfilePacketParams(",
                      "int16_t startReceive()", "int16_t startReceiveDutyCycle(")
        methods = "\n".join(method(source, signature).replace(" override", "") for signature in signatures)
        fixture = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <string>
#include <vector>
@DEFINES@
#define MC_SX1262_FAST_PACKET_CACHE 0
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_SX126X_STANDBY_RC 0
#define RADIOLIB_SX126X_RX_TIMEOUT_INF 0xffffff
#define RADIOLIB_SX126X_PACKET_TYPE_LORA 1
#define RADIOLIB_SX126X_CMD_SET_PACKET_PARAMS 0x8c
#define RADIOLIB_RADIO_MODE_NONE 0
#define RADIOLIB_IRQ_RX_DEFAULT_FLAGS 1
#define RADIOLIB_IRQ_RX_DEFAULT_MASK 2
#define RADIOLIB_IRQ_PREAMBLE_DETECTED 3
#define RADIOLIB_SX126X_LORA_CRC_ON 1
#define RADIOLIB_SX126X_LORA_HEADER_EXPLICIT 0
#define RADIOLIB_SX126X_LORA_IQ_STANDARD 0
using RadioModeType_t=int;
using RadioLibIrqFlags_t=uint32_t;
struct RadioModeConfig_t {};
@STATE@
struct Module {
  enum { MODE_RX=1 };
  std::string* trace;char* fail;std::vector<uint8_t> packet;
  int16_t SPIwriteStream(uint16_t command,const uint8_t* data,size_t size) {
    assert(command==0x8c && size==6);packet.assign(data,data+size);
    *trace+='P';return *fail=='P' ? -100:0;
  }
  void setRfSwitchState(int mode) { assert(mode==MODE_RX);*trace+='W'; }
};
struct SX1262 {
  std::string trace;char fail=0;int modem=1;bool standbyXOSC=true;
  uint16_t preambleLengthLoRa=32;
  uint8_t crcTypeLoRa=1,headerType=0,invertIQEnabled=0;
  size_t implicitLen=255;
  uint32_t rxTimeout=0;int stagedMode=3;float tcxoVoltage=1.8f;
  Module module{&trace,&fail,{}};Module* mod=&module;
  int16_t call(char c) { trace+=c;return c==fail ? -100:0; }
  Module* getMod() { return mod; }
  int16_t standby(uint8_t) { return call('S'); }
  int16_t sleep(bool) { return call('L'); }
  int16_t reset(bool) { return call('Z'); }
  virtual int16_t stageMode(RadioModeType_t,RadioModeConfig_t*) { return call('G'); }
  int16_t setPacketParams(uint16_t p,uint8_t crc,uint8_t len,uint8_t hdr,uint8_t iq) {
    int16_t rc=call('I');if(!rc) rc=call('i');
    const uint8_t data[]={uint8_t(p>>8),uint8_t(p),hdr,len,crc,iq};
    if(!rc) rc=module.SPIwriteStream(0x8c,data,sizeof(data));return rc;
  }
  virtual int16_t setPreambleLength(size_t p) {
    preambleLengthLoRa=p;return setPacketParams(p,crcTypeLoRa,implicitLen,headerType,invertIQEnabled);
  }
  int16_t setCRC(uint8_t crc,uint16_t,uint16_t,bool) {
    crcTypeLoRa=crc;return setPacketParams(preambleLengthLoRa,crc,implicitLen,headerType,invertIQEnabled);
  }
  int16_t implicitHeader(size_t n) {
    implicitLen=n;headerType=1;
    return setPacketParams(preambleLengthLoRa,crcTypeLoRa,implicitLen,headerType,invertIQEnabled);
  }
  int16_t explicitHeader() {
    implicitLen=255;headerType=0;
    return setPacketParams(preambleLengthLoRa,crcTypeLoRa,implicitLen,headerType,invertIQEnabled);
  }
  virtual int16_t invertIQ(bool enable) {
    invertIQEnabled=enable;
    return setPacketParams(preambleLengthLoRa,crcTypeLoRa,implicitLen,headerType,invertIQEnabled);
  }
  int16_t setSyncWord(uint8_t) { return call('Y'); }
  int16_t writeRegister(uint16_t,const uint8_t*,uint8_t) { return call('J'); }
  int16_t startReceive(uint32_t,uint32_t,uint32_t,size_t) {
    int16_t rc=stageMode(1,nullptr);if(!rc) rc=call('F');
    if(!rc) rc=setPacketParams(preambleLengthLoRa,crcTypeLoRa,implicitLen,headerType,invertIQEnabled);
    if(!rc) rc=call('R');return rc;
  }
  int16_t startReceiveDutyCycle(uint32_t,uint32_t,uint32_t,uint32_t) { return call('D'); }
  uint8_t getPacketType() { call('Q');return modem; }
  int16_t clearIrqStatus() { return call('C'); }
  int16_t setRx(uint32_t timeout) { assert(timeout==0xffffff);return call('R'); }
};
struct Radio : SX1262 {
  SX1262ProfileSwitchState _profileSwitch{true};
  bool _coldStandby=true,_tcxoWakePending=false,_rx_ps_rf_rx_disabled=false;
  uint32_t _profileFastResumes=0;
#ifdef MC_SX1262_HIL
  bool hilSkipUnchangedPacket=false,hilSkipUnchangedIq=true;
  uint32_t hilPacketSkips=0,hilIqWorkaroundSkips=0;
#endif
  @METHODS@
};
void prime(Radio& r) {
  assert(r.startReceive()==0);
  assert(r.trace.find("IiP")!=std::string::npos && r.trace.back()=='Q');
  assert(r._profileSwitch.rxValid && r._profileSwitch.packetValid);
  assert(!r._profileSwitch.matchesPacketIq(r.invertIQEnabled));
  r.trace.clear();
}
void arm(Radio& r) {
  r.beginProfileSwitch(true);
  assert(!r._profileSwitch.matchesPacketIq(r.invertIQEnabled));
  r._profileSwitch.standbyResult(0);
}
int main() {
  // Only IQ is reused: changing every other packet field still emits its full
  // six-byte command in the exact SX126x wire order, including both pre bytes.
  for(uint8_t iq:{0,1}) {
    Radio r;r.invertIQEnabled=iq;prime(r);arm(r);
    assert(r._profileSwitch.matchesPacketIq(iq));
    r.preambleLengthLoRa=0x1234;r.crcTypeLoRa=2;r.implicitLen=73;r.headerType=1;
    assert(r.startReceive()==0 && r.trace=="CPWR");
    assert(r.module.packet==std::vector<uint8_t>({0x12,0x34,1,73,2,iq}));
    assert(r._profileSwitch.packetValid && r._profileSwitch.packetPreamble==0x1234);
    r.endProfileSwitch(true);
  }
  // A changed IQ or an unacknowledged packet must run the original workaround.
  for(int gate=0;gate<3;++gate) {
    Radio r;prime(r);arm(r);
    if(gate==0) r.invertIQEnabled=1;
    if(gate==1) r._profileSwitch.invalidatePacket();
#ifdef MC_SX1262_HIL
    if(gate==2) r.hilSkipUnchangedIq=false;
#else
    if(gate==2) continue;
#endif
    assert(r.startReceive()==0 && r.trace=="CIiPWR");
  }
  // Owned preamble staging is the intended fast case, unlike an ordinary
  // preamble command. Every ordinary packet/direct-register path revokes IQ.
  { Radio r;prime(r);arm(r);assert(r.setPreambleLength(64)==0 && r.trace.empty());
    assert(r.startReceive()==0 && r.trace=="CPWR"); }
  for(int setter=0;setter<8;++setter) for(bool failure:{false,true}) {
    Radio r;prime(r);
    if(setter!=4) arm(r);
    r.fail=failure ? (setter==5?'Y':setter==6?'J':'P'):0;
    uint8_t value=4;
    switch(setter) {
      case 0:r.setCRC(2);break;
      case 1:r.implicitHeader(73);break;
      case 2:r.explicitHeader();break;
      case 3:r.invertIQ(false);break;
      case 4:r.setPreambleLength(64);break;
      case 5:r.setProfileProtocol(0x2b);break;
      case 6:r.writeRegister(0x736,&value,1);break;
      case 7:r.setPacketParams(64,1,255,0,0);break;
    }
    assert(!r._profileSwitch.packetValid);
    r.fail=0;r.trace.clear();
    if(failure && setter==5) {
      assert(!r._profileSwitch.rxValid);continue;
    }
    if(setter==4) arm(r);
    assert(!r._profileSwitch.matchesPacketIq(r.invertIQEnabled));
    assert(r.startReceive()==0 && r.trace=="CIiPWR");
  }
  // The acknowledged tuple cannot survive TX/CAD staging, either sleep,
  // reset, RX duty cycling, explicit disable, rollback, or a failed RX command.
  for(int event=0;event<8;++event) for(bool failure:{false,true}) {
    Radio r;prime(r);arm(r);r.fail=failure?'G':0;
    switch(event) {
      case 0:r.stageMode(2,nullptr);break;
      case 1:r.stageMode(3,nullptr);break;
      case 2:r.sleep(true);break;
      case 3:r.sleep(false);break;
      case 4:r.reset();break;
      case 5:r.startReceiveDutyCycle(1000,1000);break;
      case 6:r.setProfileSwitchOptimization(false);break;
      case 7:r.endProfileSwitch(false);break;
    }
    assert(!r._profileSwitch.packetValid && !r._profileSwitch.rxValid);
    assert(!r._profileSwitch.matchesPacketIq(r.invertIQEnabled));
  }
  for(char failure:std::string("CIiPR")) {
    Radio r;prime(r);arm(r);
    if(failure=='I' || failure=='i') r._profileSwitch.invalidatePacket();
    r.fail=failure;assert(r.startReceive()==-100 && r.trace.back()==failure);
    assert(!r._profileSwitch.rxValid && !r._profileSwitch.packetValid);
    r.endProfileSwitch(false);r.fail=0;r.trace.clear();prime(r);
  }
  for(int gate=0;gate<4;++gate) {
    Radio r;prime(r);
    switch(gate) {
      case 0:r.beginProfileSwitch(false);r._profileSwitch.standbyResult(0);break;
      case 1:r.standbyXOSC=false;arm(r);break;
      case 2:r.beginProfileSwitch(true);break;
      case 3:r._profileSwitch.packetResult(-100,32,1,255,0,0);break;
    }
    assert(!r._profileSwitch.matchesPacketIq(0));
  }
}
'''.replace("@STATE@", state).replace("@METHODS@", methods)
        for definitions in ("#define MC_SX1262_FAST_IQ_CACHE 1",
                            "#define MC_SX1262_FAST_IQ_CACHE 0\n#define MC_SX1262_HIL"):
            with self.subTest(definitions=definitions):
                self.compile_run(fixture.replace("@DEFINES@", definitions))

    def test_unqualified_boards_keep_the_original_live_modem_query_and_iq_path(self):
        source = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text()
        self.assertIn("#define MC_SX1262_FAST_IQ_CACHE 0", source)
        self.assertIn("#define MC_SX1262_FAST_MODEM_CACHE 0", source)
        # Execute the shared production-method fixture: default-off builds must
        # query the modem, while opted-in/HIL reuse must retain all ownership,
        # initialization and failure gates. Do not depend on an if's spelling.
        modem_cache.ModemCacheTests.test_confirmed_modem_requires_owned_warm_rx_and_survives_no_failure(self)
        helper = method(source, "int16_t writeProfilePacketParams(")
        self.assertIn("return getMod()->SPIwriteStream(RADIOLIB_SX126X_CMD_SET_PACKET_PARAMS,", helper)


if __name__ == "__main__":
    unittest.main()
