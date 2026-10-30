"""Offline route proof, one-shot mutations and restoration for the relay HIL tool."""
import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import routed_ota_relay_probe as probe
from test_lora_ota import firmware, mota_blob

SOURCE, TARGET, RELAY = "11" * 32, "22" * 32, "33" * 32


class Clock:
    def __init__(self):
        self.now = 0

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


class Rig:
    def __init__(self):
        image = firmware(bytes(range(256)) * 80, 0x01120003, hw="test_board")
        self.blob = mota_blob(image, version=0x01120003, hw="test_board")
        self.info = probe.ota.parse_mota(self.blob)
        self.endf = probe.ota.parse_endf(image)
        self.calls = []
        self.remote_calls = []
        self.state, self.have, self.cross = "idle", 0, "off"
        self.total = (self.info.payload_size + self.info.block_size - 1) // self.info.block_size
        self.mid = self.info.manifest_id.lower()
        self.current_body, self.current_version = bytes.fromhex("0011223344556677"), 0x01120002
        self.ignore_cross = self.lost_lease = self.lost_pull = self.lost_install = self.lost_cancel = False
        self.foreign_at_cleanup = False
        self.fail_on_resume = False
        self.verification_reply = "parsed=1 root=1 payload=1 ok=1"
        self.verification_timeout = False
        self.source, self.relay, self.target = (Node(self, label) for label in ("source", "relay", "target"))
        self.nodes = {node.label: node for node in (self.source, self.relay, self.target)}

    def progress(self):
        if self.state == "idle":
            return "fetch idle"
        if self.state == "active" and (self.cross == "on" or self.ignore_cross):
            assert probe.same_rf(self.target.active_rf, (908.5, 500., 5, 5))
            self.have = min(self.total, self.have + 2)
            if self.have == self.total:
                self.state = "done"
        return f"fetch {self.state} {self.have}/{self.total} id={self.mid}"


