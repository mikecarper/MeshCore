"""Run the actual bounded Ethernet KISS codec and TCP service with fake sockets."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test" / "fixtures" / "ethernet_kiss"


class EthernetKissTests(unittest.TestCase):
    def test_kiss_port_does_not_collide_with_companion_services(self):
        companion = (ROOT / "examples/companion_radio/main.cpp").read_text(encoding="utf-8")
        server = (ROOT / "src/helpers/esp32/EthernetKissServer.h").read_text(encoding="utf-8")
        terminal = re.search(r"#define\s+OTA_CONSOLE_TCP_PORT\s+(\d+)", companion)
        kiss = re.search(r"static constexpr uint16_t Port\s*=\s*(\d+)", server)
        self.assertIsNotNone(terminal)
        self.assertIsNotNone(kiss)
        self.assertEqual(int(terminal.group(1)), 5002)
        self.assertEqual(int(kiss.group(1)), 5003)
        self.assertNotIn(int(kiss.group(1)), {5000, 5001, int(terminal.group(1))})

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.executable = Path(cls.directory.name) / "ethernet-kiss"
        command = [
            "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-g",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie",
            "-I", str(FIXTURE), "-I", str(ROOT / "src"),
            str(FIXTURE / "test_ethernet_kiss.cpp"), "-o", str(cls.executable),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            cls.directory.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_case(self, name):
        environment = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:halt_on_error=1")
        result = subprocess.run([str(self.executable), name], env=environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_ethernet_length_and_tcp_split(self):
        self.run_case("codec-roundtrip")

    def test_malformed_escapes_ports_short_and_oversized_frames(self):
        self.run_case("codec-malformed")

    def test_queue_backpressure_and_partial_output(self):
        self.run_case("codec-backpressure")

    def test_reset_and_bounded_random_input(self):
        self.run_case("codec-reset-fuzz")

    def test_station_only_binding_single_client_and_disable(self):
        self.run_case("server-station")

    def test_per_poll_budgets_and_tcp_backpressure(self):
        self.run_case("server-budget-backpressure")

    def test_nonblocking_writes_resume_without_duplicate_bytes(self):
        self.run_case("server-partial-writes")

    def test_disconnect_reconnect_address_change_discards_stale_work(self):
        self.run_case("server-session-reset")

    def test_listener_retry_eof_and_socket_errors(self):
        self.run_case("server-failures")


if __name__ == "__main__":
    unittest.main()
