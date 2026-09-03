#!/usr/bin/env python3
"""Behavioral tests for the T1000-E ExtraFS host stress harness."""

import argparse
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "tools" / "hil" / "t1000e_extrafs_stress.py"
SPEC = importlib.util.spec_from_file_location("t1000e_extrafs_stress", HARNESS_PATH)
assert SPEC is not None and SPEC.loader is not None
HIL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HIL
SPEC.loader.exec_module(HIL)


def key(byte: int) -> str:
    return (bytes([byte]) * 32).hex()


def event(name: str, payload=None):
    return SimpleNamespace(
        type=SimpleNamespace(name=name), payload={} if payload is None else payload
    )


class FakeCommands:
    def __init__(self, owner, responses):
        self.owner = owner
        self.responses = list(responses)
        self.calls = []

    async def get_contacts(self, *, lastmod, timeout):
        self.calls.append((lastmod, timeout))
        contacts, advertised = self.responses.pop(0)
        self.owner._reader.contacts = dict(contacts)
        if advertised is not None:
            self.owner._reader.contact_nb = advertised
        return SimpleNamespace(
            type=SimpleNamespace(name="CONTACTS"), payload=dict(contacts)
        )


class FakeMeshCore:
    def __init__(self, responses):
        self._reader = SimpleNamespace(contact_nb=999, contacts={"stale": {}})
        self.commands = FakeCommands(self, responses)


def runner(responses, *, passes=None, digest=None):
    config = SimpleNamespace(
        contact_enumeration_passes=passes,
        expected_contact_keyset_sha256=digest,
        verbose=False,
    )
    identity = SimpleNamespace(serial_number=HIL.EXPECTED_USB_SERIAL)
    result = HIL.StressRunner(config, identity)
    result.mc = FakeMeshCore(responses)
    return result


class ContactEnumerationHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_snapshot_retries_a_short_stream(self):
        expected = {key(1): {}, key(2): {}}
        subject = runner([({key(1): {}}, 2), (expected, 2)])

        actual = await subject.contacts()

        self.assertEqual(set(actual), set(expected))
        self.assertEqual(subject.contact_enumeration_transactions, 2)
        self.assertEqual(subject.contact_enumeration_short_attempts, 1)
        self.assertEqual(subject.contact_enumeration_retries, 1)
        self.assertEqual(subject.mc.commands.calls, [(0, 20), (0, 20)])

    async def test_safe_snapshot_never_returns_three_short_streams(self):
        subject = runner([({key(1): {}}, 2)] * 3)

        with self.assertRaisesRegex(
            HIL.HilFailure, r"incomplete contact enumeration; attempt 1:"
        ):
            await subject.contacts()

        self.assertEqual(subject.contact_enumeration_transactions, 3)
        self.assertEqual(subject.contact_enumeration_short_attempts, 3)
        self.assertEqual(subject.contact_enumeration_retries, 2)

    async def test_missing_start_count_cannot_reuse_stale_reader_state(self):
        subject = runner([({key(1): {}}, None)])

        with self.assertRaisesRegex(HIL.HilFailure, "valid CONTACTS_START count"):
            await subject.contacts()

        self.assertIsNone(subject.mc._reader.contact_nb)

    async def test_repeated_stress_accepts_only_the_same_complete_keyset(self):
        expected = {key(1), key(2), key(3)}
        payload = {item: {} for item in expected}
        subject = runner([(payload, 3)] * 4, passes=4)

        actual = await subject.stress_contact_enumerations(
            expected, "unit-test table"
        )

        self.assertEqual(set(actual), expected)
        self.assertEqual(len(subject.mc.commands.calls), 4)
        step = subject.steps[-1]
        self.assertEqual(step["step"], "contact_enumeration_stress")
        self.assertTrue(step["ok"])
        self.assertEqual(step["completed_passes"], 4)
        self.assertEqual(step["transport_transactions"], 4)
        self.assertEqual(step["short_attempts"], 0)
        self.assertEqual(step["retry_attempts"], 0)
        self.assertEqual(step["advertised_counts"], [3, 3, 3, 3])
        self.assertEqual(step["received_counts"], [3, 3, 3, 3])

    async def test_stress_reports_retried_short_stream_and_fails(self):
        expected = {key(1), key(2)}
        complete = {item: {} for item in expected}
        subject = runner(
            [({key(1): {}}, 2), (complete, 2), (complete, 2)], passes=2
        )

        with self.assertRaisesRegex(
            HIL.HilFailure,
            r"observed 1 incomplete contact stream\(s\).*1 retry attempt",
        ):
            await subject.stress_contact_enumerations(expected, "unit-test table")

        step = subject.steps[-1]
        self.assertFalse(step["ok"])
        self.assertEqual(step["completed_passes"], 2)
        self.assertEqual(step["transport_transactions"], 3)
        self.assertEqual(step["short_attempts"], 1)
        self.assertEqual(step["retry_attempts"], 1)
        self.assertEqual(step["advertised_counts"], [2, 2])
        self.assertEqual(step["received_counts"], [2, 2])

    async def test_same_count_wrong_keyset_fails_on_exact_pass(self):
        expected = {key(1), key(2)}
        wrong = {key(1): {}, key(3): {}}
        subject = runner([(wrong, 2)], passes=1)

        with self.assertRaisesRegex(
            HIL.HilFailure, r"pass 1/1: contact key set mismatch"
        ):
            await subject.stress_contact_enumerations(expected, "unit-test table")

        self.assertEqual(subject.steps[-1]["completed_passes"], 0)
        self.assertEqual(len(subject.mc.commands.calls), 1)

    async def test_expected_keyset_digest_anchors_read_only_stress(self):
        expected = {key(1), key(2)}
        payload = {item: {} for item in expected}
        subject = runner([(payload, 2)], passes=1, digest="0" * 64)

        with self.assertRaisesRegex(HIL.HilFailure, "keyset digest is"):
            await subject.stress_contact_enumerations(None, "unit-test table")

        self.assertFalse(subject.steps[-1]["ok"])


