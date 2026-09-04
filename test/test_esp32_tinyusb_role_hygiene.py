#!/usr/bin/env python3
"""Keep native-TinyUSB repeater/room output away from blocking Arduino writes."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLES = ("simple_repeater", "simple_room_server")


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


class Esp32TinyUsbRoleHygieneTest(unittest.TestCase):
    def test_console_helper_preserves_other_platform_semantics(self):
        text = source("src/helpers/UsbLogging.cpp")
        self.assertRegex(
            text,
            r"Stream& usbConsolePort\(\)\s*\{\s*"
            r"#if MESH_ESP32_TINYUSB_NONBLOCKING\s+"
            r"return buffered_esp32_tinyusb_terminal_port;\s+"
            r"#else\s+return Serial;\s+#endif",
        )

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
                self.assertIn("mesh::beginUsbLoggingPort();", setup)
                self.assertIn("mesh::serviceUsbTerminalPort();", loop)

    def test_sleep_keeps_raw_flush_only_for_other_esp32_transports(self):
        text = source("src/helpers/ESP32Board.cpp")
        sleep = text[text.index("void ESP32Board::enterDeepSleep(") :]
        guard = re.search(
            r"#if MESH_ESP32_TINYUSB_NONBLOCKING\s+"
            r"mesh::serviceUsbTerminalPort\(\);\s+"
            r"#else\s+Serial\.flush\(\);\s+#endif",
            sleep,
        )
        self.assertIsNotNone(guard)
        self.assertEqual(sleep.count("Serial.flush();"), 1)

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

    def test_recent_list_advances_only_after_whole_row_admission(self):
        text = source("examples/simple_repeater/MyMesh.cpp")
        start = text.index("void MyMesh::servicePendingSerialOutput()")
        service = text[start : text.index("if (!serial_log_active)", start)]
        self.assertIn("char record[64];", service)
        self.assertIn("serial_recent_next < serial_recent_count", service)
        self.assertIn("getNextRecentRepeaterBySortKey", service)
        self.assertNotIn("getRecentRepeaterBySortedIdx", service)
        self.assertNotRegex(service, r"\b(?:while|for)\s*\(")
        self.assertLess(
            service.index("console.availableForWrite() < length"),
            service.index("++serial_recent_next"),
        )
        self.assertIn("!= static_cast<size_t>(length)) return;", service)

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