class Node:
    supports_radio2, acl_only = True, True

    def __init__(self, rig, label):
        self.rig, self.label = rig, label
        self.source_key = {"source": SOURCE, "relay": RELAY, "target": TARGET}[label]
        self.rf = (908.5, 500., 5, 5) if label == "target" else (909.5, 500., 5, 5)
        self.temp, self.repeat, self.rxps = "off", "on", True
        self.policy = {"hops": "3", "autofetch": "off", "autoinstall": "trusted"}
        self.remote_timeout, self.rx_frames, self.filter_count = 40, 0, 0
        self.bridge_rule = "set fr.1 12 m=b rt=a d pri=255"

    @property
    def active_rf(self):
        return self.rf if self.temp == "off" else probe.radio_tuple(self.temp)

    async def local_command(self, text):
        self.rig.calls.append((self.label, text))
        rig = self.rig
        if text == "get radio": return probe.rf_text(self.rf)
        if text == "get tempradio": return self.temp
        if text == "get tempradio2": return "off"
        if text in ("get tempradioat", "get radioat2", "get tempradioat2"): return "-none-"
        if text == "get radio2": return "908.5,500,5,5,rxtx,128" if self.label == "relay" else "off"
        if text == "get radio2.cross": return rig.cross if self.label == "relay" else "off"
        if text == "get repeat": return self.repeat
        if text == "get radio.rxps.config":
            return ("on" if self.rxps else "off") + ",level=8,preamble=16,rx=65625,sleep=60000"
        if text == "ota config": return " ".join(key + "=" + val for key, val in self.policy.items())
        if text in ("get espnow.enabled", "get espnow.running"): return "off" if self.label == "relay" else "on"
        if text == "get bridge.source": return "tx"
        if text == "get bridge.ingress": return "radio"
        if text == "get fr.1": return self.bridge_rule
        if text == "get espnow.stats drops":
            self.filter_count += int(rig.state != "idle")
            return f"filter=0 txfilter={self.filter_count}"
        if text == "get espnow.stats rx": return f"frames={self.rx_frames} decoded={self.rx_frames} mesh={self.rx_frames}"
        if text == "ota.peer radio": return "OTApeer radio=radio"
        if text == "ota.peer timeout": return "OTApeer timeout=30s"
        if text.startswith("set radio2.cross "):
            was_off = rig.cross == "off"
            rig.cross = text.split()[-1]
            if rig.fail_on_resume and was_off and rig.cross == "on" and rig.have:
                rig.fail_on_resume = False
                rig.state = "failed"
            return "OK"
        if text.startswith("ota config "):
            _, _, field, setting = text.split()
            self.policy[field] = setting
            return "OK"
        if text.startswith("set repeat "):
            self.repeat = text.split()[-1]
            return "OK"
        if text == "set radio.rxps off":
            self.rxps = False
            return "OK"
        if text.startswith("set radio.rxps "):
            self.rxps = True
            return "OK"
        if text.startswith("tempradio "):
            self.temp = ",".join(text.split(" ", 1)[1].split(",")[:4]) + ",128"
            if self.label == "target" and rig.lost_lease:
                raise TimeoutError("lost lease acknowledgement")
            return "OK - temp params accepted"
        if text == "normalradio":
            self.temp = "off"
            return "OK"
        if text == "ota status":
            return f"OTA | hw=test_board | target:{rig.info.target_id:08X} | maxblk:2048 | " + (
                "no download" if rig.state == "idle" else "download: dl 0/10 id=" + rig.mid)
        if text == "ota self":
            return "self body=100 image=156 base_hash=" + rig.current_body.hex()
        if text == "ver": return probe.ota.format_version(rig.current_version)
        if text == "ota stats": return rig.progress()
        if text == "ota ls": return "catalog " + rig.mid
        if text.startswith("ota pull "):
            rig.state = "active"
            if rig.lost_pull: raise TimeoutError("lost pull acknowledgement")
            return "OK pulling mid=" + rig.mid
        if text == "ota dev verify":
            if rig.verification_timeout: raise TimeoutError("diagnostic timeout")
            return rig.verification_reply
        if text == "ota folder off": return "OK folder detached"
        if text == "ota install":
            rig.current_body, rig.current_version = rig.endf.body_hash, rig.info.fw_version
            rig.state, self.temp = "idle", "off"
            if rig.lost_install: raise TimeoutError("lost install acknowledgement")
            return "OK installing"
        if text == "ota cancel":
            rig.state = "idle"
            if rig.lost_cancel: raise TimeoutError("lost cancel acknowledgement")
            return "OK dropped session mid=" + rig.mid + "; OTA receive slot confirmed clear"
        raise AssertionError("Unexpected command " + text)

    async def remote_command(self, key, password, text):
        assert key == TARGET and password == ""
        # Native control must remain possible while source and target have
        # no shared LoRa channel, including after an installation reboot.
        assert probe.channels_separate(self.active_rf, self.rig.target.active_rf)
        self.rx_frames += 1
        self.rig.remote_calls.append(text)
        return await self.rig.target.local_command(text)

    async def reset_remote_session(self, key):
        assert key == TARGET

    async def serve(self, blob, stop):
        assert blob == self.rig.blob
        await stop.wait()


class RelayProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rig, self.clock = Rig(), Clock()
        self.args = probe.arguments(["--target-key", TARGET, "--timeout", "60", "--reboot-timeout", "10",
                                     "--poll", "1", "--window-minutes", "90"])
        self.probe = probe.RelayProbe(self.args, self.rig.blob, self.rig.source, self.rig.relay,
                                     self.rig.target, sleep=self.clock.sleep, clock=lambda: self.clock.now)

    async def test_complete_transfer_requires_relay_stall_resume_and_restores_settings(self):
        result = await self.probe.run()
        self.assertEqual(result["result"], "PASS", result)
        self.assertTrue(result["relay_qualified"])
        self.assertTrue(result["settings_restored"])
        self.assertTrue(result["receive_slot_clear_confirmed"])
        self.assertEqual(self.rig.cross, "off")
        for node in self.rig.nodes.values():
            self.assertEqual(node.temp, "off")
            self.assertTrue(node.rxps)
            self.assertEqual(node.policy, {"hops": "3", "autofetch": "off", "autoinstall": "trusted"})
        self.assertEqual(sum(text.startswith("ota pull ") for _, text in self.rig.calls), 1)
        self.assertEqual(sum(text == "ota cancel" for _, text in self.rig.calls), 1)
        self.assertFalse(any(text == "get tx" for _, text in self.rig.calls))
        data = json.dumps(result)
        for key in (SOURCE, TARGET, RELAY): self.assertNotIn(key, data)

    async def test_lost_leases_and_pull_are_reconciled_without_replay(self):
        self.rig.lost_lease = self.rig.lost_pull = True
        result = await self.probe.run()
        self.assertEqual(result["result"], "PASS", result)
        for label in ("source", "relay", "target"):
            self.assertEqual(sum(node == label and text.startswith("tempradio ")
                                 for node, text in self.rig.calls), 1)
        self.assertEqual(sum(text.startswith("ota pull ") for _, text in self.rig.calls), 1)

    async def test_cross_off_shortcut_fails_the_hop_qualification(self):
        self.rig.ignore_cross = True
        result = await self.probe.run()
        self.assertEqual(result["result"], "FAIL", result)
        self.assertFalse(result["relay_qualified"])
        self.assertEqual(result["failure"]["code"], "download_progressed_while_relay_cross_off")
        self.assertTrue(result["settings_restored"])

    async def test_lost_install_acknowledgement_checks_exact_image_without_cancel(self):
        self.args.install = self.rig.lost_install = True
        result = await self.probe.run()
        self.assertEqual(result["result"], "PASS", result)
        self.assertTrue(result["install_verified"])
        self.assertEqual(sum(text == "ota install" for _, text in self.rig.calls), 1)
        self.assertFalse(any(text == "ota cancel" for _, text in self.rig.calls))

    async def test_verification_uses_usb_witness_without_sending_remote_diagnostic(self):
        result = await self.probe.run()
        self.assertEqual(result["result"], "PASS", result)
        self.assertEqual(result["dev_verification_controller"], "usb_witness")
        self.assertTrue(result["stage_accepted_complete"])
        self.assertTrue(result["stage_verified"])
        self.assertNotIn("ota dev verify", self.rig.remote_calls)

    async def test_flash_staged_diagnostic_unavailable_still_requires_install_body_proof(self):
        self.rig.verification_reply = "ERR nothing to verify (flash-staged: applydelta verifies)"
        self.args.install = True
        result = await self.probe.run()
        self.assertEqual(result["result"], "PASS", result)
        self.assertTrue(result["stage_accepted_complete"])
        self.assertFalse(result["stage_verified"])
        self.assertFalse(result["dev_verification_available"])
        self.assertTrue(result["install_verified"])
        self.assertEqual(sum(text == "ota install" for _, text in self.rig.calls), 1)

    async def test_transfer_only_without_diagnostic_is_not_reported_as_verified_pass(self):
        self.rig.verification_reply = "ERR ota dev verify is local-only"
        self.probe.witness = None
        result = await self.probe.run()
        self.assertEqual(result["result"], "TRANSFER_COMPLETE_VERIFICATION_UNAVAILABLE", result)
        self.assertTrue(result["stage_accepted_complete"])
        self.assertFalse(result["stage_verified"])
        self.assertFalse(result["dev_verification_available"])
        self.assertEqual(result["dev_verification_reason"], "local_only_diagnostic")
        self.assertFalse(any(text == "ota install" for _, text in self.rig.calls))

    async def test_failed_integrity_or_permission_rejection_does_not_install(self):
        for reply in ("verify parsed=1 root=0 payload=0 img=0 ok=0",
                      "ERR payload hash mismatch", "Err - not permitted", "ERR nothing to verify"):
            with self.subTest(reply=reply):
                self.setUp()
                self.args.install = True
                self.rig.verification_reply = reply
                result = await self.probe.run()
                self.assertEqual(result["result"], "FAIL", result)
                self.assertTrue(result["stage_accepted_complete"])
                self.assertFalse(result["stage_verified"])
                self.assertFalse(any(text == "ota install" for _, text in self.rig.calls))

    async def test_diagnostic_timeout_is_not_classified_as_unavailable_or_installed(self):
        self.args.install = self.rig.verification_timeout = True
        result = await self.probe.run()
        self.assertEqual(result["result"], "FAIL", result)
        self.assertEqual(result["failure"]["code"], "command_reply_unknown")
        self.assertIsNone(result["dev_verification_available"])
        self.assertFalse(any(text == "ota install" for _, text in self.rig.calls))

    async def test_sideband_changes_target_from_saved_c_to_transfer_b_then_restores_c(self):
        initial = (907.5, 500., 5, 5)
        self.args.target_initial_radio = self.rig.target.rf = initial
        self.probe = probe.RelayProbe(self.args, self.rig.blob, self.rig.source, self.rig.relay,
                                     self.rig.target, sleep=self.clock.sleep, clock=lambda: self.clock.now)
        result = await self.probe.run()
        self.assertEqual(result["result"], "PASS", result)
        self.assertEqual(result["target_initial_rf"], list(initial))
        self.assertEqual(result["target_rf"], [908.5, 500., 5, 5])
        self.assertTrue(result["target_frequency_change_requested"])
        self.assertTrue(result["target_frequency_change_confirmed"])
        self.assertTrue(result["settings_restored"])
        self.assertEqual(self.rig.target.active_rf, initial)
        commands = [text for label, text in self.rig.calls if label == "target"]
        self.assertEqual(sum(text.startswith("tempradio 908.5,") for text in commands), 1)
        self.assertFalse(any(text.startswith("set radio ") for text in commands))

    async def test_sideband_install_reboot_returns_to_saved_c_without_retuning_source(self):
        initial = (907.5, 500., 5, 5)
        self.args.target_initial_radio = self.rig.target.rf = initial
        self.args.install = self.rig.lost_install = True
        self.probe = probe.RelayProbe(self.args, self.rig.blob, self.rig.source, self.rig.relay,
                                     self.rig.target, sleep=self.clock.sleep, clock=lambda: self.clock.now)
        result = await self.probe.run()
        self.assertEqual(result["result"], "PASS", result)
        self.assertTrue(result["install_verified"])
        self.assertTrue(result["target_frequency_change_confirmed"])
        self.assertEqual(self.rig.target.active_rf, initial)
        self.assertEqual(self.rig.source.active_rf, (909.5, 500., 5, 5))
        self.assertEqual(sum(text == "ota install" for _, text in self.rig.calls), 1)

    async def test_lost_cancel_ack_does_not_replay_or_restore_automatic_install(self):
        self.rig.lost_cancel = True
        result = await self.probe.run()
        self.assertEqual(result["result"], "NEEDS_RECOVERY", result)
        self.assertEqual(sum(text == "ota cancel" for _, text in self.rig.calls), 1)
        self.assertEqual(self.rig.target.policy["autoinstall"], "off")

    async def test_foreign_live_mid_is_never_cancelled(self):
        original = self.probe.transfer
        async def changed():
            await original()
            self.rig.mid = "deadbeef"
        self.probe.transfer = changed
        result = await self.probe.run()
        self.assertEqual(result["result"], "NEEDS_RECOVERY", result)
        self.assertFalse(any(text == "ota cancel" for _, text in self.rig.calls))
        self.assertEqual(self.rig.target.policy["autoinstall"], "off")

    async def test_explicit_failed_same_mid_can_resume_once_when_opted_in(self):
        self.args.resume_failed_once = self.rig.fail_on_resume = True
        result = await self.probe.run()
        self.assertEqual(result["result"], "PASS", result)
        self.assertEqual(sum(text.startswith("ota pull ") for _, text in self.rig.calls), 2)
        self.assertEqual(sum(event["kind"] == "pull_sent" and event["resume"]
                             for event in result["events"]), 1)

    async def test_unconfirmed_server_stop_keeps_target_automatic_policy_off(self):
        async def failure(*args):
            raise RuntimeError("server stopped")
        self.rig.source.serve = failure
        original = self.rig.source.local_command
        async def detach_unknown(text):
            if text == "ota folder off": raise TimeoutError("detach unknown")
            return await original(text)
        self.rig.source.local_command = detach_unknown
        result = await self.probe.run()
        self.assertEqual(result["result"], "NEEDS_RECOVERY", result)
        self.assertEqual(self.rig.target.policy["autoinstall"], "off")

    async def test_existing_window_or_wrong_bridge_rule_refuses_before_mutation(self):
        for case in ("window", "filter"):
            with self.subTest(case=case):
                self.setUp()
                if case == "window": self.rig.source.temp = "909.5,500,5,5,128"
                else: self.rig.source.bridge_rule = "set fr.1 12 m=r d pri=255"
                result = await self.probe.run()
                self.assertEqual(result["result"], "FAIL")
                self.assertFalse(any(text.startswith(("set ", "tempradio ", "ota config "))
                                     for _, text in self.rig.calls))

    async def test_unknown_error_cannot_put_raw_reply_or_full_key_into_report(self):
        async def failure(*args, **kwargs):
            raise RuntimeError("private node description " + TARGET)
        self.probe.snapshot = failure
        result = await self.probe.run()
        self.assertEqual(result["failure"]["code"], "operation_failed")
        self.assertNotIn(TARGET, json.dumps(result))
        self.assertNotIn("private node description", json.dumps(result))


