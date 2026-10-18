"""Run the production Full CLI getter dispatch and real PUBACK formatter.

Reuses the existing real Psychic adapter/SDK-event fixture. No PlatformIO,
serial device, network endpoint, or HIL feature override is involved.
"""
from pathlib import Path
import unittest

import test_mqtt_publish_ack as ack
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

NODE = r'''
constexpr int MAX_MQTT_SLOTS = 6;
struct StoredPrefs {
  char mqtt_origin[32]{}, mqtt_iata[8]{}, mqtt_ntp_server[64]{};
  char mqtt_owner_public_key[65]{}, mqtt_email[64]{}, timezone_string[64]{}, snmp_community[64]{};
  bool mqtt_packets_enabled=false,mqtt_raw_enabled=false,mqtt_rx_enabled=false;
  bool mqtt_advert_verify=false,snmp_enabled=false;
  unsigned mqtt_tx_enabled=0,mqtt_status_interval=60000;
  int timezone_offset=0;
  char mqtt_slot_preset[6][32]{},mqtt_slot_host[6][64]{},mqtt_slot_username[6][32]{};
  char mqtt_slot_topic[6][96]{},mqtt_slot_audience[6][64]{},mqtt_slot_password[6][64]{},mqtt_slot_token[6][48]{};
  unsigned mqtt_slot_port[6]{};
};
struct MyMesh {
  StoredPrefs _mqtt_prefs;
  void execCommand(char* cmd,char* reply) {
    (void)cmd;
    reply[0]=0;
#ifdef WITH_MQTT_BRIDGE
@GETTERS@
#endif
    strcpy(reply,"Error: unsupported command");
  }
};
'''

MAIN = r'''
int main() {
  MyMesh node;
  strcpy(node._mqtt_prefs.mqtt_slot_password[0],"private-credential");
  const auto original=node._mqtt_prefs;
  MQTTBridge bridge;
  PsychicMqttClient client;
  bridge._slots[0].client=&client;
  s_mqtt_bridge_instance=&bridge;
  auto query=[&](const char* command,const char* expected){
    char cmd[96];strcpy(cmd,command);
    unsigned char bytes[176];memset(bytes,0xa7,sizeof(bytes));
    node.execCommand(cmd,reinterpret_cast<char*>(bytes+8));
    assert(!strcmp(reinterpret_cast<char*>(bytes+8),expected));
    assert(!strcmp(cmd,command));
    assert(!memcmp(&node._mqtt_prefs,&original,sizeof(original)));
    for(unsigned i=0;i<8;++i)assert(bytes[i]==0xa7);
    for(unsigned i=168;i<sizeof(bytes);++i)assert(bytes[i]==0xa7);
  };
#ifdef WITH_MQTT_BRIDGE
  query("get mqtt1.diag","> mqtt1: ok, no errors, pub_ack:0");
  // An SDK PUBLISHED event, rather than enqueue acceptance, advances the
  // actual counter read through the unmodified production CLI getter block.
  esp_mqtt_event_t event{MQTT_EVENT_PUBLISHED,17,false};
  esp_mqtt_event_handle_t event_ptr=&event;
  client._onPublish(event_ptr);
  query("get mqtt1.diag","> mqtt1: ok, no errors, pub_ack:1");
  bridge._initialized=false;
  query("get mqtt1.diag","> mqtt1: bridge not running");
  bridge._initialized=true;
  // V4 has one runtime client, while six persistent slot identities remain.
  query("get mqtt2.diag","> invalid slot");
  query("get mqtt6.diag","> invalid slot");
  query("get mqtt1.preset","> ");
  query("get mqtt1.password","> ********");
  for(const char* cmd:{"get mqtt0.diag","get mqtt7.diag","get mqtt10.diag",
      "get mqtt1.diagx","get mqtt1.diag extra","get mqtt1.diag ",
      "get mqtt1.diag\t","get mqtt1.DIAG","get mqtt1.filter","get mqtt.stopping"})
    query(cmd,"Error: unsupported command");
#else
  query("get mqtt1.diag","Error: unsupported command");
#endif
}
'''


class CompanionMqttDiagnosticTests(unittest.TestCase):
    def test_production_getter_dispatches_real_broker_ack_and_respects_availability(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        command = extract_braced(source, 'void MyMesh::execCommand(')
        getters = extract_braced(command, 'if (cmd && strncmp(cmd, "get ", 4) == 0)')
        node = NODE.replace('@GETTERS@', getters)
        original = ack.PREAMBLE
        # This fixture models the selected V4 one-slot production profile.
        preamble = original.replace('constexpr int RUNTIME_MQTT_SLOTS = 2;',
                                    'constexpr int RUNTIME_MQTT_SLOTS = 1;')
        preamble = preamble.replace('MQTTPacketFilter::kAllPacketTypes, MQTTPacketFilter::kAllPacketTypes',
                                    'MQTTPacketFilter::kAllPacketTypes')
        # Other public getter formatters are unrelated to this regression.
        # The slot formatter and real SDK PUBLISHED handler remain production.
        preamble = preamble.replace('struct MQTTBridge {', '''struct MQTTBridge {
  static void formatMqttStatusReply(char* p,size_t n,const void*){snprintf(p,n,"> msgs: off");}
  static void formatMqttStatsReply(char* p,size_t n){snprintf(p,n,"> (bridge not running)");}
''')
        try:
            for enabled in (False, True):
                with self.subTest(mqtt=enabled):
                    ack.PREAMBLE = preamble
                    case = ack.MqttPublishAckTests('test_production_publish_events_and_slot_diagnostics')
                    case.compile_run(('#define WITH_MQTT_BRIDGE 1\n' if enabled else '') + node + MAIN)
        finally:
            ack.PREAMBLE = original


if __name__ == '__main__':
    unittest.main()
