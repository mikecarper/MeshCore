#!/usr/bin/env python3
"""Compile default AGC capability, shared ABI and real ESP32/nRF52 pulse paths."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import test_rxps_pulse_integration as pulse

ROOT = Path(__file__).resolve().parents[1]


class DriverCapabilityTest(unittest.TestCase):
    def compile_run(self, source, flags=(), extra_source=None):
        with tempfile.TemporaryDirectory(prefix="agc-driver-capability-") as temp:
            folder = Path(temp)
            cpp, binary = folder / "main.cpp", folder / "main"
            cpp.write_text(source, encoding="ascii")
            sources = [str(cpp)]
            if extra_source is not None:
                extra = folder / "other.cpp"
                extra.write_text(extra_source, encoding="ascii")
                sources.append(str(extra))
            built = subprocess.run([
                os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-reorder", "-Wno-sign-compare",
                *flags, "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                *sources, "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_concrete_driver_selects_default_before_any_optional_variant_define(self):
        code = """#include <helpers/RadioAGCConfig.h>
static_assert(MESH_WARM_RX_REFRESH == EXPECT_PULSE, "AGC driver capability");
int main() {}
"""
        for driver, expected in (
            (None, 0), ("CustomSX1262", 1), ("CustomSX1268", 1),
            ("CustomSTM32WLx", 1), ("CustomLLCC68", 1), ("CustomSX1276", 1),
            ("CustomLR1110", 1), ("CustomLR1121", 1), ("CustomLR2021", 1),
            ("UnknownRadio", 0), ("UniversalLoRaRadio", 0),
        ):
            with self.subTest(driver=driver):
                flags = ["-DEXPECT_PULSE=" + str(expected)]
                if driver:
                    flags.append("-DRADIO_CLASS=" + driver)
                # Some variants advertise optional SX1262 hardware even when
                # another concrete driver is selected. That is not capability.
                flags.append("-DUSE_SX1262=1")
                self.compile_run(code, flags)

    def test_explicit_optout_and_universal_runtime_capability_contract(self):
        code = """#include <helpers/RadioAGCConfig.h>
static_assert(MESH_WARM_RX_REFRESH == EXPECT_PULSE, "Explicit/default AGC capability");
int main() {}
"""
        for flags, expected in (
            (["-DRADIO_CLASS=CustomSX1262", "-DMESH_WARM_RX_REFRESH=0"], 0),
            (["-DRADIO_CLASS=CustomLR2021", "-DMESH_WARM_RX_REFRESH=1"], 1),
            (["-DRADIO_CLASS=UniversalLoRaRadio", "-DMESH_RADIO_AUTO_FAMILY=1", "-DUSE_SX1262=1"], 1),
            (["-DRADIO_CLASS=UniversalLoRaRadio", "-DMESH_RADIO_AUTO_FAMILY=0", "-DUSE_SX1262=1"], 0),
            (["-DRADIO_CLASS=UniversalLoRaRadio", "-DMESH_RADIO_AUTO_FAMILY=1", "-DUSE_LR1121=1"], 1),
            (["-DRADIO_CLASS=UniversalLoRaRadio", "-DMESH_RADIO_AUTO_FAMILY=1", "-DUSE_LR2021=1"], 1),
            (["-DRADIO_CLASS=UniversalLoRaRadio", "-DMESH_RADIO_AUTO_FAMILY=1", "-DUSE_SX1276=1"], 1),
            (["-DRADIO_CLASS=CustomLR2021", "-DMESH_WARM_RX_REFRESH=0"], 0),
            (["-DRADIO_CLASS=CustomSX1276", "-DMESH_WARM_RX_REFRESH=0"], 0),
        ):
            with self.subTest(flags=flags):
                self.compile_run(code, [*flags, "-DEXPECT_PULSE=" + str(expected)])

    def test_dispatcher_and_driver_include_orders_resolve_one_interface(self):
        main = """#include <Dispatcher.h>
#include <helpers/radiolib/WarmRxRefresh.h>
#include <cassert>
#include <type_traits>
extern unsigned other_capability();
extern unsigned other_size();
static_assert(MESH_WARM_RX_REFRESH == EXPECT_PULSE, "Dispatcher must see capability first");
static_assert(std::is_same<decltype(&mesh::Radio::serviceAGCMaintenanceFastLane), bool (mesh::Radio::*)()>::value,
              "Same maintenance API in both translation units");
int main() { assert(other_capability() == MESH_WARM_RX_REFRESH); assert(other_size() == sizeof(mesh::Radio)); }
"""
        other = """#include <helpers/radiolib/WarmRxRefresh.h>
