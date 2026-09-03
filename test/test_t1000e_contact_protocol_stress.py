#!/usr/bin/env python3
"""Offline behavioral tests for the focused T1000-E protocol HIL tool."""

import argparse
import asyncio
import importlib.util
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
HIL_DIR = ROOT / "tools" / "hil"
if str(HIL_DIR) not in sys.path:
    sys.path.insert(0, str(HIL_DIR))
TOOL_PATH = HIL_DIR / "t1000e_contact_protocol_stress.py"
SPEC = importlib.util.spec_from_file_location(
    "t1000e_contact_protocol_stress", TOOL_PATH
)
assert SPEC is not None and SPEC.loader is not None
HIL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HIL
SPEC.loader.exec_module(HIL)

TEST_NODE_KEY_PREFIX = "01234567"


def valid_config(**changes):
    values = dict(
        port="COM23",
        allow_destructive=True,
        confirm_usb_serial=HIL.EXPECTED_USB_SERIAL,
        expected_node_key_prefix=TEST_NODE_KEY_PREFIX,
        expected_contact_keyset_sha256=HIL.EXPECTED_KEYSET_SHA256,
        mutation_index=349,
        stream_trigger_contacts=12,
        settle_seconds=7.0,
        verbose=False,
    )
    values.update(changes)
    return HIL.Config(**values)


class SafetyGateTests(unittest.TestCase):
    def test_all_destructive_confirmations_are_mandatory(self):
        bad = (
            valid_config(allow_destructive=False),
            valid_config(confirm_usb_serial="WRONG"),
            valid_config(expected_node_key_prefix="deadbee"),
            valid_config(expected_node_key_prefix="not-hex!"),
            valid_config(expected_contact_keyset_sha256="00" * 32),
        )
        for config in bad:
            with self.subTest(config=config):
                with self.assertRaises(HIL.ProtocolHilFailure):
                    HIL.require_consent(config)

        HIL.require_consent(valid_config())

    def test_ranges_fail_closed(self):
        for config in (
            valid_config(mutation_index=-1),
            valid_config(mutation_index=350),
            valid_config(stream_trigger_contacts=0),
            valid_config(stream_trigger_contacts=101),
            valid_config(settle_seconds=0),
        ):
            with self.subTest(config=config):
                with self.assertRaises(HIL.ProtocolHilFailure):
                    HIL.require_consent(config)

    def test_port_is_required_outside_self_test(self):
        parser = HIL.build_argument_parser()
        args = parser.parse_args([])
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "--port"):
            HIL.config_from_args(args)


