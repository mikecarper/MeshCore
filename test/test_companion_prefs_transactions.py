"""Fault-inject storage in the actual Companion frame/web/terminal setters."""
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
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <string>
#include <vector>
#include <algorithm>
#include <helpers/CLICommandUtils.h>
#include <helpers/radiolib/RXPowerSaving.h>
using std::min;
using std::isfinite;
#define constrain(value,lo,hi) ((value)<(lo)?(lo):((value)>(hi)?(hi):(value)))
#define COMPANION_FEATURE_TEMP_RADIO 0
@CODES@
struct StrHelper {
  static void strncpy(char* out,const char* in,size_t n) {
    ::strncpy(out,in,n-1);out[n-1]=0;
  }
};
struct Prefs {
  char node_name[32]="original";
  char default_scope_name[31]="old-scope";
  uint8_t default_scope_key[16]={9};
  float freq=915,bw=125,airtime_factor=1,rx_delay_base=2;
  uint8_t sf=7,cr=5,rx_ps_level=1,rx_ps_preamble=16,rx_powersaving_enabled=0;
  uint32_t rx_ps_rx_us=3000,rx_ps_sleep_us=4000,ble_pin=123456,gps_interval=60;
  uint8_t manual_add_contacts=0,telemetry_mode_base=0,telemetry_mode_loc=0;
  uint8_t telemetry_mode_env=0,advert_loc_policy=0,multi_acks=0;
  uint8_t path_hash_mode=0,client_repeat=0,autoadd_config=0,autoadd_max_hops=2,gps_enabled=0;
  uint8_t rx_boosted_gain=0,powersaving_enabled=0;
  int8_t tx_power_dbm=3;
};
struct Sensors {
  double node_lat=10,node_lon=20;
  std::string gps="0",interval="60",other="old";
  bool accepts=true;
  bool powersaving_enabled=false;
  void setPowerSavingEnabled(bool enabled) { powersaving_enabled=enabled; }
  const char* getSettingByKey(const char* key) {
    return !strcmp(key,"gps") ? gps.c_str() : interval.c_str();
  }
  bool setSettingValue(const char* key,const char* value) {
    if(!accepts)return false;
    if(!strcmp(key,"gps"))gps=value;
    else if(!strcmp(key,"gps_interval"))interval=value;
    else other=value;
    return true;
  }
} sensors;
struct Driver {
  bool supported=true,rxps=false;
  bool gain_supported=true,boosted_gain=false;
  uint32_t rx=3000,sleep=4000;
  unsigned calls=0,gain_calls=0;
  std::deque<bool> results,gain_results;
  bool supportsRxBoostedGainMode() const { return gain_supported; }
  bool supportsRxPowerSaving() const { return supported; }
  bool setRxPowerSaving(bool enable,uint32_t r,uint32_t s) {
    ++calls;bool ok=true;
    if(!results.empty()){ok=results.front();results.pop_front();}
    if(ok){rxps=enable;rx=r;sleep=s;}
    return ok;
  }
  bool setRxBoostedGainMode(bool enabled) {
    ++gain_calls;bool ok=true;
    if(!gain_results.empty()){ok=gain_results.front();gain_results.pop_front();}
    if(ok)boosted_gain=enabled;
    return ok;
  }
  bool setTxPower(int8_t) { return true; }
} radio_driver;
@PARSERS@
struct Clock { uint32_t getMillis() const { return 100; } } clock_source;
struct MyMesh {
  Prefs _prefs,durable;
  double durable_lat=10,durable_lon=20;
  bool storage_accepts=true,_radio_available=true;
  unsigned saves=0,gps_policy_updates=0;
  bool saved_radio_apply_pending=false,command_radio_apply_pending=false;
  uint32_t radio_apply_retry_at=0;
  uint8_t radio_apply_failures=0;
  Clock* _ms=&clock_source;
  uint8_t cmd_frame[180]={};
  std::vector<uint8_t> replies;
  std::string output;
  MyMesh() { memcpy(&durable,&_prefs,sizeof(Prefs));sensors=Sensors();radio_driver=Driver(); }
  @SAVE_PREFERENCE@
  bool saveAdvertName(const char*);
  bool saveAdvertLocation(double,double);
  bool applyAndSaveRxPowerSaving(const char*,char*);
  bool applyAndSavePowerSaving(const char*,char*);
  bool applyAndSaveRxBoostedGain(bool);
  bool savePrefs() {
    ++saves;if(!storage_accepts)return false;
    memcpy(&durable,&_prefs,sizeof(Prefs));durable_lat=sensors.node_lat;durable_lon=sensors.node_lon;return true;
  }
  void writeOKFrame() { replies.push_back(0); }
  void writeErrFrame(uint8_t error) { replies.push_back(error); }
  void updateGpsTelemetryPolicy() { ++gps_policy_updates; }
  void applyGpsPrefs() {
    sensors.setSettingValue("gps",_prefs.gps_enabled?"1":"0");
    char s[16];snprintf(s,sizeof(s),"%lu",(unsigned long)_prefs.gps_interval);
    sensors.setSettingValue("gps_interval",s);
  }
  MyMesh& terminalOutput() { return *this; }
  void print(const char* value) { output+=value; }
  void frame(size_t len) { @FRAMES@ }
  void web(const char* key,const char* value,char* reply) { @WEB@ }
  void terminal(const char* config) { @TERMINAL@ }
  bool gps(const char* gps_value,char* reply) { const size_t reply_size=160;@GPS@;return false; }
  bool hasOutbound() const { return false; }
  bool millisHasNowPassed(uint32_t) const { return true; }
  uint32_t futureMillis(uint32_t delay) const { return delay+100; }
  bool applySavedRadioParams() { return true; }
  void recover() { @RECOVERY@ }
  void expectOriginal() const {
    assert(!memcmp(&_prefs,&durable,sizeof(Prefs)));
    assert(!strcmp(_prefs.node_name,"original"));
    assert(_prefs.freq==915 && _prefs.bw==125 && _prefs.sf==7 && _prefs.cr==5);
    assert(_prefs.airtime_factor==1 && _prefs.rx_delay_base==2 && _prefs.ble_pin==123456);
    assert(_prefs.manual_add_contacts==0 && _prefs.telemetry_mode_base==0);
    assert(_prefs.telemetry_mode_loc==0 && _prefs.telemetry_mode_env==0);
    assert(_prefs.advert_loc_policy==0 && _prefs.multi_acks==0 && _prefs.client_repeat==0);
    assert(_prefs.path_hash_mode==0 && _prefs.autoadd_config==0 && _prefs.autoadd_max_hops==2);
    assert(!strcmp(_prefs.default_scope_name,"old-scope") && _prefs.default_scope_key[0]==9);
    assert(_prefs.gps_enabled==0 && _prefs.gps_interval==60);
    assert(sensors.node_lat==10 && sensors.node_lon==20 && durable_lat==10 && durable_lon==20);
  }
  void laterSaveCannotResurrectFailure() {
    expectOriginal();storage_accepts=true;
    assert(saveAdvertName("later"));
    assert(durable.ble_pin==123456 && durable.airtime_factor==1 && durable.rx_delay_base==2);
    assert(durable.path_hash_mode==0 && durable.gps_enabled==0 && durable.gps_interval==60);
    assert(durable.autoadd_config==0 && durable.autoadd_max_hops==2);
    assert(durable.freq==915 && durable.bw==125 && durable.sf==7 && durable.cr==5);
    assert(durable.rx_ps_rx_us==3000 && durable.rx_ps_sleep_us==4000 && !durable.rx_powersaving_enabled);
    assert(durable_lat==10 && durable_lon==20);
  }
};
@METHODS@
static void u32(std::vector<uint8_t>& bytes,uint32_t value) {
  for(unsigned i=0;i<4;++i) bytes.push_back(uint8_t(value>>(8*i)));
}
static void send(MyMesh& m,const std::vector<uint8_t>& bytes) {
  memcpy(m.cmd_frame,bytes.data(),bytes.size());m.frame(bytes.size());
}
static bool error(const char* reply) { return strstr(reply,"Error") || strstr(reply,"ERROR"); }
int main() {
  std::vector<std::vector<uint8_t>> frames={{CMD_SET_ADVERT_NAME,'n','e','w'},
      {CMD_SET_OTHER_PARAMS,1,0x2a,1,2}, {CMD_SET_OTHER_PARAMS,1},
      {CMD_SET_PATH_HASH_MODE,0,2},{CMD_SET_AUTOADD_CONFIG,31,100},
      {CMD_SET_AUTOADD_CONFIG,31},{CMD_SET_DEFAULT_FLOOD_SCOPE}};
  std::vector<uint8_t> location={CMD_SET_ADVERT_LATLON};u32(location,30000000);u32(location,40000000);
  frames.push_back(location);
  std::vector<uint8_t> tuning={CMD_SET_TUNING_PARAMS};u32(tuning,9000);u32(tuning,5000);frames.push_back(tuning);
  std::vector<uint8_t> pin={CMD_SET_DEVICE_PIN};u32(pin,654321);frames.push_back(pin);
  std::vector<uint8_t> scope(48,0);scope[0]=CMD_SET_DEFAULT_FLOOD_SCOPE;
  memcpy(scope.data()+1,"new-scope",9);scope[32]=42;frames.push_back(scope);
#if ENV_INCLUDE_GPS == 1
  for(const char* value:{"gps:1","gps_interval:120"}) {
    std::vector<uint8_t> custom={CMD_SET_CUSTOM_VAR};
    custom.insert(custom.end(),value,value+strlen(value));frames.push_back(custom);
  }
#endif
  unsigned checks=0;
  for(const auto& bytes:frames) {
    for(bool save_ok:{false,true}) {
      MyMesh m;m.storage_accepts=save_ok;send(m,bytes);++checks;
      assert(m.replies.size()==1 && m.replies[0]==(save_ok?0:ERR_CODE_FILE_IO_ERROR));
      assert(m.saves==1);
      if(!save_ok) {
        assert(m.gps_policy_updates==0 && sensors.gps=="0" && sensors.interval=="60");
        m.laterSaveCannotResurrectFailure();
      } else {
        assert(!memcmp(&m._prefs,&m.durable,sizeof(Prefs)));
        assert(m.durable_lat==sensors.node_lat && m.durable_lon==sensors.node_lon);
        if(bytes[0]==CMD_SET_OTHER_PARAMS)assert(m.gps_policy_updates==1);
        if(bytes[0]==CMD_SET_AUTOADD_CONFIG && bytes.size()==3)assert(m.durable.autoadd_max_hops==64);
      }
    }
  }
  const char* web[][2]={{"name","new"},{"lat","30"},{"lon","40"},{"radio","920,250,9,6"},
      {"af","3"},{"rxdelay","4"},{"repeat","on"},{"radio.rxgain","on"}};
  for(const auto& command:web)for(bool save_ok:{false,true}) {
    MyMesh m;char reply[160]={};m.storage_accepts=save_ok;
    m.web(command[0],command[1],reply);++checks;
    assert(error(reply)!=save_ok && m.saves==1);
    if(!save_ok)m.laterSaveCannotResurrectFailure();
    else assert(!memcmp(&m._prefs,&m.durable,sizeof(Prefs)));
  }
  for(const char* command:{"name new","lat 30","lon 40","af 3"})for(bool save_ok:{false,true}) {
    MyMesh m;m.storage_accepts=save_ok;m.terminal(command);++checks;
    assert(error(m.output.c_str())!=save_ok && m.saves==1);
    if(!save_ok)m.laterSaveCannotResurrectFailure();
  }
  for(const auto& bytes:std::vector<std::vector<uint8_t>>{
        {CMD_SET_PATH_HASH_MODE,0,3},{CMD_SET_TUNING_PARAMS},{CMD_SET_OTHER_PARAMS},
        {CMD_SET_AUTOADD_CONFIG}}) {
    MyMesh m;send(m,bytes);++checks;
    assert(m.replies.size()==1 && m.replies[0]==ERR_CODE_ILLEGAL_ARG && m.saves==0);
    m.expectOriginal();
  }
#if ENV_INCLUDE_GPS == 1
  for(bool save_ok:{false,true}) {
    MyMesh m;char reply[160]={};m.storage_accepts=save_ok;
    assert(m.gps("on",reply));++checks;
    assert(error(reply)!=save_ok && m.saves==1);
    assert(sensors.gps==(save_ok?"1":"0"));
    if(!save_ok)m.laterSaveCannotResurrectFailure();
  }
#endif
  // Checked RXPS applies roll back hardware too, and keep retrying a busy
  // rollback until the saved (including disabled) duty-cycle mode is restored.
  for(const char* value:{"10000 20000","level 2","off"})for(bool save_ok:{false,true}) {
    MyMesh m;char reply[160]={};m.storage_accepts=save_ok;
    assert(m.applyAndSaveRxPowerSaving(value,reply)==save_ok);++checks;
    if(error(reply)==save_ok || m.saves!=1) fprintf(stderr,"RXPS %s: %s, saves=%u\n",value,reply,m.saves);
    assert(error(reply)!=save_ok && m.saves==1);
    if(!save_ok) {
      assert(!radio_driver.rxps && radio_driver.rx==3000 && radio_driver.sleep==4000);
      m.laterSaveCannotResurrectFailure();
    } else assert(radio_driver.rxps==bool(m.durable.rx_powersaving_enabled));
  }
  {
    MyMesh m;char reply[160]={};m.storage_accepts=false;
    radio_driver.results={true,false,false,true};
    assert(!m.applyAndSaveRxPowerSaving("10000 20000",reply));++checks;
    m.expectOriginal();assert(m.saved_radio_apply_pending && radio_driver.rxps);
    m.recover();assert(m.saved_radio_apply_pending && radio_driver.rxps);
    m.recover();assert(!m.saved_radio_apply_pending && !radio_driver.rxps);
    assert(radio_driver.rx==3000 && radio_driver.sleep==4000);
  }
  {
    MyMesh m;char reply[160]={};radio_driver.results={false};
    assert(!m.applyAndSaveRxPowerSaving("10000 20000",reply));++checks;
    assert(m.saves==0);m.expectOriginal();
  }
  // Boosted gain uses the actual setter and the shared saved-radio recovery.
  // Cover both directions, rejected saves, busy restoration, and unsupported radios.
  for(bool initial:{false,true})for(bool save_ok:{false,true}) {
    MyMesh m;m.storage_accepts=save_ok;
    m._prefs.rx_boosted_gain=m.durable.rx_boosted_gain=initial;
    radio_driver.boosted_gain=initial;
    assert(m.applyAndSaveRxBoostedGain(!initial)==save_ok);++checks;
    const bool expected=save_ok?!initial:initial;
    assert(m._prefs.rx_boosted_gain==expected && m.durable.rx_boosted_gain==expected);
    assert(radio_driver.boosted_gain==expected && !m.saved_radio_apply_pending);
    assert(m.saves==1 && radio_driver.gain_calls==(save_ok?1U:2U));
    m.storage_accepts=true;assert(m.saveAdvertName("later"));
    assert(m.durable.rx_boosted_gain==expected);
  }
  for(bool initial:{false,true}) {
    MyMesh m;m.storage_accepts=false;
    m._prefs.rx_boosted_gain=m.durable.rx_boosted_gain=initial;
    radio_driver.boosted_gain=initial;
    radio_driver.gain_results={true,false,false,true};
    m.radio_apply_retry_at=500;m.radio_apply_failures=3;
    assert(!m.applyAndSaveRxBoostedGain(!initial));++checks;
    m.expectOriginal();
    assert(m.saved_radio_apply_pending && radio_driver.boosted_gain!=initial);
    assert(m.radio_apply_retry_at==0 && m.radio_apply_failures==0);
    m.recover();
    assert(m.saved_radio_apply_pending && radio_driver.boosted_gain!=initial);
    assert(m.radio_apply_retry_at!=0 && m.radio_apply_failures!=0);
    m.recover();
    assert(!m.saved_radio_apply_pending && radio_driver.boosted_gain==initial);
    assert(m.radio_apply_retry_at==0 && m.radio_apply_failures==0);
    assert(m.saves==1 && radio_driver.gain_calls==4);
    m.storage_accepts=true;assert(m.saveAdvertName("later"));
    assert(m.durable.rx_boosted_gain==initial);
  }
  {
    MyMesh m;radio_driver.gain_results={false};
    assert(!m.applyAndSaveRxBoostedGain(true));++checks;
    assert(m.saves==0 && !radio_driver.boosted_gain && !m.saved_radio_apply_pending);
    m.expectOriginal();
  }
  {
    MyMesh m;radio_driver.gain_supported=false;radio_driver.gain_results={false};
    assert(!m.applyAndSaveRxBoostedGain(true));++checks;
    assert(m.saves==0 && radio_driver.gain_calls==0);m.expectOriginal();
    m.saved_radio_apply_pending=true;m.recover();
    assert(!m.saved_radio_apply_pending && radio_driver.gain_calls==0);
  }
  for(bool save_ok:{false,true}) {
    MyMesh m;m._radio_available=false;m.storage_accepts=save_ok;
    assert(m.applyAndSaveRxBoostedGain(true)==save_ok);++checks;
    assert(m.saves==1 && radio_driver.gain_calls==0 && !m.saved_radio_apply_pending);
    assert(m._prefs.rx_boosted_gain==save_ok && m.durable.rx_boosted_gain==save_ok);
  }
  for(bool initial:{false,true})for(bool save_ok:{false,true}) {
    MyMesh m;char reply[160]={};m.storage_accepts=save_ok;
    m._prefs.powersaving_enabled=m.durable.powersaving_enabled=initial;
    sensors.powersaving_enabled=initial;
    assert(m.applyAndSavePowerSaving(initial?"off":"on",reply)==save_ok);++checks;
    assert(error(reply)!=save_ok && m.saves==1);
    const bool expected=save_ok?!initial:initial;
    assert(m._prefs.powersaving_enabled==expected && m.durable.powersaving_enabled==expected);
    assert(sensors.powersaving_enabled==expected);
    m.storage_accepts=true;assert(m.saveAdvertName("later"));
    assert(m.durable.powersaving_enabled==expected);
  }
  printf("%u Companion persistence scenarios passed (GPS=%d)\n",checks,ENV_INCLUDE_GPS);
}
'''


class CompanionPrefsTransactionTests(unittest.TestCase):
    def test_production_setters_roll_back_failed_saves(self):
        text = SOURCE.read_text(encoding='utf-8')
        header = (SOURCE.parent / 'MyMesh.h').read_text(encoding='utf-8')
        frames = (
            'CMD_SET_ADVERT_NAME && len >= 2', 'CMD_SET_ADVERT_LATLON && len >= 9',
            'CMD_SET_TUNING_PARAMS', 'CMD_SET_OTHER_PARAMS',
            'CMD_SET_PATH_HASH_MODE && len >= 3 && cmd_frame[1] == 0',
            'CMD_SET_DEVICE_PIN && len >= 5', 'CMD_SET_CUSTOM_VAR && len >= 4',
            'CMD_SET_DEFAULT_FLOOD_SCOPE && len >= 1', 'CMD_SET_AUTOADD_CONFIG',
        )
        web = (
            'strcmp(key, "name") == 0',
            'strcmp(key, "lat") == 0 || strcmp(key, "lon") == 0',
            'strcmp(key, "radio") == 0',
            'strcmp(key, "af") == 0 || strcmp(key, "rxdelay") == 0',
            'strcmp(key, "radio.rxgain") == 0 || strcmp(key, "repeat") == 0',
        )
        terminal = (
            'strncmp(config, "af ", 3) == 0',
            'strncmp(config, "name ", 5) == 0 && config[5] != 0',
            'strncmp(config, "lat ", 4) == 0', 'strncmp(config, "lon ", 4) == 0',
        )
        replacements = {
            '@CODES@': '\n'.join(re.findall(r'^#define (?:CMD_SET_\w+|ERR_CODE_\w+)\s+\d+',text,re.M)),
            '@PARSERS@': '\n'.join(extract_braced(text, sig) for sig in (
                'static bool wcParseBool(', 'static bool wcParseDouble(', 'static bool wcCopyValue(',
                'static uint32_t nextRadioApplyRetryDelay(',
            )),
            '@SAVE_PREFERENCE@': extract_braced(header, 'template <typename T> bool savePreference('),
            '@METHODS@': '\n'.join(extract_braced(text,sig) for sig in (
                'bool MyMesh::saveAdvertName(', 'bool MyMesh::saveAdvertLocation(',
                'bool MyMesh::applyAndSaveRxPowerSaving(',
                'bool MyMesh::applyAndSaveRxBoostedGain(',
                'bool MyMesh::applyAndSavePowerSaving(',
            )),
            '@FRAMES@': ' else '.join(extract_braced(text, f'if (cmd_frame[0] == {cmd})') for cmd in frames),
            '@WEB@': ' else '.join(extract_braced(text,f'if ({expr})') for expr in web),
            '@TERMINAL@': ' else '.join(extract_braced(text,f'if ({expr})') for expr in terminal),
            '@GPS@': extract_braced(text,'if (gps_value)'),
            '@RECOVERY@': extract_braced(text,'if (!command_radio_apply_pending && saved_radio_apply_pending && !hasOutbound()'),
        }
        harness = HARNESS
        for marker,value in replacements.items():
            harness = harness.replace(marker,value)
        with tempfile.TemporaryDirectory(prefix='companion-prefs-') as tmp:
            root = Path(tmp)
            (root/'Arduino.h').write_text('#pragma once\n#include <cstdint>\n#include <cmath>\n')
            cpp = root/'test.cpp'
            cpp.write_text(harness,encoding='utf-8')
            binary = root/('test.exe' if os.name=='nt' else 'test')
            for gps in (0,1):
                with self.subTest(gps=gps):
                    subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror',
                                    '-Wno-sign-compare','-Wno-type-limits',f'-DENV_INCLUDE_GPS={gps}',
                                    '-I',str(root),'-I',str(ROOT/'src'),str(cpp),'-o',str(binary)],check=True)
                    subprocess.run([str(binary)],check=True)


if __name__=='__main__':
    unittest.main()
