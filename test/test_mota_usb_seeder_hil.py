"""Offline tests for the existing-protocol USB mOTA adapter; no radio access."""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/hil"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("mota_usb_under_test", TOOLS / "mota_usb_seeder.py")
USB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(USB)


def request(op, args=b""):
    return b"MS" + bytes([op]) + args + bytes([USB.xor(bytes([op]) + args)])


def stats():
    return {"requests": {}, "usb_received_bytes": 0, "usb_sent_bytes": 0,
            "error_responses": 0, "discarded_serial_bytes": 0,
            "bad_request_checksums": 0, "detach_sent": False}


class Clock:
    now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class MotaUsbTests(unittest.TestCase):
    def test_plan_without_dependencies_or_ports(self):
        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(USB, "prepare_catalog", side_effect=AssertionError):
            self.assertEqual(USB.main(["--plan"]), 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["install_commands"])
        self.assertEqual(report["zopfli_iterations"], 1000)

    def test_consent_and_fresh_report_required(self):
        with redirect_stderr(io.StringIO()), mock.patch.object(USB, "prepare_catalog", side_effect=AssertionError):
            with self.assertRaises(SystemExit):
                USB.main([])
            with self.assertRaises(SystemExit):
                USB.main(["--plan", "--duration", "nan"])

    def test_fragmented_resynchronization_exact_four_wire_requests(self):
        parser = USB.RequestParser()
        frames = [request(1), request(2, b"\x00"), request(3, b"\x00" + struct.pack("<IH", 8, 192)),
                  request(9, b"\x00" + struct.pack("<HHH", 19, 0, 0))]
        result = []
        for byte in b"hello\r\nM" + b"".join(frames):
            result += parser.feed(bytes([byte]))
        self.assertEqual(result, frames)
        self.assertLessEqual(len(parser.buffer), 11)

    def test_bad_checksum_resync_and_no_storage_write_support(self):
        parser = USB.RequestParser()
        self.assertEqual(parser.feed(b"MS\x01\x00" + request(1)), [request(1)])
        self.assertEqual(parser.bad_checksum, 1)
        with self.assertRaisesRegex(USB.Failure, "storage/write"):
            parser.feed(b"MS\x06")
        with self.assertRaises(USB.Failure):
            USB.RequestParser().feed(b"a" * 4097)

    def test_noise_budget_bounded(self):
        parser = USB.RequestParser()
        with self.assertRaisesRegex(USB.Failure, "budget"):
            for _ in range(18):
                parser.feed(b"a" * 4096)

    def test_cached_snapshot_retains_more_than16_blocks_without_recompression(self):
        offset, raw, count = 205, bytes(range(64)) * 20, 20
        descriptor = bytearray(38)
        struct.pack_into("<III", descriptor, 22, count, offset, len(raw))
        descriptor[34] = 6
        blob = b"h" * offset + raw + b"vk496"
        calls = []

        def compress(value, *, numiterations):
            calls.append(numiterations)
            return zlib.compress(value)[2:-4]

        cached = USB.CachedFile(blob, bytes(descriptor), compress)
        self.assertEqual(len(cached.blocks), 20)
        for index in (0, 19, 0, 18, 0):
            cached.deflated_block(index)
        self.assertEqual(calls, [1000] * 20)
        self.assertEqual(cached.path.open("rb").read(), blob)
        with self.assertRaises(USB.Failure):
            cached.path.open("wb")

    def test_real_catalog_golden_requests_verifies_and_ignores_mutated_original(self):
        sys.path.insert(0, str(ROOT / "tools/mota"))
        import motalib
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.mota"
            raw = b"USB source transport fixture. " * 40
            manifest = motalib.build_manifest(target_id=0x12345678, fw_version=0x01120001,
                image_size=len(raw), payload=raw, block_size=64, image_hash=motalib.mh32(raw),
                codec_id=motalib.CODEC_FULL, is_full=True)
            blob = motalib.build_container(manifest, raw)
            path.write_bytes(blob)
            catalog, details = USB.prepare_catalog(path)
            self.assertGreater(details["blocks"], 16)
            self.assertEqual(details["zopfli_iterations"], 1000)
            path.write_bytes(b"broken same-source replacement")
            count = catalog.handle_request(request(1))
            self.assertEqual(count, b"ms\x01\x00\x01" + bytes([USB.xor(b"ms\x01\x00\x01")]))
            desc = catalog.handle_request(request(2, b"\x00"))
            self.assertEqual(len(desc), 43)
            response = catalog.handle_request(request(3, b"\x00" + struct.pack("<IH", 0, 192)))
            self.assertEqual(response[4:-1], blob[:192])
            self.assertEqual(USB.xor(response), 0)
            # Every block is retained, so revisiting first after block19 is RAM-only.
            last = catalog.handle_request(request(9, b"\x00" + struct.pack("<HHH", details["blocks"] - 1, 0, 0)))
            first = catalog.handle_request(request(9, b"\x00" + struct.pack("<HHH", 0, 0, 0)))
            self.assertEqual(last[3], 0)
            self.assertEqual(first[3], 0)

    def test_invalid_container_refuses_before_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.mota"
            path.write_bytes(b"mOTA invalid")
            with self.assertRaises((ValueError, USB.Failure)):
                USB.prepare_catalog(path)

    def test_guard_live_primary_and_lease_all_fail_closed_without_setters(self):
        class Node:
            guard = USB.GUARD
            live = "> TempRadio active: 909.500,500.00,5,5 100s left,preamble=32"

            def __init__(self):
                self.commands = []

            def cli(self, command):
                self.commands.append(command)
                return {"get lab.tx": self.guard, "get tempradio": self.live,
                        "get radio": "910.525,62.5,7,5", "get radio2.status": "TX=0,0"}[command]

        node = Node()
        self.assertEqual(USB.source_safety(node, 60)["lease_seconds"], 100)
        for guard, live in (("unlocked", node.live), (USB.GUARD, "TempRadio pending: 909.5,500,5,5"),
                (USB.GUARD, "TempRadio active: 910.525,500,5,5 100s left"),
                (USB.GUARD, "TempRadio active: 909.5,500,7,5 100s left"),
                (USB.GUARD, "TempRadio active: 909.5,500,5,5 89s left")):
            node.guard, node.live = guard, live
            with self.assertRaises(USB.Failure):
                USB.source_safety(node, 60)
        self.assertTrue(all(command.startswith("get ") for command in node.commands))

    def test_attach_services_count_before_ack_then_serves_and_detaches(self):
        clock, state = Clock(), stats()

        class Catalog:
            def handle_request(self, frame):
                self.frame = frame
                return b"ms\x01\x00\x01\x1f"

        class Port:
            def __init__(self):
                self.incoming, self.writes = bytearray(), []

            @property
            def in_waiting(self):
                return len(self.incoming)

            def write(self, value):
                self.writes.append(value)
                if value.startswith(b"ota folder on"):
                    self.incoming.extend(request(1))
                elif value.startswith(b"ms"):
                    self.incoming.extend(b"\r\nOK folder attached (serial): advertising1/1\r\n")
                return len(value)

            def flush(self):
                pass

            def read(self, count):
                clock.now += .01
                value = bytes(self.incoming[:count])
                del self.incoming[:count]
                return value

        node = SimpleNamespace(port=Port())
        with mock.patch.object(USB.time, "monotonic", clock.monotonic), mock.patch.object(USB.time, "sleep", clock.sleep), redirect_stdout(io.StringIO()):
            USB.serve(node, Catalog(), .1, state)
        self.assertEqual(state["requests"], {"1": 1})
        self.assertTrue(state["detach_sent"])
        self.assertEqual(node.port.writes[0], b"+++MESHCORE-TERM-START\r")
        self.assertEqual(node.port.writes[-1], b"ota folder off\r\n")
        self.assertNotIn(b"install", b"".join(node.port.writes))

    def test_no_attach_ack_times_out_and_still_detaches(self):
        clock = Clock()

        class Port:
            in_waiting = 0
            writes = []

            def read(self, count):
                clock.now += .1
                return b""

            def write(self, value):
                self.writes.append(value)
                return len(value)

            def flush(self):
                pass

        with mock.patch.object(USB.time, "monotonic", clock.monotonic), mock.patch.object(USB.time, "sleep", clock.sleep):
            with self.assertRaisesRegex(USB.Failure, "attach timed out"):
                USB.serve(SimpleNamespace(port=Port()), None, .1, stats())
        self.assertEqual(Port.writes[-1], b"ota folder off\r\n")


if __name__ == "__main__":
    unittest.main()
