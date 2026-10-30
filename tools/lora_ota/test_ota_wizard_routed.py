"""ESP-NOW control with separate, stateful LoRa source/target profiles."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import lora_ota as ota
from ota_wizard_routed import EspNowRelaySession, bridge_ota_drop_rule
from ota_wizard_session import full_package, radio_tuple
from test_lora_ota import firmware


KEY, SOURCE = "12" * 32, "34" * 32
TARGET, HW = 0x1234ABCD, "test_board"
A, B, C = (909.5, 500., 5, 5), (908.5, 500., 5, 5), (907.5, 500., 5, 5)
OLD, NEW = 0x01120002, 0x01120003


def image(version=NEW):
    return ota.parse_endf(firmware((b"old image" if version == OLD else b"new image") * 1000,
                                  version, TARGET, HW))


class RadioTransport:
    source_key, supports_radio2, acl_only = SOURCE, True, True

    def __init__(self):
        self.calls = []
        self.saved_radio = {"source": A, "target": C}
        self.windows = {"source": None, "target": None}
        self.policies = {"source": dict(hops="3", autofetch="off", autoinstall="off"),
                         "target": dict(hops="2", autofetch="signed", autoinstall="trusted")}
        self.rxps = {"source": "off,level=8,preamble=16,rx=10000,sleep=10000",
                     "target": "on,level=8,preamble=16,rx=10000,sleep=10000"}
        self.bridge_on = {"source": True, "target": True}
        self.rules = {label: "set fr.1 12 m=b rt=a d pri=255" for label in ("source", "target")}
        self.secondary = {"source": "off", "target": "off"}
        self.schedules = {"source": "-none-", "target": "-none-"}
        self.folder_active = False
        self.folder_entries = None
        self.folder_detach_lost = False
        self.folder_status_unknown = False
        self.serving = None
        self.image = image(OLD)
        self.mid = None
        self.slot_mid = None
        self.hold_download = False
        self.bad_mid = self.bad_body = self.lost_pull = self.lost_install = self.lost_cancel = False
        self.defer_install = False
        self.lost_window_ack = set()
        self.transient_commands = {}
        self.reset_calls = []

    async def verify_peer(self, *args):
        raise AssertionError("ACL control must not add a proof/login exchange")

    async def local_command(self, text):
        return await self.command("source", text)

    async def remote_command(self, key, password, text):
        assert key == KEY and password == ""
        # This test transport has only an ESP-NOW control link. LoRa source A
        # never matches either the target's saved C or transfer B.
        assert self.bridge_on["source"] and self.bridge_on["target"]
        return await self.command("target", text)

    async def reset_remote_session(self, key):
        assert key == KEY
        self.reset_calls.append(key)

    async def serve(self, blob, stop):
        self.serving = ota.parse_mota(blob)
        self.folder_active = True
        await stop.wait()
        if not self.folder_detach_lost:
            self.folder_active = False

    async def command(self, label, text):
        self.calls.append((label, text))
        key = label + ":" + text
        if self.transient_commands.get(key, 0):
            self.transient_commands[key] -= 1
            raise TimeoutError("synthetic lost reply")
        if text == "ota.peer radio": return "OTApeer radio=radio"
        if text == "get radio": return ",".join(map(str, self.saved_radio[label]))
        if text == "get radio2": return self.secondary[label]
        if text == "get tempradio2": return "off"
        if text == "get radio2.cross": return "off"
        if text in ("get espnow.enabled", "get espnow.running"): return "on" if self.bridge_on[label] else "off"
        if text == "get bridge.source": return "tx"
        if text == "get bridge.ingress": return "radio"
        if text == "get fr.1": return self.rules[label]
        if text == "get tempradioat": return self.schedules[label]
        if text == "get tempradio":
            return "off" if self.windows[label] is None else ",".join(map(str, self.windows[label])) + ",128"
        if text == "ota config": return " ".join(key + "=" + val for key, val in self.policies[label].items())
        if text.startswith("ota config "):
            _, _, field, val = text.split()
            self.policies[label][field] = val
            return "OK"
        if text in ("get radio.rxps.config", "get radio.rxps"): return self.rxps[label]
        if text == "set radio.rxps off":
            self.rxps[label] = self.rxps[label].replace("on,", "off,")
            return "OK"
        if text == "set radio.rxps level 8 preamble 16":
            self.rxps[label] = self.rxps[label].replace("off,", "on,")
            return "OK"
        if text.startswith("tempradio "):
            self.windows[label] = radio_tuple(text.split(" ", 1)[1])
            if label in self.lost_window_ack:
                self.lost_window_ack.remove(label)
                raise TimeoutError("lost lease acknowledgement")
            return "OK applying in 2s"
        if text == "normalradio":
            self.windows[label] = None
            return "OK"
        if text == "ota status":
            text = f"OTA | hw={HW} target:{TARGET:08X} maxblk:2048 | "
            if self.mid:
                mid = "DEADBEEF" if self.bad_mid else self.mid
                state = "downloading" if self.hold_download else "ready to install"
                return text + f"download: {state} 1/1 id={mid}"
            return text + "no download"
        if text == "ota self": return "self body=9000 image=9056 base_hash=" + self.image.body_hash.hex()
        if text == "ver": return ota.format_version(self.image.fw_version)
        if text == "ota stats":
            if not self.mid: return "OTA | fetch idle"
            mid = "DEADBEEF" if self.bad_mid else self.mid
            return "OTA | fetch " + ("dl" if self.hold_download else "done") + f" 1/1 id={mid}"
        if text == "ota folder":
            if self.folder_status_unknown: raise TimeoutError("folder state unknown")
            if not self.folder_active: return "folder=off serving=1: *BAD0CAFE/1234ABCD"
            mid = self.folder_entries or (self.serving.manifest_id if self.serving else "DEADBEEF")
            return "folder=on host=1/1 serving=2: *BAD0CAFE/1234ABCD " + mid + "/1234ABCD"
        if text == "ota folder off":
            self.folder_active = False
            return "OK folder detached"
        if text == "ota ls":
            assert self.windows["source"] == A and self.windows["target"] == B
            return self.serving.manifest_id
        if text.startswith("ota pull "):
            assert self.windows == {"source": A, "target": B}
            assert self.policies["source"]["hops"] == self.policies["target"]["hops"] == "1"
            self.mid = text.split()[2]
            self.slot_mid = self.mid
            if self.lost_pull: raise TimeoutError("lost pull acknowledgement")
            return "OK pulling"
        if text == "ota install":
            if not self.defer_install:
                self.finish_install()
            if self.lost_install: raise TimeoutError("lost install acknowledgement")
            return "OK rebooting"
        if text == "ota cancel":
            before = self.mid
            self.mid = self.slot_mid = None
            if self.lost_cancel: raise TimeoutError("lost cancel acknowledgement")
            return f"OK dropped session (was C mid={before}); OTA receive slot confirmed clear"
        if text.startswith("ota key add "): return "OK"
        raise AssertionError((label, text))

    def finish_install(self):
        self.image = ota.parse_endf(self.serving.payload)
        if self.bad_body:
            self.image = ota.parse_endf(firmware(b"wrong body" * 1000, self.image.fw_version, TARGET, HW))
        self.windows["target"] = None
        self.mid = self.slot_mid = None


class RoutedSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The stateful fixture applies profiles immediately. Keep scheduling
        # real, without spending two seconds on each hardware settle delay.
        real_sleep = asyncio.sleep
        async def fixture_sleep(seconds):
            await real_sleep(min(seconds, .001))
        sleeping = mock.patch("ota_wizard_routed.asyncio.sleep", new=fixture_sleep)
        sleeping.start()
        self.addCleanup(sleeping.stop)
        self.folder = tempfile.TemporaryDirectory(prefix="meshcore-routed-ota-")
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "session.json"
        self.link = RadioTransport()
        self.session = self.make_session()

    def make_session(self, **kwargs):
        return EspNowRelaySession(self.link, KEY, "", self.path, target_transfer_radio=B,
            window_minutes=90, poll_seconds=.001, transfer_timeout=.1, reboot_timeout=.1,
            progress=lambda _: None, **kwargs)

    async def test_check_is_read_only_and_uses_existing_acl_without_proof(self):
        before = await self.session.inspect()
        self.assertEqual(before.base_hash, image(OLD).body_hash)
        self.assertFalse(self.path.exists())
        self.assertEqual(await self.session.cleanup(), [])
        self.assertFalse(any(text.startswith(("set ", "ota config ", "tempradio ", "ota folder off"))
                             for _, text in self.link.calls))
        self.assertNotIn(("target", "get public.key"), self.link.calls)

    async def test_separate_windows_positive_hops_and_originals_are_recorded_before_mutation(self):
        await self.session.begin()
        self.assertEqual(self.link.windows, {"source": A, "target": B})
        self.assertEqual(self.link.policies["target"]["hops"], "1")
        record = json.loads(self.path.read_text())
        self.assertEqual(record["session_kind"], "espnow-relay")
        self.assertEqual(record["source_transfer_radio"], list(A))
        self.assertEqual(record["target_transfer_radio"], list(B))
        self.assertEqual(radio_tuple(record["settings"]["target"]["radio"]), C)
        self.assertEqual(record["preamble"], 128)
        self.assertEqual(await self.session.cleanup(), [])
        self.assertEqual(self.link.windows, {"source": None, "target": None})
        self.assertEqual(self.link.policies["target"], dict(hops="2", autofetch="signed", autoinstall="trusted"))

    async def test_lost_lease_acknowledgements_do_not_replay_either_relative_lease(self):
        self.link.lost_window_ack = {"source", "target"}
        await self.session.begin()
        for label in ("source", "target"):
            self.assertEqual(sum(node == label and text.startswith("tempradio ")
                                 for node, text in self.link.calls), 1)
        self.assertEqual(await self.session.cleanup(), [])

    async def test_inspection_during_owned_transfer_keeps_a_and_b_leases(self):
        await self.session.begin()
        self.link.calls.clear()
        before = await self.session.inspect()
        self.assertEqual(before.base_hash, image(OLD).body_hash)
        self.assertEqual(self.link.windows, {"source": A, "target": B})
        self.assertFalse(any(text.startswith(("set ", "tempradio ", "normalradio", "ota.peer radio "))
                             for _, text in self.link.calls))
        self.assertEqual(await self.session.cleanup(), [])

    async def test_install_reboots_target_to_c_and_never_retunes_source_to_b(self):
        before = await self.session.inspect()
        self.link.lost_pull = self.link.lost_install = True
        result = await self.session.install(full_package(image(), 2048), before)
        self.assertEqual(result.base_hash, image().body_hash)
        self.assertEqual(self.link.windows, {"source": A, "target": None})
        self.assertEqual(await self.session.cleanup(), [])
        self.assertEqual(self.link.saved_radio, {"source": A, "target": C})
        self.assertEqual(sum(text == "ota install" for _, text in self.link.calls), 1)
        self.assertEqual(sum(text.startswith("ota pull ") for _, text in self.link.calls), 1)
        self.assertFalse(any(label == "source" and text.startswith(("set radio ", "set radio2 ", "ota.peer radio "))
                             for label, text in self.link.calls))
        self.assertTrue(all(not text.startswith("tempradio ") or radio_tuple(text.split(" ", 1)[1]) == A
                            for label, text in self.link.calls if label == "source"))

    async def test_foreign_download_is_never_cancelled_and_automatic_policy_stays_off(self):
        before = await self.session.inspect()
        self.link.hold_download = True
        with self.assertRaisesRegex(ota.OtaError, "timed out"):
            await self.session.install(full_package(image(), 2048), before)
        self.link.bad_mid = True
        self.assertTrue(await self.session.cleanup())
        self.assertNotIn(("target", "ota cancel"), self.link.calls)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")

    async def test_lost_cancel_acknowledgement_is_not_replayed_or_treated_as_clear(self):
        before = await self.session.inspect()
        self.link.hold_download = self.link.lost_cancel = True
        with self.assertRaises(ota.OtaError):
            await self.session.install(full_package(image(), 2048), before)
        self.assertTrue(await self.session.cleanup())
        self.assertEqual(sum(text == "ota cancel" for _, text in self.link.calls), 1)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")
        self.assertTrue(await self.session.cleanup())
        self.assertEqual(sum(text == "ota cancel" for _, text in self.link.calls), 1)

    async def test_recovery_uses_recorded_options_and_recovers_only_owned_state(self):
        await self.session.begin()
        recovery = EspNowRelaySession(self.link, KEY, "", self.path, target_transfer_radio=(906.5, 125, 8, 5),
                                     ota_hops=8, preamble=256, progress=lambda _: None)
        recovery._state = json.loads(self.path.read_text())
        recovery.saved, recovery.active = recovery._state["settings"], True
        self.assertEqual(await recovery.cleanup(), [])
        self.assertEqual((recovery.source_transfer_radio, recovery.target_transfer_radio, recovery.ota_hops,
                          recovery.preamble), (A, B, 1, 128))

    async def test_changed_target_temporary_profile_is_not_cleared(self):
        await self.session.begin()
        self.link.windows["target"] = (906.5, 125., 8, 5)
        self.link.calls.clear()
        self.assertTrue(await self.session.cleanup())
        self.assertNotIn(("target", "normalradio"), self.link.calls)
        self.assertEqual(self.link.windows["target"], (906.5, 125., 8, 5))
        self.assertIsNone(self.link.windows["source"])

    async def test_missing_rule_or_existing_folder_refuses_before_mutation(self):
        for kind in ("rule", "folder"):
            with self.subTest(kind=kind):
                self.link = RadioTransport()
                self.session = self.make_session()
                if kind == "rule": self.link.rules["target"] = "set fr.1 12 m=b d"
                else: self.link.folder_active = True
                with self.assertRaises(ota.OtaError): await self.session.begin()
                self.assertFalse(self.session.active)
                self.assertFalse(any(text.startswith(("set ", "ota config ", "tempradio ")) for _, text in self.link.calls))

    async def test_transient_read_retries_three_times_without_source_profile_or_rf_changes(self):
        await self.session.begin()
        self.link.calls.clear()
        self.link.transient_commands["target:get radio"] = 2
        self.assertEqual(radio_tuple(await self.session.remote("get radio")), C)
        self.assertEqual(self.link.calls, [("target", "get radio")] * 3)
        self.assertEqual(self.link.windows["source"], A)
        self.assertEqual(await self.session.cleanup(), [])

    async def test_source_folder_is_rechecked_after_slow_package_preparation(self):
        await self.session.snapshot()
        self.link.folder_active = True
        self.link.calls.clear()
        with self.assertRaisesRegex(ota.OtaError, "attached OTA folder"):
            await self.session.begin()
        self.assertFalse(self.session.active)
        self.assertFalse(any(text.startswith(("set ", "ota config ", "tempradio ", "ota folder off"))
                             for _, text in self.link.calls))

    async def test_recovery_does_not_detach_another_operators_folder(self):
        await self.session.begin()
        self.session._state.update(source_folder_owned=True, package_mid="1234ABCD")
        self.link.folder_active, self.link.folder_entries = True, "DEADBEEF"
        self.assertTrue(await self.session.cleanup())
        self.assertNotIn(("source", "ota folder off"), self.link.calls)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")

    def test_constructor_rejects_password_companion_and_nonpositive_hops(self):
        for hops in (0, 9, True):
            with self.subTest(hops=hops), self.assertRaises(ota.OtaError): self.make_session(ota_hops=hops)
        with self.assertRaises(ota.OtaError): EspNowRelaySession(self.link, KEY, "password", self.path)
        self.link.acl_only = False
        with self.assertRaises(ota.OtaError): self.make_session()

    def test_ota_bridge_exclusion_cannot_be_conditional_disabled_or_flood_only(self):
        self.assertTrue(bridge_ota_drop_rule("set fr.1 12 m=b rt=a d pri=255"))
        for rule in ("set fr.1 12 m=b d pri=255", "set fr.1 12 m=b rt=a d pri=255 f=t",
                     "set fr.1 12 m=b rt=a d pri=255 e=0", "set fr.2 12 m=b rt=a d pri=255"):
            self.assertFalse(bridge_ota_drop_rule(rule))


if __name__ == "__main__":
    unittest.main()
