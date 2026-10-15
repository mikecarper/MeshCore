"""TAP relay byte/backpressure checks without privileged resources or sockets."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/hil"))
import ethernet_tap_relay as relay
from ethernet_kiss_probe import kiss_encode, KissDecoder


class EthernetTapRelayTests(unittest.TestCase):
    def test_windows_kiss_transport_keeps_raw_bytes_and_separate_fds(self):
        shared = Mock(read_fd=77, write_fd=78)
        payload = b"\xc0\x10\0\xdb\xdc\xff\xc0"
        shared.read.return_value = payload
        shared.write.return_value = len(payload)
        with patch.object(relay, "WindowsUsbStream", return_value=shared) as opened:
            stream = relay.KissStream("winusb://COM6", 115200, windows_python="/mnt/c/python.exe",
                expect_serial="EXACT", expect_vid=0x303a, expect_pid=0x1001,
                serial_dtr=True, duration=300)
            self.assertEqual((stream.read_fd, stream.write_fd), (77, 78))
            self.assertEqual(stream.read(99), payload)
            self.assertEqual(stream.write(payload), len(payload))
            stream.close()
        opened.assert_called_once_with("winusb://COM6", python="/mnt/c/python.exe", expected_serial="EXACT",
            expected_vid=0x303a, expected_pid=0x1001, baud=115200, serial_dtr=True, duration=300)
        shared.write.assert_called_once_with(payload)
        shared.close.assert_called_once()

    def test_kiss_relay_uses_separate_pipe_fds_without_changing_modem_pacing(self):
        stream = Mock(read_fd=77, write_fd=78)
        stream.write.side_effect = len
        selections = []

        def selected(reads, writes, errors, timeout):
            selections.append((reads, writes))
            return [], [78], []

        with patch.object(relay.select, "select", side_effect=selected):
            stats = relay.relay(stream, 88, duration=1, kiss_modem=True, kiss_port=1,
                                stop=lambda: len(selections) >= 3)
        self.assertEqual(selections, [([88, 77], [78])] * 3)
        self.assertEqual(stats["modem_controls_written"], 3)
        self.assertEqual(stats["kiss_written"], 0)
    def test_selected_port_preserves_bytes_and_ignores_other_data_and_full_modem_metadata(self):
        frame = bytes(range(255))
        metadata = b"\xc0\x06\xf9\x00\x01\xc0\x06\xf8\x01\xc0"
        for port in (0, 1):
            buffers = relay.RelayBuffers(kiss_port=port)
            buffers.queue_tap_frame(frame)
            self.assertEqual(buffers.to_kiss[0][1], port << 4)
            self.assertEqual(KissDecoder(port=port).feed(buffers.to_kiss[0]), [frame])
            buffers.feed_kiss(metadata + kiss_encode(b"other" * 12, port=1 - port) + kiss_encode(frame, port=port))
            self.assertEqual(list(buffers.to_tap), [frame])
            self.assertEqual(buffers.stats()["decode_errors"], 0)
        for invalid in (True, -1, 2, 1.0):
            with self.assertRaises(ValueError): relay.RelayBuffers(kiss_port=invalid)
            with self.assertRaises(ValueError): kiss_encode(frame, port=invalid)

    def test_bench_endpoint_allowlist_is_explicit_and_does_not_access_devices(self):
        for value in ("serial:///dev/serial/by-id/usb-RAK_4631_ABC-if00", "serial:///dev/serial/by-path/platform-usb-0:1.2:1.0",
                      "serial:///dev/ttyACM0", "serial:///dev/ttyUSB0", "tcp://192.168.1.84:5003"):
            self.assertIn(relay.validate_endpoint(value, strict_tcp=True).scheme, ("serial", "tcp"))
        for value in ("serial:///dev/mem", "serial:///etc/passwd", "serial://host/dev/ttyACM0",
                      "serial:///dev/serial/by-id/../by-path/device", "serial:///dev/ttyACM0?baud=1",
                      "serial:///dev/serial/by-id/%2fetc", "tcp://radio:5003", "tcp://192.168.1.84:5002",
                      "tcp://user:secret@192.168.1.84:5003", "tcp://192.168.1.84:5003/path"):
            with self.assertRaises(ValueError): relay.validate_endpoint(value, strict_tcp=True)
        with patch.object(relay.os.path, "realpath", return_value="/dev/mem"), \
                patch.object(relay, "open_configured_session") as opened:
            with self.assertRaises(ValueError):
                relay.KissStream("serial:///dev/serial/by-id/explicit-device", 115200)
        opened.assert_not_called()

    def test_explicit_full_modem_initialization_then_one_data_until_successful_tx_done(self):
        clock = [100.0]
        buffers = relay.RelayBuffers(kiss_port=1, kiss_modem=True, now=lambda: clock[0])
        frames = [b"a" * 60, b"b" * 60]
        for frame in frames: buffers.queue_tap_frame(frame)
        written = []
        while buffers.controls:
            buffers.write_kiss(lambda data: written.append(data) or len(data))
        self.assertEqual(written, [b"\xc0\x01\0\xc0", b"\xc0\x02\xff\xc0", b"\xc0\x03\x01\xc0"])
        buffers.write_kiss(lambda data: written.append(data) or len(data))
        self.assertEqual(written[-1], kiss_encode(frames[0], port=1))
        self.assertFalse(buffers.can_write_kiss())
        writer = Mock(); buffers.write_kiss(writer); writer.assert_not_called()
        buffers.feed_kiss(b"\xc0\x06\xf9\x00\x01\xc0")
        self.assertEqual(buffers.stats()["modem_rx_meta"], 1)
        self.assertFalse(buffers.can_write_kiss())
        buffers.feed_kiss(b"\xc0\x16\xf8\x01\xc0")  # Wrong control port cannot release it.
        self.assertFalse(buffers.can_write_kiss())
        buffers.feed_kiss(b"\xc0\x06\xf8\x01\xc0")
        self.assertTrue(buffers.can_write_kiss())
        self.assertEqual(buffers.stats()["modem_tx_done"], 1)
        buffers.write_kiss(lambda data: written.append(data) or len(data))
        self.assertEqual(written[-1], kiss_encode(frames[1], port=1))
        clock[0] += 10
        with self.assertRaises(relay.ModemFlowError) as failure: buffers.check_tx_done()
        self.assertEqual(failure.exception.stats["modem_tx_timeout"], 1)

    def test_full_modem_errors_fail_closed_no_next_data_or_automatic_retry(self):
        for control in (b"\xc0\x06\xf8\x02\xc0", b"\xc0\x06\xf1\x07\xc0", b"\xc0\x06\xf8\xc0"):
            buffers = relay.RelayBuffers(kiss_modem=True)
            buffers.queue_tap_frame(b"a" * 60); buffers.queue_tap_frame(b"b" * 60)
            while buffers.controls: buffers.write_kiss(len)
            buffers.write_kiss(len)
            with self.assertRaises(relay.ModemFlowError) as failure: buffers.feed_kiss(control)
            self.assertEqual(failure.exception.stats["modem_tx_error"], 1)
            self.assertEqual(len(buffers.to_kiss), 1)
            self.assertFalse(buffers.can_write_kiss())

    def test_rejected_tx_done_drops_one_frame_then_allows_next_without_replay(self):
        buffers = relay.RelayBuffers(kiss_port=1, kiss_modem=True)
        frames = [b"first" * 12, b"other" * 12]
        for frame in frames: buffers.queue_tap_frame(frame)
        written = []
        while buffers.controls: buffers.write_kiss(len)
        buffers.write_kiss(lambda data: written.append(data) or len(data))
        self.assertFalse(buffers.can_write_kiss())
        buffers.feed_kiss(b"\xc0\x06\xf8\x00\xc0")
        self.assertTrue(buffers.can_write_kiss())
        self.assertEqual(buffers.stats()["modem_tx_error"], 1)
        self.assertEqual(buffers.stats()["modem_tx_done"], 0)
        buffers.write_kiss(lambda data: written.append(data) or len(data))
        self.assertEqual(written, [kiss_encode(frame, port=1) for frame in frames])
        self.assertEqual(list(buffers.to_kiss), [])
        self.assertEqual(buffers.stats()["kiss_written"], 2)
        self.assertTrue(buffers.stats()["awaiting_tx_done"])
        buffers.feed_kiss(b"\xc0\x06\xf8\x01\xc0")
        self.assertEqual(buffers.stats()["modem_tx_done"], 1)
        self.assertFalse(buffers.stats()["awaiting_tx_done"])

    def test_unsolicited_tx_done_does_not_create_a_credit_or_change_default_tcp_flow(self):
        buffers = relay.RelayBuffers(kiss_modem=True)
        buffers.feed_kiss(b"\xc0\x06\xf8\x01\xc0")
        self.assertEqual(buffers.stats()["modem_unexpected_tx_done"], 1)
        default = relay.RelayBuffers()
        self.assertFalse(default.controls)
        default.queue_tap_frame(b"a" * 60); default.queue_tap_frame(b"b" * 60)
        default.write_kiss(len); default.write_kiss(len)
        self.assertEqual(default.stats()["kiss_written"], 2)
        self.assertFalse(default.stats()["awaiting_tx_done"])

    def test_phone_fixture_keeps_default_tcp_and_explicitly_forwards_serial_modem_options(self):
        source = (Path(relay.__file__).parent / "ethernet_ssh_phone_bench.sh").read_text()
        self.assertIn("kiss_port=0; baud=115200; serial_dtr=off; kiss_modem=off", source)
        self.assertIn('validate_endpoint(sys.argv[1], strict_tcp=True)', source)
        self.assertIn('--baud "$baud" --kiss-port "$kiss_port" --serial-dtr "$serial_dtr"', source)
        self.assertIn('modem_args+=(--kiss-modem)', source)
        self.assertIn('Server helper PID: %s', source)
        self.assertIn('"$observed" == "$inode"', source)

    def test_tcp_transport_defaults_to_dedicated_kiss_port(self):
        with patch.object(relay.socket, "create_connection") as connect:
            endpoint = relay.KissStream("tcp://bench-radio", 115200)
            connect.assert_called_once_with(("bench-radio", 5003), timeout=5)
            endpoint.close()

    def test_current_host_namespace_is_rejected_before_setns(self):
        identity = SimpleNamespace(st_dev=5, st_ino=700)
        with patch.object(relay.os, "open", return_value=88), \
                patch.object(relay.os, "O_CLOEXEC", 0, create=True), \
                patch.object(relay.os, "fstat", return_value=identity), \
                patch.object(relay.os, "stat", return_value=identity), \
                patch.object(relay.os, "close") as close, \
                patch.object(relay.os, "setns", create=True) as setns:
            with self.assertRaisesRegex(ValueError, "current host"):
                relay.enter_network_namespace("/proc/self/ns/net")
        setns.assert_not_called()
        close.assert_called_once_with(88)

    def test_distinct_namespace_is_entered_and_handle_closed(self):
        with patch.object(relay.os, "open", return_value=88), \
                patch.object(relay.os, "O_CLOEXEC", 0, create=True), \
                patch.object(relay.os, "fstat", return_value=SimpleNamespace(st_dev=5, st_ino=701)), \
                patch.object(relay.os, "stat", return_value=SimpleNamespace(st_dev=5, st_ino=700)), \
                patch.object(relay.os, "close") as close, \
                patch.object(relay.os, "setns", create=True) as setns:
            relay.enter_network_namespace("/var/run/netns/bench-a")
        setns.assert_called_once_with(88, 0x40000000)
        close.assert_called_once_with(88)

    def test_kiss_is_opened_before_namespace_or_tap(self):
        calls = []
        stream = Mock()
        with patch.object(relay, "KissStream", side_effect=lambda *args, **kwargs: calls.append("socket") or stream), \
                patch.object(relay, "enter_network_namespace", side_effect=lambda path: calls.append("netns")), \
                patch.object(relay, "create_tap", side_effect=lambda name: calls.append("tap") or (77, name)):
            self.assertEqual(relay.open_transport_then_tap("tcp://host:5003", 115200, "/ns", "lora0"),
                             (stream, 77, "lora0"))
        self.assertEqual(calls, ["socket", "netns", "tap"])
        stream.close.assert_not_called()

    def test_failed_namespace_or_tap_closes_kiss(self):
        for stage in ("enter_network_namespace", "create_tap"):
            stream = Mock()
            with patch.object(relay, "KissStream", return_value=stream), \
                    patch.object(relay, "enter_network_namespace"), \
                    patch.object(relay, "create_tap", return_value=(77, "lora0")), \
                    patch.object(relay, stage, side_effect=OSError("denied")):
                with self.assertRaises(OSError):
                    relay.open_transport_then_tap("tcp://host:5003", 115200, "/ns", "lora0")
            stream.close.assert_called_once()

    def test_partial_stream_writes_preserve_exact_frame(self):
        buffers = relay.RelayBuffers(1)
        frame = bytes(range(255))
        self.assertTrue(buffers.queue_tap_frame(frame))
        self.assertFalse(buffers.can_read_tap())
        with self.assertRaises(BufferError):
            buffers.queue_tap_frame(frame)
        written = bytearray()

        def write(data):
            written.extend(data[:3])
            return min(3, len(data))

        buffers.write_kiss(lambda data: 0)
        while buffers.to_kiss:
            buffers.write_kiss(write)
        self.assertTrue(buffers.can_read_tap())
        self.assertEqual(bytes(written), kiss_encode(frame))
        self.assertEqual(KissDecoder().feed(bytes(written)), [frame])
        self.assertEqual(buffers.stats()["kiss_written"], 1)

    def test_incoming_staging_applies_backpressure_without_losing_frames(self):
        buffers = relay.RelayBuffers(1)
        frames = [bytes((index,)) * 255 for index in range(3)]
        buffers.feed_kiss(b"".join(kiss_encode(frame) for frame in frames))
        self.assertEqual(len(buffers.to_tap), 1)
        self.assertTrue(buffers.staging)
        self.assertFalse(buffers.can_read_kiss())
        with self.assertRaises(BufferError):
            buffers.feed_kiss(b"more bytes")
        written = []
        for frame in frames:
            buffers.write_tap(lambda data: written.append(data) or len(data))
            buffers.parse_pending()
        self.assertEqual(written, frames)
        self.assertTrue(buffers.can_read_kiss())
        self.assertEqual(buffers.stats()["kiss_read"], 3)
        self.assertEqual(buffers.stats()["tap_written"], 3)

    def test_partial_tap_write_is_not_retried_as_a_new_frame(self):
        buffers = relay.RelayBuffers()
        buffers.feed_kiss(kiss_encode(b"x" * 60))
        with self.assertRaisesRegex(IOError, "partial TAP"):
            buffers.write_tap(lambda frame: 20)
        self.assertEqual(buffers.stats()["tap_written"], 0)
        self.assertEqual(len(buffers.to_tap), 1)

    def test_invalid_frame_sizes_are_dropped_not_truncated(self):
        buffers = relay.RelayBuffers()
        for size in (0, 13, 256, 1500):
            self.assertFalse(buffers.queue_tap_frame(b"x" * size))
        self.assertEqual(len(buffers.to_kiss), 0)
        buffers.feed_kiss(kiss_encode(b"x" * 13))
        self.assertEqual(len(buffers.to_tap), 0)
        self.assertEqual(buffers.stats()["bad_frame_length"], 5)
        for size in (14, 255):
            self.assertTrue(buffers.queue_tap_frame(b"x" * size))

    def test_bad_stream_cannot_grow_unbounded(self):
        buffers = relay.RelayBuffers()
        with self.assertRaises(BufferError):
            buffers.feed_kiss(b"x" * 4097)
        buffers.feed_kiss(b"\xc0\0" + b"x" * 3500)
        self.assertEqual(buffers.staging, b"")
        self.assertLessEqual(len(buffers.decoder.buffer), 257)
        self.assertEqual(buffers.stats()["decode_errors"], 1)


if __name__ == "__main__":
    unittest.main()
