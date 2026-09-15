"""Check HIL-only optimization boundaries; not full radio lifecycle qualification."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as batched_tests

ROOT = Path(__file__).resolve().parents[1]


class ProfileSwitchExperimentTests(unittest.TestCase):
    compile_run = batched_tests.BatchedModulationTests.compile_run

    def test_rx_omissions_are_scoped_and_commands_preserved(self):
        source = (ROOT / "tools/hil/profile_switch_experiments.h").read_text()
        source = source[source.index("class ExperimentalSX1262") :]
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <string>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_WRONG_MODEM -4
#define RADIOLIB_SX126X_STANDBY_XOSC 1
#define RADIOLIB_SX126X_STANDBY_RC 0
#define RADIOLIB_SX126X_PACKET_TYPE_LORA 1
#define RADIOLIB_SX126X_RX_TIMEOUT_INF 0xffffff
#define RADIOLIB_RADIO_MODE_NONE 0
#define RADIOLIB_IRQ_RX_DEFAULT_FLAGS 1
#define RADIOLIB_IRQ_RX_DEFAULT_MASK 2
#define RADIOLIB_IRQ_PREAMBLE_DETECTED 3
#define RADIOLIB_ASSERT(rc) do { if ((rc)!=0) return (rc); } while(0)
struct Module { enum { MODE_RX=1 }; void setRfSwitchState(int m) { assert(m==MODE_RX); } };
struct SX1262 {
  bool standbyXOSC=true;
  size_t preambleLengthLoRa=16;
  uint8_t crcTypeLoRa=1, implicitLen=255, headerType=0, invertIQEnabled=0;
  uint32_t rxTimeout=0; int stagedMode=3;
  std::string calls; char failAt=0; int modem=1; Module mod;
  virtual ~SX1262()=default;
  int call(char c) { calls+=c;return failAt==c ? -10:0; }
  virtual int16_t standby() { return standby(1,true); }
  int16_t standby(int mode,bool wake) { assert(mode==0 || mode==1);return call(wake?'S':'s'); }
  virtual int16_t startReceive() { return call('X'); }
  virtual int16_t setFrequency(float) { return call('F'); }
  int16_t setFrequency(float,bool) { return call('F'); }
  virtual int16_t setPreambleLength(size_t p) { preambleLengthLoRa=p;return call('P'); }
  int getIrqMapped(int x) { return x; }
  int setDioIrqParams(int flags,int mask) { assert(flags==9 && mask==2);return call('I'); }
  int setBufferBaseAddress() { return call('B'); }
  int clearIrqStatus() { return call('C'); }
  int getPacketType() { call('Q');return modem; }
  int setPacketParams(size_t p,int,int,int,int) { assert(p==preambleLengthLoRa);return call('K'); }
  Module* getMod() { return &mod; }
  int setRx(uint32_t t) { assert(t==0xffffff);return call('R'); }
};
struct CustomSX1262: SX1262 { int16_t startReceive() override { return call('F'); } };
@CLASS@
int main() {
  // Outside an owned RX-to-RX window every mask uses the ordinary path.
  for(unsigned mask=0;mask<256;++mask) {
    ExperimentalSX1262 r;r.experiment=mask;
    assert(r.startReceive()==0 && r.calls=="F");r.calls.clear();
    r.beginHop(false);r.standby();r.calls.clear();
    assert(r.startReceive()==0 && r.calls=="F");r.endHop();r.calls.clear();
    r.beginHop(true); // no successful standby yet
    assert(r.startReceive()==0 && r.calls=="F");r.endHop();r.calls.clear();
    r.beginHop(true);r.standby();r.rxPrimed=false;r.calls.clear();
    assert(r.startReceive()==0 && r.calls=="F");
  }
  // Exhaust every omission combination, after a valid normal RX setup.
  for(unsigned mask=1;mask<256;++mask) {
    ExperimentalSX1262 r;r.experiment=mask;r.rxPrimed=true;r.beginHop(true);
    assert(r.standby()==0);r.calls.clear();
    assert(r.setPreambleLength(91)==0);
    assert(r.preambleLengthLoRa==91);
    const bool deferred=mask & 32;
    assert(r.calls==(deferred ? "":"P"));r.calls.clear();
    assert(r.startReceive()==0);
    std::string want;
    if(!(mask&1)) want+=(mask&64)?'s':'S';
    if(!(mask&2)) want+='I';
    if(!(mask&4)) want+='B';
    want+='C'; // clearing stale IRQs must never be optimized out
    if(!(mask&16)) want+='Q';
    if(!(mask&8) || deferred) want+='K';
    want+='R'; // always issue RX and retain the underlying BUSY waits
    assert(r.calls==want && r.stagedMode==0);
    r.endHop();r.calls.clear();assert(r.startReceive()==0 && r.calls=="F");
  }
  // A command failure short-circuits; wrong modem never reaches SetRx.
  for(char fail: std::string("SIBCKR")) {
    ExperimentalSX1262 r;r.experiment=128;r.rxPrimed=true;r.beginHop(true);
    r.haveStandby=true;r.failAt=fail;
    assert(r.startReceive()==-10 && r.calls.back()==fail);
    if(fail=='R') assert(!r.rxPrimed);
  }
  ExperimentalSX1262 r;r.experiment=128;r.rxPrimed=true;r.beginHop(true);r.haveStandby=true;r.modem=0;
  assert(r.startReceive()==-4 && r.calls=="SIBCQ");
}
'''
        # These cases deliberately select the old HIL omission masks, not
        # the production state machine (tested separately).
        harness = harness.replace("ExperimentalSX1262 r;", "ExperimentalSX1262 r;r.productionPath=false;")
        self.compile_run(harness.replace("@CLASS@", source))

    def test_bulk_hal_preserves_full_duplex_buffer(self):
        source = (ROOT / "tools/hil/profile_switch.cpp").read_text()
        operation = method(source, "void spiTransfer(").replace(" override", "")
        offset = (ROOT / "tools/hil/ProfileFrequencyOffset.h").read_text().replace("#pragma once", "")
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
@OFFSET@
struct Spi {
  unsigned bulk=0;
  void transferBytes(uint8_t* out,uint8_t* in,size_t n) {
    ++bulk;for(size_t i=0;i<n;++i) in[i]=out[i]^0xa5;
  }
};
struct ArduinoHal {
  unsigned fallback=0;Spi device;Spi* spi=&device;
  void spiTransfer(uint8_t* out,size_t n,uint8_t* in) {
    ++fallback;for(size_t i=0;i<n;++i) in[i]=out[i]^0xa5;
  }
};
struct ESP32BufferedRadioHal: ArduinoHal {
  void spiTransfer(uint8_t* out,size_t n,uint8_t* in) {
    if(n) spi->transferBytes(out,in,n);
  }
};
using BenchHalBase=ESP32BufferedRadioHal;
struct {
  unsigned calls=0;
  void observe(const uint8_t* out,size_t n,const uint8_t* in) {
    ++calls;
    for(size_t i=0;i<n;++i) assert(in[i]==(out[i]^0xa5));
  }
} channelTrace;
struct Hal: BenchHalBase { bool bulkTransfer=false; @OP@ };
int main() {
  for(size_t n=1;n<=260;++n) for(int bulk=0;bulk<2;++bulk) {
    Hal h;h.bulkTransfer=bulk;uint8_t in[262]={},out[260];
    for(size_t i=0;i<n;++i) out[i]=uint8_t(i);
    h.spiTransfer(out,n,in+1);
    assert(!in[0] && !in[n+1]);
    for(size_t i=0;i<n;++i) assert(in[i+1]==(out[i]^0xa5));
    assert(h.fallback==unsigned(!bulk) && h.device.bulk==unsigned(bulk));
  }
  assert(channelTrace.calls==520);
  for(int bulk=0;bulk<2;++bulk) {
    Hal h;h.bulkTransfer=bulk;
    uint8_t out[5]={0x86,0x38,0xd8,0,0},in[5]={};
    { HilFrequencyOffsetScope offset(10);h.spiTransfer(out,5,in); }
    assert(out[4]==0 && in[4]==(10^0xa5)); // actual transmitted and observed byte
    h.spiTransfer(out,5,in);
    assert(in[4]==0xa5); // second/corrected pass has the original RF word
  }
}
'''
        self.compile_run(harness.replace("@OP@", operation).replace("@OFFSET@", offset))


if __name__ == "__main__":
    unittest.main()
