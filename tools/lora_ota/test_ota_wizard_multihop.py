"""Explicit native ACL routes and owned same-channel relay windows, offline."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import lora_ota as ota
from ota_wizard_multihop import LoRaRelaySession, RoutedRepeaterTransport, relay_route
from ota_wizard_repeater import RepeaterError, RepeaterTransport
from ota_wizard_session import full_package, radio_tuple
from test_ota_wizard_routed import RadioTransport, KEY, SOURCE, A, B, OLD, image


RELAYS = ("56" * 32, "78" * 32)


class NativeMemory(RoutedRepeaterTransport):
    supports_routed_path = True

    def __init__(self, **kwargs):
        super().__init__("unused", known_repeater=KEY, relay_keys=RELAYS, **kwargs)
        self.calls, self.path = [], "off"
        self._source_key = SOURCE
        self.lost_path_ack = False

    async def _check_clock(self):
        pass

    async def local_command(self, text):
        self.calls.append(text)
        if text == "ota.peer path":return "OTApeer path=" + self.path
        if text.startswith("ota.peer path "):
            self.path = text.split(" ", 2)[2]
            if self.lost_path_ack:
                self.lost_path_ack = False
                raise TimeoutError("lost path acknowledgement")
            return "OTApeer path=" + self.path
        if text == "ota.peer close":return "OTApeer closed"
        raise AssertionError(text)

    async def _job(self, text):
        self.calls.append(text)
        await asyncio.sleep(0)
        return "OK"


class NativeRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefixes_routes_and_first_use_acl_proof(self):
        link = NativeMemory()
        for key in (RELAYS[0], RELAYS[1], KEY, RELAYS[1], KEY):
            await link.remote_command(key, "", "get tx")
        expected = ["ota.peer acl " + RELAYS[0],
                    "ota.peer cmd.acl " + RELAYS[0] + " get tx",
                    "ota.peer path 565656", "ota.peer acl " + RELAYS[1],
                    "ota.peer cmd.acl " + RELAYS[1] + " get tx",
                    "ota.peer path 565656,787878", "ota.peer acl " + KEY,
                    "ota.peer cmd.acl " + KEY + " get tx",
                    "ota.peer path 565656", "ota.peer cmd.acl " + RELAYS[1] + " get tx",
                    "ota.peer path 565656,787878", "ota.peer cmd.acl " + KEY + " get tx"]
        self.assertEqual(link.calls, expected)
        await link.reset_remote_session(KEY)
        await link.remote_command(KEY, "", "ver")
        self.assertEqual(link.calls.count("ota.peer acl " + KEY), 2)
        self.assertEqual(link.calls.count("ota.peer acl " + RELAYS[0]), 1)
        self.assertFalse(any("login" in call or "discover" in call for call in link.calls))

    async def test_scope_password_and_prefix_collisions_rejected_before_commands(self):
        link = NativeMemory()
        with self.assertRaisesRegex(RepeaterError, "outside"):
            await link.remote_command("90" * 32, "", "get tx")
        with self.assertRaisesRegex(RepeaterError, "empty password"):
            await link.remote_command(KEY, "secret", "get tx")
        self.assertEqual(link.calls, [])
        with self.assertRaisesRegex(RepeaterError, "distinct"):
            relay_route(KEY, ["12" * 3 + "34" * 29])
        with self.assertRaisesRegex(RepeaterError, "one to eight"):
            relay_route(KEY, [])

    async def test_native_path_and_request_are_atomic_between_destinations(self):
        link = NativeMemory()
        await asyncio.gather(link.remote_command(KEY, "", "get tx"),
                             link.remote_command(RELAYS[0], "", "get repeat"))
        self.assertEqual(link.calls[:3], ["ota.peer path 565656,787878",
            "ota.peer acl " + KEY, "ota.peer cmd.acl " + KEY + " get tx"])
        self.assertEqual(link.calls[3:], ["ota.peer path off", "ota.peer acl " + RELAYS[0],
            "ota.peer cmd.acl " + RELAYS[0] + " get repeat"])

    async def test_close_restores_only_owned_native_path(self):
        link = NativeMemory()
        await link.remote_command(KEY, "", "get tx")
        await link._restore_native_path()
        self.assertEqual(link.path, "off")
        await link.remote_command(KEY, "", "get tx")
        link.path = "ABCDEF"
        with self.assertRaisesRegex(RepeaterError, "changed"):
            await link._restore_native_path()
        self.assertEqual(link.path, "ABCDEF")

    async def test_open_refuses_existing_foreign_native_path(self):
        link = NativeMemory()
        link.path = "ABCDEF"
        with mock.patch.object(RepeaterTransport, "open", new=mock.AsyncMock(return_value=link)):
            with self.assertRaisesRegex(RepeaterError, "existing"):
                await link.open()
        self.assertEqual(link.path, "ABCDEF")

    async def test_recovery_open_accepts_only_an_exact_recorded_route(self):
        link = NativeMemory(recovering=True, expected_source_key=SOURCE)
        link.path = "565656,787878"
        with mock.patch.object(RepeaterTransport, "open", new=mock.AsyncMock(return_value=link)):
            self.assertIs(await link.open(), link)
        self.assertEqual(link._selected_path, ("565656", "787878"))
        self.assertTrue(link._path_owned)
        self.assertEqual(link.calls, ["ota.peer path"])
        await link._restore_native_path()
        self.assertEqual(link.path, "off")
        foreign = NativeMemory(recovering=True, expected_source_key=SOURCE)
        foreign.path = "787878,565656"
        with mock.patch.object(RepeaterTransport, "open", new=mock.AsyncMock(return_value=foreign)):
            with self.assertRaisesRegex(RepeaterError, "existing"):
                await foreign.open()
        self.assertEqual(foreign.path, "787878,565656")

    async def test_recovery_wrong_source_cannot_claim_or_clear_matching_path(self):
        link = NativeMemory(recovering=True, expected_source_key="90" * 32)
        link.path = "565656,787878"
        with mock.patch.object(RepeaterTransport, "open", new=mock.AsyncMock(return_value=link)):
            with self.assertRaisesRegex(RepeaterError, "recovery identity"):
                await link.open()
        self.assertEqual(link.calls, [])
        self.assertFalse(link._path_owned)
        self.assertEqual(link.path, "565656,787878")
        with self.assertRaisesRegex(RepeaterError, "recorded USB source"):
            NativeMemory(recovering=True)

    async def test_lost_native_route_ack_reconciles_without_repeating_setter(self):
        link = NativeMemory()
        link.lost_path_ack = True
        await link.remote_command(KEY, "", "get tx")
        self.assertEqual(link.calls.count("ota.peer path 565656,787878"), 1)
        self.assertIn("ota.peer path", link.calls)
        self.assertEqual(link.calls[-1], "ota.peer cmd.acl " + KEY + " get tx")

    async def test_source_prefix_collision_refused_before_route_mutation(self):
        link = NativeMemory()
        link._source_key = RELAYS[0]
        with mock.patch.object(RepeaterTransport, "open", new=mock.AsyncMock(return_value=link)):
            with self.assertRaisesRegex(RepeaterError, "colliding"):
                await link.open()
        self.assertEqual(link.calls, ["ota.peer path"])


class RelayMemory(RadioTransport):
    supports_routed_path = True
    relay_keys, prefix_bytes = RELAYS, 3

    def __init__(self):
        super().__init__()
        self.saved_radio["target"] = A
        self.repeating = {"relay_0": "off", "relay_1": "off"}
        self.fail_change = None
        for label in self.repeating:
            self.saved_radio[label] = A
            self.windows[label] = None
            self.policies[label] = dict(hops="3", autofetch="signed", autoinstall="trusted")
            self.rxps[label] = "on,level=8,preamble=16,rx=10000,sleep=10000"
            self.secondary[label] = "off"
            self.schedules[label] = "-none-"

    async def remote_command(self, key, password, text):
        assert password == ""
        label = "target" if key == KEY else "relay_" + str(RELAYS.index(key))
        return await self.command(label, text)

    async def command(self, label, text):
        if self.fail_change == (label, text):
            self.calls.append((label, text))
            raise ota.OtaError("synthetic setup rejection")
        if text == "get repeat":
            self.calls.append((label, text));return self.repeating[label]
        if text.startswith("set repeat "):
            self.calls.append((label, text));self.repeating[label] = text.split()[-1];return "OK"
        if text == "ota ls":
            self.calls.append((label, text))
            assert all(window == A for window in self.windows.values())
            return self.serving.manifest_id
        if text.startswith("ota pull "):
            self.calls.append((label, text))
            assert all(window == A for window in self.windows.values())
            assert all(policy["hops"] == "2" for policy in self.policies.values())
            assert all(value == "on" for value in self.repeating.values())
            self.mid = self.slot_mid = text.split()[2]
            if self.lost_pull:raise TimeoutError("lost pull acknowledgement")
            return "OK pulling"
        return await super().command(label, text)


class LoRaRelaySessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        real_sleep = asyncio.sleep
        async def short_sleep(seconds):
            await real_sleep(min(seconds, .001))
        patcher = mock.patch("ota_wizard_multihop.asyncio.sleep", new=short_sleep)
        patcher.start();self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="meshcore-lora-relay-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.json"
        self.link = RelayMemory()
        self.session = self.make_session()

    def make_session(self, **kwargs):
        return LoRaRelaySession(self.link, KEY, "", self.path, window_minutes=90,
            poll_seconds=.001, transfer_timeout=.1, reboot_timeout=.1,
            progress=lambda _: None, **kwargs)

    async def test_inspect_read_only_no_espnow_and_same_channel_required(self):
        before = await self.session.inspect()
        self.assertEqual(before.base_hash, image(OLD).body_hash)
        self.assertFalse(self.path.exists())
        self.assertFalse(any("espnow" in text or "bridge" in text or text.startswith(("set ", "tempradio "))
                             for _, text in self.link.calls))
        self.link.saved_radio["relay_1"] = B
        with self.assertRaisesRegex(ota.OtaError, "share"):
            await self.session.inspect()

    async def test_setup_far_to_near_snapshots_and_restores_every_node(self):
        policies, rxps = copy.deepcopy(self.link.policies), dict(self.link.rxps)
        await self.session.begin()
        windows = [label for label, text in self.link.calls if text.startswith("tempradio ")]
        self.assertEqual(windows, ["target", "relay_1", "relay_0", "source"])
        record = json.loads(self.path.read_text())
        self.assertEqual(record["session_kind"], "lora-relay")
        self.assertEqual(record["relay_keys"], list(RELAYS))
        self.assertEqual(set(record["settings"]), {"source", "target", "relay_0", "relay_1"})
        self.link.calls.clear()
        self.assertEqual(await self.session.cleanup(), [])
        closes = [label for label, text in self.link.calls if text == "normalradio"]
        self.assertEqual(closes, ["target", "relay_1", "relay_0", "source"])
        self.assertEqual(self.link.policies, policies)
        self.assertEqual(self.link.rxps, rxps)
        self.assertEqual(set(self.link.repeating.values()), {"off"})

    async def test_lost_relative_lease_ack_never_repeats_for_any_node(self):
        self.link.lost_window_ack = {"target", "relay_1", "relay_0", "source"}
        await self.session.begin()
        for label in self.link.windows:
            self.assertEqual(sum(node == label and text.startswith("tempradio ")
                                 for node, text in self.link.calls), 1)
        self.assertEqual(await self.session.cleanup(), [])

    async def test_partial_setup_cleanup_restores_only_changed_nodes(self):
        self.link.fail_change = ("relay_0", "set repeat on")
        with self.assertRaisesRegex(ota.OtaError, "synthetic"):
            await self.session.begin()
        self.link.fail_change = None
        self.assertFalse(self.session._state["source_settings_changed"])
        self.assertEqual(await self.session.cleanup(), [])
        self.assertFalse(any(label == "source" and text.startswith(("tempradio ", "normalradio", "set ", "ota config "))
                             for label, text in self.link.calls))
        self.assertEqual(set(self.link.repeating.values()), {"off"})

    async def test_install_lost_mutation_ack_reproves_after_reboot_no_source_retune(self):
        before = await self.session.inspect()
        self.link.lost_pull = self.link.lost_install = True
        current = await self.session.install(full_package(image(), 2048), before)
        self.assertEqual(current.base_hash, image().body_hash)
        self.assertTrue(self.link.reset_calls)
        self.assertEqual(await self.session.cleanup(), [])
        self.assertEqual(sum(text.startswith("ota pull ") for _, text in self.link.calls), 1)
        self.assertEqual(sum(text == "ota install" for _, text in self.link.calls), 1)
        self.assertFalse(any(label == "source" and text.startswith(("set radio ", "ota.peer radio "))
                             for label, text in self.link.calls))

    async def test_recovery_rejects_foreign_folder_and_keeps_target_auto_off(self):
        await self.session.begin()
        package = full_package(image(), 2048)
        self.session.save("serving", source_folder_owned=True, package_mid=package.info.manifest_id)
        self.link.folder_active = True
        self.link.folder_entries = "DEADBEEF"
        recovered = self.make_session()
        recovered._state = json.loads(self.path.read_text())
        recovered.saved = recovered._state["settings"]
        recovered.active = True
        self.assertTrue(await recovered.cleanup())
        self.assertTrue(self.link.folder_active)
        self.assertNotIn(("source", "ota folder off"), self.link.calls)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")

    async def test_foreign_download_is_not_cancelled(self):
        before = await self.session.inspect()
        self.link.hold_download = True
        with self.assertRaises(ota.OtaError):
            await self.session.install(full_package(image(), 2048), before)
        self.link.bad_mid = True
        self.assertTrue(await self.session.cleanup())
        self.assertNotIn(("target", "ota cancel"), self.link.calls)
        self.assertEqual(self.link.policies["target"]["autofetch"], "off")

    async def test_changed_lease_not_cleared_and_recovery_route_must_match(self):
        await self.session.begin()
        self.link.windows["relay_1"] = B
        self.assertTrue(await self.session.cleanup())
        self.assertEqual(self.link.windows["relay_1"], B)
        self.session._state["relay_keys"] = list(reversed(RELAYS))
        with self.assertRaisesRegex(ota.OtaError, "routes differ"):
            await self.session.cleanup()

    async def test_source_folder_rechecked_after_package_preparation(self):
        await self.session.snapshot()
        self.link.folder_active = True
        with self.assertRaisesRegex(ota.OtaError, "attached"):
            await self.session.begin()
        self.assertFalse(self.session.active)
        self.assertFalse(any(text.startswith(("set ", "tempradio ", "ota config ")) for _, text in self.link.calls))

    async def test_source_folder_rechecked_after_relay_setup_before_attach(self):
        await self.session.begin()
        self.link.folder_active = True
        with self.assertRaisesRegex(ota.OtaError, "attached"):
            await self.session._start_serving(full_package(image(), 2048))
        self.assertFalse(self.session._state["source_folder_owned"])
        self.assertNotIn(("source", "ota folder off"), self.link.calls)
        self.link.folder_active = False
        self.assertEqual(await self.session.cleanup(), [])

    async def test_rejects_short_hops_different_transfer_and_password(self):
        with self.assertRaisesRegex(ota.OtaError, "cover every relay"):
            self.make_session(ota_hops=1)
        with self.assertRaises(ota.OtaError):
            LoRaRelaySession(self.link, KEY, "password", self.path)
        different = self.make_session(transfer_radio=B)
        with self.assertRaisesRegex(ota.OtaError, "frequency changes"):
            await different.inspect()


if __name__ == "__main__":
    unittest.main()
