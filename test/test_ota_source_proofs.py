#!/usr/bin/env python3
"""Execute bounded source proofs and real manager ownership under sanitizers."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OtaSourceProofTests(unittest.TestCase):
    def test_actual_policy_preserves_custom_source_capacities(self):
        source = """
#include <helpers/ota/OtaManager.h>
static_assert(OTA_BOUNDED_SOURCE_PROOFS == EXPECT_BOUNDED, "wrong source policy");
#if OTA_BOUNDED_SOURCE_PROOFS
static_assert(sizeof(mesh::ota::SourceMerkleProof<OTA_PROOFGEN_SCRATCH / 4, OTA_LEAVES_FRAG>) <= 1536,
              "bounded source storage exceeds the actual allocation budget");
#endif
#if OTA_PROOFGEN_SCRATCH == 16383
static_assert(OTA_PROOFGEN_SCRATCH / 4 == 4095, "odd byte limit lost usable leaf capacity");
#endif
"""
        base = [os.environ.get("CXX", "g++"), "-std=c++17", "-fsyntax-only", "-x", "c++", "-",
                "-I", str(ROOT / "src")]
        for platform, role in (("ESP32_PLATFORM", "COMPANION_RADIO_FULL"),
                               ("ESP32_PLATFORM", "MESH_CLIENT_REPEATER_ONLY"),
                               ("NRF52_PLATFORM", "MESH_CLIENT_REPEATER_ONLY")):
            for scratch in (None, 0, 1, 3, 4, 5, 16383, 16384, 16385, 32768):
                bounded = scratch is None or 4 <= scratch <= 16384
                flags = [f"-D{platform}=1", f"-D{role}=1"]
                if scratch is not None:
                    flags.append(f"-DOTA_PROOFGEN_SCRATCH={scratch}")
                with self.subTest(platform=platform, role=role, scratch=scratch):
                    result = subprocess.run([*base, *flags, f"-DEXPECT_BOUNDED={int(bounded)}"],
                                            input=source, text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
        # Repeater proof buffers are internal even when the board has PSRAM.
        result = subprocess.run([*base, "-DESP32_PLATFORM=1", "-DMESH_CLIENT_REPEATER_ONLY=1",
                                 "-DBOARD_HAS_PSRAM=1", "-DEXPECT_BOUNDED=1"],
                                input=source, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM"):
            cases = [[], ["-DMESH_CLIENT_REPEATER_ONLY=0"],
                     ["-DCOMPANION_RADIO_FULL=1", "-DBOARD_HAS_PSRAM=1"],
                     ["-DMESH_CLIENT_REPEATER_ONLY=1", "-DOTA_BOUNDED_SOURCE_PROOFS=0"]]
            if platform != "ESP32_PLATFORM":
                cases.append(["-DCOMPANION_RADIO_FULL=1"])
            if platform in ("RP2040_PLATFORM", "STM32_PLATFORM"):
                cases.append(["-DMESH_CLIENT_REPEATER_ONLY=1"])
            for flags in cases:
                with self.subTest(platform=platform, legacy=flags):
                    result = subprocess.run([*base, f"-D{platform}=1", *flags, "-DEXPECT_BOUNDED=0"],
                                            input=source, text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
        for scratch in (0, 3, 16385):
            with self.subTest(forced_invalid=scratch):
                result = subprocess.run([*base, f"-DOTA_PROOFGEN_SCRATCH={scratch}",
                                         "-DOTA_BOUNDED_SOURCE_PROOFS=1", "-DEXPECT_BOUNDED=1"],
                                        input=source, text=True, capture_output=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Bounded source proofs require 4..16384 leaf bytes", result.stderr)

    def test_serial_metadata_stays_one_transaction_across_capacity_changes(self):
        native = (ROOT / "test/test_ota/test_ota_core.cpp").read_text()
        fake = native[native.index("class FakeMotaSeederStream"):
                      native.index("class FakeDeflateMotaSeederStream")]
        code = r'''
#include <cassert>
#include <vector>
#include <helpers/ota/MotaSourceSerial.h>
#include <helpers/ota/MotaSeederProto.h>
#include <helpers/ota/OtaByteIO.h>
using namespace mesh::ota;
// This serial-source capacity test has no competing USB stream owner.
namespace mesh { bool isUsbBinaryStreamOwned() { return false; } }
@FAKE@
class MtuSource : public SerialMotaSource {
public:
  using SerialMotaSource::SerialMotaSource;
  uint16_t frame = 197;
protected:
  uint16_t responseFrameCapacity() const override { return frame; }
};
int main() {
  FakeMotaSeederStream stream;
  MtuSource source(stream, MotaStreamWritePolicy::NoFlush);
  uint8_t bytes[192];
  for (uint16_t frame : {197,182,176,20}) {
    source.frame = frame;
    const auto before = stream.lengths.size();
    const auto quantum = source.read_quantum();
    assert(quantum == frame - 5);
    assert(source.read_metadata(0, 99, bytes, quantum));
    assert(stream.lengths.size() == before + 1 && stream.lengths.back() == quantum);
    for (unsigned i = 0; i < quantum; ++i) assert(bytes[i] == uint8_t(99 + i));
  }
  source.frame = 197;
  const auto old_quantum = source.read_quantum();
  source.frame = 20;
  const auto before = stream.lengths.size();
  assert(!source.read_metadata(0, 0, bytes, old_quantum));
  source.frame = 0;
  assert(!source.read_metadata(0, 0, bytes, 1));
  assert(stream.lengths.size() == before);
}
'''.replace("@FAKE@", fake)
        with tempfile.TemporaryDirectory(prefix="ota-source-quantum-") as folder:
            source, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            source.write_text(code)
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DARDUINO=10819", "-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1",
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"), str(source),
                str(ROOT / "src/helpers/ota/MotaSourceSerial.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_grouped_equivalence_faults_and_manager_lifetimes(self):
        self.check_grouped_equivalence(["-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1"])

    def test_nrf52_full_and_reduced_profiles_keep_maximum_source_and_ownership(self):
        for sensors in (0, 1):
            with self.subTest(full_sensors=bool(sensors)):
                self.check_grouped_equivalence([
                    "-DNRF52_PLATFORM=1", "-DMESH_CLIENT_REPEATER_ONLY=1",
                    f"-DENV_INCLUDE_BME680_BSEC={sensors}",
                ])

    def test_tbeam_full_rooms_preserve_maximum_source_and_bounded_ownership(self):
        for board in ("TBEAM_SX1262", "TBEAM_SX1276"):
            with self.subTest(board=board):
                self.check_grouped_equivalence([
                    "-DESP32_PLATFORM=1", "-D" + board + "=1", "-DBOARD_HAS_PSRAM=1",
                    "-DOTA_BOUNDED_SOURCE_PROOFS=1",
                ])

    def test_tbeam_room_budget_changes_only_actual_source_workspace(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from check_firmware_ram import requirements
        flags = dict(ENABLE_OTA=1, OTA_HEAP_CONTEXT=1, WITH_MQTT_BRIDGE=1,
                     ADMIN_PASSWORD="password", BOARD_HAS_PSRAM=1,
                     DISPLAY_CLASS="SSD1306Display", MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE=1,
                     MESHCORE_OTA_DEVICE_DEFLATE=1)
        for radio in ("SX1262", "SX1276"):
            with self.subTest(radio=radio):
                target = "Tbeam_" + radio + "_room_server_observer_mqtt"
                before = requirements("ESP32_PLATFORM", flags, target)
                after = requirements("ESP32_PLATFORM", dict(flags, OTA_BOUNDED_SOURCE_PROOFS=1), target)
                self.assertEqual(before["required_heap_bytes"], 212784)
                self.assertEqual(after["required_heap_bytes"], 197936)
                self.assertEqual(before["required_contiguous_bytes"], 18464)
                self.assertEqual(after["required_contiguous_bytes"], 16400)
                self.assertEqual(before["components"]["ota_generic_source_buffers"], 18464)
                self.assertEqual(after["components"]["ota_generic_source_buffers"], 3616)
                before_parts = dict(before["components"])
                after_parts = dict(after["components"])
                before_parts.pop("ota_generic_source_buffers")
                after_parts.pop("ota_generic_source_buffers")
                self.assertEqual(before_parts, after_parts)
                self.assertEqual(before["mqtt"], after["mqtt"])
                self.assertEqual(after_parts["allocation_and_transient_margin"], 16384)

    def test_tlora_full_room_preserves_maximum_source_and_bounded_ownership(self):
        self.check_grouped_equivalence([
            "-DESP32_PLATFORM=1", "-DLILYGO_TLORA=1", "-DOTA_BOUNDED_SOURCE_PROOFS=1",
        ])

    def test_wireless_tracker_full_room_preserves_source_proofs(self):
        self.check_grouped_equivalence([
            "-DESP32_PLATFORM=1", "-DHELTEC_LORA_V3=1", "-DOTA_BOUNDED_SOURCE_PROOFS=1",
        ])

    def test_wireless_tracker_room_proof_profile_is_scoped(self):
        script = r'''
set -eu
source build.sh
for full in 0 1; do
  for target in Heltec_Wireless_Tracker_room_server Heltec_Wireless_Tracker_room_server_observer_mqtt Heltec_Wireless_Tracker_repeater; do
    PIO_ENV_FULL_BUILD_BY_NAME[$target]=1
    PIO_ENV_PLATFORM_BY_NAME[$target]=ESP32_PLATFORM
    ESP32_FULL_BUILD=$full
    PLATFORMIO_BUILD_FLAGS=""
    PLATFORMIO_BUILD_UNFLAGS=""
    apply_esp32_full_size_profile "$target"
    expected=0
    if [ "$full" = 1 ] && [[ "$target" == *room_server* ]]; then expected=1; fi
    found=0
    [[ " $PLATFORMIO_BUILD_FLAGS " == *" -DOTA_BOUNDED_SOURCE_PROOFS=1 "* ]] && found=1
    [ "$found" = "$expected" ] || exit 1
    expected=0
    if [ "$full" = 1 ] && [[ "$target" == *repeater* ]]; then expected=1; fi
    found=0
    [[ " $PLATFORMIO_BUILD_FLAGS " == *" -DMESH_TERMINAL_INITIAL_OUTPUT_CAPACITY=2048 "* ]] && found=1
    [ "$found" = "$expected" ] || exit 1
  done
done
'''
        result = subprocess.run(["bash", "-c", script], cwd=ROOT,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tlora_room_budget_changes_only_actual_source_workspace(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from check_firmware_ram import requirements
        flags = dict(ENABLE_OTA=1, OTA_HEAP_CONTEXT=1, WITH_MQTT_BRIDGE=1,
                     ADMIN_PASSWORD="password", MQTT_TASK_STACK_SIZE=16384,
                     DISPLAY_CLASS="SSD1306Display", MESH_ENABLE_ROOM_FLOOD_RULE_ENGINE=1,
                     MESHCORE_OTA_DEVICE_DEFLATE=1)
        target = "LilyGo_TLora_V2_1_1_6_room_server_observer_mqtt_"
        before = requirements("ESP32_PLATFORM", flags, target)
        after = requirements("ESP32_PLATFORM", dict(flags, OTA_BOUNDED_SOURCE_PROOFS=1), target)
        self.assertEqual(before["required_heap_bytes"], 225072)
        self.assertEqual(after["required_heap_bytes"], 210224)
        self.assertEqual(before["required_contiguous_bytes"], 18464)
        self.assertEqual(after["required_contiguous_bytes"], 16400)
        self.assertEqual(before["components"]["ota_generic_source_buffers"], 18464)
        self.assertEqual(after["components"]["ota_generic_source_buffers"], 3616)
        before_parts = dict(before["components"])
        after_parts = dict(after["components"])
        before_parts.pop("ota_generic_source_buffers")
        after_parts.pop("ota_generic_source_buffers")
        self.assertEqual(before_parts, after_parts)
        self.assertEqual(before["mqtt"], after["mqtt"])
        self.assertEqual(after_parts["allocation_and_transient_margin"], 16384)

    def test_tlora_legacy_source_reference_retains_same_maximum_geometry(self):
        # Keep the real old all-leaf cache path as the independent comparison:
        # actual allocation, all Merkle proofs, maximum leaf count and last DATA
        # tail still execute under the exact same ESP32/TLora geometry.
        from test_ota_cold_buffers import HOST_MARKERS
        with tempfile.TemporaryDirectory(prefix="tlora-legacy-proofs-") as folder:
            binary = Path(folder) / "legacy"
            files = ("OtaManager.cpp", "OtaProtocol.cpp", "MotaContainer.cpp", "MerkleTree.cpp")
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-sign-compare", "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie", "-DESP32_PLATFORM=1",
                "-DLILYGO_TLORA=1", "-DOTA_BOUNDED_SOURCE_PROOFS=0",
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"),
                str(ROOT / "test/fixtures/ota_cold_buffers/test.cpp"),
                *[str(ROOT / "src/helpers/ota" / name) for name in files], str(ROOT / "src/Utils.cpp"),
                "-Wl,--wrap=malloc", "-Wl,--wrap=free", "-o", str(binary),
            ], capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=90)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            for marker in HOST_MARKERS:
                self.assertIn("PASS: " + marker, ran.stdout)

    def check_grouped_equivalence(self, flags):
        # Reuse the existing source/wire/allocator harness, retaining its legacy
        # tests unchanged in their own suite. Only its helpers precede this point.
        prefix = (ROOT / "test/fixtures/ota_cold_buffers/test.cpp").read_text().split(
            "static void host_allocation_and_fallbacks()", 1)[0]
        prefix = prefix.replace("(data[0] == OTA_MANIFEST && !sent.accept_manifest)",
                                "((data[0] == OTA_MANIFEST || data[0] == OTA_LEAVES) && !sent.accept_manifest)")
        code = prefix + (ROOT / "test/fixtures/ota_source_proofs/test.cpp").read_text()
        with tempfile.TemporaryDirectory(prefix="ota-source-proofs-") as folder:
            source, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            source.write_text(code)
            files = ("OtaManager.cpp", "OtaProtocol.cpp", "MotaContainer.cpp", "MerkleTree.cpp")
            result = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-sign-compare",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                *flags,
                "-I", str(ROOT / "src"), "-I", str(ROOT / "test/mocks"), str(source),
                *[str(ROOT / "src/helpers/ota" / name) for name in files], str(ROOT / "src/Utils.cpp"),
                "-Wl,--wrap=malloc", "-Wl,--wrap=free", "-o", str(binary),
            ], capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            for marker in ("byte-identical grouped proofs", "partial reads and source changes",
                           "bounded manager ownership and backpressure", "simultaneous capture and serving"):
                self.assertIn("PASS: " + marker, ran.stdout)
            print(ran.stdout, flush=True)


if __name__ == "__main__":
    unittest.main()
