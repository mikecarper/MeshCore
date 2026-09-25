"""Compile real Companion command dispatch with local and LoRa callers."""
from pathlib import Path
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
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <initializer_list>
#include <helpers/CLICommandUtils.h>
#include <helpers/RadioProfileCommandUtils.h>
#define ESP32 1
#define WIFI_SSID "test"
#define WITH_MQTT_BRIDGE 1
#define PRV_KEY_SIZE 64
#define MAX_MQTT_SLOTS 2
#define MAX_LORA_TX_POWER 22
#define TXT_TYPE_CLI_COMMAND 1
namespace mesh {
struct RadioProfileCLI {
  bool remote_origin=false;
  bool handle(const char*, char*, size_t, bool remote=false) { remote_origin=remote;return false; }
  static bool parseSuffix(const char* input, unsigned fields, char* legacy, size_t size, uint16_t& preamble) {
    return cli::parseRadioPreambleSuffix(input, fields, legacy, size, preamble);
  }
  bool acceptsPrimary(float, float, uint8_t, uint8_t, uint16_t) { return true; }
  bool savePrimaryPreamble(uint16_t) { return true; }
  uint16_t primaryPreamble() const { return 0; }
  void appendSavedPreamble(char*, size_t, uint8_t, float) {}
  void appendPrimaryChirpWarning(char*, size_t, uint8_t, float, uint16_t) const {}
};
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
  float tx_delay_factor=0.5f, direct_tx_delay_factor=0.2f;
  uint8_t interference_threshold=0, agc_reset_interval=0;
  int8_t tz_offset=0;
  float freq=910, bw=125, airtime_factor=1, rx_delay_base=1;
  uint8_t sf=7, cr=5, path_hash_mode=0, multi_acks=0, rx_ps_level=0, rx_ps_preamble=16;
  uint32_t rx_ps_rx_us=1, rx_ps_sleep_us=1;
  int8_t tx_power_dbm=10;
  char node_name[32]="test";
  Prefs* getRadioPrefs(){return this;}
  bool handleCommand(const char*,uint32_t,char*){return false;}
  bool isDirty() const {return false;}
  void clearDirty(){}
};
struct Board {
  int power_offs=0;
  bool rebootToUf2Bootloader(){return false;}
  bool handleCommand(const char*,uint32_t,char*){return false;}
  void powerOff(){++power_offs;}
} board;
struct Radio {bool setTxPower(int){return true;}} radio_driver;
struct AdvertDataParser {static bool isValidName(const char*){return true;}};
struct StrHelper {static void strncpy(char* out,const char* in,size_t len){::strncpy(out,in,len);}};
void recalcRxPowerSavingFromLevel(uint8_t,uint8_t,float,uint8_t,uint32_t*,uint32_t*){}
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
struct MyMesh {
  mesh::RadioProfileCLI _radio_profiles;
  Identity self_id;
  Store store; Store* _store=&store;
  Prefs _prefs;
  struct {char mqtt_slot_password[2][64]={"mqtt-secret","other"};
          char mqtt_slot_token[2][64]={"mqtt-token","other-token"};} _mqtt_prefs;
  unsigned dirty_contacts_expiry=1; uint8_t dirty_contacts_failures=1;
  int value=0; int* _ms=&value; int* _radio=&value; int* _mgr=&value;
  uint16_t _err_flags=0;
  bool _radio_available=false, save_ok=true;
  bool saved_radio_apply_pending=false;
  unsigned radio_apply_retry_at=0;
  uint8_t radio_apply_failures=0;
  int resets=0, saves=0;
  enum class RadioSettingResult { Saved, RadioRejected, SaveFailed };
  RadioSettingResult applyAndSaveTxPower(int8_t);
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
  void resetContacts(){++resets;}
  bool savePrefs(){++saves;return save_ok;}
  int getTotalAirTime(){return 0;}
  int getReceiveAirTime(){return 0;}
  int getNumSentFlood(){return 0;}
  int getNumSentDirect(){return 0;}
  int getNumRecvFlood(){return 0;}
  int getNumRecvDirect(){return 0;}
  bool hasOutbound(){return false;}
  void markConnectionActive(const ContactInfo&){}
  void queueMessage(const ContactInfo&,int,mesh::Packet*,uint32_t,const void*,int,const char*){}
};
'''

SCENARIOS = r'''
int main() {
  MyMesh node;
  char reply[160] = {};
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
  assert(node.handleCommand("set freq 915.25",0,reply));
  assert(!node._radio_profiles.remote_origin);
  assert(node._prefs.freq==915.25f && strstr(reply,"reboot to apply"));
  assert(node.handleCommand("set freq 920",100,reply));
  assert(node._radio_profiles.remote_origin);
  assert(node._prefs.freq==920.0f && strstr(reply,"reboot to apply"));
  for(uint32_t stamp:{0u,100u}) {
    reply[0]=0;
    node.onCLICommandRecv(ContactInfo{},nullptr,stamp,"A7|set freq 921.125",reply);
    assert(node._radio_profiles.remote_origin); // even a zero-timestamp LoRa command
    assert(node._prefs.freq==921.125f && !strcmp(reply,"A7|OK - reboot to apply"));
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
  assert(node.store.erased==1 && node.resets==1 && node.dirty_contacts_expiry==0);
  assert(strstr(reply,"OK"));
  node.store.success=false;
  assert(node.handleCommand("erase",0,reply));
  assert(node.resets==1 && strstr(reply,"Err"));
  for(const char* command:{"poweroff","shutdown"}) {
    const int previous=board.power_offs;
    node.handleCommand(command,100,reply);
    assert(board.power_offs==previous);
    assert(node.handleCommand(command,0,reply));
    assert(!strcmp(reply,"OK") && board.power_offs==previous+1);
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
  int8_t tx_power_dbm=30;
} prefs;
Prefs* _prefs=&prefs;
int saves=0;
void savePrefs(){++saves;}
void set(const char* config,uint32_t sender_timestamp,char* reply) {
''' + setter + r'''
}
int main() {
  char reply[160]={};
  for(uint32_t stamp:{0u,100u,0xFFFFFFFFu}) {
    prefs=Prefs{};
    set("freq 915.25",stamp,reply);
    assert(prefs.freq==915.25f && prefs.tx_power_dbm==22);
    assert(strstr(reply,"reboot to apply") && strstr(reply,"TX power limited"));
    assert(prefs.bw==62.5f && prefs.sf==7 && prefs.cr==5);
    for(const char* bad:{"freq 149.9","freq 2500.1","freq 920oops",
                         "freq NaN","freq inf","freq 1e3","freq "}) {
      const int writes=saves;
      set(bad,stamp,reply);
      assert(strstr(reply,"Error") && prefs.freq==915.25f && saves==writes);
    }
    for(const char* boundary:{"freq 150","freq 2500"}) {
      set(boundary,stamp,reply);
      assert(strstr(reply,"OK - reboot to apply"));
    }
  }
  assert(saves==9);
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
            "bool MyMesh::saveAdvertName(",
            "bool MyMesh::handleDirectCommand(", "bool MyMesh::handleCommand(",
            "void MyMesh::onCLICommandRecv("))
        for export, webconfig in ((0, False), (1, False), (0, True), (1, True)):
            with self.subTest(export=export, webconfig=webconfig), tempfile.TemporaryDirectory() as temp:
                binary = Path(temp) / "test"
                result = subprocess.run([
                    "c++", "-std=c++17", "-x", "c++", "-", "-I"+str(ROOT / "src"),
                    "-DENABLE_PRIVATE_KEY_EXPORT="+str(export),
                    *(["-DWITH_WEBCONFIG=1"] if webconfig else []),
                    *SANITIZER_FLAGS, "-o", str(binary),
                ], input=PREAMBLE+implementation+SCENARIOS, text=True, capture_output=True)
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
