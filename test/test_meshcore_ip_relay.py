"""Host-only RAW point IPv4 framing/MTU/state tests; no sockets, USB or TUN."""
import io
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "hil"))
import meshcore_ip_relay as ip


def packet(size=40, reverse=False, flags=0x4000, protocol=6, header=20):
    result = bytearray(size)
    result[0] = 0x40 | (header // 4)
    result[8], result[9] = 64, protocol
    result[12:16] = bytes((10, 90, 5, 2 if reverse else 1))
    result[16:20] = bytes((10, 90, 5, 1 if reverse else 2))
    result[header + 12] = 0x50
    struct.pack_into("!H", result, 2, size)
    struct.pack_into("!H", result, 6, flags)
    checksum = sum(struct.unpack("!" + "H" * (header // 2), result[:header]))
    while checksum >> 16:
        checksum = (checksum & 0xffff) + (checksum >> 16)
    struct.pack_into("!H", result, 10, ~checksum & 0xffff)
    return bytes(result)


def codec(path=None, back=0, **kwargs):
    return ip.IpCodec("10.90.5.1", "10.90.5.2", path or ip.MeshPath(), back,
                      session=0x11223344, **kwargs)


def response(frame):
    return b">" + struct.pack("<H", len(frame)) + frame


def incoming(size=40):
    peer = ip.IpCodec("10.90.5.2", "10.90.5.1", ip.MeshPath(), 0, session=7)
    return bytes((0x84, 0, 0, 255)) + peer.command(packet(size, reverse=True))[2:]


def started_buffers(**kwargs):
    buffers = ip.IpRelayBuffers(codec(), **kwargs)
    buffers.write_companion(lambda data: len(data))
    buffers.feed_companion(response(bytes((5, 0))))
    return buffers


class MeshCoreIpTests(unittest.TestCase):
    def test_optional_ready_callback_follows_real_app_start_reply_once(self):
        buffers = ip.IpRelayBuffers(codec())
        events = []
        iteration = [0]
        stream = mock.Mock(read_fd=77, write_fd=77)
        stream.write.side_effect = lambda data: events.append("app-start-written") or len(data)
        stream.read.side_effect = lambda count: events.append("self-info-received") or response(bytes((5, 0)))

        def ready(reads, writes, errors, timeout):
            iteration[0] += 1
            if iteration[0] == 1: return [], [77], []
            if iteration[0] == 2: return [77], [], []
            return [], [], []

        with mock.patch.object(ip.select, "select", side_effect=ready):
            stats = ip.relay(stream, 88, buffers, duration=1,
                stop=lambda: iteration[0] >= 4, on_ready=lambda: events.append("ready"))
        self.assertEqual(events, ["app-start-written", "self-info-received", "ready"])
        self.assertTrue(stats["ready"])

    def test_path_width_count_and_no_flood(self):
        self.assertEqual(ip.MeshPath.from_hex("aabbccdd", 2).encoded, 0x42)
        self.assertEqual(ip.MeshPath().encoded, 0)
        for data, width in ((bytes(64), 1), (bytes(65), 2), (bytes(3), 2), (b"", 4), (b"", 0)):
            with self.subTest(width=width, length=len(data)), self.assertRaises(ValueError):
                ip.MeshPath(data, width)
        self.assertEqual(ip.MeshPath(bytes(64), 2).encoded, 0x60)
        with self.assertRaises(ValueError):
            ip.MeshPath.from_hex("f")

    def test_mtu_derived_for_both_paths_and_host_frame_cap(self):
        self.assertEqual(ip.mtu_budget(0, 0), (156, 156))
        self.assertEqual(ip.mtu_budget(0, 64), (94, 94))
        self.assertEqual(ip.mtu_budget(64, 0), (94, 94))
        self.assertEqual(ip.mtu_budget(4, 2), (154, 154))
        self.assertEqual(ip.mtu_budget(0, 0, requested=230), (156, 156))
        self.assertEqual(ip.mtu_budget(0, 0, requested=128), (156, 128))
        self.assertEqual(ip.mtu_budget(64, 64, transport_bytes=4), (94, 94))
        for values in ((65, 0, None), (0, -1, None), (0, 0, 93)):
            with self.assertRaises(ValueError):
                ip.mtu_budget(*values)

    def test_command_wire_golden_matches_dart_and_nonces_change(self):
        link = codec(ip.MeshPath.from_hex("aabb", 2))
        first, second = link.command(packet()), link.command(packet())
        self.assertEqual(first[:16].hex(), "1941aabb535348014433221100000000")
        self.assertEqual(second[12:16], b"\x01\0\0\0")
        self.assertNotEqual(first, second)
        self.assertEqual(first[16:], packet())
        self.assertEqual(ip.companion_frame(first)[:3], b"<\x38\0")

    def test_sequence_exhaustion_never_wraps(self):
        link = codec(sequence=0xffffffff)
        self.assertEqual(link.command(packet())[10:14], b"\xff" * 4)
        with self.assertRaises(OverflowError):
            link.command(packet())

    def test_ipv4_limits_direction_checksum_fragments_protocol_and_padding(self):
        link = codec()
        self.assertEqual(len(link.command(packet(156))), 170)
        for value in (packet(157), packet(reverse=True), packet(flags=0x2000), packet(flags=1),
                      packet(flags=0x8000), packet(protocol=17), packet() + b"\0", packet()[:10]):
            with self.subTest(value=value[:10]), self.assertRaises(ValueError):
                link.command(value)
        damaged = bytearray(packet())
        damaged[8] ^= 1
        with self.assertRaises(ValueError):
            link.command(damaged)
        self.assertTrue(ip.valid_ipv4(packet(header=24, size=44), link.local, link.remote, 156))

    def test_raw_push_only_valid_peer_packet_not_text_or_other_apps(self):
        link = codec()
        self.assertEqual(link.raw_push(incoming()), packet(reverse=True))
        for frame in (incoming()[:35], b"\x08" + incoming()[1:], incoming()[:4] + b"OTHER" + incoming()[9:],
                      incoming() + b"\0", bytes((0x84, 0, 0, 255)) + link.command(packet())[2:]):
            self.assertIsNone(link.raw_push(frame))

    def test_stream_every_split_boundary_and_wrong_direction_length(self):
        wire = response(incoming())
        for split in range(len(wire) + 1):
            decoder = ip.CompanionDecoder()
            decoder.feed(wire[:split])
            first = decoder.pop()
            decoder.feed(wire[split:])
            self.assertEqual(first if first is not None else decoder.pop(), incoming())
            self.assertIsNone(decoder.pop())
        for wire in (b"<\x01\0x", b">\0\0", b">\xb1\0"):
            decoder = ip.CompanionDecoder()
            decoder.feed(wire)
            with self.assertRaises(ValueError):
                decoder.pop()
        with self.assertRaises(BufferError):
            ip.CompanionDecoder().feed(bytes(4097))

    def test_actual176byte_rx_limit_does_not_increase_command_or_ip_budget(self):
        buffers = started_buffers()
        for size in (173, 176):
            frame = bytes((0x88,)) + bytes(size - 1)
            buffers.feed_companion(response(frame))
        self.assertEqual(buffers.counters["ignored_frames"], 2)
        self.assertTrue(buffers.can_read_tun())
        self.assertEqual(codec().mtu, 156)
        with self.assertRaises(ValueError): ip.companion_frame(bytes(173))
        decoder = ip.CompanionDecoder()
        decoder.feed(response(bytes(177)))
        with self.assertRaises(ValueError): decoder.pop()

    def test_serial_printable_banner_is_bounded_and_only_before_binary(self):
        decoder = ip.CompanionDecoder(ascii_preamble=True)
        banner = b"WELCOME HIL\r\n(+++MESHCORE-TERM-STOP returns to Binary mode)\r\n> "
        wire = banner + response(bytes((5, 0)))
        for byte in wire:
            decoder.feed(bytes((byte,)))
            result = decoder.pop()
        self.assertEqual(result, bytes((5, 0)))
        self.assertEqual(decoder.preamble_bytes, len(banner))
        decoder.feed(b"bad")
        with self.assertRaises(ValueError): decoder.pop()
        for wire, exception in ((b"bad\x00", ValueError), (b"a" * 4096, None)):
            decoder = ip.CompanionDecoder(ascii_preamble=True)
            decoder.feed(wire)
            if exception:
                with self.assertRaises(exception): decoder.pop()
            else:
                decoder.pop()
                decoder.feed(b"x")
                with self.assertRaises(BufferError): decoder.pop()

    def test_startup_and_one_acknowledged_command_at_a_time(self):
        buffers = ip.IpRelayBuffers(codec())
        self.assertFalse(buffers.can_read_tun())
        self.assertEqual(buffers.output[:4], b"<\x13\0\x01")
        buffers.write_companion(lambda data: len(data))
        buffers.feed_companion(response(bytes((5, 0))))
        self.assertTrue(buffers.can_read_tun())
        self.assertTrue(buffers.queue_tun(packet()))
        with self.assertRaises(BufferError):
            buffers.queue_tun(packet())
        buffers.write_companion(lambda data: 2)
        self.assertTrue(buffers.output)
        buffers.write_companion(lambda data: len(data))
        buffers.feed_companion(response(b"\0"))
        self.assertTrue(buffers.can_read_tun())
        self.assertEqual(buffers.stats()["accepted_commands"], 1)
        self.assertIn("not RF-delivered", buffers.stats()["acceptance_note"])

    def test_partial_stream_continues_reading_and_receive_queue_backpressures(self):
        buffers = started_buffers(depth=1)
        wire = response(incoming())
        buffers.feed_companion(wire[:1])
        self.assertTrue(buffers.can_read_companion())
        buffers.feed_companion(wire[1:] + wire)
        self.assertEqual(len(buffers.to_tun), 1)
        self.assertFalse(buffers.can_read_companion())
        buffers.write_tun(lambda data: len(data))
        buffers.parse_pending()
        self.assertEqual(len(buffers.to_tun), 1)
        self.assertFalse(buffers.decoder.staging)
        with self.assertRaises(IOError):
            buffers.write_tun(lambda data: 1)

    def test_error_acceptance_invalid_packet_and_deadline_are_honest(self):
        now = [0.]
        buffers = started_buffers(now=lambda: now[0])
        self.assertFalse(buffers.queue_tun(b"bad IPv4"))
        buffers.queue_tun(packet())
        buffers.write_companion(lambda data: len(data))
        buffers.feed_companion(response(b"\x01\x03"))
        self.assertEqual(buffers.stats()["rejected_commands"], 1)
        buffers.queue_tun(packet())
        now[0] = 12
        with self.assertRaises(TimeoutError):
            buffers.check_deadline()

    def test_socket_before_namespace_before_new_tun_only(self):
        events = []
        stream = mock.Mock()
        with mock.patch.object(ip, "CompanionStream", side_effect=lambda endpoint, **kwargs: events.append("socket") or stream), \
             mock.patch.object(ip, "enter_network_namespace", side_effect=lambda path: events.append("namespace")), \
             mock.patch.object(ip, "create_tun", side_effect=lambda name: events.append("tun") or (99, "mcip0")), \
             mock.patch.object(ip.subprocess, "run") as run:
            self.assertEqual(ip.open_transport_then_tun("tcp://192.168.1.84:5000", "/var/run/netns/test", "mcip0",
                                                       "10.90.5.1", "10.90.5.2", 156), (stream, 99, "mcip0"))
            self.assertEqual(events, ["socket", "namespace", "tun"])
            self.assertEqual(run.call_args_list[1].args[0], ["ip", "address", "add", "10.90.5.1/32", "peer", "10.90.5.2/32", "dev", "mcip0"])
        stream.close.assert_not_called()

    def test_namespace_rejection_closes_own_socket_without_touching_tun(self):
        stream = mock.Mock()
        with mock.patch.object(ip, "CompanionStream", return_value=stream), \
             mock.patch.object(ip, "enter_network_namespace", side_effect=ValueError("same host")), \
             mock.patch.object(ip, "create_tun") as create:
            with self.assertRaises(ValueError):
                ip.open_transport_then_tun("endpoint", "host", "mcip0", "10.90.5.1", "10.90.5.2", 156)
        create.assert_not_called()
        stream.close.assert_called_once()


class CompanionStreamTests(unittest.TestCase):
    endpoint = "serial:///dev/serial/by-id/usb-RAK_USB_TEST_SERIAL-if00"
    identity = ("/dev/ttyACM3", "USB_TEST_SERIAL", 11, 22, 33)

    def test_windows_stream_shared_binary_transport_and_explicit_identity(self):
        shared = mock.Mock(read_fd=77, write_fd=78)
        payload = b"\0\xc0\xdb\xff"
        shared.read.return_value = payload
        shared.write.return_value = len(payload)
        with mock.patch.object(ip, "WindowsUsbStream", return_value=shared) as opened:
            stream = ip.CompanionStream("winusb://COM6", windows_python="/mnt/c/python.exe",
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
        with self.assertRaises(ValueError): ip.CompanionStream("winusb://COM6")
        with self.assertRaises(ValueError):
            ip.CompanionStream("tcp://192.168.1.87:5000", windows_python="/mnt/c/python.exe")

    def test_relay_selects_separate_pipe_read_and_write_descriptors(self):
        stream = mock.Mock(read_fd=77, write_fd=78)
        stream.write.side_effect = len
        stream.read.return_value = response(bytes((5, 0)))
        selections = []

        def selected(reads, writes, errors, timeout):
            selections.append((reads, writes))
            return ([], [78], []) if len(selections) == 1 else ([77], [], [])

        with mock.patch.object(ip.select, "select", side_effect=selected):
            stats = ip.relay(stream, 88, ip.IpRelayBuffers(codec()), duration=1, stop=lambda: len(selections) >= 2)
        self.assertEqual(selections, [([77], [78]), ([77], [])])
        self.assertTrue(stats["ready"])

    def test_tcp_defaults_unchanged_and_no_serial_open(self):
        sock = mock.Mock()
        sock.recv.return_value = b">\x01\0\0"
        sock.send.return_value = 2
        with mock.patch.object(ip.socket, "create_connection", return_value=sock) as connect, \
                mock.patch.object(ip, "serial_identity") as identity:
            stream = ip.CompanionStream("tcp://192.168.1.87:5000")
            self.assertEqual(stream.read(10), b">\x01\0\0")
            self.assertEqual(stream.write(b"<\x01\0\x01"), 2)
            stream.close()
        connect.assert_called_once_with(("192.168.1.87", 5000), timeout=5)
        identity.assert_not_called()
        sock.setblocking.assert_called_once_with(False)
        sock.close.assert_called_once()

    def test_unstable_alias_wrong_protocol_and_credentials_rejected_without_open(self):
        for endpoint in ("serial:///dev/ttyACM3", "serial:///dev/serial/by-path/port0",
                         "serial:///dev/serial/by-id/../../ttyACM3", "serial:///tmp/radio",
                         "tcp://192.168.1.87:5003", "tcp://user:secret@192.168.1.87:5000"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                ip.validate_companion_endpoint(endpoint)
        with self.assertRaises(ValueError):
            ip.validate_companion_endpoint("tcp://192.168.1.87:5000", expect_serial="9AB3B64C641BA927")

    def test_busy_serial_never_opens_or_changes_lines(self):
        with mock.patch.object(ip, "serial_identity", return_value=self.identity), \
                mock.patch.object(ip, "assert_port_idle", side_effect=RuntimeError("busy")), \
                mock.patch.object(ip, "open_configured_session") as opened:
            with self.assertRaisesRegex(RuntimeError, "busy"):
                ip.CompanionStream(self.endpoint)
        opened.assert_not_called()

    def test_exact_identity_exclusive_safe_dtr_selection_read_write_and_close(self):
        for selected in (False, True):
            port = mock.Mock()
            port.fileno.return_value = 77
            fake_serial = SimpleNamespace(Serial=mock.Mock(return_value=port))
            info = SimpleNamespace(st_mode=0o020600, st_dev=11, st_ino=22, st_rdev=33)
            with mock.patch.dict(sys.modules, serial=fake_serial), \
                    mock.patch.object(ip, "serial_identity", return_value=self.identity) as identity, \
                    mock.patch.object(ip, "assert_port_idle") as idle, \
                    mock.patch.object(ip, "open_configured_session") as opened, \
                    mock.patch.object(ip.os, "fstat", return_value=info), \
                    mock.patch.object(ip.os, "read", return_value=b">\x02\0\x05\0") as read, \
                    mock.patch.object(ip.os, "write", return_value=2) as write:
                stream = ip.CompanionStream(self.endpoint, baud=115200, serial_dtr=selected,
                                            expect_serial="9AB3B64C641BA927")
                self.assertEqual(stream.read(4096), b">\x02\0\x05\0")
                self.assertEqual(stream.write(b"<\x01\0\x01"), 2)
                stream.close()
            self.assertEqual(identity.call_count, 2)
            idle.assert_called_once_with("/dev/ttyACM3")
            opened.assert_called_once_with(port)
            self.assertEqual(port.port, "/dev/ttyACM3")
            self.assertTrue(port.exclusive)
            self.assertIs(port.dtr, selected)
            self.assertIs(port.rts, False)
            self.assertEqual((port.timeout, port.write_timeout), (0, 0))
            read.assert_called_once_with(77, 4096)
            write.assert_called_once_with(77, b"<\x01\0\x01")
            port.close.assert_called_once()

    def test_alias_change_after_open_closes_owned_port(self):
        port = mock.Mock()
        fake_serial = SimpleNamespace(Serial=mock.Mock(return_value=port))
        changed = ("/dev/ttyACM4", *self.identity[1:])
        with mock.patch.dict(sys.modules, serial=fake_serial), \
                mock.patch.object(ip, "serial_identity", side_effect=[self.identity, changed]), \
                mock.patch.object(ip, "assert_port_idle"), mock.patch.object(ip, "open_configured_session"), \
                mock.patch.object(ip.os, "fstat"):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                ip.CompanionStream(self.endpoint)
        port.close.assert_called_once()

    def test_wrong_usb_serial_and_alias_target_fail_closed(self):
        with mock.patch.object(ip.Path, "resolve", return_value=Path("/tmp/not-a-radio")):
            with self.assertRaisesRegex(ValueError, "ttyACM/ttyUSB"):
                ip.serial_identity("/dev/serial/by-id/radio", "EXPECTED")
        info = SimpleNamespace(st_mode=0o020600, st_dev=11, st_ino=22, st_rdev=33)
        with mock.patch.object(ip.Path, "resolve", side_effect=["/dev/ttyACM3", Path("/sys/devices/usb/1-1")]), \
                mock.patch.object(ip.os, "stat", return_value=info), \
                mock.patch.object(ip.Path, "is_file", return_value=True), \
                mock.patch.object(ip.Path, "read_text", return_value="WRONG"):
            with self.assertRaisesRegex(ValueError, "USB serial differs"):
                ip.serial_identity("/dev/serial/by-id/radio", "EXPECTED")


if __name__ == "__main__":
    unittest.main()
