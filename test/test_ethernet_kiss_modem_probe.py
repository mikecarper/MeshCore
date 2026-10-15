"""Host-only full KISS configuration tests; never open a serial device."""

from collections import deque
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/hil"))
import ethernet_kiss_modem_probe as probe


def fake_serial_modules():
    serial = SimpleNamespace(Serial=Mock())
    list_ports = SimpleNamespace(comports=Mock())
    modules = {"serial": serial, "serial.tools": SimpleNamespace(list_ports=list_ports)}
    return serial, list_ports, modules


def response(code, body=b""):
    return probe.encode_frame(6, bytes((code,)) + body)


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        self.now += 0.01
        return self.now


class FakePort:
    def __init__(self):
        self.decoder = probe.Decoder()
        self.input = deque()
        self.writes = []
        self.override = {}
        self.tx = 0

    def write(self, data):
        for frame in self.decoder.feed(data):
            self.writes.append(frame)
            if frame[0] != 6:
                continue
            command = frame[1]
            body = {0x21: struct.pack("<I", probe.GUARD_HZ), 0x11: b"\2\0",
                    0x01: bytes(range(32)), 0x17: b"",
                    0x12: struct.pack("<III", 10, self.tx, 0),
                    0x0B: probe.PRIMARY_STRUCT.pack(*probe.PRIMARY),
                    0x20: probe.SECONDARY_STRUCT.pack(*probe.SECONDARY), 0x0C: b"\0"}
            result = self.override.get(command, response(command | 0x80, body[command])
                                       if command in body else response(0xF0))
            self.input.extend(result[i:i + 1] for i in range(len(result)))
        return len(data)

    def flush(self):
        pass

    def read(self, size):
        return self.input.popleft() if self.input else b""


