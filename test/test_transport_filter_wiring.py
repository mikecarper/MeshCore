"""Execute real bridge admission methods and check boot/restart policy wiring."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import re
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

PACKET = r'''
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
namespace mesh {
struct Packet {
 uint8_t payload[64] = {}, payload_len = 32, type = 5;
 uint8_t getPayloadType() const { return type; }
 int getRawLength() const { return 32; }
 uint16_t writeTo(uint8_t* bytes) const { memcpy(bytes,payload,32);return 32; }
};
}
'''

HARNESS = r'''
#include <helpers/AbstractBridge.h>
#include <cassert>
#define BRIDGE_DEBUG_PRINTLN(...) ((void)0)
#define PUB_KEY_SIZE 32
#define PAYLOAD_TYPE_ADVERT 4
#define MAX_SERIAL_PACKET_SIZE 300
#define MAX_TRANS_UNIT 256
#define SERIAL_OVERHEAD 6
#define BRIDGE_PACKET_MAGIC 0xabcd
#define BRIDGE_MAGIC_SIZE 2
#define BRIDGE_CHECKSUM_SIZE 2
#define MAX_ESPNOW_PACKET_SIZE 256
unsigned millis() { return 100; }
namespace mesh {
namespace espnow {
 struct ESPNowRawFrames {uint8_t count=0;uint16_t lengths[2]={};uint8_t data[2][256]={};};
 bool encodeEspNowRawFrames(const uint8_t*,size_t,ESPNowRawFrames& f) {f.count=1;return true;}
}
namespace bridge {
 enum {ESPNOW_FORMAT_RAW=0};
 size_t espNowMaxMeshPacketSize(int) {return 256;}
 const char* espNowFormatName(int) {return "raw";}
}
}
struct Seen {
 unsigned checks=0,marks=0;
 bool wasSeen(mesh::Packet*) {++checks;return false;}
 void markSeen(mesh::Packet*) {++marks;}
};
struct Manager {
 unsigned freed=0,queued=0;
 void free(mesh::Packet*) {++freed;}
 void queueInbound(mesh::Packet*,unsigned) {++queued;}
};
struct Prefs {unsigned bridge_delay=10;};
struct BridgeBase : AbstractBridge {
 bool _initialized=true;
 Seen _seen_packets;
 Manager manager; Manager* _mgr=&manager;
 Prefs prefs; Prefs* _prefs=&prefs;
 unsigned queued=0;
 void begin() override {} void end() override {} void loop() override {}
 bool isRunning() const override {return _initialized;}
 void sendPacket(mesh::Packet*) override {}
 void onPacketReceived(mesh::Packet* p) override {handleReceivedPacket(p);}
 void handleReceivedPacket(mesh::Packet*);
 uint16_t fletcher16(const uint8_t*,size_t) {return 0;}
};
struct Serial {unsigned writes=0;void write(const uint8_t*,size_t) {++writes;}};
struct RS232Bridge : BridgeBase {
 Serial serial; Serial* _serial=&serial;
 void sendPacket(mesh::Packet*) override;
};
struct ESPNowBridge : BridgeBase {
 int _active_format=0;
 bool xorCrypt(uint8_t*,size_t) {return true;}
 bool queueTransmitFrames(mesh::espnow::ESPNowRawFrames&,const mesh::Packet&) {++queued;return true;}
 void sendPacket(mesh::Packet*) override;
};
struct MQTTBridge : BridgeBase {
 struct Obs {bool mqtt_packets_enabled=true,mqtt_rx_enabled=true;uint8_t mqtt_tx_enabled=1;} obs;
 Obs* _obs=&obs;
 struct Identity {uint8_t pub_key[32]={};} id;
 Identity* _identity=&id;
 unsigned _filtered_packets=0;
 bool _staged_raw_valid=false;
 bool shouldQueuePacketType(uint8_t,bool&) {return true;}
 void queuePacket(mesh::Packet*,bool) {++queued;}
 void onPacketReceived(mesh::Packet*) override;
 void sendPacket(mesh::Packet*) override;
};
@METHODS@
int main() {
 bool allowed=false;
 auto filter=[](void* context,const mesh::Packet*) {return *static_cast<bool*>(context);};
 mesh::Packet p;
 BridgeBase inbound; RS232Bridge serial; ESPNowBridge espnow; MQTTBridge mqtt;
 AbstractBridge* bridges[] = {&inbound,&serial,&espnow,&mqtt};
 for (AbstractBridge* b : bridges)
   b->setPacketFilter(filter,&allowed);
 // RX denial frees exactly once, before the seen table or mesh inbound queue.
 inbound.onPacketReceived(&p);
 assert(inbound.manager.freed==1 && inbound.manager.queued==0);
 assert(inbound._seen_packets.checks==0 && inbound._seen_packets.marks==0);
 serial.sendPacket(&p);espnow.sendPacket(&p);mqtt.sendPacket(&p);mqtt.onPacketReceived(&p);
 assert(serial.serial.writes==0 && espnow.queued==0 && mqtt.queued==0);
 assert(serial._seen_packets.marks==0 && espnow._seen_packets.marks==0);
 assert(mqtt._filtered_packets==2);
 // Removing the drop immediately restores every path, including wrapped ESP-NOW.
 allowed=true;
 inbound.onPacketReceived(&p);serial.sendPacket(&p);espnow.sendPacket(&p);
 espnow._active_format=1;espnow.sendPacket(&p);
 mqtt.onPacketReceived(&p);mqtt.sendPacket(&p);
 assert(inbound.manager.queued==1 && inbound._seen_packets.marks==1);
 assert(serial.serial.writes==1 && espnow.queued==2 && mqtt.queued==2);
 assert(serial._seen_packets.marks==1 && espnow._seen_packets.marks==2);
 BridgeBase no_policy;no_policy.onPacketReceived(&p);assert(no_policy.manager.queued==1);
}
'''

class TransportFilterWiring(unittest.TestCase):
    def test_temporary_filter_suspension_checks_both_profiles_in_all_role_paths(self):
        repeater = (ROOT/'examples/simple_repeater/MyMesh.cpp').read_text()
        suspension = re.findall(r'entry\.suspend_on_temp_radio && (\w+)\(\)', repeater)
        self.assertEqual(suspension, ['isAnyTempRadioActive', 'isAnyTempRadioActive'])
        room = (ROOT/'examples/simple_room_server/MyMesh.cpp').read_text()
        evaluations = re.findall(r'flood_rules\.evaluate\(\s*\w+,\s*(\w+)\(\)', room)
        self.assertEqual(evaluations, ['isAnyTempRadioActive'] * 3)

    def test_shorthand_uses_the_existing_filter_manager_permissions(self):
        source = (ROOT/'examples/simple_repeater/MyMesh.cpp').read_text()
        helpers = '\n'.join(method(source, signature) for signature in [
            'static bool commandFamilyMatches(', 'static bool isCommonManagerReadOnlyAllowed(',
            'static bool isRegionMgrAllowed(', 'static bool isFilterMgrAllowed('])
        code = '#include <cstring>\n#include <cassert>\n#include <initializer_list>\n' + helpers + r'''
int main() {
 for (const char* cmd : {"get fr", "get fr.3", "set fr.3 any m=bc d", "del fr.3", "del fr all"}) {
   assert(isFilterMgrAllowed(cmd) == (MESH_ENABLE_FLOOD_RULE_ENGINE != 0));
   assert(!isRegionMgrAllowed(cmd));
 }
 for (const char* cmd : {"get fridge", "set frwifi.password secret", "get wifi.password", "del frx"})
   assert(!isFilterMgrAllowed(cmd));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'test.cpp').write_text(code)
            for enabled in [0, 1]:
                subprocess.run(['g++', '-std=c++17', f'-DMESH_ENABLE_FLOOD_RULE_ENGINE={enabled}',
                    str(work/'test.cpp'), '-o', str(work/'test')], check=True)
                subprocess.run([str(work/'test')], check=True)
        for verb, action in [('get', 'formatFloodPacketFilters'), ('set', 'setFloodPacketFilter'),
                             ('del', 'deleteFloodPacketFilter')]:
            anchor = f'commandFamilyMatches(command, "{verb} fr")'
            dispatch = source[source.index(anchor):].split('} else if', 1)[0]
            self.assertIn(action, dispatch)

    def test_production_bridge_admission(self):
        methods = []
        for name, signatures in {
            'BridgeBase': ['void BridgeBase::handleReceivedPacket('],
            'RS232Bridge': ['void RS232Bridge::sendPacket('],
            'ESPNowBridge': ['void ESPNowBridge::sendPacket('],
            'MQTTBridge': ['void MQTTBridge::sendPacket(', 'void MQTTBridge::onPacketReceived('],
        }.items():
            source = (ROOT/f'src/helpers/bridges/{name}.cpp').read_text()
            methods.extend(method(source, signature) for signature in signatures)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'Mesh.h').write_text(PACKET)
            code = '#include <initializer_list>\n' + HARNESS.replace('@METHODS@', '\n'.join(methods))
            (work/'test.cpp').write_text(code)
            result = subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-I', str(work), '-I', str(ROOT/'src'), str(work/'test.cpp'),
                '-o', str(work/'test')], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            subprocess.run([str(work/'test')], check=True)

    def test_roles_wire_filter_at_every_bridge_start_and_cross_hook(self):
        for role in ['simple_repeater', 'simple_room_server']:
            header = (ROOT/f'examples/{role}/MyMesh.h').read_text()
            source = (ROOT/f'examples/{role}/MyMesh.cpp').read_text()
            for text in [header, source]:
                lines = text.splitlines()
                for index, line in enumerate(lines):
                    if 'bridge->begin();' in line:
                        self.assertIn('configureBridgeFilter(', lines[index-1], role)
            cross = method(header, 'bool allowRadioProfileCross(')
            self.assertIn('allowTransportPacket', cross)
            admission = method(source, 'bool MyMesh::allowTransportPacket(')
            self.assertIn('incoming_region, context)', admission)
            self.assertNotIn('recv_pkt_filter_match_mask =', admission)
            self.assertNotIn('recv_pkt_rule_match_mask =', admission)
        source = (ROOT/'examples/simple_repeater/MyMesh.cpp').read_text()
        defaults = method(source, 'void MyMesh::seedDefaultFloodPacketFilters(')
        self.assertIn('flood_packet_filters[2]', defaults)
        self.assertIn('bridge_wardriving = wardriving', defaults)
        self.assertIn('bridge_wardriving.min_hops = 0', defaults)
        self.assertIn('RULE_MODE_BRIDGE', defaults)
        self.assertIn('RULE_MODE_CROSS', defaults)
        self.assertNotIn('flood_packet_filters[3]', defaults)

if __name__ == '__main__':
    unittest.main()
