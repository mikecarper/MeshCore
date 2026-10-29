"""Offline checks for the contact cache collector's serial opening boundary."""
import os
from types import SimpleNamespace
import unittest
from unittest import mock

import contact_cache_serial_stress as hil


class ContactCacheOpenTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Requires the POSIX pySerial backend")
    def test_actual_posix_open_avoids_reset_and_keeps_requested_session(self):
        from test_profile_switch_host import PosixModemSerial

        class BeforeProtocol(Exception):
            pass

        master, slave = os.openpty()
        try:
            for vid, expected in ((0x10C4, False), (0x303A, True)):
                with self.subTest(vid=vid):
                    ports = []
                    def factory(*args, **kwargs):
                        port = PosixModemSerial(*args, **kwargs)
                        ports.append(port)
                        return port

                    with mock.patch.object(hil.serial, "Serial", side_effect=factory), mock.patch.object(
                            hil.time, "sleep", side_effect=BeforeProtocol), mock.patch(
                            "profile_switch.list_ports.comports", return_value=[
                                SimpleNamespace(device=os.ttyname(slave), vid=vid)]):
                        # Stop at the existing initial settle, before any radio
                        # or protocol request. Exercise the actual collector open.
                        with self.assertRaises(BeforeProtocol):
                            hil.run(SimpleNamespace(port=os.ttyname(slave)))
                    self.assertEqual(len(ports), 1)
                    port = ports[0]
                    self.assertFalse(port.has_reset_transition(), port.modem_edges)
                    self.assertEqual(port.modem_lines, {"dtr": expected, "rts": False})
                    self.assertFalse(port.dsrdtr)
                    self.assertFalse(port.is_open)
        finally:
            os.close(master)
            os.close(slave)

    def test_collector_open_failure_closes_and_restores_flow(self):
        from test_profile_switch_host import FailingSessionPort

        for phase in ("open", "rts", "dtr", "restore"):
            with self.subTest(phase=phase):
                port = FailingSessionPort(phase)
                with mock.patch.object(hil.serial, "Serial", return_value=port), mock.patch(
                        "serial_session.os.name", "posix"), mock.patch(
                        "serial_session.sys.platform", "darwin"), mock.patch(
                        "profile_switch.list_ports.comports", return_value=[]):
                    with self.assertRaisesRegex(RuntimeError, phase + " failed"):
                        hil.run(SimpleNamespace(port="test"))
                self.assertFalse(port.is_open)
                self.assertFalse(port.dsrdtr)
                self.assertEqual(port.close_count, 1)


if __name__ == "__main__":
    unittest.main()
