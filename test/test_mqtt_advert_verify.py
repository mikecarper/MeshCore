"""Exercise the production MQTT advert gate and transactional CLI on the host."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def compile_run(source):
    with tempfile.TemporaryDirectory(prefix="mqtt-advert-verify-") as directory:
        cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test"
        cpp.write_text(source, encoding="utf-8")
        result = subprocess.run([
            "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-fno-pie", "-no-pie", "-I", str(ROOT / "src"),
            str(cpp), "-o", str(binary),
        ], text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        subprocess.run([str(binary)], check=True)


class MqttAdvertVerifyTests(unittest.TestCase):
    def test_actual_queue_gate_rejects_bad_and_unsigned_tails_only_when_enabled(self):
        source = (ROOT / "src/helpers/bridges/MQTTBridge.cpp").read_text()
        method = extract_braced(source, "void MQTTBridge::onPacketReceived(")
        compile_run(r'''
#include <cassert>
#include <initializer_list>
#include <cstdint>
#include <cstring>
#include <helpers/AdvertSignature.h>
constexpr unsigned PAYLOAD_TYPE_ADVERT=4, PAYLOAD_TYPE_TEXT=2;
static unsigned verifies=0;
static bool valid_signature=true;
namespace mesh {
struct Packet {
  uint8_t payload[184] = {};
  int payload_len=100;
  unsigned type=PAYLOAD_TYPE_ADVERT;
  unsigned getPayloadType() const { return type; }
};
struct Identity {
  const uint8_t* pub;
  explicit Identity(const uint8_t* p) : pub(p) {}
  bool verify(const uint8_t* sig, const uint8_t* message, int size) {
    ++verifies;
    assert(sig == pub+36 && size>=36 && size<=68);
    assert(!memcmp(pub,message,36));
    if(size>36) assert(!memcmp(pub+100,message+36,size-36));
    return valid_signature;
  }
};
}
struct MQTTBridge {
  struct Obs { bool mqtt_packets_enabled=true, mqtt_rx_enabled=true, mqtt_advert_verify=false; } obs;
  Obs* _obs=&obs;
  bool _initialized=true, type_allowed=true, scope_allowed=true;
  struct Stop { bool value=false; bool load(int)const{return value;} } _stop_requested;
  unsigned _filtered_packets=0, _bad_sig_adverts=0, queued=0;
  bool shouldQueuePacketType(unsigned, bool& filtered) { filtered=!type_allowed; return type_allowed; }
  bool allowsPacket(mesh::Packet*) { return scope_allowed; }
  void queuePacket(mesh::Packet*,bool tx){assert(!tx);++queued;}
  void onPacketReceived(mesh::Packet*);
};
namespace std { constexpr int memory_order_acquire=0; }
''' + method + r'''
int main() {
  MQTTBridge bridge; mesh::Packet packet;
  for(unsigned i=0;i<sizeof(packet.payload);++i) packet.payload[i]=uint8_t(i);
  // Default off preserves the observer's existing publish-as-heard behavior.
  valid_signature=false; bridge.onPacketReceived(&packet);
  assert(bridge.queued==1 && verifies==0 && bridge._bad_sig_adverts==0);
  bridge.obs.mqtt_advert_verify=true;
  bridge.onPacketReceived(&packet);
  assert(bridge.queued==1 && verifies==1 && bridge._bad_sig_adverts==1);
  valid_signature=true; packet.payload_len=132;
  bridge.onPacketReceived(&packet);
  assert(bridge.queued==2 && verifies==2);
  // Valid signed prefixes cannot bless an appended unsigned byte.
  for(int size : {99,133,184}) {
    packet.payload_len=size; bridge.onPacketReceived(&packet);
    assert(bridge.queued==2 && verifies==2);
  }
  assert(bridge._bad_sig_adverts==4);
  packet.type=PAYLOAD_TYPE_TEXT; bridge.onPacketReceived(&packet);
  assert(bridge.queued==3 && verifies==2);
  packet.type=PAYLOAD_TYPE_ADVERT; packet.payload_len=100;
  bridge.scope_allowed=false; bridge.onPacketReceived(&packet);
  assert(bridge.queued==3 && verifies==2 && bridge._filtered_packets==1);
  bridge.scope_allowed=true; bridge.type_allowed=false; bridge.onPacketReceived(&packet);
  assert(bridge.queued==3 && verifies==2 && bridge._filtered_packets==2);
}
''')

    def test_actual_observer_setter_rejects_typos_and_rolls_back_failed_saves(self):
        source = (ROOT / "src/helpers/CommonCLI_Observer.cpp").read_text()
        branch = extract_braced(source, 'if (memcmp(config, "mqtt.advert.verify ", 19) == 0)')
        compile_run(r'''
#include <cassert>
#include <cstdint>
#include <cstring>
struct CLI {
  struct { uint8_t mqtt_advert_verify=0; } _mqtt_prefs;
  bool save_ok=true; unsigned saves=0;
  bool saveObserverPrefs(){++saves;return save_ok;}
  bool set(const char* config,char* reply) {
''' + branch + r'''
    else return false;
    return true;
  }
};
int main() {
  CLI cli; char reply[160]={};
  assert(cli.set("mqtt.advert.verify on",reply));
  assert(cli.saves==1 && cli._mqtt_prefs.mqtt_advert_verify==1 && !strcmp(reply,"OK"));
  assert(cli.set("mqtt.advert.verify onx",reply));
  assert(cli.saves==1 && cli._mqtt_prefs.mqtt_advert_verify==1 && !strncmp(reply,"Error:",6));
  cli.save_ok=false; assert(cli.set("mqtt.advert.verify off",reply));
  assert(cli.saves==2 && cli._mqtt_prefs.mqtt_advert_verify==1 && !strncmp(reply,"Error:",6));
  cli.save_ok=true; assert(cli.set("mqtt.advert.verify off",reply));
  assert(cli.saves==3 && cli._mqtt_prefs.mqtt_advert_verify==0 && !strcmp(reply,"OK"));
}
''')


if __name__ == "__main__":
    unittest.main()
