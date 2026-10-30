"""Explicit ACL routes and same-channel LoRa OTA through ordered repeaters."""
from __future__ import annotations

import asyncio
from pathlib import Path
import re
import time

import lora_ota as ota
from ota_wizard_repeater import RepeaterTransport, RepeaterError, _key, _one_line
from ota_wizard_routed import EspNowRelaySession, _same_radio, _validate_policy, _window_radio
from ota_wizard_session import DirectSession, checked, radio_tuple, temp_state


SESSION_KIND = "lora-relay"


def relay_route(target, relays, prefix_bytes=3):
    """Validate full identities before deriving the only permitted routes."""
    target = _key(target)
    if not isinstance(relays, (list, tuple)) or not 1 <= len(relays) <= 8:
        raise RepeaterError("Choose one to eight ordered relay public keys, nearest first")
    relays = tuple(_key(key) for key in relays)
    if type(prefix_bytes) is not int or prefix_bytes not in (1, 2, 3):
        raise RepeaterError("Relay route prefixes must contain one, two or three bytes")
    identities = relays + (target,)
    prefixes = [key[:prefix_bytes * 2].upper() for key in identities]
    if len(set(identities)) != len(identities) or len(set(prefixes)) != len(prefixes):
        raise RepeaterError("Relay and destination identities need distinct route prefixes")
    paths = {key: tuple(prefixes[:index]) for index, key in enumerate(relays)}
    paths[target] = tuple(prefixes[:-1])
    return target, relays, paths


class RoutedRepeaterTransport(RepeaterTransport):
    """Native ACL commands scoped to one target and its explicit relay list."""

    def __init__(self, port, baud=115200, *, known_repeater, relay_keys,
                 prefix_bytes=3, recovering=False, expected_source_key=None, **kwargs):
        target, relays, paths = relay_route(known_repeater, relay_keys, prefix_bytes)
        if kwargs.pop("acl_only", True) is not True:
            raise RepeaterError("Routed LoRa administration requires existing ACL access")
        super().__init__(port, baud, known_repeater=target, acl_only=True, **kwargs)
        self.relay_keys, self.prefix_bytes = relays, prefix_bytes
        if type(recovering) is not bool:
            raise RepeaterError("The route recovery choice must be true or false")
        self.recovering = recovering
        self.expected_source_key = _key(expected_source_key) if expected_source_key is not None else None
        if recovering and self.expected_source_key is None:
            raise RepeaterError("Route recovery requires the recorded USB source public key")
        self._route_paths = paths
        self._proved_keys = set()
        self._selected_path = ()
        self._path_owned = False

    async def open(self):
        try:
            await super().open()
            if not getattr(self, "supports_routed_path", False):
                raise RepeaterError("Update the USB source firmware for routed_path OTA administration")
            if self.expected_source_key is not None and self.source_key != self.expected_source_key:
                raise RepeaterError("The USB source differs from the recorded recovery identity")
            reply = await self.local_command("ota.peer path")
            current = self._parse_native_path(reply)
            if current and (not self.recovering or current not in self._route_paths.values()):
                raise RepeaterError("Clear the USB source's existing ota.peer path before updating")
            if self.source_key[:self.prefix_bytes * 2] in {
                    key[:self.prefix_bytes * 2] for key in self._route_paths}:
                raise RepeaterError("The source and routed destinations have colliding route prefixes")
            self._selected_path, self._path_owned = current, bool(current)
            self._proved_keys.clear()
            return self
        except BaseException:
            await self.close()
            raise

    def _peer_key(self, public_key):
        key = _key(public_key)
        if key not in self._route_paths:
            raise RepeaterError("The destination is outside the explicit relay route")
        return key

    def _parse_native_path(self, reply):
        if reply == "OTApeer path=off":
            return ()
        match = re.fullmatch(r"OTApeer path=([0-9A-F]+(?:,[0-9A-F]+)*)", reply)
        if match is None:
            raise RepeaterError("The USB source did not report a valid native route")
        path = tuple(match[1].split(","))
        if not 1 <= len(path) <= 8 or any(len(h) != self.prefix_bytes * 2 for h in path):
            raise RepeaterError("The USB source route does not match the selected prefix width")
        return path

    async def _select_route_locked(self, key):
        path = self._route_paths[key]
        if path != self._selected_path:
            value = ",".join(path) if path else "off"
            self._selected_path, self._path_owned = path, True
            try:
                reply = await self.local_command("ota.peer path " + value)
            except (TimeoutError, ConnectionError, ota.TransmissionError):
                reply = await self.local_command("ota.peer path")
            if self._parse_native_path(reply) != path:
                raise RepeaterError("The USB source did not confirm the selected relay path")

    async def _prove_locked(self, key):
        if key not in self._proved_keys:
            await self._job("ota.peer acl " + key)
            self._proved_keys.add(key)

    async def verify_peer(self, public_key, password):
        key = self._peer_key(public_key)
        self._validate_password(password)
        async with self._admin_lock:
            await self._check_clock()
            await self._select_route_locked(key)
            await self._prove_locked(key)
            return key

    async def remote_command(self, public_key, password, text):
        key = self._peer_key(public_key)
        _one_line(text, 154)
        self._validate_password(password)
        async with self._admin_lock:
            await self._check_clock()
            await self._select_route_locked(key)
            await self._prove_locked(key)
            return await self._job(f"ota.peer cmd.acl {key} {text}")

    async def reset_remote_session(self, public_key):
        key = self._peer_key(public_key)
        async with self._admin_lock:
            await self._select_route_locked(key)
            if await self.local_command("ota.peer close") != "OTApeer closed":
                raise RepeaterError("Could not reset the routed ACL session")
            self._proved_keys.discard(key)
            await self._check_clock()
            await self._prove_locked(key)

    async def _restore_native_path(self):
        if not self._path_owned:
            return
        expected = "OTApeer path=" + (",".join(self._selected_path) or "off")
        if await self.local_command("ota.peer path") != expected:
            raise RepeaterError("The USB source path changed; it was not overwritten")
        if await self.local_command("ota.peer path off") != "OTApeer path=off":
            raise RepeaterError("Could not restore the USB source's original empty route")
        self._selected_path, self._path_owned = (), False

    async def close(self):
        error = None
        if self._serial is not None and self._failure is None:
            try:
                await self._restore_native_path()
            except Exception as exc:
                error = exc
        await super().close()
        self._proved_keys.clear()
        if error:
            raise error


