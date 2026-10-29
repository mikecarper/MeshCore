"""Execute real bridge admission and dispatcher/OTA lease guards without RF."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method
from test_transport_filter_wiring import HARNESS, PACKET

ROOT = Path(__file__).resolve().parents[1]


class BridgeIngressTests(unittest.TestCase):
    def test_radio2_selection_is_local_explicit_and_never_falls_back(self):
        base = (ROOT / "src/helpers/bridges/BridgeBase.cpp").read_text()
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        receive = method(dispatcher, "void Dispatcher::processRecvPacket(")
        guard = receive[receive.index("  const auto* profiles ="):receive.index("  const uint8_t previous_profile")]
        code = '#include <helpers/BridgeIngressProfile.h>\n' + HARNESS[:HARNESS.index("@METHODS@")]
        code += method(base, "void BridgeBase::handleReceivedPacket(")
        code += r'''
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define MAX_PACKET_PAYLOAD 64
using mesh::Packet;
using mesh::RadioProfileProtocol;
using mesh::RADIO_TX_AUTO;
using mesh::explicitRadioTxMask;
struct Radio {
 mesh::RadioProfiles* state;
 mesh::RadioProfiles* profiles() const {return state;}
};
struct Dispatcher {
 Radio* _radio;
 bool radio_available=true;
 unsigned accepted=0,released=0,failed=0,copies=0;
 std::vector<mesh::Packet> outbound;
 mesh::Packet copy_pool[4]; unsigned allocated=0;
 explicit Dispatcher(Radio* radio):_radio(radio) {}
 void releasePacket(mesh::Packet*) {++released;}
 void receive(mesh::Packet* pkt) {
@DISPATCH_GUARD@
  ++accepted;
 }
 bool queueTransmitCandidate(mesh::Packet* packet,uint8_t,uint32_t) {
  outbound.push_back(*packet);return true;
 }
 unsigned futureMillis(unsigned delay) {return delay;}
 bool allowRadioProfileCross(const mesh::Packet*) const {return true;}
 void onSendFail(mesh::Packet*) {++failed;}
 void onRadioProfileCopyQueued(mesh::Packet*,mesh::Packet*,uint8_t) {++copies;}
 void onTracePacketQueuedForSend(mesh::Packet*) {}
 mesh::Packet* obtainNewPacket() {assert(allocated<4);return &copy_pool[allocated++];}
 bool queueOutboundPacket(mesh::Packet*,uint8_t,uint32_t);
 uint8_t getTransmitProfileMask(const mesh::Packet*) const;
 bool isPacketRadioCurrent(const mesh::Packet*) const;
};
@OUTBOUND@
@MASK@
@CURRENT@
int main() {
 using mesh::RadioCrossMode;
 using mesh::RadioProfileMode;
 using mesh::bridge::IngressProfile;
 auto& selection=mesh::bridge::requested_ingress;
 mesh::RadioProfiles profiles;
 profiles.generation[0]=12;profiles.generation[1]=93;
 profiles.secondary.mode=RadioProfileMode::RxTx;
 profiles.cross=RadioCrossMode::Off;
 BridgeBase bridge;bridge.setIngressProfiles(&profiles);
 Radio radio{&profiles};Dispatcher dispatch{&radio};

 // A missing setting means primary/AUTO exactly as before, including the
 // operator's existing crossover policy. Merely selecting another RX tuple
 // does not make a bridge follow the scanner.
 assert(selection==IngressProfile::Radio);
 mesh::Packet legacy;
 legacy.radio_profile=legacy.radio_origin=1;
 legacy.radio_bound=legacy.radio_local=legacy.radio_reply=legacy.radio_reply_force=true;
 legacy.tx_radio=mesh::RADIO_TX_BOTH;
 bridge.handleReceivedPacket(&legacy);
 assert(legacy.radio_profile==0&&legacy.radio_origin==0&&legacy.radio_generation==12);
 assert(legacy.tx_radio==mesh::RADIO_TX_AUTO&&!legacy.radio_local&&!legacy.radio_bound);
 assert(!legacy.radio_reply&&!legacy.radio_reply_force&&legacy.forwarded);
 profiles.cross=RadioCrossMode::On;
 assert(dispatch.queueOutboundPacket(&legacy,0,0));
 assert(dispatch.outbound.size()==2&&dispatch.outbound[0].radio_profile==0&&dispatch.outbound[1].radio_profile==1);
 dispatch.outbound.clear();

 // The destination setting applies before admission filters/deduplication.
 // The source wire packet remains ordinary MeshCore bytes, so old bridge
 // senders do not need new headers or firmware to use the selected target.
 selection=IngressProfile::Radio2;
 unsigned filter_calls=0;
 bridge.setPacketFilter([](void* context,const mesh::Packet* packet) {
   ++*static_cast<unsigned*>(context);
   assert(packet->radio_profile==1&&packet->radio_origin==1);
   assert(packet->radio_generation==93&&packet->radio_origin_generation==93);
   assert(packet->tx_radio==mesh::RADIO_TX_SECONDARY&&packet->forwarded);
   assert(!packet->radio_local&&!packet->radio_bound&&!packet->radio_reply&&!packet->radio_reply_force);
   return true;
 },&filter_calls);
 mesh::Packet media;
 media.radio_reply=media.radio_reply_force=media.radio_local=media.radio_bound=true;
 media.tx_radio=mesh::RADIO_TX_BOTH;
 uint8_t before[32],after[32];media.writeTo(before);
 bridge.handleReceivedPacket(&media);
 media.writeTo(after);assert(memcmp(before,after,sizeof(before))==0);
 assert(filter_calls==1&&media.radio_profile==1&&media.tx_radio==mesh::RADIO_TX_SECONDARY);
 assert(dispatch.getTransmitProfileMask(&media)==2);
 assert(dispatch.queueOutboundPacket(&media,0,0));
 assert(dispatch.outbound.size()==1&&dispatch.outbound[0].radio_profile==1);
 assert(dispatch.isPacketRadioCurrent(&media));
 // Changing the main frequency cannot invalidate or redirect a queued
 // secondary packet. Retuning/disabling that secondary must invalidate it.
 ++profiles.generation[0];assert(dispatch.isPacketRadioCurrent(&media));
 ++profiles.generation[1];assert(!dispatch.isPacketRadioCurrent(&media));
 const auto sent=dispatch.outbound.size();
 assert(!dispatch.queueOutboundPacket(&media,0,0)&&dispatch.outbound.size()==sent);
 profiles.generation[1]=93;
 profiles.secondary.mode=RadioProfileMode::Rx;
 assert(!dispatch.isPacketRadioCurrent(&media));
 profiles.secondary.mode=RadioProfileMode::RxTx;
 profiles.secondary.params.protocol=RadioProfileProtocol::Meshtext;
 assert(!dispatch.isPacketRadioCurrent(&media));
 profiles.secondary.params.protocol=RadioProfileProtocol::MeshCore;

 // Bridge-delay work holds the generation selected on receipt, not the
 // generation or target selected when it is processed later.
 mesh::Packet delayed;bridge.handleReceivedPacket(&delayed);
 dispatch.receive(&delayed);assert(dispatch.accepted==1&&dispatch.released==0);
 ++profiles.generation[1];
 dispatch.receive(&delayed);assert(dispatch.accepted==1&&dispatch.released==1);
 assert(!dispatch.queueOutboundPacket(&delayed,0,0));
 profiles.generation[1]=93;

 // Invalid selected targets are rejected before filters, dedup marking and
 // queueing. There is no primary fallback even with crossover enabled.
 const auto queued=bridge.manager.queued,checks=bridge._seen_packets.checks,marks=bridge._seen_packets.marks;
 const auto filters=filter_calls;
 unsigned drops=bridge.manager.freed;
 for(auto mode:{RadioProfileMode::Off,RadioProfileMode::Rx}) {
   profiles.secondary.mode=mode;
   mesh::Packet p;bridge.handleReceivedPacket(&p);assert(bridge.manager.freed==++drops);
 }
 profiles.secondary.mode=RadioProfileMode::RxTx;
 for(auto protocol:{RadioProfileProtocol::Meshtext,RadioProfileProtocol::Ethernet,RadioProfileProtocol::Kiss}) {
   profiles.secondary.params.protocol=protocol;
   mesh::Packet p;bridge.handleReceivedPacket(&p);assert(bridge.manager.freed==++drops);
 }
 profiles.secondary.params.protocol=RadioProfileProtocol::MeshCore;
 bridge.setIngressProfiles(nullptr);
 mesh::Packet missing;bridge.handleReceivedPacket(&missing);assert(bridge.manager.freed==++drops);
 bridge.setIngressProfiles(&profiles);
 for (auto target : {IngressProfile::Blocked, IngressProfile::Off}) {
   selection=target;
   mesh::Packet blocked;bridge.handleReceivedPacket(&blocked);assert(bridge.manager.freed==++drops);
 }
 assert(bridge.manager.queued==queued&&bridge._seen_packets.checks==checks&&bridge._seen_packets.marks==marks);
 assert(filter_calls==filters&&dispatch.outbound.size()==sent);

 // Selecting radio2 for incoming bridge traffic does not change the route
 // of unrelated local/primary traffic or serialize a profile hint onto RF.
 selection=IngressProfile::Radio2;
 profiles.cross=RadioCrossMode::Off;
 mesh::Packet normal;normal.radio_generation=profiles.generation[0];
 assert(dispatch.getTransmitProfileMask(&normal)==1);
 assert(dispatch.queueOutboundPacket(&normal,0,0));
 assert(dispatch.outbound.back().radio_profile==0);

 // Explicit source mappings can also pin USA to primary while crossing is
 // enabled. The source identity is visible only during this admission.
 bridge.setPacketFilter(nullptr,nullptr);
 selection=IngressProfile::Radio;
 mesh::bridge::requested_explicit=true;
 profiles.cross=RadioCrossMode::On;
 const uint8_t address[]={2,16,32,48,64,81};
 const auto source=mesh::bridge::IngressSource::mac(address);
 mesh::Packet usa;bridge.receiveFrom(&usa,source);
 assert(mesh::bridge::observed_source.kind==mesh::bridge::IngressSource::Kind::Mac);
 assert(memcmp(mesh::bridge::observed_source.bytes,address,6)==0);
 assert(usa.radio_profile==0&&usa.radio_generation==profiles.generation[0]&&usa.tx_radio==mesh::RADIO_TX_PRIMARY);
 assert(dispatch.getTransmitProfileMask(&usa)==1);
 mesh::bridge::requested_explicit=false;
 mesh::Packet next;bridge.handleReceivedPacket(&next);
 assert(mesh::bridge::observed_source.kind==mesh::bridge::IngressSource::Kind::None);
 assert(next.tx_radio==mesh::RADIO_TX_AUTO&&dispatch.getTransmitProfileMask(&next)==3);
}
'''
        code = code.replace("@DISPATCH_GUARD@", guard)
        for marker, signature in (("@OUTBOUND@", "bool Dispatcher::queueOutboundPacket("),
                                  ("@MASK@", "uint8_t Dispatcher::getTransmitProfileMask("),
                                  ("@CURRENT@", "bool Dispatcher::isPacketRadioCurrent(")):
            code = code.replace(marker, method(dispatcher, signature))
        packet = PACKET.replace(" uint8_t payload[64]", " uint8_t path_len=0;\n static bool isValidPathLen(uint8_t n) {return n<64;}\n uint8_t payload[64]")
        selector = (ROOT / "src/helpers/BridgeIngressProfile.h").read_text() + r'''
namespace mesh { namespace bridge {
static IngressProfile requested_ingress=IngressProfile::Radio;
static bool requested_explicit=false;
static IngressSource observed_source;
inline IngressProfile ingressProfile() {return requested_ingress;}
inline IngressRoute ingressRoute(const IngressSource* source) {
 observed_source=source?*source:IngressSource();
 return {requested_ingress, requested_explicit};
}
}}
'''
        with tempfile.TemporaryDirectory(prefix="meshcore-bridge-radio2-") as directory:
            work = Path(directory)
            (work / "Mesh.h").write_text(packet)
            (work / "helpers").mkdir()
            (work / "helpers/AbstractBridge.h").write_bytes(
                (ROOT / "src/helpers/AbstractBridge.h").read_bytes())
            (work / "helpers/BridgeIngressProfile.h").write_text(selector)
            source, binary = work / "main.cpp", work / "test"
            source.write_text(code)
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-DWITH_BRIDGE=1", "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(work), "-I", str(ROOT / "src"), str(source), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_regenerated_direct_and_multipart_acks_retain_ingress_tx_policy(self):
        production = (ROOT / "src/Mesh.cpp").read_text()
        code = r'''
#include <Packet.h>
#include <cassert>
#include <cstring>
#include <vector>
#include <initializer_list>
#undef MESH_DEBUG_PRINTLN
#define MESH_DEBUG_PRINTLN(...) ((void)0)
using mesh::Packet;
using DispatcherAction=unsigned;
constexpr DispatcherAction ACTION_RELEASE=0;
struct Seen {
 bool seen=false;
 unsigned marked=0;
 bool wasSeen(Packet*) {return seen;}
 void markSeen(Packet*) {seen=true;++marked;}
};
struct Mesh {
 Packet pool[12];unsigned used=0,retries=0;
 Seen tables;Seen* _tables=&tables;
 uint8_t expected_policy=mesh::RADIO_TX_AUTO;
 std::vector<Packet> sent;
 std::vector<unsigned> delays;
 Packet* obtainNewPacket() {
  assert(used<12);auto* p=&pool[used++];
  // The dispatcher allocator inherits the radio receive context. The
  // regenerated ACK must independently retain the explicit TX selection.
  p->radio_profile=p->radio_origin=1;
  p->radio_generation=p->radio_origin_generation=93;
  p->radio_local=false;
  return p;
 }
 uint8_t getExtraAckTransmitCount() const {return 2;}
 void maybeScheduleDirectRetry(Packet* p,uint8_t) {
  assert(p->tx_radio==expected_policy);++retries;
 }
 void sendPacket(Packet* p,uint8_t,uint32_t delay) {sent.push_back(*p);delays.push_back(delay);}
 Packet* createAck(const uint8_t*,uint8_t);
 Packet* createMultiAck(const uint8_t*,uint8_t,uint8_t);
 void removePathPrefix(Packet*,uint8_t);
 void routeDirectRecvAcks(Packet*,uint32_t);
 DispatcherAction forwardMultipartDirect(Packet*,uint32_t);
};
@METHODS@
int main() {
 for(uint8_t policy:{mesh::RADIO_TX_AUTO,mesh::RADIO_TX_PRIMARY,
                     mesh::RADIO_TX_SECONDARY,mesh::RADIO_TX_BOTH}) {
  Packet source;
  source.header=(PAYLOAD_TYPE_ACK<<PH_TYPE_SHIFT)|ROUTE_TYPE_DIRECT;
  source.setPathHashSizeAndCount(2,3);
  for(unsigned n=0;n<6;++n)source.path[n]=uint8_t(n+1);
  source.payload_len=4;
  memcpy(source.payload,"ACK!",4);
  source.tx_radio=policy;
  Mesh direct;direct.expected_policy=policy;
  direct.routeDirectRecvAcks(&source,100);
  assert(direct.sent.size()==3&&direct.retries==3);
  for(unsigned n=0;n<3;++n) {
   const auto& p=direct.sent[n];
   assert(p.tx_radio==policy&&p.radio_profile==1&&p.radio_generation==93);
   assert(!p.radio_reply&&p.forwarded&&p.isRouteDirect());
   assert(p.getPathHashSize()==2&&p.getPathHashCount()==3);
   assert(memcmp(p.path,source.path,6)==0);
   assert(direct.delays[n]==100+n*300);
   assert(p.getPayloadType()==(n<2?PAYLOAD_TYPE_MULTIPART:PAYLOAD_TYPE_ACK));
   assert(p.payload_len==(n<2?5:4));
   assert(memcmp(p.payload+(n<2?1:0),source.payload,4)==0);
  }
  source.header=(PAYLOAD_TYPE_MULTIPART<<PH_TYPE_SHIFT)|ROUTE_TYPE_DIRECT;
  source.payload[0]=(2<<4)|PAYLOAD_TYPE_ACK;
  memcpy(source.payload+1,"ACK!",4);source.payload_len=5;
  Mesh multipart;multipart.expected_policy=policy;
  assert(multipart.forwardMultipartDirect(&source,100)==ACTION_RELEASE);
  assert(multipart.sent.size()==3&&multipart.retries==3&&multipart.tables.marked==1);
  for(unsigned n=0;n<3;++n) {
   const auto& p=multipart.sent[n];
   assert(p.tx_radio==policy&&p.radio_profile==1&&p.radio_generation==93);
   assert(!p.radio_reply&&p.forwarded&&p.isRouteDirect());
   assert(p.getPathHashSize()==2&&p.getPathHashCount()==2);
   assert(memcmp(p.path,source.path+2,4)==0);
   assert(multipart.delays[n]==1000+n*300);
   assert(memcmp(p.payload+(n<2?1:0),source.payload+1,4)==0);
  }
  // A repeated multipart receipt remains deduplicated. Malformed and
  // deliberately suppressed ACKs never gain a new radio transmission.
  multipart.forwardMultipartDirect(&source,100);
  assert(multipart.sent.size()==3&&multipart.tables.marked==1);
  source.header=0xff;direct.routeDirectRecvAcks(&source,100);
  assert(direct.sent.size()==3);
  assert(multipart.forwardMultipartDirect(nullptr,100)==ACTION_RELEASE);
  source.payload_len=0;multipart.forwardMultipartDirect(&source,100);
  assert(multipart.sent.size()==3);
 }
}
'''
        signatures = ("Packet* Mesh::createAck(const uint8_t*",
                      "Packet* Mesh::createMultiAck(const uint8_t*",
                      "void Mesh::removePathPrefix(",
                      "void Mesh::routeDirectRecvAcks(",
                      "DispatcherAction Mesh::forwardMultipartDirect(")
        code = code.replace("@METHODS@", "\n".join(method(production, signature) for signature in signatures))
        with tempfile.TemporaryDirectory(prefix="meshcore-bridge-acks-") as directory:
            work = Path(directory)
            source, binary = work / "main.cpp", work / "test"
            source.write_text(code)
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-sign-compare",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                str(source), str(ROOT / "src/Packet.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_primary_affinity_stale_queue_and_no_secondary_lease_appropriation(self):
        base = (ROOT / "src/helpers/bridges/BridgeBase.cpp").read_text()
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        mesh = (ROOT / "src/Mesh.cpp").read_text()
        receive = method(dispatcher, "void Dispatcher::processRecvPacket(")
        guard = receive[receive.index("  const auto* profiles ="):receive.index("  const uint8_t previous_profile")]
        ota_start = mesh.index("    case PAYLOAD_TYPE_OTA: {")
        ota_guard = mesh[mesh.index("      if (!isAnyTempRadioActive()", ota_start):
                         mesh.index("      observeOtaRequestPressure(pkt);", ota_start)]
        ota_guard = ota_guard.replace(" break;", " return false;")
        code = HARNESS[:HARNESS.index("@METHODS@")]
        code += method(base, "void BridgeBase::handleReceivedPacket(")
        code += r'''
using mesh::RadioCrossMode;
using mesh::RadioProfileProtocol;
struct Radio {
 mesh::RadioProfiles* state;
 const mesh::RadioProfiles* profiles() const {return state;}
};
struct Dispatcher {
 Radio* _radio; unsigned accepted=0,released=0;
 void releasePacket(mesh::Packet*) {++released;}
 void receive(mesh::Packet* pkt) {
@DISPATCH_GUARD@
  ++accepted;
 }
 bool isAnyTempRadioActive() const {
  return _radio->state && (_radio->state->primary_temporary || _radio->state->secondary_temporary);
 }
 bool otaEligible(mesh::Packet* pkt) {
@OTA_GUARD@
  return true;
 }
};
int main() {
 mesh::RadioProfiles profiles;
 profiles.generation[0]=12;profiles.generation[1]=93;
 profiles.primary_temporary=false;profiles.secondary_temporary=true;
 profiles.cross=mesh::RadioCrossMode::Off;
 BridgeBase bridge;bridge.setIngressProfiles(&profiles);
 mesh::Packet packet;
 packet.header=(PAYLOAD_TYPE_OTA<<2)|1;
 packet.radio_profile=packet.radio_origin=1;
 packet.radio_generation=packet.radio_origin_generation=93;
 packet.radio_bound=true;packet.radio_local=true;packet.tx_radio=mesh::RADIO_TX_BOTH;
 packet.radio_reply=packet.radio_reply_force=true;
 bridge.handleReceivedPacket(&packet);
 assert(bridge.manager.queued==1&&bridge.manager.freed==0);
 assert(packet.radio_profile==0&&packet.radio_origin==0);
 assert(packet.radio_generation==12&&packet.radio_origin_generation==12);
 assert(!packet.radio_bound&&!packet.radio_local&&packet.tx_radio==mesh::RADIO_TX_AUTO);
 assert(!packet.radio_reply&&!packet.radio_reply_force);
 Radio radio{&profiles};Dispatcher dispatch{&radio};
 dispatch.receive(&packet);assert(dispatch.accepted==1&&dispatch.released==0);
 // A secondary-only update window is not permission to steal that lease.
 assert(!dispatch.otaEligible(&packet));
 profiles.primary_temporary=true;assert(dispatch.otaEligible(&packet));
 // Frames already admitted with bridge.delay retain the old primary lease.
 profiles.generation[0]=13;
 dispatch.receive(&packet);assert(dispatch.accepted==1&&dispatch.released==1);
 assert(packet.radio_generation==12&&packet.radio_origin_generation==12);
 mesh::Packet fresh;fresh.header=packet.header;bridge.handleReceivedPacket(&fresh);
 assert(fresh.radio_generation==13&&fresh.radio_profile==0);
 dispatch.receive(&fresh);assert(dispatch.accepted==2&&dispatch.released==1);
 // An explicit cross policy remains the sole opt-in to the other temp tuple.
 profiles.primary_temporary=false;profiles.cross=mesh::RadioCrossMode::On;
 assert(dispatch.otaEligible(&fresh));profiles.cross=mesh::RadioCrossMode::Off;
 // A foreign primary tuple cannot reinterpret bridge MeshCore bytes.
 profiles.primary.protocol=mesh::RadioProfileProtocol::Meshtext;
 const auto queued=bridge.manager.queued;
 bridge.handleReceivedPacket(&fresh);
 assert(bridge.manager.queued==queued&&bridge.manager.freed==1);
 // Legacy single-profile radios have no generation table; preserve that path.
 bridge.setIngressProfiles(nullptr);radio.state=nullptr;
 bridge.handleReceivedPacket(&fresh);assert(fresh.radio_generation==0&&!fresh.radio_local);
 dispatch.receive(&fresh);assert(dispatch.accepted==3&&dispatch.released==1);
}
'''
        code = code.replace("@DISPATCH_GUARD@", guard).replace("@OTA_GUARD@", ota_guard)
        with tempfile.TemporaryDirectory(prefix="meshcore-bridge-ingress-") as directory:
            work = Path(directory)
            (work / "Mesh.h").write_text(PACKET)
            source, binary = work / "main.cpp", work / "test"
            source.write_text(code)
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(work), "-I", str(ROOT / "src"), str(source), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_every_role_bridge_configuration_binds_current_profiles(self):
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                header = (ROOT / f"examples/{role}/MyMesh.h").read_text()
                configure = method(header, "void configureBridgeFilter(")
                self.assertIn("active_bridge->setIngressProfiles(_radio->profiles());", configure)
                if role == "simple_repeater":
                    self.assertLess(configure.index("setIngressProfiles"), configure.index("#if MESH_ENABLE_FLOOD_RULE_ENGINE"))


if __name__ == "__main__":
    unittest.main()
