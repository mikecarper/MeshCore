"""LoRa relay updates with an existing ESP-NOW control link.

The operator provisions the relay path, its temporary OTA windows, and an
unconditional first-slot OTA bridge-drop rule on both endpoints. This session
owns only the source and destination primary leases, RXPS and OTA policies.
It does not provision relays, bridge credentials, filter rules or radio2.
Native administration remains authenticated zero-hop traffic over the existing
ESP-NOW bridge; the OTA payload uses ordinary positive-hop LoRa flooding.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import re
import shlex
import time

import lora_ota as ota
from ota_wizard_session import DirectSession, Package, checked, radio_tuple, temp_state


SESSION_KIND = "espnow-relay"


def _radio(value, label):
    if value is None:
        return None
    try:
        if not isinstance(value, (tuple, list)) or len(value) != 4:
            raise ValueError()
        return radio_tuple(",".join(map(str, value)))
    except (ValueError, TypeError, argparse.ArgumentTypeError, ota.OtaError) as exc:
        raise ota.OtaError(f"The {label} radio needs valid frequency, bandwidth, SF and CR.") from exc


def _same_radio(first, second):
    return all(abs(a - b) <= .001 for a, b in zip(first, second))


def _separate_channels(first, second):
    return abs(first[0] - second[0]) * 1000 > (first[1] + second[1]) / 2 + 50


def bridge_ota_drop_rule(reply):
    """Accept the full or copied compact first-slot unconditional drop row."""
    try:
        tokens = shlex.split(reply.strip().removeprefix(">").strip().lower())
    except ValueError:
        return False
    types = {"ota", "t=ota", "type=ota", "12", "t=12", "type=12"}
    allowed = types | {"set", "fr.1", "flood.rule.1", "m=b", "mode=bridge",
        "rt=a", "route=any", "pri=255", "priority=255", "d", "drop",
        "h=*", "hops=all", "e=1", "e=on", "enabled=on"}
    selected = set(tokens)
    return (bool(tokens) and selected <= allowed and bool(selected & types)
            and bool(selected & {"fr.1", "flood.rule.1"})
            and bool(selected & {"m=b", "mode=bridge"})
            and bool(selected & {"rt=a", "route=any"})
            and bool(selected & {"pri=255", "priority=255"})
            and bool(selected & {"d", "drop"}))


def _validate_policy(reply, label):
    fields = dict(re.findall(r"\b(hops|autofetch|autoinstall)=([A-Za-z0-9]+)", reply))
    if (set(fields) != {"hops", "autofetch", "autoinstall"}
            or not fields["hops"].isdigit() or not 0 <= int(fields["hops"]) <= 63
            or fields["autofetch"] not in ("off", "signed", "any")
            or fields["autoinstall"] not in ("off", "trusted")):
        raise ota.OtaError(f"The {label} did not report restorable OTA policies.")


class EspNowRelaySession(DirectSession):
    """Reuse package/install guards while keeping source A and target B apart."""

    def __init__(self, transport, public_key: str, password: str, journal: Path,
                 *, ota_hops: int = 1, target_transfer_radio=None,
                 source_transfer_radio=None, preamble: int = 128,
                 window_minutes: int | None = None, progress=print,
                 poll_seconds: float = 45, transfer_timeout: float = 4 * 60 * 60,
                 reboot_timeout: float = 300):
        if (not getattr(transport, "supports_radio2", False)
                or not getattr(transport, "acl_only", False) or password != ""):
            raise ota.OtaError("ESP-NOW relay updates require a USB repeater using saved ACL access.")
        self._validate_numbers(ota_hops, preamble)
        self.ota_hops, self.preamble = ota_hops, preamble
        self.source_transfer_radio = _radio(source_transfer_radio, "source")
        self.target_transfer_radio = _radio(target_transfer_radio, "target transfer")
        super().__init__(transport, public_key, password, journal, progress=progress,
                         poll_seconds=poll_seconds, transfer_timeout=transfer_timeout,
                         reboot_timeout=reboot_timeout, source_radio="radio",
                         transfer_radio=self.source_transfer_radio, window_minutes=window_minutes)
        self._state.update(session_kind=SESSION_KIND, ota_hops=ota_hops, preamble=preamble,
            source_transfer_radio=self.source_transfer_radio,
            target_transfer_radio=self.target_transfer_radio,
            source_settings_changed=False, target_settings_changed=False,
            source_folder_owned=False)

    @staticmethod
    def _validate_numbers(hops, preamble):
        if type(hops) is not int or not 1 <= hops <= 8:
            raise ota.OtaError("ESP-NOW relay updates need an OTA hop limit from 1 to 8.")
        if type(preamble) is not int or not 16 <= preamble <= 65535:
            raise ota.OtaError("The transfer preamble must be 16..65535 whole symbols.")

    async def remote(self, command: str, *, retries: int = 2) -> str:
        # Administration follows the preconfigured sideband. A lost reply
        # must never switch the source radio or retune it to the target tuple.
        for attempt in range(retries + 1):
            try:
                return checked(await self.link.remote_command(self.key, "", command))
            except (TimeoutError, asyncio.TimeoutError, ConnectionError, ota.TransmissionError) as exc:
                if attempt == retries:
                    raise ota.TransmissionError("No reply over the configured ESP-NOW control link.") from exc
                await asyncio.sleep(min(1 + attempt, 3))
        raise AssertionError("unreachable")

    async def _sideband_prerequisites(self):
        if await self.local("ota.peer radio") != "OTApeer radio=radio":
            raise ota.OtaError("Select ota.peer radio radio before an ESP-NOW relay update.")
        current = {}
        for label, command in (("source", self.local), ("target", self.remote)):
            current[label] = radio_tuple(await command("get radio"))
            secondary = await command("get radio2")
            if not re.match(r"(?i)^off(?:\s|;|$)", secondary):
                raise ota.OtaError(f"The {label} radio2 must be off for ESP-NOW relay control.")
            if await command("get tempradio2") != "off" or await command("get radio2.cross") != "off":
                raise ota.OtaError(f"The {label} temporary radio2 and crossing must be off.")
            for key in ("espnow.enabled", "espnow.running"):
                if await command("get " + key) != "on":
                    raise ota.OtaError(f"The {label} needs its existing ESP-NOW control bridge running.")
            if await command("get bridge.source") != "tx" or await command("get bridge.ingress") != "radio":
                raise ota.OtaError(f"The {label} bridge must use source tx and ingress radio.")
            if not bridge_ota_drop_rule(await command("get fr.1")):
                raise ota.OtaError(
                    f"The {label} needs an existing first-slot bridge OTA exclusion: "
                    "set fr.1 ota m=b rt=a pri=255 d. Configure it before updating.")
        source = self.source_transfer_radio or current["source"]
        transfer = self.target_transfer_radio or current["target"]
        if not _same_radio(source, current["source"]):
            raise ota.OtaError("The source transfer radio must match its saved primary; it will not be retuned.")
        if (not _separate_channels(source, current["target"])
                or not _separate_channels(source, transfer)):
            raise ota.OtaError("The source and both target channels must be separate for ESP-NOW relay control.")
        return current

    async def inspect(self):
        # --check validates the sideband without writing settings or a journal.
        if not self.active:
            await self._sideband_prerequisites()
        return await super().inspect()

    async def snapshot(self):
        current = await self._sideband_prerequisites()
        source_key = getattr(self.link, "source_key", None)
        if not isinstance(source_key, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_key):
            raise ota.OtaError("The USB source did not report its complete public key.")
        self._state["source_key"] = source_key.lower()
        for label, command in (("source", self.local), ("target", self.remote)):
            await self._check_temporary_windows(command, label)
            if label == "target":
                await self._require_idle_download()
            else:
                await self._require_idle_source()
            policy = await command("ota config")
            _validate_policy(policy, label)
            try:
                rxps = await command("get radio.rxps.config")
            except ota.TransmissionError:
                raise
            except ota.OtaError as exc:
                if re.search(r"unsupported|not supported", str(exc), re.I):
                    rxps = "unsupported"
                elif re.search(r"unknown|unrecognized", str(exc), re.I):
                    rxps = await command("get radio.rxps")
                else:
                    raise
            if rxps != "unsupported":
                ota.parse_rxps_settings(rxps, label)
            self.saved[label] = {"radio": ",".join(map(str, current[label])), "rxps": rxps, "ota": policy}
        self.source_transfer_radio = self.source_transfer_radio or current["source"]
        self.target_transfer_radio = self.target_transfer_radio or current["target"]
        self.transfer_radio = self.source_transfer_radio
        self.save("prepared", source_transfer_radio=self.source_transfer_radio,
                  target_transfer_radio=self.target_transfer_radio, transfer_radio=self.transfer_radio)

    async def _require_idle_source(self):
        if not re.search(r"\bfetch idle\b", await self.local("ota stats"), re.I):
            raise ota.OtaError("Finish the USB source's existing OTA download before updating another node.")
        if not re.match(r"^folder=off\b", await self.local("ota folder")):
            raise ota.OtaError("The source already has an attached OTA folder; it will not be replaced.")

    async def _verify_paused_policy(self, command, label):
        policy = await command("ota config")
        fields = ("hops",) if "mode=seeder-only" in policy else ("hops", "autofetch", "autoinstall")
        for field in fields:
            expected = str(self.ota_hops) if field == "hops" else "off"
            if not re.search(rf"\b{field}={expected}\b", policy):
                raise ota.OtaError(f"The {label} did not confirm relay OTA policy {field}={expected}.")

    async def begin(self):
        if not self.saved:
            await self.snapshot()
        else:
            current = await self._sideband_prerequisites()
            for label, command in (("source", self.local), ("target", self.remote)):
                if not _same_radio(current[label], radio_tuple(self.saved[label]["radio"])):
                    raise ota.OtaError(f"The {label} saved radio changed during package preparation.")
                await self._check_temporary_windows(command, label)
            await self._require_idle_download()
            await self._require_idle_source()
        self.active = True
        self.save("opening OTA windows", lease_expires_at=time.time() + self.window_minutes * 60)
        for label, command in (("source", self.local), ("target", self.remote)):
            self.save("opening OTA windows", **{label + "_settings_changed": True})
            changes = [f"ota config hops {self.ota_hops}"]
            if "mode=seeder-only" not in self.saved[label]["ota"]:
                changes[:0] = ["ota config autofetch off", "ota config autoinstall off"]
            if self.saved[label]["rxps"] != "unsupported":
                changes.append("set radio.rxps off")
            for text in changes:
                await command(text)
            await self._verify_paused_policy(command, label)
            if self.saved[label]["rxps"] != "unsupported" and (await self._read_rxps(command, label)).enabled:
                raise ota.OtaError(f"The {label} did not disable RX power saving.")
            await self._open_window(label)
        self.save("OTA windows active")

    async def _open_window(self, label):
        radio = self.source_transfer_radio if label == "source" else self.target_transfer_radio
        if radio is None:
            raise ota.OtaError("The routed transfer radio settings have not been captured.")
        self.save(self._state["phase"], **{label + "_window_attempted": True})
        text = "tempradio " + ",".join(map(str, radio)) + f",{self.window_minutes},{self.preamble}"
        acknowledged = False
        try:
            if label == "source":
                await self.local(text)
            else:
                await self.remote(text, retries=0)
            acknowledged = True
        except (TimeoutError, asyncio.TimeoutError, ConnectionError, ota.TransmissionError):
            pass  # Relative leases are never replayed after an unknown reply.
        await asyncio.sleep(2.2)
        if not acknowledged:
            command = self.local if label == "source" else self.remote
            actual = await command("get tempradio")
            if temp_state(actual) != "active" or not _same_radio(_window_radio(actual), radio):
                raise ota.OtaError(f"The {label} temporary profile outcome is unresolved; its lease was not repeated.")
        self.save(self._state["phase"], **{label + "_window_active": True})

    async def _target_primary(self):
        # The target reboot clears B and restores C. ESP-NOW control does not
        # require any matching retune or native profile switch at the source.
        self.save(self._state["phase"], target_window_active=False)

    async def _start_serving(self, package):
        await self._stop_serving()
        self.save(self._state["phase"], source_folder_owned=True)
        self._serve_stop = asyncio.Event()
        self._serve_task = asyncio.create_task(self.link.serve(package.blob, self._serve_stop))
        await asyncio.sleep(.2)
        if self._serve_task.done():
            await self._serve_task
            raise ota.OtaError("The file server stopped before starting the transfer.")

    async def _stop_serving(self):
        had_task = self._serve_task is not None
        failure = None
        try:
            await super()._stop_serving()
        except Exception as exc:
            failure = exc
        if had_task or self._state.get("source_folder_owned") is True:
            status = await self.local("ota folder")
            if re.match(r"^folder=on\b", status):
                # A fresh recovery must not detach another operator's folder.
                entries = re.findall(r"(?<![0-9A-Fa-f*])(\*?)([0-9A-Fa-f]{8})/[0-9A-Fa-f]{8}\b", status)
                offered = {mid.lower() for own, mid in entries if not own}
                expected = self._state.get("package_mid", "").lower()
                if not had_task and (not expected or offered != {expected}):
                    raise ota.OtaError("The source folder ownership changed; it was not detached.")
                reply = await self.local("ota folder off")
                if not reply.startswith("OK"):
                    raise ota.OtaError("The source did not confirm detaching the updater's folder.")
                status = await self.local("ota folder")
            if not re.match(r"^folder=off\b", status):
                raise ota.OtaError("The source file server stop is unconfirmed.")
            self.save(self._state["phase"], source_folder_owned=False)
        if failure is not None:
            raise failure

    async def install(self, package: Package, before):
        try:
            return await super().install(package, before)
        except ota.OtaError as exc:
            if str(exc) == "The selected package was not discovered over the direct link.":
                raise ota.OtaError("The selected package was not discovered through the preconfigured LoRa relay path.") from exc
            raise

    def _restore_routed_options(self):
        state = self._state
        if state.get("session_kind") != SESSION_KIND or state.get("auth") != "acl":
            raise ota.OtaError("This recovery record is not an ESP-NOW relay session.")
        self._validate_numbers(state.get("ota_hops"), state.get("preamble"))
        self.ota_hops, self.preamble = state["ota_hops"], state["preamble"]
        self.source_transfer_radio = _radio(state.get("source_transfer_radio"), "recorded source")
        self.target_transfer_radio = _radio(state.get("target_transfer_radio"), "recorded target")
        if (self.source_transfer_radio is None or self.target_transfer_radio is None
                or not _same_radio(self.source_transfer_radio, radio_tuple(self.saved["source"]["radio"]))):
            raise ota.OtaError("The recovery record has invalid source or target transfer settings.")
        minutes = state.get("window_minutes")
        if type(minutes) is not int or not 1 <= minutes <= 10080:
            raise ota.OtaError("The recovery record has an invalid temporary window.")
        self.window_minutes, self.transfer_radio = minutes, self.source_transfer_radio
        self.source_radio = "radio"

    async def _close_owned_window(self, label):
        if not self._state.get(label + "_window_attempted"):
            return
        command = self.local if label == "source" else self.remote
        expected = self.source_transfer_radio if label == "source" else self.target_transfer_radio
        actual = await command("get tempradio")
        if temp_state(actual) == "pending":
            await asyncio.sleep(2.2)
            actual = await command("get tempradio")
        if temp_state(actual) != "off":
            if not _same_radio(_window_radio(actual), expected):
                raise ota.OtaError(f"The {label} temporary radio changed; it was not cleared.")
            try:
                if label == "target":
                    await self.remote("normalradio", retries=0)
                else:
                    await self.local("normalradio")
            except (TimeoutError, asyncio.TimeoutError, ConnectionError, ota.TransmissionError):
                pass
            await asyncio.sleep(2.2)
            if temp_state(await command("get tempradio")) != "off":
                raise ota.OtaError(f"The {label} did not confirm its normal radio.")
        self.save(self._state["phase"], **{label + "_window_active": False})

    async def cleanup(self):
        if not self.active:
            return []
        self._restore_routed_options()
        errors = []
        server_stopped = True
        try:
            await self._stop_serving()
        except Exception as exc:
            server_stopped = False
            errors.append(f"Confirm the updater's USB source folder has stopped: {exc}")
        target_safe = server_stopped
        try:
            if not self._state.get("pull_attempted"):
                await self._require_idle_download()
            target_safe = await self._finalize_download() and target_safe
        except Exception as exc:
            target_safe = False
            errors.append(f"Resolve the recorded target update before restoring automatic OTA policy: {exc}")
        if not target_safe and self._state.get("target_settings_changed") is not False:
            try:
                await self.remote("ota config autoinstall off")
                await self.remote("ota config autofetch off")
            except Exception:
                errors.append("Confirm the target automatic OTA policies remain off.")
        for label in ("target", "source"):
            if self._state.get(label + "_settings_changed") is False:
                continue
            try:
                await self._close_owned_window(label)
                await self._restore_endpoint(label, restore_auto=label != "target" or target_safe)
            except Exception as exc:
                errors.append(f"Restore the recorded {label} lease/RXPS/OTA settings: {exc}")
        self.save("needs cleanup" if errors else "finished", recovery_commands=errors)
        self.active = bool(errors)
        return errors


def _window_radio(reply):
    match = re.search(r"(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)", reply)
    if match is None:
        raise ota.OtaError("The temporary radio did not report a complete frequency/modulation tuple.")
    return radio_tuple(match[0])
