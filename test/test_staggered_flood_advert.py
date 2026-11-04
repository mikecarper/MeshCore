"""Exercise midpoint scheduling and real dispatcher radio selection."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method
from test_transport_filter_wiring import PACKET

ROOT = Path(__file__).resolve().parents[1]


class StaggeredFloodAdvertTests(unittest.TestCase):
    def test_room_and_sensor_use_real_midpoint_schedule(self):
        for role, source_path in (("MyMesh", "examples/simple_room_server/MyMesh.cpp"),
                                  ("SensorMesh", "examples/simple_sensor/SensorMesh.cpp")):
            with self.subTest(role=role):
                source = (ROOT / source_path).read_text()
                header = (ROOT / source_path.replace('.cpp', '.h')).read_text()
                update = method(source, f"void {role}::updateFloodAdvertTimer(")
                eligible = method(source, f"bool {role}::canSendSecondaryFloodAdvert(")
                callback = method(header, "void onRadio2FloodAdvertChanged(").replace(' override', '')
                loop = method(source, f"void {role}::loop(")
                start = loop.index("  if (next_flood_advert &&")
                end = loop.index("  servicePendingPrimaryRadioSettings();", start)
                periodic = loop[start:end]
                code = r'''
#include <helpers/StaggeredFloodAdvert.h>
#include <cassert>
#include <vector>
#include <cstdint>
namespace mesh { namespace radioDirection {
Mode mode() { return Mode::Both; }
}}
uint32_t now_ms=0;
uint32_t millis(){return now_ms;}
uint32_t futureMillis(uint32_t delay){return now_ms+delay;}
bool millisHasNowPassed(uint32_t time){return int32_t(now_ms-time)>=0;}
namespace mesh {
struct Packet {uint8_t radio_profile=0,tx_radio=RADIO_TX_AUTO;bool radio_local=true;};
}
struct Radio {mesh::RadioProfiles value;mesh::RadioProfiles* profiles(){return &value;}};
class @ROLE@ {
public:
 Radio radio;Radio* _radio=&radio;
 struct {uint32_t flood_advert_interval=12;uint8_t path_hash_mode=0,radio2_flood_advert_disabled=0;} _prefs;
 unsigned long next_flood_advert=0,next_local_advert=0;
 mesh::StaggeredFloodAdvert secondary_flood_advert;
 bool tracker=false;int default_scope=0,local_sends=0;
 std::vector<mesh::Packet> sends;mesh::Packet scratch;
 bool isTrackerModeEnabled()const{return tracker;}
 bool canSendSecondaryFloodAdvert()const;
 @CALLBACK@
 void updateFloodAdvertTimer();
 void updateAdvertTimer(){next_local_advert=0;}
 mesh::Packet* createSelfAdvert(){scratch=mesh::Packet{};return &scratch;}
 void sendFloodScoped(int,mesh::Packet* p,uint32_t,uint8_t){sends.push_back(*p);}
 void sendZeroHop(mesh::Packet*){++local_sends;}
 void periodic(){@PERIODIC@}
};
@UPDATE@
@ELIGIBLE@
int main(){
 constexpr uint32_t hour=3600000;
 @ROLE@ node;node.radio.value.secondary.mode=mesh::RadioProfileMode::RxTx;
 node.radio.value.cross=mesh::RadioCrossMode::Off;
 node.updateFloodAdvertTimer();
 now_ms=6*hour-1;node.periodic();assert(node.sends.empty());
 now_ms=6*hour;node.periodic();assert(node.sends.size()==1);
 assert(node.sends.back().radio_profile==1&&node.sends.back().tx_radio==mesh::RADIO_TX_SECONDARY);
 // Turning off the setting suppresses only the radio2 midpoint. Enabling it
 // after the midpoint never sends a stale flood, even within the grace period.
 now_ms=0;@ROLE@ setting;
 setting.radio.value.secondary.mode=mesh::RadioProfileMode::RxTx;
 setting.updateFloodAdvertTimer();
 now_ms=5*hour;setting._prefs.radio2_flood_advert_disabled=0xA5;
 setting.onRadio2FloodAdvertChanged(false); // no loop while disabled
 now_ms=6*hour+1;setting._prefs.radio2_flood_advert_disabled=0;
 setting.onRadio2FloodAdvertChanged(true);setting.periodic();assert(setting.sends.empty());
 now_ms=12*hour;setting.periodic();assert(setting.sends.size()==1);
 assert(setting.sends.back().tx_radio==mesh::RADIO_TX_PRIMARY);
 now_ms=18*hour;setting.periodic();assert(setting.sends.size()==2);
 assert(setting.sends.back().tx_radio==mesh::RADIO_TX_SECONDARY);
 now_ms=0;@ROLE@ off;
 off.radio.value.secondary.mode=mesh::RadioProfileMode::RxTx;
 off._prefs.radio2_flood_advert_disabled=0xA5;off.updateFloodAdvertTimer();
 now_ms=6*hour;off.periodic();assert(off.sends.empty());
 now_ms=12*hour;off.periodic();assert(off.sends.size()==1);
 assert(off.sends[0].tx_radio==mesh::RADIO_TX_PRIMARY);
 now_ms=12*hour;node.periodic();assert(node.sends.size()==2);
 assert(node.sends.back().radio_profile==0&&node.sends.back().tx_radio==mesh::RADIO_TX_PRIMARY);
 node.radio.value.cross=mesh::RadioCrossMode::On;
 now_ms=18*hour;node.periodic();assert(node.sends.size()==3);
 assert(node.sends.back().radio_profile==1&&node.sends.back().tx_radio==mesh::RADIO_TX_SECONDARY);
 // A disabled profile does not fall back to the primary or create stale work.
 node.radio.value.secondary.mode=mesh::RadioProfileMode::Off;
 now_ms=24*hour;node.periodic();assert(node.sends.size()==4);
 now_ms=31*hour;node.radio.value.secondary.mode=mesh::RadioProfileMode::RxTx;
 node.periodic();assert(node.sends.size()==4);
 // Zero interval disables both schedules.
 node._prefs.flood_advert_interval=0;node.updateFloodAdvertTimer();
 now_ms=100*hour;node.periodic();assert(node.sends.size()==4);
@TRACKER@
 // Long emergency sleep misses the midpoint without a catch-up flood. The
 // ordinary primary schedule still runs when its own deadline arrives.
 now_ms=0;@ROLE@ sleeper;
 sleeper.radio.value.secondary.mode=mesh::RadioProfileMode::RxTx;
 sleeper.updateFloodAdvertTimer();
 now_ms=6*hour+60001;sleeper.periodic();assert(sleeper.sends.empty());
 now_ms=12*hour;sleeper.periodic();assert(sleeper.sends.size()==1);
 assert(sleeper.sends[0].tx_radio==mesh::RADIO_TX_PRIMARY);
}
'''
                tracker = ""
                if role == "SensorMesh":
                    tracker = r'''
 node._prefs.flood_advert_interval=12;node.tracker=true;node.updateFloodAdvertTimer();
 assert(node.next_flood_advert==0);
 now_ms+=7*hour;node.periodic();assert(node.sends.size()==4);
 node.tracker=false;node.updateFloodAdvertTimer();
 now_ms+=6*hour;node.periodic();assert(node.sends.size()==5);
 node.tracker=true;node.next_flood_advert=0; // configureTrackerRuntime does this.
 now_ms+=12*hour;node.periodic();assert(node.sends.size()==5);
'''
                code = (code.replace("@ROLE@", role).replace("@PERIODIC@", periodic)
                        .replace("@UPDATE@", update).replace("@ELIGIBLE@", eligible)
                        .replace("@CALLBACK@", callback)
                        .replace("@TRACKER@", tracker))
                with tempfile.TemporaryDirectory(prefix="staggered-role-") as folder:
                    temp = Path(folder)
                    test_source, binary = temp / "main.cpp", temp / "test"
                    test_source.write_text(code)
                    build = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                                            "-DMESH_ENABLE_RADIO_DIRECTION=1", "-I", str(ROOT / "src"), str(test_source), "-o", str(binary)],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
                    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_scheduler_and_secondary_admission(self):
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        code = r'''
#include <helpers/StaggeredFloodAdvert.h>
#include <Packet.h>
#include <cassert>
#include <vector>
#include <initializer_list>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define MAX_PACKET_PAYLOAD 64
#define PAYLOAD_TYPE_OTA 0x0c
using mesh::Packet;
using mesh::RadioProfileProtocol;
using mesh::RADIO_TX_AUTO;
using mesh::explicitRadioTxMask;
namespace radioDirection = mesh::radioDirection;
namespace mesh { namespace radioDirection {
static Mode current = Mode::Both;
Mode mode() { return current; }
}}
struct Radio { mesh::RadioProfiles* state; mesh::RadioProfiles* profiles() const {return state;} };
struct Dispatcher {
 Radio* _radio;
 bool radio_available=true;
 unsigned failed=0,allocated=0,cross_checks=0;
 std::vector<Packet> outbound;
 Packet copy_pool[4];
 explicit Dispatcher(Radio* r):_radio(r) {}
 bool queueTransmitCandidate(Packet* p,uint8_t,uint32_t) {outbound.push_back(*p);return true;}
 uint32_t futureMillis(uint32_t delay) {return delay;}
 bool allowRadioProfileCross(const Packet*) {++cross_checks;return false;}
 void onSendFail(Packet*) {++failed;}
 void onRadioProfileCopyQueued(Packet*,Packet*,uint8_t) {}
 void onTracePacketQueuedForSend(Packet*) {}
 void releasePacket(Packet*) {}
 Packet* obtainNewPacket() {assert(allocated<4);return &copy_pool[allocated++];}
 bool queueOutboundPacket(Packet*,uint8_t,uint32_t);
 uint8_t getTransmitProfileMask(const Packet*) const;
 bool isPacketRadioCurrent(const Packet*) const;
};
@OUTBOUND@
@MASK@
@CURRENT@
int main() {
 using namespace mesh;
 StaggeredFloodAdvert s;
 constexpr uint32_t hour=3600000;
 s.schedule(0,12*hour,true);
 assert(s.sleepDeadline(true)==6*hour && s.sleepDeadline(false)==0);
 assert(!s.service(6*hour-1,true));
 assert(s.service(6*hour,true));
 assert(!s.service(6*hour+1,true) && s.sleepDeadline(true)==0);
 // Each completed primary resets the phase. No old midpoint is sent even
 // if both deadlines passed while execution was suspended.
 s.schedule(0,12*hour,true);
 s.schedule(12*hour+20,12*hour,true);
 assert(!s.service(12*hour+20,true));
 assert(s.sleepDeadline(true)==18*hour+20);
 assert(s.service(18*hour+20,true));
 // Enable before the midpoint, or disable before it: only the former sends.
 s.schedule(0,12*hour,false);
 assert(!s.service(1*hour,true));
 assert(s.service(6*hour,true));
 s.schedule(0,12*hour,true);
 assert(!s.service(1*hour,false));
 assert(!s.service(6*hour,false));
 assert(!s.service(7*hour,true));
 // A radio first enabled after the midpoint does not create a stale burst.
 s.schedule(0,12*hour,false);
 assert(!s.service(7*hour,true));
 assert(!s.service(8*hour,true));
 // A wake immediately after the accepted one-minute grace drops stale work.
 s.schedule(0,12*hour,true);
 assert(s.service(6*hour+60000,true));
 s.schedule(0,12*hour,true);
 assert(!s.service(6*hour+60001,true));
 assert(!s.service(7*hour,true));
 // Runtime interval change starts a new phase; zero disables completely.
 s.schedule(0,12*hour,true);s.schedule(hour,4*hour,true);
 assert(s.sleepDeadline(true)==3*hour);
 assert(s.service(3*hour,true));
 s.schedule(0,0,true);
 assert(!s.service(7*hour,true) && s.sleepDeadline(true)==0);
 s.schedule(0,0x80000000UL,true);
 assert(s.sleepDeadline(true)==0);
 // Deadline rollover and the sleep API's reserved zero sentinel.
 s.schedule(0xfffffff0UL,64,true);
 assert(s.sleepDeadline(true)==0x10);
 assert(!s.service(0xffffffffUL,true));
 assert(s.service(0x10,true));
 s.schedule(0xfffffff0UL,64,true);
 assert(!s.service(0x10+60001,true));
 s.schedule(0xfffffff0UL,32,true);
 assert(s.sleepDeadline(true)==1);
 assert(!s.service(0,true));assert(s.service(1,true));

 RadioProfiles p;Radio radio{&p};Dispatcher d{&radio};
 p.primary.protocol=RadioProfileProtocol::MeshCore;
 p.secondary.params.protocol=RadioProfileProtocol::MeshCore;
 p.secondary.mode=RadioProfileMode::RxTx;
 assert(!radio2FloodAdvertEligible(nullptr));
 assert(radio2FloodAdvertEligible(&p));
 for(auto cross:{RadioCrossMode::Off,RadioCrossMode::On,RadioCrossMode::Auto}) {
   p.cross=cross;Packet packet;packet.header=(4<<2)|1;
   targetRadio2FloodAdvert(packet);
   assert(packet.radio_local && packet.radio_profile==1 && packet.tx_radio==RADIO_TX_SECONDARY);
   assert(d.getTransmitProfileMask(&packet)==2);
   auto previous=d.outbound.size();
   assert(d.queueOutboundPacket(&packet,0,0));
   assert(d.outbound.size()==previous+1 && d.outbound.back().radio_profile==1);
   assert(d.cross_checks==0); // This is local origination, not crossing.
 }
 for(auto m:{RadioProfileMode::Off,RadioProfileMode::Rx}) {
   p.secondary.mode=m;assert(!radio2FloodAdvertEligible(&p));
   Packet packet;targetRadio2FloodAdvert(packet);
   assert(d.getTransmitProfileMask(&packet)==0);
   assert(!d.queueOutboundPacket(&packet,0,0));
 }
 p.secondary.mode=RadioProfileMode::RxTx;
 for(auto protocol:{RadioProfileProtocol::Meshtext,RadioProfileProtocol::Ethernet,RadioProfileProtocol::Kiss}) {
   p.secondary.params.protocol=protocol;
   assert(!radio2FloodAdvertEligible(&p));
   Packet packet;targetRadio2FloodAdvert(packet);
   assert(d.getTransmitProfileMask(&packet)==0);
   assert(!d.queueOutboundPacket(&packet,0,0));
 }
 p.secondary.params.protocol=RadioProfileProtocol::MeshCore;
 for(auto mode:{radioDirection::Mode::Rx,radioDirection::Mode::Off}) {
   radioDirection::current=mode;
   assert(!radio2FloodAdvertEligible(&p));
   Packet packet;targetRadio2FloodAdvert(packet);
   assert(!d.queueOutboundPacket(&packet,0,0));
 }
 radioDirection::current=radioDirection::Mode::Tx;
 assert(radio2FloodAdvertEligible(&p));
}
'''
        for marker, signature in (("@OUTBOUND@", "bool Dispatcher::queueOutboundPacket("),
                                  ("@MASK@", "uint8_t Dispatcher::getTransmitProfileMask("),
                                  ("@CURRENT@", "bool Dispatcher::isPacketRadioCurrent(")):
            code = code.replace(marker, method(dispatcher, signature))
        packet = PACKET.replace(" uint8_t payload[64]", " uint8_t path_len=0;\n static bool isValidPathLen(uint8_t n) {return n<64;}\n uint8_t payload[64]")
        with tempfile.TemporaryDirectory(prefix="staggered-flood-") as folder:
            temp = Path(folder)
            (temp / "Packet.h").write_text(packet)
            source = temp / "main.cpp"
            binary = temp / "test"
            source.write_text(code)
            build = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                                    "-DMESH_ENABLE_RADIO_DIRECTION=1", "-I", str(temp), "-I", str(ROOT / "src"),
                                    str(source), "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