class ContactFrameTests(unittest.TestCase):
    def setUp(self):
        self.contact = HIL.BASE.make_contact(349)

    def test_encoder_matches_all_three_firmware_boundaries(self):
        with_lastmod = HIL.encode_contact_update(self.contact)
        gps_only = HIL.encode_contact_update(
            self.contact, include_lastmod=False
        )
        self.assertEqual(len(with_lastmod), HIL.CONTACT_UPDATE_LASTMOD_LEN)
        self.assertEqual(len(gps_only), HIL.CONTACT_UPDATE_GPS_LEN)
        self.assertEqual(with_lastmod[0], HIL.CMD_ADD_UPDATE_CONTACT)
        self.assertEqual(with_lastmod[1:33].hex(), self.contact["public_key"])
        self.assertEqual(with_lastmod[33], 1)
        self.assertEqual(with_lastmod[35], 0xFF)

    def test_case_matrix_covers_every_requested_forbidden_length(self):
        cases = HIL.malformed_cases(self.contact)
        lengths = {
            len(case.frame)
            for case in cases
            if case.name.startswith("forbidden_length_")
        }
        self.assertEqual(lengths, set(HIL.FORBIDDEN_CONTACT_UPDATE_LENGTHS))
        self.assertEqual(
            lengths,
            {135, *range(137, 144), *range(145, 148)},
        )
        self.assertTrue(
            all(case.frame[0] == HIL.CMD_ADD_UPDATE_CONTACT for case in cases)
        )

    def test_invalid_type_and_path_offsets_are_exact(self):
        cases = {case.name: case.frame for case in HIL.malformed_cases(self.contact)}
        for value in HIL.INVALID_CONTACT_TYPES:
            frame = cases[f"invalid_type_{value}"]
            self.assertEqual(len(frame), 148)
            self.assertEqual(frame[33], value)
        for value in HIL.INVALID_PATH_LENGTHS:
            frame = cases[f"invalid_path_{value:02x}"]
            self.assertEqual(len(frame), 148)
            self.assertEqual(frame[35], value)

    def test_illegal_arg_requires_error_code_six(self):
        HIL.require_illegal_arg(
            SimpleNamespace(
                type=SimpleNamespace(name="ERROR"), payload={"error_code": 6}
            ),
            "case",
        )
        for event in (
            SimpleNamespace(type=SimpleNamespace(name="OK"), payload={}),
            SimpleNamespace(
                type=SimpleNamespace(name="ERROR"), payload={"error_code": 5}
            ),
        ):
            with self.assertRaises(HIL.ProtocolHilFailure):
                HIL.require_illegal_arg(event, "case")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.contacts = {
            contact["public_key"]: contact
            for contact in (
                HIL.BASE.make_contact(index)
                for index in range(HIL.EXPECTED_CONTACT_COUNT)
            )
        }

    def test_exact_deterministic_table_has_locked_digest(self):
        result = HIL.validate_exact_snapshot(
            self.contacts, require_canonical_fields=True
        )
        self.assertEqual(result["count"], 350)
        self.assertEqual(result["keyset_sha256"], HIL.EXPECTED_KEYSET_SHA256)

    def test_missing_key_and_same_keys_changed_content_are_rejected(self):
        missing = dict(self.contacts)
        missing.pop(next(iter(missing)))
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "expected 350"):
            HIL.validate_exact_snapshot(missing, require_canonical_fields=True)

        changed = {key: dict(value) for key, value in self.contacts.items()}
        changed[next(iter(changed))]["adv_name"] = "mutated"
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "unexpected contact fields"):
            HIL.validate_exact_snapshot(changed, require_canonical_fields=True)

    def test_wire_digest_includes_lastmod_while_canonical_digest_does_not(self):
        baseline = {key: dict(value) for key, value in self.contacts.items()}
        changed = {key: dict(value) for key, value in baseline.items()}
        key = next(iter(changed))
        baseline[key]["lastmod"] = 100
        changed[key]["lastmod"] = 101

        self.assertEqual(
            HIL.semantic_digest(baseline), HIL.semantic_digest(changed)
        )
        self.assertNotEqual(
            HIL.wire_snapshot_digest(baseline),
            HIL.wire_snapshot_digest(changed),
        )


def fake_event(name, payload=None):
    return SimpleNamespace(
        type=SimpleNamespace(name=name),
        payload={} if payload is None else payload,
    )


class FakeMalformedCommands:
    def __init__(self):
        self.frames = []

    async def send(self, frame, expected_events, timeout):
        self.frames.append((bytes(frame), tuple(expected_events), timeout))
        return fake_event("ERROR", {"error_code": 6})


class FakeMalformedBase:
    def __init__(self, contacts):
        self._contacts = contacts
        self.mc = SimpleNamespace(commands=FakeMalformedCommands())
        self.contact_reads = 0

    async def contacts(self):
        self.contact_reads += 1
        return {key: dict(value) for key, value in self._contacts.items()}


class MalformedExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_rejection_is_followed_by_an_exact_inventory(self):
        contacts = {
            contact["public_key"]: contact
            for contact in (
                HIL.BASE.make_contact(index)
                for index in range(HIL.EXPECTED_CONTACT_COUNT)
            )
        }
        subject = HIL.ProtocolRunner(
            valid_config(), SimpleNamespace(serial_number=HIL.EXPECTED_USB_SERIAL)
        )
        fake = FakeMalformedBase(contacts)
        subject.base = fake

        await subject.test_malformed_updates(contacts)

        self.assertEqual(len(fake.mc.commands.frames), 17)
        self.assertEqual(fake.contact_reads, 17)
        self.assertEqual(subject.steps[-1]["step"], "malformed_contact_updates")
        self.assertTrue(subject.steps[-1]["unchanged_after_each"])
        self.assertTrue(
            all(frame[0] == HIL.CMD_ADD_UPDATE_CONTACT for frame, _, _ in fake.mc.commands.frames)
        )


class FakeStreamReader:
    def __init__(self, owner):
        self.owner = owner
        self.contact_nb = None
        self.contacts = {}

    async def handle_rx(self, raw):
        packet_type = raw[0]
        if packet_type == HIL.RESP_CODE_CONTACTS_START:
            self.contact_nb = int.from_bytes(raw[1:5], "little")
            self.contacts = {}
        elif packet_type == HIL.RESP_CODE_CONTACT:
            key = raw[1:33].hex()
            self.contacts[key] = dict(self.owner.table[key])


class FakeStreamCommands:
    def __init__(self, owner):
        self.owner = owner

    @staticmethod
    def _start(count):
        return bytes((HIL.RESP_CODE_CONTACTS_START,)) + struct.pack("<I", count)

    @staticmethod
    def _contact(key):
        return bytes((HIL.RESP_CODE_CONTACT,)) + bytes.fromhex(key) + b"\0" * 115

    async def get_contacts(self, *, lastmod, timeout):
        self.owner.reader.contacts = {}
        starting_revision = self.owner.revision
        await self.owner.reader.handle_rx(self._start(len(self.owner.table)))
        for key in sorted(self.owner.table)[:12]:
            await self.owner.reader.handle_rx(self._contact(key))
            await asyncio.sleep(0)

        deadline = asyncio.get_running_loop().time() + 1.0
        while self.owner.revision == starting_revision:
            if asyncio.get_running_loop().time() >= deadline:
                return fake_event("ERROR", {"reason": "mutation did not run"})
            await asyncio.sleep(0)

        # Both local mutation commands complete before this task is scheduled
        # again, representing the firmware's revision-triggered fresh snapshot.
        await self.owner.reader.handle_rx(self._start(len(self.owner.table)))
        for key in sorted(self.owner.table):
            await self.owner.reader.handle_rx(self._contact(key))
            await asyncio.sleep(0)
        await self.owner.reader.handle_rx(
            bytes((HIL.RESP_CODE_END_OF_CONTACTS,)) + b"\0" * 4
        )
        return fake_event("CONTACTS", dict(self.owner.reader.contacts))

    async def remove_contact(self, key):
        self.owner.table.pop(key)
        self.owner.revision += 1
        return fake_event("OK")

    async def add_contact(self, contact):
        self.owner.table[contact["public_key"]] = dict(contact)
        self.owner.revision += 1
        return fake_event("OK")

    async def send(self, frame, expected_events, timeout):
        self.owner.table[self.owner.original_target["public_key"]] = dict(
            self.owner.original_target
        )
        self.owner.revision += 1
        return fake_event("OK")


class FakeStreamMesh:
    def __init__(self, contacts):
        self.table = {key: dict(value) for key, value in contacts.items()}
        target_key = HIL.BASE.make_contact(349)["public_key"]
        self.original_target = dict(self.table[target_key])
        self.revision = 0
        self.reader = FakeStreamReader(self)
        self._reader = self.reader
        self.commands = FakeStreamCommands(self)


class StructuralExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_remove_readd_requires_raw_restart_and_exact_sdk_result(self):
        contacts = {
            contact["public_key"]: contact
            for contact in (
                HIL.BASE.make_contact(index)
                for index in range(HIL.EXPECTED_CONTACT_COUNT)
            )
        }
        subject = HIL.ProtocolRunner(
            valid_config(), SimpleNamespace(serial_number=HIL.EXPECTED_USB_SERIAL)
        )
        subject.base.mc = FakeStreamMesh(contacts)
        target_key = HIL.BASE.make_contact(349)["public_key"]
        subject.original_target_contact = dict(contacts[target_key])

        await subject.test_structural_restart()

        step = subject.steps[-1]
        self.assertEqual(step["step"], "structural_stream_restart")
        self.assertGreaterEqual(step["raw"]["fresh_starts"], 1)
        self.assertEqual(step["raw"]["completed_count"], 350)
        self.assertEqual(step["sdk"]["keyset_sha256"], HIL.EXPECTED_KEYSET_SHA256)
        self.assertEqual(subject.base.mc.revision, 2)


class FakeRestoreCommands:
    def __init__(self, owner):
        self.owner = owner
        self.sent_frames = []

    async def send(self, frame, expected_events, timeout):
        self.sent_frames.append(bytes(frame))
        self.owner.table[self.owner.target["public_key"]] = dict(self.owner.target)
        return fake_event("OK")

    async def get_autoadd_config(self):
        return fake_event("AUTOADD_CONFIG", {"config": self.owner.autoadd})

    async def send_appstart(self):
        return fake_event(
            "SELF_INFO", {"manual_add_contacts": self.owner.manual}
        )


class FakeRestoreBase:
    def __init__(self, table, target):
        self.table = {key: dict(value) for key, value in table.items()}
        self.target = dict(target)
        self.autoadd = 0
        self.manual = True
        self.reboots = 0
        self.mc = SimpleNamespace(commands=FakeRestoreCommands(self))

    @staticmethod
    def _require_event(event, expected):
        return HIL.require_event(event, expected, "fake")

    async def contacts(self):
        return {key: dict(value) for key, value in self.table.items()}

    async def reboot_and_reconnect(self):
        self.reboots += 1

    async def set_autoadd_config(self, value):
        self.autoadd = value

    async def set_manual_contact_mode(self, value):
        self.manual = value


class RestorationTests(unittest.IsolatedAsyncioTestCase):
    async def test_restoration_preserves_lastmod_reboots_and_restores_admission(self):
        table = {}
        for index in range(HIL.EXPECTED_CONTACT_COUNT):
            contact = HIL.BASE.make_contact(index)
            contact["lastmod"] = 10_000 + index
            table[contact["public_key"]] = contact
        target_key = HIL.BASE.make_contact(349)["public_key"]
        target = dict(table[target_key])
        table[target_key] = {**target, "lastmod": 999_999}

        subject = HIL.ProtocolRunner(
            valid_config(settle_seconds=0.001),
            SimpleNamespace(serial_number=HIL.EXPECTED_USB_SERIAL),
        )
        fake = FakeRestoreBase(table, target)
        subject.base = fake
        subject.restoration_armed = True
        subject.original_target_contact = target
        expected_table = {key: dict(value) for key, value in table.items()}
        expected_table[target_key] = target
        subject.baseline_wire_sha256 = HIL.wire_snapshot_digest(expected_table)
        subject.original_autoadd_config = 31
        subject.original_manual_add_contacts = False

        await subject.restore()

        self.assertEqual(fake.reboots, 1)
        self.assertEqual(fake.table[target_key]["lastmod"], target["lastmod"])
        self.assertEqual(len(fake.mc.commands.sent_frames), 1)
        self.assertEqual(len(fake.mc.commands.sent_frames[0]), 148)
        self.assertEqual(fake.autoadd, 31)
        self.assertFalse(fake.manual)
        self.assertEqual(
            [step["step"] for step in subject.steps[-3:]],
            [
                "contact_table_restored",
                "autoadd_config_restored",
                "contact_admission_restored",
            ],
        )


