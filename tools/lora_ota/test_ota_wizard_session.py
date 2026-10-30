"""Offline direct-update transactions with stateful radios and real containers."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

import lora_ota as ota
import ota_wizard_session as wizard
from test_lora_ota import firmware
from test_lora_ota_bootloader import boot_blob


KEY = "12" * 32
SOURCE = "34" * 32
TARGET = 0xE5331F02
HW = "ikoka_stick_nrf_33dbm"
OLD = 0x01110109
NEW = 0x01120001
BOOT_OLD = 0x02040BFF
BOOT_NEW = 0x02040CFF
TEST_SIGNER = "8A88E3DD7409F195FD52DB2D3CBA5D72CA6709BF1D94121BF3748801B40F6F5C"


def application(version=NEW, body=b"new application" * 600):
    return ota.parse_endf(firmware(body, version, TARGET, HW))


def boot_status(staged="none mid=- hash=-"):
    return ("BL board=28860044 target=28860044 name=XIAO_DFU "
            "crc=6F9BE280 abi=3 caps=0E | staged:" + staged)


class FakeTransport:
    """Apply mutations before optionally losing their replies, as real USB can."""
    source_key = SOURCE

    def __init__(self):
        self.image = application(OLD, b"old application" * 600)
        self.boot_version = BOOT_OLD
        self.calls = []
        self.windows = {"source": False, "target": False}
        self.temp_schedules = {"source": "-none-", "target": "-none-"}
        self.rxps = {"source": "off,level=8,preamble=16,rx=10000,sleep=10000",
                     "target": "on,level=8,preamble=16,rx=10000,sleep=10000"}
        self.policies = {"source": dict(hops="3", autofetch="off", autoinstall="off"),
                         "target": dict(hops="2", autofetch="signed", autoinstall="trusted")}
        self.seeder = True
        self.unsupported_source = False
        self.serving = None
        self.serves = []
        self.mid = None
        self.slot_mid = None
        self.trusted_keys = set()
        self.signer = None
        self.auto_installed = False
        self.apply_pending = False
        self.defer_install = False
        self.lost_cancel = self.discard_fails = False
        self.lease_minutes = {}
        self.lease_until = {}
        self.now = 0
        self.lost_window_ack = set()
        self.lost_pull = self.lost_install = False
        self.bad_mid = self.bad_hash = self.bad_body = False
        self.truncate_status = False
        self.hold_download = False
        self.download_started = asyncio.Event()
        self.ignore_restore = False
        self.restore_path_called = False
        self.reset_calls = []
        self.login_stale = False

    async def local_command(self, text):
        return await self.command("source", text)

    async def remote_command(self, key, password, text):
        if key != KEY or password != "secret-for-test":
            raise AssertionError("incorrect target or credentials")
        if self.login_stale:
            raise AssertionError("post-reboot probe needs a fresh authenticated session")
        return await self.command("target", text)

    async def reset_remote_session(self, key):
        self.reset_calls.append(key)
        self.login_stale = False

    async def serve(self, blob, stop):
        self.serving = ota.parse_mota(blob)
        manifest = ota.bootloader_library().parse_container(blob).manifest
        self.signer = manifest.signer_pubkey.hex().upper() if manifest.is_signed else None
        self.serves.append(self.serving)
        await stop.wait()

    async def restore_contact_path(self):
        self.restore_path_called = True

    async def command(self, label, text):
        self.calls.append((label, text))
        if text == "get public.key":
            return KEY if label == "target" else SOURCE
        if text == "ver":
            return ota.format_version(self.image.fw_version) + "-dev-test"
        if text == "get bootloader.ver":
            return f"OTAFIX{self.boot_version >> 24}.{self.boot_version >> 16 & 255}.{self.boot_version >> 8 & 255}"
        if text == "ota status":
            result = f"OTA | this fw 0000 hw={HW} | target:{TARGET:08X} | maxblk:2048 | bl:QSPI"
            if self.mid:
                mid = "DEADBEEF" if self.bad_mid else self.mid
                state = "downloading" if self.hold_download else "ready to install"
                result += f" | download: {state} 1/1 (100%)" + ("" if self.truncate_status else f" id={mid}")
            else:
                result += " | no download"
            return result
        if text == "ota stats":
            mid = "DEADBEEF" if self.bad_mid else self.mid
            state = "dl" if self.hold_download else "done"
            return f"OTA | fw v1.17.1.9 id=DEADCAFE body=0000 | fetch {state} 1/1 100% id={mid} 0s"
        if text == "ota self":
            return f"self body=9000 image=9056 base_hash={self.image.body_hash.hex()} | bootloader: QSPI apply OK (abi=3 codecs=0x5)"
        if text == "ota bootloader status":
            if self.mid:
                image_hash = "00" * 8 if self.bad_hash else self.serving.image_hash[:8].hex()
                return boot_status(f"ready mid={self.mid} hash={image_hash}")
            return boot_status()
        if text == "ota config":
            mode = "mode=seeder-only " if label == "source" and self.seeder else ""
            return "ota config: " + mode + " ".join(f"{key}={value}" for key, value in self.policies[label].items())
        if text.startswith("ota config "):
            _, _, field, value = text.split()
            if label == "source" and self.seeder and field != "hops":
                return "ERR seeder-only policy must stay off"
            if not self.ignore_restore or value == "0":
                self.policies[label][field] = value
            if label == "target":
                self.auto_install_tick()
            return "OK"
        if text == "get radio":
            return "910.525024,62.5,7,5"
        if text in ("get radio.rxps.config", "get radio.rxps"):
            if label == "source" and self.unsupported_source:
                return "ERR RX power saving is unsupported on this radio"
            return self.rxps[label]
        if text == "set radio.rxps off":
            self.rxps[label] = self.rxps[label].replace("on,", "off,")
            return "OK off"
        if text == "set radio.rxps level 8 preamble 16":
            self.rxps[label] = "on,level=8,preamble=16,rx=10000,sleep=10000"
            return "OK on"
        if text == "get tempradio":
            if self.now >= self.lease_until.get(label, float("inf")):
                self.windows[label] = False
            if label == "source":
                return "TempRadio active: 910.525024,62.5,7,5 14000s left,preamble=16" if self.windows[label] else "TempRadio inactive"
            return "910.525024,62.5,7,5,240" if self.windows[label] else "off"
        if text == "get tempradioat":
            return self.temp_schedules[label]
        if text.startswith("tempradio "):
            self.windows[label] = True
            self.lease_minutes[label] = int(text.rsplit(",", 1)[1])
            self.lease_until[label] = self.now + self.lease_minutes[label] * 60
            if label in self.lost_window_ack:
                self.lost_window_ack.remove(label)
                raise TimeoutError("lost lease acknowledgment")
            return "OK applying in 2s"
        if text == "normalradio":
            self.windows[label] = False
            return "OK"
        if text.startswith("ota key add "):
            self.trusted_keys.add(text.split()[-1])
            return "OK trusted key added"
        if text == "ota ls":
            return f"OTA | {self.serving.manifest_id}"
        if text.startswith("ota pull "):
            if not all(self.windows.values()):
                raise AssertionError("pull without overlapping leases")
            self.mid = text.split()[2]
            self.slot_mid = self.mid
            self.download_started.set()
            if self.lost_pull:
                raise TimeoutError("lost pull acknowledgment")
            return "OK pulling"
        if text == "ota cancel":
            # Firmware guards application apply_pending here, but not a
            # separately armed bootloader installation.
            if self.apply_pending and not self.serving.is_bootloader:
                return "ERR update is armed; reboot is pending"
            previous = self.mid
            self.mid = None
            if not self.discard_fails:
                self.slot_mid = None
            if self.lost_cancel:
                raise TimeoutError("lost cancel acknowledgment")
            if self.discard_fails:
                return f"ERR dropped live session (was C mid={previous}), but persistent OTA slot invalidation failed"
            return f"OK dropped session (was C mid={previous}); OTA receive slot confirmed clear"
        if text.startswith("ota bootloader install ") or text == "ota install":
            self.apply_pending = True
            if not self.defer_install:
                self.finish_install()
            if self.lost_install:
                raise TimeoutError("lost install acknowledgment")
            return "OK rebooting"
        raise AssertionError((label, text))

    def finish_install(self):
        if self.serving.is_bootloader:
            self.boot_version = self.serving.fw_version
        else:
            self.image = ota.parse_endf(self.serving.payload)
            if self.bad_body:
                self.image = application(self.image.fw_version, b"different body" * 600)
        self.windows["target"] = False
        self.login_stale = True
        self.mid = None
        self.apply_pending = False

    def auto_install_tick(self):
        # Match Mesh.cpp's COMPLETE/signed/non-bootloader/trusted-auto gate,
        # including the apply path's trusted-key/newer-version requirement.
        if (self.mid and not self.hold_download and not self.apply_pending
                and self.policies["target"]["autoinstall"] == "trusted"
                and self.serving is not None and self.serving.flags & 2
                and not self.serving.is_bootloader and self.signer in self.trusted_keys
                and self.serving.fw_version > self.image.fw_version):
            self.auto_installed = True
            self.finish_install()


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.link = FakeTransport()
        self.path = Path(self.temporary.name) / "session.json"
        self.session = wizard.DirectSession(self.link, KEY, "secret-for-test", self.path,
                                            progress=lambda _: None, poll_seconds=.001,
                                            reboot_timeout=.03, transfer_timeout=1)

    async def test_inspect_proves_board_storage_and_boot_contract(self):
        destination = await self.session.inspect()
        self.assertEqual((destination.public_key, destination.target_id, destination.hw_id), (KEY, TARGET, HW))
        self.assertEqual((destination.platform, destination.external_storage, destination.codecs), ("nrf52", True, 5))
        self.assertEqual((destination.bootloader.storage_flags, destination.boot_version), (14, BOOT_OLD))

    async def test_rejects_wrong_target_identity_before_mutations(self):
        original = self.link.remote_command
        async def wrong(key, password, text):
            return "56" * 32 if text == "get public.key" else await original(key, password, text)
        self.link.remote_command = wrong
        with self.assertRaisesRegex(ota.OtaError, "public key"):
            await self.session.inspect()
        self.assertEqual(self.link.calls, [])

    async def test_lost_pull_and_install_replies_are_reconciled_once(self):
        self.link.lost_pull = self.link.lost_install = self.link.truncate_status = True
        before = await self.session.inspect()
        package = wizard.full_package(application(), 1024)
        after = await self.session.install(package, before)
        self.assertEqual((after.version, after.base_hash), (NEW, application().body_hash))
        commands = [text for label, text in self.link.calls if label == "target"]
        self.assertEqual(sum(text.startswith("ota pull ") for text in commands), 1)
        self.assertEqual(commands.count("ota install"), 1)
        self.assertIn("ota stats", commands)
        self.assertEqual(await self.session.cleanup(), [])
        self.assertEqual(self.link.rxps["source"].split(",")[0], "off")
        self.assertEqual(self.link.rxps["target"].split(",")[0], "on")
        self.assertEqual(self.link.policies["target"], dict(hops="2", autofetch="signed", autoinstall="trusted"))
        self.assertFalse(any(self.link.windows.values()))
        self.assertTrue(self.link.restore_path_called)
        journal = json.loads(self.path.read_text())
        self.assertEqual((journal["target_key"], journal["source_key"], journal["phase"]), (KEY, SOURCE, "finished"))
        self.assertNotIn("secret-for-test", self.path.read_text())

    async def test_bootloader_then_application_reopens_target_window(self):
        before = await self.session.inspect()
        boot = wizard.Package(boot_blob(storage=14, version=BOOT_NEW), signer=TEST_SIGNER)
        after_boot = await self.session.install(boot, before)
        self.assertEqual(after_boot.base_hash, before.base_hash)
        self.assertEqual(after_boot.boot_version, BOOT_NEW)
        after = await self.session.install(wizard.full_package(application(), 1024), after_boot)
        self.assertEqual(after.version, NEW)
        target = [text for label, text in self.link.calls if label == "target"]
        self.assertEqual(sum(text.startswith("tempradio ") for text in target), 2)
        self.assertEqual(len(self.link.serves), 2)
        self.assertEqual(self.link.reset_calls, [KEY, KEY])
        self.assertEqual(await self.session.cleanup(), [])

    async def test_wrong_mid_fails_without_install(self):
        before = await self.session.inspect()
        self.link.bad_mid = True
        try:
            with self.assertRaisesRegex(ota.OtaError, "not the selected package"):
                await self.session.install(wizard.full_package(application(), 1024), before)
        finally:
            self.assertTrue(await self.session.cleanup())
        self.assertNotIn(("target", "ota cancel"), self.link.calls)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")
        self.assertNotIn(("target", "ota install"), self.link.calls)

    async def test_staged_bootloader_hash_must_match_exactly(self):
        before = await self.session.inspect()
        self.link.bad_hash = True
        try:
            with self.assertRaisesRegex(ota.OtaError, "MID/hash"):
                await self.session.install(wizard.Package(boot_blob(storage=14, version=BOOT_NEW), signer=TEST_SIGNER), before)
        finally:
            await self.session.cleanup()
        self.assertFalse(any(text.startswith("ota bootloader install ") for _, text in self.link.calls))

    async def test_running_version_alone_is_not_install_success(self):
        before = await self.session.inspect()
        self.link.bad_body = True
        try:
            with self.assertRaisesRegex(ota.OtaError, "version and body hash"):
                await self.session.install(wizard.full_package(application(), 1024), before)
        finally:
            await self.session.cleanup()

    async def test_destination_temporarily_missing_after_reboot_is_probed_again(self):
        import itertools
        from types import SimpleNamespace
        from unittest import mock
        from ota_wizard_companion import CompanionError
        before = await self.session.inspect()
        original = self.link.remote_command
        missing = 2
        async def reboot_discovery(key, password, text):
            nonlocal missing
            if self.link.image.fw_version == NEW and text == "get public.key" and missing:
                missing -= 1
                raise CompanionError("No directly reachable destination replied yet")
            return await original(key, password, text)
        self.link.remote_command = reboot_discovery
        # Advance only this module's deadline clock; compiler load must not
        # exhaust the tiny test reboot budget between three fake probes.
        clock = SimpleNamespace(monotonic=itertools.count(0, .001).__next__, time=time.time)
        with mock.patch.object(wizard, "time", clock):
            after = await self.session.install(wizard.full_package(application(), 1024), before)
        self.assertEqual(after.version, NEW)
        self.assertEqual(missing, 0)
        self.assertEqual(len(self.link.reset_calls), 3)
        self.assertEqual([text for _, text in self.link.calls].count("ota install"), 1)
        self.assertEqual(await self.session.cleanup(), [])

    async def test_cancelled_download_can_restore_every_setting(self):
        before = await self.session.inspect()
        self.link.hold_download = True
        task = asyncio.create_task(self.session.install(wizard.full_package(application(), 1024), before))
        await asyncio.wait_for(self.link.download_started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self.session.cleanup(), [])
        self.assertFalse(self.session.active)
        self.assertFalse(any(self.link.windows.values()))
        self.assertNotIn(("target", "ota install"), self.link.calls)

    async def stage_and_cancel(self, *, signed=False, complete=False):
        from test_ota_wizard_bundle import signed_package
        package = signed_package(application()) if signed else wizard.full_package(application(), 1024)
        before = await self.session.inspect()
        self.link.hold_download = True
        task = asyncio.create_task(self.session.install(package, before))
        await asyncio.wait_for(self.link.download_started.wait(), 2)
        self.link.hold_download = not complete
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        return package

    async def test_cancel_complete_signed_fetch_clears_slot_before_restoring_trusted_policy(self):
        await self.stage_and_cancel(signed=True, complete=True)
        errors = await self.session.cleanup()
        self.assertFalse(self.link.auto_installed)
        self.assertEqual(errors, [])
        self.assertEqual(self.link.image.fw_version, OLD)
        self.assertIsNone(self.link.mid)
        self.assertIsNone(self.link.slot_mid)
        target = [text for label, text in self.link.calls if label == "target"]
        self.assertEqual(target.count("ota cancel"), 1)
        self.assertLess(target.index("ota cancel"), target.index("ota config autoinstall trusted"))
        self.assertNotIn("ota install", target)
        self.assertEqual(json.loads(self.path.read_text())["phase"], "finished")
        # There is no abandoned live receive slot blocking the next update.
        after = await self.session.install(wizard.full_package(application(), 1024), await self.session.inspect())
        self.assertEqual(after.version, NEW)
        self.assertEqual(await self.session.cleanup(), [])

    async def test_lost_cancel_reply_never_replays_cancel_or_restores_automatic_policy(self):
        await self.stage_and_cancel(signed=True, complete=True)
        self.link.lost_cancel = True
        self.assertTrue(await self.session.cleanup())
        self.assertTrue(await self.session.cleanup())
        self.assertEqual([text for _, text in self.link.calls].count("ota cancel"), 1)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")
        self.assertEqual(self.link.policies["target"]["autofetch"], "off")
        self.assertFalse(self.link.auto_installed)
        self.assertEqual(json.loads(self.path.read_text())["phase"], "needs cleanup")

    async def test_legacy_download_journal_remembers_uncertain_cancel_after_restart(self):
        await self.stage_and_cancel(signed=True, complete=True)
        for name in ("pull_attempted", "install_attempted", "package_finalized", "cancel_sent", "cancel_confirmed"):
            self.session._state.pop(name, None)
        self.session.save("downloading")
        self.link.lost_cancel = True
        self.assertTrue(await self.session.cleanup())
        recovery = wizard.DirectSession(self.link, KEY, "secret-for-test", self.path, progress=lambda _: None)
        recovery._state = json.loads(self.path.read_text())
        recovery.saved, recovery.active = recovery._state["settings"], True
        self.assertTrue(recovery._state["pull_attempted"])
        self.assertTrue(recovery._state["cancel_sent"])
        self.assertTrue(await recovery.cleanup())
        self.assertEqual([text for _, text in self.link.calls].count("ota cancel"), 1)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")
        self.assertEqual(self.link.policies["target"]["autofetch"], "off")
        self.assertFalse(self.link.auto_installed)

    async def test_legacy_install_journal_never_becomes_cancelable_after_failed_recovery(self):
        self.link.defer_install = True
        before = await self.session.inspect()
        with self.assertRaises(ota.OtaError):
            await self.session.install(wizard.full_package(application(), 1024), before)
        for name in ("pull_attempted", "install_attempted", "package_finalized", "cancel_sent", "cancel_confirmed"):
            self.session._state.pop(name, None)
        self.session.save("installing")
        self.assertTrue(await self.session.cleanup())
        recovery = wizard.DirectSession(self.link, KEY, "secret-for-test", self.path, progress=lambda _: None)
        recovery._state = json.loads(self.path.read_text())
        recovery.saved, recovery.active = recovery._state["settings"], True
        self.assertTrue(recovery._state["install_attempted"])
        self.assertTrue(await recovery.cleanup())
        self.assertNotIn(("target", "ota cancel"), self.link.calls)
        self.assertTrue(self.link.apply_pending)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")

    async def test_malformed_install_marker_never_cancels_armed_bootloader(self):
        self.link.defer_install = True
        before = await self.session.inspect()
        package = wizard.Package(boot_blob(storage=14, version=BOOT_NEW), signer=TEST_SIGNER)
        with self.assertRaises(ota.OtaError):
            await self.session.install(package, before)
        original = json.loads(json.dumps(self.session._state))
        for malformed in ("install marker", "legacy phase", "legacy missing ID"):
            with self.subTest(malformed=malformed):
                self.session._state = json.loads(json.dumps(original))
                self.session.active = True
                if malformed == "install marker":
                    self.session.save("needs cleanup", install_attempted="false")
                else:
                    for name in ("pull_attempted", "install_attempted", "package_finalized",
                                 "cancel_sent", "cancel_confirmed"):
                        self.session._state.pop(name, None)
                    if malformed == "legacy missing ID":
                        self.session._state.pop("package_mid")
                    self.session.save(["installing"] if malformed == "legacy phase" else "installing")
                self.assertTrue(await self.session.cleanup())
                recovery = wizard.DirectSession(self.link, KEY, "secret-for-test", self.path, progress=lambda _: None)
                recovery._state = json.loads(self.path.read_text())
                recovery.saved, recovery.active = recovery._state["settings"], True
                self.assertIs(recovery._state["install_attempted"], True)
                self.assertTrue(await recovery.cleanup())
                self.assertNotIn(("target", "ota cancel"), self.link.calls)
                self.assertTrue(self.link.apply_pending)
                self.assertEqual(self.link.slot_mid, package.info.manifest_id)
                self.assertEqual(self.link.policies["target"]["autoinstall"], "off")
                self.assertEqual(self.link.policies["target"]["autofetch"], "off")

    async def test_failed_source_stop_cannot_reenable_automatic_fetch(self):
        from unittest import mock
        await self.stage_and_cancel(signed=True, complete=True)
        original = self.session._stop_serving
        with mock.patch.object(self.session, "_stop_serving", new_callable=mock.AsyncMock,
                               side_effect=ota.OtaError("source did not stop")):
            self.assertTrue(await self.session.cleanup())
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")
        self.assertEqual(self.link.policies["target"]["autofetch"], "off")
        self.assertFalse(self.link.auto_installed)
        await original()
        self.assertEqual(await self.session.cleanup(), [])

    async def test_persistent_slot_invalidation_failure_keeps_both_automatic_policies_off(self):
        package = await self.stage_and_cancel(signed=True, complete=True)
        self.link.discard_fails = True
        self.assertTrue(await self.session.cleanup())
        self.assertIsNone(self.link.mid)  # IDLE does not prove durable erasure.
        self.assertEqual(self.link.slot_mid, package.info.manifest_id)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")
        self.assertEqual(self.link.policies["target"]["autofetch"], "off")
        self.assertTrue(self.session.active)
        self.assertFalse(self.link.auto_installed)

    async def test_foreign_existing_download_is_not_canceled_or_mutated(self):
        self.link.mid = "DEADBEEF"
        with self.assertRaisesRegex(ota.OtaError, "already has OTA download"):
            await self.session.begin()
        self.assertFalse(self.session.active)
        self.assertFalse(any(text.startswith(("ota cancel", "ota config ", "set ", "tempradio "))
                             for _, text in self.link.calls))
        self.assertEqual(self.link.mid, "DEADBEEF")

    async def test_download_started_during_preparation_is_not_replaced(self):
        await self.session.snapshot()
        self.link.mid = "DEADBEEF"
        self.link.calls.clear()
        with self.assertRaisesRegex(ota.OtaError, "already has OTA download"):
            await self.session.begin()
        self.assertFalse(self.session.active)
        self.assertFalse(any(text.startswith(("ota cancel", "ota config ", "set ", "tempradio "))
                             for _, text in self.link.calls))

    async def test_uncertain_armed_install_is_not_erased_and_recovery_verifies_it(self):
        self.link.defer_install = True
        before = await self.session.inspect()
        with self.assertRaisesRegex(ota.OtaError, "version and body hash"):
            await self.session.install(wizard.full_package(application(), 1024), before)
        self.assertTrue(self.link.apply_pending)
        self.assertTrue(await self.session.cleanup())
        self.assertNotIn(("target", "ota cancel"), self.link.calls)
        self.assertEqual(self.link.policies["target"]["autoinstall"], "off")
        # A fresh process reads the saved exact expectations, never guesses
        # that a timed-out install failed, and does not send install twice.
        recovery = wizard.DirectSession(self.link, KEY, "secret-for-test", self.path,
                                        progress=lambda _: None)
        recovery._state = json.loads(self.path.read_text())
        recovery.saved, recovery.active = recovery._state["settings"], True
        self.link.finish_install()
        self.assertEqual(await recovery.cleanup(), [])
        self.assertEqual(self.link.policies["target"]["autoinstall"], "trusted")
        self.assertEqual([text for _, text in self.link.calls].count("ota install"), 1)
        self.assertEqual(json.loads(self.path.read_text())["phase"], "finished")

    async def test_combined_lease_covers_two_long_transfers_and_reboots(self):
        session = wizard.DirectSession(self.link, KEY, "secret-for-test", self.path, progress=lambda _: None)
        await session.begin()
        # Spend the first package's entire four-hour transfer and five-minute
        # reboot budget, then reopen the rebooted target for the application.
        self.link.now += 4 * 3600 + 5 * 60
        self.assertEqual(wizard.temp_state(await self.link.local_command("get tempradio")), "active")
        self.link.windows["target"] = False
        await session._open_window("target")
        self.link.now += 4 * 3600 + 5 * 60
        self.assertEqual(wizard.temp_state(await self.link.local_command("get tempradio")), "active")
        self.assertEqual(wizard.temp_state(await self.link.remote_command(KEY, "secret-for-test", "get tempradio")), "active")
        source_windows = [text for label, text in self.link.calls if label == "source" and text.startswith("tempradio ")]
        self.assertEqual(len(source_windows), 1)
        self.assertEqual(await session.cleanup(), [])

    async def test_relative_window_acknowledgments_are_read_back_without_replay(self):
        self.link.lost_window_ack = {"source", "target"}
        await self.session.begin()
        for label in ("source", "target"):
            commands = [text for side, text in self.link.calls if side == label and text.startswith("tempradio ")]
            self.assertEqual(len(commands), 1)
            self.assertTrue(self.link.windows[label])
        self.assertEqual(await self.session.cleanup(), [])

    async def test_cleanup_requires_policy_readback_and_can_be_retried(self):
        await self.session.begin()
        self.link.ignore_restore = True
        self.assertTrue(await self.session.cleanup())
        self.assertTrue(self.session.active)
        self.assertEqual(json.loads(self.path.read_text())["phase"], "needs cleanup")
        self.link.ignore_restore = False
        self.assertEqual(await self.session.cleanup(), [])
        self.assertFalse(self.session.active)

    async def test_unsupported_source_rxps_is_explicitly_skipped(self):
        self.link.unsupported_source = True
        await self.session.begin()
        self.assertEqual(self.session.saved["source"]["rxps"], "unsupported")
        self.assertNotIn(("source", "set radio.rxps off"), self.link.calls)
        self.assertEqual(await self.session.cleanup(), [])

    async def test_existing_window_prevents_any_mutation(self):
        self.link.windows["source"] = True
        with self.assertRaisesRegex(ota.OtaError, "already has a temporary"):
            await self.session.begin()
        self.assertFalse(self.session.active)
        self.assertFalse(any(text.startswith("set ") or text.startswith("ota config ") for _, text in self.link.calls))

    async def test_legacy_future_temp_schedule_is_preserved_before_any_mutation(self):
        future = "> 1:910.540,62.5,7,5,32@1900000000-1900000900"
        for label in ("source", "target"):
            self.link.calls.clear()
            self.link.temp_schedules = {"source": "-none-", "target": "-none-"}
            self.link.temp_schedules[label] = future
            with self.subTest(label=label), self.assertRaisesRegex(ota.OtaError, "scheduled temporary"):
                await self.session.begin()
            self.assertFalse(self.session.active)
            self.assertEqual(self.link.temp_schedules[label], future)
            self.assertFalse(any(text.startswith(("set ", "ota config ", "tempradio ", "normalradio"))
                                 for _, text in self.link.calls))

    async def test_schedule_queued_during_preparation_is_checked_again(self):
        await self.session.snapshot()
        self.link.calls.clear()
        self.link.temp_schedules["target"] = "> 1:910.540,62.5,7,5@1900000000-1900000900"
        with self.assertRaisesRegex(ota.OtaError, "scheduled temporary"):
            await self.session.begin()
        self.assertFalse(self.session.active)
        self.assertFalse(any(text.startswith(("set ", "ota config ", "tempradio ", "normalradio"))
                             for _, text in self.link.calls))


    async def test_roles_without_primary_schedule_getter_can_still_update(self):
        self.link.temp_schedules = {"source": "Unknown command", "target": "Error: unsupported"}
        await self.session.begin()
        self.assertTrue(self.session.active)
        self.assertEqual(await self.session.cleanup(), [])

    async def test_schedule_read_timeout_cannot_be_treated_as_unsupported(self):
        original = self.link.local_command
        async def uncertain(text):
            if text == "get tempradioat":
                raise ota.TransmissionError("unknown command result after timeout")
            return await original(text)
        self.link.local_command = uncertain
        with self.assertRaises(ota.TransmissionError):
            await self.session.begin()
        self.assertFalse(self.session.active)
        self.assertFalse(any(text.startswith(("set ", "ota config ", "tempradio ", "normalradio"))
                             for _, text in self.link.calls))


class Radio2Transport(FakeTransport):
    """Model native profile routing and target RAM leases clearing on reboot."""
    supports_radio2 = True
    acl_only = True

    def __init__(self):
        super().__init__()
        self.primary = {"source": "910.525024,62.5,7,5", "target": "910.525024,62.5,7,7"}
        self.transfer = None
        self.secondary = "off"
        self.secondary_saved = "off (manual)"
        self.cross = "off"
        self.secondary_schedule = {"radioat2": "slots: ", "tempradioat2": "slots: "}
        self.peer_radio = "radio"
        self.peer_timeout = 30
        self.remote_timeout = 40
        self.ready_at = 0
        self.lose_secondary_ack = False
        self.block_normal = False
        self.journal = None
        self.verify_calls = 0

    async def verify_peer(self, key, password):
        self.verify_calls += 1

    async def remote_command(self, key, password, text):
        desired = self.transfer if self.windows["target"] else self.primary["target"]
        actual = self.secondary if self.peer_radio == "radio2" else self.primary["source"]
        if actual == "off" or wizard.radio_tuple(actual)[:3] != wizard.radio_tuple(desired)[:3]:
            self.calls.append(("unreachable", text))
            raise TimeoutError("wrong control profile")
        return await super().remote_command(key, password, text)

    async def command(self, label, text):
        if label == "source":
            result = None
            if text == "ota.peer radio":
                result = "OTApeer radio=" + self.peer_radio
            elif text.startswith("ota.peer radio "):
                profile = text.split()[-1]
                if profile == "radio2" and (self.secondary == "off" or time.monotonic() < self.ready_at):
                    raise ota.OtaError("radio2 is not eligible yet")
                self.peer_radio = profile
                result = "OK"
            elif text == "ota.peer timeout":
                result = f"OTApeer timeout={self.peer_timeout}s"
            elif text.startswith("ota.peer timeout "):
                self.peer_timeout = int(text.split()[-1])
                result = "OK"
            elif text == "get radio2":
                result = self.secondary_saved
            elif text == "get tempradio2":
                result = self.secondary
            elif text == "get radio2.cross":
                result = self.cross
            elif text in ("get radioat2", "get tempradioat2"):
                result = self.secondary_schedule[text.split()[1]]
            elif text.startswith("set tempradio2 "):
                if self.journal:
                    record = json.loads(self.journal.read_text())
                    assert record["source_radio"] == "radio2"
                    assert record["settings"]["source"]["peer_timeout"] == "OTApeer timeout=30s"
                    assert record["source_window_attempted"] is True
                value = text.removeprefix("set tempradio2 ")
                self.secondary = value
                self.ready_at = time.monotonic() + 2 if value != "off" else 0
                self.calls.append((label, text))
                if self.lose_secondary_ack:
                    self.lose_secondary_ack = False
                    raise TimeoutError("lost source lease ACK")
                return "OK"
            if result is not None:
                self.calls.append((label, text))
                return result
        if text == "get radio":
            self.calls.append((label, text))
            return self.primary[label]
        if label == "target" and text.startswith("tempradio "):
            self.transfer = text.split()[1]
        if label == "target" and text == "normalradio" and self.block_normal:
            self.calls.append((label, text))
            raise TimeoutError("normalradio not delivered")
        if label == "target" and text.startswith("ota pull "):
            if self.secondary == "off" or not self.windows["target"]:
                raise AssertionError("pull without owned source radio2 and target lease")
            self.calls.append((label, text))
            self.mid = text.split()[2]
            self.slot_mid = self.mid
            self.download_started.set()
            if self.lost_pull:
                raise TimeoutError("lost pull acknowledgment")
            return "OK pulling"
        return await super().command(label, text)


class Radio2SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "session.json"
        self.link = Radio2Transport()
        self.link.journal = self.path
        self.session = wizard.DirectSession(
            self.link, KEY, "secret-for-test", self.path,
            source_radio="radio2", transfer_radio=(910.250, 62.5, 7, 5),
            window_minutes=720, progress=lambda _: None, poll_seconds=.001,
            reboot_timeout=.03, transfer_timeout=1)

    async def test_secondary_is_owned_and_established_before_target_retune(self):
        await self.session.begin()
        calls = self.link.calls
        source_lease = next(i for i, (label, text) in enumerate(calls) if text.startswith("set tempradio2 "))
        target_lease = next(i for i, (label, text) in enumerate(calls) if text.startswith("tempradio "))
        self.assertLess(source_lease, target_lease)
        self.assertEqual(self.link.peer_radio, "radio2")
        self.assertEqual(self.link.peer_timeout, 60)
        self.assertEqual(self.link.remote_timeout, 75)
        self.assertEqual(self.link.lease_minutes["target"], 720)
        self.assertFalse(any(label == "source" and text.startswith(("tempradio ", "normalradio", "set radio2.cross"))
                             for label, text in calls))
        # An acknowledged target lease needs no RF getter to confirm it.
        self.assertNotIn(("target", "get tempradio"), calls[target_lease + 1:])
        self.assertEqual(await self.session.cleanup(), [])
        self.assertEqual((self.link.secondary, self.link.cross), ("off", "off"))
        self.assertEqual((self.link.peer_radio, self.link.peer_timeout, self.link.remote_timeout), ("radio", 30, 40))
        normal = calls.index(("target", "normalradio"))
        released = calls.index(("source", "set tempradio2 off"))
        rxps = calls.index(("target", "set radio.rxps level 8 preamble 16"))
        self.assertLess(normal, released)
        self.assertLess(released, rxps)

    async def test_lost_relative_lease_acks_are_reconciled_on_transfer_profile(self):
        self.link.lost_window_ack = {"target"}
        self.link.lose_secondary_ack = True
        await self.session.begin()
        self.assertEqual(sum(text.startswith("set tempradio2 ") for _, text in self.link.calls), 1)
        self.assertEqual(sum(text.startswith("tempradio ") for _, text in self.link.calls), 1)
        self.assertFalse(any(text == "get tx" for _, text in self.link.calls))
        self.assertEqual(await self.session.cleanup(), [])

    async def test_acl_inspection_avoids_login_and_key_proof(self):
        await self.session.inspect()
        self.assertEqual(self.link.verify_calls, 0)
        self.assertNotIn(("target", "get public.key"), self.link.calls)
        self.assertFalse(any(text.startswith(("set ", "ota.peer radio ", "ota.peer timeout "))
                             for _, text in self.link.calls))

    async def test_password_inspection_uses_transport_key_proof_once(self):
        self.link.acl_only = False
        await self.session.inspect()
        self.assertEqual(self.link.verify_calls, 1)
        self.assertNotIn(("target", "get public.key"), self.link.calls)

    async def test_configured_secondary_cross_and_schedules_are_never_replaced(self):
        cases = (("secondary_saved", "911.15,250,7,5,rxtx"),
                 ("secondary", "911.15,250,7,5,rxtx,720"), ("cross", "on"),
                 ("secondary_schedule", {"radioat2": "slots: 1@1900000000", "tempradioat2": "slots: "}))
        for field, value in cases:
            with self.subTest(field=field):
                original = getattr(self.link, field)
                setattr(self.link, field, value)
                self.link.calls.clear()
                self.session.saved = {}
                with self.assertRaises(ota.OtaError):
                    await self.session.begin()
                self.assertFalse(self.session.active)
                self.assertFalse(any(text.startswith(("set ", "ota config ", "tempradio ", "ota.peer radio "))
                                     for _, text in self.link.calls))
                setattr(self.link, field, original)

    async def test_recovery_restores_recorded_profile_and_tuple(self):
        await self.session.begin()
        recovery = wizard.DirectSession(self.link, KEY, "secret-for-test", self.path, progress=lambda _: None)
        recovery._state = json.loads(self.path.read_text())
        recovery.saved, recovery.active = recovery._state["settings"], True
        self.assertEqual(await recovery.cleanup(), [])
        self.assertEqual(recovery.source_radio, "radio2")
        self.assertEqual(recovery.transfer_radio, (910.25, 62.5, 7, 5))
        self.assertEqual(self.link.secondary, "off")

    async def test_recovery_reconciles_crash_before_selection_is_recorded(self):
        await self.session.begin()
        self.session._state["control_radio"] = "radio"
        self.link.peer_radio = "radio"
        self.assertEqual(await self.session.cleanup(), [])
        self.assertEqual(self.link.secondary, "off")

    async def test_expired_leases_recover_from_stale_native_radio2_selection(self):
        await self.session.begin()
        self.link.secondary = "off"
        self.link.windows["target"] = False
        self.link.peer_radio = "radio2"
        self.assertEqual(await self.session.cleanup(), [])
        self.assertEqual(self.link.peer_radio, "radio")
        self.assertNotIn(("source", "set tempradio2 off"), self.link.calls)

    async def test_retry_after_secondary_release_keeps_source_primary_unchanged(self):
        await self.session.snapshot()
        self.session._state.update(source_window_attempted=True, source_window_active=False)
        original = self.link.remote_command
        attempts = 0
        async def lose_first(key, password, text):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("lost status reply")
            return await original(key, password, text)
        self.link.remote_command = lose_first
        self.assertIn("no download", await self.session.remote("ota status"))
        self.assertEqual(attempts, 2)
        self.assertFalse(any(text.startswith(("tempradio ", "normalradio"))
                             for _, text in self.link.calls))

    async def test_application_reboot_is_proved_on_primary_before_secondary_release(self):
        before = await self.session.inspect()
        package = wizard.full_package(application(), 2048)
        after = await self.session.install(package, before)
        self.assertEqual(after.version, NEW)
        self.assertEqual(self.link.peer_radio, "radio")
        self.assertNotEqual(self.link.secondary, "off")
        self.assertEqual(await self.session.cleanup(), [])

    async def test_install_recovery_reconciles_reboot_before_primary_selection(self):
        before = await self.session.inspect()
        package = wizard.full_package(application(), 2048)
        self.link.defer_install = True
        with self.assertRaisesRegex(ota.OtaError, "confirm the installed"):
            await self.session.install(package, before)
        self.link.finish_install()
        # Simulate a crash before _installed can record primary selection.
        self.session._state["control_radio"] = "radio2"
        self.link.peer_radio = "radio2"
        self.assertEqual(await self.session.cleanup(), [])
        self.assertTrue(self.session._state["install_verified"])
        self.assertEqual(self.link.policies["target"]["autoinstall"], "trusted")

    async def test_temporary_secondary_is_rechecked_after_preparation(self):
        await self.session.snapshot()
        self.link.secondary = "911.15,250,7,5,rxtx,30"
        self.link.calls.clear()
        with self.assertRaisesRegex(ota.OtaError, "temporary radio2"):
            await self.session.begin()
        self.assertFalse(any(text.startswith(("set ", "ota config ", "tempradio "))
                             for _, text in self.link.calls))

    async def test_recovery_keeps_secondary_when_target_has_not_returned(self):
        await self.session.begin()
        self.link.block_normal = True
        self.assertTrue(await self.session.cleanup())
        self.assertNotEqual(self.link.secondary, "off")
        self.assertEqual(self.link.peer_radio, "radio2")
        self.assertNotIn(("source", "set tempradio2 off"), self.link.calls)
        self.link.block_normal = False
        self.assertEqual(await self.session.cleanup(), [])

    def test_options_are_validated_and_companion_radio2_is_rejected(self):
        for minutes in (0, 10081, 1.5, True):
            with self.subTest(minutes=minutes), self.assertRaises(ota.OtaError):
                wizard.DirectSession(self.link, KEY, "", self.path, window_minutes=minutes)
        with self.assertRaisesRegex(ota.OtaError, "USB repeater"):
            wizard.DirectSession(FakeTransport(), KEY, "", self.path, source_radio="radio2")
        self.assertEqual(wizard.DirectSession(self.link, KEY, "", self.path).poll_seconds, 45)


class PrimaryRoutingTransport(Radio2Transport):
    supports_radio2 = False

    def __init__(self):
        super().__init__()
        self.source_transfer = None

    async def remote_command(self, key, password, text):
        desired = self.transfer if self.windows["target"] else self.primary["target"]
        actual = self.source_transfer if self.windows["source"] else self.primary["source"]
        if wizard.radio_tuple(actual)[:3] != wizard.radio_tuple(desired)[:3]:
            self.calls.append(("unreachable", text))
            raise TimeoutError("wrong primary control channel")
        return await FakeTransport.remote_command(self, key, password, text)

    async def command(self, label, text):
        if label == "source" and text.startswith("tempradio "):
            self.source_transfer = text.split()[1]
        if text == "get tempradio" and self.windows[label]:
            self.calls.append((label, text))
            return self.source_transfer if label == "source" else self.transfer
        if label == "target" and text.startswith("ota pull "):
            return await FakeTransport.command(self, label, text)
        return await super().command(label, text)


class PrimaryTransferSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "session.json"
        self.link = PrimaryRoutingTransport()
        self.session = wizard.DirectSession(
            self.link, KEY, "secret-for-test", self.path,
            transfer_radio=(910.250, 62.5, 7, 5), window_minutes=720,
            progress=lambda _: None, poll_seconds=.001,
            reboot_timeout=.03, transfer_timeout=1)

    async def test_custom_primary_cleanup_restores_both_channels_before_rxps(self):
        await self.session.begin()
        self.assertEqual(await self.session.cleanup(), [])
        calls = self.link.calls
        target_return = calls.index(("target", "normalradio"))
        source_return = calls.index(("source", "normalradio"))
        target_rxps = calls.index(("target", "set radio.rxps level 8 preamble 16"))
        self.assertLess(target_return, source_return)
        self.assertLess(source_return, target_rxps)
        self.assertNotIn(("source", "ota.peer radio radio2"), calls)

    async def test_recovery_of_source_reboot_uses_original_expiry(self):
        await self.session.begin()
        expiry = self.session._state["lease_expires_at"]
        self.link.windows["source"] = False
        # The next required read reconciles a rebooted source, rather than
        # changing the target lease or starting another full-duration lease.
        self.assertIn("no download", await self.session.remote("ota status"))
        self.assertEqual(self.session._state["lease_expires_at"], expiry)
        source_leases = [text for label, text in self.link.calls if label == "source" and text.startswith("tempradio ")]
        self.assertEqual(len(source_leases), 2)
        self.assertLess(int(source_leases[-1].rsplit(",", 1)[1]), 720)
        self.assertEqual(await self.session.cleanup(), [])

    async def test_short_remaining_lease_is_not_rounded_up_or_reopened(self):
        await self.session.snapshot()
        self.session._state["lease_expires_at"] = time.time() + 30
        with self.assertRaisesRegex(ota.OtaError, "expired"):
            await self.session._alternate_primary_source()
        self.assertFalse(any(text.startswith(("tempradio ", "normalradio"))
                             for _, text in self.link.calls))

    async def test_recovery_proves_rebooted_install_when_source_still_on_transfer(self):
        before = await self.session.inspect()
        package = wizard.full_package(application(), 2048)
        self.link.defer_install = True
        with self.assertRaisesRegex(ota.OtaError, "confirm the installed"):
            await self.session.install(package, before)
        self.link.finish_install()
        self.link.windows["source"] = True
        self.session._state["source_window_active"] = True
        self.assertEqual(await self.session.cleanup(), [])
        self.assertTrue(self.session._state["install_verified"])
        self.assertEqual(self.link.policies["target"]["autoinstall"], "trusted")


if __name__ == "__main__":
    unittest.main()
