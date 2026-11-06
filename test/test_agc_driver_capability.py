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
            (None, 0), ("CustomSX1262", 1), ("CustomSX1268", 0),
            ("CustomSTM32WLx", 0), ("CustomLLCC68", 0), ("CustomSX1276", 0),
            ("CustomLR1110", 0), ("CustomLR1121", 0), ("CustomLR2021", 0),
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
            (["-DRADIO_CLASS=UniversalLoRaRadio", "-DMESH_RADIO_AUTO_FAMILY=1", "-DUSE_LR1121=1"], 0),
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
        for driver, expected in (("CustomSX1262", 1), ("CustomLR2021", 0), ("CustomSX1276", 0)):
            with self.subTest(driver=driver):
                self.compile_run(main, ["-DRADIO_CLASS=" + driver, "-DEXPECT_PULSE=" + str(expected)], other)


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


if __name__ == "__main__":
    unittest.main()
