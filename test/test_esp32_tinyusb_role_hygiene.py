#!/usr/bin/env python3
"""Keep ESP32 native-USB role output away from blocking Arduino writes."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
ROLES = ("simple_repeater", "simple_room_server")


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


class Esp32TinyUsbRoleHygieneTest(unittest.TestCase):
    def test_console_helper_preserves_other_platform_semantics(self):
        text = source("src/helpers/UsbLogging.cpp")
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++ compiler is required")
        selector = body(text, "static Stream& rawUsbConsolePort()")
        flags = ("MESH_PRIMARY_SERIAL_IS_UART", "MESH_ESP32_TINYUSB_NONBLOCKING",
                 "MESH_ESP32_HWCDC_SESSION_GUARD", "MESH_NRF52_USB_CONSOLE_COOPERATIVE")
        # Execute the production selection, including the UART bridge which
        # has no native CDC/DTR. Sentinel identity catches a wrong transport.
        cases = (
            ("uart_bridge", (1, 0, 0, 0), "buffered_primary_uart_terminal_port"),
            ("tinyusb", (0, 1, 0, 0), "buffered_esp32_tinyusb_terminal_port"),
            ("hwcdc", (0, 0, 1, 0), "guarded_esp32_hwcdc_port"),
            ("nrf52_usb", (0, 0, 0, 1), "buffered_primary_usb_terminal_port"),
            ("other_serial", (0, 0, 0, 0), "Serial"),
            ("uart_priority", (1, 1, 1, 1), "buffered_primary_uart_terminal_port"),
        )
        harness = ["#include <cassert>", "struct Stream {};",
                   "Stream Serial, buffered_primary_uart_terminal_port,",
                   "buffered_esp32_tinyusb_terminal_port, guarded_esp32_hwcdc_port,",
                   "buffered_primary_usb_terminal_port;"]
        for name, values, expected in cases:
            harness.extend(f"#define {flag} {value}" for flag, value in zip(flags, values))
            harness.extend((f"namespace {name} {{",
                            selector,
                            f"void check() {{ assert(&rawUsbConsolePort() == &{expected}); }}",
                            "}"))
            harness.extend(f"#undef {flag}" for flag in flags)
        harness.append("int main() {" + "".join(name + "::check();" for name, _, _ in cases) + "}")
        with tempfile.TemporaryDirectory(prefix="usb-console-selection-") as temporary:
            cpp, binary = Path(temporary) / "selection.cpp", Path(temporary) / "selection"
            cpp.write_text("\n".join(harness))
            sanitizers = ([] if os.name == "nt" else
                          ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"])
            built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                    *sanitizers, str(cpp), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        # The shared binary-owner facade wraps the same transport selection.
        # With host KISS omitted, every platform still gets its original port.
        facade = body(text, "Stream& usbConsolePort()")
        self.assertIn("guarded.use(rawUsbConsolePort());", facade)
        self.assertRegex(facade, r"#else\s+return rawUsbConsolePort\(\);\s+#endif")

    def test_role_output_never_bypasses_the_usb_facade(self):
        # A capacity check outside Serial.write() cannot make Arduino's
        # native USBCDC writer bounded. Cover direct writes and Print/Stream
        # references, including printHex's less obvious indirect writes.
        for role in ROLES:
            for filename in ("main.cpp", "MyMesh.cpp", "UITask.cpp"):
                relative = f"examples/{role}/{filename}"
                with self.subTest(path=relative):
                    text = source(relative)
                    self.assertNotRegex(
                        text,
                        r"\bSerial\s*\.\s*(?:print|println|printf|write|flush)\s*\(",
                    )
                    self.assertNotRegex(text, r"\bprintHex\s*\(\s*Serial\b")
                    self.assertNotRegex(
                        text,
                        r"\b(?:Print|Stream)\s*&\s*\w+\s*=\s*Serial\b",
                    )
                    self.assertIn("mesh::usbConsolePort()", text)

    def test_functional_replies_do_not_use_the_logging_gate(self):
        for role in ROLES:
            with self.subTest(role=role):
                text = source(f"examples/{role}/MyMesh.cpp")
                self.assertIn('mesh::usbConsolePort().printf("ACL:\\r\\n");', text)
                self.assertIn(
                    'mesh::usbConsolePort().printf("%02X %s\\n", '
                    "c->permissions, public_key);",
                    text,
                )
                self.assertIn(
                    'mesh::usbConsolePort().printf("OTA: starting update\\r\\n");',
                    text,
                )
                self.assertIn('mesh::usbConsolePort().printf("%s\\r\\n", wc_reply);', text)
                start = text.index("void MyMesh::dumpLogFile()")
                end = text.index("bool MyMesh::setTxPower(", start)
                dump = text[start:end]
                self.assertIn("mesh::usbConsolePort().print((char)c);", dump)
                self.assertNotIn("isUsbLoggingEnabled", dump)
                self.assertNotIn("usbLoggingPort", dump)

        repeater = source("examples/simple_repeater/MyMesh.cpp")
        self.assertIn(
            'mesh::usbConsolePort().printf("Recent repeaters (%d):\\n", count);',
            repeater,
        )
        self.assertIn('mesh::usbConsolePort().printf("%s\\r\\n", record);', repeater)

    def test_unconditional_display_diagnostics_keep_their_visibility(self):
        for role in ROLES:
            with self.subTest(role=role):
                text = source(f"examples/{role}/UITask.cpp")
                self.assertIn("#include <helpers/UsbLogging.h>", text)
                self.assertIn("// Logged unconditionally:", text)
                self.assertIn(
                    'mesh::usbConsolePort().printf("Display: flip %s\\n",',
                    text,
                )
                self.assertIn(
                    'mesh::usbConsolePort().printf("Display: %s -> %s\\n",',
                    text,
                )
                self.assertIn(
                    'mesh::usbConsolePort().printf("Powering Off\\r\\n");', text
                )

    def test_both_roles_initialize_and_service_queued_terminal_output(self):
        for role in ROLES:
            with self.subTest(role=role):
                text = source(f"examples/{role}/main.cpp")
                setup = text[text.index("void setup()") : text.index("void loop()")]
                loop = text[text.index("void loop()") :]
                self.assertLess(
                    setup.index("mesh::prepareUsbLoggingPort();"),
                    setup.index("Serial.begin(115200);"),
                )
                self.assertIn("mesh::beginUsbLoggingPort();", setup)
                self.assertIn("mesh::serviceUsbTerminalPort();", loop)

    def test_sensor_uses_the_same_guarded_console_lifecycle(self):
        main = source("examples/simple_sensor/main.cpp")
        sensor = source("examples/simple_sensor/SensorMesh.cpp")
        setup = main[main.index("void setup()") : main.index("void loop()")]
        loop = main[main.index("void loop()") :]
        self.assertLess(
            setup.index("mesh::prepareUsbLoggingPort();"),
            setup.index("Serial.begin(115200);"),
        )
        self.assertIn("mesh::beginUsbLoggingPort();", setup)
        self.assertIn("mesh::takeUsbTerminalSessionReset()", loop)
        self.assertIn("mesh::tryCompleteUsbTerminalSessionReset()", loop)
        self.assertIn("mesh::canAcceptUsbConsoleCommand()", loop)
        for text in (main, sensor):
            self.assertNotRegex(
                text,
                r"\bSerial\s*\.\s*(?:print|println|printf|write|flush|available|read|peek)\s*\(",
            )
            self.assertNotRegex(text, r"\bprintHex\s*\(\s*Serial\b")
        self.assertIn("mesh::usbConsolePort()", main)
        self.assertIn("mesh::usbConsolePort()", sensor)

    def test_hwcdc_guard_is_not_limited_to_companion_builds(self):
        header = source("src/helpers/UsbLogging.h")
        start = header.index("#if defined(ESP32) && defined(ARDUINO_USB_MODE)",
                             header.index("MESH_ESP32_HWCDC_SESSION_GUARD") - 300)
        end = header.index("#if MESH_ESP32_TINYUSB_NONBLOCKING", start)
        guard = header[start:end]
        self.assertIn("ARDUINO_USB_MODE == 1", guard)
        self.assertNotIn("ENABLE_USB_INTERFACE", guard)

    def test_sleep_keeps_raw_flush_only_for_other_esp32_transports(self):
        text = source("src/helpers/ESP32Board.cpp")
        shutdown = text[text.index("void ESP32Board::shutdownPeripherals(") :
                        text.index("void ESP32Board::powerOff(")]
        sleep = text[text.index("void ESP32Board::enterDeepSleep(") :]
        self.assertIn("shutdownPeripherals();", sleep)
        guard = re.search(
            r"#if MESH_ESP32_USB_CONSOLE_COOPERATIVE\s+"
            r"mesh::serviceUsbTerminalPort\(\);\s+"
            r"#else\s+Serial\.flush\(\);\s+#endif",
            shutdown,
        )
        self.assertIsNotNone(guard)
        self.assertEqual(shutdown.count("Serial.flush();"), 1)
        self.assertNotIn("Serial.flush();", sleep)

    def test_large_file_dumps_are_bounded_and_cancelable(self):
        for role in ROLES:
            with self.subTest(role=role):
                text = source(f"examples/{role}/MyMesh.cpp")
                header = source(f"examples/{role}/MyMesh.h")
                self.assertIn("char serial_log_pending[640];", header)
                self.assertIn("serial_log_active ? serial_log_dump.size() : 0", text)
                start = text.index("void MyMesh::servicePendingSerialOutput()")
                end = text.index("bool MyMesh::setTxPower(", start)
                service = text[start:end]
                self.assertIn("serial_log_pending_size < sizeof(serial_log_pending)", service)
                self.assertIn("--serial_log_remaining;", service)
                self.assertIn("serial_log_pending_size -= written;", service)
                self.assertIn("memmove(serial_log_pending", service)
                self.assertNotIn("delay(", service)
                self.assertNotIn("flush(", service)
                cancel = text[
                    text.index("void MyMesh::cancelPendingSerialOutput()") : start
                ]
                self.assertIn("serial_log_dump.close();", cancel)
                self.assertIn("serial_log_pending_size = 0;", cancel)
                loop = text[text.index("void MyMesh::loop()") :]
                self.assertLess(
                    loop.index("mesh::Mesh::loop();"),
                    loop.index("servicePendingSerialOutput();"),
                )

    def test_recent_list_advances_only_after_retained_row_finishes(self):
        text = source("examples/simple_repeater/MyMesh.cpp")
        start = text.index("void MyMesh::servicePendingSerialOutput()")
        start = text.index("if (serial_recent_next >= 0)", start)
        service = text[start : text.index("if (!serial_log_active)", start)]
        self.assertIn("char record[64];", service)
        self.assertIn("serial_recent_next < serial_recent_count", service)
        self.assertIn("getNextRecentRepeaterBySortKey", service)
        self.assertNotIn("getRecentRepeaterBySortedIdx", service)
        self.assertNotRegex(service, r"\b(?:while|for)\s*\(")
        self.assertLess(
            service.index("if (serial_log_pending_size != 0) return;"),
            service.index("++serial_recent_next"),
        )
        self.assertIn("if (serial_log_pending_size == 0)", service)
        self.assertIn("memcpy(serial_log_pending, record, length);", service)
        self.assertIn("write_pending();", service)

    def test_functional_reserve_fits_default_full_acl_plus_command_echo(self):
        logging = source("src/helpers/UsbLogging.cpp")
        reserve = int(re.search(
            r"esp32_tinyusb_functional_reserve\s*=\s*(\d+)", logging
        ).group(1))
        acl = source("src/helpers/ClientACL.h")
        clients = int(re.search(r"#define MAX_CLIENTS\s+(\d+)", acl).group(1))
        acl_bytes = len("ACL:\r\n") + clients * (2 + 1 + 64 + 1)
        self.assertGreaterEqual(reserve, acl_bytes + len("get acl\r\n") + 160)

    def test_file_eof_and_malformed_line_marker_are_deferred(self):
        for role in ROLES:
            with self.subTest(role=role):
                text = source(f"examples/{role}/MyMesh.cpp")
                self.assertIn('strcmp(reply, "   EOF") == 0) reply[0] = 0;', text)
                self.assertIn('static const char eof[] = "  ->    EOF\\r\\n";', text)
                self.assertIn("if (!serial_log_active) {", text)
                self.assertIn("serial_log_eof_pending = false;", text)
                self.assertIn("while (budget-- > 0 && serial_log_remaining > 0)", text)
                self.assertIn("[USB log line omitted: exceeds 640 bytes]", text)
                self.assertIn("if (serial_log_skip_line) return;", text)


if __name__ == "__main__":
    unittest.main()
