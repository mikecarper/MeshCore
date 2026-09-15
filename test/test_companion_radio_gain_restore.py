"""Fault-inject gain restoration in actual Companion startup/TempRadio methods."""
from pathlib import Path
import os
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
#include <deque>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
using Apply = mesh::RadioParamApplyResult;
static uint32_t now_ms=100;
template<typename T> T nextResult(std::deque<T>& results,T fallback) {
  if(results.empty())return fallback;
  T result=results.front();results.pop_front();return result;
}
struct Prefs {
  float freq=915,bw=250;
  uint8_t sf=7,cr=5,rx_boosted_gain=0;
  bool radio_fem_rxgain=false,radio_fem_txgain=false,cad_enabled=false;
  bool rx_powersaving_enabled=false;
  uint32_t rx_ps_rx_us=1000,rx_ps_sleep_us=2000;
  int tx_power_dbm=22,cad_scan_timeout_ms=100;
  void* getCustom(){return nullptr;}
};
struct Board {
  void attachDynamicPrefs(void*){}
  bool setLoRaFemLnaEnabled(bool){return true;}
  bool setLoRaFemPaGainEnabled(bool){return true;}
  bool canControlLoRaFemLna(){return false;}
  bool isLoRaFemLnaEnabled(){return false;}
} board;
struct Driver {
  bool gain=false,supported=true;
  int power=0;
  unsigned gain_calls=0,power_calls=0;
  std::deque<bool> gain_results,power_results;
  bool supportsRxBoostedGainMode(){return supported;}
  bool setRxBoostedGainMode(bool value){
    ++gain_calls;
    bool ok=nextResult(gain_results,true);
    if(ok)gain=value;
    return ok;
  }
  bool getRxBoostedGainMode(){return gain;}
  bool setTxPower(int value){
    ++power_calls;
    bool ok=nextResult(power_results,true);
    if(ok)power=value;
    return ok;
  }
  bool supportsRxPowerSaving(){return false;}
  bool setRxPowerSaving(bool,uint32_t,uint32_t){return true;}
  void setCADScanTimeoutMillis(int){}
} radio_driver;
struct Radio {
  void setCADEnabled(bool){}
  void recalibrateNoiseFloor(){}
} radio;
struct Clock { uint32_t getMillis(){return now_ms;} } clock_source;
@RETRY_DELAY@
struct MyMesh {
  Prefs _prefs;
  Radio* _radio=&radio;
  Clock* _ms=&clock_source;
  bool _radio_available=true,saved_radio_apply_pending=false;
  bool _temp_radio_applied=false,command_radio_apply_pending=false,outbound=false;
  uint32_t _temp_radio_set_at=0,_temp_radio_revert_at=0,_temp_radio_retry_at=0;
  uint32_t radio_apply_retry_at=0;
  uint8_t _temp_radio_failures=0,radio_apply_failures=0;
  float _temp_radio_freq=916,_temp_radio_bw=125,live_freq=0;
  uint8_t _temp_radio_sf=8,_temp_radio_cr=6,_temp_radio_preamble=16;
  unsigned applies=0,saves=0;
  std::deque<Apply> apply_results;
  MyMesh(){now_ms=100;radio_driver=Driver();}
  bool hasOutbound(){return outbound;}
  bool millisHasNowPassed(uint32_t at){return int32_t(now_ms-at)>0;}
  uint32_t futureMillis(uint32_t delay){return now_ms+delay;}
  Apply tryApplyRadioParams(float freq,float,uint8_t,uint8_t,bool=false,uint16_t=0){
    ++applies;
    Apply result=nextResult(apply_results,Apply::APPLIED);
    if(result==Apply::APPLIED)live_freq=freq;
    return result;
  }
  bool savePrefs(){++saves;return false;}
  void configureRadioFromPrefs();
  bool applySavedRadioParams();
  bool applyAndSaveRxBoostedGain(bool);
#if COMPANION_FEATURE_TEMP_RADIO
  void serviceTempRadio();
#endif
  void recover(){@RECOVERY@}
};
@METHODS@

static void checkStartupAndRetry(){
  {
    MyMesh m;m._prefs.rx_boosted_gain=1;
    m.configureRadioFromPrefs();
    assert(radio_driver.gain && !m.saved_radio_apply_pending);
    assert(radio_driver.power==m._prefs.tx_power_dbm && m.saves==0);
  }
  for(bool desired : {false,true}){
    MyMesh m;m._prefs.rx_boosted_gain=desired;radio_driver.gain=!desired;
    radio_driver.gain_results={false,false,true};
    m.configureRadioFromPrefs();
    assert(radio_driver.gain!=desired && m.saved_radio_apply_pending);
    m.recover();
    assert(m.saved_radio_apply_pending && m.radio_apply_retry_at>now_ms);
    unsigned calls=radio_driver.gain_calls;
    m.recover();
    assert(radio_driver.gain_calls==calls); // Respect backoff, do not spin.
    now_ms=m.radio_apply_retry_at+1;m.recover();
    assert(radio_driver.gain==desired && !m.saved_radio_apply_pending);
    assert(!m.radio_apply_retry_at && !m.radio_apply_failures && m.saves==0);
  }
  {
    MyMesh m;m.apply_results={Apply::BUSY};
    m.configureRadioFromPrefs();
    assert(m.saved_radio_apply_pending && radio_driver.gain_calls==0);
    m.recover();assert(!m.saved_radio_apply_pending);
  }
  {
    MyMesh m;radio_driver.power_results={false};
    m.configureRadioFromPrefs();
    assert(m.saved_radio_apply_pending);
    m.recover();assert(!m.saved_radio_apply_pending);
  }
  {
    MyMesh m;m._radio_available=false;
    m.configureRadioFromPrefs();
    assert(!m.saved_radio_apply_pending && !m.applies && !radio_driver.gain_calls);
  }
  {
    MyMesh m;radio_driver.supported=false;radio_driver.gain_results={false};
    m.configureRadioFromPrefs();
    assert(!m.saved_radio_apply_pending && !radio_driver.gain_calls);
    m.saved_radio_apply_pending=true;m.recover();
    assert(!m.saved_radio_apply_pending && !radio_driver.gain_calls);
  }
}

