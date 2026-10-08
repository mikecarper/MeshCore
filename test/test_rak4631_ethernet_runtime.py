#!/usr/bin/env python3
"""Exercise combined Ethernet on/off, SPI ownership and retained Stream safety."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/rak4631_ethernet_runtime"
SCENARIOS = (
    "disabled boot and exact command parsing",
    "flash conflict or active OTA refuses initialization",
    "task allocation failure releases SPI",
    "OFF before worker scheduling avoids hardware IO",
    "OFF during slow initial DHCP waits for cooperative cleanup",
    "OFF wakes DHCP retry promptly",
    "missing Ethernet hardware reaps worker before returning SPI",
    "online framing, short writes and real remote disconnect",
    "slow DHCP renewal gates retained Stream without blocking mesh",
    "renewal failure returns to service without restarting worker",
    "active or staged OTA refuses OFF",
    "remote OFF reply precedes socket cleanup and synchronous task reclamation",
    "OTA race retains SPI owner while main-thread release retries",
    "optional setting-save failure is transactional",
    "repeated hot switches reclaim workers without stale notifications",
    "saved-ON boot does not save twice",
    "OFF during slow renewal never releases SPI early",
    "bounded ring queue retains order and prevents command dispatch under backpressure",
    "oversized line discards the whole command prefix",
    "embedded NUL discards the whole record",
    "missing TCP acknowledgement holds the SPI gate without blocking mesh",
    "stuck SEND command faults without vendor RX or accept",
    "remote OFF acknowledgement is queued and missing ACK shutdown is bounded",
    "fragmented oversized record discard is bounded between loop turns",
    "newly stuck RECV command yields without a vendor command spin",
    "delayed terminal RECV retains its synchronous response",
    "new peer cannot receive previous peer synchronous or retained async output",
    "partial record from a replaced peer cannot concatenate with new input",
    "concurrent worker replacement cannot steal a record's response owner",
)


class Rak4631EthernetRuntimeTests(unittest.TestCase):
    def test_production_runtime_with_background_dhcp_and_spi_gate(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="meshcore-rak4631-ethernet-runtime-") as directory:
            binary = Path(directory) / "ethernet-runtime"
            compiled = subprocess.run(
                [compiler, "-std=c++17", "-pthread", "-Wall", "-Wextra", "-Werror",
                 *SANITIZER_FLAGS, "-DETHERNET_ENABLED=1", "-DRAK4631_COMBINED_ETHERNET=1",
                 f"-I{FIXTURE / 'mocks'}", f"-I{ROOT / 'src'}",
                 str(FIXTURE / "test.cpp"), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            for scenario, description in enumerate(SCENARIOS):
                with self.subTest(scenario=description):
                    checked = subprocess.run([str(binary), str(scenario)],
                                             capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn(f"PASS: RAK4631 Ethernet runtime scenario {scenario}", checked.stdout)


if __name__ == "__main__":
    unittest.main()