class ParserTests(unittest.TestCase):
    def test_plan_needs_no_radios_package_or_keys(self):
        with mock.patch.object(probe, "execute", side_effect=AssertionError("USB opened")), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(probe.main(["--plan"]), 0)
        self.assertFalse(json.loads(out.getvalue())["opens_usb"])

    def test_bridge_drop_requires_first_slot_all_routes_and_no_conditions(self):
        self.assertTrue(probe.bridge_drop_rule("set fr.1 12 m=b rt=a d pri=255"))
        self.assertTrue(probe.bridge_drop_rule("set fr.1 ota m=b rt=a pri=255 d"))
        for text in ("set fr.2 12 m=b rt=a d pri=255", "set fr.1 12 m=b d pri=255",
                     "set fr.1 12 m=b rt=a d pri=255 f=t", "set fr.1 12 m=b rt=a d pri=255 e=0"):
            self.assertFalse(probe.bridge_drop_rule(text))

    def test_overlap_and_short_window_are_rejected_without_ports(self):
        for flags in (["--target-radio", "909.6,500,5,5"], ["--window-minutes", "1"],
                      ["--target-initial-radio", "909.5,500,5,5"]):
            with self.subTest(flags=flags), self.assertRaises(probe.ProbeError):
                probe.validate_arguments(probe.arguments(["--plan", *flags]))

    def test_progress_rejects_partial_or_foreign_format(self):
        self.assertEqual(probe.parse_progress("fetch done 10/10 id=1234ABCD")["state"], "complete")
        self.assertEqual(probe.parse_progress("fetch mf 0/0 id=1234ABCD")["state"], "active")
        self.assertEqual(probe.parse_progress("fetch failed:manifest timeout 0/0 id=1234ABCD")["state"], "failed")
        for text in ("fetch dl 2/10", "fetch dl 11/10 id=1234abcd", "fetch done 0/0 id=1234abcd", "self id=1234abcd"):
            with self.subTest(text=text), self.assertRaises(probe.ProbeError): probe.parse_progress(text)


if __name__ == "__main__":
    unittest.main()