#if COMPANION_FEATURE_TEMP_RADIO
static void checkTemporaryOverrideCoexistence(){
  for(int phase=0;phase<3;++phase){
    MyMesh m;m._prefs.rx_boosted_gain=1;radio_driver.gain_results={false};
    m.configureRadioFromPrefs();assert(m.saved_radio_apply_pending);
    if(phase==0)m._temp_radio_set_at=now_ms+1500;
    if(phase==1)m._temp_radio_applied=true;
    if(phase==2)m._temp_radio_revert_at=now_ms+60000;
    unsigned applies=m.applies,gain_calls=radio_driver.gain_calls;
    m.recover();
    assert(m.saved_radio_apply_pending && m.applies==applies);
    assert(radio_driver.gain_calls==gain_calls); // Never override a lease.
    m._temp_radio_set_at=0;m._temp_radio_applied=false;m._temp_radio_revert_at=0;
    m.recover();assert(!m.saved_radio_apply_pending && radio_driver.gain);
  }
}

static void checkTempRadioRestoration(){
  for(bool rollback_pending : {false,true}){
    MyMesh m;m._temp_radio_applied=true;m._temp_radio_revert_at=now_ms;
    if(rollback_pending){
      radio_driver.gain_results={true,false};
      assert(!m.applyAndSaveRxBoostedGain(true));
      assert(radio_driver.gain && m.saved_radio_apply_pending);
      assert(!m._prefs.rx_boosted_gain);
    }else{
      m._prefs.rx_boosted_gain=1;
    }
    radio_driver.gain_results={false,true};
    m.serviceTempRadio();
    assert(m._temp_radio_applied && m._temp_radio_revert_at);
    assert(m._temp_radio_retry_at>now_ms);
    assert(m.saved_radio_apply_pending==rollback_pending);
    assert(radio_driver.gain!=bool(m._prefs.rx_boosted_gain));
    unsigned applies=m.applies,gain_calls=radio_driver.gain_calls;
    m.recover();m.serviceTempRadio();
    assert(m.applies==applies && radio_driver.gain_calls==gain_calls);
    now_ms=m._temp_radio_retry_at;m.serviceTempRadio();
    assert(radio_driver.gain==bool(m._prefs.rx_boosted_gain));
    assert(!m._temp_radio_applied && !m._temp_radio_revert_at);
    assert(!m._temp_radio_retry_at && !m._temp_radio_failures);
    assert(!m.saved_radio_apply_pending && m.live_freq==m._prefs.freq);
    assert(m.saves==unsigned(rollback_pending)); // Restoring never saves settings.
  }
  {
    MyMesh m;m._temp_radio_applied=true;m._temp_radio_revert_at=now_ms;
    m.saved_radio_apply_pending=true;radio_driver.supported=false;
    radio_driver.gain_results={false};m.serviceTempRadio();
    assert(!m._temp_radio_revert_at && !m.saved_radio_apply_pending);
    assert(!radio_driver.gain_calls);
  }
  {
    MyMesh m;m._temp_radio_applied=true;m._temp_radio_revert_at=now_ms;
    m.outbound=true;m.serviceTempRadio();
    assert(!m.applies && !radio_driver.gain_calls && m._temp_radio_revert_at);
    m.outbound=false;m.serviceTempRadio();assert(!m._temp_radio_revert_at);
  }
}
#endif

int main(){
  checkStartupAndRetry();
#if COMPANION_FEATURE_TEMP_RADIO
  checkTemporaryOverrideCoexistence();
  checkTempRadioRestoration();
#endif
}
'''


class CompanionRadioGainRestoreTests(unittest.TestCase):
    def test_production_gain_restore_keeps_failed_work_pending(self):
        text = SOURCE.read_text(encoding='utf-8')
        methods = '\n'.join(extract_braced(text, signature) for signature in (
            'void MyMesh::configureRadioFromPrefs(',
            'bool MyMesh::applySavedRadioParams(',
            'bool MyMesh::applyAndSaveRxBoostedGain(',
        ))
        methods += '\n#if COMPANION_FEATURE_TEMP_RADIO\n'
        methods += extract_braced(text, 'void MyMesh::serviceTempRadio(')
        methods += '\n#endif\n'
        replacements = {
            '@METHODS@': methods,
            '@RETRY_DELAY@': extract_braced(text, 'static uint32_t nextRadioApplyRetryDelay('),
            '@RECOVERY@': extract_braced(text, 'if (!command_radio_apply_pending && saved_radio_apply_pending && !hasOutbound()'),
        }
        harness = HARNESS
        for marker, value in replacements.items():
            harness = harness.replace(marker, value)
        with tempfile.TemporaryDirectory(prefix='mesh-companion-gain-restore-') as tmp:
            cpp = Path(tmp) / 'test.cpp'
            binary = Path(tmp) / ('test.exe' if os.name == 'nt' else 'test')
            cpp.write_text(harness, encoding='utf-8')
            for temporary_radio in (0, 1):
                with self.subTest(temporary_radio=temporary_radio):
                    subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                                    '-Wall', '-Wextra', '-Werror',
                                    f'-DCOMPANION_FEATURE_TEMP_RADIO={temporary_radio}',
                                    str(cpp), '-o', str(binary)], check=True)
                    subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
