"""Boot evidence must distinguish trace/USB presence from a real CLI reply."""
import unittest
import json
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from capture_infrastructure_boot import (
    CompanionCapturePort, capture, companion_boot_request, completed_version, preflight, selected_port, trace_phases,
)


class BootEvidenceTests(unittest.TestCase):
    def test_transient_missing_usb_enumeration_retries_but_wrong_identity_fails(self):
        endpoint = Mock()
        endpoint.stat.return_value.st_mode = stat.S_IFCHR
        path = Mock()
        path.resolve.return_value = endpoint
        with patch('capture_infrastructure_boot.Path', return_value=path), \
                patch('serial.tools.list_ports.comports', return_value=[]):
            with self.assertRaises(FileNotFoundError):
                selected_port('physical', 'identity', 'aabbccddeeff')
        port = SimpleNamespace(device='/dev/ttyACM99', vid=0x303A, pid=0x1001,
                               serial_number='001122334455')
        with patch('capture_infrastructure_boot.Path', return_value=path), \
                patch('serial.tools.list_ports.comports', return_value=[port]):
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                selected_port('physical', 'identity', 'aabbccddeeff')

    def test_boot_trace_and_descriptor_do_not_confirm_runtime_console(self):
        data = (b'USB JTAG/serial debug unit\r\n'
                b'HIL_BOOT phase=identity_begin at=1234\r\n'
                b'Firmware: v1.18.0.2\r\n')
        self.assertIsNone(completed_version(data))
        self.assertEqual(trace_phases(data), [{'phase': 'identity_begin', 'milliseconds': 1234}])

    def test_requires_exact_command_and_complete_reply(self):
        self.assertIsNone(completed_version(b'get ver\r\n  -> v1.18.0.2\r\n'))
        self.assertIsNone(completed_version(b'ver\r\n  -> v1.18.0.2\r'))
        self.assertIsNone(completed_version(b'ver\r\n  -> Unknown command\r\n'))
        self.assertEqual(completed_version(b'\r\nver\r\n  -> v1.18.0.2-a925bdef\r\n'),
                         'v1.18.0.2-a925bdef')

    def test_trace_retains_order_and_does_not_accept_partial_stage(self):
        data = (b'HIL_BOOT phase=board_ready at=12\r\n'
                b'HIL_BOOT phase=radio_begin at=1025\r\n'
                b'HIL_BOOT phase=identity_begin at=1999')
        self.assertEqual(trace_phases(data), [
            {'phase': 'board_ready', 'milliseconds': 12},
            {'phase': 'radio_begin', 'milliseconds': 1025}])

    def test_capture_retains_early_bytes_and_requires_its_own_version_reply(self):
        for answer, expected_exit in ((True, 0), (False, 1)):
            with self.subTest(answer=answer), TemporaryDirectory() as folder:
                clock = SimpleNamespace(now=0)

                class Port:
                    def __init__(self, **kwargs):
                        self.pending = b'HIL_BOOT phase=radio_begin at=100\r\n'
                        self.in_waiting = 1
                        self.writes = []
                        self.closed = False

                    def read(self, count):
                        clock.now += .25
                        data, self.pending = self.pending, b''
                        return data

                    def write(self, data):
                        self.writes.append(data)
                        if answer:
                            self.pending = b'ver\r\n  -> v1.18.0.2-a925bdef\r\n'
                        return len(data)

                    def close(self):
                        self.closed = True

                port = Port()
                args = SimpleNamespace(seconds=3, usb_path='physical', usb_id='identity',
                                       expected_serial='aabbccddeeff', output=Path(folder)/'boot.raw')
                with patch('serial.Serial', return_value=port), \
                        patch('capture_infrastructure_boot.time.monotonic', side_effect=lambda: clock.now), \
                        patch('capture_infrastructure_boot.selected_port', return_value='/dev/ttyACM99'), \
                        patch('capture_infrastructure_boot.assert_port_idle'), \
                        patch('capture_infrastructure_boot.open_configured_session'), \
                        patch('builtins.print'):
                    self.assertEqual(capture(args), expected_exit)
                self.assertFalse(port.dtr)
                self.assertFalse(port.rts)
                self.assertTrue(port.closed)
                self.assertEqual(port.writes, [b'\rver\r'])
                self.assertTrue(args.output.read_bytes().startswith(b'HIL_BOOT'))
                report = json.loads(Path(str(args.output)+'.safe.json').read_text())
                self.assertEqual(report['status'], 'runtime_verified' if answer else 'runtime_not_verified')
                self.assertNotIn('usb_path', report)
                self.assertNotIn('usb_id', report)

    def test_capture_retries_a_transient_open_failure_without_switching_identity(self):
        import serial
        with TemporaryDirectory() as folder:
            clock = SimpleNamespace(now=0)
            args = SimpleNamespace(seconds=3, usb_path='physical', usb_id='identity',
                                   expected_serial='aabbccddeeff', output=Path(folder)/'boot.raw')
            first, second = Mock(), Mock()
            second.in_waiting = 1
            def read(_):
                clock.now += .5
                return b'ver\r\n  -> v1.18.0.2\r\n' if clock.now >= 2.5 else b''
            second.read.side_effect = read
            second.write.return_value = 5
            def sleep(duration):
                clock.now += duration
            with patch('serial.Serial', side_effect=[first, second]), \
                    patch('capture_infrastructure_boot.time.monotonic', side_effect=lambda: clock.now), \
                    patch('capture_infrastructure_boot.time.sleep', side_effect=sleep), \
                    patch('capture_infrastructure_boot.selected_port', return_value='/dev/ttyACM99') as selected, \
                    patch('capture_infrastructure_boot.assert_port_idle'), \
                    patch('capture_infrastructure_boot.open_configured_session',
                          side_effect=[serial.SerialException('disconnected'), None]), \
                    patch('builtins.print'):
                self.assertEqual(capture(args), 0)
            first.close.assert_called_once_with()
            second.close.assert_called_once_with()
            self.assertEqual(selected.call_count, 4)
            self.assertEqual(json.loads(Path(str(args.output)+'.safe.json').read_text())['status'],
                             'runtime_verified')

    def test_preflight_rejects_unwritable_capture_directory_before_usb_access(self):
        with TemporaryDirectory() as folder:
            args = SimpleNamespace(protocol='infrastructure', usb_path='physical', usb_id='identity',
                                   expected_serial='aabbccddeeff', output=Path(folder)/'boot.raw')
            with patch('capture_infrastructure_boot.tempfile.TemporaryFile', side_effect=PermissionError), \
                    patch('capture_infrastructure_boot.selected_port') as selected:
                with self.assertRaises(PermissionError):
                    preflight(args)
            selected.assert_not_called()

    def test_preflight_loads_companion_dependencies_before_usb_access(self):
        import builtins
        original_import = builtins.__import__
        def reject_companion(name, *args, **kwargs):
            if name == 'companion_serial':
                raise ImportError('missing companion dependency')
            return original_import(name, *args, **kwargs)
        with TemporaryDirectory() as folder:
            args = SimpleNamespace(protocol='companion', usb_path='physical', usb_id='identity',
                                   expected_serial='aabbccddeeff', output=Path(folder)/'boot.json')
            with patch('builtins.__import__', side_effect=reject_companion), \
                    patch('capture_infrastructure_boot.selected_port') as selected:
                with self.assertRaises(ImportError):
                    preflight(args)
            selected.assert_not_called()

    def test_companion_uses_framed_start_and_version_without_retaining_keys(self):
        with TemporaryDirectory() as folder:
            args = SimpleNamespace(seconds=3, protocol='companion', usb_path='physical',
                                   usb_id='identity', expected_serial='aabbccddeeff',
                                   output=Path(folder)/'boot.json')
            client = Mock()
            client.start.return_value = {'model': 'Heltec V4.3', 'primary': {'public_key': 'SECRET'}}
            client.cli.return_value = 'v1.18.0.2-test'
            with patch('capture_infrastructure_boot.selected_port', return_value='/dev/ttyACM99') as selected, \
                    patch('capture_infrastructure_boot.assert_port_idle'), \
                    patch('capture_infrastructure_boot.new_companion', return_value=client), \
                    patch('builtins.print'):
                self.assertEqual(capture(args), 0)
            self.assertEqual(selected.call_count, 2)
            client.start.assert_called_once_with()
            client.cli.assert_called_once_with('ver')
            client.close.assert_called_once_with()
            report = json.loads(args.output.read_text())
            self.assertEqual(report['model'], 'Heltec V4.3')
            self.assertEqual(report['version'], 'v1.18.0.2-test')
            self.assertNotIn('SECRET', args.output.read_text())
            self.assertNotIn('primary', report)
            self.assertNotIn('usb_path', report)
            self.assertNotIn('usb_id', report)

    def test_companion_refuses_changed_identity_before_open(self):
        with TemporaryDirectory() as folder:
            args = SimpleNamespace(seconds=3, protocol='companion', usb_path='physical',
                                   usb_id='identity', expected_serial='aabbccddeeff',
                                   output=Path(folder)/'boot.json')
            with patch('capture_infrastructure_boot.selected_port', side_effect=['/dev/ttyACM99', '/dev/ttyACM98']), \
                    patch('capture_infrastructure_boot.assert_port_idle'), \
                    patch('capture_infrastructure_boot.new_companion') as opened, \
                    patch('builtins.print'):
                with self.assertRaisesRegex(RuntimeError, 'identity moved'):
                    capture(args)
            opened.assert_not_called()
            self.assertEqual(json.loads(args.output.read_text())['status'], 'capture_failed')

    def test_native_companion_negotiation_cannot_raise_control_lines_or_flush(self):
        port = Mock()
        guarded = CompanionCapturePort(port, 20)
        guarded.dtr = True
        guarded.rts = True
        guarded.flush()
        self.assertFalse(port.dtr)
        self.assertFalse(port.rts)
        port.flush.assert_not_called()

    def test_companion_boot_timeout_retries_but_rejection_does_not(self):
        from companion_serial import Failure
        for failure, expected_attempts in (('Companion response timeout for command 22', 2),
                                           ('Companion command rejected, error 01', 1)):
            with self.subTest(failure=failure), TemporaryDirectory() as folder:
                args = SimpleNamespace(seconds=3, protocol='companion', usb_path='physical',
                                       usb_id='identity', expected_serial='aabbccddeeff',
                                       output=Path(folder)/'boot.json')
                first, second = Mock(), Mock()
                first.start.side_effect = Failure(failure)
                second.start.return_value = {'model': 'Heltec V4.3'}
                second.cli.return_value = 'v1.18.0.2-test'
                with patch('capture_infrastructure_boot.selected_port', return_value='/dev/ttyACM99'), \
                        patch('capture_infrastructure_boot.assert_port_idle'), \
                        patch('capture_infrastructure_boot.new_companion', side_effect=[first, second]) as opened, \
                        patch('capture_infrastructure_boot.time.sleep'), patch('builtins.print'):
                    if expected_attempts == 2:
                        self.assertEqual(capture(args), 0)
                        second.close.assert_called_once_with()
                    else:
                        with self.assertRaises(Failure):
                            capture(args)
                        second.close.assert_not_called()
                self.assertEqual(opened.call_count, expected_attempts)
                first.close.assert_called_once_with()

    def test_companion_startup_skips_valid_late_device_info_before_app_start(self):
        # An earlier DEVICE_QUERY response may arrive after APP_START was sent.
        info = bytearray(82)
        info[0:4] = bytes((13, 14, 10, 40))
        info[8:18] = b'28-Oct-26\0'
        info[20:31] = b'Heltec V4.3'
        info[60:70] = b'v1.18.0.2\0'
        expected = b'\x05expected-self-info'
        client = Mock()
        client.port.write.side_effect = len
        client.reader.read_frame.side_effect = [bytes(info), expected]
        with patch('capture_infrastructure_boot.time.monotonic', return_value=0):
            self.assertEqual(companion_boot_request(client, b'\x01', (5,), 30), expected)
        self.assertEqual(client.reader.read_frame.call_count, 2)

    def test_companion_startup_rejects_malformed_unknown_and_error_replies(self):
        from companion_serial import Failure
        for frame in (b'\x0dshort', b'\x7funknown', b'\x01\x02'):
            with self.subTest(frame=frame):
                client = Mock()
                client.port.write.side_effect = len
                client.reader.read_frame.return_value = frame
                with patch('capture_infrastructure_boot.time.monotonic', return_value=0), \
                        self.assertRaises((Failure, ValueError)):
                    companion_boot_request(client, b'\x01', (5,), 30)
                self.assertEqual(client.reader.read_frame.call_count, 1)

    def test_companion_startup_bounds_unrelated_push_frames(self):
        from companion_serial import Failure
        client = Mock()
        client.port.write.side_effect = len
        client.reader.read_frame.return_value = b'\x80push'
        with patch('capture_infrastructure_boot.time.monotonic', return_value=0), \
                self.assertRaisesRegex(Failure, 'unrelated-frame limit'):
            companion_boot_request(client, b'\x01', (5,), 30)
        self.assertEqual(client.reader.read_frame.call_count, 65)


if __name__ == '__main__':
    unittest.main()
