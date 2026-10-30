"""Offline one-port repeater broker tests, including binary/ASCII interleaving."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import queue
import struct
import threading
import time
import unittest
from unittest import mock
import zlib

import lora_ota as ota
from ota_wizard_repeater import RepeaterError, RepeaterTransport, SeederData, StreamDecoder
from test_lora_ota import firmware, mota_blob


SOURCE = "a" * 64
TARGET = "b" * 64


def request(op, args=b""):
    checksum = op
    for byte in args:
        checksum ^= byte
    return b"MS" + bytes((op,)) + args + bytes((checksum,))


def package():
    image = firmware(b"repeating application bytes" * 100, 0x01120001)
    return mota_blob(image, version=0x01120001)


class SeederTest(unittest.TestCase):
    def test_exact_wire_geometry_read_limits_and_checksum_independent_payload(self):
        blob = package()
        core = SeederData(blob)
        self.assertEqual(core.request(1, b""), (0, b"\1"))
        status, desc = core.request(2, b"\0")
        self.assertEqual(status, 0)
        self.assertEqual(len(desc), 38)
        self.assertEqual(desc[:4], core.info.merkle_root)
        self.assertEqual(struct.unpack_from("<I", desc, 14)[0], len(blob))
        self.assertEqual(struct.unpack_from("<I", desc, 26)[0], core.info.payload_offset)
        self.assertEqual(desc[35], 1)
        self.assertEqual(core.request(3, struct.pack("<BIH", 0, 7, 192)), (0, blob[7:199]))
        for index, offset, length in ((1, 0, 1), (0, 0, 193), (0, len(blob), 1), (0, 0, 0)):
            self.assertEqual(core.request(3, struct.pack("<BIH", index, offset, length)), (1, b""))
        for op in (4, 5, 6, 7, 8):
            self.assertEqual(core.request(op, b""), (1, b""))

    def test_deflate_is_independent_raw_rfc1951_and_chunks_are_bounded(self):
        core = SeederData(package())
        status, size = core.request(9, struct.pack("<BHHH", 0, 0, 0, 0))
        self.assertEqual(status, 0)
        count = int.from_bytes(size, "little")
        compressed = bytearray()
        while len(compressed) < count:
            length = min(190, count - len(compressed))
            status, chunk = core.request(9, struct.pack("<BHHH", 0, 0, len(compressed), length))
            self.assertEqual(status, 0)
            self.assertEqual(int.from_bytes(chunk[:2], "little"), count)
            compressed.extend(chunk[2:])
        self.assertEqual(zlib.decompress(compressed, -15), core.info.payload[:core.info.block_size])
        for args in ((0, 0, 0, 191), (1, 0, 0, 1), (0, 65535, 0, 0), (0, 0, 1, 0)):
            self.assertEqual(core.request(9, struct.pack("<BHHH", *args)), (1, b""))

    def test_decoder_fragmentation_text_false_magic_and_bad_checksum(self):
        stream = StreamDecoder()
        events = []
        wire = b"LOG MSG text\r\n" + request(3, struct.pack("<BIH", 0, 42, 12)) + b"  -> done\r\n"
        for byte in wire:
            events.extend(stream.feed(bytes((byte,))))
        self.assertEqual(events, [("line", "LOG MSG text"),
                                  ("request", 3, struct.pack("<BIH", 0, 42, 12)),
                                  ("line", "  -> done")])
        self.assertEqual(stream.feed(request(1)[:-1] + b"\0"), [])
        self.assertEqual(stream.feed(request(1)), [("request", 1, b"")])

    def test_decoder_expires_old_partial_frame_without_dropping_fresh_data(self):
        stream = StreamDecoder(frame_timeout=1)
        with mock.patch("ota_wizard_repeater.time.monotonic", return_value=1):
            self.assertEqual(stream.feed(b"MS\3"), [])
        with mock.patch("ota_wizard_repeater.time.monotonic", return_value=3):
            self.assertEqual(stream.feed(request(1)), [("request", 1, b"")])


class FakeSerial:
    def __init__(self, **kwargs):
        self.is_open = False
        self.incoming = queue.Queue()
        self.pending = bytearray()
        self.commands = []
        self.writes = []
        self.binary = []
        self.port = None
        self.job = 0
        self.result = ""
        self.result_error = False
        self.result_pending = 0
        self.authenticated = False
        self.clock = None
        self.interleave = False
        self.attaching = False
        self.stale_job = False
        self.reject_login_once = False
        self.folder_off = False
        self.peer_type = 2
        self.peer_count = 1
        self.peer_key = TARGET
        self.known_repeater_supported = True
        self.routed_path_supported = False
        self.peer_path = "off"
        self.login_error = None
        self.command_results = {"ver": "v1.18.0.1", "stats-core": "x" * 150}

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def feed(self, data):
        self.incoming.put(bytes(data))

    @property
    def in_waiting(self):
        with self.incoming.mutex:
            return len(self.pending) + sum(len(chunk) for chunk in self.incoming.queue)

    def read(self, size):
        if not self.pending:
            try:
                self.pending.extend(self.incoming.get(timeout=.02))
            except queue.Empty:
                return b""
        data = bytes(self.pending[:size])
        del self.pending[:size]
        return data

    def answer(self, text):
        self.feed(("  -> " + text + "\r\n").encode())

    def start_job(self, result, error=False):
        self.job += 1
        self.result, self.result_error = result, error
        self.answer(f"OTApeer job={self.job:08X} state=pending")

    def write(self, raw):
        data = bytes(raw)
        self.writes.append(data)
        if data.startswith(b"ms"):
            checksum = 0
            for byte in data:
                checksum ^= byte
            assert checksum == 0
            self.binary.append(data)
            if self.attaching:
                if data[2] == 1:
                    assert data[3:5] == b"\0\1"
                    self.feed(request(2, b"\0"))
                elif data[2] == 2:
                    assert len(data) == 43
                    self.feed(request(3, struct.pack("<BIH", 0, 0, 16)))
                elif data[2] == 3:
                    self.attaching = False
                    self.answer("OK folder attached")
            return len(data)
        command = data.decode().strip("\r\n")
        self.commands.append(command)
        login_prefix = next((prefix for prefix in ("ota.peer login ", "ota.peer login.repeater ")
                             if command.startswith(prefix)), None)
        if login_prefix:
            key, password = command[len(login_prefix):].split(" ", 1)
            self.feed((f"{login_prefix}{key} " + "*" * len(password.encode()) + "\r\n").encode())
        else:
            self.feed((command + "\r\n").encode())
        if command == "get public.key":
            self.answer("> " + SOURCE)
        elif command == "ota.peer status":
            self.answer(f"OTApeer v=1 session={int(self.authenticated)} busy=0 peers={self.peer_count}")
        elif command == "ota.peer capabilities":
            self.answer(("OTApeer known_repeater=1" + (" routed_path=1" if self.routed_path_supported else ""))
                        if self.known_repeater_supported else "Unknown command")
        elif command == "ota.peer path":
            self.answer("OTApeer path=" + self.peer_path)
        elif command.startswith("ota.peer path "):
            self.peer_path = command.split(" ", 2)[2]
            self.answer("OTApeer path=" + self.peer_path)
        elif command == "clock":
            now = self.clock or datetime.now(timezone.utc)
            self.answer(f"{now.hour:02}:{now.minute:02} - {now.day}/{now.month}/{now.year} UTC")
        elif command.startswith("time "):
            self.answer("OK - clock set")
        elif command == "ota.peer discover":
            self.start_job("discovery complete")
        elif command == "ota.peer peers 0":
            self.answer(f"OTApeer peer=0 key={self.peer_key} type={self.peer_type} rssi=-61 snr=3")
        elif login_prefix or command.startswith("ota.peer acl "):
            if self.login_error:
                self.start_job(self.login_error, error=True)
            else:
                self.authenticated = True
                self.start_job("authenticated")
        elif command.startswith(("ota.peer cmd ", "ota.peer cmd.acl ")):
            if self.reject_login_once:
                self.reject_login_once = False
                self.authenticated = False
                self.answer("ERR peer login required")
            else:
                text = command.split(" ", 3)[3]
                result = self.command_results.get(text, "OK")
                self.start_job(result, self.result_error)
        elif command.startswith("ota.peer result"):
            offset = int(command.split()[-1]) if len(command.split()) == 3 else 0
            if self.interleave:
                self.feed(request(3, struct.pack("<BIH", 0, 4, 32)))
            if self.result_pending:
                self.result_pending -= 1
                self.answer(f"OTApeer job={self.job:08X} state=pending bytes=0 off=0 data=-")
            else:
                chunk = self.result.encode()[offset:offset + 48]
                job = 0xFFFF if self.stale_job else self.job
                state = "error" if self.result_error else "ok"
                self.answer(f"OTApeer job={job:08X} state={state} bytes={len(self.result.encode())} off={offset} data={chunk.hex() or '-'}")
        elif command == "ota folder on":
            self.attaching = True
            self.feed(request(1))
        elif command == "ota folder off":
            self.folder_off = True
            self.answer("OK folder detached")
        elif command == "ota.peer close":
            self.answer("OTApeer closed")
        elif command == "get radio":
            self.answer("> 910.525,62.5,7,5")
        elif command.startswith("set radio "):
            self.answer("OK - applying in 2s")
        else:
            self.answer("Unknown command")
        return len(data)


class RepeaterTransportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.serial = FakeSerial()
        self.link = RepeaterTransport("fake", serial_factory=lambda **kwargs: self.serial,
            open_settle=0, command_timeout=.5, remote_timeout=.8, poll_seconds=.001)
        await self.link.open()

    async def asyncTearDown(self):
        await self.link.close()

    async def test_source_identity_and_zero_hop_discovery(self):
        self.assertEqual(self.link.source_key, SOURCE)
        self.serial.result_pending = 1
        peers = await self.link.discover()
        self.assertEqual(peers, [{"public_key": TARGET, "type": 2, "rssi": -61, "snr": 3}])

    async def open_known_repeater(self, *, acl_only=False):
        supported, peer_count = self.serial.known_repeater_supported, self.serial.peer_count
        routed = self.serial.routed_path_supported
        await self.link.close()
        self.serial = FakeSerial()
        self.serial.known_repeater_supported, self.serial.peer_count = supported, peer_count
        self.serial.routed_path_supported = routed
        self.link = RepeaterTransport("fake", serial_factory=lambda **kwargs: self.serial,
            open_settle=0, command_timeout=.5, remote_timeout=.8, poll_seconds=.001,
            known_repeater=TARGET, acl_only=acl_only)
        await self.link.open()

    async def test_known_repeater_authenticates_without_discovery_and_accepts_empty_password(self):
        self.serial.peer_count = 0
        await self.open_known_repeater()
        self.serial.commands.clear()
        self.assertEqual(await self.link.remote_command(TARGET, "", "ver"), "v1.18.0.1")
        self.assertIn(f"ota.peer login.repeater {TARGET} ", self.serial.commands)
        self.assertFalse(any(command.startswith(("ota.peer discover", "ota.peer peers", "ota.peer login "))
                             for command in self.serial.commands))
        self.assertEqual(self.link._authenticated_key, TARGET)
        before = len(self.serial.commands)
        with self.assertRaisesRegex(RepeaterError, "explicit known repeater key"):
            await self.link.remote_command("c" * 64, "", "ver")
        with self.assertRaisesRegex(RepeaterError, "complete destination public key"):
            await self.link.remote_command("", "", "ver")
        self.assertEqual(len(self.serial.commands), before)

    async def test_acl_only_requires_known_key_and_rejects_password_before_transmitting(self):
        with self.assertRaisesRegex(RepeaterError, "complete known repeater key"):
            RepeaterTransport("fake", acl_only=True)
        await self.open_known_repeater(acl_only=True)
        before = len(self.serial.commands)
        with self.assertRaisesRegex(RepeaterError, "empty password"):
            await self.link.remote_command(TARGET, "password", "ver")
        with self.assertRaisesRegex(RepeaterError, "explicit known repeater key"):
            await self.link.remote_command("c" * 64, "", "ver")
        self.assertEqual(len(self.serial.commands), before)
        self.assertIsNone(self.link._authenticated_key)

    async def test_acl_only_sends_one_direct_command_without_login_or_proof(self):
        await self.open_known_repeater(acl_only=True)
        self.serial.commands.clear()
        self.assertEqual(await self.link.remote_command(TARGET, "", "ver"), "v1.18.0.1")
        self.assertEqual(self.serial.commands.count(f"ota.peer cmd.acl {TARGET} ver"), 1)
        self.assertFalse(any(command.startswith(("ota.peer login", "ota.peer acl ", "ota.peer discover", "ota.peer peers"))
                             for command in self.serial.commands))
        self.assertIsNone(self.link._authenticated_key)
        self.assertEqual(self.link._last_peer_use, 0)
        await self.link.verify_peer(TARGET, "")
        self.assertEqual(self.link._authenticated_key, TARGET)
        await self.link.remote_command(TARGET, "", "ver")
        self.assertIsNone(self.link._authenticated_key)
        self.assertEqual(self.link._last_peer_use, 0)
        self.serial.reject_login_once = True
        before = len(self.serial.commands)
        with self.assertRaisesRegex(RepeaterError, "ERR peer login required"):
            await self.link.remote_command(TARGET, "", "ota install")
        attempted = self.serial.commands[before:]
        self.assertEqual(sum(command.startswith("ota.peer cmd.acl ") for command in attempted), 1)
        self.assertFalse(any(command.startswith(("ota.peer login", "ota.peer acl ", "ota.peer discover", "ota.peer cmd "))
                             for command in attempted))
        self.assertIsNone(self.link._authenticated_key)
        self.assertEqual(self.link._last_peer_use, 0)

    async def test_acl_only_read_retry_sends_no_proof_and_mutations_are_not_replayed(self):
        await self.open_known_repeater(acl_only=True)
        self.serial.commands.clear()
        self.serial.command_results["ver"] = "request timed out; result is unknown"
        self.serial.result_error = True
        with self.assertRaises(ota.TransmissionError):
            await self.link.remote_command(TARGET, "", "ver")
        self.assertEqual(self.serial.commands.count(f"ota.peer cmd.acl {TARGET} ver"), 1)
        self.serial.command_results["ver"] = "v1.18.0.1"
        self.serial.result_error = False
        self.assertEqual(await self.link.remote_command(TARGET, "", "ver"), "v1.18.0.1")
        self.assertEqual(self.serial.commands.count(f"ota.peer cmd.acl {TARGET} ver"), 2)
        self.serial.command_results["ota install"] = "request timed out; result is unknown"
        self.serial.result_error = True
        with self.assertRaises(ota.TransmissionError):
            await self.link.remote_command(TARGET, "", "ota install")
        self.assertEqual(self.serial.commands.count(f"ota.peer cmd.acl {TARGET} ota install"), 1)
        self.assertFalse(any(command.startswith(("ota.peer login", "ota.peer acl ", "ota.peer discover"))
                             for command in self.serial.commands))
        self.assertIsNone(self.link._authenticated_key)
        self.assertEqual(self.link._last_peer_use, 0)

    async def test_verify_peer_always_uses_fresh_proof_without_extra_cli_read(self):
        for mode in ("discovery", "known", "acl"):
            with self.subTest(mode=mode):
                if mode != "discovery":
                    await self.open_known_repeater(acl_only=mode == "acl")
                await self.link.verify_peer(TARGET, "")
                self.serial.commands.clear()
                self.assertEqual(await self.link.verify_peer(TARGET.upper(), ""), TARGET)
                method = "acl" if mode == "acl" else "login.repeater" if mode == "known" else "login"
                self.assertEqual(sum(command.startswith(f"ota.peer {method} ") for command in self.serial.commands), 1)
                self.assertFalse(any(command.startswith(("ota.peer cmd ", "ota.peer cmd.acl ")) for command in self.serial.commands))
                if mode == "acl":
                    self.assertFalse(any(command.startswith("ota.peer login") for command in self.serial.commands))
                self.assertEqual(self.link._authenticated_key, TARGET)
                self.assertGreater(self.link._last_peer_use, 0)

                self.serial.login_error = "login identity mismatch"
                self.serial.commands.clear()
                with self.assertRaisesRegex(RepeaterError, "login identity mismatch"):
                    await self.link.verify_peer(TARGET, "")
                self.assertIsNone(self.link._authenticated_key)
                self.assertEqual(self.link._last_peer_use, 0)
                self.assertFalse(any(command.startswith(("ota.peer cmd ", "ota.peer cmd.acl ")) for command in self.serial.commands))

    async def test_verify_peer_validates_key_and_password_before_transmitting(self):
        await self.open_known_repeater(acl_only=True)
        before = len(self.serial.commands)
        for key, password in (("", ""), ("bad", ""), ("c" * 64, ""),
                              (TARGET, "password"), (TARGET, "bad\npassword"),
                              (TARGET, "x" * 64)):
            with self.subTest(key=key, password=password):
                with self.assertRaises(RepeaterError):
                    await self.link.verify_peer(key, password)
        self.assertEqual(len(self.serial.commands), before)
        self.assertIsNone(self.link._authenticated_key)

    async def test_known_repeater_masks_password_and_does_not_fall_back_after_denial(self):
        await self.open_known_repeater()
        self.serial.commands.clear()
        self.assertEqual(await self.link.remote_command(TARGET, "secret with spaces", "ver"), "v1.18.0.1")
        self.assertIn(f"ota.peer login.repeater {TARGET} secret with spaces", self.serial.commands)
        await self.link.reset_remote_session(TARGET)
        self.serial.login_error = "admin login rejected"
        before = len(self.serial.commands)
        with self.assertRaisesRegex(RepeaterError, "admin login rejected"):
            await self.link.remote_command(TARGET, "", "ota install")
        self.assertIsNone(self.link._authenticated_key)
        attempted = self.serial.commands[before:]
        self.assertFalse(any(command.startswith(("ota.peer discover", "ota.peer login ", "ota.peer cmd "))
                             for command in attempted))

    async def test_known_repeater_requires_explicit_firmware_capability(self):
        self.serial.known_repeater_supported = False
        with self.assertRaisesRegex(RepeaterError, "Update the USB source firmware"):
            await self.open_known_repeater()
        self.assertFalse(self.serial.is_open)

    async def test_routed_path_capability_is_explicit_and_legacy_source_still_opens(self):
        await self.open_known_repeater(acl_only=True)
        self.assertFalse(self.link.supports_routed_path)
        self.serial.routed_path_supported = True
        await self.open_known_repeater(acl_only=True)
        self.assertTrue(self.link.supports_routed_path)
        await self.link.close()
        self.assertFalse(self.link.supports_routed_path)

    async def test_routed_path_capability_rejects_unknown_duplicate_and_malformed_fields(self):
        for value in ("OTApeer known_repeater=0 routed_path=1", "OTApeer known_repeater=1 routed_path=0",
                      "OTApeer known_repeater=1 routed_path=1 routed_path=1", "OTApeer routed_path=1",
                      "OTApeer known_repeater=1 arbitrary=1", "OTApeer known_repeater=1 routed_path=1x"):
            with self.subTest(value=value):
                await self.link.close()
                self.serial = FakeSerial()
                answer = self.serial.answer
                def capabilities(text):
                    answer(value if text == "OTApeer known_repeater=1" else text)
                self.serial.answer = capabilities
                self.link = RepeaterTransport("fake", serial_factory=lambda **kwargs: self.serial,
                    open_settle=0, command_timeout=.5, known_repeater=TARGET, acl_only=True)
                with self.assertRaisesRegex(RepeaterError, "Update the USB source firmware"):
                    await self.link.open()
                self.assertFalse(self.link.supports_routed_path)
                self.assertFalse(self.serial.is_open)
        self.assertFalse(any(command.startswith("ota.peer login.repeater") for command in self.serial.commands))

    async def test_direct_mode_refuses_existing_native_route_without_clearing_or_using_it(self):
        await self.link.close()
        self.serial = FakeSerial()
        self.serial.routed_path_supported = True
        self.serial.peer_path = "ABCDEF,123456"
        self.link = RepeaterTransport("fake", serial_factory=lambda **kwargs: self.serial,
            open_settle=0, command_timeout=.5, known_repeater=TARGET, acl_only=True)
        with self.assertRaisesRegex(RepeaterError, "routed path.*direct update"):
            await self.link.open()
        self.assertEqual(self.serial.peer_path, "ABCDEF,123456")
        self.assertEqual(self.serial.commands.count("ota.peer path"), 1)
        self.assertFalse(any(text.startswith(("ota.peer path ", "ota.peer cmd", "ota.peer acl ",
                                              "ota.peer login", "ota.peer discover"))
                             for text in self.serial.commands))
        self.assertFalse(self.serial.is_open)

    async def test_direct_mode_checks_new_source_route_without_extra_legacy_query(self):
        for routed in (False, True):
            with self.subTest(routed=routed):
                self.serial.routed_path_supported = routed
                await self.open_known_repeater(acl_only=True)
                self.assertEqual(self.serial.commands.count("ota.peer path"), int(routed))
                self.assertEqual(await self.link.remote_command(TARGET, "", "ver"), "v1.18.0.1")
                self.assertEqual(self.serial.commands.count(f"ota.peer cmd.acl {TARGET} ver"), 1)

    async def test_routed_transport_uses_its_own_recovery_route_guard(self):
        from ota_wizard_multihop import RoutedRepeaterTransport
        await self.link.close()
        self.serial = FakeSerial()
        self.serial.routed_path_supported = True
        self.serial.peer_path = "CCCCCC"
        self.link = RoutedRepeaterTransport("fake", known_repeater=TARGET, relay_keys=["c" * 64],
            recovering=True, expected_source_key=SOURCE, open_settle=0, command_timeout=.5,
            serial_factory=lambda **kwargs: self.serial)
        self.assertIs(await self.link.open(), self.link)
        self.assertEqual(self.serial.commands.count("ota.peer path"), 1)
        self.assertTrue(self.link._path_owned)
        self.assertEqual(self.serial.peer_path, "CCCCCC")
        await self.link.close()
        self.assertEqual(self.serial.peer_path, "off")

    async def test_owned_return_path_failure_is_transient_without_replaying_commands(self):
        await self.open_known_repeater()
        self.serial.commands.clear()
        self.serial.login_error = "login return path transmission failed"
        with self.assertRaisesRegex(ota.TransmissionError, "return path transmission failed"):
            await self.link.remote_command(TARGET, "", "ota install")
        self.assertIsNone(self.link._authenticated_key)
        self.assertEqual(sum(text.startswith("ota.peer login.repeater") for text in self.serial.commands), 1)
        self.assertFalse(any(text.startswith("ota.peer cmd ") for text in self.serial.commands))
        # The caller decides whether to retry a read; the broker itself sends
        # no second login and never replays the unadmitted install command.
        self.serial.login_error = None
        self.assertEqual(await self.link.remote_command(TARGET, "", "ver"), "v1.18.0.1")
        self.assertEqual(sum(text.startswith("ota.peer login.repeater") for text in self.serial.commands), 2)
        self.assertFalse(any(text.endswith("ota install") for text in self.serial.commands))

    async def test_permission_and_identity_failures_remain_permanent(self):
        await self.open_known_repeater()
        for error in ("admin login rejected", "invalid login identity proof", "login identity mismatch",
                      "login return path transmission failed unexpectedly"):
            with self.subTest(error=error):
                self.serial.login_error = error
                before = len(self.serial.commands)
                with self.assertRaisesRegex(RepeaterError, error):
                    await self.link.remote_command(TARGET, "", "ver")
                attempted = self.serial.commands[before:]
                self.assertEqual(sum(text.startswith("ota.peer login.repeater") for text in attempted), 1)
                self.assertFalse(any(text.startswith("ota.peer cmd ") for text in attempted))
                self.assertIsNone(self.link._authenticated_key)

    async def test_discovery_mode_still_accepts_older_source_firmware(self):
        await self.link.close()
        self.serial = FakeSerial()
        self.serial.known_repeater_supported = False
        await self.link.open()
        self.assertEqual(await self.link.remote_command(TARGET, "", "ver"), "v1.18.0.1")
        self.assertIn("ota.peer discover", self.serial.commands)
        self.assertNotIn("ota.peer capabilities", self.serial.commands)

    async def test_password_echo_masking_and_full_multipart_result(self):
        reply = await self.link.remote_command(TARGET, "secret with spaces", "stats-core")
        self.assertEqual(reply, "x" * 150)
        self.assertEqual(sum(text.startswith("ota.peer login") for text in self.serial.commands), 1)
        await self.link.remote_command(TARGET, "secret with spaces", "ver")
        self.assertEqual(sum(text.startswith("ota.peer login") for text in self.serial.commands), 1)

    async def test_first_cli_reply_loss_reuses_recent_login_but_still_expires(self):
        for mode in ("discovery", "known"):
            with self.subTest(mode=mode):
                if mode != "discovery":
                    await self.open_known_repeater(acl_only=mode == "acl")
                self.link._clock_checked = True
                original_job = self.link._job
                lost_reply = False

                async def lose_first_cli_reply(command):
                    nonlocal lost_reply
                    result = await original_job(command)
                    if command.startswith("ota.peer cmd ") and not lost_reply:
                        lost_reply = True
                        raise ota.TransmissionError("request timed out; result is unknown")
                    return result

                # Replace this module's clock, leaving asyncio's real clock alone.
                with mock.patch("ota_wizard_repeater.time", wraps=time) as clock, \
                     mock.patch.object(self.link, "_job", side_effect=lose_first_cli_reply):
                    clock.monotonic.return_value = 1000
                    with self.assertRaises(ota.TransmissionError):
                        await self.link.remote_command(TARGET, "", "ver")
                    self.assertEqual(self.link._authenticated_key, TARGET)
                    self.assertEqual(self.link._last_peer_use, 1000)
                    self.assertEqual(sum(text.startswith("ota.peer cmd ") for text in self.serial.commands), 1)

                    clock.monotonic.return_value = 1001
                    self.assertEqual(await self.link.remote_command(TARGET, "", "ver"), "v1.18.0.1")
                    self.assertEqual(sum(text.startswith(("ota.peer login", "ota.peer acl ")) for text in self.serial.commands), 1)
                    self.assertEqual(self.link._last_peer_use, 1001)

                    clock.monotonic.return_value = 1242
                    self.assertEqual(await self.link.remote_command(TARGET, "", "ver"), "v1.18.0.1")
                    self.assertEqual(sum(text.startswith(("ota.peer login", "ota.peer acl ")) for text in self.serial.commands), 2)

    async def test_rejected_login_does_not_start_session_age(self):
        for mode in ("discovery", "known", "acl"):
            with self.subTest(mode=mode):
                if mode != "discovery":
                    await self.open_known_repeater(acl_only=mode == "acl")
                self.link._clock_checked = True
                self.serial.login_error = "admin login rejected"
                with mock.patch("ota_wizard_repeater.time", wraps=time) as clock:
                    clock.monotonic.return_value = 1000
                    with self.assertRaisesRegex(RepeaterError, "admin login rejected"):
                        if mode == "acl":
                            await self.link.verify_peer(TARGET, "")
                        else:
                            await self.link.remote_command(TARGET, "", "ver")
                self.assertIsNone(self.link._authenticated_key)
                self.assertEqual(self.link._last_peer_use, 0)
                self.assertFalse(any(text.startswith("ota.peer cmd ") for text in self.serial.commands))

    async def test_room_discovery_preserves_role_and_unknown_key_cannot_login(self):
        self.serial.peer_type = 3
        peers = await self.link.discover()
        self.assertEqual(peers[0]["type"], 3)
        await self.link.remote_command(TARGET, "password", "ver")
        self.assertTrue(any(text.startswith("ota.peer login") for text in self.serial.commands))
        await self.link.reset_remote_session(TARGET)
        before = len(self.serial.commands)
        self.serial.peer_key = "c" * 64
        with self.assertRaisesRegex(RepeaterError, "fresh direct discovery"):
            await self.link.remote_command(TARGET, "password", "ver")
        self.assertFalse(any(text.startswith("ota.peer login") for text in self.serial.commands[before:]))

    async def test_one_port_answers_binary_requests_during_remote_commands(self):
        stopped = asyncio.Event()
        serving = asyncio.create_task(self.link.serve(package(), stopped))
        for _ in range(100):
            if not self.serial.attaching and len(self.serial.binary) >= 3:
                break
            await asyncio.sleep(.01)
        self.assertFalse(serving.done())
        self.serial.interleave = True
        result = await self.link.remote_command(TARGET, "password", "stats-core")
        self.assertEqual(result, "x" * 150)
        self.assertTrue(any(reply[2] == 3 for reply in self.serial.binary))
        for reply in self.serial.binary:
            if reply[2] == 3:
                self.assertEqual(reply[3], 0)
        stopped.set()
        await asyncio.wait_for(serving, 1)
        self.assertTrue(self.serial.folder_off)
        self.assertIsNone(self.link._seeder)

    async def test_stale_job_and_unknown_results_do_not_resend_mutations(self):
        await self.link.remote_command(TARGET, "password", "ver")
        self.serial.stale_job = True
        with self.assertRaisesRegex(RepeaterError, "changed"):
            await self.link.remote_command(TARGET, "password", "ota install")
        self.assertEqual(sum(text.endswith("ota install") for text in self.serial.commands), 1)
        self.serial.stale_job = False
        self.serial.result_error = True
        self.serial.command_results["ota install"] = "request timed out; result is unknown"
        with self.assertRaises(ota.TransmissionError):
            await self.link.remote_command(TARGET, "password", "ota install")
        self.assertEqual(sum(text.endswith("ota install") for text in self.serial.commands), 2)

    async def test_explicit_login_required_allows_one_new_login(self):
        await self.link.remote_command(TARGET, "password", "ver")
        self.serial.reject_login_once = True
        self.assertEqual(await self.link.remote_command(TARGET, "password", "ver"), "v1.18.0.1")
        self.assertEqual(sum(text.startswith("ota.peer login") for text in self.serial.commands), 2)

    async def test_target_reboot_reset_discards_authentication_without_detaching_source(self):
        await self.link.remote_command(TARGET, "password", "ver")
        self.assertEqual(self.link._authenticated_key, TARGET)
        await self.link.reset_remote_session(TARGET)
        self.assertIsNone(self.link._authenticated_key)
        self.assertEqual(self.link.source_key, SOURCE)
        self.assertFalse(self.serial.folder_off)
        await self.link.remote_command(TARGET, "password", "ver")
        self.assertEqual(sum(text.startswith("ota.peer login") for text in self.serial.commands), 2)

    async def test_acl_reboot_reset_rebuilds_volatile_path_once_without_password_login(self):
        await self.open_known_repeater(acl_only=True)
        await self.link.remote_command(TARGET, "", "ver")
        self.serial.commands.clear()
        await self.link.reset_remote_session(TARGET.upper())
        commands = self.serial.commands
        self.assertEqual(commands[0], "ota.peer close")
        self.assertEqual(commands.count(f"ota.peer acl {TARGET}"), 1)
        self.assertFalse(any(text.startswith(("ota.peer login", "ota.peer discover", "ota.peer cmd"))
                             for text in commands))
        self.assertFalse(self.serial.folder_off)
        self.assertEqual(self.link._authenticated_key, TARGET)
        self.serial.commands.clear()
        self.assertEqual(await self.link.remote_command(TARGET, "", "ver"), "v1.18.0.1")
        self.assertEqual(self.serial.commands.count(f"ota.peer cmd.acl {TARGET} ver"), 1)
        self.assertNotIn(f"ota.peer acl {TARGET}", self.serial.commands)

    async def test_acl_reboot_proof_failure_propagates_without_login_or_command_fallback(self):
        await self.open_known_repeater(acl_only=True)
        self.serial.login_error = "login identity mismatch"
        self.serial.commands.clear()
        with self.assertRaisesRegex(RepeaterError, "login identity mismatch"):
            await self.link.reset_remote_session(TARGET)
        self.assertEqual(self.serial.commands.count("ota.peer close"), 1)
        self.assertEqual(self.serial.commands.count(f"ota.peer acl {TARGET}"), 1)
        self.assertIsNone(self.link._authenticated_key)
        self.assertEqual(self.link._last_peer_use, 0)
        self.assertFalse(any(text.startswith(("ota.peer login", "ota.peer cmd"))
                             for text in self.serial.commands))

    async def test_known_password_reboot_reset_remains_close_only(self):
        await self.open_known_repeater()
        await self.link.remote_command(TARGET, "password", "ver")
        self.serial.commands.clear()
        await self.link.reset_remote_session(TARGET)
        self.assertEqual(self.serial.commands, ["ota.peer close"])
        self.assertIsNone(self.link._authenticated_key)

    async def test_reboot_reset_validates_exact_known_key_before_touching_native_session(self):
        await self.open_known_repeater(acl_only=True)
        self.serial.commands.clear()
        for key in ("", "bad", TARGET[:12], "c" * 64):
            with self.subTest(key=key), self.assertRaises(RepeaterError):
                await self.link.reset_remote_session(key)
        self.assertEqual(self.serial.commands, [])

    async def test_acl_reboot_reset_does_not_bootstrap_when_native_close_fails(self):
        await self.open_known_repeater(acl_only=True)
        with mock.patch.object(self.link, "local_command", new_callable=mock.AsyncMock,
                               return_value="ERR peer busy") as command:
            with self.assertRaisesRegex(RepeaterError, "ERR peer busy"):
                await self.link.reset_remote_session(TARGET)
        command.assert_awaited_once_with("ota.peer close")

    async def test_reboot_discovery_absence_is_recoverable_before_target_returns(self):
        await self.link.remote_command(TARGET, "password", "ver")
        await self.link.reset_remote_session(TARGET)
        self.serial.peer_count = 0
        with self.assertRaisesRegex(ota.OtaError, "fresh direct discovery"):
            await self.link.remote_command(TARGET, "password", "ver")
        self.assertIsNone(self.link._authenticated_key)
        self.assertEqual(sum(text.startswith("ota.peer login") for text in self.serial.commands), 1)
        self.serial.peer_count = 1
        self.assertEqual(await self.link.remote_command(TARGET, "password", "ver"), "v1.18.0.1")
        self.assertEqual(sum(text.startswith("ota.peer login") for text in self.serial.commands), 2)
        self.assertFalse(self.serial.folder_off)

    async def test_clock_is_advanced_when_old_and_future_clock_stops_before_login(self):
        self.serial.clock = datetime(2001, 1, 1, tzinfo=timezone.utc)
        await self.link.remote_command(TARGET, "password", "ver")
        self.assertTrue(any(text.startswith("time ") for text in self.serial.commands))
        self.link._clock_checked = False
        self.serial.clock = datetime(2099, 1, 1, tzinfo=timezone.utc)
        count = len(self.serial.commands)
        with self.assertRaisesRegex(RepeaterError, "ahead"):
            await self.link.remote_command(TARGET, "password", "ver")
        self.assertFalse(any(text.startswith("ota.peer login") for text in self.serial.commands[count:]))

    async def test_control_characters_and_oversize_are_rejected_before_writing(self):
        count = len(self.serial.commands)
        for text in ("ver\nreboot", "x" * 155):
            with self.assertRaises(RepeaterError):
                await self.link.remote_command(TARGET, "password", text)
        with self.assertRaises(RepeaterError):
            await self.link.remote_command(TARGET, "secret\rreboot", "ver")
        self.assertEqual(len(self.serial.commands), count)

    async def test_cancellation_detaches_folder_and_close_releases_serial(self):
        serving = asyncio.create_task(self.link.serve(package(), asyncio.Event()))
        for _ in range(100):
            if len(self.serial.binary) >= 3:
                break
            await asyncio.sleep(.01)
        serving.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await serving
        self.assertTrue(self.serial.folder_off)
        await self.link.close()
        self.assertFalse(self.serial.is_open)

    async def test_attach_reply_completion_cannot_swallow_server_cancellation(self):
        stop = asyncio.Event()
        original_get = self.link._lines.get
        async def cancel_at_attach_reply():
            line = await original_get()
            if "OK folder attached" in line:
                asyncio.get_running_loop().call_soon(serving.cancel)
            return line
        with mock.patch.object(self.link._lines, "get", side_effect=cancel_at_attach_reply):
            serving = asyncio.create_task(self.link.serve(package(), stop))
            try:
                done, _ = await asyncio.wait((serving,), timeout=2)
                self.assertIn(serving, done, "The stop request was swallowed by the completed attach reply")
                with self.assertRaises(asyncio.CancelledError):
                    await serving
            finally:
                stop.set()
                if not serving.done():
                    serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
        self.assertTrue(self.serial.folder_off)

    async def test_command_timeout_leaves_no_reader_to_steal_next_reply(self):
        original_timeout = self.link.command_timeout
        self.link.command_timeout = .01
        try:
            with mock.patch.object(self.link, "_write", new_callable=mock.AsyncMock):
                with self.assertRaises(TimeoutError):
                    await self.link.local_command("no reply")
        finally:
            self.link.command_timeout = original_timeout
        self.assertEqual(await self.link.local_command("get public.key"), "> " + SOURCE)


class BufferedReadTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class SizedReadSerial(FakeSerial):
            """Like pyserial, wait when a sized read has only partial data."""
            def __init__(self):
                super().__init__()
                self.read_sizes = []
                self.fill_wait = threading.Event()
                self.release_fill = threading.Event()
                self.binary_written = threading.Event()

            def read(self, size):
                self.read_sizes.append(size)
                data = bytearray(super().read(size))
                while data and len(data) < size:
                    if self.in_waiting:
                        data.extend(super().read(size - len(data)))
                    else:
                        self.fill_wait.set()
                        self.release_fill.wait(3)
                        break
                return bytes(data)

            def write(self, raw):
                count = super().write(raw)
                if bytes(raw).startswith(b"ms"):
                    self.binary_written.set()
                return count

        self.serial = SizedReadSerial()
        self.serial.open()
        self.link = RepeaterTransport("buffered", command_timeout=.5)
        self.link._serial = self.serial
        self.link._seeder = SeederData(package())
        self.link._reader_task = asyncio.create_task(self.link._read_loop())

    async def asyncTearDown(self):
        self.serial.release_fill.set()
        await self.link.close()

    async def test_fragmented_host_request_does_not_wait_for_a_full_read_buffer(self):
        frame = request(3, struct.pack("<BIH", 0, 4, 32))
        for chunk in (frame[:2], frame[2:5], frame[5:]):
            self.serial.feed(chunk)
            await asyncio.sleep(.01)
        answered = await asyncio.to_thread(self.serial.binary_written.wait, 2)
        self.assertTrue(answered, "A small host request waited for the serial read buffer to fill")
        self.assertFalse(self.serial.fill_wait.is_set())
        expected = b"ms\3\0" + package()[4:36]
        self.assertEqual(self.serial.binary[0][:-1], expected)

    async def test_large_uart_burst_is_drained_in_bounded_batches(self):
        self.serial.feed(b"log line\r\n" * 200 + request(1))
        self.assertTrue(await asyncio.to_thread(self.serial.binary_written.wait, 2))
        self.assertIn(511, self.serial.read_sizes)
        self.assertTrue(all(1 <= size <= 511 for size in self.serial.read_sizes))
        self.assertFalse(self.serial.fill_wait.is_set())


if __name__ == "__main__":
    unittest.main()
