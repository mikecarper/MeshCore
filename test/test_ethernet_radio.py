"""Exercise the actual foreign-radio isolation and Ethernet ownership layer.

These host tests prove routing/state contracts, not RF delivery or TCP speed.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_meshtext_transport import HARNESS as TRANSPORT_HARNESS
from test_radio_profile_scan import HARNESS as SCAN_HARNESS
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]


class EthernetRadioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("a host C++17 compiler is required")

    def compile_run(self, code, flags=(), sources=(), includes=(), arduino_stub=False):
        with tempfile.TemporaryDirectory(prefix="meshcore-ethernet-radio-") as folder:
            cpp, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            cpp.write_text(code, encoding="utf-8")
            command = [self.compiler, "-std=c++17", "-Wall", "-Wextra", *flags]
            if arduino_stub:
                (Path(folder) / "Arduino.h").write_text("#pragma once\n#include <cstdint>\n#include <cmath>\n")
                command += ["-I", folder]
            if os.name != "nt":
                command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
            for include in includes:
                command += ["-I", str(ROOT / include)]
            command += ["-I", str(ROOT), "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src")]
            command += [str(ROOT / source) for source in sources] + [str(cpp), "-o", str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-16000:])
            env = os.environ.copy()
            env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_feature_matrix_and_late_chip_discovery(self):
        code = r'''
#include <helpers/MeshtextConfig.h>
#if !defined(USE_SX1262)
#define USE_SX1262 1
#endif
static_assert(MESH_ENABLE_ETHERNET_TUNNEL == EXPECT_ETH, "Ethernet feature scope");
static_assert(bool(MESH_ENABLE_FOREIGN_RADIO) == bool(EXPECT_ETH || MESH_ENABLE_MESHTEXT_CHAT), "shared queue");
int main() {}
'''
        cases = [
            ([], 0),
            (["ESP32_PLATFORM", "COMPANION_RADIO_FULL", "WIFI_SSID=\"\""], 1),
            (["ESP32_PLATFORM", "COMPANION_RADIO_FULL"], 0),
            (["ESP32_PLATFORM", "WIFI_SSID=\"\""], 0),
            (["NRF52_PLATFORM", "RAK_4631", "COMPANION_RADIO_FULL", "WIFI_SSID=\"\""], 0),
            (["ESP32_PLATFORM", "COMPANION_RADIO_FULL", "WIFI_SSID=\"\"", "MESH_PRIMARY_ESPNOW=1"], 0),
            (["ESP32_PLATFORM", "COMPANION_RADIO_FULL", "WIFI_SSID=\"\"", "MESH_PRIMARY_ESPNOW=0"], 1),
            (["ESP32_PLATFORM", "COMPANION_RADIO_FULL", "WIFI_SSID=\"\"", "MESH_ENABLE_ETHERNET_TUNNEL=0"], 0),
        ]
        for defines, expected in cases:
            with self.subTest(defines=defines):
                self.compile_run(code, [*("-D" + item for item in defines), "-DEXPECT_ETH=" + str(expected)])

    def test_dispatcher_ethernet_only_and_shared_queue(self):
        code = TRANSPORT_HARNESS.split("int main() {", 1)[0]
        code = code.replace("bool incoming=false;", "bool incoming=false, ethernet_supported=true;")
        code = code.replace("uint8_t active=0;", "uint8_t active=0; uint32_t rx_generation=0;")
        code = code.replace("uint8_t receiveProfile() const override { return active; }",
            "uint8_t receiveProfile() const override { return active; }\n"
            "  uint32_t receiveProfileGeneration() const override { return rx_generation ? rx_generation : p.generation[active]; }")
        code = code.replace("bool supportsMeshtext() const override { return supported; }",
            "bool supportsMeshtext() const override { return supported; }\n"
            "  bool supportsEthernetTunnel() const override { return ethernet_supported; }")
        code = code.replace("std::vector<bool> done;",
            "std::vector<bool> done; std::vector<RadioProfileProtocol> done_protocols;")
        code = code.replace("done.push_back(ok);",
            "done.push_back(ok); done_protocols.push_back(queuedForeignRadioProtocol());")
        code = code.replace("using Dispatcher::hasForeignRadioFrame;",
            "using Dispatcher::hasForeignRadioFrame;\n  using Dispatcher::discardQueuedForeignRadioFrame;")
        code += r'''
static void ethernet(Fixture& f) {
  f.radio.p.secondary.params.protocol=RadioProfileProtocol::Ethernet;
  f.radio.p.secondary.params.preamble=32;
}
static bool enqueue(Fixture& f) {
  return f.d.queueForeignRadioFrame(bytes,sizeof(bytes),1,f.radio.p.generation[1],RadioProfileProtocol::Ethernet);
}
int main() {
  using P=RadioProfileProtocol;
  { Fixture f;ethernet(f);assert(enqueue(f));f.tick();
    assert(f.radio.sends==1 && f.radio.active==1 && f.radio.sent==std::vector<uint8_t>(bytes,bytes+3));
    f.radio.complete=true;f.tick();assert(f.d.done.size()==1 && f.d.done[0]);
    assert(f.d.done_protocols[0]==P::Ethernet && f.radio.p.tx_packets[1]==1);
    assert(!f.d.getNumSentFlood() && !f.d.getNumSentDirect());
  }
  { Fixture f;ethernet(f);assert(enqueue(f));
    f.radio.p.secondary.params.protocol=P::Meshtext; // even a bad external owner not bumping generation
    f.tick();assert(!f.radio.sends && f.d.done.size()==1 && !f.d.done[0]);
    assert(f.d.done_protocols[0]==P::Ethernet);
  }
  { Fixture f;ethernet(f);assert(enqueue(f));++f.radio.p.generation[1];f.tick();
    assert(!f.radio.sends && !f.d.done[0]);
  }
  { Fixture f;ethernet(f);f.radio.active=1;f.radio.incoming=true;f.tick();
    assert(f.d.foreign_rx==1 && !f.d.mc_rx && !f.d.raw_logs && !f.d.getLastMeshCoreRecvMillis());
    f.radio.ethernet_supported=false;f.radio.incoming=true;f.tick();
    assert(f.d.foreign_rx==1 && !f.d.mc_rx && !f.d.raw_logs);
    f.radio.p.secondary.params.protocol=P(99);f.radio.incoming=true;f.tick();
    assert(f.d.foreign_rx==1 && !f.d.mc_rx && !f.d.raw_logs && f.radio.processed==3);
  }
  { Fixture f;ethernet(f);f.radio.active=1;
    f.radio.rx_generation=f.radio.p.generation[1];
    f.radio.p.secondary.params.protocol=P::MeshCore;++f.radio.p.generation[1];
    f.radio.incoming=true;f.tick();
    assert(!f.d.foreign_rx && !f.d.mc_rx && !f.d.raw_logs && f.radio.processed==1);
  }
  { Fixture f;ethernet(f);f.radio.ethernet_supported=false;assert(!enqueue(f));
    f.radio.ethernet_supported=true;f.radio.p.secondary.mode=RadioProfileMode::Rx;assert(!enqueue(f));
    f.radio.p.secondary.mode=RadioProfileMode::RxTx;
    assert(!f.d.queueForeignRadioFrame(bytes,3,0,f.radio.p.generation[0],P::Ethernet));
    assert(!f.d.queueForeignRadioFrame(bytes,3,1,f.radio.p.generation[1],P::MeshCore));
    assert(!f.d.queueForeignRadioFrame(bytes,3,1,f.radio.p.generation[1],P(99)));
  }
  { Fixture f;ethernet(f);assert(enqueue(f));
    assert(!f.d.discardQueuedForeignRadioFrame(P::Meshtext));
    assert(f.d.discardQueuedForeignRadioFrame(P::Ethernet));
    assert(!f.d.hasForeignRadioFrame() && f.d.done.size()==1 && !f.d.done[0]);
    f.tick();assert(!f.radio.sends);
    assert(enqueue(f));f.tick();assert(!f.d.discardQueuedForeignRadioFrame(P::Ethernet));
    f.radio.complete=true;f.tick();assert(f.d.done.size()==2 && f.d.done[1]);
  }
  { Fixture f;ethernet(f);assert(enqueue(f));f.tick();f.tick(1151);
    assert(f.radio.sends==1 && f.radio.recovery==1 && !f.d.done[0]);
    f.tick(5000);assert(f.radio.sends==1);
  }
  { Fixture f;ethernet(f);auto* pkt=newPacket(f.d);f.radio.p.cross=RadioCrossMode::On;
    assert(!f.radio.p.canCross() && f.d.getTransmitProfileMask(pkt)==1);
    assert(f.d.sendPacket(pkt,0));f.tick();assert(f.radio.active==0);
    auto* denied=newPacket(f.d,RADIO_TX_SECONDARY);assert(!f.d.sendPacket(denied,0));
  }
  { Fixture f;ethernet(f);assert(enqueue(f));assert(!f.d.queue(bytes));
    f.d.setRadioAvailable(false);assert(!f.d.hasForeignRadioFrame() && !f.d.done[0]);
    assert(!enqueue(f));
  }
  { Fixture f;f.radio.active=1;f.radio.incoming=true;f.tick();
#if MESH_ENABLE_MESHTEXT_CHAT
    assert(f.d.foreign_rx==1);assert(f.d.queue(bytes));
#else
    assert(!f.d.foreign_rx);assert(!f.d.queue(bytes));
#endif
    assert(!f.d.mc_rx && !f.d.raw_logs);
  }
}
'''
        for chat in (0, 1):
            with self.subTest(chat=chat):
                self.compile_run(code, ["-DMESH_ENABLE_ETHERNET_TUNNEL=1", f"-DMESH_ENABLE_MESHTEXT_CHAT={chat}"],
                    ["src/Dispatcher.cpp", "src/Packet.cpp", "src/helpers/StaticPoolPacketManager.cpp"])

    def test_ethernet_auto_and_manual_ownership_without_chat(self):
        code = r'''
#define main existing_cli_main
#include "test/fixtures/radio_profiles/cli_test.cpp"
#undef main
int main() {
  using P=mesh::RadioProfileProtocol;
  Fixture f;
  mesh::RadioProfileConfig config;
  config.params=f.radio.p.primary;config.params.freq=910.5f;config.params.preamble=32;
  config.params.protocol=P::Ethernet;config.mode=mesh::RadioProfileMode::RxTx;
  assert(f.cli.canAutoSecondary() && f.cli.requestAutoSecondary(config));
  assert(f.cli.autoSecondaryActive() && f.radio.p.secondary.params.protocol==P::Ethernet);
  assert(f.radio.profilePreamble(1)==32 && f.fs.files.empty());
  const auto generation=f.radio.p.generation[1];
  assert(f.cli.requestAutoSecondary(config));assert(f.radio.p.generation[1]==generation);
  auto conflict=config;conflict.params.protocol=P::Meshtext;
  assert(!f.cli.requestAutoSecondary(conflict));
  f.cli.clearAutoSecondary();assert(!f.radio.p.enabled());
  f.cmd("set radio2 910.5,500,8,5,rxtx,80");f.advance(2000);
  const auto saved=f.fs.files["/radio_profiles"];
  assert(f.cli.useSecondaryProtocol(P::Ethernet));
  assert(f.radio.p.secondary.params.protocol==P::Ethernet && f.radio.profilePreamble(1)==80);
  assert(f.fs.files["/radio_profiles"]==saved);
  assert(!f.cli.useSecondaryProtocol(P::Meshtext));
  assert(f.radio.p.secondary.params.protocol==P::Ethernet);
  f.cli.clearSecondaryProtocol();assert(f.radio.p.secondary.params.protocol==P::MeshCore);
  assert(f.radio.profilePreamble(1)==80 && f.fs.files["/radio_profiles"]==saved);
  assert(f.cli.useSecondaryProtocol(P::Ethernet));
  f.cmd("set tempradio2 911.5,500,8,5,rxtx,1,80");f.advance(2000);
  f.cli.clearSecondaryProtocol();
  assert(f.radio.p.secondary.params.protocol==P::MeshCore && f.radio.p.secondary.params.freq==911.5f);
  assert(!f.cli.useSecondaryProtocol(P::Ethernet));
  Fixture veto;veto.cmd("set radio2 off");veto.advance(2000);
  assert(!veto.cli.canAutoSecondary() && !veto.cli.requestAutoSecondary(config));
}
'''
        for chat in (0, 1):
            with self.subTest(chat=chat):
                self.compile_run(code, ["-DMESH_ENABLE_ETHERNET_TUNNEL=1", f"-DMESH_ENABLE_MESHTEXT_CHAT={chat}"],
                    ["src/helpers/RadioProfileCLI.cpp"], ["test/fixtures/radio_profiles/mocks"])

    def test_actual_sx1262_and_sx1276_syncword_selection(self):
        for chip in ("SX1262", "SX1276"):
            source = (ROOT / f"src/helpers/radiolib/Custom{chip}Wrapper.h").read_text()
            protocol = method(source, "bool applyProtocol(").replace(" override", "")
            code = r'''
#include <helpers/MeshtextConfig.h>
#include <RadioProfiles.h>
#include <cassert>
#define RADIOLIB_ERR_NONE 0
struct Custom@CHIP@ {
  unsigned writes=0;uint8_t sync=0;
  int result=0;
  int setProfileProtocol(uint8_t value) { ++writes;sync=value;return result; }
};
struct Driver {
  Custom@CHIP@ chip;Custom@CHIP@* _radio=&chip;
  bool supportsMeshtext() const { return MESH_ENABLE_MESHTEXT_WIRE; }
  bool supportsEthernetTunnel() const { return MESH_ENABLE_ETHERNET_TUNNEL; }
  @METHOD@
};
int main() {
  using P=mesh::RadioProfileProtocol;Driver d;
  assert(d.applyProtocol(P::MeshCore) && d.chip.sync==0x12);
#if MESH_ENABLE_ETHERNET_TUNNEL
  assert(d.applyProtocol(P::Ethernet) && d.chip.sync==0x16);
  assert(d.applyProtocol(P::MeshCore) && d.chip.sync==0x12);
#else
  const auto before_eth=d.chip.writes;assert(!d.applyProtocol(P::Ethernet));assert(d.chip.writes==before_eth);
#endif
#if MESH_ENABLE_MESHTEXT_WIRE
  assert(d.applyProtocol(P::Meshtext) && d.chip.sync==0x2b);
#else
  const auto before_mt=d.chip.writes;assert(!d.applyProtocol(P::Meshtext));assert(d.chip.writes==before_mt);
#endif
  const auto before=d.chip.writes;assert(!d.applyProtocol(P(99)));assert(d.chip.writes==before);
  d.chip.result=-1;assert(!d.applyProtocol(P::MeshCore));
}
'''.replace("@CHIP@", chip).replace("@METHOD@", protocol)
            for wire, ethernet in ((0, 1), (1, 1), (1, 0)):
                with self.subTest(chip=chip, wire=wire, ethernet=ethernet):
                    self.compile_run(code, [f"-DMESH_ENABLE_MESHTEXT_WIRE={wire}",
                        f"-DMESH_ENABLE_ETHERNET_TUNNEL={ethernet}"])

    def test_driver_validator_rejects_disabled_or_unsupported_ethernet(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        validator = method(source, "bool RadioLibWrapper::validateProfile(")
        code = r'''
#include <helpers/MeshtextConfig.h>
#include <RadioProfiles.h>
#include <cassert>
struct RadioLibWrapper {
  bool ethernet=true;
  bool supportsMeshtext() const { return true; }
  bool supportsEthernetTunnel() const { return ethernet; }
  bool validateProfile(const mesh::RadioProfileParams&) const;
};
@VALIDATOR@
int main() {
  RadioLibWrapper driver;mesh::RadioProfileParams p;
  p.freq=909.5;p.bw=500;p.sf=8;p.cr=5;p.preamble=32;
  p.protocol=mesh::RadioProfileProtocol::Ethernet;
  assert(driver.validateProfile(p)==bool(MESH_ENABLE_ETHERNET_TUNNEL));
  driver.ethernet=false;assert(!driver.validateProfile(p));
  p.protocol=mesh::RadioProfileProtocol::MeshCore;assert(driver.validateProfile(p));
  p.protocol=mesh::RadioProfileProtocol(99);assert(!driver.validateProfile(p));
}
'''.replace("@VALIDATOR@", validator)
        for enabled in (0, 1):
            self.compile_run(code, [f"-DMESH_ENABLE_ETHERNET_TUNNEL={enabled}", "-DMESH_RADIO_PROFILE_CHIP=1262"])

    def test_ethernet_only_physical_retune_and_failed_protocol_rollback(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        names = [("uint8_t", "beginReconfigure"), ("void", "endReconfigure"),
                 ("void", "serviceProfileScan")] + [("mesh::RadioParamApplyResult", name) for name in
                 ("tuneProfile", "prepareTransmitProfile", "tryRestoreCodingRate", "trySetParams", "trySetPrimaryParams")]
        methods = "\n".join(method(source, f"{kind} RadioLibWrapper::{name}(") for kind, name in names)
        methods += "\n" + method(source, "mesh::RadioParamApplyResult RadioLibWrapper::prepareTransmitProfile(uint8_t profile, bool")
        wrapper = (ROOT / "src/helpers/radiolib/CustomSX1262Wrapper.h").read_text()
        methods += "\n" + method(wrapper, "void setProfileStandbyWarm(").replace(
            "void setProfileStandbyWarm(bool enabled) override",
            "void RadioLibWrapper::setProfileStandbyWarm(bool enabled)")
        harness = "#include <helpers/EthernetTunnelConfig.h>\n" + SCAN_HARNESS.split("@METHODS@")[0]
        harness = harness.replace("uint16_t _physical_preamble=32;", r'''
  uint16_t _physical_preamble=32;
  mesh::RadioProfileProtocol _physical_protocol=mesh::RadioProfileProtocol::MeshCore;
  mesh::RadioProfileProtocol applied_protocol=mesh::RadioProfileProtocol::MeshCore;
  bool fail_protocol=false;unsigned protocol_writes=0;
  bool applyProtocol(mesh::RadioProfileProtocol protocol) {
    ++protocol_writes;
    if (fail_protocol && protocol==mesh::RadioProfileProtocol::Ethernet) return false;
    applied_protocol=protocol;return true;
  }
''')
        harness += methods + r'''
int main() {
  using P=mesh::RadioProfileProtocol;using R=mesh::RadioParamApplyResult;
  { RadioLibWrapper driver;mesh::RadioProfileConfig config;
    config.params=driver._profiles.primary;config.params.protocol=P::Ethernet;
    config.params.preamble=80;config.mode=mesh::RadioProfileMode::RxTx;
    driver._profiles.setSecondary(config,false);
    assert(driver.tuneProfile(1)==R::APPLIED);
    assert(driver._physical_protocol==P::Ethernet && driver.applied_protocol==P::Ethernet);
    assert(driver._physical_preamble==80 && driver.protocol_writes==1);
    assert(driver.tuneProfile(0)==R::APPLIED);
    assert(driver._physical_protocol==P::MeshCore && driver.applied_protocol==P::MeshCore);
    assert(driver.protocol_writes==2);
  }
  { RadioLibWrapper driver;mesh::RadioProfileConfig config;
    config.params=driver._profiles.primary;config.params.protocol=P::Ethernet;
    config.params.preamble=32;config.mode=mesh::RadioProfileMode::RxTx;
    driver._profiles.setSecondary(config,false);driver.fail_protocol=true;
    assert(driver.tuneProfile(1)==R::FAILED);
    assert(driver._physical_protocol==P::MeshCore && driver.applied_protocol==P::MeshCore);
    assert(driver._active_profile==0 && driver._profile_generation==1);
    assert(driver.protocol_writes==2);
  }
}
'''
        for flags in (("-DMESH_ENABLE_ETHERNET_TUNNEL=1",),
                      ("-DMESH_ENABLE_ETHERNET_TUNNEL=0", "-DMESH_ENABLE_ETHERNET_WIRE=1")):
            with self.subTest(flags=flags):
                self.compile_run(harness, [*flags, "-DMESH_ENABLE_MESHTEXT_WIRE=0"], arduino_stub=True)


if __name__ == "__main__":
    unittest.main()
