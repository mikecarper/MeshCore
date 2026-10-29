#!/usr/bin/env python3
"""Guarded USB emergency-power trials with an independent RAW radio witness.

Run on the Mercerwood Pi after separately flashing a verified application.
The default is a plan only. --execute permits CLI changes and temporarily
stops only mctomqtt.service, restoring it in finally. No flashing, erasing,
power cycling, credential changes, or firmware downloads occur here.
Private transcripts remain in a newly created0700 run directory.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time


TARGETS = {
    "solarxiao33s": ("9EBCE8853076EE86", "SolarXiao 33S"),
    "rak4631": ("9AB3B64C641BA927", "RAK4631"),
    "heltec_v43": ("44:1B:F6:69:C9:C0", "Heltec V4"),
    "rak3401": ("0B81C9C68D8D01B4", "RAK3401"),
}
WITNESSES = {"rak3401": "0B81C9C68D8D01B4", "rak4631": "9AB3B64C641BA927",
             "solarxiao33s": "9EBCE8853076EE86"}
BENCH_RADIO = "909.5,500,5,5"
STATUS_RE = re.compile(
    r"mode=(manual|auto) active=(on|off) battery=(unknown|\d+%) "
    r"clock=(rtc|uptime) window=(awake|sleep) radio=(awake|sleep) "
    r"wake=(\d+)s hold=(\d+)s\b")


def private_json(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def status(reply):
    match = STATUS_RE.search(reply)
    if not match:
        raise RuntimeError("Incomplete emergency-power status")
    return dict(zip(("mode", "active", "battery", "clock", "window", "radio", "wake", "hold"),
                    (*match.groups()[:6], int(match[7]), int(match[8]))))


def reply_value(reply):
    match = re.search(r"(?m)^  -> (.*)\r?$", reply)
    if not match:
        raise RuntimeError("Missing completed infrastructure CLI reply")
    return match[1].removeprefix("> ").strip()


def public_key(reply):
    value = reply_value(reply)
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", value):
        raise RuntimeError("Cannot verify the complete public identity")
    return bytes.fromhex(value)


def advert_timestamp(line, identity):
    """Parse the actual Packet wire layout; never match TX/detail log lines."""
    match = re.search(r"(?:^|\s)RAW: ([0-9A-Fa-f]+)\s*$", line)
    if not match:
        match = re.fullmatch(r"R([0-9A-Fa-f]+)", line.strip())
    if not match or len(match[1]) % 2:
        return None
    raw = bytes.fromhex(match[1])
    if len(raw) < 2 or raw[0] >> 6 or (raw[0] >> 2) & 15 != 4:
        return None
    index = 5 if (raw[0] & 3) in (0, 3) else 1
    if len(raw) <= index:
        return None
    count, width = raw[index] & 63, (raw[index] >> 6) + 1
    if width == 4 or count * width > 64:
        return None
    index += 1 + count * width
    if len(raw) < index + 100 or raw[index:index + 32] != identity:
        return None
    return int.from_bytes(raw[index + 32:index + 36], "little")


def rxps_restore(value):
    match = re.fullmatch(r"(on|off),level=(\d+),preamble=(\d+),rx=(\d+),sleep=(\d+)", value)
    if not match:
        raise RuntimeError("Cannot safely preserve RX power-saving configuration")
    enabled, level, preamble, rx, sleep = match.groups()
    if enabled == "off":
        return "set radio.rxps off"
    if int(level):
        return f"set radio.rxps level {level} preamble {preamble}"
    return f"set radio.rxps {rx} {sleep}"


def clock_epoch(reply):
    value = reply_value(reply)
    try:
        return int(datetime.strptime(value, "%H:%M - %d/%m/%Y UTC").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        raise RuntimeError("Unrecognized current clock response") from None


class Console:
    """Single reader records RAW traffic even while no USB writes are made."""
    def __init__(self, serial_number, transcript):
        import serial
        from serial.tools import list_ports
        matches = [p for p in list_ports.comports()
                   if (p.serial_number or "").upper() == serial_number.upper()
                   and p.interface != "MeshCore Logging"]
        if len(matches) != 1:
            raise RuntimeError("Missing or ambiguous exact application USB identity")
        guard = subprocess.run(["sudo", "-n", "fuser", matches[0].device],
                               capture_output=True, timeout=5)
        if guard.returncode != 1 or guard.stdout or guard.stderr:
            raise RuntimeError("Serial ownership could not be proved idle")
        self.serial_number = serial_number
        self.port = serial.Serial(port=None, baudrate=115200, timeout=.03, exclusive=True, dsrdtr=True)
        self.port.dtr, self.port.rts = True, False
        self.port.port = matches[0].device
        self.port.open()
        self.port.rts, self.port.dtr = False, True
        self.log = os.fdopen(os.open(transcript, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb", buffering=0)
        self.condition = threading.Condition()
        self.lines = []
        self.running = True
        self.failed = False
        self.last_write = 0
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        # Boot banners and a prior host's partial input can share a line.
        # End that input before the first exact-echo transaction; never replay
        # a timed-out command or infer a reply from an unframed startup tail.
        self.port.write(b"\r")
        self.port.flush()
        time.sleep(.3)

    def _read(self):
        pending = bytearray()
        try:
            while self.running:
                chunk = self.port.read(min(self.port.in_waiting or 1, 4096))
                if not chunk:
                    continue
                self.log.write(chunk)
                pending.extend(chunk)
                if len(pending) > 65536:
                    raise RuntimeError("Unterminated serial line exceeds capture bound")
                while b"\n" in pending:
                    line, _, pending = pending.partition(b"\n")
                    with self.condition:
                        self.lines.append((time.monotonic(), line.decode(errors="replace").rstrip("\r")))
                        self.condition.notify_all()
        except Exception:
            with self.condition:
                self.failed = True
                self.condition.notify_all()

    def command(self, command, timeout=25, allow_error=False):
        if not command or any(c in command for c in "\r\n\0") or self.failed:
            raise RuntimeError("Unsafe command or desynchronized CLI session")
        with self.condition:
            first = len(self.lines)
            self.port.write((command + "\r").encode("ascii"))
            self.port.flush()
            self.last_write = time.monotonic()
            deadline = self.last_write + timeout
            while time.monotonic() < deadline and not self.failed:
                text = "\n".join(line for _, line in self.lines[first:]) + "\n"
                echo = re.search(r"(?:^|\n)(?:> )?" + re.escape(command) + r"\n", text)
                if echo:
                    done = re.search(r"(?m)^  -> .*\n", text[echo.end():])
                    if done:
                        reply = text[echo.start():echo.end() + done.end()]
                        if not allow_error and re.search(r"\b(?:Error|ERROR|ERR|Unknown)\b|(?m:^  -> \?\?:)", reply):
                            raise RuntimeError(f"CLI rejected {command.split()[0]} request; see private transcript")
                        return reply
                self.condition.wait(min(.1, max(0, deadline - time.monotonic())))
        self.failed = True
        raise RuntimeError("CLI response incomplete; session will not be reused")

    def reboot(self, command, transcript, expected_key, after_identity=None):
        if command not in ("reboot", "clkreboot") or self.failed:
            raise RuntimeError("Unverified reboot request")
        self.port.write((command + "\r").encode("ascii"))
        self.port.flush()
        self.close()
        time.sleep(3)
        deadline = time.monotonic() + 40
        while True:
            try:
                candidate = Console(self.serial_number, transcript)
                break
            except (RuntimeError, OSError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)
        try:
            if public_key(candidate.command("get public.key")) != expected_key:
                raise RuntimeError("Identity changed after reboot")
            if after_identity:
                after_identity(candidate)
        except BaseException:
            candidate.close()
            raise
        return candidate

    def close(self):
        self.running = False
        self.thread.join(timeout=2)
        self.port.close()
        self.log.close()


@contextmanager
def paused_collector():
    """Restore the existing unit state without reading its credentials."""
    unit = "/etc/systemd/system/mctomqtt.service"
    digest = hashlib.sha256(Path(unit).read_bytes()).digest()
    active = subprocess.run(["systemctl", "is-active", "--quiet", "mctomqtt.service"]).returncode == 0
    try:
        if active:
            subprocess.run(["sudo", "-n", "systemctl", "stop", "mctomqtt.service"], check=True, timeout=30)
        yield
    finally:
        if hashlib.sha256(Path(unit).read_bytes()).digest() != digest:
            raise RuntimeError("Collector unit changed during bench run; inspect before restoring")
        if active:
            subprocess.run(["sudo", "-n", "systemctl", "start", "mctomqtt.service"], check=True, timeout=30)
            subprocess.run(["systemctl", "is-active", "--quiet", "mctomqtt.service"], check=True, timeout=15)


def snapshot(console):
    keys = ("radio", "tx", "repeat", "radio2", "tempradio", "radio.rxps.config",
            "usb.logging", "usb.logging.always", "radioat", "tempradioat", "radioat2", "tempradioat2")
    values = {key: reply_value(console.command("get " + key, allow_error=True)) for key in keys}
    for key in ("radioat", "tempradioat", "radioat2", "tempradioat2"):
        if values[key] != ("-none-" if key in ("radioat", "tempradioat") else "slots:"):
            raise RuntimeError("Bench requires confirmed empty future radio schedules")
    if not re.fullmatch(r"\d+(?:\.\d+)?,\d+(?:\.\d+)?,\d+,\d+", values["radio"]):
        raise RuntimeError("Unrecognized primary radio configuration")
    if values["tempradio"] != "off" or values["radio2"] not in ("off", "off (manual)", "off (automatic allowed)"):
        raise RuntimeError("Bench requires inactive temporary and secondary radio profiles")
    if values["repeat"] not in ("on", "off") or not re.fullmatch(r"-?\d+", values["tx"]):
        raise RuntimeError("Cannot safely restore TX/repeater configuration")
    values["rxps_restore"] = rxps_restore(values["radio.rxps.config"])
    return values


def require_distinct_original_frequencies(target, witness):
    # A baseline RF success must not be two failed retunes remaining together
    # on a shared original channel. These guarded bench pairs start apart.
    if abs(float(target["radio"].split(",")[0]) - float(witness["radio"].split(",")[0])) < .001:
        raise RuntimeError("Independent witness must start on a different original main frequency")


def apply_radio(console, value):
    admission = reply_value(console.command("set radio " + value))
    if "reboot" in admission.lower() or "applying in 2s" not in admission:
        raise RuntimeError("Live radio application not confirmed; a saved getter cannot prove an RF retune")
    time.sleep(4)  # CLI admits primary radio changes with a2-second reply grace.
    actual = tuple(map(float, reply_value(console.command("get radio")).split(",")))
    wanted = tuple(map(float, value.split(",")))
    if any(abs(a - b) > .001 for a, b in zip(actual, wanted)):
        raise RuntimeError("Saved primary radio differs after applying the bench profile")


def isolate(console, saved, witness=False):
    console.command("set radio.rxps off")
    console.command("set repeat off")
    console.command("set tx -9")
    apply_radio(console, BENCH_RADIO)
    if witness:
        console.command("set usb.logging on")
        if saved["usb.logging.always"] in ("on", "off"):
            console.command("set usb.logging.always on")


def restore(console, saved):
    apply_radio(console, saved["radio"])
    console.command("set tx " + saved["tx"])
    console.command(saved["rxps_restore"])
    console.command("set repeat " + saved["repeat"])
    for key in ("usb.logging.always", "usb.logging"):
        if saved[key] in ("on", "off"):
            console.command("set " + key + " " + saved[key])
    current = snapshot(console)
    for key in ("radio", "tx", "repeat", "radio2", "radio.rxps.config", "usb.logging", "usb.logging.always"):
        if key == "radio":
            if any(abs(float(a) - float(b)) > .001 for a, b in zip(current[key].split(","), saved[key].split(","))):
                raise RuntimeError("Original radio was not restored")
        elif current[key] != saved[key]:
            raise RuntimeError("Original " + key + " was not restored")


def await_status(console, expected, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        observed = status(console.command("get power.emergency"))
        if all(observed[key] == value for key, value in expected.items()):
            return observed
        if time.monotonic() > deadline:
            raise RuntimeError("Emergency status did not reach " + repr(expected))
        time.sleep(.5)


def wireless_snapshot(console):
    value = reply_value(console.command("get 2.4ghz"))
    match = re.fullmatch(r"wifi=(on|off|unavailable), bluetooth=(on|off|unavailable), "
                         r"espnow=(on|off|unavailable)( \(pending\))?", value)
    if not match:
        raise RuntimeError("Wireless runtime state is unavailable or reports a failed transition")
    return dict(zip(("wifi", "bluetooth", "espnow", "pending"),
                    (*match.groups()[:3], bool(match[4]))))


def hold_network_clock(console):
    """Keep NTP from replacing accelerated RTC values; credentials stay saved."""
    console.command("set power.awake 1")
    await_status(console, {"radio": "awake", "window": "awake"})
    deadline = time.monotonic() + 35
    while True:
        value = reply_value(console.command("set wifi off force", allow_error=True))
        if value.startswith("OK"):
            break
        if not value.startswith(("Error: emergency sleep", "Error: scheduled sleep")) or time.monotonic() > deadline:
            raise RuntimeError("WiFi runtime clock isolation was rejected")
        time.sleep(.1)  # Wait for the completed awake request to resume its backend.
    while True:
        live = wireless_snapshot(console)
        if live["wifi"] == "off" and not live["pending"]:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("WiFi/NTP did not stop for accelerated clock testing")
        time.sleep(.1)
    console.command("set power.awake 0")


def restore_wireless(console, original):
    for service in ("espnow", "wifi"):
        live = wireless_snapshot(console)
        if original[service] != live[service]:
            if original[service] == "unavailable":
                raise RuntimeError("Wireless availability changed during the test")
            console.command("set " + service + " " + original[service]
                            + (" force" if original[service] == "off" else ""))
            deadline = time.monotonic() + 35
            while True:
                live = wireless_snapshot(console)
                if live[service] == original[service] and not live["pending"]:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Original wireless runtime subset was not restored")
                time.sleep(.1)
    if wireless_snapshot(console) != original:
        raise RuntimeError("Original wireless runtime subset differs after restoration")


def await_advert(witness, identity, after, minimum_epoch, timeout=20, maximum_epoch=None):
    deadline = time.monotonic() + timeout
    with witness.condition:
        while True:
            for stamp, line in witness.lines:
                epoch = advert_timestamp(line, identity)
                if (stamp >= after and epoch is not None and epoch >= minimum_epoch
                        and (maximum_epoch is None or epoch < maximum_epoch)):
                    return {"received_monotonic": stamp, "advert_epoch": epoch}
            if witness.failed or time.monotonic() > deadline:
                raise RuntimeError("Independent witness did not receive the target advertisement")
            witness.condition.wait(.1)


def run_trial(args):
    args.run_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    original = json.loads(args.identity_backup.read_text())
    serial_number, board_label = TARGETS[args.target]
    if original.get("usb_serial", "").upper() != serial_number.upper():
        raise RuntimeError("Private identity backup is for a different USB board")
    original_key = public_key(original["queries"]["get public.key"])
    report = {"board": board_label, "usb_serial": serial_number,
              "real_minute_lease": False, "accelerated_hourly_cycles": [],
              "manual_mode_survived_reboot": False, "cleanup": "not needed",
              "limitations": ["USB-powered manual entry; battery5% threshold/current savings not measured",
                              "Hourly boundaries accelerated with RTC; no real45-minute sleep duration claim",
                              "Application restoration is a separate guarded flashing step"]}
    target = witness = None
    target_saved = witness_saved = None
    wireless_before = None
    clock_guard = hold_network_clock if args.target == "heltec_v43" else None
    if clock_guard:
        report["limitations"].append("USB-held CPU idle fallback; detached USB light-sleep current not measured")
    target_changed = witness_changed = False
    changed_clock = False
    try:
        with paused_collector():
            try:
                target = Console(serial_number, args.run_dir / "target.private.log")
                witness_name = args.witness or ("solarxiao33s" if args.target == "rak3401" else "rak3401")
                if WITNESSES[witness_name] == serial_number:
                    raise RuntimeError("Target and independent witness must be different boards")
                witness = Console(WITNESSES[witness_name], args.run_dir / "witness.private.log")
                if public_key(target.command("get public.key")) != original_key:
                    raise RuntimeError("Target public identity differs from its private baseline backup")
                actual_board = reply_value(target.command("board"))
                if re.sub(r"[^a-z0-9]", "", board_label.lower()) not in re.sub(r"[^a-z0-9]", "", actual_board.lower()):
                    raise RuntimeError("Unexpected target board label")
                version = reply_value(target.command("ver"))
                if version != args.expected_version:
                    raise RuntimeError("Running firmware differs from the explicitly selected verified build")
                witness_board = re.sub(r"[^a-z0-9]", "", reply_value(witness.command("board")).lower())
                if re.sub(r"[^a-z0-9]", "", TARGETS[witness_name][1].lower()) not in witness_board:
                    raise RuntimeError("Wrong independent witness board")
                report["firmware"] = version
                target_saved, witness_saved = snapshot(target), snapshot(witness)
                if clock_guard:
                    wireless_before = wireless_snapshot(target)
                    if wireless_before["pending"] or wireless_before["wifi"] == "unavailable":
                        raise RuntimeError("WiFi must expose a settled reversible runtime state")
                private_json(args.run_dir / "settings.private.json", {"target": target_saved, "witness": witness_saved,
                                                                       "wireless": wireless_before})
                initial = status(target.command("get power.emergency"))
                if initial["mode"] != "auto" or initial["hold"]:
                    raise RuntimeError("Target must start with automatic mode and no awake lease")
                if witness_saved["usb.logging.always"] not in ("on", "off"):
                    raise RuntimeError("Witness needs the current logging override and live radio CLI")
                require_distinct_original_frequencies(target_saved, witness_saved)
                witness_changed = True
                isolate(witness, witness_saved, witness=True)
                target_changed = True
                isolate(target, target_saved)
                report["cleanup"] = "required"
                target.command("advert.zerohop")
                report["baseline_advert"] = await_advert(witness, original_key, target.last_write, 0)
                target.command("set power.awake 0")
                target.command("set power.emergency auto")
                if clock_guard:
                    clock_guard(target)
                # Past test clocks avoid future advert timestamps in the witness's
                # neighbour cache. RF stays isolated until present time is proven.
                future = ((int(time.time()) // 3600) - 48) * 3600
                changed_clock = True
                target = target.reboot("clkreboot", args.run_dir / "target.bench-clock.private.log", original_key, clock_guard)
                target.command(f"time {future + 1800}")
                target.command("set power.emergency on")
                report["manual_entry"] = await_status(target, {"mode": "manual", "active": "on", "radio": "sleep", "window": "sleep", "hold": 0})
                started = time.monotonic()
                target.command("set power.awake 1")
                awake = await_status(target, {"radio": "awake", "window": "awake"})
                if not 30 <= awake["hold"] <= 60:
                    raise RuntimeError("The one-minute awake lease was not observed")
                target.command("advert.zerohop")
                report["lease_advert"] = await_advert(witness, original_key, target.last_write, future,
                                                       maximum_epoch=future + 3600)
                time.sleep(max(0, started + 65 - time.monotonic()))
                await_status(target, {"radio": "sleep", "window": "sleep", "hold": 0})
                report["real_minute_lease"] = True
                for cycle in range(2):
                    cycle_hour = future + cycle * 3600
                    if cycle:
                        target.command(f"time {cycle_hour + 1800}")
                        await_status(target, {"radio": "sleep", "window": "sleep"})
                    # Defer signing/dispatch until the mesh loop runs at timer wake.
                    target.command("advert.zerohop")
                    target.command(f"time {cycle_hour + 3580}")
                    last_write = target.last_write
                    received = await_advert(witness, original_key, last_write, cycle_hour + 3600, timeout=45,
                                            maximum_epoch=cycle_hour + 3660)
                    if target.last_write != last_write:
                        raise RuntimeError("Target USB write occurred during the passive timer-wake interval")
                    received["no_usb_write_seconds"] = time.monotonic() - last_write
                    received["status_after_passive_capture"] = await_status(target, {"radio": "awake", "window": "awake"})
                    report["accelerated_hourly_cycles"].append(received)
                target.command(f"time {future + 2 * 3600 + 1800}")
                await_status(target, {"mode": "manual", "radio": "sleep"})
                target = target.reboot("reboot", args.run_dir / "target.after-reboot.private.log", original_key, clock_guard)
                report["after_reboot"] = await_status(target, {"mode": "manual", "active": "on", "hold": 0})
                target.command(f"time {future + 3 * 3600 + 1800}")
                report["after_reboot_sleep"] = await_status(target, {"mode": "manual", "radio": "sleep", "window": "sleep"})
                report["manual_mode_survived_reboot"] = True
                report["passed"] = True
            finally:
                cleanup_errors = []
                if target and target_changed:
                    try:
                        target.command("set power.awake 0")
                        target.command("set power.emergency auto")
                        await_status(target, {"mode": "auto", "radio": "awake"})
                        if changed_clock:
                            target = target.reboot("clkreboot", args.run_dir / "target.clock-restored.private.log", original_key, clock_guard)
                            target.command(f"time {int(time.time())}")
                            current_time = clock_epoch(target.command("clock"))
                            if abs(current_time - time.time()) > 90:
                                raise RuntimeError("Present clock not confirmed; retaining isolated RF")
                        restore(target, target_saved)
                        report["final_power"] = status(target.command("get power.emergency"))
                        if wireless_before:
                            restore_wireless(target, wireless_before)
                            report["wireless_runtime_restored"] = True
                    except Exception as error:
                        cleanup_errors.append("Target cleanup: " + str(error))
                if witness and witness_changed:
                    try:
                        witness_key = public_key(witness.command("get public.key"))
                        if abs(clock_epoch(witness.command("clock")) - time.time()) > 90:
                            witness = witness.reboot("clkreboot", args.run_dir / "witness.clock-restored.private.log", witness_key)
                            witness.command(f"time {int(time.time())}")
                            if abs(clock_epoch(witness.command("clock")) - time.time()) > 90:
                                raise RuntimeError("Witness present clock not confirmed; retaining isolated RF")
                        restore(witness, witness_saved)
                    except Exception as error:
                        cleanup_errors.append("Witness cleanup: " + str(error))
                for console in (target, witness):
                    if console:
                        console.close()
                report["cleanup"] = "complete" if not cleanup_errors else "NEEDS MANUAL RECOVERY"
                if cleanup_errors:
                    report["cleanup_errors"] = cleanup_errors
                    raise RuntimeError("Bench cleanup incomplete; inspect the private report before further tests")
    except BaseException as error:
        report["passed"] = False
        report["error"] = str(error)
        raise
    finally:
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        private_json(args.run_dir / "report.json", report)
        print(json.dumps({key: report.get(key) for key in ("board", "passed", "cleanup", "real_minute_lease", "manual_mode_survived_reboot")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--witness", choices=WITNESSES, help="Default RAK3401, or qualified SolarXiao when testing the3401")
    parser.add_argument("--expected-version", required=True, help="Exact ver response from the verified new build")
    parser.add_argument("--identity-backup", type=Path, required=True, help="Private preflash JSON for the selected USB identity")
    parser.add_argument("--run-dir", type=Path, required=True, help="New private run directory (must not already exist)")
    parser.add_argument("--execute", action="store_true", help="Permit the documented CLI changes and collector pause")
    args = parser.parse_args()
    if not args.execute:
        print("Plan only: isolated909.5/BW500/SF5/CR5, manual sleep, real60s lease, two accelerated hourly timer wakes, reboot persistence, finally restore clock/RF/collector. No hardware touched.")
        return
    run_trial(args)


if __name__ == "__main__":
    main()
