#!/usr/bin/env python3
"""Finite LoRa relay OTA test with ESP-NOW administration, not payload transport.

The source uses native ACL commands through one USB RepeaterTransport. Source
and target must already use different saved LoRa channels, with an ESP-NOW
control bridge and an unconditional OTA bridge-drop rule in slot 1. The relay
uses the source primary and a configured RXTX secondary matching the target's
transfer channel. --target-initial-radio can pin a different saved channel;
the ESP-NOW command opens the target's temporary transfer channel and normal
operation or an installation reboot returns it to its saved channel.
This controller owns only temporary primary windows, OTA policies, RXPS,
repeating, and the relay's cross switch. It never changes bridge credentials,
saved RF tuples, filter rows, ACL entries, or bootloaders. --plan opens no USB.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import time

OTA_TOOLS = Path(__file__).resolve().parents[1] / "lora_ota"
if str(OTA_TOOLS) not in sys.path:
    sys.path.insert(0, str(OTA_TOOLS))

import lora_ota as ota
from ota_wizard_repeater import RepeaterTransport
from ota_wizard_session import DirectSession, Package, radio_tuple, temp_state, validate_package
from serial_session import assert_port_idle


class ProbeError(RuntimeError):
    """A fixed diagnostic code; raw replies and keys never enter the report."""

    def __init__(self, code, operation=None):
        self.code, self.operation = code, operation
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise ProbeError(code)


def value(reply):
    return reply.strip().removeprefix(">").strip()


def rf_argument(text):
    try:
        return ota.parse_temp_radio(text + ",90")[:4]
    except (ValueError, argparse.ArgumentTypeError) as exc:
        raise argparse.ArgumentTypeError("Use valid frequency,BW,SF,CR") from exc


def rf_text(rf):
    return ",".join(format(number, ".9g") for number in rf)


def same_rf(actual, expected):
    return all(abs(a - b) <= .001 for a, b in zip(actual, expected))


def initial_target_radio(args):
    return args.target_initial_radio or args.target_radio


def channels_separate(first, second):
    return abs(first[0] - second[0]) * 1000 > (first[1] + second[1]) / 2 + 50


def ota_policy(reply):
    fields = dict(re.findall(r"\b(hops|autofetch|autoinstall)=([A-Za-z0-9]+)", reply))
    require(set(fields) == {"hops", "autofetch", "autoinstall"}, "invalid_ota_policy")
    require(fields["hops"].isdigit() and 0 <= int(fields["hops"]) <= 63,
            "invalid_ota_hop_limit")
    require(fields["autofetch"] in ("off", "any", "signed")
            and fields["autoinstall"] in ("off", "trusted"), "invalid_ota_policy")
    return fields


def bridge_drop_rule(reply):
    """Require first-slot, highest-priority, unconditional bridge OTA drop."""
    try:
        tokens = shlex.split(value(reply).lower())
    except ValueError:
        return False
    allowed = {"set", "fr.1", "flood.rule.1", "ota", "t=ota", "type=ota", "12", "t=12", "type=12",
               "m=b", "mode=bridge", "rt=a", "route=any", "pri=255", "priority=255",
               "d", "drop", "h=*", "hops=all", "e=1", "e=on", "enabled=on"}
    return (bool(tokens) and set(tokens) <= allowed
            and any(token in tokens for token in ("fr.1", "flood.rule.1"))
            and any(token in tokens for token in ("ota", "t=ota", "type=ota", "12", "t=12", "type=12"))
            and any(token in tokens for token in ("m=b", "mode=bridge"))
            and any(token in tokens for token in ("rt=a", "route=any"))
            and any(token in tokens for token in ("pri=255", "priority=255"))
            and any(token in tokens for token in ("d", "drop")))


def counters(reply):
    return {key: int(number) for key, number in re.findall(r"\b([A-Za-z_]+)=(\d+)\b", reply)}


def counter_delta(before, after):
    return {key: (after[key] - number) & 0xffffffff
            for key, number in before.items() if key in after}


def parse_progress(reply):
    section = re.search(r"\bfetch\s+([^|]+)", reply, re.I)
    require(section is not None, "missing_fetch_status")
    text = section[1].strip()
    if re.match(r"idle\b", text, re.I):
        return {"state": "idle", "mid": None, "have": 0, "total": 0}
    count = re.search(r"\b(\d+)/(\d+)\b", text)
    mid = re.search(r"\bid=([0-9a-fA-F]{8})\b", text)
    require(count is not None and mid is not None, "incomplete_fetch_status")
    have, total = map(int, count.groups())
    state = ("failed" if text.lower().startswith("failed") else
             "complete" if re.match(r"(?:done|ready to install)\b", text, re.I) else "active")
    require(0 <= have <= total <= 65536 and (state != "complete" or total > 0),
            "invalid_fetch_count")
    return {"state": state, "mid": mid[1].lower(), "have": have, "total": total}


def no_schedules(reply):
    return value(reply).lower() in ("off", "none", "-none-", "slots:")


def same_rxps(actual, desired):
    if desired.level is not None and 1 <= desired.level <= 10:
        return (actual.enabled, actual.level, actual.preamble) == (desired.enabled, desired.level, desired.preamble)
    return actual == desired


def verification_unavailable(reply):
    """Known capability limits only; integrity and transport errors are fatal."""
    text = value(reply).strip().lower()
    # ESP32 flash staging deliberately has no contiguous data() view. The
    # current dev helper supports streaming nRF52 stores, but the ESP32 apply
    # path performs its own streaming verification when installing the image.
    if text == "err nothing to verify (flash-staged: applydelta verifies)":
        return "esp32_flash_diagnostic_unavailable"
    text = re.sub(r"^(?:err(?:or)?\s*[:\-]?\s*)", "", text)
    if text in ("unknown command", "unsupported", "unsupported command", "not supported"):
        return "unsupported_command"
    if re.fullmatch(r"ota dev(?: verify)? (?:is |requires )?(?:local[- ]only|usb[- ]only|physical usb)", text):
        return "local_only_diagnostic"
    return None


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-port")
    parser.add_argument("--source-usb-serial")
    parser.add_argument("--source-key", help="Optional additional full source identity pin")
    parser.add_argument("--target-key", help="Full target key; used only in memory")
    parser.add_argument("--relay-port")
    parser.add_argument("--relay-usb-serial")
    parser.add_argument("--relay-key", help="Optional additional full relay identity pin")
    parser.add_argument("--target-port", help="Optional independent read-only USB witness")
    parser.add_argument("--target-usb-serial")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--source-radio", type=rf_argument, default=(909.5, 500., 5, 5))
    parser.add_argument("--target-radio", type=rf_argument, default=(908.5, 500., 5, 5))
    parser.add_argument("--target-initial-radio", type=rf_argument,
                        help="Saved target FREQ,BW,SF,CR before the temporary transfer; defaults to --target-radio")
    parser.add_argument("--preamble", type=int, default=128)
    parser.add_argument("--window-minutes", type=int, default=90)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--reboot-timeout", type=float, default=300)
    parser.add_argument("--outage", type=float, default=8)
    parser.add_argument("--poll", type=float, default=10)
    parser.add_argument("--resume-failed-once", action="store_true")
    parser.add_argument("--skip-counterfactual", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--expected-body", help="Expected eight-byte running body hash")
    parser.add_argument("--expected-version", help="Expected firmware version, checked against package")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--allow-transfer-and-clear-stage", action="store_true")
    parser.add_argument("--plan", action="store_true")
    return parser.parse_args(argv)


def validate_arguments(args):
    require(all(math.isfinite(number) for number in
                (args.timeout, args.reboot_timeout, args.outage, args.poll)), "nonfinite_timing")
    require(30 <= args.timeout <= 3600 and 5 <= args.reboot_timeout <= 600,
            "invalid_deadline")
    require(6 <= args.outage <= 30 and .1 <= args.poll <= 60, "invalid_poll_or_outage")
    require(1 <= args.window_minutes <= 10080 and 16 <= args.preamble <= 65535,
            "invalid_window_or_preamble")
    require(args.window_minutes * 60 >= args.timeout + args.reboot_timeout + 120,
            "temporary_window_too_short")
    require(channels_separate(args.source_radio, args.target_radio), "source_and_target_channels_overlap")
    require(channels_separate(args.source_radio, initial_target_radio(args)),
            "source_and_initial_target_channels_overlap")
    if args.plan:
        return
    require(args.allow_transfer_and_clear_stage, "explicit_transfer_consent_required")
    require(all((args.source_port, args.source_usb_serial, args.target_key,
                 args.package, args.report)), "source_target_package_report_required")
    require(bool(args.relay_port) == bool(args.relay_usb_serial), "relay_usb_identity_required")
    require(bool(args.target_port) == bool(args.target_usb_serial), "target_usb_identity_required")
    require(args.relay_port or args.skip_counterfactual, "relay_usb_required_for_counterfactual")
    for key in (args.target_key, args.source_key, args.relay_key):
        require(key is None or re.fullmatch(r"[0-9a-fA-F]{64}", key), "invalid_full_identity_pin")
    ports = [os.path.realpath(port) for port in (args.source_port, args.relay_port, args.target_port) if port]
    require(len(set(ports)) == len(ports), "usb_ports_must_be_distinct")
    require(not args.report.exists() and args.report.parent.is_dir(), "fresh_report_required")
    require(args.expected_body is None or re.fullmatch(r"[0-9a-fA-F]{16}", args.expected_body),
            "invalid_expected_body")


def verify_usb(port, expected_serial):
    from serial.tools import list_ports
    matches = [item for item in list_ports.comports()
               if os.path.realpath(item.device) == os.path.realpath(port)]
    require(len(matches) == 1 and (matches[0].serial_number or "").upper() == expected_serial.upper(),
            "usb_identity_mismatch")
    assert_port_idle(port)
    info = matches[0]
    return {"usb_pin_sha256": hashlib.sha256(expected_serial.upper().encode()).hexdigest()[:16],
            "vid": info.vid, "pid": info.pid}


class RelayProbe:
    def __init__(self, args, blob, source, relay=None, witness=None, *, persist=lambda _: None,
                 sleep=asyncio.sleep, clock=time.monotonic):
        self.args, self.source, self.relay, self.witness = args, source, relay, witness
        self.persist, self.sleep, self.clock = persist, sleep, clock
        self.blob, self.info = bytes(blob), ota.parse_mota(blob)
        require(not self.info.is_bootloader, "application_packages_only")
        self.mid = self.info.manifest_id.lower()
        body = bytes.fromhex(args.expected_body) if args.expected_body else None
        if self.info.is_full:
            embedded = ota.parse_endf(self.info.payload)
            require(body is None or body == embedded.body_hash, "full_package_body_pin_mismatch")
            body = embedded.body_hash
        require(body is not None, "delta_requires_expected_body")
        require(args.expected_version is None or ota.extract_reply_version(args.expected_version)
                == self.info.fw_version, "package_version_pin_mismatch")
        self.package = Package(self.blob, body)
        self.inspector = DirectSession(source, args.target_key, "", Path("unused-routed-probe.json"),
                                       progress=lambda _: None)
        self.saved, self.active, self.pull_attempted = {}, False, False
        self.install_attempted = self.install_verified = self.cancel_sent = False
        self.slot_clear = False
        self.server_task = self.server_stop = None
        self.started = clock()
        self.report = {"schema": 1, "result": "RUNNING", "events": [],
                       "started_utc": datetime.now(timezone.utc).isoformat(),
                       "manifest_id": self.mid, "package_bytes": len(self.blob),
                       "package_payload_bytes": self.info.payload_size,
                       "block_size": self.info.block_size,
                       "source_rf": list(args.source_radio), "target_rf": list(args.target_radio),
                       "target_initial_rf": list(initial_target_radio(args)),
                       "target_frequency_change_requested": abs(initial_target_radio(args)[0] - args.target_radio[0]) > .001,
                       "target_frequency_change_confirmed": False,
                       "preamble": args.preamble, "ota_hops": 1,
                       "independent_rf_capture": False, "target_usb_witness": witness is not None,
                       "proof_scope": "frequency separation, bridge OTA exclusion, relay cross-off/on counterfactual",
                       "relay_qualified": False, "install_requested": args.install,
                       "install_verified": False, "settings_restored": False,
                       "stage_accepted_complete": False, "stage_verified": False,
                       "dev_verification_available": None}

    def event(self, kind, **fields):
        require(len(self.report["events"]) < 4096, "event_limit_reached")
        self.report["events"].append({"kind": kind, "elapsed_s": round(self.clock() - self.started, 3), **fields})
        self.persist(self.report)

    async def command(self, label, text, *, tries=3, accept_error_reply=False):
        # Only idempotent/read-only callers use retries. Relative leases,
        # pull, install and cancel pass tries=1 and reconcile their outcome.
        for attempt in range(tries):
            try:
                if label == "target":
                    answer = await self.source.remote_command(self.args.target_key, "", text)
                else:
                    link = self.source if label == "source" else self.relay if label == "relay" else self.witness
                    require(link is not None, "missing_usb_controller")
                    answer = await link.local_command(text)
                answer = value(answer)
                if not answer or (not accept_error_reply and re.match(
                        r"(?i)^(?:err(?:or)?\b|unknown\b|unsupported\b)", answer)):
                    raise ProbeError("command_rejected", label + ":" + text)
                return answer
            except (TimeoutError, asyncio.TimeoutError, ConnectionError, ota.TransmissionError):
                if attempt == tries - 1:
                    raise ProbeError("command_reply_unknown", label + ":" + text)
                await self.sleep(1)
            except ota.OtaError:
                raise ProbeError("command_rejected", label + ":" + text) from None

    async def progress(self):
        sample = parse_progress(await self.command("target", "ota stats"))
        require(sample["state"] == "idle" or sample["mid"] == self.mid,
                "target_has_different_manifest")
        return sample

    async def snapshot(self):
        require(self.source.source_key.lower() != self.args.target_key.lower(), "source_equals_target")
        for label, expected in (("source", self.args.source_key), ("relay", self.args.relay_key)):
            link = self.source if label == "source" else self.relay
            if link is not None and expected:
                require(link.source_key.lower() == expected.lower(), "usb_public_key_pin_mismatch")
        if self.relay:
            require(self.relay.source_key.lower() not in
                    (self.source.source_key.lower(), self.args.target_key.lower()), "relay_identity_not_distinct")
        if self.witness:
            require(self.witness.source_key.lower() == self.args.target_key.lower(), "target_usb_key_mismatch")
        labels = ["source", "relay", "target"] if self.relay else ["source", "target"]
        for label in labels:
            expected_rf = initial_target_radio(self.args) if label == "target" else self.args.source_radio
            saved = {}
            for name in ("radio", "tempradio", "tempradio2", "radio2", "radio2.cross", "repeat", "radio.rxps.config"):
                saved[name] = await self.command(label, "get " + name)
            saved["ota"] = ota_policy(await self.command(label, "ota config"))
            saved["rxps"] = ota.parse_rxps_settings(saved["radio.rxps.config"], label)
            require(same_rf(radio_tuple(saved["radio"]), expected_rf), "saved_primary_rf_mismatch")
            require(temp_state(saved["tempradio"]) == "off" and saved["tempradio2"].lower() == "off",
                    "preexisting_temporary_profile")
            require(no_schedules(await self.command(label, "get tempradioat")),
                    "preexisting_temporary_schedule")
            if label != "target":
                require(parse_progress(await self.command(label, "ota stats"))["state"] == "idle",
                        "preexisting_local_download")
            require(saved["repeat"] in ("on", "off"), "invalid_repeat_state")
            require(saved["radio2.cross"] == "off", "cross_must_start_off")
            if label == "relay":
                require(same_rf(radio_tuple(saved["radio2"]), self.args.target_radio)
                        and re.search(r",\s*rxtx(?:,|\s|;|$)", saved["radio2"]), "relay_secondary_rf_mismatch")
                fields = saved["radio2"].split(",")
                preamble = re.match(r"\s*(\d+)(?:\b|;)", fields[5]) if len(fields) > 5 else None
                require(preamble is not None and int(preamble[1]) == self.args.preamble,
                        "relay_secondary_preamble_mismatch")
                for key in ("radioat2", "tempradioat2"):
                    require(no_schedules(await self.command(label, "get " + key)),
                            "preexisting_secondary_schedule")
                require(await self.command(label, "get espnow.enabled") == "off"
                        and await self.command(label, "get espnow.running") == "off", "relay_espnow_must_be_off")
            else:
                require(re.match(r"(?i)^off(?:\s|;|$)", saved["radio2"]), "endpoint_secondary_must_be_off")
                require(await self.command(label, "get espnow.enabled") == "on"
                        and await self.command(label, "get espnow.running") == "on", "control_espnow_not_running")
                require(await self.command(label, "get bridge.source") == "tx"
                        and await self.command(label, "get bridge.ingress") == "radio", "control_bridge_policy_mismatch")
                require(bridge_drop_rule(await self.command(label, "get fr.1")), "missing_unconditional_bridge_ota_drop")
                saved["espnow_drops"] = counters(await self.command(label, "get espnow.stats drops"))
            self.saved[label] = saved
        require(await self.command("source", "ota.peer radio") == "OTApeer radio=radio", "native_primary_selection_required")
        native_timeout = await self.command("source", "ota.peer timeout")
        match = re.fullmatch(r"OTApeer timeout=(\d+)s", native_timeout)
        require(match and 5 <= int(match[1]) <= 60, "native_timeout_must_be_5_to_60_seconds")
        self.source.remote_timeout = max(self.source.remote_timeout, int(match[1]) + 10)
        require((await self.progress())["state"] == "idle", "preexisting_target_download")
        before = await self.inspector.inspect()
        validate_package(self.package, before)
        self.before = before
        self.report["target_hardware"] = before.hw_id
        self.report["saved_settings"] = {label: {"radio": list(radio_tuple(saved["radio"])),
                "ota": saved["ota"], "rxps": asdict(saved["rxps"]), "repeat": saved["repeat"],
                "cross": saved["radio2.cross"]} for label, saved in self.saved.items()}
        self.event("preflight_passed")

    async def open_window(self, label):
        rf = self.args.target_radio if label == "target" else self.args.source_radio
        text = "tempradio " + rf_text(rf) + f",{self.args.window_minutes},{self.args.preamble}"
        self.saved[label]["window_attempted"] = True
        self.event("temporary_profile_sent", controller=label)
        before_rx = counters(await self.command("source", "get espnow.stats rx")) if label == "target" else None
        acknowledged = False
        try:
            await self.command(label, text, tries=1)
            acknowledged = True
        except ProbeError as exc:
            if exc.code != "command_reply_unknown":
                raise
        await self.sleep(2.2)
        if not acknowledged:
            actual = await self.command(label, "get tempradio")
            require(temp_state(actual) == "active" and same_rf(radio_tuple(actual), rf),
                    "temporary_profile_outcome_unresolved")
        fields = {"controller": label, "acknowledged": acknowledged, "requested_rf": list(rf)}
        if label == "target":
            after_rx = counters(await self.command("source", "get espnow.stats rx"))
            fields["source_espnow_rx_delta"] = counter_delta(before_rx, after_rx)
            require(fields["source_espnow_rx_delta"].get("mesh", 0) > 0,
                    "target_retune_lacks_espnow_receive_evidence")
            if self.witness:
                actual = await self.command("witness", "get tempradio")
                require(temp_state(actual) == "active" and same_rf(radio_tuple(actual), rf),
                        "independent_target_window_mismatch")
        self.event("temporary_profile_opened", **fields)

    async def begin(self):
        self.active = True
        for label in self.saved:
            for text in ("ota config autofetch off", "ota config autoinstall off", "ota config hops 1",
                         "set radio.rxps off", "set repeat " + ("on" if label == "relay" else "off")):
                await self.command(label, text)
            await self.open_window(label)
            policy = ota_policy(await self.command(label, "ota config"))
            require(policy == {"hops": "1", "autofetch": "off", "autoinstall": "off"},
                    "manual_one_hop_policy_not_confirmed")
            require(not ota.parse_rxps_settings(await self.command(label, "get radio.rxps.config"), label).enabled,
                    "rxps_disable_not_confirmed")
        self.event("temporary_windows_active")

    async def cross(self, enabled):
        require(self.relay is not None, "relay_usb_required_for_counterfactual")
        desired = "on" if enabled else "off"
        await self.command("relay", "set radio2.cross " + desired)
        require(await self.command("relay", "get radio2.cross") == desired, "relay_cross_readback_failed")
        self.event("relay_cross", enabled=enabled)

    async def serve(self):
        self.server_stop = asyncio.Event()
        self.server_task = asyncio.create_task(self.source.serve(self.blob, self.server_stop))
        await self.sleep(.2)
        if self.server_task.done():
            await self.server_task
            raise ProbeError("source_server_stopped")

    async def pull(self, *, resume=False):
        self.pull_attempted = True
        self.event("pull_sent", resume=resume)
        try:
            await self.command("target", "ota pull " + self.mid + " flash", tries=1)
        except ProbeError as exc:
            if exc.code == "command_rejected":
                self.pull_attempted = resume
                raise
            sample = await self.progress()
            require(sample["mid"] == self.mid and sample["state"] in ("active", "complete", "failed"),
                    "pull_outcome_unresolved")

    async def counterfactual(self):
        await self.cross(False)
        try:
            # Drain frames already queued before cross was disabled.
            await self.sleep(2)
            first = await self.progress()
            require(first["state"] != "complete" and 0 < first["have"] < first["total"],
                    "midpoint_not_observed")
            await self.sleep(self.args.outage - 2)
            last = await self.progress()
            require(last["mid"] == first["mid"] == self.mid
                    and last["have"] == first["have"] and last["total"] == first["total"],
                    "download_progressed_while_relay_cross_off")
            self.report["counterfactual"] = {"have": first["have"], "total": first["total"],
                    "settled_off_seconds": self.args.outage - 2, "same_manifest": True}
            self.event("relay_off_stall_confirmed", have=first["have"], total=first["total"])
            return first["have"]
        finally:
            await self.cross(True)

    async def transfer(self):
        if self.relay:
            await self.cross(True)
        await self.serve()
        deadline = self.clock() + self.args.timeout
        while not re.search(r"(?<![0-9a-f])" + self.mid + r"(?![0-9a-f])",
                            (await self.command("target", "ota ls")).lower()):
            require(self.clock() < deadline, "catalog_deadline_expired")
            await self.sleep(self.args.poll)
        self.event("catalog_visible")
        require((await self.progress())["state"] == "idle", "target_download_changed_before_pull")
        await self.pull()
        frozen = None
        initial_have = None
        resumed_once = resumed_proven = False
        while self.clock() < deadline:
            require(self.server_task and not self.server_task.done(), "source_server_stopped")
            sample = await self.progress()
            self.event("download_progress", **sample)
            require(sample["state"] != "idle", "owned_download_became_idle")
            if initial_have is None:
                initial_have = sample["have"]
            elif sample["have"] > initial_have and self.report["target_frequency_change_requested"]:
                # Do not mistake resumed staging bytes for a new reception.
                # Progress beyond the first sample proves new blocks on B.
                self.report["target_frequency_change_confirmed"] = True
            if sample["state"] == "failed":
                if self.args.resume_failed_once and not resumed_once:
                    resumed_once = True
                    await self.pull(resume=True)
                    continue
                raise ProbeError("target_download_failed")
            if frozen is not None and sample["have"] > frozen:
                resumed_proven = True
            if sample["state"] == "complete":
                require(sample["have"] == sample["total"], "incomplete_ready_state")
                break
            if frozen is None and not self.args.skip_counterfactual and 0 < sample["have"] < sample["total"]:
                frozen = await self.counterfactual()
            await self.sleep(self.args.poll)
        else:
            raise ProbeError("transfer_deadline_expired")
        require(self.args.skip_counterfactual or frozen is not None and resumed_proven,
                "relay_resume_not_proven")
        require(not self.report["target_frequency_change_requested"]
                or self.report["target_frequency_change_confirmed"], "target_frequency_change_not_proven")
        self.report["stage_accepted_complete"] = True
        self.report["final_blocks"] = {"have": sample["have"], "total": sample["total"]}
        await self.verify_stage()
        deltas = {}
        for label in ("source", "target"):
            after = counters(await self.command(label, "get espnow.stats drops"))
            deltas[label] = counter_delta(self.saved[label]["espnow_drops"], after)
        self.report["espnow_drop_deltas"] = deltas
        require(all(values.get("txfilter", 0) > 0 for values in deltas.values()),
                "ota_bridge_exclusion_not_observed")
        self.report["relay_qualified"] = not self.args.skip_counterfactual
        self.event("staged_package_verified" if self.report["stage_verified"] else "download_complete_diagnostic_unavailable")

    async def verify_stage(self):
        # This diagnostic is optional capability evidence, unlike COMPLETE's
        # per-block Merkle admission or the mandatory post-install body hash.
        # Prefer the independent local console when present; do not fall back
        # after a timeout, rejection, malformed report or integrity failure.
        controller = "witness" if self.witness is not None else "target"
        self.report["dev_verification_controller"] = "usb_witness" if self.witness is not None else "espnow_admin"
        reply = await self.command(controller, "ota dev verify", accept_error_reply=True)
        unavailable = verification_unavailable(reply)
        if unavailable:
            self.report["dev_verification_available"] = False
            self.report["dev_verification_reason"] = unavailable
            self.event("dev_verification_unavailable", reason=unavailable)
            return
        require(not re.match(r"(?i)^(?:err(?:or)?\b|unknown\b|unsupported\b)", reply),
                "dev_verification_rejected")
        fields = {key: int(number) for key, number in re.findall(
            r"\b(parsed|root|payload|img|signed|sig|trust|ok|auto)=([01])\b", reply)}
        required = ("parsed", "root", "payload", "ok")
        require(all(field in fields for field in required), "dev_verification_malformed")
        self.report["dev_verification_available"] = True
        self.report["dev_verification_flags"] = fields
        require(all(fields[field] == 1 for field in required)
                and fields.get("img", 1) == 1
                and (fields.get("signed") != 1 or fields.get("sig") == 1),
                "staged_verification_failed")
        self.report["stage_verified"] = True

    async def installed_identity(self):
        current = await self.inspector.inspect()
        return (current.target_id == self.info.target_id and current.hw_id == self.info.hw_id
                and current.version == self.info.fw_version and current.base_hash == self.package.body_hash)

    async def install(self):
        self.install_attempted = True
        self.event("install_sent")
        try:
            await self.command("target", "ota install", tries=1)
        except ProbeError as exc:
            if exc.code != "command_reply_unknown":
                raise
        deadline = self.clock() + self.args.reboot_timeout
        while self.clock() < deadline:
            try:
                await self.source.reset_remote_session(self.args.target_key)
                if await self.installed_identity():
                    self.install_verified = True
                    self.report["install_verified"] = True
                    self.event("running_image_verified", body_hash=self.package.body_hash.hex(),
                               version=self.info.fw_version)
                    return
            except (ProbeError, ota.OtaError, TimeoutError, ConnectionError):
                pass
            await self.sleep(min(self.args.poll, 10))
        raise ProbeError("installed_identity_not_confirmed")

    async def clear_owned_slot(self):
        if not self.pull_attempted:
            # A download can have appeared after preflight but before our
            # pull. Never re-enable automatic policy around an unknown slot.
            return (await self.progress())["state"] == "idle"
        if self.install_verified:
            return True
        if self.install_attempted:
            # An install with a lost acknowledgement must never become cancel.
            if await self.installed_identity():
                self.install_verified = self.report["install_verified"] = True
                return True
            return False
        live = await self.progress()
        if live["mid"] != self.mid or live["state"] == "idle" or self.cancel_sent:
            return self.slot_clear
        self.cancel_sent = True
        self.event("cancel_sent")
        answer = None
        try:
            answer = await self.command("target", "ota cancel", tries=1)
        except ProbeError:
            pass
        after = await self.progress()
        self.slot_clear = bool(answer and after["state"] == "idle"
            and re.match(r"(?i)^OK dropped session\b", answer)
            and re.search(rf"(?i)\bmid={self.mid}\b", answer)
            and "OTA receive slot confirmed clear" in answer)
        self.report["receive_slot_clear_confirmed"] = self.slot_clear
        return self.slot_clear

    async def cleanup(self):
        errors = []
        server_stopped = True
        if self.server_task:
            self.server_stop.set()
            try:
                await asyncio.wait_for(self.server_task, 15)
            except (Exception, asyncio.CancelledError):
                self.server_task.cancel()
                await asyncio.gather(self.server_task, return_exceptions=True)
            # The reusable transport intentionally suppresses detach errors
            # during its finally block. Obtain an explicit idempotent local
            # detach acknowledgement before restoring automatic policies.
            try:
                require((await self.command("source", "ota folder off")).startswith("OK"),
                        "source_folder_detach_not_confirmed")
            except Exception:
                server_stopped = False
                errors.append("source_server_stop_unconfirmed")
        if not self.active:
            return errors
        try:
            target_safe = await self.clear_owned_slot() and server_stopped
        except Exception:
            target_safe = False
        if not target_safe:
            errors.append("target_receive_slot_or_install_unresolved")
        # ESP-NOW control survives all normal-channel returns. No relative
        # lease is replayed during restoration.
        for label in ("target", "relay", "source"):
            saved = self.saved.get(label)
            if not saved:
                continue
            try:
                if saved.get("window_attempted"):
                    try:
                        await self.command(label, "normalradio", tries=1)
                    except ProbeError as exc:
                        if exc.code != "command_reply_unknown":
                            raise
                    await self.sleep(2.2)
                    require(temp_state(await self.command(label, "get tempradio")) == "off",
                            "normal_radio_not_confirmed")
                if label == "relay":
                    await self.command(label, "set radio2.cross " + saved["radio2.cross"])
                await self.command(label, "set repeat " + saved["repeat"])
                desired = saved["rxps"]
                await self.command(label, ota.rxps_restore_command(desired))
                if not desired.enabled:
                    await self.command(label, "set radio.rxps off")
                for field, setting in saved["ota"].items():
                    if label == "target" and field != "hops" and not target_safe:
                        setting = "off"
                    await self.command(label, f"ota config {field} {setting}")
                require(same_rf(radio_tuple(await self.command(label, "get radio")),
                                radio_tuple(saved["radio"])), "saved_radio_not_restored")
                require(await self.command(label, "get repeat") == saved["repeat"],
                        "repeat_not_restored")
                actual_rxps = ota.parse_rxps_settings(await self.command(label, "get radio.rxps.config"), label)
                require(same_rxps(actual_rxps, desired), "rxps_not_restored")
                expected_policy = dict(saved["ota"])
                if label == "target" and not target_safe:
                    expected_policy.update(autofetch="off", autoinstall="off")
                require(ota_policy(await self.command(label, "ota config")) == expected_policy,
                        "ota_policy_not_restored")
                if label == "relay":
                    require(await self.command(label, "get radio2.cross") == saved["radio2.cross"],
                            "relay_cross_not_restored")
            except Exception:
                errors.append(label + "_settings_restore_unconfirmed")
        self.report["settings_restored"] = not errors
        self.report["cleanup_errors"] = errors
        return errors

    async def run(self):
        try:
            await self.snapshot()
            await self.begin()
            await self.transfer()
            if self.args.install:
                await self.install()
            else:
                current = await self.inspector.inspect()
                require((current.target_id, current.hw_id, current.version, current.base_hash)
                        == (self.before.target_id, self.before.hw_id, self.before.version, self.before.base_hash),
                        "running_image_changed_during_transfer_only_test")
            if not self.args.install and not self.report["stage_verified"]:
                self.report["result"] = "TRANSFER_COMPLETE_VERIFICATION_UNAVAILABLE"
            else:
                self.report["result"] = "PASS" if self.report["relay_qualified"] else "TRANSFER_VERIFIED_NOT_RELAY_QUALIFIED"
        except (Exception, KeyboardInterrupt, asyncio.CancelledError) as exc:
            self.report["result"] = "FAIL"
            self.report["failure"] = {"type": type(exc).__name__,
                "code": exc.code if isinstance(exc, ProbeError) else "operation_failed",
                "operation": exc.operation if isinstance(exc, ProbeError) else None}
        finally:
            failures = await self.cleanup()
            if failures:
                self.report["result"] = "NEEDS_RECOVERY"
            self.report["finished_utc"] = datetime.now(timezone.utc).isoformat()
            self.persist(self.report)
        return self.report


async def execute(args, persist):
    import serial
    links = {}
    pins = {}
    specs = [("source", args.source_port, args.source_usb_serial),
             ("relay", args.relay_port, args.relay_usb_serial),
             ("witness", args.target_port, args.target_usb_serial)]
    for label, port, usb_serial in specs:
        if port:
            pins[label] = verify_usb(port, usb_serial)
    def serial_factory(**kwargs):
        return serial.Serial(**kwargs, **({"exclusive": True} if os.name == "posix" else {}))
    blob = ota.read_bounded_file(args.package, 16 * 1024 * 1024, "OTA test package")
    try:
        for label, port, usb_serial in specs:
            if port:
                link = RepeaterTransport(port, serial_factory=serial_factory,
                    known_repeater=args.target_key if label == "source" else None,
                    acl_only=label == "source")
                links[label] = link
                await link.open()
                # Verify identity again without rechecking fuser (we own it).
                from serial.tools import list_ports
                match = [item for item in list_ports.comports()
                         if os.path.realpath(item.device) == os.path.realpath(port)]
                require(len(match) == 1 and (match[0].serial_number or "").upper() == usb_serial.upper(),
                        "usb_identity_changed_after_open")
        probe = RelayProbe(args, blob, links["source"], links.get("relay"), links.get("witness"), persist=persist)
        probe.report["usb"] = pins
        return await probe.run()
    finally:
        for link in reversed(list(links.values())):
            try:
                await link.close()
            except (Exception, asyncio.CancelledError):
                pass


def main(argv=None):
    args = arguments(argv)
    try:
        validate_arguments(args)
        if args.plan:
            print(json.dumps({"topology": "LoRa source A -> dual-profile LoRa relay A/B -> target B",
                "control": "ESP-NOW; OTA payload blocked at both bridge endpoints",
                "source_rf": args.source_radio, "target_rf": args.target_radio,
                "target_initial_rf": initial_target_radio(args),
                "hops": 1, "preamble": args.preamble, "opens_usb": False,
                "relative_leases_and_pull_sent_once": True, "install": args.install,
                "relay_counterfactual": not args.skip_counterfactual}, indent=2))
            return 0
        with args.report.open("x", encoding="utf-8") as output:
            try:
                os.chmod(args.report, 0o600)
            except OSError:
                pass
            def persist(report):
                output.seek(0)
                json.dump(report, output, indent=2)
                output.write("\n")
                output.truncate()
                output.flush()
            try:
                result = asyncio.run(execute(args, persist))
            except (Exception, KeyboardInterrupt) as exc:
                persist({"schema": 1, "result": "FAIL", "failure": {
                    "type": type(exc).__name__,
                    "code": exc.code if isinstance(exc, ProbeError) else "startup_failed"}})
                raise
        print(json.dumps({"result": result["result"], "relay_qualified": result["relay_qualified"],
                          "install_verified": result["install_verified"],
                          "settings_restored": result["settings_restored"]}))
        return 0 if result["result"] == "PASS" else 1
    except (ProbeError, OSError, ota.OtaError, ValueError, ImportError, KeyboardInterrupt) as exc:
        print(json.dumps({"result": "FAIL", "type": type(exc).__name__,
                          "code": exc.code if isinstance(exc, ProbeError) else "startup_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
