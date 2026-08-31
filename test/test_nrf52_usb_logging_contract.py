#!/usr/bin/env python3
"""Static integration guards for nRF52 nonblocking USB diagnostics."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Nrf52UsbLoggingContractTest(unittest.TestCase):
    def test_all_nrf52_logging_ports_return_the_nonblocking_facade(self):
        source = (ROOT / "src/helpers/UsbLogging.cpp").read_text()

        self.assertIn(
            "nonblocking_dedicated_usb_logging_port(dedicated_usb_logging_port)",
            source,
        )
        self.assertIn("nonblocking_primary_usb_logging_port(Serial)", source)
        self.assertIn("return nonblocking_dedicated_usb_logging_port;", source)
        self.assertIn("return nonblocking_primary_usb_logging_port;", source)
        # ESP32 and other platforms retain their original Serial path.
        self.assertIn("#else\n    return Serial;\n  #endif", source)

    def test_facade_uses_a_zero_wait_gate_around_capacity_and_write(self):
        source = (ROOT / "src/helpers/NonBlockingWriteStream.h").read_text()

        enter = source.index("_writer_busy.test_and_set")
        capacity = source.index("_delegate.availableForWrite()", enter)
        write = source.index("_delegate.write(data, size)")
        release = source.index("_writer_busy.clear")
        self.assertLess(enter, capacity)
        self.assertLess(capacity, write)
        self.assertLess(write, release)
        self.assertIn("size > MAX_WRITE_SIZE", source)

    def test_meshcore_debug_macros_use_the_bounded_formatter_only_on_nrf52(self):
        source = (ROOT / "src/MeshCore.h").read_text()

        self.assertIn("#if defined(NRF52_PLATFORM)", source)
        self.assertIn('mesh::nrf52DebugPrintf("DEBUG: " F', source)
        self.assertIn(
            'mesh::usbLoggingPort().printf("DEBUG: " F',
            source,
        )


if __name__ == "__main__":
    unittest.main()
