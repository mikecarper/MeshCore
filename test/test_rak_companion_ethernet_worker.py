"""Run the actual RAK Companion driver, framing and routes against a fake RTOS."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_serial_wifi_sessions import SANITIZER_FLAGS


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/rak_companion_ethernet_worker"
SCENARIOS = (
    "admission and task allocation failures",
    "blocked initial DHCP and cooperative OFF",
    "OFF before worker scheduling",
    "framed short writes, backpressure and routing",
    "blocked renewal preserves session, replies and parser",
    "stalled listener command is bounded and preserves cached session",
    "same-IP replacement replays complete response",
    "different-IP replacement cancels reply ownership",
    "missing TCP acknowledgement",
    "stuck SEND controller command",
    "stuck RECV controller command",
    "SD insertion during DHCP and reacquisition",
    "online SD insertion and pinned release",
    "repeated acquisitions reclaim tasks and restore CS",
    "terminal timeout retains blocked worker",
    "release hook failure retains SPI ownership",
    "missing Ethernet hardware",
    "OFF wakes DHCP retry promptly",
    "occupied OTA owner refuses OFF without cancelling session",
    "failed ACK retains complete response for same-IP reconnect",
)
FLAGS = (
    "NRF52_PLATFORM", "RAK_4631", "OTA_SD_STORE", "OTA_RAK15002_SD_STORE",
    "SPI_INTERFACES_COUNT=1", "PIN_SPI_MISO=29", "PIN_SPI_SCK=3", "PIN_SPI_MOSI=30",
    "WB_IO6=10", "ETHERNET_ENABLED=1", "ETHERNET_RAW_LINE=0",
)


def build_worker(test, folder, production=None, *, adaptive=True, static_ip=False, rak3401=False, whitebox=False):
    compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        test.skipTest("a host C++17 compiler is required")
    driver = ROOT / "src/helpers/ethernet/RAK13800/RAK13800EthernetInterface.cpp"
    binary = Path(folder) / "worker"
    if production is None:
        source = str(driver)
        stdin = None
    else:
        # Keep the production include's relative helper paths intact while
        # compiling a deliberate behavioral mutation from stdin.
        source = "-"
        stdin = production
    flags = list(FLAGS)
    if not adaptive:
        flags = [item for item in flags if item not in ("OTA_SD_STORE", "OTA_RAK15002_SD_STORE")]
    if rak3401:
        flags = [item for item in flags if item != "RAK_4631"] + ["RAK_3401"]
    if static_ip:
        flags.extend(("ETHERNET_STATIC_IP=192,168,1,42", "ETHERNET_STATIC_DNS=8,8,8,8",
                      "ETHERNET_STATIC_GATEWAY=192,168,1,1", "ETHERNET_STATIC_SUBNET=255,255,255,0"))
    access_flags = ["-fno-access-control", "-DMESH_TEST_PRIVATE_ACCESS=1"] if whitebox else []
    result = subprocess.run([
        compiler, "-std=c++17", "-pthread", "-Wall", "-Wextra", "-Werror",
        *SANITIZER_FLAGS, *access_flags, *["-D" + item for item in flags],
        f"-I{FIXTURE / 'mocks'}", f"-I{ROOT / 'src'}", f"-I{driver.parent}",
        str(FIXTURE / "test.cpp"),
        str(ROOT / "src/helpers/ethernet/SerialEthernetInterface.cpp"),
        "-x", "c++", source, "-o", str(binary),
    ], input=stdin, text=True, capture_output=True, timeout=60)
    test.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    return binary


class RakCompanionEthernetWorkerTests(unittest.TestCase):
    def test_active_worker_publication_gaps_refuse_restart(self):
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-ethernet-publication-") as folder:
            # Access control is relaxed only to place the actual atomic fields
            # in the two publication windows while the fake task is held at
            # its real entry barrier. Production methods and layout are intact.
            binary = build_worker(self, folder, whitebox=True)
            result = subprocess.run([str(binary), "34"], text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    def test_dedicated_ethernet_boot_and_static_ip(self):
        for adaptive, static_ip, scenario in ((False, False, 20), (False, True, 20), (True, True, 3)):
            with self.subTest(adaptive=adaptive, static_ip=static_ip), tempfile.TemporaryDirectory(
                    prefix="meshcore-companion-ethernet-legacy-") as folder:
                binary = build_worker(self, folder, adaptive=adaptive, static_ip=static_ip)
                result = subprocess.run([str(binary), str(scenario)], text=True,
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"PASS: RAK Companion Ethernet worker scenario {scenario}", result.stdout)

    def test_actual_companion_worker_with_framing_and_routes(self):
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-ethernet-") as folder:
            binary = build_worker(self, folder)
            for scenario, description in enumerate(SCENARIOS):
                with self.subTest(scenario=description):
                    result = subprocess.run([str(binary), str(scenario)], text=True,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(f"PASS: RAK Companion Ethernet worker scenario {scenario}", result.stdout)
            for scenario, description in ((21, "missing hooks"), (28, "wrong OPEN state"),
                    (29, "fast SYNRECV"), (30, "wrong LISTEN state"),
                    (31, "closing socket fallback"), (32, "wrong CLOSE state"),
                    (33, "direct restart cancels stopped-fault session"),
                    (35, "unACKed head survives priority insertion and full queue"),
                    (36, "priority queue survives unACKed head reconnect"),
                    (37, "ACKed but unretired head remains pinned")):
                with self.subTest(scenario=description):
                    result = subprocess.run([str(binary), str(scenario)], text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_synchronous_start_and_busy_disconnect_negative_controls(self):
        driver = ROOT / "src/helpers/ethernet/RAK13800/RAK13800EthernetInterface.cpp"
        source = driver.read_text()
        startup = extract_braced(source, "bool RAK13800EthernetInterface::beginHardware()")
        statement = ("  const BaseType_t created = xTaskCreate(workerTask, \"eth_comp\", RAK4631_ETHERNET_TASK_STACK_WORDS,\n"
                     "                                       this, 1, &_task);")
        self.assertEqual(startup.count(statement), 1)
        synchronous = source.replace(startup, startup.replace(statement,
            "  const BaseType_t created = (workerTask(this), pdPASS);"))
        connected = extract_braced(source, "bool RAK13800EthernetInterface::isConnected() const")
        statement = "return isEnabled() && _isConnected && !_storageFault.load();"
        self.assertEqual(connected.count(statement), 1)
        false_eof = source.replace(connected, connected.replace(statement,
            "return isEnabled() && _isConnected && !_storageFault.load() && _access.load() == Access::Idle;"))
        commands = extract_braced(source, "bool RAK13800EthernetInterface::commandsIdle()")
        status_guard = extract_braced(commands, "if (idle && _listenerCommand)")
        unchecked_status = source.replace(commands, commands.replace(status_guard, "_listenerCommand = 0;"))
        for name, mutation, scenario, error in (
                ("synchronous startup", synchronous, 1, "SPI startup entered the mesh thread"),
                ("busy false EOF", false_eof, 4, "f.owner.isConnected()"),
                ("unchecked listener state", unchecked_status, 28, "wrong listener status must fault once")):
            with self.subTest(mutation=name), tempfile.TemporaryDirectory(
                    prefix="meshcore-companion-ethernet-negative-") as folder:
                binary = build_worker(self, folder, mutation)
                result = subprocess.run([str(binary), str(scenario)], text=True,
                                        capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)


if __name__ == "__main__":
    unittest.main()
