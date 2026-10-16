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

    def test_every_role_bridge_configuration_binds_the_primary_profiles(self):
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        configure = method(header, "void configureBridgeFilter(")
        self.assertIn("active_bridge->setIngressProfiles(_radio->profiles());", configure)
        self.assertLess(configure.index("setIngressProfiles"), configure.index("#if MESH_ENABLE_FLOOD_RULE_ENGINE"))


if __name__ == "__main__":
    unittest.main()