class LoRaRelaySession(EspNowRelaySession):
    """Same saved primary RF on all nodes; ordered routes carry ACL control."""

    def __init__(self, transport, public_key: str, password: str, journal: Path,
                 *, relay_keys=None, ota_hops=None, preamble=128,
                 transfer_radio=None, window_minutes=None, progress=print,
                 poll_seconds=45, transfer_timeout=4 * 60 * 60, reboot_timeout=300):
        if (not getattr(transport, "supports_radio2", False)
                or not getattr(transport, "acl_only", False) or password != ""):
            raise ota.OtaError("LoRa relay updates require a USB repeater using existing ACL access.")
        if not getattr(transport, "supports_routed_path", False):
            raise ota.OtaError("LoRa relay updates require routed native USB repeater control.")
        target, relays, _ = relay_route(public_key,
            relay_keys if relay_keys is not None else getattr(transport, "relay_keys", ()),
            getattr(transport, "prefix_bytes", 3))
        if tuple(getattr(transport, "relay_keys", ())) != relays:
            raise ota.OtaError("The session and transport relay identities differ.")
        self.relay_keys = relays
        self.prefix_bytes = getattr(transport, "prefix_bytes", 3)
        hops = len(relays) if ota_hops is None else ota_hops
        if type(hops) is not int or not len(relays) <= hops <= 8:
            raise ota.OtaError("The OTA hop limit must cover every relay and be at most eight.")
        super().__init__(transport, target, password, journal, ota_hops=hops,
            source_transfer_radio=transfer_radio, target_transfer_radio=transfer_radio,
            preamble=preamble, window_minutes=window_minutes, progress=progress,
            poll_seconds=poll_seconds, transfer_timeout=transfer_timeout,
            reboot_timeout=reboot_timeout)
        self._state.update(session_kind=SESSION_KIND, relay_keys=list(relays),
            prefix_bytes=self.prefix_bytes)
        for label in self._relay_labels():
            self._state[label + "_settings_changed"] = False

    @staticmethod
    def _validate_numbers(hops, preamble):
        if type(hops) is not int or not 1 <= hops <= 8:
            raise ota.OtaError("LoRa relay updates need an OTA hop limit from one to eight.")
        if type(preamble) is not int or not 16 <= preamble <= 65535:
            raise ota.OtaError("The transfer preamble must be 16..65535 whole symbols.")

    def _relay_labels(self):
        return tuple(f"relay_{index}" for index in range(len(self.relay_keys)))

    async def _remote_key(self, key, command, *, retries=2):
        for attempt in range(retries + 1):
            try:
                return checked(await self.link.remote_command(key, "", command))
            except (TimeoutError, asyncio.TimeoutError, ConnectionError, ota.TransmissionError) as exc:
                if attempt == retries:
                    raise ota.TransmissionError("No reply over the explicit LoRa relay route.") from exc
                await asyncio.sleep(min(1 + attempt, 3))
        raise AssertionError("unreachable")

    async def remote(self, command, *, retries=2):
        return await self._remote_key(self.key, command, retries=retries)

    def _command(self, label):
        if label == "source":
            return self.local
        if label == "target":
            return self.remote
        key = self.relay_keys[self._relay_labels().index(label)]
        async def command(text, *, retries=2):
            return await self._remote_key(key, text, retries=retries)
        return command

    async def _route_prerequisites(self):
        if await self.local("ota.peer radio") != "OTApeer radio=radio":
            raise ota.OtaError("Select the USB source primary radio before a LoRa relay update.")
        current = {}
        for label in ("source",) + self._relay_labels() + ("target",):
            command = self._command(label)
            current[label] = radio_tuple(await command("get radio"))
            if not re.match(r"(?i)^off(?:\s|;|$)", await command("get radio2")):
                raise ota.OtaError(f"The {label} radio2 must be off for this routed update.")
            if await command("get tempradio2") != "off" or await command("get radio2.cross") != "off":
                raise ota.OtaError(f"The {label} temporary radio2 and crossing must be off.")
            if not _same_radio(current[label], current["source"]):
                raise ota.OtaError("The source, relays and target must share their saved primary radio settings.")
        if self.transfer_radio is not None and not _same_radio(self.transfer_radio, current["source"]):
            raise ota.OtaError("Routed updates keep the saved primary radio; frequency changes are not supported.")
        return current

    async def inspect(self):
        if not self.active:
            await self._route_prerequisites()
        return await DirectSession.inspect(self)

    async def snapshot(self):
        current = await self._route_prerequisites()
        source = getattr(self.link, "source_key", None)
        if not isinstance(source, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source):
            raise ota.OtaError("The USB source did not report its complete public key.")
        self._state["source_key"] = source.lower()
        for label in ("source",) + self._relay_labels() + ("target",):
            command = self._command(label)
            await self._check_temporary_windows(command, label)
            if label == "source":
                await self._require_idle_source()
            elif label == "target":
                await self._require_idle_download()
            elif not re.search(r"\bfetch idle\b", await command("ota stats"), re.I):
                raise ota.OtaError(f"Finish the {label} OTA download before starting this update.")
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
            settings = dict(radio=",".join(map(str, current[label])), rxps=rxps, ota=policy)
            if label.startswith("relay_"):
                settings["repeat"] = await command("get repeat")
                if settings["repeat"] not in ("on", "off"):
                    raise ota.OtaError(f"The {label} did not report a restorable repeating setting.")
            self.saved[label] = settings
        self.transfer_radio = self.source_transfer_radio = self.target_transfer_radio = current["source"]
        self.save("prepared", transfer_radio=self.transfer_radio,
            source_transfer_radio=self.transfer_radio, target_transfer_radio=self.transfer_radio)

    async def begin(self):
        if not self.saved:
            await self.snapshot()
        else:
            current = await self._route_prerequisites()
            await self._require_idle_source()
            await self._require_idle_download()
            for label, settings in self.saved.items():
                command = self._command(label)
                if not _same_radio(current[label], radio_tuple(settings["radio"])):
                    raise ota.OtaError(f"The {label} saved radio changed during package preparation.")
                await self._check_temporary_windows(command, label)
                if label.startswith("relay_") and not re.search(r"\bfetch idle\b", await command("ota stats"), re.I):
                    raise ota.OtaError(f"The {label} started another OTA download.")
        self.active = True
        self.save("opening OTA windows", lease_expires_at=time.time() + self.window_minutes * 60)
        for label in ("target",) + tuple(reversed(self._relay_labels())) + ("source",):
            command = self._command(label)
            self.save("opening OTA windows", **{label + "_settings_changed": True})
            if "mode=seeder-only" not in self.saved[label]["ota"]:
                await command("ota config autofetch off")
                await command("ota config autoinstall off")
            await command(f"ota config hops {self.ota_hops}")
            if self.saved[label]["rxps"] != "unsupported":
                await command("set radio.rxps off")
            if label.startswith("relay_"):
                await command("set repeat on")
                if await command("get repeat") != "on":
                    raise ota.OtaError(f"The {label} did not enable repeating.")
            await self._verify_paused_policy(command, label)
            if self.saved[label]["rxps"] != "unsupported" and (await self._read_rxps(command, label)).enabled:
                raise ota.OtaError(f"The {label} did not disable RX power saving.")
            await self._open_window(label)
        self.save("OTA windows active")

    async def _open_window(self, label):
        command = self._command(label)
        self.save(self._state["phase"], **{label + "_window_attempted": True})
        text = "tempradio " + ",".join(map(str, self.transfer_radio)) + f",{self.window_minutes},{self.preamble}"
        acknowledged = False
        try:
            await command(text, **({} if label == "source" else {"retries": 0}))
            acknowledged = True
        except (TimeoutError, asyncio.TimeoutError, ConnectionError, ota.TransmissionError):
            pass
        await asyncio.sleep(2.2)
        if not acknowledged:
            actual = await command("get tempradio")
            if temp_state(actual) != "active" or not _same_radio(_window_radio(actual), self.transfer_radio):
                raise ota.OtaError(f"The {label} lease outcome is unresolved; it was not repeated.")
        self.save(self._state["phase"], **{label + "_window_active": True})

    async def _target_primary(self):
        self.save(self._state["phase"], target_window_active=False)

    async def _start_serving(self, package):
        if self._serve_task is None and not self._state.get("source_folder_owned"):
            await self._require_idle_source()
        await super()._start_serving(package)

    def _restore_routed_options(self):
        s = self._state
        if s.get("session_kind") != SESSION_KIND or s.get("auth") != "acl":
            raise ota.OtaError("This recovery record is not an ACL LoRa relay session.")
        _, relays, _ = relay_route(self.key, s.get("relay_keys"), s.get("prefix_bytes"))
        if relays != self.relay_keys or s["prefix_bytes"] != self.prefix_bytes:
            raise ota.OtaError("The recovery record and transport routes differ.")
        self._validate_numbers(s.get("ota_hops"), s.get("preamble"))
        if s["ota_hops"] < len(relays):
            raise ota.OtaError("The recorded hop limit cannot cover its relay route.")
        self.ota_hops, self.preamble = s["ota_hops"], s["preamble"]
        self.transfer_radio = radio_tuple(",".join(map(str, s.get("transfer_radio", ()))))
        if set(self.saved) != {"source", "target", *self._relay_labels()}:
            raise ota.OtaError("The recovery record lacks the complete source/relay/target settings.")
        if any(not _same_radio(self.transfer_radio, radio_tuple(v["radio"])) for v in self.saved.values()):
            raise ota.OtaError("The recorded routed radio settings do not agree.")
        minutes = s.get("window_minutes")
        if type(minutes) is not int or not 1 <= minutes <= 10080:
            raise ota.OtaError("The recovery record has an invalid temporary window.")
        self.window_minutes = minutes
        self.source_transfer_radio = self.target_transfer_radio = self.transfer_radio
        self.source_radio = "radio"

    async def _close_owned_window(self, label):
        if not self._state.get(label + "_window_attempted"):
            return
        command = self._command(label)
        actual = await command("get tempradio")
        if temp_state(actual) == "pending":
            await asyncio.sleep(2.2)
            actual = await command("get tempradio")
        if temp_state(actual) != "off":
            if not _same_radio(_window_radio(actual), self.transfer_radio):
                raise ota.OtaError(f"The {label} temporary radio changed; it was not cleared.")
            try:
                await command("normalradio", **({} if label == "source" else {"retries": 0}))
            except (TimeoutError, asyncio.TimeoutError, ConnectionError, ota.TransmissionError):
                pass
            await asyncio.sleep(2.2)
            if temp_state(await command("get tempradio")) != "off":
                raise ota.OtaError(f"The {label} did not confirm its normal radio.")
        self.save(self._state["phase"], **{label + "_window_active": False})

    async def _restore_endpoint(self, label, *, restore_auto):
        command, settings = self._command(label), self.saved[label]
        if settings["rxps"] != "unsupported":
            desired = ota.parse_rxps_settings(settings["rxps"], label)
            if not self._rxps_equal(await self._read_rxps(command, label), desired):
                await command(ota.rxps_restore_command(desired))
                if not desired.enabled:
                    await command("set radio.rxps off")
        for field in ("hops", "autofetch", "autoinstall"):
            if field != "hops" and (not restore_auto or "mode=seeder-only" in settings["ota"]):
                continue
            value = re.search(rf"\b{field}=([A-Za-z0-9]+)", settings["ota"])
            if value:
                await command(f"ota config {field} {value[1]}")
        await self._verify_restored(command, label, settings, restore_auto=restore_auto)
        if "repeat" in settings:
            await command("set repeat " + settings["repeat"])
            if await command("get repeat") != settings["repeat"]:
                raise ota.OtaError(f"The {label} repeating setting did not restore.")

    async def cleanup(self):
        if not self.active:
            return []
        self._restore_routed_options()
        errors, target_safe = [], True
        try:
            await self._stop_serving()
        except Exception as exc:
            target_safe = False
            errors.append(f"Confirm the updater's source folder has stopped: {exc}")
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
        for label in ("target",) + tuple(reversed(self._relay_labels())) + ("source",):
            if self._state.get(label + "_settings_changed") is False:
                continue
            try:
                await self._close_owned_window(label)
                await self._restore_endpoint(label, restore_auto=label != "target" or target_safe)
            except Exception as exc:
                errors.append(f"Restore the recorded {label} lease/RXPS/OTA/repeat settings: {exc}")
        self.save("needs cleanup" if errors else "finished", recovery_commands=errors)
        self.active = bool(errors)
        return errors