class ProbeTest(unittest.TestCase):
    def link(self, port=None):
        return probe.ControlLink(port or FakePort(), timeout=1, clock=Clock())

    def test_full_configuration_exact_tuple_no_data_or_crypto(self):
        port = FakePort()
        result = probe.configure_modem(self.link(port))
        self.assertTrue(result["success"])
        self.assertEqual(result["radio2"]["preamble"], 512)
        self.assertEqual(result["radio2"]["protocol"], 2)
        self.assertEqual(port.writes[0], b"\6\x21")
        self.assertIn(b"\6\x1f" + probe.SECONDARY_STRUCT.pack(*probe.SECONDARY), port.writes)
        self.assertIn(b"\1\0", port.writes)
        self.assertIn(b"\2\xff", port.writes)
        self.assertTrue(all(frame[0] in (1, 2, 6) for frame in port.writes))
        self.assertEqual(result["stats_before"], result["stats_after"])
        self.assertNotIn(bytes(range(32)).hex(), str(result))

    def test_bandwidth125_preamble128_exact_setter_and_readback_without_global_changes(self):
        port = FakePort()
        expected = (probe.GUARD_HZ, 125000, 5, 5, 2, 128, 2)
        port.override[0x20] = response(0xA0, probe.SECONDARY_STRUCT.pack(*expected))
        report = probe.configure_modem(self.link(port), bandwidth=125, preamble=128)
        self.assertTrue(report["success"])
        self.assertEqual(report["radio2"], dict(zip(("frequency_hz", "bandwidth_hz", "sf", "cr",
                                                   "mode", "preamble", "protocol"), expected)))
        self.assertIn(b"\6\x1f" + probe.SECONDARY_STRUCT.pack(*expected), port.writes)
        self.assertEqual(probe.SECONDARY, (probe.GUARD_HZ, 500000, 5, 5, 2, 512, 2))
        self.assertEqual(probe.secondary_tuple(), probe.SECONDARY)
        self.assertTrue(all(frame[0] in (1, 2, 6) for frame in port.writes))

    def test_selected_secondary_readback_cannot_silently_use_default_width_or_preamble(self):
        for observed in (probe.SECONDARY, (probe.GUARD_HZ, 125000, 5, 5, 2, 512, 2),
                         (probe.GUARD_HZ, 500000, 5, 5, 2, 128, 2)):
            port = FakePort()
            port.override[0x20] = response(0xA0, probe.SECONDARY_STRUCT.pack(*observed))
            with self.assertRaisesRegex(probe.ProtocolError, "Secondary"):
                probe.configure_modem(self.link(port), bandwidth=125, preamble=128)

    def test_invalid_bandwidth_or_preamble_fails_before_any_hardware_request(self):
        for options in ([dict(bandwidth=value) for value in (250, 125.0, True, "125", None)] +
                        [dict(preamble=value) for value in (31, 65, 1025, 128.0, True, "128", None)]):
            port = FakePort()
            with self.assertRaises(ValueError): probe.configure_modem(self.link(port), **options)
            self.assertEqual(port.writes, [])
        for preamble in probe.PREAMBLES:
            self.assertEqual(probe.secondary_tuple(bandwidth=125, preamble=preamble),
                             (probe.GUARD_HZ, 125000, 5, 5, 2, preamble, 2))

    def test_cli_passes_explicit_width_and_preamble_and_rejects_invalid_before_open(self):
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        port = FakePort(); port.close = Mock()
        info = SimpleNamespace(serial_number="known", device="/dev/example", vid=0x239A, pid=0x8029)
        with patch.object(probe, "open_verified_port", return_value=(port, info)), \
             patch.object(probe, "configure_modem", return_value={"success": True}) as configure, \
             redirect_stdout(StringIO()):
            result = probe.main(["--port", "/dev/example", "--expect-serial", "known",
                                 "--bandwidth", "125", "--preamble", "128"])
        self.assertEqual(result, 0)
        self.assertEqual(configure.call_args.kwargs, dict(bandwidth=125, preamble=128))
        port.close.assert_called_once()
        for option, value in (("--bandwidth", "250"), ("--preamble", "65")):
            with patch.object(probe, "open_verified_port") as opened, redirect_stderr(StringIO()), \
                 self.assertRaises(SystemExit):
                probe.main(["--port", "/dev/example", "--expect-serial", "known", option, value])
            opened.assert_not_called()

    def test_guard_unknown_fails_before_any_setter(self):
        port = FakePort()
        port.override[0x21] = response(0xF1, b"\5")
        with self.assertRaisesRegex(probe.ProtocolError, "failed: 0x05"):
            probe.configure_modem(self.link(port))
        self.assertEqual(port.writes, [b"\6\x21"])

    def test_wrong_guard_never_sets_anything(self):
        port = FakePort()
        port.override[0x21] = response(0xA1, struct.pack("<I", 910_525_000))
        with self.assertRaisesRegex(probe.ProtocolError, "TX lock"):
            probe.configure_modem(self.link(port))
        self.assertEqual(len(port.writes), 1)

    def test_bad_version_fails_before_setters(self):
        port = FakePort()
        port.override[0x11] = response(0x91, b"\1\0")
        with self.assertRaisesRegex(probe.ProtocolError, "version"):
            probe.configure_modem(self.link(port))
        self.assertEqual(len(port.writes), 2)

    def test_primary_float_rounding_only_frequency(self):
        port = FakePort()
        port.override[0x0B] = response(0x8B, probe.PRIMARY_STRUCT.pack(910_525_024, 62500, 7, 5))
        self.assertEqual(probe.configure_modem(self.link(port))["primary_rx_only"]["frequency_hz"],
                         910_525_024)
        for bad in ((910_526_000, 62500, 7, 5), (910_525_000, 125000, 7, 5)):
            port = FakePort()
            port.override[0x0B] = response(0x8B, probe.PRIMARY_STRUCT.pack(*bad))
            with self.assertRaisesRegex(probe.ProtocolError, "Primary"):
                probe.configure_modem(self.link(port))

    def test_secondary_legacy_truncated_wrong_protocol_or_preamble_rejected(self):
        for body in (probe.SECONDARY_STRUCT.pack(*probe.SECONDARY)[:-1],
                     probe.SECONDARY_STRUCT.pack(*probe.SECONDARY[:-1], 0),
                     probe.SECONDARY_STRUCT.pack(*probe.SECONDARY[:5], 32, 2)):
            port = FakePort()
            port.override[0x20] = response(0xA0, body)
            with self.assertRaises(probe.ProtocolError):
                probe.configure_modem(self.link(port))

    def test_unknown_or_malformed_error_is_immediate(self):
        for frame in (response(0xF1, b"\5"), response(0xF1), response(0xF1, b"\5\0")):
            port = FakePort()
            port.override[0x21] = frame
            with self.assertRaises(probe.ProtocolError):
                self.link(port).request(0x21, size=4)

    def test_timeout_bounded_and_no_retries(self):
        port = FakePort()
        port.override[0x21] = b""
        with self.assertRaisesRegex(probe.ProtocolError, "Timed out"):
            self.link(port).request(0x21, size=4)
        self.assertEqual(len(port.writes), 1)

    def test_metadata_and_rx_data_allowed_but_tx_done_fails(self):
        port = FakePort()
        port.override[0x21] = (response(0xF9, b"\0\0") + b"\xc0\x10rx\xc0" +
                               response(0xA1, struct.pack("<I", probe.GUARD_HZ)))
        link = self.link(port)
        self.assertEqual(len(link.request(0x21, size=4)), 4)
        self.assertEqual((link.rx_metadata_frames, link.ignored_data_frames), (1, 1))
        port = FakePort()
        port.override[0x21] = response(0xF8, b"\1")
        with self.assertRaisesRegex(probe.ProtocolError, "TX_DONE"):
            self.link(port).request(0x21, size=4)

    def test_escape_split_and_overflow(self):
        decoder = probe.Decoder()
        wire = response(0x81, b"\xc0\xdb")
        frames = []
        for value in wire:
            frames.extend(decoder.feed(bytes((value,))))
        self.assertEqual(frames, [b"\6\x81\xc0\xdb"])
        for wire in (b"\xc0\6\xdb\0", b"\xc0\6\xdb\xc0",
                     b"\xc0" + b"x" * 515):
            with self.assertRaises(probe.ProtocolError):
                probe.Decoder().feed(wire)

    def test_encoder_refuses_data_and_nonzero_port(self):
        for command in (0, 0x10, -1):
            with self.assertRaises(ValueError):
                probe.encode_frame(command, b"test")

    def test_tx_counter_change_is_not_reported_as_success(self):
        with patch.object(probe, "_stats", side_effect=[{"rx": 1, "tx": 0, "errors": 0},
                                                       {"rx": 2, "tx": 1, "errors": 0}]):
            with self.assertRaisesRegex(probe.ProtocolError, "TX counter changed"):
                probe.configure_modem(self.link())

    def test_busy_or_wrong_usb_identity_is_never_opened(self):
        serial, list_ports, modules = fake_serial_modules()
        info = SimpleNamespace(device="/dev/example", serial_number="wrong", vid=0x239A)
        with patch.dict(sys.modules, modules), \
             patch.object(list_ports, "comports", return_value=[info]), \
             patch.object(serial, "Serial") as constructor:
            with self.assertRaisesRegex(probe.ProtocolError, "USB serial"):
                probe.open_verified_port("/dev/example", "9AB3B64C641BA927")
            constructor.assert_not_called()
        info.serial_number = "9AB3B64C641BA927"
        with patch.dict(sys.modules, modules), \
             patch.object(list_ports, "comports", return_value=[info]), \
             patch.object(probe, "assert_port_idle", side_effect=RuntimeError("busy")), \
             patch.object(serial, "Serial") as constructor:
            with self.assertRaisesRegex(RuntimeError, "busy"):
                probe.open_verified_port("/dev/example", info.serial_number)
            constructor.assert_not_called()

    def test_native_nrf_usb_open_is_exclusive_dtr_on_rts_off(self):
        serial, list_ports, modules = fake_serial_modules()
        info = SimpleNamespace(device="/dev/example", serial_number="9AB3B64C641BA927", vid=0x239A)
        port = SimpleNamespace(close=Mock())
        with patch.dict(sys.modules, modules), \
             patch.object(list_ports, "comports", return_value=[info]), \
             patch.object(probe, "assert_port_idle"), \
             patch.object(serial, "Serial", return_value=port) as constructor, \
             patch.object(probe, "open_configured_session") as opener:
            selected, observed = probe.open_verified_port("/dev/example", info.serial_number)
        self.assertIs(selected, port)
        self.assertIs(observed, info)
        self.assertTrue(port.dtr)
        self.assertFalse(port.rts)
        self.assertTrue(constructor.call_args.kwargs["exclusive"])
        self.assertEqual(constructor.call_args.kwargs["baudrate"], 115200)
        opener.assert_called_once_with(port)

    def test_post_open_usb_identity_change_closes_before_commands(self):
        serial, list_ports, modules = fake_serial_modules()
        info = SimpleNamespace(device="/dev/example", serial_number="9AB3B64C641BA927", vid=0x239A)
        wrong = SimpleNamespace(device="/dev/example", serial_number="different", vid=0x239A)
        port = SimpleNamespace(close=Mock())
        with patch.dict(sys.modules, modules), \
             patch.object(list_ports, "comports", side_effect=[[info], [wrong]]), \
             patch.object(probe, "assert_port_idle"), \
             patch.object(serial, "Serial", return_value=port), \
             patch.object(probe, "open_configured_session"):
            with self.assertRaisesRegex(probe.ProtocolError, "identity changed"):
                probe.open_verified_port("/dev/example", info.serial_number)
        port.close.assert_called_once()

    def test_cli_releases_port_on_protocol_failure(self):
        from contextlib import redirect_stdout
        from io import StringIO
        port = FakePort()
        port.close = Mock()
        info = SimpleNamespace(serial_number="9AB3B64C641BA927", device="/dev/example", vid=0x239A, pid=0x8029)
        with patch.object(probe, "open_verified_port", return_value=(port, info)), \
             patch.object(probe, "configure_modem", side_effect=probe.ProtocolError("rejected")), \
             redirect_stdout(StringIO()):
            result = probe.main(["--port", "/dev/example", "--expect-serial", info.serial_number])
        self.assertEqual(result, 1)
        port.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
