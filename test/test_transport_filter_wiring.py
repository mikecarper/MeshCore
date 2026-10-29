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
#include <RadioProfiles.h>
#include <RadioTxPolicy.h>
namespace mesh {
struct Packet {
 uint8_t payload[64] = {}, payload_len = 32, type = 5, header = (5 << 2);
 uint8_t radio_profile=0,radio_origin=0,tx_radio=RADIO_TX_AUTO;
 uint32_t radio_generation=0,radio_origin_generation=0;
 bool radio_bound=false,radio_local=true,radio_reply=false,radio_reply_force=false,forwarded=false;
 uint8_t getPayloadType() const { return (header >> 2) & 0x0f; }
 bool violatesRoutePolicy() const {
   const uint8_t route = header & 0x03;
   const uint8_t payload_type = (header >> 2) & 0x0f;
   return (route == 0 || route == 1)
       && (payload_type == 9 || payload_type == 11);
 }
 int getRawLength() const { return 32; }
 uint16_t writeTo(uint8_t* bytes) const { memcpy(bytes,payload,32);return 32; }
};
}
'''

HARNESS = r'''
#include <helpers/AbstractBridge.h>
#include <helpers/RadioDirectionPolicy.h>
namespace radioDirection = mesh::radioDirection;
#include <atomic>
#include <cassert>
#include <vector>
#include <helpers/bridges/RS232Heartbeat.h>
#include <helpers/bridges/RS232BridgeDiagnostics.h>
namespace rsdiag = mesh::bridge::rs232diag;
#include <helpers/bridges/ESPNowBridgeDiagnostics.h>
#include <helpers/bridges/ESPNowBridgeFormat.h>
#define BRIDGE_DEBUG_PRINTLN(...) ((void)0)
#define PUB_KEY_SIZE 32
#define PAYLOAD_TYPE_ADVERT 4
#define PAYLOAD_TYPE_OTA 0x0c
#define MAX_SERIAL_PACKET_SIZE 300
#define MAX_TRANS_UNIT 256
#define SERIAL_OVERHEAD 6
#define BRIDGE_PACKET_MAGIC 0xabcd
#define BRIDGE_MAGIC_SIZE 2
#define BRIDGE_CHECKSUM_SIZE 2
#define MAX_ESPNOW_PACKET_SIZE 256
unsigned millis() { return 100; }
namespace mesh {
struct Identity {
 explicit Identity(const uint8_t*) {}
 bool verify(const uint8_t*,const uint8_t*,int) {assert(false);return false;}
};
}
namespace AdvertSignature {
 constexpr unsigned kMaxMessageLen=68;
 int buildMessage(const uint8_t*,int,uint8_t*,const uint8_t**) {assert(false);return -1;}
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
struct Prefs {unsigned bridge_delay=10;unsigned bridge_baud=115200;};
struct BridgeBase : AbstractBridge {
@INGRESS_HOOK@
 bool _initialized=true;
 Seen _seen_packets;
 Manager manager; Manager* _mgr=&manager;
 Prefs prefs; Prefs* _prefs=&prefs;
 unsigned queued=0;
 void begin() override {} void end() override {} void loop() override {}
 bool isRunning() const override {return _initialized;}
 void sendPacket(mesh::Packet*) override {}
 void onPacketReceived(mesh::Packet* p) override {handleReceivedPacket(p);}
 void receiveFrom(mesh::Packet* p, const mesh::bridge::IngressSource& source) {
   IngressSourceScope scope(*this, source); handleReceivedPacket(p);
 }
 void handleReceivedPacket(mesh::Packet*);
 uint16_t fletcher16(const uint8_t*,size_t) {return 0;}
};
struct Serial {
 unsigned writes=0;
 size_t limit=300;
 int capacity=300;
 std::vector<uint8_t> bytes;
 int availableForWrite() {return capacity;}
 size_t write(const uint8_t* source,size_t length) {
   ++writes;
   if(length>limit)length=limit;
   bytes.insert(bytes.end(),source,source+length);
   return length;
 }
};
struct RS232Bridge : BridgeBase {
 Serial serial; Serial* _serial=&serial;
 uint8_t _tx_buffer[MAX_SERIAL_PACKET_SIZE] = {};
 uint16_t _tx_size=0,_tx_pos=0;
 uint32_t _tx_wire_until=0,_tx_ping_challenge=0;
 uint32_t _outstanding_challenge=0,_outstanding_since=0;
 bool _tx_wire_pending=false,_tx_is_ping=false,_tx_is_probe=false,_tx_is_data=false,_probe_timed_out=false;
 uint32_t _probe_deadline=0;
 bool _debug_dirty=false;
 rsdiag::Stats _stats;
 void expireProbeTransmit(uint32_t);
 void drainTransmit(uint32_t,bool blocking=false);
 void sendPacket(mesh::Packet*) override;
};
struct ESPNowBridge : BridgeBase {
 int _active_format=0;
 mesh::bridge::diag::Stats _stats;
 std::atomic<bool> _debug_dirty{false};
 void noteTx(uint32_t mesh::bridge::diag::TxStats::* field) {
   mesh::bridge::diag::increment(_stats.tx.*field);
 }
 void recordError(mesh::bridge::diag::Stage stage,int32_t code) {
   _stats.error={stage,code,millis()};
 }
 bool xorCrypt(uint8_t*,size_t) {return true;}
 bool queueTransmitFrames(const mesh::espnow::ESPNowRawFrames&,const mesh::Packet&,bool=false) {++queued;return true;}
 void sendPacket(mesh::Packet*) override;
};
struct MQTTBridge : BridgeBase {
 std::atomic<bool> _stop_requested{false};
 struct Obs {bool mqtt_packets_enabled=true,mqtt_rx_enabled=true,mqtt_advert_verify=false;uint8_t mqtt_tx_enabled=1;} obs;
 Obs* _obs=&obs;
 struct Identity {uint8_t pub_key[32]={};} id;
 Identity* _identity=&id;
 unsigned _filtered_packets=0,_bad_sig_adverts=0;
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
 // Exercise the actual production pending-write path, not a fake drain helper:
 // a denied frame never enters the owned queue; an accepted partial frame is
 // retained byte-for-byte and later data cannot interleave into its wire bytes.
 RS232Bridge backed_up;
 backed_up.setPacketFilter(filter,&allowed);
 backed_up.serial.limit=3;
 backed_up.sendPacket(&p);
 assert(backed_up._tx_size==38 && backed_up._tx_pos==3);
 assert(backed_up._seen_packets.marks==1);
 backed_up.sendPacket(&p);
 assert(backed_up._tx_size==38 && backed_up._tx_pos==6);
 assert(backed_up._seen_packets.marks==1);
 allowed=false;
 backed_up.sendPacket(&p);
 assert(backed_up._tx_pos==6 && backed_up.serial.bytes.size()==6);
 allowed=true;
 backed_up.serial.limit=300;
 backed_up.drainTransmit(millis());
 assert(backed_up._tx_size==0 && backed_up.serial.bytes.size()==38);
 assert(backed_up.serial.bytes[0]==0xab && backed_up.serial.bytes[1]==0xcd);
 assert(backed_up.serial.bytes[2]==0 && backed_up.serial.bytes[3]==32);
 assert(memcmp(backed_up.serial.bytes.data()+4,p.payload,32)==0);
 // A small hardware FIFO is ordinary backpressure, not a failed data write.
 // Data keeps the legacy blocking acceptance behavior, even though heartbeat
 // loop service uses availableForWrite() and never blocks.
 RS232Bridge small_fifo;
 small_fifo.serial.capacity=3;
 small_fifo.sendPacket(&p);
 small_fifo.sendPacket(&p);
 assert(small_fifo._tx_size==0 && small_fifo.serial.bytes.size()==76);
 assert(small_fifo._seen_packets.marks==2 && small_fifo.serial.writes==2);
 // Once teardown is requested, neither RX nor TX admits more queue work,
 // even while the bridge remains initialized until the worker acknowledges.
 mqtt._stop_requested=true;
 mqtt.onPacketReceived(&p);mqtt.sendPacket(&p);
 assert(mqtt.queued==2 && mqtt._filtered_packets==2);
 mqtt._stop_requested=false;
 mqtt.onPacketReceived(&p);mqtt.sendPacket(&p);
 assert(mqtt.queued==4);
 BridgeBase no_policy;no_policy.onPacketReceived(&p);assert(no_policy.manager.queued==1);
}
'''

# Importers use this shared harness directly, so expand its production hook
# here rather than leaving fixture setup to one test's compilation path.
_base_header = (ROOT / 'src/helpers/bridges/BridgeBase.h').read_text(encoding='ascii')
_ingress_hook = '#if defined(WITH_ESPNOW_BRIDGE) || defined(WITH_RS232_BRIDGE) || defined(WITH_UDP_BRIDGE)\n'
_ingress_hook += method(_base_header, 'enum class IngressResult') + ';\n'
_ingress_hook += method(_base_header, 'virtual void onIngressResult(') + '\n#endif\n'
HARNESS = HARNESS.replace('@INGRESS_HOOK@', _ingress_hook)

class TransportFilterWiring(unittest.TestCase):
    def test_aliases_dispatch_the_original_suffix_without_expanding_a_command_buffer(self):
        source = (ROOT/'examples/simple_repeater/MyMesh.cpp').read_text()
        start = source.index('  if (commandFamilyMatches(command, "get flood.channel.scope.require"))')
        end = source.index('#if MESH_ENABLE_CLOCK_SYNC', start)
        dispatch = source[start:end]
        helper = method(source, 'static bool commandFamilyMatches(')
        code = (f'#include "{(ROOT/"src/helpers/FloodCommandAliases.h").as_posix()}"\n'
                '#include <cassert>\n#include <cstring>\n#include <initializer_list>\n') + helper + r'''
static const char* actual_args;
static const char* actual_action;
#define STUB(name) static void name(const char* args,char* reply) {actual_args=args;actual_action=#name;strcpy(reply,"OK");}
STUB(formatFloodChannelScopeRequirements)
STUB(setFloodChannelScopeRequirement)
STUB(deleteFloodChannelScopeRequirement)
STUB(formatFloodChannelScopes)
STUB(setFloodChannelScope)
STUB(deleteFloodChannelScope)
STUB(formatFloodPacketFilterBlacklist)
STUB(setFloodPacketFilterBlacklist)
STUB(deleteFloodPacketFilterBlacklist)
STUB(deleteFloodPacketFilter)
STUB(formatFloodGroupModeration)
STUB(setFloodGroupModeration)
STUB(deleteFloodGroupModeration)
static void formatFloodPacketFilters(const char* args,char* reply,bool=false) {actual_args=args;actual_action="formatFloodPacketFilters";strcpy(reply,"OK");}
static void setFloodPacketFilter(const char* args,char* reply,bool=false) {actual_args=args;actual_action="setFloodPacketFilter";strcpy(reply,"OK");}
void run(const char* command,char* reply) {
@DISPATCH@
else strcpy(reply,"unhandled");
}
int main() {
 // Preserve the advert CLI's whitespace delimiters for both full names and
 // aliases, returning the original bytes for the caller's own validation.
 struct Boundary {const char* command;const char* suffix;};
 for(const auto& row : {
     Boundary{"get flood.advert", ""}, Boundary{"get fa", ""},
     Boundary{"get flood.advert\t2\r\n", "\t2\r\n"},
     Boundary{"get fa\t2\r\n", "\t2\r\n"},
     Boundary{"get flood.advert\r\n", "\r\n"},
     Boundary{"get fa\n", "\n"}, Boundary{"get fa 2", " 2"},
     Boundary{"get fa.2", ".2"}}) {
   const char* suffix=mesh::cli::floodCommandArguments(row.command,"get flood.advert");
   assert(suffix==row.command+strlen(row.command)-strlen(row.suffix));
   assert(!strcmp(suffix,row.suffix));
 }
 for(const char* command : {"get flood.advertisement", "get fax", "get fa/2",
     "get fa=2", "get fa\v2", "get fa\f2", "get\tfa 2"})
   assert(!mesh::cli::floodCommandArguments(command,"get flood.advert"));
 struct Test {const char* command;const char* suffix;const char* action;};
 for(const auto& row : {
     Test{"get fm.2",".2","formatFloodGroupModeration"},
     Test{"set fm.2 public Alice drop",".2 public Alice drop","setFloodGroupModeration"},
     Test{"del fm.2",".2","deleteFloodGroupModeration"},
     Test{"get fs.2",".2","formatFloodChannelScopes"},
     Test{"set fs.2 public scope=local",".2 public scope=local","setFloodChannelScope"},
     Test{"del fs.2",".2","deleteFloodChannelScope"},
     Test{"get fsr.2",".2","formatFloodChannelScopeRequirements"},
     Test{"set fsr.2 public",".2 public","setFloodChannelScopeRequirement"},
     Test{"del fsr.2",".2","deleteFloodChannelScopeRequirement"},
     Test{"get fb.2",".2","formatFloodPacketFilterBlacklist"},
     Test{"set fb.2 AABBCC",".2 AABBCC","setFloodPacketFilterBlacklist"},
     Test{"del fb.2",".2","deleteFloodPacketFilterBlacklist"},
     Test{"get fr.2 n",".2 n","formatFloodPacketFilters"},
     Test{"set fr.2 any n=Alice d",".2 any n=Alice d","setFloodPacketFilter"},
     Test{"del fr.2",".2","deleteFloodPacketFilter"},
     Test{"set flood.rule.2 any n=Alice d",".2 any n=Alice d","setFloodPacketFilter"}}) {
   char reply[160]{};
   actual_args=nullptr;actual_action=nullptr;
   run(row.command,reply);
   assert(!strcmp(reply,"OK") && actual_args && actual_action);
   assert(!strcmp(actual_args,row.suffix) && !strcmp(actual_action,row.action));
   assert(actual_args>=row.command && actual_args<=row.command+strlen(row.command));
 }
 for(const char* command : {"get fmore","get fsrs","get fbwifi.password","get fridge"}) {
   char reply[160]{};run(command,reply);assert(!strcmp(reply,"unhandled"));
 }
}
'''
        code = code.replace('@DISPATCH@', dispatch)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'test.cpp').write_text(code)
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-DMESH_ENABLE_FLOOD_RULE_ENGINE=1', '-DMESH_ENABLE_FLOOD_GROUP_MODERATION=1',
                str(work/'test.cpp'), '-o', str(work/'test')], check=True)
            subprocess.run([str(work/'test')], check=True)

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
            'static bool commandFamilyMatches(', 'static bool commandListMatches(',
            'static bool isCommonManagerReadOnlyAllowed(',
            'static bool isRegionMgrAllowed(', 'static bool isFilterMgrAllowed('])
        code = (f'#include "{(ROOT/"src/helpers/FloodCommandAliases.h").as_posix()}"\n'
                '#include <cstring>\n#include <cassert>\n#include <initializer_list>\n') + helpers + r'''
int main() {
 // Every common namespace retains both manager roles; sensitive get namespaces
 // and partial command names stay denied. Clock deliberately requires equality.
 for (const char* cmd : {"ver", "board", "neighbors", "sensor get", "sensor list",
     "get role", "get public.key", "get clock.sync", "get battery.alert",
     "get rx.watchdog", "get recent.repeater", "get recent.repeaters",
     "get outpath", "get altpath", "clock", "  get public.key"}) {
   assert(isFilterMgrAllowed(cmd));
   assert(isRegionMgrAllowed(cmd));
 }
 for (const char* cmd : {"clock secret", "version", "get rolepassword", "get public.keys",
     "get admin.password", "get guest.password", "get wifi.password", "get bridge.secret"}) {
   assert(!isFilterMgrAllowed(cmd));
   assert(!isRegionMgrAllowed(cmd));
 }
 for (const char* cmd : {"get repeat", "get loop.detect", "get flood.max",
     "get flood.channel.data", "get flood.advert", "get flood.filter",
     "get flood.filter.blacklist", "get flood.moderation", "set flood.filter",
     "del flood.filter", "set flood.filter.blacklist", "del flood.filter.blacklist",
     "set flood.moderation", "del flood.moderation", "clear flood.advert",
     "set flood.channel.data", "set flood.max", "set loop.detect", "set repeat"}) {
   assert(isFilterMgrAllowed(cmd));
   assert(!isRegionMgrAllowed(cmd));
 }
 for (const char* cmd : {"region", "get flood.channel.scope", "set flood.channel.scope",
     "del flood.channel.scope", "get flood.channel.scope.require",
     "set flood.channel.scope.require", "del flood.channel.scope.require"}) {
   assert(isRegionMgrAllowed(cmd));
   assert(!isFilterMgrAllowed(cmd));
 }
 for (const char* cmd : {"get fr", "get fr.3", "set fr.3 any m=bc d", "del fr.3", "del fr all",
     "set fr.3 any c=public sender=^Alice$ text=BEACON len=1-80 prob=50 drop",
     "set flood.rule.3 type=grp_txt channel=public sender=^Alice$ text=BEACON drop",
     "set flood.rule.3 type=any route=direct path=^AABB>CCDD$ enabled=off stop"}) {
   assert(isFilterMgrAllowed(cmd) == (MESH_ENABLE_FLOOD_RULE_ENGINE != 0));
   assert(!isRegionMgrAllowed(cmd));
 }
 for (const char* cmd : {"get fridge", "set frwifi.password secret", "get wifi.password", "del frx",
     "set frwifi.password sender=^Alice$", "set flood.rulebook type=any drop",
     "get fleet.channel", "set fleet.controller off"})
   assert(!isFilterMgrAllowed(cmd));
 for (const char* cmd : {"get fm", "set fm.2 public Alice drop", "del fm.2",
     "get fb", "set fb.2 AABBCC", "del fb.2", "get fa", "get fa 2", "clear fa all"}) {
   assert(isFilterMgrAllowed(cmd));
   assert(!isRegionMgrAllowed(cmd));
 }
 for (const char* cmd : {"get fs", "set fs.2 public scope=local", "del fs.2",
     "get fsr", "set fsr.2 public", "del fsr.2"}) {
   assert(isRegionMgrAllowed(cmd));
   assert(!isFilterMgrAllowed(cmd));
 }
 for (const char* cmd : {"get fmore", "set fss.password secret", "get fsrs", "clear failure all"}) {
   assert(!isFilterMgrAllowed(cmd));
   assert(!isRegionMgrAllowed(cmd));
 }
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
            'RS232Bridge': ['void RS232Bridge::expireProbeTransmit(', 'void RS232Bridge::drainTransmit(', 'void RS232Bridge::sendPacket('],
            'ESPNowBridge': ['void ESPNowBridge::sendPacket('],
            'MQTTBridge': ['void MQTTBridge::sendPacket(', 'void MQTTBridge::onPacketReceived('],
        }.items():
            source = (ROOT/f'src/helpers/bridges/{name}.cpp').read_text()
            methods.extend(method(source, signature) for signature in signatures)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work/'Mesh.h').write_text(PACKET)
            code = '#include <initializer_list>\n' + HARNESS.replace('@METHODS@', '\n'.join(methods))
            code += '''
#if MESH_ENABLE_BRIDGE_INGRESS
namespace mesh { namespace bridge {
IngressProfile ingressProfile() { return IngressProfile::Radio; }
IngressRoute ingressRoute(const IngressSource*) { return {IngressProfile::Radio, false}; }
}}
#endif
'''
            (work/'test.cpp').write_text(code)
            for macros in [[], ['-DWITH_ESPNOW_BRIDGE=1'], ['-DWITH_UDP_BRIDGE=1']]:
                with self.subTest(macros=macros):
                    result = subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        *macros, '-I', str(work), '-I', str(ROOT/'src'), str(work/'test.cpp'),
                        '-o', str(work/'test')], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    subprocess.run([str(work/'test')], check=True)

    def test_roles_wire_filter_at_every_bridge_start_and_cross_hook(self):
        # An on request during asynchronous MQTT teardown only retains start
        # intent on the existing object. Its packet filter survives the stop;
        # the fresh-start branches below must still configure admission first.
        mqtt_begin = method((ROOT/'src/helpers/bridges/MQTTBridge.cpp').read_text(),
                            'void MQTTBridge::begin(')
        stop_guard = mqtt_begin.index('if (_initialized || _lifecycle.isStopInProgress())')
        self.assertLess(mqtt_begin.index('_start_requested = true;'), stop_guard)
        self.assertIn('return;', method(mqtt_begin[stop_guard:],
                                      'if (_initialized || _lifecycle.isStopInProgress())'))
        self.assertNotIn('setPacketFilter(', mqtt_begin)
        self.assertNotIn('_packet_filter', mqtt_begin)
        deferred_start = re.compile(
            r'if \((?:enable && )?(?P<pointer>mqtt_bridge|bridge) && '
            r'(?P=pointer)->isStopping\(\)\) \{\s*'
            r'(?P=pointer)->begin\(\);\s*'
            r'return (?P=pointer)->isStartPending\(\);\s*\}')
        for role in ['simple_repeater', 'simple_room_server']:
            header = (ROOT/f'examples/{role}/MyMesh.h').read_text()
            source = (ROOT/f'examples/{role}/MyMesh.cpp').read_text()
            retained_starts = 0
            for text in [header, source]:
                for match in deferred_start.finditer(text):
                    self.assertIn('configureBridgeFilter(' + match['pointer'] + ');', header, role)
                    retained_starts += 1
                text = deferred_start.sub('', text)
                lines = text.splitlines()
                for index, line in enumerate(lines):
                    if 'bridge->begin();' in line:
                        self.assertIn('configureBridgeFilter(', lines[index-1], role)
            self.assertEqual(retained_starts, 2, role)
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
