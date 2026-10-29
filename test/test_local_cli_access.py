"""Compile real Companion command dispatch with local and LoRa callers."""
from pathlib import Path
from itertools import product
import os
import subprocess
import tempfile
import unittest
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
# MinGW does not ship these runtimes; keep sanitizer coverage on Linux.
SANITIZER_FLAGS = [] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-pie", "-no-pie"]

PREAMBLE = r'''
#include <cassert>
#include <helpers/TempRadioReplyBarrier.h>
#include <helpers/RadioPresetConfig.h>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <initializer_list>
#include <string>
#define TBEAM_SX1262 1
#include <helpers/CLICommandUtils.h>
#include <helpers/RadioProfileCommandUtils.h>
#include <helpers/BatteryChargeCLI.h>
#include <helpers/CompanionLostReply.h>
#include <helpers/CompanionTimezonePrefs.h>
#include "companion_fleet_dispatch.h"
#include "battery_calibration_cli_stub.h"
#define ESP32 1
#define WIFI_SSID "test"
#define WITH_MQTT_BRIDGE 1
#define PRV_KEY_SIZE 64
#define MAX_MQTT_SLOTS 2
#define MAX_LORA_TX_POWER 22
#define TXT_TYPE_CLI_COMMAND 1
uint32_t millis() { return 0; }
namespace mesh {
enum class RadioParamApplyResult { APPLIED, BUSY, FAILED };
// The real Morse parser, capture paging and ownership are executed by their
// dedicated tests. This fixture isolates unrelated local/remote CLI access.
template <typename Radio>
bool handleMorseCommand(Radio*, const char*, char*, size_t) { return false; }
struct MorseReplyHandoff {
  unsigned generation() const { return 0; }
  void beginCommand(const uint8_t*) {}
  void endCommand() {}
};
template <typename Radio>
bool handleMorseReplyCommand(MorseReplyHandoff&, Radio*, const char*, char*, size_t, uint32_t) { return false; }
struct RadioProfileCLI {
  bool remote_origin=false;
  bool handle(const char*, char*, size_t, bool remote=false) { remote_origin=remote;return false; }
  static bool parseSuffix(const char* input, unsigned fields, char* legacy, size_t size, uint16_t& preamble) {
    return cli::parseRadioPreambleSuffix(input, fields, legacy, size, preamble);
  }
  bool acceptsPrimary(float, float, uint8_t, uint8_t, uint16_t) { return true; }
  bool savePrimaryPreamble(uint16_t) { return true; }
  bool restorePrimaryPreamble(uint16_t) { return true; }
  uint16_t primaryPreamble() const { return 0; }
  void appendSavedPreamble(char*, size_t, uint8_t, float) {}
  void appendPrimaryChirpWarning(char*, size_t, uint8_t, float, uint16_t) const {}
};
// Radio presets are local-only on companions; their parser has its own suite.
struct RadioPresetCLI {
  unsigned local_calls=0;
  bool handle(const char*, char*, size_t, bool remote) { assert(!remote); ++local_calls; return false; }
};
namespace companion {
// Retry settings have their own test harness; this one exercises local/remote
// dispatch around that branch of the production command handler.
template <typename Prefs, typename Save, typename Cancel>
bool handleRetryCommand(Prefs&, const char*, char*, size_t, Save, Cancel) { return false; }
}
struct Packet {};
void resetLazyPersistenceAfterSuccess(unsigned& when, uint8_t& failures) { when=0; failures=0; }
struct Utils {
  static void toHex(char* out,const uint8_t* data,size_t len) {
    for(size_t i=0;i<len;++i) sprintf(out+2*i,"%02X",data[i]);
  }
};
}
struct ContactInfo {
  bool allowed=true;
  struct { uint8_t pub_key[32] = {}; } id;
  bool isRemoteCLIAllowed() const { return allowed; }
};
struct WiFiSetupPortal {
  static bool loadStoredCredentials(char*,size_t,char* password,size_t size) {
    snprintf(password,size,"wifi-secret");return true;
  }
};
struct Identity {
  int writeTo(uint8_t* out,size_t len) { memset(out,0xA5,len);return len; }
};
struct Store {
  int erased=0; bool success=true;
  bool formatFileSystem(){++erased;return success;}
};
struct Prefs {
  uint8_t lost_reply=0, client_repeat=0;
  bool rx_powersaving_enabled=false;
  float tx_delay_factor=0.5f, direct_tx_delay_factor=0.2f;
  uint8_t interference_threshold=0, agc_reset_interval=0;
  float tz_offset=0;
  float freq=910, bw=125, airtime_factor=1, rx_delay_base=1;
  uint8_t sf=7, cr=5, path_hash_mode=0, multi_acks=0, rx_ps_level=0, rx_ps_preamble=16;
  uint32_t rx_ps_rx_us=1, rx_ps_sleep_us=1;
  int8_t tx_power_dbm=10;
  char node_name[32]="test";
  Prefs* getRadioPrefs(){return this;}
  bool handleCommand(const char*,uint32_t,char*,bool=true){return false;}
  bool isDirty() const {return false;}
  void clearDirty(){}
};
struct Board {
  int power_offs=0;
  uint16_t charge_target=4200;
  unsigned charge_writes=0;
  const char* getBatteryChargeTargetOptions() const {return "4.10,4.20";}
  bool batteryChargeTargetRestoreFailed() const {return false;}
  const char* getBatteryChargeTargetUnsupportedReason() const {return "unsupported";}
  bool getBatteryChargeTarget(uint16_t& mv){mv=charge_target;return true;}
  bool supportsBatteryChargeTarget(uint16_t mv){return mv==4100 || mv==4200;}
  bool setBatteryChargeTarget(uint16_t mv){++charge_writes;charge_target=mv;return true;}
  bool rebootToUf2Bootloader(){return false;}
  bool handleCommand(const char*,uint32_t,char*,bool=true){return false;}
  void powerOff(){++power_offs;}
} board;
struct Radio {
  mesh::RadioParamApplyResult accepted=mesh::RadioParamApplyResult::APPLIED;
  int8_t power=10;
  bool power_verified=true;
  bool isTxPowerValid(int8_t) const {return true;}
  bool isTxPowerApplied(int8_t requested) const {return power_verified && power==requested;}
  bool setTxPower(int requested){power=requested;power_verified=true;return true;}
  mesh::RadioParamApplyResult trySetTxPower(int requested){
    if(accepted==mesh::RadioParamApplyResult::APPLIED){power=requested;power_verified=true;}
    if(accepted==mesh::RadioParamApplyResult::FAILED)power_verified=false;
    return accepted;
  }
} radio_driver;
struct AdvertDataParser {static bool isValidName(const char*){return true;}};
struct StrHelper {static void strncpy(char* out,const char* in,size_t len){::strncpy(out,in,len);}};
bool recalcRxPowerSavingFromLevel(uint8_t,uint8_t,float,uint8_t,uint32_t*,uint32_t*){return true;}
struct StatsFormatHelper {
  template<class... T> static void formatCoreStats(char* out,T&&...){strcpy(out,"core");}
  template<class... T> static void formatRadioStats(char* out,T&&...){strcpy(out,"radio");}
  template<class... T> static void formatRadioDiag(char* out,T&&...){strcpy(out,"diag");}
  template<class... T> static void formatPacketStats(char* out,T&&...){strcpy(out,"packets");}
};
struct WebConfigServer {
  static bool formatWiFiPassword(char* out,size_t len){snprintf(out,len,"> wifi-secret");return true;}
};
const char* FIRMWARE_VERSION="test";
const char* FIRMWARE_BUILD_DATE="test";
struct CommonRadioPrefs {static bool isValidBandwidth(float){return true;}};
struct MyMesh : CompanionFleetFixture {
  mesh::TempRadioReplyBarrier _primary_radio_reply;
  bool _primary_radio_blocked=false,_primary_radio_reply_admission=false;

  mesh::MorseReplyHandoff _morse_reply;
  bool _morse_reply_admission = false;
  mesh::RadioProfileCLI _radio_profiles;
  mesh::RadioPresetCLI _radio_presets;
  Identity self_id;
  Store store; Store* _store=&store;
  Prefs _prefs;
  struct {char mqtt_slot_password[2][64]={"mqtt-secret","other"};
          char mqtt_slot_token[2][64]={"mqtt-token","other-token"};} _mqtt_prefs;
  unsigned dirty_contacts_expiry=1; uint8_t dirty_contacts_failures=1;
  int value=0; int* _ms=&value; int* _radio=&value; int* _mgr=&value;
  uint16_t _err_flags=0;
  bool _radio_available=false, save_ok=true;
  bool saved_radio_apply_pending=false, command_radio_apply_pending=false, tx_power_apply_pending=false;
  uint32_t tx_power_apply_retry_at=0;
  unsigned radio_apply_retry_at=0;
  uint8_t radio_apply_failures=0;
  int resets=0, saves=0;
  enum class RadioSettingResult { Saved, RadioRejected, SaveFailed, SaveUnconfirmed };
  RadioSettingResult applyAndSaveTxPower(int8_t);
  RadioSettingResult applyAndSaveRadioParams(float,float,uint8_t,uint8_t,uint8_t,uint16_t,bool=false);
  void queueSavedRadioApply();
  bool isValidClientRepeatFreq(uint32_t) const {return true;}
  mesh::RadioParamApplyResult tryApplyRadioParams(float,float,uint8_t,uint8_t) {return mesh::RadioParamApplyResult::APPLIED;}
  bool applySavedRadioParams(){return true;}
  bool saveAdvertName(const char*);
  bool handleCommand(const char*,uint32_t,char*);
  bool handleDirectCommand(const char*,char*,size_t);
  void onCLICommandRecv(const ContactInfo&,mesh::Packet*,uint32_t,const char*,char*);
  bool handleLocalControlCommand(const char* text,char* reply,size_t) {
    if(!strcmp(text,"get mqtt1.password")||!strcmp(text,"get mqtt1.token")) {
      strcpy(reply,"> ********");return true;
    }
    return false;
  }
  void stopContactsIterator(){}
  void onContactCacheFlushed();
  void cancelAllFloodRetries(){}
  void resetContacts(){++resets;}
  bool savePrefs(){++saves;return save_ok;}
  @SAVE_PREFERENCE@
  int getTotalAirTime(){return 0;}
  int getReceiveAirTime(){return 0;}
  int getNumSentFlood(){return 0;}
  int getNumSentDirect(){return 0;}
  int getNumRecvFlood(){return 0;}
  int getNumRecvDirect(){return 0;}
  bool hasOutbound(){return false;}
  void markConnectionActive(const ContactInfo&){}
  void queueMessage(const ContactInfo&,int,mesh::Packet*,uint32_t,const void*,int,const char*){}
  // This harness exercises radio/local identity dispatch. Notification parsing
  // and permissions run against the real controller in their native suite.
  bool handleNotificationCommand(const char*,char*,size_t){return false;}
};
'''