class RawContactStreamTests(unittest.TestCase):
    @staticmethod
    def start(count):
        return bytes((HIL.RESP_CODE_CONTACTS_START,)) + struct.pack("<I", count)

    @staticmethod
    def contact(key):
        return bytes((HIL.RESP_CODE_CONTACT,)) + bytes.fromhex(key) + b"\0" * 115

    def test_restart_discards_partial_segment_and_accepts_exact_final_snapshot(self):
        keys = sorted(HIL.deterministic_keys())
        observer = HIL.ContactStreamObserver(trigger_contacts=3)
        observer.observe(self.start(350))
        for key in keys[:3]:
            observer.observe(self.contact(key))
        self.assertTrue(observer.ready.is_set())
        marker = observer.frame_count

        # A remove can be observed as a transient 349-start before the re-add;
        # the next revision must restart again at the final count of 350.
        observer.observe(self.start(349))
        for key in keys[:2]:
            observer.observe(self.contact(key))
        observer.observe(self.start(350))
        for key in keys:
            observer.observe(self.contact(key))
        observer.observe(bytes((HIL.RESP_CODE_END_OF_CONTACTS,)) + b"\0" * 4)

        details = observer.validate_restart(set(keys), marker)
        self.assertEqual(details["starts"], 3)
        self.assertEqual(details["fresh_starts"], 2)
        self.assertEqual(details["start_counts"], [350, 349, 350])
        self.assertEqual(details["completed_count"], 350)
        self.assertEqual(details["aborted_contact_counts"], [3, 2])

    def test_no_fresh_start_and_duplicate_final_keys_fail(self):
        keys = sorted(HIL.deterministic_keys())
        no_restart = HIL.ContactStreamObserver(trigger_contacts=1)
        no_restart.observe(self.start(350))
        no_restart.observe(self.contact(keys[0]))
        marker = no_restart.frame_count
        no_restart.observe(bytes((HIL.RESP_CODE_END_OF_CONTACTS,)) + b"\0" * 4)
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "fresh CONTACTS_START"):
            no_restart.validate_restart(set(keys), marker)

        duplicate = HIL.ContactStreamObserver(trigger_contacts=1)
        duplicate.observe(self.start(350))
        duplicate.observe(self.contact(keys[0]))
        marker = duplicate.frame_count
        duplicate.observe(self.start(350))
        for key in keys[:-1]:
            duplicate.observe(self.contact(key))
        duplicate.observe(self.contact(keys[0]))
        duplicate.observe(bytes((HIL.RESP_CODE_END_OF_CONTACTS,)) + b"\0" * 4)
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "duplicate"):
            duplicate.validate_restart(set(keys), marker)

    def test_malformed_raw_sequence_is_rejected(self):
        observer = HIL.ContactStreamObserver(trigger_contacts=1)
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "without"):
            observer.observe(self.contact("11" * 32))
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "expected 5"):
            observer.observe(bytes((HIL.RESP_CODE_CONTACTS_START, 1)))

    def test_exact_contact_and_end_lengths_and_terminal_state_are_enforced(self):
        key = next(iter(HIL.deterministic_keys()))
        observer = HIL.ContactStreamObserver(trigger_contacts=1)
        observer.observe(self.start(350))
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "expected 148"):
            observer.observe(bytes((HIL.RESP_CODE_CONTACT,)) + bytes.fromhex(key))

        observer = HIL.ContactStreamObserver(trigger_contacts=1)
        observer.observe(self.start(350))
        observer.observe(self.contact(key))
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "expected 5"):
            observer.observe(bytes((HIL.RESP_CODE_END_OF_CONTACTS,)))
        observer.observe(bytes((HIL.RESP_CODE_END_OF_CONTACTS,)) + b"\0" * 4)
        with self.assertRaisesRegex(HIL.ProtocolHilFailure, "unexpected"):
            observer.observe(self.start(350))


class OfflineEntryPointTests(unittest.TestCase):
    def test_self_test_does_not_need_a_port(self):
        parser = HIL.build_argument_parser()
        args = parser.parse_args(["--self-test"])
        self.assertTrue(args.self_test)
        result = HIL.offline_self_test()
        self.assertTrue(result["ok"])
        self.assertEqual(result["malformed_cases"], 17)


if __name__ == "__main__":
    unittest.main()
