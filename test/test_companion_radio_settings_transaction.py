"""Execute Companion radio commands with production commit/recovery methods."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'examples/companion_radio/MyMesh.cpp'

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <initializer_list>
#include <vector>
#include <helpers/CLICommandUtils.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define MAX_LORA_TX_POWER 22
#define COMPANION_FEATURE_TEMP_RADIO 0
#define COMMAND_RADIO_APPLY_TIMEOUT_MS 3000
@CODES@
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
using Apply = mesh::RadioParamApplyResult;
static uint32_t now_ms = 100;
struct Clock { uint32_t getMillis() const { return now_ms; } } clock_source;
struct BaseSerialInterface {
  BaseSerialInterface* current = this;
  BaseSerialInterface* disconnected = nullptr;
  bool busy = false;
  BaseSerialInterface* captureReplyRoute() { return current; }
  bool isReplyRouteAvailable(BaseSerialInterface* route) { return route && route != disconnected; }
  bool isReplyRouteWriteBusy(BaseSerialInterface*) { return busy; }
};
struct Prefs {
  float freq=915, bw=250;
  uint8_t sf=7, cr=5, client_repeat=0, rx_ps_level=1, rx_ps_preamble=16;
  uint32_t rx_ps_rx_us=700, rx_ps_sleep_us=25000;
  int8_t tx_power_dbm=3;
  bool rx_boosted_gain=false, rx_powersaving_enabled=false;
};
void recalcRxPowerSavingFromLevel(uint8_t,uint8_t sf,float bw,uint8_t,
                                 uint32_t* rx,uint32_t* sleep) {
  *rx=sf*100; *sleep=uint32_t(bw*100);
}
struct Driver {
  int8_t power=3;
  unsigned calls=0;
  std::deque<bool> accepted;
  bool setTxPower(int8_t requested) {
    ++calls;
    bool ok=true;
    if (!accepted.empty()) { ok=accepted.front(); accepted.pop_front(); }
    if (ok) power=requested;
    return ok;
  }
  bool supportsRxBoostedGainMode() const { return false; }
  bool setRxBoostedGainMode(bool) { return true; }
  bool supportsRxPowerSaving() const { return false; }
  bool setRxPowerSaving(bool,uint32_t,uint32_t) { return true; }
} radio_driver;
@RETRY_DELAY@
struct MyMesh {
  Prefs _prefs, durable, live;
  BaseSerialInterface serial, original, other;
  BaseSerialInterface* _serial=&serial;
  Clock* _ms=&clock_source;
  bool storage_accepts=true, _radio_available=true, outbound=false;
  unsigned saves=0, applies=0;
  std::deque<Apply> accepted;
  bool saved_radio_apply_pending=false, command_radio_apply_pending=false;
  uint32_t radio_apply_retry_at=0, command_radio_apply_deadline=0;
  uint8_t radio_apply_failures=0;
  float command_radio_freq=0, command_radio_bw=0;
  uint8_t command_radio_sf=0, command_radio_cr=0, command_radio_repeat=0;
  BaseSerialInterface* command_radio_reply_route=nullptr;
  uint8_t cmd_frame[180]={};
  struct Reply { uint8_t error; BaseSerialInterface* route; };
  std::vector<Reply> replies;
  @RESULT_ENUM@;
  MyMesh() { serial.current=&original; radio_driver=Driver(); now_ms=100; }
  uint32_t futureMillis(uint32_t delay) const { return now_ms+delay; }
  bool millisHasNowPassed(uint32_t deadline) const { return int32_t(now_ms-deadline)>=0; }
  bool hasOutbound() const { return outbound; }
  bool isValidClientRepeatFreq(uint32_t) const { return true; }
  bool savePrefs() { ++saves; if(!storage_accepts)return false; durable=_prefs; return true; }
  void writeOKFrame(BaseSerialInterface* route=nullptr) { replies.push_back({0,route?route:serial.current}); }
  void writeErrFrame(uint8_t code,BaseSerialInterface* route=nullptr) { replies.push_back({code,route?route:serial.current}); }
  Apply tryApplyRadioParams(float freq,float bw,uint8_t sf,uint8_t cr) {
    ++applies;
    Apply result=Apply::APPLIED;
    if(!accepted.empty()) { result=accepted.front(); accepted.pop_front(); }
    if(result==Apply::APPLIED) { live.freq=freq;live.bw=bw;live.sf=sf;live.cr=cr; }
    return result;
  }
  bool applySavedRadioParams();
  RadioSettingResult applyAndSaveTxPower(int8_t);
  void finishRadioParamApply(float,float,uint8_t,uint8_t,uint8_t,BaseSerialInterface* = nullptr);
  void servicePendingRadioParamApply();
  void cancelPendingRadioParamApply();
  void frame(size_t len) { @FRAME_BRANCHES@ }
  void recover() { @RECOVERY@ }
  bool command(const char* command,char* reply) {
    const size_t reply_capacity=160;
    @CLI_BRANCH@
    return false;
  }
  void powerFrame(uint8_t power,size_t size=2) {
    cmd_frame[0]=CMD_SET_RADIO_TX_POWER;cmd_frame[1]=power;frame(size);
  }
  void tupleFrame() {
    cmd_frame[0]=CMD_SET_RADIO_PARAMS;
    uint32_t freq=920000,bw=125000;
    memcpy(cmd_frame+1,&freq,4);memcpy(cmd_frame+5,&bw,4);
    cmd_frame[9]=9;cmd_frame[10]=6;cmd_frame[11]=1;
    frame(12);
  }
  void expectError(uint8_t code,BaseSerialInterface* route=nullptr) {
    assert(replies.size()==1 && replies[0].error==code);
    assert(replies[0].route==(route?route:&original));
  }
  void expectOriginalPrefs() {
    assert(_prefs.freq==915 && _prefs.bw==250 && _prefs.sf==7 && _prefs.cr==5);
    assert(_prefs.client_repeat==0 && _prefs.rx_ps_rx_us==700 && _prefs.rx_ps_sleep_us==25000);
    assert(durable.freq==915 && durable.bw==250 && durable.sf==7 && durable.cr==5);
  }
};
@METHODS@
int main() {
  // Reject unavailable/busy hardware before either persisting or reporting it.
  {
    MyMesh m;radio_driver.accepted={false};m.powerFrame(17);
    m.expectError(ERR_CODE_BAD_STATE);
    assert(m.saves==0 && m._prefs.tx_power_dbm==3 && radio_driver.power==3);
  }
  for (int value : {-10,23}) {
    MyMesh m;m.powerFrame(uint8_t(value));m.expectError(ERR_CODE_ILLEGAL_ARG);
    assert(m.saves==0 && radio_driver.calls==0);
  }
  {
    MyMesh m;m.powerFrame(17,1);m.expectError(ERR_CODE_ILLEGAL_ARG);
    assert(m.saves==0 && radio_driver.calls==0);
  }
  {
    MyMesh m;m.powerFrame(17);m.expectError(0);
    assert(m.saves==1 && m.durable.tx_power_dbm==17 && radio_driver.power==17);
  }
  {
    MyMesh m;m._radio_available=false;m.powerFrame(17);m.expectError(0);
    assert(m.durable.tx_power_dbm==17 && radio_driver.calls==0);
  }
  // A failed commit restores RAM and the physical output-power setting.
  {
    MyMesh m;m.storage_accepts=false;m.powerFrame(17);m.expectError(ERR_CODE_FILE_IO_ERROR);
    assert(m._prefs.tx_power_dbm==3 && m.durable.tx_power_dbm==3 && radio_driver.power==3);
    assert(radio_driver.calls==2 && !m.saved_radio_apply_pending);
  }
  // RX may become busy during flash I/O. Retain recovery through a rejected
  // rollback and through a later successful tuple restore but failed power apply.
  {
    MyMesh m;m.storage_accepts=false;radio_driver.accepted={true,false,false,true};
    m.powerFrame(17);m.expectError(ERR_CODE_FILE_IO_ERROR);
    assert(m._prefs.tx_power_dbm==3 && radio_driver.power==17 && m.saved_radio_apply_pending);
    m.recover();assert(m.saved_radio_apply_pending && radio_driver.power==17);
    assert(m.radio_apply_failures==1 && m.radio_apply_retry_at>now_ms);
    unsigned calls=radio_driver.calls;m.recover();assert(radio_driver.calls==calls);
    now_ms=m.radio_apply_retry_at;m.recover();
    assert(!m.saved_radio_apply_pending && radio_driver.power==3 && m.saves==1);
    assert(m.replies.size()==1); // no later success for the failed command
  }
  // ASCII and framed setters share identical hardware/durability semantics.
  {
    MyMesh m;char reply[160]={};radio_driver.accepted={false};
    assert(m.command("set tx 17",reply) && strstr(reply,"Error"));
    assert(m.saves==0 && m._prefs.tx_power_dbm==3);
    m.storage_accepts=false;
    assert(m.command("set tx 17",reply) && strstr(reply,"saved"));
    assert(strstr(reply,"Error") && radio_driver.power==3);
    assert(m.command("set tx 17junk",reply) && strstr(reply,"Error"));
    assert(m.saves==1);
  }
  // Both immediate and deferred tuple changes must commit before OK.
  {
    MyMesh m;m.tupleFrame();m.expectError(0);
    assert(m._prefs.freq==920 && m.durable.freq==920 && m.live.freq==920);
    assert(m.durable.rx_ps_rx_us==900 && m.durable.rx_ps_sleep_us==12500);
    assert(!m.saved_radio_apply_pending);
  }
  {
    MyMesh m;m.storage_accepts=false;m.tupleFrame();m.expectError(ERR_CODE_FILE_IO_ERROR);
    m.expectOriginalPrefs();assert(m.live.freq==915 && !m.saved_radio_apply_pending);
  }
  {
    MyMesh m;m.accepted={Apply::BUSY,Apply::APPLIED};m.tupleFrame();
    assert(m.replies.empty() && m.command_radio_apply_pending && m.saves==0);
    m.serial.current=&m.other;m.servicePendingRadioParamApply();m.expectError(0);
    assert(!m.command_radio_apply_pending && m.durable.freq==920);
  }
  {
    MyMesh m;m.storage_accepts=false;m.accepted={Apply::BUSY,Apply::APPLIED,Apply::BUSY};
    m.tupleFrame();assert(m.replies.empty() && m.command_radio_apply_pending);
    m.serial.current=&m.other;m.servicePendingRadioParamApply();
    m.expectError(ERR_CODE_FILE_IO_ERROR);m.expectOriginalPrefs();
    assert(m.saved_radio_apply_pending && m.live.freq==920 && !m.command_radio_apply_pending);
    m.recover();assert(!m.saved_radio_apply_pending && m.live.freq==915);
    assert(m.saves==1 && m.replies.size()==1);
  }
  // Disconnect cancels a deferred command without persisting its candidate.
  {
    MyMesh m;m.accepted={Apply::BUSY};m.tupleFrame();
    m.serial.disconnected=&m.original;m.servicePendingRadioParamApply();
    assert(!m.command_radio_apply_pending && m.replies.empty() && m.saves==0);
    m.expectOriginalPrefs();
  }
  // Tuple completion must not swallow an outstanding power rollback.
  {
    MyMesh m;m.saved_radio_apply_pending=true;radio_driver.power=17;
    m.tupleFrame();m.expectError(0);assert(m.saved_radio_apply_pending);
    m.recover();assert(!m.saved_radio_apply_pending && radio_driver.power==3);
    assert(m.live.freq==920 && m.durable.freq==920);
  }
  puts("Companion radio-setting commit and recovery regressions passed");
}
'''

SLEEP_HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
static uint32_t now_ms=100;
struct Serial {
  bool hasPendingIO() const { return false; }
  bool isConnected() const { return false; }
};
struct {
  bool isWatchdogObserving() const { return false; }
  bool isCalibratingNoiseFloor() const { return false; }
} radio_driver;
struct MyMesh {
  bool _radio_available=true, _iter_started=false, command_radio_apply_pending=false;
  bool binary_trace_pending=false, saved_radio_apply_pending=false;
  uint32_t radio_apply_retry_at=0, _scheduled_reboot_at=0;
  uint32_t emergency_client_repeat_send_at=0, dirty_contacts_expiry=0;
  uint8_t dirty_contacts_failures=0;
  const void *pending_serial_reply_route=nullptr, *sign_data=nullptr;
  const void *emergency_client_repeat_packet=nullptr;
  Serial* _serial=nullptr;
#if COMPANION_FEATURE_TEMP_RADIO
  bool _temp_radio_applied=false;
  uint32_t _temp_radio_set_at=0, _temp_radio_revert_at=0, _temp_radio_retry_at=0;
#endif
  bool millisHasNowPassed(uint32_t at) const { return int32_t(now_ms-at)>=0; }
  bool isDualRadioActive() const { return false; }
  bool isContactWriteDue() const { return false; }
  bool hasPendingOtaApply() const { return false; }
  bool hasQueuedWorkDue() const { return false; }
  bool hasRetryWorkDue() const { return false; }
  bool hasPendingWork() const;
};
@PENDING_WORK@
int main() {
  MyMesh m;
  assert(!m.hasPendingWork());
  m.saved_radio_apply_pending=true;
  assert(m.hasPendingWork());
  m.radio_apply_retry_at=now_ms+10;
  assert(!m.hasPendingWork());
  now_ms+=10;
  assert(m.hasPendingWork());
  m.radio_apply_retry_at=0;
#if COMPANION_FEATURE_TEMP_RADIO
  // An already active lease can last hours; a rejected power-save rollback
  // must not keep spinning solely because saved radio restoration is pending.
  m._temp_radio_applied=true;
  m._temp_radio_revert_at=now_ms+3600000;
  assert(!m.hasPendingWork());
  // A future scheduled TempRadio entry also blocks normal-radio restoration.
  m._temp_radio_applied=false;m._temp_radio_revert_at=0;
  m._temp_radio_set_at=now_ms+20;
  assert(!m.hasPendingWork());
  now_ms=m._temp_radio_set_at;
  assert(m.hasPendingWork()); // the entry timer, not the blocked restore
  m._temp_radio_set_at=0;m._temp_radio_applied=true;
  m._temp_radio_revert_at=now_ms+20;
  assert(!m.hasPendingWork());
  now_ms=m._temp_radio_revert_at;
  assert(m.hasPendingWork()); // lease expiry still gets serviced
  m._temp_radio_applied=false;m._temp_radio_revert_at=0;
  assert(m.hasPendingWork()); // rollback is runnable after the lease clears
#endif
  m.saved_radio_apply_pending=false;
  assert(!m.hasPendingWork());
}
'''


class CompanionRadioSettingsTransactionTests(unittest.TestCase):
    def test_lease_blocked_recovery_does_not_prevent_sleep(self):
        text = SOURCE.read_text(encoding='utf-8')
        pending = extract_braced(text, 'bool MyMesh::hasPendingWork() const')
        harness = SLEEP_HARNESS.replace('@PENDING_WORK@', pending)
        with tempfile.TemporaryDirectory(prefix='mesh-companion-radio-sleep-') as tmp:
            cpp = Path(tmp) / 'test.cpp'
            binary = Path(tmp) / ('test.exe' if os.name == 'nt' else 'test')
            cpp.write_text(harness, encoding='utf-8')
            for macros in (['-DCOMPANION_FEATURE_TEMP_RADIO=0'],
                           ['-DCOMPANION_FEATURE_TEMP_RADIO=1'],
                           ['-DCOMPANION_FEATURE_TEMP_RADIO=1', '-DNRF52_PLATFORM=1']):
                with self.subTest(macros=macros):
                    subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                                    '-Wall', '-Wextra', '-Werror', *macros,
                                    str(cpp), '-o', str(binary)], check=True)
                    subprocess.run([str(binary)], check=True)

    def test_production_commands_commit_and_restore(self):
        text = SOURCE.read_text(encoding='utf-8')
        header = (SOURCE.parent / 'MyMesh.h').read_text(encoding='utf-8')
        codes = '\n'.join(re.findall(
            r'^#define (?:CMD_SET_RADIO_PARAMS|CMD_SET_RADIO_TX_POWER|ERR_CODE_\w+)\s+\d+',
            text, flags=re.MULTILINE))
        methods = '\n'.join(extract_braced(text, signature) for signature in (
            'bool MyMesh::applySavedRadioParams(',
            'MyMesh::RadioSettingResult MyMesh::applyAndSaveTxPower(',
            'void MyMesh::finishRadioParamApply(',
            'void MyMesh::cancelPendingRadioParamApply(',
            'void MyMesh::servicePendingRadioParamApply(',
        ))
        frame_branches = ' else '.join(extract_braced(text, f'if (cmd_frame[0] == {command})')
                                      for command in ('CMD_SET_RADIO_PARAMS', 'CMD_SET_RADIO_TX_POWER'))
        replacements = {
            '@CODES@': codes,
            '@RESULT_ENUM@': extract_braced(header, 'enum class RadioSettingResult'),
            '@RETRY_DELAY@': extract_braced(text, 'static uint32_t nextRadioApplyRetryDelay('),
            '@METHODS@': methods,
            '@FRAME_BRANCHES@': frame_branches,
            '@CLI_BRANCH@': extract_braced(text, 'if (strncmp(command, "set tx ", 7) == 0)'),
            '@RECOVERY@': extract_braced(text, 'if (!command_radio_apply_pending && saved_radio_apply_pending && !hasOutbound()'),
        }
        harness = HARNESS
        for marker, value in replacements.items():
            harness = harness.replace(marker, value)
        with tempfile.TemporaryDirectory(prefix='mesh-companion-radio-settings-') as tmp:
            cpp = Path(tmp) / 'test.cpp'
            binary = Path(tmp) / ('test.exe' if os.name == 'nt' else 'test')
            cpp.write_text(harness, encoding='utf-8')
            subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-Wno-sign-compare',  # existing frame parser compares size_t and byte offsets
                            '-I', str(ROOT / 'src'), str(cpp), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_all_tx_power_frontends_use_checked_handler(self):
        text = SOURCE.read_text(encoding='utf-8')
        terminal = extract_braced(text, 'if (strncmp(config, "tx ", 3) == 0)')
        self.assertIn('handleCommand(command, 0, local_reply)', terminal)
        web = extract_braced(text, 'if (strcmp(key, "tx") == 0)')
        self.assertIn('applyAndSaveTxPower(', web)
        self.assertIn('RadioSettingResult::SaveFailed',
                      extract_braced(text, 'if (strncmp(command, "set tx ", 7) == 0)'))


if __name__ == '__main__':
    unittest.main()