SCENARIOS = r'''
int main() {
  MyMesh node;
  char reply[160] = {};
  assert(node.handleCommand("set lost.reply yes",0,reply));
  assert(node._prefs.lost_reply==2 && !strcmp(reply,"> lost.reply yes"));
  assert(node.handleCommand("get lost.reply",0,reply) && !strcmp(reply,"> yes"));
  const int lost_saves=node.saves;
  for(uint32_t stamp:{0u,100u}) {
    reply[0]=0;
    node.onCLICommandRecv(ContactInfo{},nullptr,stamp,"set lost.reply no",reply);
    assert(node._prefs.lost_reply==2 && node.saves==lost_saves);
    reply[0]=0;
    node.onCLICommandRecv(ContactInfo{},nullptr,stamp,"A7|get lost.reply",reply);
    assert(!strstr(reply,"> yes") && node.saves==lost_saves);
  }
  node.save_ok=false;
  assert(node.handleCommand("set lost.reply no",0,reply));
  assert(node._prefs.lost_reply==2 && strstr(reply,"could not save"));
  node.save_ok=true;
  for (uint32_t timestamp : {0u, 100u}) {
    node.onCLICommandRecv(ContactInfo{}, nullptr, timestamp,
                          "fleet send 1 all set radio2 off", reply);
    assert(strstr(reply, "requires local Companion access"));
  }
  assert(node.handleCommand("set charge.voltage 4.1",0,reply));
  assert(board.charge_target==4100 && strstr(reply,"saved"));
  for(uint32_t stamp:{0u,100u}) {
    node.onCLICommandRecv(ContactInfo{},nullptr,stamp,"A7|set charge.voltage 4.2",reply);
    assert(board.charge_target==4200 && !strcmp(reply,"A7|OK - charge.voltage 4.200 V (saved)"));
    const unsigned writes=board.charge_writes;
    node.onCLICommandRecv(ContactInfo{},nullptr,stamp,"A7|set charge.voltage 3.65",reply);
    assert(board.charge_writes==writes && !strncmp(reply,"A7|Error: unsupported target",27));
    node.onCLICommandRecv(ContactInfo{false},nullptr,stamp,"set charge.voltage 4.1",reply);
    assert(board.charge_writes==writes && board.charge_target==4200);
  }
  const char* local[] = {"get password", "get prv.key", "get wifi.pwd", "get mqtt1.password",
      "get mqtt1.token", "stats-core", "stats-radio", "stats-radio-diag", "stats-packets"};
  for(const char* command:local) {
    memset(reply,0,sizeof(reply));
    assert(node.handleCommand(command,0,reply));
    assert(strlen(reply)>0);
    if(!strcmp(command,"get password")) assert(strstr(reply,"no admin password"));
    if(!strcmp(command,"get prv.key")) {
#if ENABLE_PRIVATE_KEY_EXPORT
      assert(strlen(reply)==130 && !strncmp(reply,"> A5A5",6));
#else
      assert(strstr(reply,"disabled"));
#endif
    }
    if(!strcmp(command,"get wifi.pwd")) assert(!strcmp(reply,"> wifi-secret"));
    if(!strcmp(command,"get mqtt1.password")) assert(!strcmp(reply,"> mqtt-secret"));
    if(!strcmp(command,"get mqtt1.token")) assert(!strcmp(reply,"> mqtt-token"));
    memset(reply,0,sizeof(reply));
    node.handleCommand(command,100,reply);
    assert(!strstr(reply,"secret") && !strstr(reply,"token") && !strstr(reply,"A5A5"));
    // In particular, an authenticated LoRa packet carrying timestamp zero
    // still enters the production receiver with remote restrictions.
    memset(reply,0,sizeof(reply));
    node.onCLICommandRecv(ContactInfo{},nullptr,0,command,reply);
    assert(!strstr(reply,"secret") && !strstr(reply,"token") && !strstr(reply,"A5A5"));
  }
  const unsigned preset_calls=node._radio_presets.local_calls;
  assert(node.handleCommand("set freq 915.25",0,reply));
  assert(!node._radio_profiles.remote_origin);
  assert(node._radio_presets.local_calls==preset_calls+1);
  assert(node._prefs.freq==915.25f && !strncmp(reply,"OK",2));
  assert(node.handleCommand("set freq 920",100,reply));
  assert(node._radio_profiles.remote_origin);
  assert(node._radio_presets.local_calls==preset_calls+1); // never over LoRa
  assert(node._prefs.freq==920.0f && !strcmp(reply,"OK"));
  for(uint32_t stamp:{0u,100u}) {
    reply[0]=0;
    node.onCLICommandRecv(ContactInfo{},nullptr,stamp,"A7|set freq 921.125",reply);
    assert(node._radio_profiles.remote_origin); // even a zero-timestamp LoRa command
    assert(node._radio_presets.local_calls==preset_calls+1);
    assert(node._prefs.freq==921.125f && !strcmp(reply,"A7|OK"));
    assert(node._prefs.bw==125.0f && node._prefs.sf==7 && node._prefs.cr==5);
    for(const char* bad:{"set freq 149.9","set freq 2500.1","set freq 920oops",
                         "set freq NaN","set freq inf","set freq 1e3","set freq "}) {
      const int saves=node.saves;
      reply[0]=0;
      node.onCLICommandRecv(ContactInfo{},nullptr,stamp,bad,reply);
      assert(strstr(reply,"Error") && node._prefs.freq==921.125f && node.saves==saves);
    }
  }
  const int saves=node.saves;
  node.onCLICommandRecv(ContactInfo{false},nullptr,100,"set freq 930",reply);
  assert(node._prefs.freq==921.125f && node.saves==saves);
  node.save_ok=false;
  for(uint32_t stamp:{0u,100u}) {
    assert(node.handleCommand("set freq 920",stamp,reply));
    assert(node._prefs.freq==921.125f && strstr(reply,"Error"));
  }
  assert(!node.handleCommand("erase",100,reply));
  node.onCLICommandRecv(ContactInfo{},nullptr,0,"erase",reply);
  assert(node.store.erased==0);
  assert(node.handleCommand("erase",0,reply));
  assert(node.store.erased==1 && node.resets==1 && node.dirty_contacts_expiry==0
         && node.dirty_contacts_failures==0);
  assert(strstr(reply,"OK"));
  node.store.success=false;
  node.dirty_contacts_expiry=2; node.dirty_contacts_failures=3;
  assert(node.handleCommand("erase",0,reply));
  assert(node.resets==1 && strstr(reply,"Err") && node.dirty_contacts_expiry==2
         && node.dirty_contacts_failures==3);
  for(const char* command:{"poweroff","shutdown"}) {
    const int previous=board.power_offs;
    node.handleCommand(command,100,reply);
    assert(board.power_offs==previous);
    assert(node.handleCommand(command,0,reply));
    assert(!strcmp(reply,"OK") && board.power_offs==previous+1);
  }
  // Both directly attached and on-air CLI replies describe accepted durable
  // intent. Request prefixes and the fixed 160-byte reply boundary survive.
  node.save_ok=true;node._radio_available=true;
  radio_driver.accepted=mesh::RadioParamApplyResult::BUSY;
  for(uint32_t stamp:{0u,100u}) {
    char bounded[162];memset(bounded,0x5A,sizeof(bounded));
    assert(node.handleCommand("A7|set tx 17",stamp,bounded+1));
    assert(!strcmp(bounded+1,"A7|OK - saved; pending"));
    assert(bounded[0]==0x5A && bounded[161]==0x5A);
    assert(node._prefs.tx_power_dbm==17 && node.tx_power_apply_pending);
#if defined(MESH_HIL_TX_FREQ_MHZ)
    assert(node.handleCommand("A7|get tx.status",stamp,bounded+1));
    assert(!strcmp(bounded+1,"A7|> desired=17,apply=pending"));
    assert(bounded[0]==0x5A && bounded[161]==0x5A);
#else
    assert(!node.handleCommand("A7|get tx.status",stamp,bounded+1));
#endif
    node.onCLICommandRecv(ContactInfo{},nullptr,stamp,"B8|set tx 12",reply);
    assert(!strcmp(reply,"B8|OK - saved; pending") && node._prefs.tx_power_dbm==12);
  }
  radio_driver.accepted=mesh::RadioParamApplyResult::APPLIED;
  assert(node.handleCommand("set tx 15",0,reply) && !strcmp(reply,"OK"));
  assert(!node.tx_power_apply_pending);
#if defined(MESH_HIL_TX_FREQ_MHZ)
  assert(node.handleCommand("get tx.status",0,reply) && !strcmp(reply,"> desired=15,apply=applied"));
  radio_driver.power_verified=false;
  assert(node.handleCommand("get tx.status",0,reply) && !strcmp(reply,"> desired=15,apply=pending"));
  radio_driver.power_verified=true;node._radio_available=false;
  assert(node.handleCommand("get tx.status",0,reply) && !strcmp(reply,"> desired=15,apply=pending"));
  node._radio_available=true;
  assert(node.handleCommand("get tx.status",0,reply) && !strcmp(reply,"> desired=15,apply=applied"));
#else
  assert(!node.handleCommand("get tx.status",0,reply));
#endif
  // Exercise the real timezone dispatch and typed transaction helper, not a
  // fixture-only setter: a failed save must preserve the exact fraction.
  node.save_ok=true;
  assert(node.handleCommand("set tz.offset 5.75",0,reply) && !strcmp(reply,"OK"));
  assert(node._prefs.tz_offset==5.75f);
  assert(node.handleCommand("get tz.offset",0,reply) && !strcmp(reply,"> 5.75"));
  node.save_ok=false;
  const int timezone_saves=node.saves;
  assert(node.handleCommand("set tz.offset -3.5",0,reply));
  assert(strstr(reply,"Error") && node.saves==timezone_saves+1);
  assert(node._prefs.tz_offset==5.75f);
  for(const char* bad:{"nan","inf","5.75junk","15","-12.25"}) {
    const std::string command=std::string("set tz.offset ")+bad;
    assert(node.handleCommand(command.c_str(),0,reply));
    assert(strstr(reply,"Error") && node.saves==timezone_saves+1);
    assert(node._prefs.tz_offset==5.75f);
  }
}
'''


