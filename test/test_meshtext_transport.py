"""Run the actual Dispatcher foreign-frame ownership and isolation contract."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from test_radio_receive_contract import method
from test_radio_profile_scan import HARNESS as SCAN_HARNESS

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <Dispatcher.h>
#include <helpers/StaticPoolPacketManager.h>
#include <cassert>
#include <cstring>
#include <vector>
using namespace mesh;
class Clock : public MillisecondClock {
public: unsigned long now=1000; unsigned long getMillis() override { return now; }
};
class TestRadio : public Radio {
public:
  RadioProfiles p;
  uint8_t active=0;
  bool supported=true, receiving=false, complete=false, sending=false, start_ok=true, carrier=false;
  bool incoming=false;
  unsigned sends=0, finishes=0, recovery=0, processed=0;
  RadioParamApplyResult prepare=RadioParamApplyResult::APPLIED;
  std::vector<uint8_t> sent;
  TestRadio() {
    p.primary.freq=909.5f; p.primary.bw=500; p.primary.sf=5; p.primary.cr=5;
    p.secondary.params=p.primary; p.secondary.params.protocol=RadioProfileProtocol::Meshtext;
    p.secondary.params.preamble=16; p.secondary.mode=RadioProfileMode::RxTx;
  }
  RadioProfiles* profiles() override { return &p; }
  const RadioProfiles* profiles() const override { return &p; }
  bool supportsMeshtext() const override { return supported; }
  uint8_t receiveProfile() const override { return active; }
  int recvRaw(uint8_t* bytes,int) override {
    if (!incoming) return 0;
    incoming=false; bytes[0]=0x11;bytes[1]=0;bytes[2]=0x23;return 3;
  }
  void onReceiveProcessed() override { ++processed; }
  uint32_t getEstAirtimeFor(int) override { return 100; }
  uint32_t getProfileAirtime(uint8_t,int,uint8_t=0) override { return 100; }
  float packetScore(float,int) override { return 1; }
  RadioParamApplyResult prepareTransmitProfile(uint8_t n) override {
    if (prepare==RadioParamApplyResult::APPLIED) active=n;
    return prepare;
  }
  bool startSendRaw(const uint8_t* bytes,int len) override {
    ++sends;sent.assign(bytes,bytes+len);sending=start_ok;complete=false;return start_ok;
  }
  bool isSendComplete() override { return complete; }
  void onSendFinished() override { ++finishes;sending=false; }
  bool isInRecvMode() const override { return !sending; }
  bool isReceiving() override { return receiving; }
  bool isCarrierWaveActive() const override { return carrier; }
  bool recoverRadio(bool) override { ++recovery;return true; }
};
class TestDispatcher : public Dispatcher {
public:
  unsigned foreign_rx=0, mc_rx=0, raw_logs=0, mc_sent=0;
  bool allow_foreign=true;
  unsigned long duty_window=3600000;
  std::vector<bool> done;
  TestDispatcher(TestRadio& r, Clock& c, StaticPoolPacketManager& m):Dispatcher(r,c,m) {}
  using Dispatcher::queueForeignRadioFrame;
  using Dispatcher::hasForeignRadioFrame;
  using Dispatcher::setRadioAvailable;
  using Dispatcher::getTransmitProfileMask;
  using Dispatcher::getNextQueueWakeDelay;
  DispatcherAction onRecvPacket(Packet*) override { ++mc_rx;return ACTION_RELEASE; }
  void logRxRaw(float,float,const uint8_t*,int) override { ++raw_logs; }
  bool onForeignRadioFrame(const uint8_t* bytes,size_t length,uint8_t profile,uint32_t generation,float,float) override {
    assert(length==3 && bytes[0]==0x11 && profile==1 && generation);++foreign_rx;return false;
  }
  void onForeignRadioSendComplete(bool ok) override { done.push_back(ok); }
  bool allowForeignRadioTransmit() const override { return allow_foreign; }
  unsigned long getDutyCycleWindowMs() const override { return duty_window; }
  void onSendComplete(Packet*) override { ++mc_sent; }
  bool queue(const uint8_t* b,size_t n=3) { return queueForeignRadioFrame(b,n,1,_radio->profiles()->generation[1]); }
};
struct Fixture {
  Clock clock;TestRadio radio;StaticPoolPacketManager manager{8};TestDispatcher d{radio,clock,manager};
  Fixture() { d.begin(); }
  void tick(unsigned delta=1) { clock.now+=delta;d.loop(); }
};
static const uint8_t bytes[]={0x11,0,0x23};
static Packet* newPacket(TestDispatcher& d,uint8_t policy=RADIO_TX_BOTH) {
  auto* p=d.obtainNewPacket();assert(p);p->header=ROUTE_TYPE_FLOOD | (PAYLOAD_TYPE_GRP_TXT<<2);
  p->tx_radio=policy;p->payload[0]=0x12;p->payload_len=1;return p;
}
int main() {
  { Fixture f;assert(f.d.queue(bytes));assert(f.d.hasOutbound());assert(!f.d.queue(bytes));
    uint32_t delay=99;assert(f.d.getNextQueueWakeDelay(delay) && delay==0);
    f.tick();assert(f.radio.sends==1 && f.radio.active==1 && f.radio.sent==std::vector<uint8_t>(bytes,bytes+3));
    f.tick(20);assert(f.radio.sends==1 && f.d.done.empty());
    f.radio.complete=true;f.tick();assert(f.d.done.size()==1 && f.d.done[0]);
    assert(!f.d.hasOutbound() && f.radio.finishes==1 && f.radio.p.tx_packets[1]==1);
    assert(f.d.getNumSentFlood()==0 && f.d.getNumSentDirect()==0 && f.d.getTotalAirTime()==21);
  }
  { Fixture f;f.radio.active=1;f.radio.incoming=true;f.tick();
    assert(f.d.foreign_rx==1 && !f.d.mc_rx && !f.d.raw_logs && f.d.getLastMeshCoreRecvMillis()==0);
    assert(f.radio.processed==1 && f.d.getNumRecvFlood()==0 && f.radio.p.rx_packets[1]==1);
    f.radio.p.secondary.mode=RadioProfileMode::Off;f.radio.incoming=true;f.tick();
    assert(f.d.foreign_rx==1 && !f.d.raw_logs && !f.d.mc_rx);
  }
  { Fixture f;assert(f.d.queue(bytes));++f.radio.p.generation[1];f.tick();
    assert(f.radio.sends==0 && f.d.done.size()==1 && !f.d.done[0] && !f.d.hasOutbound());
  }
  { Fixture f;f.radio.start_ok=false;assert(f.d.queue(bytes));f.tick();f.tick(2000);
    assert(f.radio.sends==1 && f.d.done.size()==1 && !f.d.done[0] && !f.d.hasOutbound());
  }
  { Fixture f;assert(f.d.queue(bytes));f.tick();f.tick(1151);
    assert(f.radio.sends==1 && f.radio.finishes==1 && f.radio.recovery==1);
    assert(f.d.done.size()==1 && !f.d.done[0] && !f.d.hasOutbound());f.tick(10000);assert(f.radio.sends==1);
  }
  { Fixture f;assert(f.d.queue(bytes));f.tick();f.d.setRadioAvailable(false);f.tick(10000);
    assert(f.radio.sends==1 && f.radio.finishes==1 && f.d.done.size()==1 && !f.d.done[0]);
    assert(!f.d.queue(bytes) && !f.d.hasOutbound());
  }
  { Fixture f;assert(f.d.queue(bytes));f.tick();++f.radio.p.generation[1];f.radio.complete=true;f.tick();
    assert(f.d.done.size()==1 && !f.d.done[0] && f.radio.finishes==1);
  }
  { Fixture f;f.radio.prepare=RadioParamApplyResult::FAILED;assert(f.d.queue(bytes));f.tick();
    assert(f.radio.sends==0 && f.d.done.size()==1 && !f.d.done[0]);
  }
  { Fixture f;assert(f.d.queue(bytes));f.d.allow_foreign=false;f.tick();
    assert(f.radio.sends==0 && f.d.done.size()==1 && !f.d.done[0] && !f.d.queue(bytes));
  }
  { Fixture f;assert(f.d.queue(bytes));f.tick();f.d.begin();
    assert(f.radio.finishes==1 && f.d.done.size()==1 && !f.d.done[0] && !f.d.hasOutbound());
  }
  { Fixture f;f.radio.prepare=RadioParamApplyResult::BUSY;assert(f.d.queue(bytes));f.tick();f.tick(8151);
    assert(f.radio.sends==0 && f.d.done.size()==1 && !f.d.done[0] && f.radio.recovery==1);
  }
  { Fixture f;f.radio.receiving=true;assert(f.d.queue(bytes));f.tick();f.tick(201);
    assert(f.radio.sends==0 && f.d.hasOutbound());f.radio.receiving=false;f.tick(201);assert(f.radio.sends==1);
  }
  { Fixture f;auto* p=newPacket(f.d);f.radio.p.cross=RadioCrossMode::On;
    assert(!f.radio.p.canCross() && f.d.getTransmitProfileMask(p)==1);
    assert(f.d.sendPacket(p,0));assert(f.manager.getOutboundTotal()==1);f.tick();
    assert(f.radio.sends==1 && f.radio.active==0);f.radio.complete=true;f.tick();assert(f.d.mc_sent==1);
    auto* denied=newPacket(f.d,RADIO_TX_SECONDARY);assert(!f.d.sendPacket(denied,0));
    assert(f.manager.getOutboundTotal()==0 && f.radio.sends==1);
  }
  { Fixture f;auto* p=newPacket(f.d,RADIO_TX_PRIMARY);assert(f.d.sendPacket(p,0));f.tick();
    assert(f.d.queue(bytes));f.tick();assert(f.radio.sends==1);f.radio.complete=true;f.tick();
    f.tick();assert(f.radio.sends==2 && f.radio.active==1);f.radio.complete=true;f.tick();
    assert(f.d.done.size()==1 && f.d.done[0] && f.d.mc_sent==1);
  }
  { Fixture f;f.d.duty_window=200;f.d.begin();assert(f.d.queue(bytes));f.tick();
    f.clock.now+=100;f.radio.complete=true;f.d.loop();assert(f.d.getRemainingTxBudget()==0);
    assert(f.d.queue(bytes));f.tick(100);assert(f.radio.sends==1);
    f.tick(101);assert(f.radio.sends==2);
  }
  { Fixture f;assert(f.d.queue(bytes));f.tick();f.radio.complete=true;f.tick();
    assert(f.d.queue(bytes));auto* p=newPacket(f.d,RADIO_TX_PRIMARY);assert(f.d.sendPacket(p,0));f.tick();
    assert(f.radio.sends==2 && f.radio.active==0);f.radio.complete=true;f.tick();f.tick();
    assert(f.radio.sends==3 && f.radio.active==1);
  }
  { Fixture f;f.radio.supported=false;assert(!f.d.queue(bytes));f.radio.supported=true;
    assert(!f.d.queueForeignRadioFrame(bytes,3,0,f.radio.p.generation[0]));
    assert(!f.d.queueForeignRadioFrame(bytes,3,1,f.radio.p.generation[1]+1));
    assert(!f.d.queue(nullptr));assert(!f.d.queue(bytes,0));uint8_t full[256]={};assert(!f.d.queue(full,256));
    f.radio.carrier=true;assert(!f.d.queue(bytes));f.radio.carrier=false;
    f.radio.p.secondary.mode=RadioProfileMode::Rx;assert(!f.d.queue(bytes));
  }
}
'''


class MeshtextTransportTest(unittest.TestCase):
    def compile_and_run(self, text, extra_flags=()):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as folder:
            cpp, exe = Path(folder)/'test.cpp', Path(folder)/'test.exe'
            (Path(folder)/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cmath>\n')
            cpp.write_text(text)
            result = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra',
                *extra_flags, '-I', folder, '-I', str(ROOT/'src'), str(cpp), '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr[-12000:])
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_actual_dispatcher(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as folder:
            cpp, exe = Path(folder)/'test.cpp', Path(folder)/'test.exe'
            cpp.write_text(HARNESS)
            flags = ['-std=c++17', '-Wall', '-Wextra', '-DMESH_ENABLE_MESHTEXT_CHAT=1']
            if os.name != 'nt':
                flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-pie', '-no-pie']
            result = subprocess.run([compiler, *flags,
                '-I', str(ROOT/'test/mocks'), '-I', str(ROOT/'src'),
                str(ROOT/'src/Dispatcher.cpp'), str(ROOT/'src/Packet.cpp'),
                str(ROOT/'src/helpers/StaticPoolPacketManager.cpp'), str(cpp), '-o', str(exe)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr[-12000:])
            env = os.environ.copy()
            env['ASAN_OPTIONS'] = env.get('ASAN_OPTIONS', '') + ':detect_leaks=0'
            run = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_protocol_retune_and_rollback(self):
        source = (ROOT/'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        names = [('uint8_t', 'beginReconfigure'), ('void', 'endReconfigure'),
            ('void', 'serviceProfileScan')] + [('mesh::RadioParamApplyResult', n) for n in
            ('tuneProfile', 'prepareTransmitProfile', 'tryRestoreCodingRate', 'trySetParams', 'trySetPrimaryParams')]
        methods = '\n'.join(method(source, f'{kind} RadioLibWrapper::{name}(') for kind, name in names)
        methods += '\n' + method(source, 'mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool')
        wrapper = (ROOT/'src/helpers/radiolib/CustomSX1262Wrapper.h').read_text()
        methods += '\n' + method(wrapper, 'void setProfileStandbyWarm(').replace(
            'void setProfileStandbyWarm(bool enabled) override',
            'void RadioLibWrapper::setProfileStandbyWarm(bool enabled)')
        harness = SCAN_HARNESS.split('@METHODS@')[0]
        harness = harness.replace('uint16_t _physical_preamble=32;', '''
  uint16_t _physical_preamble=32;
  mesh::RadioProfileProtocol _physical_protocol=mesh::RadioProfileProtocol::MeshCore;
  mesh::RadioProfileProtocol applied_protocol=mesh::RadioProfileProtocol::MeshCore;
  unsigned protocol_writes=0;
  bool protocol_fail=false;
  bool applyProtocol(mesh::RadioProfileProtocol p) {
    ++protocol_writes;
    if (protocol_fail && p==mesh::RadioProfileProtocol::Meshtext) return false;
    applied_protocol=p;return true;
  }''')
        cases = r'''
int main() {
  using Protocol=mesh::RadioProfileProtocol;
  using Result=mesh::RadioParamApplyResult;
  {
    RadioLibWrapper w;
    mesh::RadioProfileConfig cfg;cfg.params=w._profiles.primary;
    cfg.params.protocol=Protocol::Meshtext;cfg.mode=mesh::RadioProfileMode::RxTx;
    w._profiles.setSecondary(cfg,false);
    assert(w._profiles.preamble(1,128)==16 && w._profiles.automaticPreambleFits());
    assert(w.tuneProfile(1)==Result::APPLIED);
    assert(w._physical_protocol==Protocol::Meshtext && w.applied_protocol==Protocol::Meshtext);
    assert(w.protocol_writes==1 && w._physical_preamble==16);
    assert(w.tuneProfile(0)==Result::APPLIED);
    assert(w._physical_protocol==Protocol::MeshCore && w.applied_protocol==Protocol::MeshCore);
    assert(w.protocol_writes==2);
    const auto generation=w._profiles.generation[1];
    cfg.params.protocol=Protocol::MeshCore;w._profiles.setSecondary(cfg,false);
    assert(w._profiles.generation[1]==generation+1);
  }
  {
    RadioLibWrapper w;mesh::RadioProfileConfig cfg;cfg.params=w._profiles.primary;
    cfg.params.protocol=Protocol::Meshtext;cfg.mode=mesh::RadioProfileMode::RxTx;
    w._profiles.setSecondary(cfg,false);w.protocol_fail=true;
    assert(w.tuneProfile(1)==Result::FAILED);
    assert(w._physical_protocol==Protocol::MeshCore && w.applied_protocol==Protocol::MeshCore);
    assert(w._active_profile==0 && w._profile_generation==1);
    assert(w.protocol_writes==2);
  }
  {
    RadioLibWrapper w;mesh::RadioProfileConfig cfg;cfg.params=w._profiles.primary;
    cfg.params.protocol=Protocol::Meshtext;cfg.mode=mesh::RadioProfileMode::RxTx;
    w._profiles.setSecondary(cfg,false);w.failRxStarts=1;
    assert(w.tuneProfile(1)==Result::FAILED);
    assert(w._physical_protocol==Protocol::MeshCore && w.applied_protocol==Protocol::MeshCore);
    assert(w._active_profile==0 && w.isInRecvMode());
  }
  {
    mesh::RadioProfiles p;p.primary.freq=909.5;p.primary.bw=500;p.primary.sf=5;p.primary.cr=5;
    p.secondary.params=p.primary;p.secondary.params.protocol=Protocol::Meshtext;
    p.secondary.mode=mesh::RadioProfileMode::RxTx;
    for (auto cross: {mesh::RadioCrossMode::Off,mesh::RadioCrossMode::On,mesh::RadioCrossMode::Auto}) {
      p.cross=cross;assert(!p.canCross());assert(p.transmitMask(0)==1 && p.transmitMask(1)==2);
    }
  }
}
'''
        self.compile_and_run(harness + methods + cases,
                             ['-DMESH_ENABLE_MESHTEXT_CHAT=1', '-DMESH_ENABLE_MESHTEXT_WIRE=1'])

    def test_sx1262_wire_settings_and_deep_recovery(self):
        wrapper = (ROOT/'src/helpers/radiolib/CustomSX1262Wrapper.h').read_text()
        wire = method(wrapper, 'bool applyProtocol(').replace(' override', '')
        chip_source = (ROOT/'src/helpers/radiolib/CustomSX1262.h').read_text()
        protocol = method(chip_source, 'int16_t setProfileProtocol(')
        source = (ROOT/'src/helpers/radiolib/RadioLibWrappers.cpp').read_text()
        restore = method(source, 'bool RadioLibWrapper::restoreAfterDeepInit()')
        harness = r'''
#include <cassert>
#include <cstdint>
#include <RadioProfiles.h>
#include <helpers/radiolib/SX1262ProfileSwitchState.h>
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_SX126X_LORA_CRC_ON 1
#define RADIOLIB_SX126X_LORA_HEADER_EXPLICIT 0
#define RADIOLIB_SX126X_LORA_IQ_STANDARD 0
#define STATE_IDLE 0
struct SX1262 {
  uint8_t sync=0x12;bool crc=false,explicit_header=false,iq=true;
  size_t preambleLengthLoRa=32;
  uint8_t crcTypeLoRa=1,implicitLen=255,headerType=0,invertIQEnabled=0;
  unsigned step=0,fail_at=0;
  int status() { return ++step==fail_at ? -1 : 0; }
  int setSyncWord(uint8_t n) { sync=n;return status(); }
  int setCRC(uint8_t n) { crc=n==1;return status(); }
  int explicitHeader() { explicit_header=true;return status(); }
  int invertIQ(bool n) { iq=n;return status(); }
  int setPreambleLength(uint16_t) { return 0; }
};
struct CustomSX1262: SX1262 {
  SX1262ProfileSwitchState _profileSwitch{false};
  @PROTOCOL@
};
struct RadioLibWrapper {
  CustomSX1262 chip;CustomSX1262* _radio=&chip;
  bool supported=true;
  bool supportsMeshtext() const { return supported; }
  bool _rx_ps_armed=true,_params_valid=true,_dbm_valid=false,_rx_boosted_gain_valid=false;
  int state=1;uint8_t _cur_sf=11,_cur_cr=5;float _cur_freq=906.875,_cur_bw=250;
  int8_t _cur_dbm=22;bool _cur_rx_boosted_gain=false;
  mesh::RadioProfileProtocol _physical_protocol=mesh::RadioProfileProtocol::Meshtext;
  bool radioDeepInit() { chip.sync=0x12;chip.step=0;return true; }
  bool registerInterruptAction() { return true; }
  bool applyParams(float,float,uint8_t,uint8_t) { return true; }
  uint16_t currentPreambleLength() { return 16; }
  int applyCachedTxPower(int8_t) { return 0; }
  bool applyRxBoostedGainMode(bool) { return true; }
  void recalibrateNoiseFloor() {}
  @WIRE@
  bool restoreAfterDeepInit();
};
@RESTORE@
int main() {
  using Protocol=mesh::RadioProfileProtocol;
  for (Protocol p: {Protocol::MeshCore,Protocol::Meshtext}) {
    RadioLibWrapper w;assert(w.applyProtocol(p));
    assert(w.chip.sync==(p==Protocol::MeshCore ? 0x12 : 0x2b));
    assert(w.chip.crc && w.chip.explicit_header && !w.chip.iq);
  }
  for (unsigned fail=1;fail<=4;++fail) {
    RadioLibWrapper w;w.chip.fail_at=fail;assert(!w.applyProtocol(Protocol::Meshtext));
  }
  RadioLibWrapper w;assert(w.restoreAfterDeepInit());
  assert(w.chip.sync==0x2b && w.chip.crc && w.chip.explicit_header && !w.chip.iq);
  assert(!w._rx_ps_armed && w.state==STATE_IDLE);
  w._physical_protocol=Protocol::MeshCore;assert(w.restoreAfterDeepInit());assert(w.chip.sync==0x12);
  RadioLibWrapper disabled;disabled.supported=false;
  assert(disabled.applyProtocol(Protocol::MeshCore));
  assert(!disabled.applyProtocol(Protocol::Meshtext) && disabled.chip.step==0);
}
'''
        self.compile_and_run('#include <initializer_list>\n' + harness.replace('@WIRE@',wire)
                             .replace('@PROTOCOL@',protocol).replace('@RESTORE@',restore),
                             ['-DMESH_ENABLE_MESHTEXT_CHAT=1', '-DMESH_ENABLE_MESHTEXT_WIRE=1'])


if __name__ == '__main__':
    unittest.main()