class ContactEnumerationArgumentTests(unittest.TestCase):
    def test_new_corruption_option_is_backward_compatible_and_off_by_default(self):
        legacy_positional = HIL.RunConfig(
            "COM23",
            "inspect",
            False,
            None,
            None,
            350,
            131072,
            39,
            7,
            35,
            False,
            False,
            None,
            False,
            None,
            None,
            False,
        )
        self.assertIsNone(legacy_positional.corrupt_occupied_page)
        self.assertFalse(legacy_positional.advert_remove_rollback)
        self.assertIsNone(legacy_positional.fail_read_page)
        self.assertIsNone(legacy_positional.fail_stat_page)

        args = HIL.build_argument_parser().parse_args(["--port", "COM23"])
        del args.corrupt_occupied_page
        del args.advert_remove_rollback
        del args.fail_read_page
        del args.fail_stat_page
        config = HIL.config_from_args(args)
        self.assertIsNone(config.corrupt_occupied_page)
        self.assertFalse(config.advert_remove_rollback)
        self.assertIsNone(config.fail_read_page)
        self.assertIsNone(config.fail_stat_page)

    def test_parser_accepts_read_only_stress_without_destructive_consent(self):
        args = HIL.build_argument_parser().parse_args(
            [
                "--port",
                "COM23",
                "--contact-enumeration-passes",
                "25",
                "--expected-contact-keyset-sha256",
                "AB" * 32,
            ]
        )

        config = HIL.config_from_args(args)

        self.assertEqual(config.scenario, "inspect")
        self.assertEqual(config.contact_enumeration_passes, 25)
        self.assertEqual(config.expected_contact_keyset_sha256, "ab" * 32)
        HIL.require_destructive_consent(config)

    def test_digest_requires_enumeration_option(self):
        args = HIL.build_argument_parser().parse_args(
            [
                "--port",
                "COM23",
                "--expected-contact-keyset-sha256",
                "ab" * 32,
            ]
        )

        with self.assertRaisesRegex(
            HIL.HilFailure, "requires --contact-enumeration-passes"
        ):
            HIL.config_from_args(args)

    def test_runtime_port_gate_still_rejects_wrong_physical_identity(self):
        wrong = SimpleNamespace(
            device="COM23",
            serial_number="NOT-THE-SPARE",
            vid=HIL.EXPECTED_USB_VID,
            pid=HIL.EXPECTED_USB_PID,
            description="T1000-E",
            product="T1000-E",
            interface="MI_00",
            location="1-8:x.0",
            hwid="USB MI_00",
        )

        with self.assertRaisesRegex(HIL.HilFailure, "expected the locked spare"):
            HIL.resolve_initial_port("COM23", port_source=lambda: [wrong])

    def test_occupied_mask_corruption_is_comprehensive_only_and_consent_gated(self):
        parser = HIL.build_argument_parser()
        inspect_args = parser.parse_args(
            ["--port", "COM23", "--corrupt-occupied-page", "13"]
        )
        with self.assertRaisesRegex(HIL.HilFailure, "require --scenario"):
            HIL.config_from_args(inspect_args)

        comprehensive_args = parser.parse_args(
            [
                "--port",
                "COM23",
                "--scenario",
                "comprehensive",
                "--corrupt-occupied-page",
                "13",
                "--allow-existing-contacts",
            ]
        )
        config = HIL.config_from_args(comprehensive_args)
        self.assertEqual(config.corrupt_occupied_page, 13)
        with self.assertRaisesRegex(HIL.HilFailure, "--allow-destructive"):
            HIL.require_destructive_consent(config)

    def test_restorative_page_corruption_accepts_reviewed_existing_inventory(self):
        parser = HIL.build_argument_parser()
        args = parser.parse_args(
            [
                "--port",
                "COM23",
                "--scenario",
                "comprehensive",
                "--corrupt-page",
                "7",
                "--allow-existing-contacts",
                "--allow-destructive",
                "--confirm-usb-serial",
                HIL.EXPECTED_USB_SERIAL,
            ]
        )

        config = HIL.config_from_args(args)

        self.assertEqual(config.corrupt_page, 7)
        self.assertTrue(config.allow_existing_contacts)
        HIL.require_destructive_consent(config)

    def test_contact_page_mask_parser_is_strict(self):
        self.assertEqual(
            HIL.parse_contact_page_mask(
                "HIL contact page 13 occupied=81Ff00a5"
            ),
            HIL.ContactPageMask(13, 0x81FF00A5),
        )
        for malformed in (
            "HIL contact page 14 occupied=01ffffff",
            "HIL contact page 0 occupied=1ffffff",
            "HIL contact page 0 mask=01ffffff",
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(HIL.HilFailure):
                    HIL.parse_contact_page_mask(malformed)

    def test_advert_remove_rollback_is_opt_in_comprehensive_and_gated(self):
        parser = HIL.build_argument_parser()
        inspect_args = parser.parse_args(
            ["--port", "COM23", "--advert-remove-rollback"]
        )
        with self.assertRaisesRegex(HIL.HilFailure, "require --scenario"):
            HIL.config_from_args(inspect_args)

        args = parser.parse_args(
            [
                "--port",
                "COM23",
                "--scenario",
                "comprehensive",
                "--advert-remove-rollback",
            ]
        )
        config = HIL.config_from_args(args)
        self.assertTrue(config.advert_remove_rollback)
        with self.assertRaisesRegex(HIL.HilFailure, "--allow-destructive"):
            HIL.require_destructive_consent(config)

    def test_contact_slot_and_advert_seed_parsers_are_strict(self):
        self.assertEqual(HIL.parse_contact_slot("HIL contact slot=349"), 349)
        self.assertEqual(HIL.parse_advert_seed_slot("HIL advert seeded slot=0"), 0)
        for parser, malformed in (
            (HIL.parse_contact_slot, "HIL contact slot=350"),
            (HIL.parse_contact_slot, "HIL contact slot=-1"),
            (HIL.parse_advert_seed_slot, "HIL advert seed failed"),
            (HIL.parse_advert_seed_slot, "HIL advert seeded slot=999"),
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(HIL.HilFailure):
                    parser(malformed)

    def test_fail_read_page_is_bounded_comprehensive_only_and_gated(self):
        parser = HIL.build_argument_parser()
        inspect_args = parser.parse_args(
            ["--port", "COM23", "--fail-read-page", "13"]
        )
        with self.assertRaisesRegex(HIL.HilFailure, "require --scenario"):
            HIL.config_from_args(inspect_args)

        with self.assertRaisesRegex(argparse.ArgumentTypeError, "in 0..13"):
            HIL._nonnegative_page("14")

        args = parser.parse_args(
            [
                "--port",
                "COM23",
                "--scenario",
                "comprehensive",
                "--fail-read-page",
                "0",
            ]
        )
        config = HIL.config_from_args(args)
        self.assertEqual(config.fail_read_page, 0)
        with self.assertRaisesRegex(HIL.HilFailure, "--allow-destructive"):
            HIL.require_destructive_consent(config)

    def test_fail_stat_page_is_bounded_exclusive_and_gated(self):
        parser = HIL.build_argument_parser()
        inspect_args = parser.parse_args(
            ["--port", "COM23", "--fail-stat-page", "13"]
        )
        with self.assertRaisesRegex(HIL.HilFailure, "require --scenario"):
            HIL.config_from_args(inspect_args)

        args = parser.parse_args(
            [
                "--port",
                "COM23",
                "--scenario",
                "comprehensive",
                "--fail-stat-page",
                "0",
            ]
        )
        config = HIL.config_from_args(args)
        self.assertEqual(config.fail_stat_page, 0)
        with self.assertRaisesRegex(HIL.HilFailure, "--allow-destructive"):
            HIL.require_destructive_consent(config)

        both = parser.parse_args(
            [
                "--port",
                "COM23",
                "--scenario",
                "comprehensive",
                "--fail-read-page",
                "0",
                "--fail-stat-page",
                "1",
            ]
        )
        with self.assertRaisesRegex(HIL.HilFailure, "mutually exclusive"):
            HIL.config_from_args(both)


class ExactContactFrameTests(unittest.TestCase):
    def test_exact_frame_preserves_lastmod_and_all_contact_fields(self):
        contact = HIL.make_contact(7)
        contact.update(
            {
                "flags": 5,
                "out_path_hash_mode": 1,
                "out_path_len": 2,
                "out_path": "00112233",
                "adv_name": "HIL-EXACT",
                "adv_lat": 12.345678,
                "adv_lon": -87.654321,
                "lastmod": 1_800_000_007,
            }
        )

        frame = HIL.encode_exact_contact_frame(contact)

        self.assertEqual(len(frame), 148)
        self.assertEqual(frame[0], 0x09)
        self.assertEqual(frame[1:33], bytes.fromhex(contact["public_key"]))
        self.assertEqual(frame[33:36], bytes((1, 5, 0x42)))
        self.assertEqual(frame[36:40], bytes.fromhex("00112233"))
        self.assertEqual(frame[40:100], bytes(60))
        self.assertEqual(frame[100:109], b"HIL-EXACT")
        self.assertEqual(frame[109:132], bytes(23))
        self.assertEqual(
            int.from_bytes(frame[132:136], "little"), contact["last_advert"]
        )
        self.assertEqual(
            int.from_bytes(frame[136:140], "little", signed=True), 12_345_678
        )
        self.assertEqual(
            int.from_bytes(frame[140:144], "little", signed=True), -87_654_321
        )
        self.assertEqual(int.from_bytes(frame[144:148], "little"), 1_800_000_007)

    def test_exact_frame_requires_original_lastmod(self):
        with self.assertRaisesRegex(HIL.HilFailure, "lastmod"):
            HIL.encode_exact_contact_frame(HIL.make_contact(7))


class DestructiveScenarioTests(unittest.IsolatedAsyncioTestCase):
    def make_runner(self):
        config = SimpleNamespace(
            contact_count=350,
            contact_enumeration_passes=None,
            expected_contact_keyset_sha256=None,
            settle_seconds=0.001,
            corrupt_occupied_page=0,
            allow_existing_contacts=True,
            verbose=False,
        )
        identity = SimpleNamespace(serial_number=HIL.EXPECTED_USB_SERIAL)
        return HIL.StressRunner(config, identity)

    @staticmethod
    def full_contacts():
        contacts = [HIL.make_contact(index) for index in range(350)]
        for index, contact in enumerate(contacts):
            contact["lastmod"] = 1_800_000_000 + index
        return {contact["public_key"]: dict(contact) for contact in contacts}

    async def test_occupied_mask_scenario_repairs_populated_empty_and_high_bits(self):
        subject = self.make_runner()
        contacts = self.full_contacts()
        target_key = HIL.make_contact(0)["public_key"]
        without_target = {
            key_value: value
            for key_value, value in contacts.items()
            if key_value != target_key
        }
        subject.contacts = AsyncMock(
            side_effect=[
                contacts,
                contacts,
                contacts,
                contacts,
                without_target,
                without_target,
                without_target,
                without_target,
                contacts,
            ]
        )
        subject.reboot_and_reconnect = AsyncMock()
        subject.cli = AsyncMock(
            side_effect=[
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupancy bit 0 corrupted",
                "HIL contact page 0 occupied=01fffffe",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupancy bit 31 corrupted",
                "HIL contact page 0 occupied=81ffffff",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupied=01fffffe",
                "HIL contact page 0 occupancy bit 0 corrupted",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupied=01fffffe",
                "HIL contact page 0 occupied=01fffffe",
                "HIL contact page 0 occupied=01ffffff",
            ]
        )
        commands = SimpleNamespace(
            remove_contact=AsyncMock(return_value=event("OK")),
            add_contact=AsyncMock(return_value=event("OK")),
            update_contact=AsyncMock(return_value=event("OK")),
        )
        subject.mc = SimpleNamespace(commands=commands)

        await subject.test_contact_occupied_mask_recovery(0, contacts)

        self.assertEqual(subject.reboot_and_reconnect.await_count, 8)
        self.assertEqual(subject.contacts.await_count, 9)
        commands.remove_contact.assert_awaited_once_with(target_key)
        commands.add_contact.assert_awaited_once_with(contacts[target_key])
        commands.update_contact.assert_not_awaited()
        self.assertEqual(
            [call.args[0] for call in subject.cli.await_args_list],
            [
                "hil extrafs page-mask 0",
                "hil extrafs corrupt-occupied 0 0 CONFIRM",
                "hil extrafs page-mask 0",
                "hil extrafs page-mask 0",
                "hil extrafs page-mask 0",
                "hil extrafs corrupt-occupied 0 31 CONFIRM",
                "hil extrafs page-mask 0",
                "hil extrafs page-mask 0",
                "hil extrafs page-mask 0",
                "hil extrafs page-mask 0",
                "hil extrafs corrupt-occupied 0 0 CONFIRM",
                "hil extrafs page-mask 0",
                "hil extrafs page-mask 0",
                "hil extrafs page-mask 0",
                "hil extrafs page-mask 0",
            ],
        )
        step = subject.steps[-1]
        self.assertEqual(step["step"], "contact_occupied_mask_recovery")
        self.assertEqual(step["contacts_preserved"], 350)
        self.assertEqual(
            [case["corrupted_mask"] for case in step["cases"]],
            ["01fffffe", "81ffffff", "01ffffff"],
        )
        self.assertEqual(
            [case["durable_mask"] for case in step["cases"]],
            ["01ffffff", "01ffffff", "01fffffe"],
        )
        empty_case = step["cases"][-1]
        self.assertEqual(empty_case["kind"], "valid empty")
        self.assertEqual(empty_case["contact_count"], 349)
        self.assertEqual(empty_case["restored_contact_count"], 350)
        self.assertEqual(empty_case["restored_mask"], "01ffffff")
        self.assertTrue(step["original_contact_restored"])

    async def test_crc_restore_adds_missing_page_in_original_slot_order(self):
        subject = self.make_runner()
        baseline = self.full_contacts()
        baseline_order = list(baseline)
        expected_slots = {
            public_key: slot for slot, public_key in enumerate(baseline_order)
        }
        page = 7
        first = page * HIL.EXPECTED_CONTACTS_PER_PAGE
        lost = set(baseline_order[first:first + HIL.EXPECTED_CONTACTS_PER_PAGE])
        surviving = {
            public_key: value
            for public_key, value in baseline.items()
            if public_key not in lost
        }
        subject.contacts = AsyncMock(side_effect=[baseline, baseline])
        subject.assert_contact_slot_map = AsyncMock()
        subject.set_contact_exact = AsyncMock(return_value=event("OK"))
        subject.contact_slot = AsyncMock(
            side_effect=list(
                range(first, first + HIL.EXPECTED_CONTACTS_PER_PAGE)
            )
        )
        subject.contact_page_mask = AsyncMock(
            return_value=HIL.EXPECTED_FULL_PAGE_MASK
        )
        subject.reboot_and_reconnect = AsyncMock()

        restored, digests = await subject.restore_contact_inventory_exact(
            baseline,
            baseline_order,
            expected_slots,
            page,
            current=surviving,
            context="unit CRC restore",
        )

        self.assertEqual(restored, baseline)
        self.assertEqual(digests, subject.exact_inventory_digests(baseline))
        self.assertEqual(
            [
                call.args[0]["public_key"]
                for call in subject.set_contact_exact.await_args_list
            ],
            baseline_order[first:first + HIL.EXPECTED_CONTACTS_PER_PAGE],
        )
        self.assertEqual(subject.reboot_and_reconnect.await_count, 2)
        self.assertEqual(subject.contacts.await_count, 2)
        self.assertEqual(subject.assert_contact_slot_map.await_count, 2)
        self.assertEqual(subject.contact_page_mask.await_count, 3)

    async def test_crc_page_corruption_restores_exact_inventory_and_slots(self):
        subject = self.make_runner()
        subject.config.expected_contact_keyset_sha256 = None
        baseline = self.full_contacts()
        baseline_order = list(baseline)
        expected_slots = {
            public_key: slot for slot, public_key in enumerate(baseline_order)
        }
        page = 7
        first = page * HIL.EXPECTED_CONTACTS_PER_PAGE
        lost_order = baseline_order[
            first:first + HIL.EXPECTED_CONTACTS_PER_PAGE
        ]
        surviving = {
            public_key: value
            for public_key, value in baseline.items()
            if public_key not in set(lost_order)
        }
        status = HIL.ExtraFsStatus(True, 60, 100, 0)
        digests = subject.exact_inventory_digests(baseline)
        subject.clear_filler = AsyncMock()
        subject.status = AsyncMock(side_effect=[status, status])
        subject.contact_page_mask = AsyncMock(
            return_value=HIL.EXPECTED_FULL_PAGE_MASK
        )
        subject.capture_contact_slot_map = AsyncMock(
            return_value=expected_slots
        )
        subject.cli = AsyncMock(
            return_value=f"HIL contact page {page} CRC corrupted"
        )
        subject.reboot_and_reconnect = AsyncMock()
        subject.contacts = AsyncMock(return_value=surviving)
        subject.expect_contact_not_found = AsyncMock()
        subject.restore_contact_inventory_exact = AsyncMock(
            return_value=(baseline, digests)
        )

        result = await subject.corrupt_contact_page(page, baseline)

        self.assertEqual(result, set(baseline))
        subject.cli.assert_awaited_once_with(
            f"hil extrafs corrupt-page {page} CONFIRM"
        )
        self.assertEqual(subject.reboot_and_reconnect.await_count, 1)
        self.assertEqual(subject.expect_contact_not_found.await_count, 25)
        self.assertEqual(
            [call.args[0] for call in subject.expect_contact_not_found.await_args_list],
            lost_order,
        )
        restore_call = subject.restore_contact_inventory_exact.await_args
        self.assertEqual(restore_call.args[0], baseline)
        self.assertEqual(restore_call.args[1], baseline_order)
        self.assertEqual(restore_call.args[2], expected_slots)
        self.assertEqual(restore_call.args[3], page)
        self.assertEqual(restore_call.kwargs["current"], surviving)
        step = subject.steps[-1]
        self.assertEqual(step["step"], "contact_page_crc_loss_and_restore")
        self.assertEqual(step["isolated_after"], 325)
        self.assertEqual(step["lost_slots"], list(range(175, 200)))
        self.assertTrue(step["slots_restored"])
        self.assertTrue(step["stream_order_restored"])
        self.assertEqual(step["semantic_sha256"], digests["semantic_sha256"])
        self.assertEqual(step["wire_sha256"], digests["wire_sha256"])

    async def test_crc_page_failure_runs_emergency_exact_restore(self):
        subject = self.make_runner()
        subject.config.expected_contact_keyset_sha256 = None
        baseline = self.full_contacts()
        baseline_order = list(baseline)
        expected_slots = {
            public_key: slot for slot, public_key in enumerate(baseline_order)
        }
        page = 7
        lost = set(baseline_order[175:200])
        # An extra missing record proves the isolation assertion fails after
        # corruption, while the emergency path still restores the baseline.
        lost.add(baseline_order[200])
        wrong_survivors = {
            public_key: value
            for public_key, value in baseline.items()
            if public_key not in lost
        }
        status = HIL.ExtraFsStatus(True, 60, 100, 0)
        digests = subject.exact_inventory_digests(baseline)
        subject.clear_filler = AsyncMock()
        subject.status = AsyncMock(side_effect=[status, status])
        subject.contact_page_mask = AsyncMock(
            return_value=HIL.EXPECTED_FULL_PAGE_MASK
        )
        subject.capture_contact_slot_map = AsyncMock(
            return_value=expected_slots
        )
        subject.cli = AsyncMock(
            return_value=f"HIL contact page {page} CRC corrupted"
        )
        subject.reboot_and_reconnect = AsyncMock()
        subject.contacts = AsyncMock(return_value=wrong_survivors)
        subject.restore_contact_inventory_exact = AsyncMock(
            return_value=(baseline, digests)
        )

        with self.assertRaisesRegex(HIL.HilFailure, "contact key set mismatch"):
            await subject.corrupt_contact_page(page, baseline)

        self.assertEqual(subject.clear_filler.await_count, 2)
        self.assertEqual(subject.reboot_and_reconnect.await_count, 2)
        emergency_call = subject.restore_contact_inventory_exact.await_args
        self.assertNotIn("current", emergency_call.kwargs)
        self.assertEqual(
            emergency_call.kwargs["context"],
            "emergency contact-page CRC restoration",
        )
        step = subject.steps[-1]
        self.assertEqual(
            step["step"], "contact_page_corruption_emergency_restore"
        )
        self.assertTrue(step["recovered_exactly"])
        self.assertEqual(step["wire_sha256"], digests["wire_sha256"])

    async def test_empty_mask_failure_emergency_restores_exact_contact(self):
        subject = self.make_runner()
        contacts = self.full_contacts()
        target_key = HIL.make_contact(0)["public_key"]
        without_target = {
            key_value: value
            for key_value, value in contacts.items()
            if key_value != target_key
        }
        subject.contacts = AsyncMock(
            side_effect=[
                contacts,
                contacts,
                contacts,
                contacts,
                without_target,
                without_target,
                contacts,
            ]
        )
        subject.reboot_and_reconnect = AsyncMock(
            side_effect=[None, None, None, None, None, HIL.HilFailure("injected"), None]
        )
        subject.cli = AsyncMock(
            side_effect=[
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupancy bit 0 corrupted",
                "HIL contact page 0 occupied=01fffffe",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupancy bit 31 corrupted",
                "HIL contact page 0 occupied=81ffffff",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupied=01fffffe",
                "HIL contact page 0 occupancy bit 0 corrupted",
                "HIL contact page 0 occupied=01ffffff",
                "HIL contact page 0 occupied=01ffffff",
            ]
        )
        commands = SimpleNamespace(
            remove_contact=AsyncMock(return_value=event("OK")),
            add_contact=AsyncMock(return_value=event("OK")),
            update_contact=AsyncMock(return_value=event("OK")),
        )
        subject.mc = SimpleNamespace(commands=commands)

        with self.assertRaisesRegex(HIL.HilFailure, "injected"):
            await subject.test_contact_occupied_mask_recovery(0, contacts)

        commands.remove_contact.assert_awaited_once_with(target_key)
        commands.add_contact.assert_awaited_once_with(contacts[target_key])
        commands.update_contact.assert_not_awaited()
        self.assertEqual(subject.contacts.await_count, 7)
        self.assertEqual(subject.reboot_and_reconnect.await_count, 7)
        self.assertEqual(
            subject.steps[-1]["step"],
            "contact_occupied_mask_emergency_restore",
        )
        self.assertTrue(subject.steps[-1]["original_restored"])

    async def test_occupied_mask_preflight_rejects_non_harness_inventory(self):
        subject = self.make_runner()
        subject.contacts = AsyncMock(
            return_value={key(0xAA): {"public_key": key(0xAA)}}
        )

        with self.assertRaisesRegex(
            HIL.HilFailure, "inventory made only from this harness"
        ):
            await subject.add_contacts_to_capacity()

    async def test_storage_fill_helper_is_bounded_verified_and_scoped(self):
        subject = self.make_runner()
        subject.config.fill_bytes = 131072
        subject.clear_filler = AsyncMock()
        subject.cli = AsyncMock(
            return_value=(
                "HIL ExtraFS fill requested=131072 written=36864 "
                "used=99KiB total=100KiB"
            )
        )

        fill = await subject.fill_storage_to_enospc()

        subject.clear_filler.assert_awaited_once()
        subject.cli.assert_awaited_once_with(
            "hil extrafs fill 131072",
            timeout=HIL.EXTRAFS_FILL_TIMEOUT_SECONDS,
        )
        self.assertEqual(fill, HIL.ExtraFsFill(131072, 36864, 99, 100))

    async def test_full_storage_contact_ack_refusal_recovery_and_restore(self):
        subject = self.make_runner()
        original = self.full_contacts()
        target_key = HIL.make_contact(349)["public_key"]
        changed = {key_value: dict(value) for key_value, value in original.items()}
        changed[target_key]["adv_name"] = "HIL-ENOSPC"
        changed[target_key]["flags"] = 1
        subject.contacts = AsyncMock(
            side_effect=[original, changed, changed, changed, original]
        )
        subject.generated_keys = list(original)
        subject.core_uptime = AsyncMock(side_effect=[20, 21])
        subject.clear_filler = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        commands = SimpleNamespace(
            update_contact=AsyncMock(side_effect=[event("OK"), event("OK")]),
            send=AsyncMock(return_value=event("ERROR", {"error_code": 5})),
        )
        subject.mc = SimpleNamespace(commands=commands)

        await subject.test_contact_update_recovery_when_full(set(original))

        self.assertEqual(commands.update_contact.await_count, 2)
        first_update = commands.update_contact.await_args_list[0].args[0]
        second_update = commands.update_contact.await_args_list[1].args[0]
        self.assertEqual(first_update["adv_name"], "HIL-ENOSPC")
        self.assertEqual(first_update["flags"], 1)
        self.assertEqual(second_update["adv_name"], "HIL-349")
        self.assertEqual(second_update["flags"], 0)
        reboot_call = commands.send.await_args
        self.assertEqual(reboot_call.args[0], b"\x13reboot")
        self.assertEqual(
            reboot_call.kwargs["timeout"],
            HIL.ENOSPC_REBOOT_REFUSAL_TIMEOUT_SECONDS,
        )
        self.assertEqual(subject.clear_filler.await_count, 2)
        self.assertEqual(subject.reboot_and_reconnect.await_count, 2)
        step = subject.steps[-1]
        self.assertEqual(step["step"], "full_storage_contact_recovery")
        self.assertEqual(step["uptime_before_refusal"], 20)
        self.assertEqual(step["uptime_after_refusal"], 21)
        self.assertTrue(step["persisted_after_filler_clear"])
        self.assertTrue(step["original_restored"])

    async def test_full_storage_failure_restores_only_harness_owned_contact(self):
        subject = self.make_runner()
        synthetic = [HIL.make_contact(index) for index in range(349)]
        baseline_key = key(0xFF)
        mixed = {
            **{contact["public_key"]: dict(contact) for contact in synthetic},
            baseline_key: {
                **HIL.make_contact(999),
                "public_key": baseline_key,
                "adv_name": "USER-CONTACT",
            },
        }
        subject.generated_keys = [contact["public_key"] for contact in synthetic]
        subject.contacts = AsyncMock(
            side_effect=[mixed, HIL.HilFailure("injected after ACK"), mixed]
        )
        subject.clear_filler = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        commands = SimpleNamespace(
            update_contact=AsyncMock(side_effect=[event("OK"), event("OK")]),
        )
        subject.mc = SimpleNamespace(commands=commands)

        with self.assertRaisesRegex(HIL.HilFailure, "injected after ACK"):
            await subject.test_contact_update_recovery_when_full(set(mixed))

        first_target = commands.update_contact.await_args_list[0].args[0]
        restored_target = commands.update_contact.await_args_list[1].args[0]
        self.assertNotEqual(first_target["public_key"], baseline_key)
        self.assertEqual(restored_target["public_key"], first_target["public_key"])
        self.assertEqual(restored_target["adv_name"], "HIL-348")
        subject.clear_filler.assert_awaited_once()
        subject.reboot_and_reconnect.assert_awaited_once()
        self.assertEqual(
            subject.steps[-1]["step"], "full_storage_contact_emergency_restore"
        )

    async def test_full_storage_add_ack_refusal_persistence_and_exact_restore(self):
        subject = self.make_runner()
        original = self.full_contacts()
        original_key = HIL.make_contact(349)["public_key"]
        replacement = HIL.make_contact(2351)
        replacement_key = replacement["public_key"]
        without_original = {
            key_value: dict(value)
            for key_value, value in original.items()
            if key_value != original_key
        }
        substituted = {
            **without_original,
            replacement_key: dict(replacement),
        }
        subject.generated_keys = list(original)
        subject.contacts = AsyncMock(
            side_effect=[
                original,
                without_original,
                substituted,
                substituted,
                substituted,
                substituted,
                original,
            ]
        )
        fill = HIL.ExtraFsFill(131072, 36864, 99, 100)
        subject.fill_storage_to_enospc = AsyncMock(return_value=fill)
        subject.core_uptime = AsyncMock(side_effect=[20, 21])
        subject.clear_filler = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        subject.set_contact_exact = AsyncMock(return_value=event("OK"))
        commands = SimpleNamespace(
            remove_contact=AsyncMock(side_effect=[event("OK"), event("OK")]),
            add_contact=AsyncMock(side_effect=[event("OK"), event("OK")]),
            update_contact=AsyncMock(return_value=event("OK")),
            send=AsyncMock(return_value=event("ERROR", {"error_code": 5})),
        )
        subject.mc = SimpleNamespace(commands=commands)

        await subject.test_contact_add_recovery_when_full(set(original))

        self.assertEqual(
            [entry.args[0] for entry in commands.remove_contact.await_args_list],
            [original_key, replacement_key],
        )
        self.assertEqual(
            [
                entry.args[0]["public_key"]
                for entry in commands.add_contact.await_args_list
            ],
            [replacement_key],
        )
        commands.update_contact.assert_not_awaited()
        self.assertEqual(
            subject.set_contact_exact.await_args.args[0]["public_key"],
            original_key,
        )
        self.assertEqual(subject.fill_storage_to_enospc.await_count, 1)
        self.assertEqual(subject.clear_filler.await_count, 2)
        self.assertEqual(subject.reboot_and_reconnect.await_count, 3)
        self.assertEqual(subject.contacts.await_count, 7)
        step = subject.steps[-1]
        self.assertEqual(step["step"], "full_storage_contact_add_recovery")
        self.assertEqual(step["uptime_before_refusal"], 20)
        self.assertEqual(step["uptime_after_refusal"], 21)
        self.assertTrue(step["add_acknowledged"])
        self.assertTrue(step["persisted_after_filler_clear"])
        self.assertTrue(step["original_restored"])
        self.assertEqual(step["keyset_sha256"], subject.keyset_digest(original))

    async def test_full_storage_add_failure_emergency_restores_substitution(self):
        subject = self.make_runner()
        original = self.full_contacts()
        original_key = HIL.make_contact(349)["public_key"]
        replacement = HIL.make_contact(2351)
        replacement_key = replacement["public_key"]
        without_original = {
            key_value: dict(value)
            for key_value, value in original.items()
            if key_value != original_key
        }
        substituted = {
            **without_original,
            replacement_key: dict(replacement),
        }
        subject.generated_keys = list(original)
        subject.contacts = AsyncMock(
            side_effect=[
                original,
                without_original,
                HIL.HilFailure("injected after ADD ACK"),
                substituted,
                original,
            ]
        )
        subject.fill_storage_to_enospc = AsyncMock(
            return_value=HIL.ExtraFsFill(131072, 36864, 99, 100)
        )
        subject.clear_filler = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        subject.set_contact_exact = AsyncMock(return_value=event("OK"))
        commands = SimpleNamespace(
            remove_contact=AsyncMock(side_effect=[event("OK"), event("OK")]),
            add_contact=AsyncMock(side_effect=[event("OK"), event("OK")]),
            update_contact=AsyncMock(return_value=event("OK")),
        )
        subject.mc = SimpleNamespace(commands=commands)

        with self.assertRaisesRegex(HIL.HilFailure, "injected after ADD ACK"):
            await subject.test_contact_add_recovery_when_full(set(original))

        self.assertEqual(
            [entry.args[0] for entry in commands.remove_contact.await_args_list],
            [original_key, replacement_key],
        )
        self.assertEqual(
            [
                entry.args[0]["public_key"]
                for entry in commands.add_contact.await_args_list
            ],
            [replacement_key],
        )
        commands.update_contact.assert_not_awaited()
        self.assertEqual(
            subject.set_contact_exact.await_args.args[0]["public_key"],
            original_key,
        )
        self.assertEqual(subject.clear_filler.await_count, 1)
        self.assertEqual(subject.reboot_and_reconnect.await_count, 2)
        self.assertEqual(
            subject.steps[-1]["step"],
            "full_storage_contact_add_emergency_restore",
        )
        self.assertEqual(
            subject.steps[-1]["keyset_sha256"], subject.keyset_digest(original)
        )

    async def test_full_storage_remove_ack_refusal_persistence_and_exact_restore(self):
        subject = self.make_runner()
        original = self.full_contacts()
        target_key = HIL.make_contact(348)["public_key"]
        without_target = {
            key_value: dict(value)
            for key_value, value in original.items()
            if key_value != target_key
        }
        subject.generated_keys = list(original)
        subject.contacts = AsyncMock(
            side_effect=[
                original,
                without_target,
                without_target,
                without_target,
                without_target,
                original,
            ]
        )
        fill = HIL.ExtraFsFill(131072, 36864, 99, 100)
        subject.fill_storage_to_enospc = AsyncMock(return_value=fill)
        subject.clear_cached_advert = AsyncMock()
        subject.core_uptime = AsyncMock(side_effect=[20, 21])
        subject.clear_filler = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        subject.set_contact_exact = AsyncMock(return_value=event("OK"))
        commands = SimpleNamespace(
            remove_contact=AsyncMock(return_value=event("OK")),
            add_contact=AsyncMock(return_value=event("OK")),
            update_contact=AsyncMock(return_value=event("OK")),
            send=AsyncMock(return_value=event("ERROR", {"error_code": 5})),
        )
        subject.mc = SimpleNamespace(commands=commands)

        await subject.test_contact_remove_recovery_when_full(set(original))

        commands.remove_contact.assert_awaited_once_with(target_key)
        subject.clear_cached_advert.assert_awaited_once_with(target_key)
        commands.add_contact.assert_not_awaited()
        commands.update_contact.assert_not_awaited()
        self.assertEqual(
            subject.set_contact_exact.await_args.args[0]["public_key"], target_key
        )
        self.assertEqual(subject.fill_storage_to_enospc.await_count, 1)
        self.assertEqual(subject.clear_filler.await_count, 2)
        self.assertEqual(subject.reboot_and_reconnect.await_count, 2)
        self.assertEqual(subject.contacts.await_count, 6)
        step = subject.steps[-1]
        self.assertEqual(step["step"], "full_storage_contact_remove_recovery")
        self.assertEqual(step["uptime_before_refusal"], 20)
        self.assertEqual(step["uptime_after_refusal"], 21)
        self.assertTrue(step["remove_acknowledged"])
        self.assertTrue(step["persisted_after_filler_clear"])
        self.assertTrue(step["original_restored"])
        self.assertEqual(step["keyset_sha256"], subject.keyset_digest(original))

    async def test_full_storage_remove_failure_emergency_restores_contact(self):
        subject = self.make_runner()
        original = self.full_contacts()
        target_key = HIL.make_contact(348)["public_key"]
        without_target = {
            key_value: dict(value)
            for key_value, value in original.items()
            if key_value != target_key
        }
        subject.generated_keys = list(original)
        subject.contacts = AsyncMock(
            side_effect=[
                original,
                HIL.HilFailure("injected after REMOVE ACK"),
                without_target,
                original,
            ]
        )
        subject.fill_storage_to_enospc = AsyncMock(
            return_value=HIL.ExtraFsFill(131072, 36864, 99, 100)
        )
        subject.clear_cached_advert = AsyncMock()
        subject.clear_filler = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        subject.set_contact_exact = AsyncMock(return_value=event("OK"))
        commands = SimpleNamespace(
            remove_contact=AsyncMock(return_value=event("OK")),
            add_contact=AsyncMock(return_value=event("OK")),
            update_contact=AsyncMock(return_value=event("OK")),
        )
        subject.mc = SimpleNamespace(commands=commands)

        with self.assertRaisesRegex(HIL.HilFailure, "injected after REMOVE ACK"):
            await subject.test_contact_remove_recovery_when_full(set(original))

        commands.remove_contact.assert_awaited_once_with(target_key)
        subject.clear_cached_advert.assert_awaited_once_with(target_key)
        commands.add_contact.assert_not_awaited()
        commands.update_contact.assert_not_awaited()
        self.assertEqual(
            subject.set_contact_exact.await_args.args[0]["public_key"], target_key
        )
        self.assertEqual(subject.clear_filler.await_count, 1)
        subject.reboot_and_reconnect.assert_awaited_once()
        self.assertEqual(
            subject.steps[-1]["step"],
            "full_storage_contact_remove_emergency_restore",
        )
        self.assertEqual(
            subject.steps[-1]["keyset_sha256"], subject.keyset_digest(original)
        )

    async def test_full_storage_remove_error_reconciles_uncertain_mutation(self):
        subject = self.make_runner()
        original = self.full_contacts()
        target_key = HIL.make_contact(348)["public_key"]
        subject.generated_keys = list(original)
        subject.contacts = AsyncMock(return_value=original)
        subject.fill_storage_to_enospc = AsyncMock(
            return_value=HIL.ExtraFsFill(131072, 36864, 99, 100)
        )
        subject.clear_cached_advert = AsyncMock()
        subject.clear_filler = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        subject.set_contact_exact = AsyncMock(return_value=event("OK"))
        commands = SimpleNamespace(
            remove_contact=AsyncMock(
                return_value=event("ERROR", {"error_code": 5})
            ),
            add_contact=AsyncMock(return_value=event("OK")),
            update_contact=AsyncMock(return_value=event("OK")),
        )
        subject.mc = SimpleNamespace(commands=commands)

        with self.assertRaisesRegex(HIL.HilFailure, "device returned error"):
            await subject.test_contact_remove_recovery_when_full(set(original))

        commands.remove_contact.assert_awaited_once_with(target_key)
        subject.clear_cached_advert.assert_awaited_once_with(target_key)
        commands.update_contact.assert_not_awaited()
        commands.add_contact.assert_not_awaited()
        subject.set_contact_exact.assert_awaited_once()
        subject.clear_filler.assert_awaited_once()
        subject.reboot_and_reconnect.assert_awaited_once()
        self.assertEqual(subject.contacts.await_count, 3)
        self.assertEqual(
            subject.steps[-1]["step"],
            "full_storage_contact_remove_emergency_restore",
        )

    async def test_full_storage_remove_fill_failure_still_clears_partial_filler(self):
        subject = self.make_runner()
        original = self.full_contacts()
        target_key = HIL.make_contact(348)["public_key"]
        subject.generated_keys = list(original)
        subject.contacts = AsyncMock(return_value=original)
        subject.fill_storage_to_enospc = AsyncMock(
            side_effect=HIL.HilFailure("injected partial fill failure")
        )
        subject.clear_cached_advert = AsyncMock()
        subject.clear_filler = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        commands = SimpleNamespace(remove_contact=AsyncMock())
        subject.mc = SimpleNamespace(commands=commands)

        with self.assertRaisesRegex(HIL.HilFailure, "partial fill failure"):
            await subject.test_contact_remove_recovery_when_full(set(original))

        commands.remove_contact.assert_not_awaited()
        subject.clear_cached_advert.assert_awaited_once_with(target_key)
        subject.clear_filler.assert_awaited_once()
        subject.reboot_and_reconnect.assert_not_awaited()

    async def test_advert_hil_helpers_use_exact_direct_only_commands(self):
        subject = self.make_runner()
        public_key = HIL.make_contact(12)["public_key"]
        token = public_key[:14]
        subject.cli = AsyncMock(
            side_effect=[
                "HIL contact slot=27",
                "HIL advert seeded slot=27",
                "HIL advert cleared",
                "HIL contact page 13 read failure armed",
                "HIL contact page 12 stat failure armed",
            ]
        )

        self.assertEqual(await subject.contact_slot(public_key), 27)
        self.assertEqual(await subject.seed_cached_advert(public_key), 27)
        await subject.clear_cached_advert(public_key)
        await subject.arm_contact_read_failure(13)
        await subject.arm_contact_stat_failure(12)

        self.assertEqual(
            [entry.args[0] for entry in subject.cli.await_args_list],
            [
                f"hil extrafs contact-slot {token}",
                f"hil extrafs seed-advert {token} CONFIRM",
                f"hil extrafs clear-advert {token} CONFIRM",
                "hil extrafs fail-read-page 13 CONFIRM",
                "hil extrafs fail-stat-page 12 CONFIRM",
            ],
        )

    async def test_unread_page_get_contacts_requires_file_io_without_partial_state(self):
        subject = self.make_runner()
        reader = SimpleNamespace(contact_nb=350, contacts={"stale": {}})
        commands = SimpleNamespace(
            send=AsyncMock(
                return_value=event("ERROR", {"error_code": 5})
            )
        )
        subject.mc = SimpleNamespace(_reader=reader, commands=commands)

        await subject.expect_contacts_file_io("unit unread page")

        send_call = commands.send.await_args
        self.assertEqual(send_call.args[0], b"\x04")
        self.assertEqual(send_call.kwargs["timeout"], 20)
        self.assertIsNone(reader.contact_nb)
        self.assertEqual(reader.contacts, {})

    async def test_unread_page_get_contacts_rejects_partial_frames_before_error(self):
        subject = self.make_runner()
        reader = SimpleNamespace(contact_nb=None, contacts={})

        async def partial_then_error(*_args, **_kwargs):
            reader.contact_nb = 350
            reader.contacts = {HIL.make_contact(0)["public_key"]: {}}
            return event("ERROR", {"error_code": 5})

        commands = SimpleNamespace(send=AsyncMock(side_effect=partial_then_error))
        subject.mc = SimpleNamespace(_reader=reader, commands=commands)

        with self.assertRaisesRegex(HIL.HilFailure, "partial contact stream"):
            await subject.expect_contacts_file_io("unit unread page")

    async def test_advert_backed_remove_rolls_back_live_slot_and_exact_inventory(self):
        subject = self.make_runner()
        baseline = self.full_contacts()
        first_key = HIL.make_contact(0)["public_key"]
        target_key = HIL.make_contact(349)["public_key"]
        without_first = {
            key_value: dict(value)
            for key_value, value in baseline.items()
            if key_value != first_key
        }
        subject.contacts = AsyncMock(
            side_effect=[
                baseline,
                without_first,
                without_first,
                without_first,
                without_first,
                baseline,
            ]
        )
        subject.contact_slot = AsyncMock(
            side_effect=[10, 300, 300, 300, 300, 10, 300]
        )
        subject.seed_cached_advert = AsyncMock(return_value=300)
        subject.fill_storage_to_enospc = AsyncMock(
            return_value=HIL.ExtraFsFill(131072, 36864, 99, 100)
        )
        subject.graceful_reboot_or_enospc_refusal = AsyncMock(
            return_value=("refused", 20, 21)
        )
        subject.clear_filler = AsyncMock()
        subject.clear_cached_advert = AsyncMock()
        subject.set_contact_exact = AsyncMock(return_value=event("OK"))
        subject.reboot_and_reconnect = AsyncMock()
        commands = SimpleNamespace(
            remove_contact=AsyncMock(
                side_effect=[
                    event("OK"),
                    event("ERROR", {"error_code": 5}),
                ]
            )
        )
        subject.mc = SimpleNamespace(commands=commands)

        await subject.test_advert_backed_remove_rollback(set(baseline))

        self.assertEqual(
            [entry.args[0] for entry in commands.remove_contact.await_args_list],
            [first_key, target_key],
        )
        subject.seed_cached_advert.assert_awaited_once_with(target_key)
        subject.clear_cached_advert.assert_awaited_once_with(target_key)
        self.assertEqual(
            [
                entry.args[0]["public_key"]
                for entry in subject.set_contact_exact.await_args_list
            ],
            [first_key, target_key],
        )
        self.assertEqual(subject.reboot_and_reconnect.await_count, 2)
        self.assertEqual(subject.contacts.await_count, 6)
        step = subject.steps[-1]
        self.assertEqual(step["step"], "advert_remove_rollback")
        self.assertEqual(step["earlier_slot"], 10)
        self.assertEqual(step["target_slot"], 300)
        self.assertEqual(step["reboot_outcome"], "refused")
        self.assertTrue(step["live_target_and_slot_rolled_back"])
        self.assertEqual(
            step["semantic_sha256"], subject.semantic_contact_digest(baseline)
        )
        self.assertEqual(step["wire_sha256"], subject.wire_contact_digest(baseline))
        self.assertEqual(step["keyset_sha256"], subject.keyset_digest(baseline))

    async def test_advert_rollback_unexpected_remove_ok_emergency_restores_slots(self):
        subject = self.make_runner()
        baseline = self.full_contacts()
        first_key = HIL.make_contact(0)["public_key"]
        target_key = HIL.make_contact(349)["public_key"]
        without_first = {
            key_value: dict(value)
            for key_value, value in baseline.items()
            if key_value != first_key
        }
        without_both = {
            key_value: dict(value)
            for key_value, value in without_first.items()
            if key_value != target_key
        }
        subject.contacts = AsyncMock(
            side_effect=[baseline, without_first, without_both, baseline]
        )
        subject.contact_slot = AsyncMock(
            side_effect=[10, 300, 300, 10, 300]
        )
        subject.seed_cached_advert = AsyncMock(return_value=300)
        subject.fill_storage_to_enospc = AsyncMock(
            return_value=HIL.ExtraFsFill(131072, 36864, 99, 100)
        )
        subject.graceful_reboot_or_enospc_refusal = AsyncMock()
        subject.clear_filler = AsyncMock()
        subject.clear_cached_advert = AsyncMock()
        subject.set_contact_exact = AsyncMock(return_value=event("OK"))
        subject.reboot_and_reconnect = AsyncMock()
        commands = SimpleNamespace(
            remove_contact=AsyncMock(
                side_effect=[event("OK"), event("OK")]
            )
        )
        subject.mc = SimpleNamespace(commands=commands)

        with self.assertRaisesRegex(HIL.HilFailure, "expected error code 5"):
            await subject.test_advert_backed_remove_rollback(set(baseline))

        subject.graceful_reboot_or_enospc_refusal.assert_not_awaited()
        subject.clear_filler.assert_awaited_once()
        subject.clear_cached_advert.assert_awaited_once_with(target_key)
        self.assertEqual(
            [
                entry.args[0]["public_key"]
                for entry in subject.set_contact_exact.await_args_list
            ],
            [first_key, target_key],
        )
        self.assertEqual(subject.reboot_and_reconnect.await_count, 2)
        self.assertEqual(
            subject.steps[-1]["step"],
            "advert_remove_rollback_emergency_restore",
        )
        self.assertEqual(
            subject.steps[-1]["wire_sha256"],
            subject.wire_contact_digest(baseline),
        )

    async def test_read_failure_selector_finds_page_and_live_targets_by_slot(self):
        subject = self.make_runner()
        subject.contact_slot = AsyncMock(side_effect=[325, 326, 0, 1])

        failed_key, update_key, remove_key, slots = (
            await subject.select_read_failure_contacts(13)
        )

        self.assertEqual(failed_key, HIL.make_contact(325)["public_key"])
        self.assertEqual(update_key, HIL.make_contact(0)["public_key"])
        self.assertEqual(remove_key, HIL.make_contact(1)["public_key"])
        self.assertEqual(
            slots,
            {
                failed_key: 325,
                update_key: 0,
                remove_key: 1,
            },
        )

    async def test_contact_read_failure_fails_closed_then_recovers_exactly(self):
        subject = self.make_runner()
        baseline = self.full_contacts()
        failed_key = HIL.make_contact(0)["public_key"]
        update_key = HIL.make_contact(25)["public_key"]
        remove_key = HIL.make_contact(26)["public_key"]
        expected_slots = {failed_key: 0, update_key: 25, remove_key: 26}
        status = HIL.ExtraFsStatus(True, 60, 100, 0)
        subject.clear_filler = AsyncMock()
        subject.status = AsyncMock(side_effect=[status, status, status])
        subject.contacts = AsyncMock(side_effect=[baseline, baseline])
        subject.contact_page_mask = AsyncMock(
            return_value=HIL.EXPECTED_FULL_PAGE_MASK
        )
        subject.select_read_failure_contacts = AsyncMock(
            return_value=(failed_key, update_key, remove_key, expected_slots)
        )
        subject.arm_contact_read_failure = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        subject.expect_contacts_file_io = AsyncMock()
        subject.expect_contact_not_found = AsyncMock()
        subject.contact_by_key = AsyncMock(
            side_effect=[
                baseline[update_key],
                baseline[remove_key],
                baseline[update_key],
                baseline[remove_key],
                baseline[update_key],
            ]
        )
        subject.set_contact_exact = AsyncMock(
            side_effect=[
                event("ERROR", {"error_code": 5}),
                event("ERROR", {"error_code": 5}),
            ]
        )
        subject.contact_slot = AsyncMock(side_effect=[25, 26, 0, 25, 26])
        commands = SimpleNamespace(
            remove_contact=AsyncMock(
                return_value=event("ERROR", {"error_code": 5})
            )
        )
        subject.mc = SimpleNamespace(commands=commands)

        await subject.test_contact_page_read_failure(0)

        subject.arm_contact_read_failure.assert_awaited_once_with(0)
        self.assertEqual(subject.expect_contacts_file_io.await_count, 2)
        self.assertEqual(subject.expect_contact_not_found.await_count, 3)
        self.assertEqual(subject.set_contact_exact.await_count, 2)
        commands.remove_contact.assert_awaited_once_with(remove_key)
        self.assertEqual(
            [entry.args[0] for entry in subject.contact_slot.await_args_list],
            [update_key, remove_key, failed_key, update_key, remove_key],
        )
        self.assertEqual(subject.reboot_and_reconnect.await_count, 2)
        self.assertEqual(subject.status.await_count, 3)
        step = subject.steps[-1]
        self.assertEqual(step["step"], "contact_read_failure_recovery")
        self.assertEqual(step["page"], 0)
        self.assertTrue(step["partial_inventory_rejected"])
        self.assertTrue(step["live_mutations_rolled_back"])
        self.assertTrue(step["live_slots_rolled_back"])
        self.assertEqual(step["baseline_used_kib"], 60)
        self.assertEqual(step["final_used_kib"], 60)
        self.assertEqual(step["wire_sha256"], subject.wire_contact_digest(baseline))
        self.assertEqual(
            step["semantic_sha256"], subject.semantic_contact_digest(baseline)
        )

    async def test_contact_read_failure_rejects_final_storage_usage_change(self):
        subject = self.make_runner()
        baseline = self.full_contacts()
        failed_key = HIL.make_contact(0)["public_key"]
        update_key = HIL.make_contact(25)["public_key"]
        remove_key = HIL.make_contact(26)["public_key"]
        expected_slots = {failed_key: 0, update_key: 25, remove_key: 26}
        clean_status = HIL.ExtraFsStatus(True, 60, 100, 0)
        leaked_status = HIL.ExtraFsStatus(True, 61, 100, 0)
        subject.clear_filler = AsyncMock()
        subject.status = AsyncMock(
            side_effect=[
                clean_status,
                clean_status,
                leaked_status,
                clean_status,
            ]
        )
        subject.contacts = AsyncMock(
            side_effect=[baseline, baseline, baseline]
        )
        subject.contact_page_mask = AsyncMock(
            return_value=HIL.EXPECTED_FULL_PAGE_MASK
        )
        subject.select_read_failure_contacts = AsyncMock(
            return_value=(failed_key, update_key, remove_key, expected_slots)
        )
        subject.arm_contact_read_failure = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        subject.expect_contacts_file_io = AsyncMock()
        subject.expect_contact_not_found = AsyncMock()
        subject.contact_by_key = AsyncMock(
            side_effect=[
                baseline[update_key],
                baseline[remove_key],
                baseline[update_key],
                baseline[remove_key],
                baseline[update_key],
            ]
        )
        subject.set_contact_exact = AsyncMock(
            side_effect=[
                event("ERROR", {"error_code": 5}),
                event("ERROR", {"error_code": 5}),
            ]
        )
        subject.contact_slot = AsyncMock(
            side_effect=[25, 26, 0, 25, 26, 0, 25, 26]
        )
        commands = SimpleNamespace(
            remove_contact=AsyncMock(
                return_value=event("ERROR", {"error_code": 5})
            )
        )
        subject.mc = SimpleNamespace(commands=commands)

        with self.assertRaisesRegex(
            HIL.HilFailure,
            "changed ExtraFS usage from 60 KiB to 61 KiB",
        ):
            await subject.test_contact_page_read_failure(0)

        self.assertEqual(subject.reboot_and_reconnect.await_count, 4)
        self.assertEqual(subject.status.await_count, 4)
        self.assertEqual(
            subject.steps[-1]["step"],
            "contact_read_failure_emergency_recovery",
        )
        self.assertTrue(subject.steps[-1]["recovered_exactly"])

    async def test_contact_read_failure_unexpected_add_ok_emergency_recovers(self):
        subject = self.make_runner()
        baseline = self.full_contacts()
        failed_key = HIL.make_contact(0)["public_key"]
        update_key = HIL.make_contact(25)["public_key"]
        remove_key = HIL.make_contact(26)["public_key"]
        expected_slots = {failed_key: 0, update_key: 25, remove_key: 26}
        status = HIL.ExtraFsStatus(True, 60, 100, 0)
        subject.clear_filler = AsyncMock()
        subject.status = AsyncMock(side_effect=[status, status])
        subject.contacts = AsyncMock(side_effect=[baseline, baseline])
        subject.contact_page_mask = AsyncMock(
            return_value=HIL.EXPECTED_FULL_PAGE_MASK
        )
        subject.select_read_failure_contacts = AsyncMock(
            return_value=(failed_key, update_key, remove_key, expected_slots)
        )
        subject.arm_contact_read_failure = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        subject.expect_contacts_file_io = AsyncMock()
        subject.expect_contact_not_found = AsyncMock()
        subject.contact_by_key = AsyncMock(
            side_effect=[baseline[update_key], baseline[remove_key]]
        )
        subject.set_contact_exact = AsyncMock(return_value=event("OK"))
        subject.contact_slot = AsyncMock(side_effect=[0, 25, 26])
        commands = SimpleNamespace(remove_contact=AsyncMock())
        subject.mc = SimpleNamespace(commands=commands)

        with self.assertRaisesRegex(HIL.HilFailure, "expected error code 5"):
            await subject.test_contact_page_read_failure(0)

        self.assertEqual(subject.reboot_and_reconnect.await_count, 3)
        commands.remove_contact.assert_not_awaited()
        self.assertEqual(
            subject.steps[-1]["step"],
            "contact_read_failure_emergency_recovery",
        )
        self.assertTrue(subject.steps[-1]["recovered_exactly"])
        self.assertEqual(
            subject.steps[-1]["keyset_sha256"], subject.keyset_digest(baseline)
        )

    async def test_contact_stat_failure_blocks_whole_store_then_recovers_exactly(self):
        subject = self.make_runner()
        baseline = self.full_contacts()
        failed_key = HIL.make_contact(0)["public_key"]
        update_key = HIL.make_contact(25)["public_key"]
        remove_key = HIL.make_contact(26)["public_key"]
        expected_slots = {failed_key: 0, update_key: 25, remove_key: 26}
        status = HIL.ExtraFsStatus(True, 60, 100, 0)
        subject.clear_filler = AsyncMock()
        subject.status = AsyncMock(side_effect=[status, status, status])
        subject.contacts = AsyncMock(side_effect=[baseline, baseline])
        subject.contact_page_mask = AsyncMock(
            return_value=HIL.EXPECTED_FULL_PAGE_MASK
        )
        subject.select_read_failure_contacts = AsyncMock(
            return_value=(failed_key, update_key, remove_key, expected_slots)
        )
        subject.arm_contact_stat_failure = AsyncMock()
        subject.reboot_and_reconnect = AsyncMock()
        subject.expect_contacts_file_io = AsyncMock()
        subject.set_contact_exact = AsyncMock(
            side_effect=[
                event("ERROR", {"error_code": 5}),
                event("ERROR", {"error_code": 5}),
            ]
        )
        subject.contact_slot = AsyncMock(side_effect=[0, 25, 26])
        commands = SimpleNamespace(
            remove_contact=AsyncMock(
                return_value=event("ERROR", {"error_code": 5})
            )
        )
        subject.mc = SimpleNamespace(commands=commands)

        await subject.test_contact_page_stat_failure(0)

        subject.arm_contact_stat_failure.assert_awaited_once_with(0)
        self.assertEqual(subject.expect_contacts_file_io.await_count, 2)
        self.assertEqual(subject.set_contact_exact.await_count, 2)
        commands.remove_contact.assert_awaited_once_with(remove_key)
        self.assertEqual(subject.reboot_and_reconnect.await_count, 2)
        self.assertEqual(subject.status.await_count, 3)
        self.assertEqual(
            [entry.args[0] for entry in subject.contact_slot.await_args_list],
            [failed_key, update_key, remove_key],
        )
        step = subject.steps[-1]
        self.assertEqual(step["step"], "contact_stat_failure_recovery")
        self.assertEqual(step["page"], 0)
        self.assertTrue(step["whole_source_discovery_rejected"])
        self.assertTrue(step["marker_storage_reclaimed"])
        self.assertEqual(step["baseline_used_kib"], 60)
        self.assertEqual(step["final_used_kib"], 60)
        self.assertEqual(step["wire_sha256"], subject.wire_contact_digest(baseline))
        self.assertEqual(
            step["semantic_sha256"], subject.semantic_contact_digest(baseline)
        )


class ReconnectRaceTests(unittest.IsolatedAsyncioTestCase):
    def make_runner(self):
        config = SimpleNamespace(verbose=False)
        identity = SimpleNamespace(device="COM23")
        return HIL.StressRunner(config, identity)

    async def test_retries_transient_windows_access_denied(self):
        subject = self.make_runner()
        subject.connect = AsyncMock(side_effect=[PermissionError(13, "Access denied"), None])
        subject.disconnect = AsyncMock()

        await subject.connect_when_openable(1.0)

        self.assertEqual(subject.connect.await_count, 2)
        subject.disconnect.assert_awaited_once()

    async def test_reenumeration_surfaces_typed_reboot_refusal(self):
        refusal = event("ERROR", {"error_code": 5})
        error_future = asyncio.get_running_loop().create_future()
        error_future.set_result(refusal)
        previous = HIL.PortIdentity(
            "COM23",
            HIL.EXPECTED_USB_SERIAL,
            HIL.EXPECTED_USB_VID,
            HIL.EXPECTED_USB_PID,
            "T1000-E",
            "T1000-E",
            "MI_00",
            "1-8:x.0",
            "USB MI_00",
        )

        with self.assertRaises(HIL.RebootRefused) as raised:
            await HIL.wait_for_reenumeration(
                previous,
                0.1,
                port_source=lambda: [],
                error_future=error_future,
            )

        self.assertIs(raised.exception.event, refusal)

    async def test_identity_mismatch_is_never_retried(self):
        subject = self.make_runner()
        subject.connect = AsyncMock(
            side_effect=HIL.IdentityGateFailure("wrong node public key")
        )
        subject.disconnect = AsyncMock()

        with self.assertRaisesRegex(HIL.IdentityGateFailure, "wrong node"):
            await subject.connect_when_openable(1.0)

        subject.connect.assert_awaited_once()
        subject.disconnect.assert_awaited_once()

    async def test_graceful_reboot_accepts_verified_file_io_refusal(self):
        subject = self.make_runner()
        refusal = event("ERROR", {"error_code": 5})
        subject.core_uptime = AsyncMock(side_effect=[20, 21])
        subject.reboot_and_reconnect = AsyncMock(
            side_effect=HIL.RebootRefused(refusal)
        )

        outcome = await subject.graceful_reboot_or_enospc_refusal()

        self.assertEqual(outcome, ("refused", 20, 21))
        self.assertEqual(subject.steps[-1]["step"], "advert_rollback_reboot_refused")

    async def test_graceful_reboot_relies_on_inner_reset_proof_after_slow_reconnect(self):
        subject = self.make_runner()
        subject.core_uptime = AsyncMock(side_effect=[6, 9])
        subject.reboot_and_reconnect = AsyncMock()

        outcome = await subject.graceful_reboot_or_enospc_refusal()

        self.assertEqual(outcome, ("rebooted", 6, 9))


if __name__ == "__main__":
    unittest.main()
