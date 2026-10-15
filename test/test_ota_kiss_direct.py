"""Exercise direct OTA wire framing and the real bounded Dispatcher owner.

These native host tests do not transmit RF, invoke PlatformIO, or claim that a
hosted modem delivered a frame. OtaManager negotiation/geometry has separate
coverage; this file tests the scheduler and its admission-policy boundary.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_meshtext_transport import HARNESS as TRANSPORT_HARNESS
from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]


def dispatcher_harness():
    code = TRANSPORT_HARNESS.split("int main() {", 1)[0]
    code = code.replace(
        "#include <Dispatcher.h>",
        "#include <Dispatcher.h>\n#include <helpers/ota/OtaDirectWire.h>\n"
        "#include <helpers/ota/OtaTiming.h>\n#include <cstdio>",
    )
    code = code.replace(
        "bool incoming=false;",
        "bool incoming=false; uint32_t rx_generation=0; std::vector<uint8_t> rx;",
    )
    code = code.replace(
        "uint8_t receiveProfile() const override { return active; }",
        "uint8_t receiveProfile() const override { return active; }\n"
        "  uint32_t receiveProfileGeneration() const override {\n"
        "    return rx_generation ? rx_generation : p.generation[active];\n  }",
    )
    start = code.index("  int recvRaw(")
    end = code.index("  void onReceiveProcessed()", start)
    code = code[:start] + r'''
  int recvRaw(uint8_t* bytes,int capacity) override {
    if (!incoming) return 0;
    incoming=false;
    assert(rx.size()<=size_t(capacity));
    if (!rx.empty()) memcpy(bytes,rx.data(),rx.size());
    return int(rx.size());
  }
''' + code[end:]
    code = code.replace(
        "bool allow_foreign=true;",
        "bool allow_foreign=true, allow_direct=false;\n"
        "  unsigned direct_calls=0, direct_rx=0; float speed=1; uint32_t epoch=1;\n"
        "  std::vector<uint8_t> direct_message;",
    )
    code = code.replace(
        "using Dispatcher::queueForeignRadioFrame;",
        "using Dispatcher::queueForeignRadioFrame;\n"
        "  using Dispatcher::discardQueuedForeignRadioFrame;\n"
        "#if MESH_ENABLE_OTA_KISS_DIRECT\n"
        "  using Dispatcher::queueDirectOtaRadioFrame;\n"
        "  uint32_t directOtaEpoch() const override { return epoch; }\n"
        "  bool allowDirectOtaTransmit(uint8_t,uint32_t) const override { return allow_direct; }\n"
        "  bool onDirectOtaRadioFrame(const uint8_t* b,size_t n,uint8_t,uint32_t) override {\n"
        "    ++direct_calls; const uint8_t* msg=nullptr;uint16_t size=0;\n"
        "    if (!allow_direct || !ota::ota_direct_wire_decode(b,n,msg,size)) return false;\n"
        "    ++direct_rx;direct_message.assign(msg,msg+size);return true;\n"
        "  }\n#endif\n"
        "  float getOtaPacketSpeedFactor() const override { return speed; }",
    )
    code += r'''
static void directProfile(Fixture& f,uint8_t profile=1) {
  f.radio.p.secondary.params.protocol=RadioProfileProtocol::MeshCore;
  f.radio.p.secondary.params.preamble=32;
  f.radio.p.primary_temporary=profile==0;
  f.radio.p.secondary_temporary=profile==1;
  f.d.allow_direct=true;
}
#if MESH_ENABLE_OTA_KISS_DIRECT
static bool enqueue(Fixture& f,uint8_t profile=1) {
  const uint8_t msg[]={ota::OTA_REQ,0xc0,0xdb};
  return f.d.queueDirectOtaRadioFrame(msg,sizeof(msg),profile,f.radio.p.generation[profile]);
}
#endif
static void receive(Fixture& f,const std::vector<uint8_t>& frame,uint8_t profile=1) {
  f.radio.active=profile;f.radio.rx=frame;f.radio.incoming=true;f.tick();
}
'''
    return code


class OtaKissDirectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def compile_run(self, code, flags=(), dispatcher=False, expect_compile=True):
        with tempfile.TemporaryDirectory(prefix="meshcore-ota-kiss-direct-") as folder:
            cpp, binary = Path(folder) / "test.cpp", Path(folder) / "test.exe"
            cpp.write_text(code, encoding="utf-8")
            command = [self.compiler, "-std=c++17", "-Wall", "-Wextra", *flags]
            if os.name != "nt":
                command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
            command += ["-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src")]
            if dispatcher:
                command += [str(ROOT / source) for source in (
                    "src/Dispatcher.cpp", "src/Packet.cpp", "src/helpers/StaticPoolPacketManager.cpp")]
            command += [str(cpp), "-o", str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if not expect_compile:
                self.assertNotEqual(result.returncode, 0, "invalid configuration unexpectedly compiled")
                return result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-16000:])
            env = os.environ.copy()
            env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_wire_full_frame_binary_escaping_and_rejection(self):
        code = r'''
#include <helpers/ota/OtaDirectWire.h>
#include <helpers/EthernetKiss.h>
#include <cassert>
#include <vector>
using namespace mesh;
int main() {
  static_assert(ota::OTA_DIRECT_FRAG_DATA==240,"full data budget");
  uint8_t message[253];message[0]=ota::OTA_DATA;
  for (unsigned i=1;i<sizeof(message);++i) message[i]=uint8_t(i);
  message[1]=0xc0;message[2]=0xdb;message[3]=0;
  uint8_t raw[256]={};
  assert(ota::ota_direct_wire_encode(raw,255,message,sizeof(message))==255);
  assert(raw[0]==0xf2 && raw[1]==0);
  const uint8_t* decoded=nullptr;uint16_t length=0;
  assert(ota::ota_direct_wire_decode(raw,255,decoded,length));
  assert(length==253 && !memcmp(decoded,message,length));
  // Verify KISS quoting round trips the complete RF255, including both escapes.
  ethernet::KissCodec sender,receiver;
  assert(sender.queueReceive(raw,255));const uint8_t* encoded;size_t count;
  assert(sender.peekOutput(encoded,count));assert(count>258);
  for (size_t i=0;i<count;++i) assert(receiver.feed(encoded[i]));
  const uint8_t* roundtrip;size_t roundtrip_len;
  assert(receiver.peekTransmit(roundtrip,roundtrip_len));
  assert(roundtrip_len==255 && !memcmp(roundtrip,raw,255));
  assert(!ota::ota_direct_wire_encode(raw,254,message,253));
  assert(!ota::ota_direct_wire_encode(nullptr,255,message,253));
  assert(!ota::ota_direct_wire_encode(raw,255,nullptr,1));
  assert(!ota::ota_direct_wire_encode(raw,255,message,0));
  assert(!ota::ota_direct_wire_encode(raw,256,message,254));
  assert(!ota::ota_direct_wire_decode(nullptr,3,decoded,length));
  assert(!decoded && !length);
  for (unsigned header=0;header<256;++header) {
    uint8_t frame[]={uint8_t(header),0,ota::OTA_REQ};
    assert(ota::ota_direct_wire_decode(frame,3,decoded,length)==(header==0xf2));
  }
  uint8_t short_frame[]={0xf2,0,ota::OTA_REQ};
  for (size_t n=0;n<3;++n) assert(!ota::ota_direct_wire_decode(short_frame,n,decoded,length));
  for (unsigned path=1;path<256;++path) {
    short_frame[1]=uint8_t(path);assert(!ota::ota_direct_wire_decode(short_frame,3,decoded,length));
  }
  raw[1]=0;assert(!ota::ota_direct_wire_decode(raw,256,decoded,length));
  for (unsigned type=0;type<256;++type) {
    short_frame[1]=0;short_frame[2]=uint8_t(type);
    assert(ota::ota_direct_wire_decode(short_frame,3,decoded,length)==ota::ota_is_transfer_message(uint8_t(type)));
    assert(bool(ota::ota_direct_wire_encode(raw,255,short_frame+2,1))==ota::ota_is_transfer_message(uint8_t(type)));
  }
}
'''
        self.compile_run(code)

    def test_feature_defaults_invalid_configuration_and_packet_footprint(self):
        code = r'''
#include <Dispatcher.h>
#include <cstdio>
static_assert(MESH_ENABLE_OTA_KISS_DIRECT==EXPECT_DIRECT,"feature scope");
static_assert(bool(MESH_ENABLE_FOREIGN_RADIO)==bool(EXPECT_DIRECT),"shared slot scope");
static_assert(MAX_PACKET_PAYLOAD==184,"routed payload remains unchanged");
int main() { std::printf("%zu\n",sizeof(mesh::Packet)); }
'''
        sizes = set()
        for flags, enabled in (
            ([], 0), (["-DENABLE_OTA"], 1),
            (["-DENABLE_OTA", "-DMESH_PRIMARY_ESPNOW=1"], 0),
            (["-DENABLE_OTA", "-DMESH_PRIMARY_ESPNOW=0"], 1),
            (["-DENABLE_OTA", "-DMESH_ENABLE_OTA_KISS_DIRECT=0"], 0),
            (["-DENABLE_OTA", "-DMESH_ENABLE_FOREIGN_RADIO=0"], 0),
        ):
            with self.subTest(flags=flags):
                sizes.add(self.compile_run(code, [*flags, f"-DEXPECT_DIRECT={enabled}",
                    "-DMESH_ENABLE_MESHTEXT_CHAT=0", "-DMESH_ENABLE_ETHERNET_TUNNEL=0"]).strip())
        self.assertEqual(len(sizes), 1, "direct support must not enlarge each Packet")
        for flags in (["-DMESH_ENABLE_OTA_KISS_DIRECT=1"],
                      ["-DENABLE_OTA", "-DMESH_ENABLE_OTA_KISS_DIRECT=2"],
                      ["-DENABLE_OTA", "-DMESH_ENABLE_OTA_KISS_DIRECT=1", "-DMESH_ENABLE_FOREIGN_RADIO=0"]):
            diagnostic = self.compile_run("#include <helpers/ota/OtaDirectConfig.h>\nint main() {}",
                                          flags, expect_compile=False)
            self.assertIn("direct", diagnostic.lower())

    def test_real_dispatcher_profile_affinity_admission_and_accounting(self):
        code = dispatcher_harness() + r'''
int main() {
  using P=RadioProfileProtocol;
  for (uint8_t profile: {0,1}) {
    Fixture f;directProfile(f,profile);const auto free=f.manager.getFreeCount();
    uint8_t message[253];message[0]=ota::OTA_DATA;
    for (unsigned i=1;i<253;++i) message[i]=uint8_t(i);
    assert(f.d.queueDirectOtaRadioFrame(message,253,profile,f.radio.p.generation[profile]));
    assert(f.manager.getFreeCount()==free && f.manager.getOutboundTotal()==0);
    assert(!enqueue(f,profile));f.tick();
    assert(f.radio.sends==1 && f.radio.active==profile && f.radio.sent.size()==255);
    assert(f.radio.sent[0]==0xf2 && !f.radio.sent[1] && !memcmp(f.radio.sent.data()+2,message,253));
    f.tick(20);f.radio.complete=true;f.tick();
    assert(!f.d.hasOutbound() && f.d.done.empty() && !f.d.mc_sent);
    assert(f.radio.p.tx_packets[profile]==1 && f.d.getNumSentDirect()==1 && !f.d.getNumSentFlood());
    assert(f.d.getTotalAirTime()==21 && f.manager.getFreeCount()==free);
  }
  { Fixture f;directProfile(f);f.d.allow_direct=false;assert(!enqueue(f));
    f.d.allow_direct=true;f.radio.carrier=true;assert(!enqueue(f));f.radio.carrier=false;
    f.radio.p.secondary.mode=RadioProfileMode::Rx;assert(!enqueue(f));
    f.radio.p.secondary.mode=RadioProfileMode::Off;assert(!enqueue(f));
    f.radio.p.secondary.mode=RadioProfileMode::RxTx;
    assert(!f.d.queueDirectOtaRadioFrame(bytes,3,2,1));
    assert(!f.d.queueDirectOtaRadioFrame(bytes,3,1,f.radio.p.generation[1]+1));
    f.radio.p.secondary.params.protocol=P::Ethernet;assert(!enqueue(f));
    f.radio.p.secondary.params.protocol=P::MeshCore;
    assert(!f.d.queueDirectOtaRadioFrame(nullptr,3,1,f.radio.p.generation[1]));
    const uint8_t discover[]={ota::OTA_ADV};
    assert(!f.d.queueDirectOtaRadioFrame(discover,1,1,f.radio.p.generation[1]));
    assert(!f.d.queueForeignRadioFrame(bytes,3,1,f.radio.p.generation[1],P::MeshCore));
  }
  for (unsigned changed=0;changed<4;++changed) {
    Fixture f;directProfile(f);assert(enqueue(f));
    if (changed==0) ++f.radio.p.generation[1];
    if (changed==1) f.radio.p.secondary.params.protocol=P::Meshtext;
    if (changed==2) f.radio.p.secondary.mode=RadioProfileMode::Off;
    if (changed==3) f.d.allow_direct=false;
    f.tick();assert(!f.radio.sends && !f.d.hasOutbound() && f.d.done.empty());
    assert(!f.d.getNumSentDirect() && !f.radio.p.tx_packets[1]);
  }
  { Fixture f;directProfile(f);assert(enqueue(f));f.tick();
    ++f.radio.p.generation[1];f.radio.complete=true;f.tick(10);
    assert(!f.d.hasOutbound() && f.d.done.empty() && !f.d.getNumSentDirect());
    assert(f.d.getTotalAirTime()==10 && !f.radio.p.tx_packets[1]);
  }
  { Fixture f;directProfile(f);assert(enqueue(f));
    assert(!f.d.discardQueuedForeignRadioFrame(P::Ethernet));
    assert(f.d.discardQueuedForeignRadioFrame(P::MeshCore));
    f.tick();assert(!f.radio.sends && !f.d.hasOutbound() && f.d.done.empty());
    assert(enqueue(f));f.tick();assert(!f.d.discardQueuedForeignRadioFrame(P::MeshCore));
    f.radio.complete=true;f.tick();assert(f.d.getNumSentDirect()==1);
  }
}
'''
        self.compile_run(code, ["-DENABLE_OTA", "-DMESH_ENABLE_MESHTEXT_CHAT=0",
                               "-DMESH_ENABLE_ETHERNET_TUNNEL=0"], dispatcher=True)

    def test_real_dispatcher_epoch_retires_abort_fallback_and_old_context(self):
        code = dispatcher_harness() + r'''
int main() {
  { Fixture f;directProfile(f);f.d.epoch=0;assert(!enqueue(f));assert(!f.d.hasOutbound()); }
  // The manager changes the opaque token on abort, fallback and context
  // reconstruction. Neither a stable radio generation nor a live temp window
  // can authorize an already-admitted frame belonging to the former token.
  for (uint32_t retired: {0u,2u,0xffffffffu}) {
    Fixture f;directProfile(f);assert(enqueue(f));f.d.epoch=retired;f.tick();
    assert(!f.radio.sends && !f.d.hasOutbound() && f.d.done.empty());
    assert(!f.d.getNumSentDirect() && !f.radio.p.tx_packets[1]);
    f.d.epoch=3;assert(enqueue(f));f.tick();assert(f.radio.sends==1);
    f.radio.complete=true;f.tick();assert(f.d.getNumSentDirect()==1 && f.d.done.empty());
  }
  { Fixture f;directProfile(f);f.radio.prepare=RadioParamApplyResult::BUSY;
    assert(enqueue(f));f.tick();f.d.epoch=2;f.tick(20);
    assert(!f.radio.sends && !f.d.hasOutbound() && !f.radio.recovery && f.d.done.empty());
  }
  { Fixture f;directProfile(f);assert(enqueue(f));f.tick();f.d.epoch=2;
    f.radio.complete=true;f.tick(10);
    assert(f.radio.sends==1 && f.radio.finishes==1 && !f.d.hasOutbound());
    assert(f.d.getTotalAirTime()==10 && !f.d.getNumSentDirect() && !f.radio.p.tx_packets[1]);
    assert(f.d.done.empty());f.tick(5000);assert(f.radio.sends==1);
    assert(enqueue(f));f.tick();assert(f.radio.sends==2); // new token remains usable
  }
  { Fixture f;directProfile(f);assert(enqueue(f));f.tick();f.d.epoch=0;f.tick(1151);
    assert(f.radio.sends==1 && f.radio.finishes==1 && f.radio.recovery==1 && !f.d.hasOutbound());
    assert(f.d.getTotalAirTime()==1151 && !f.d.getNumSentDirect() && f.d.done.empty());
  }
}
'''
        self.compile_run(code, ["-DENABLE_OTA", "-DMESH_ENABLE_MESHTEXT_CHAT=0",
                               "-DMESH_ENABLE_ETHERNET_TUNNEL=0"], dispatcher=True)

    def test_real_dispatcher_failures_cad_duty_quiet_and_packet_fairness(self):
        code = dispatcher_harness() + r'''
int main() {
  { Fixture f;directProfile(f);f.radio.start_ok=false;assert(enqueue(f));f.tick();f.tick(10000);
    assert(f.radio.sends==1 && !f.d.hasOutbound() && f.d.done.empty() && !f.d.getNumSentDirect());
  }
  { Fixture f;directProfile(f);assert(enqueue(f));f.tick();f.tick(1151);
    assert(f.radio.sends==1 && f.radio.finishes==1 && f.radio.recovery==1 && !f.d.hasOutbound());
    f.tick(10000);assert(f.radio.sends==1 && f.d.done.empty());
  }
  { Fixture f;directProfile(f);f.radio.prepare=RadioParamApplyResult::FAILED;assert(enqueue(f));f.tick();
    assert(!f.radio.sends && !f.d.hasOutbound() && f.d.done.empty());
  }
  { Fixture f;directProfile(f);f.radio.prepare=RadioParamApplyResult::BUSY;assert(enqueue(f));f.tick();f.tick(8151);
    assert(!f.radio.sends && !f.d.hasOutbound() && f.radio.recovery==1 && f.d.done.empty());
  }
  { Fixture f;directProfile(f);f.radio.receiving=true;assert(enqueue(f));f.tick();f.tick(201);
    assert(!f.radio.sends && f.d.hasOutbound());f.radio.receiving=false;f.tick(201);assert(f.radio.sends==1);
  }
  { Fixture f;directProfile(f);assert(enqueue(f));f.tick();f.d.setRadioAvailable(false);f.tick(10000);
    assert(f.radio.sends==1 && f.radio.finishes==1 && !f.d.hasOutbound() && !enqueue(f) && f.d.done.empty());
  }
  { Fixture f;directProfile(f);assert(enqueue(f));f.tick();f.d.begin();
    assert(f.radio.finishes==1 && !f.d.hasOutbound() && !f.d.getNumSentDirect() && f.d.done.empty());
  }
  { Fixture f;directProfile(f);f.d.duty_window=200;f.d.begin();assert(enqueue(f));f.tick();
    f.clock.now+=100;f.radio.complete=true;f.d.loop();assert(f.d.getRemainingTxBudget()==0);
    assert(enqueue(f));f.tick(100);assert(f.radio.sends==1);f.tick(101);assert(f.radio.sends==2);
  }
  { Fixture f;directProfile(f);f.d.speed=0.5;assert(enqueue(f));f.tick();f.tick(99);
    f.radio.complete=true;f.tick();assert(f.d.getTotalAirTime()==100);
    assert(enqueue(f));f.tick(99);assert(f.radio.sends==1);f.tick(2);assert(f.radio.sends==2);
  }
  { Fixture f;directProfile(f);assert(enqueue(f));f.tick();f.radio.complete=true;f.tick();
    assert(enqueue(f));auto* packet=newPacket(f.d,RADIO_TX_PRIMARY);assert(f.d.sendPacket(packet,0));f.tick();
    assert(f.radio.sends==2 && f.radio.active==0);f.radio.complete=true;f.tick();f.tick();
    assert(f.radio.sends==3 && f.radio.active==1 && f.d.mc_sent==1 && f.d.done.empty());
  }
}
'''
        self.compile_run(code, ["-DENABLE_OTA", "-DMESH_ENABLE_MESHTEXT_CHAT=0",
                               "-DMESH_ENABLE_ETHERNET_TUNNEL=0"], dispatcher=True)

    def test_real_dispatcher_receive_isolation_without_packet_credit(self):
        code = dispatcher_harness() + r'''
int main() {
  const std::vector<uint8_t> valid={0xf2,0,ota::OTA_REQ,0xc0,0xdb};
  { Fixture f;directProfile(f);std::vector<Packet*> reserved;
    while (auto* p=f.d.obtainNewPacket()) reserved.push_back(p);
    assert(!f.manager.getFreeCount());receive(f,valid);
    assert(f.d.direct_rx==1 && f.d.direct_message==std::vector<uint8_t>(valid.begin()+2,valid.end()));
    assert(!f.d.mc_rx && !f.d.foreign_rx && !f.d.raw_logs && !f.d.getLastMeshCoreRecvMillis());
    assert(f.d.getNumRecvDirect()==1 && !f.d.getNumRecvFlood() && f.radio.p.rx_packets[1]==1);
    assert(f.radio.processed==1 && f.d.getReceiveAirTime()==100 && !f.manager.getFreeCount());
    for (auto* p:reserved) f.d.releasePacket(p);
  }
  { Fixture f;directProfile(f);f.d.allow_direct=false;receive(f,valid);
    assert(f.d.direct_calls==1 && !f.d.direct_rx && !f.d.raw_logs && !f.d.mc_rx && !f.d.foreign_rx);
    assert(!f.d.getNumRecvDirect() && !f.radio.p.rx_packets[1] && !f.d.getReceiveAirTime());
  }
  for (const auto& bad: std::vector<std::vector<uint8_t>>{
      {0xf2},{0xf2,0},{0xf2,1,ota::OTA_REQ},{0xf2,0,ota::OTA_ADV},{0xf2,0,0xff}}) {
    Fixture f;directProfile(f);receive(f,bad);
    assert(f.d.direct_calls==1 && !f.d.direct_rx && !f.d.raw_logs && !f.d.mc_rx && !f.d.foreign_rx);
    assert(!f.d.getNumRecvDirect() && !f.radio.p.rx_packets[1] && f.radio.processed==1);
  }
  { Fixture f;directProfile(f);f.radio.rx_generation=f.radio.p.generation[1];++f.radio.p.generation[1];receive(f,valid);
    assert(!f.d.direct_calls && !f.d.raw_logs && !f.d.mc_rx && f.radio.processed==1);
  }
  { Fixture f;directProfile(f,0);receive(f,valid,0);assert(f.d.direct_rx==1 && f.radio.p.rx_packets[0]==1); }
  { Fixture f;directProfile(f);auto flood=valid;flood[0]=0xf1;receive(f,flood);
    assert(!f.d.direct_calls && !f.d.mc_rx && !f.d.getNumRecvFlood() && !f.manager.getOutboundTotal());
  }
}
'''
        self.compile_run(code, ["-DENABLE_OTA", "-DMESH_ENABLE_MESHTEXT_CHAT=0",
                               "-DMESH_ENABLE_ETHERNET_TUNNEL=0"], dispatcher=True)

    def test_real_dispatcher_disabled_and_shared_transport_ownership(self):
        code = dispatcher_harness() + r'''
int main() {
#if MESH_ENABLE_OTA_KISS_DIRECT
  { Fixture f;directProfile(f);assert(enqueue(f));assert(!f.d.queue(bytes));
    f.d.discardQueuedForeignRadioFrame(RadioProfileProtocol::MeshCore);
    f.radio.p.secondary.params.protocol=RadioProfileProtocol::Meshtext;
    assert(f.d.queue(bytes));assert(!enqueue(f,0));
  }
#else
  { Fixture f;directProfile(f);receive(f,{0xf2,0,ota::OTA_REQ});
    assert(!f.d.direct_calls && !f.d.mc_rx && !f.manager.getOutboundTotal());
  }
#endif
  // Existing generic Meshtext ownership and its completion callback are unchanged.
  { Fixture f;assert(f.d.queue(bytes));f.tick();f.radio.complete=true;f.tick();
    assert(f.d.done.size()==1 && f.d.done[0] && !f.d.getNumSentDirect());
  }
}
'''
        for enabled in (0, 1):
            with self.subTest(direct=enabled):
                self.compile_run(code, ["-DENABLE_OTA", f"-DMESH_ENABLE_OTA_KISS_DIRECT={enabled}",
                                       "-DMESH_ENABLE_MESHTEXT_CHAT=1"], dispatcher=True)

    def test_mesh_temp_policy_and_no_unsolicited_context_allocation(self):
        source = (ROOT / "src/Mesh.cpp").read_text()
        epoch = method(source, "uint32_t Mesh::directOtaEpoch(")
        policy = method(source, "bool Mesh::allowDirectOtaTransmit(")
        receive = method(source, "bool Mesh::onDirectOtaRadioFrame(")
        code = r'''
#include <Dispatcher.h>
#include <helpers/ota/OtaDirectWire.h>
#include <cassert>
namespace mesh { namespace ota {
struct OtaReplyRoute { uint8_t profile=255;uint32_t generation=0;bool direct=false; };
struct Manager {
  unsigned calls=0;bool enabled=false;uint8_t hops=255;OtaReplyRoute route;
  uint32_t directTransportEpoch() const { return 42; }
  void set_direct_transport_enabled(bool value) { enabled=value; }
  void note_rx_path_hops(uint8_t value) { hops=value; }
  bool on_message(const uint8_t*,uint16_t,OtaReplyRoute value) { ++calls;route=value;return true; }
  uint8_t fetchState() const { return 0; }
};
struct Context { Manager manager;void track_session(uint8_t,unsigned long) {} } context;
bool exists=false;unsigned lookups=0;
Context* ota_context_if_active() { ++lookups;return exists ? &context:nullptr; }
} }
namespace mesh {
struct TestRadio { RadioProfiles p;const RadioProfiles* profiles() const { return &p; } };
struct Clock { unsigned long getMillis() const { return 1000; } };
class Mesh {
public:
  TestRadio radio;TestRadio* _radio=&radio;Clock clock;Clock* _ms=&clock;
  bool temp=false,primary=false,transmit=true;
  Mesh() {
    radio.p.primary.freq=909.5;radio.p.primary.bw=500;radio.p.primary.sf=5;radio.p.primary.cr=5;
    radio.p.secondary.params=radio.p.primary;radio.p.secondary.mode=RadioProfileMode::RxTx;
  }
  bool isAnyTempRadioActive() const { return temp; }
  bool isTempRadioActive() const { return primary; }
  bool allowPacketTransmit(const Packet*) const { return transmit; }
  void syncOtaTiming(ota::Manager&) {}
  bool allowDirectOtaTransmit(uint8_t,uint32_t) const;
  uint32_t directOtaEpoch() const;
  bool onDirectOtaRadioFrame(const uint8_t*,size_t,uint8_t,uint32_t);
};
@POLICY@
@EPOCH@
@RECEIVE@
}
int main() {
  using namespace mesh;Mesh m;const auto gen=m.radio.p.generation[1];
  const uint8_t frame[]={0xf2,0,ota::OTA_REQ};
  assert(!m.directOtaEpoch() && !ota::exists && ota::lookups==1);
  assert(!m.allowDirectOtaTransmit(1,gen));m.temp=true;
  assert(!m.allowDirectOtaTransmit(1,gen));m.radio.p.secondary_temporary=true;
  assert(m.allowDirectOtaTransmit(1,gen));
  assert(!m.onDirectOtaRadioFrame(frame,3,1,gen) && ota::lookups==2 && !ota::context.manager.calls);
  ota::exists=true;assert(m.onDirectOtaRadioFrame(frame,3,1,gen));
  assert(m.directOtaEpoch()==42);
  assert(ota::context.manager.calls==1 && ota::context.manager.enabled && ota::context.manager.hops==0);
  assert(ota::context.manager.route.direct && ota::context.manager.route.profile==1 && ota::context.manager.route.generation==gen);
  m.transmit=false;assert(!m.onDirectOtaRadioFrame(frame,3,1,gen));m.transmit=true;
  assert(!m.allowDirectOtaTransmit(1,gen+1) && !m.allowDirectOtaTransmit(2,gen));
  m.radio.p.secondary.mode=RadioProfileMode::Rx;assert(!m.allowDirectOtaTransmit(1,gen));
  m.radio.p.secondary.mode=RadioProfileMode::RxTx;
  m.radio.p.secondary.params.protocol=RadioProfileProtocol::Ethernet;assert(!m.allowDirectOtaTransmit(1,gen));
  m.radio.p.secondary.params.protocol=RadioProfileProtocol::MeshCore;
  m.radio.p.secondary_temporary=false;m.primary=true;
  assert(!m.allowDirectOtaTransmit(1,gen));assert(m.allowDirectOtaTransmit(0,m.radio.p.generation[0]));
  m.primary=false;m.radio.p.primary_temporary=true;
  assert(m.allowDirectOtaTransmit(0,m.radio.p.generation[0]));
  m.radio.p.primary_temporary=false;m.radio.p.cross=RadioCrossMode::On;
  assert(m.allowDirectOtaTransmit(1,gen));m.temp=false;assert(!m.allowDirectOtaTransmit(1,gen));
}
'''.replace("@POLICY@", policy).replace("@RECEIVE@", receive).replace("@EPOCH@", epoch)
        self.compile_run(code, ["-DENABLE_OTA", "-DMESH_ENABLE_MESHTEXT_CHAT=0"])


if __name__ == "__main__":
    unittest.main()