#include <Dispatcher.h>
unsigned other_capability() { return MESH_WARM_RX_REFRESH; }
unsigned other_size() { return sizeof(mesh::Radio); }
"""
        for driver, expected in (("CustomSX1262", 1), ("CustomSTM32WLx", 1), ("CustomLR2021", 1), ("CustomSX1276", 1)):
            with self.subTest(driver=driver):
                self.compile_run(main, ["-DRADIO_CLASS=" + driver, "-DEXPECT_PULSE=" + str(expected)], other)

    def test_compact_stm32_uses_virtual_pulse_api_and_keeps_old_timer_optional(self):
        source = """#include <Dispatcher.h>
static_assert(MESH_WARM_RX_REFRESH == 1, "Real STM32WL pulse driver");
static_assert(MESH_COMPACT_STM32_RADIO == 1, "Compact storage policy retained");
static_assert(MESH_ENABLE_OLD_AGC == 0, "Independent legacy timer stays optional");
struct PulseContract : mesh::Radio {
  bool supportsWarmRxRefresh() const override { return true; }
  bool serviceAGCMaintenanceFastLane() override { return false; }
  mesh::AGCMaintenanceResult periodicAGCMaintenance(bool) override { return mesh::AGCMaintenanceResult::Deferred; }
};
int main() {}
"""
        for flag in ("WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC", "STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC"):
            with self.subTest(flag=flag):
                self.compile_run(source, ["-DRADIO_CLASS=CustomSTM32WLx", "-D" + flag + "=1"])

    def test_compact_warm_maintenance_uses_actual_retry_contract(self):
        dispatcher = (ROOT / "src/Dispatcher.cpp").read_text()
        start = dispatcher.index("#if (", dispatcher.index("const uint32_t deferred_at = _ms->getMillis();"))
        # Extract the backend/owner delay selection only. The following warm
        # deadline application belongs to the actual Dispatcher regressions.
        end = dispatcher.index("#if defined(MESH_WARM_RX_REFRESH)",
                               dispatcher.index("receive_delay > retry_delay", start))
        retry = dispatcher[start:end]
        code = """#include <Dispatcher.h>
#include <cassert>
struct FakeRadio {
  uint32_t maintenance=0, receive=0;
  unsigned queries=0;
  uint32_t agcMaintenanceRetryMs() { ++queries;return maintenance; }
  bool getReceiveRetryDelay(uint32_t, uint32_t& delay) { delay=receive;return receive!=0; }
};
uint32_t actual_retry(FakeRadio& radio,bool old_agc_serviced,bool adaptive) {
  FakeRadio* _radio=&radio;const uint32_t deferred_at=10;
  (void)_radio;
""" + retry + """
  return retry_delay;
}
int main() {
  FakeRadio radio;
  // A fixed scanner retry follows its next primary visit; a regular owned
  // or unavailable window keeps the backend's slower retry policy.
  for(uint32_t maintenance : {1U,250U}) {
    radio.maintenance=maintenance;
#if MESH_WARM_RX_REFRESH || !MESH_COMPACT_STM32_RADIO
    assert(actual_retry(radio,false,false)==maintenance);
#else
    assert(actual_retry(radio,false,false)==50);
#endif
    assert(actual_retry(radio,true,false)==250);
    radio.receive=500;
    assert(actual_retry(radio,false,true)==500);
    radio.receive=0;
  }
#if MESH_WARM_RX_REFRESH || !MESH_COMPACT_STM32_RADIO
  assert(radio.queries==4);
#else
  assert(radio.queries==0);
#endif
}
"""
        code = code.replace("#include <cassert>", "#include <cassert>\n#include <initializer_list>")
        for compact in (None, "WIO_E5_MINI_COMPACT_UNSUPPORTED_AGC", "STM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC"):
            for warm in (0,1):
                with self.subTest(compact=compact,warm=warm):
                    flags=["-DMESH_WARM_RX_REFRESH="+str(warm)]
                    if compact: flags.append("-D"+compact+"=1")
                    self.compile_run(code,flags)

    def test_compact_companion_dispatches_actual_pulse_getters(self):
        radio = """#pragma once
#include <cstdint>
#include <cstdio>
#include <cstddef>
namespace mesh { struct Radio {
  mutable unsigned calls=0;
  bool supportsWarmRxRefresh() const { return true; }
  bool supportsOldAGC() const { return false; }
  uint32_t getOldAGCInterval() const { return 0; }
  bool setOldAGCInterval(uint32_t) { return false; }
  bool formatWarmRxRefresh(uint8_t page,uint32_t seconds,char* out,size_t cap) const {
    ++calls;snprintf(out,cap,"> page=%u,seconds=%u,ok=2,cycles=2",unsigned(page),unsigned(seconds));return true;
  }
}; }
"""
        source = """#include <helpers/RadioAGCConfig.h>