class LocalCliAccessTest(unittest.TestCase):
    def test_infrastructure_frequency_accepts_local_and_lora_callers(self):
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        anchor = source.index('memcmp(config, "freq ", 5)')
        start = source.rfind('} else if (', 0, anchor) + len('} else ')
        setter = extract_braced(source[start:], "if (")
        # The following optional else-if puts its preprocessor guard just
        # before this branch's closing brace. It is not part of this handler.
        setter = setter.replace("#ifdef WITH_BRIDGE\n", "")
        code = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <initializer_list>
#define MAX_LORA_TX_POWER 22
#include <helpers/CLICommandUtils.h>
#include <helpers/radiolib/RadioPowerLimits.h>
struct Prefs {
  float freq=910.525f,bw=62.5f;
  uint8_t sf=7,cr=5;
  uint16_t primary_radio_preamble=32;
  int8_t tx_power_dbm=30;
} prefs;
Prefs* _prefs=&prefs;
int saves=0;
int schedules=0;
bool save_ok=true;
bool savePrimaryRadioParams(float f,float bw,uint8_t sf,uint8_t cr,uint16_t preamble) {
  ++saves;
  if (!save_ok) return false;
  _prefs->freq=f; _prefs->bw=bw; _prefs->sf=sf; _prefs->cr=cr;
  _prefs->primary_radio_preamble=preamble;
  _prefs->tx_power_dbm=mesh::clampLoRaTxPower(_prefs->tx_power_dbm,f);
  return true;
}
bool applyAndSavePrimaryRadioParams(float f,float bw,uint8_t sf,uint8_t cr,uint16_t preamble,char* reply){
  const auto previous_power=_prefs->tx_power_dbm;
  if(!savePrimaryRadioParams(f,bw,sf,cr,preamble)) {
    strcpy(reply,"Error: radio settings could not be saved");return false;
  }
  ++schedules;
  strcpy(reply,previous_power!=_prefs->tx_power_dbm ?
      "OK - applying in 2s; TX power limited" : "OK - applying in 2s");
  return true;
}
void set(const char* config,uint32_t sender_timestamp,char* reply) {
''' + setter + r'''
}
int main() {
  char reply[160]={};
  for(uint32_t stamp:{0u,100u,0xFFFFFFFFu}) {
    prefs=Prefs{};
    set("freq 915.25",stamp,reply);
    assert(prefs.freq==915.25f && prefs.tx_power_dbm==22);
    assert(strstr(reply,"applying in 2s") && strstr(reply,"TX power limited"));
    assert(prefs.bw==62.5f && prefs.sf==7 && prefs.cr==5);
    for(const char* bad:{"freq 149.9","freq 2500.1","freq 920oops",
                         "freq NaN","freq inf","freq 1e3","freq "}) {
      const int writes=saves;
      set(bad,stamp,reply);
      assert(strstr(reply,"Error") && prefs.freq==915.25f && saves==writes);
    }
    for(const char* boundary:{"freq 150","freq 2500"}) {
      set(boundary,stamp,reply);
      assert(strstr(reply,"OK - applying in 2s"));
    }
  }
  assert(saves==9 && schedules==9);
  const float previous=prefs.freq;
  save_ok=false;
  set("freq 920",0,reply);
  assert(strstr(reply,"Error") && prefs.freq==previous && schedules==9);
  assert(prefs.primary_radio_preamble==32);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "test"
            result = subprocess.run([
                "c++", "-std=c++17", "-x", "c++", "-", "-I"+str(ROOT / "src"),
                *SANITIZER_FLAGS, "-o", str(binary),
            ], input=code, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_infrastructure_password_read_and_browser_transport_limits(self):
        source = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        getter = extract_braced(source, "void CommonCLI::handleGetCmd(")
        password = extract_braced(getter, 'if (strcmp(config, "password") == 0)')
        web = (ROOT / "src/helpers/esp32/WebConfigServer.cpp").read_text()
        limits = extract_braced(web, "static const char* wcCliUnavailable(")
        code = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdint>
struct Prefs { char password[16] = "admin-secret"; } prefs;
Prefs* _prefs = &prefs;
void get(const char* config, uint32_t sender_timestamp, char* reply) {
''' + password + "\n}\n" + limits + r'''
int main() {
  char reply[160] = {};
  get("password",0,reply);
  assert(!strcmp(reply,"> admin-secret"));
  for(uint32_t stamp:{1u,100u,0xFFFFFFFFu}) {
    get("password",stamp,reply);
    assert(strstr(reply,"local connection") && !strstr(reply,"admin-secret"));
  }
  reply[0]=0;get("passwordx",0,reply);assert(!reply[0]);
  assert(wcCliUnavailable("get password",false)==nullptr);
  assert(wcCliUnavailable("get password",true)==nullptr);
  assert(wcCliUnavailable("log",false)!=nullptr);
  assert(wcCliUnavailable("log",true)==nullptr);
  assert(wcCliUnavailable("get acl",true)==nullptr);
  assert(wcCliUnavailable("start ota",true)!=nullptr);
  assert(wcCliUnavailable("clock sync",true)!=nullptr);
}
'''
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "test"
            result = subprocess.run([
                "c++", "-std=c++17", "-include", "initializer_list", "-x", "c++", "-",
                "-include", str(ROOT / "src/helpers/CLICommandUtils.h"),
                *SANITIZER_FLAGS, "-o", str(binary),
            ], input=code, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_real_companion_dispatch_local_binary_and_radio(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        implementation = "\n".join(extract_braced(source, signature) for signature in (
            "static bool isCompanionRadioPrefsCommand(",
            "MyMesh::RadioSettingResult MyMesh::applyAndSaveTxPower(",
            "MyMesh::RadioSettingResult MyMesh::applyAndSaveRadioParams(",
            "void MyMesh::queueSavedRadioApply(",
            "bool MyMesh::saveAdvertName(",
            "void MyMesh::onContactCacheFlushed()",
            "bool MyMesh::handleDirectCommand(", "bool MyMesh::handleCommand(",
            "void MyMesh::onCLICommandRecv("))
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        preamble = PREAMBLE.replace("@SAVE_PREFERENCE@", extract_braced(
            header, "template <typename T> bool savePreference("))
        for export, webconfig, hil in product((0, 1), (False, True), (False, True)):
            with self.subTest(export=export, webconfig=webconfig, hil=hil), tempfile.TemporaryDirectory() as temp:
                binary = Path(temp) / "test"
                result = subprocess.run([
                    "c++", "-std=c++17", "-x", "c++", "-", "-I"+str(ROOT / "src"),
                    "-I"+str(ROOT / "test/fixtures"),
                    "-DENABLE_PRIVATE_KEY_EXPORT="+str(export),
                    "-DMESH_ENABLE_LOST_REPLY=1",
                    *(["-DMESH_HIL_TX_FREQ_MHZ=907.125f"] if hil else []),
                    *(["-DWITH_WEBCONFIG=1"] if webconfig else []),
                    *SANITIZER_FLAGS, "-o", str(binary),
                ], input=preamble+implementation+SCENARIOS, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_each_host_transport_uses_local_dispatch(self):
        for role in ("simple_repeater", "simple_room_server"):
            header = (ROOT / "examples" / role / "MyMesh.h").read_text()
            web = extract_braced(header, "void execAdminCommand(")
            self.assertIn("handleCommand(0, cmd, reply)", web)
            local = extract_braced(header, "void handleLocalCommand(")
            self.assertIn("_command_output = &output", local)
            self.assertIn("handleCommand(0, command, reply)", local)
            self.assertIn("_command_output = nullptr", local)
            stream = extract_braced(header, "void runStreamTerminal(")
            self.assertIn("handleLocalCommand(command, reply, *_web_terminal)", stream)
            main = (ROOT / "examples" / role / "main.cpp").read_text()
            self.assertIn("handleLocalCommand(ethernet_command, reply, ethernet_client)", main)
            self.assertIn("cancelLocalOutput(ethernet_client)", main)
            source = (ROOT / "examples" / role / "MyMesh.cpp").read_text()
            guard = ("sender != nullptr" if role == "simple_repeater"
                     else "gpio_client_index >= 0")
            self.assertIn("if ("+guard+" && sender_timestamp == 0) sender_timestamp = 1;", source)
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        framed = extract_braced(companion, "else if (mesh::companion::isRunCliFrame(")
        self.assertIn("handleCommand(text, 0, reply_buf)", framed)
        sensor = (ROOT / "examples/simple_sensor/SensorMesh.cpp").read_text()
        self.assertIn("if (gpio_client_index >= 0 && sender_timestamp == 0) sender_timestamp = 1;", sensor)


if __name__ == "__main__":
    unittest.main()
