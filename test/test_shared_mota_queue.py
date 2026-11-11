#!/usr/bin/env python3
"""Run the actual shared OtaContext and byte-exact mOTA transfers under sanitizers."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]


class SharedMotaQueueTest(unittest.TestCase):
    def test_context_lifecycle_and_real_transfers_preserve_unread_messages(self):
        self.run_transfer_test("NRF52_PLATFORM")

    def test_wireless_paper_wifi_listener_and_transfers_share_only_when_needed(self):
        self.run_transfer_test("ESP32_PLATFORM")

    def test_c3_rtc_retained_queue_preserves_real_ota_and_wifi_transfers(self):
        self.run_transfer_test("ESP32_PLATFORM", rtc="ESP32C3")

    def test_s3_both_rtc_banks_preserve_real_ota_and_wifi_transfers(self):
        self.run_transfer_test("ESP32_PLATFORM", rtc="ESP32S3")

    def test_preserve_policy_refuses_full_queue_until_app_sync(self):
        self.run_transfer_test("ESP32_PLATFORM", preserve_unread=True)

    def run_transfer_test(self, platform, rtc=None, preserve_unread=False):
        with tempfile.TemporaryDirectory(prefix="meshcore-mota-queue-") as temp:
            binary = Path(temp) / "transfer"
            tinf = Path(temp) / "tinf.o"
            control = method((ROOT / "examples/companion_radio/main.cpp").read_text(),
                             "class Nrf52BleMotaSourceControl")
            (Path(temp) / "ble_control_under_test.h").write_text(control + ";\n")
            (Path(temp) / "temp_radio_under_test.h").write_text(method(
                (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(),
                "bool MyMesh::scheduleTempRadio("))
            queue_init = method((ROOT / "examples/companion_radio/MyMesh.cpp").read_text(),
                                "void MyMesh::initializeOfflineQueue()")
            (Path(temp) / "queue_init_under_test.h").write_text(
                queue_init.replace("MyMesh::initializeOfflineQueue", "Queue::initializeOfflineQueue"))
            subprocess.run([
                "cc", "-DENABLE_OTA=1", "-fsanitize=address,undefined", "-g",
                "-c", str(ROOT / "src/helpers/ota/OtaTinf.c"), "-o", str(tinf),
            ], check=True)
            sources = [
                "test/fixtures/shared_mota_queue/test_shared_mota_queue.cpp",
                "src/helpers/ota/OtaContext.cpp", "src/helpers/ota/OtaManager.cpp",
                "src/helpers/ota/OtaProtocol.cpp", "src/helpers/ota/MotaContainer.cpp",
                "src/helpers/ota/MerkleTree.cpp", "src/helpers/ota/OtaDeflate.cpp",
                "src/Utils.cpp",
            ]
            flags = ["-D" + platform + "=1"]
            if preserve_unread:
                flags.append("-DOTA_PRESERVE_UNREAD_ON_BORROW=1")
            if platform == "ESP32_PLATFORM":
                flags += ["-DHELTEC_WIRELESS_PAPER=1", "-DWIFI_OTA_SEEDER=1"]
                sources += ["src/helpers/esp32/WiFiOtaSeeder.cpp",
                            "src/helpers/ota/MotaSourceSerial.cpp",
                            "src/helpers/ota/FolderMotaStore.cpp"]
            if rtc:
                capacity = 224 if rtc == "ESP32C3" else 256
                slow = 0 if rtc == "ESP32C3" else 32
                fixture = ROOT / "test/fixtures/shared_mota_queue"
                source = (fixture / "test_shared_mota_queue.cpp").read_text()
                source = source[:source.index("  Queue full;")] + "}\n"
                source = source.replace("256", str(capacity))
                source = source.replace("struct Queue {", (
                    "#include <helpers/CompanionRtcQueue.h>\n"
                    "alignas(4) RTC_FAST_ATTR Frame rtc_frames[32];\n"
                    + ("alignas(4) RTC_SLOW_ATTR Frame rtc_slow_frames[32];\n" if slow else "")
                    + "struct Queue {"))
                source = source.replace(
                    f"mesh::BorrowableFrameBuffer<Frame, {capacity}, 128, OtaContext> buffer;",
                    f"mesh::BorrowableFrameBuffer<Frame, {capacity}, 128, OtaContext, "
                    "mesh::CompanionRtcQueue<Frame, 128>> buffer;\n"
                    + "  Queue() : buffer(rtc_frames, "
                    + ("rtc_slow_frames" if slow else "nullptr") + ") {}")
                (Path(temp) / "test_shared_rtc.cpp").write_text(source)
                (Path(temp) / "test_wifi_shared_queue.h").write_text(
                    (fixture / "test_wifi_shared_queue.h").read_text().replace("256", str(capacity)))
                (Path(temp) / "sdkconfig.h").write_text("#pragma once\n")
                (Path(temp) / "esp_attr.h").write_text(
                    '#pragma once\n#define RTC_FAST_ATTR __attribute__((section(".rtc.force_fast.test")))\n'
                    '#define RTC_SLOW_ATTR __attribute__((section(".rtc.force_slow.test")))\n')
                sources[0] = str(Path(temp) / "test_shared_rtc.cpp")
                flags += ["-DCOMPANION_RTC_FAST_OFFLINE_FRAMES=32",
                          f"-DCOMPANION_RTC_SLOW_OFFLINE_FRAMES={slow}",
                          f"-DCONFIG_IDF_TARGET_{rtc}=1",
                          "-DCONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP=1",
                          "-I", str(fixture)]
            built = subprocess.run([
                "c++", "-std=c++17", "-fsanitize=address,undefined", "-g",
                *flags, "-DOTA_SEEDER_ONLY=1",
                "-DCOMPANION_RADIO_FULL=1", "-DOTA_SHARED_COMPANION_QUEUE=1",
                "-DMESH_ENABLE_HOST_KISS=0",
                "-DENABLE_OTA=1", "-I", str(ROOT / "src"),
                "-I", str(ROOT / "test/fixtures/shared_mota_queue/mocks"),
                "-I", str(ROOT / "test/mocks"), "-I", temp,
                *[str(ROOT / source) for source in sources], str(tinf),
                "-o", str(binary),
            ], text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