#include <helpers/AGCMaintenanceCLI.h>
#include <cassert>
#include <cstring>
int main() {
  mesh::Radio r;char reply[160];
  const char* getters[]={"get agc.status","get agc.timing","get agc.recovery","get agc.pulse","get agc.sleep","get agc.checks","get agc.scan"};
  for(unsigned page=0;page<7;++page) {
    assert(mesh::handleAGCMaintenanceCommand(&r,getters[page],16,reply,sizeof(reply)));
    char expected[24];snprintf(expected,sizeof(expected),"> page=%u,",page);
    assert(!strncmp(reply,expected,strlen(expected)) && strstr(reply,"ok=2,cycles=2"));
  }
  assert(r.calls==7);
  assert(mesh::handleAGCMaintenanceCommand(&r,"get agc.old",16,reply,sizeof(reply)));
  assert(!strcmp(reply,"Error: unsupported") && r.calls==7);
}
"""
        with tempfile.TemporaryDirectory(prefix="compact-pulse-cli-") as temp:
            folder=Path(temp);(folder/"Dispatcher.h").write_text(radio,encoding="ascii")
            self.compile_run(source, ["-I"+str(folder), "-DRADIO_CLASS=CustomSTM32WLx",
                                     "-DSTM32WL_COMPANION_COMPACT_UNSUPPORTED_AGC=1"])


class DefaultDriverPulseTest(pulse.PulseIntegrationTest):
    """Reuse real driver command/failure tests with no per-board pulse opt-in."""
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        if not compiler:
            raise unittest.SkipTest("A C++ compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="default-driver-pulse-")
        cls.addClassCleanup(cls.temporary.cleanup)
        folder = Path(cls.temporary.name)
        source = folder / "test.cpp"
        # Remove the fixture's explicit default: compilation must select the
        # real production capability before it sees any driver/wrapper code.
        fixture = pulse.harness().replace(
            "#ifndef MESH_WARM_RX_REFRESH\n#define MESH_WARM_RX_REFRESH 1\n#endif\n", "")
        source.write_text("#include <helpers/RadioAGCConfig.h>\n"
                          "static_assert(MESH_WARM_RX_REFRESH == 1, \"Default SX1262 pulse\");\n" + fixture,
                          encoding="ascii")
        cls.binaries = []
        for platform in ("esp32", "nrf52"):
            for fs in (0, 1):
                binary = folder / (platform + "-fs" + str(fs))
                compiled = subprocess.run([
                    compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                    "-fsanitize=address,undefined,float-cast-overflow", "-fno-omit-frame-pointer",
                    "-fno-pie", "-no-pie", "-DRADIO_CLASS=CustomSX1262", "-DTEST_FS=" + str(fs),
                    *(["-DMESH_TEST_PULSE_NRF52=1"] if platform == "nrf52" else []),
                    "-I", str(pulse.FIXTURE), "-I", str(ROOT / "src"), str(source), "-o", str(binary),
                ], capture_output=True, text=True, timeout=60)
                if compiled.returncode:
                    raise AssertionError(compiled.stderr)
                cls.binaries.append(binary)


class UncachedFamilyPulseTest(pulse.PulseIntegrationTest):
    """Run the same real command/fault tests on the new uncached driver ops."""
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        if not compiler:
            raise unittest.SkipTest("A C++ compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="sx126x-uncached-pulse-")
        cls.addClassCleanup(cls.temporary.cleanup)
        folder=Path(cls.temporary.name);source=folder/"test.cpp"
        fixture=pulse.harness(uncached=True).replace(
            "#ifndef MESH_WARM_RX_REFRESH\n#define MESH_WARM_RX_REFRESH 1\n#endif\n", "")
        source.write_text("#include <helpers/RadioAGCConfig.h>\n"
                          "static_assert(MESH_WARM_RX_REFRESH == 1, \"Default family pulse\");\n"+fixture,
                          encoding="ascii")
        cls.binaries=[]
        for driver in ("CustomSX1268","CustomLLCC68","CustomSTM32WLx"):
            for platform in ("esp32","nrf52"):
                binary=folder/(driver+"-"+platform)
                built=subprocess.run([
                    compiler,"-std=c++11","-Wall","-Wextra","-Werror","-pedantic",
                    "-fsanitize=address,undefined,float-cast-overflow","-fno-omit-frame-pointer",
                    "-fno-pie","-no-pie","-DRADIO_CLASS="+driver,"-DTEST_FS=0",
                    "-DMESH_TEST_PULSE_UNCACHED=1",
                    *(["-DMESH_TEST_PULSE_NRF52=1"] if platform=="nrf52" else []),
                    "-I",str(pulse.FIXTURE),"-I",str(ROOT/"src"),str(source),"-o",str(binary),
                ],capture_output=True,text=True,timeout=60)
                if built.returncode:
                    raise AssertionError(built.stderr)
                cls.binaries.append(binary)


if __name__ == "__main__":
    unittest.main()
