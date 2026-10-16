#!/usr/bin/env python3
"""Guarded four-node mOTA LoRa -> ESP-NOW -> LoRa transfer-only HIL control.

Controls only three explicit USB ASCII devices: bridge A909.5, bridge B908.5,
and the908.5 receiver. The separately owned909.5 USB seeder is never opened.
Requires already active500/SF5/CR5/preamble128 primary TempRadio, radio2off,
and immutable matching-frequency TX guards. May TRANSMIT and changes OTA
policy plus bridge BESP-NOW; clears ONLY the receiver's staged OTA slot.
Never flashes, reboots, changes RF tuples, enables automatic fetching/install,
or installs firmware. BESP-NOW is restored on exit. --plan opens no ports.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time


class Failure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def diagnostic_record(text, raw="", status="reply"):
    allowed = re.fullmatch(r"(?:get (?:lab\.tx|tempradio2?|radio2|repeat|espnow\.(?:enabled|running))"
                           r"|set espnow\.enabled (?:on|off)|ota (?:self|stats|ls|cancel|config|dev verify)"
                           r"|ota config (?:autofetch off|autoinstall off|hops 3)|ota pull [\da-fA-F]{8} flash)", text)
    if not allowed:
        return {"command": "unrecognized command; diagnostic omitted", "status": status}
    # No secret-reading commands are permitted in this controller. Still scrub
    # potential unrelated long keys if unsolicited logging enters the capture.
    safe = re.sub(r"(?<![\da-fA-F])[\da-fA-F]{32,}(?![\da-fA-F])", "<redacted>", raw)
    safe = re.sub(r"(?im)\b(?:password|pwd|psk|private.?key)\s*[:=].*", "<redacted>", safe)
    if len(safe) > 1536:
        safe = safe[:768] + "\n<bounded middle omitted>\n" + safe[-768:]
    return {"command": text, "status": status, "reply_characters": len(raw),
            "arrow_markers": len(re.findall(r"(?:^|\n)[ \t]*->[ \t]*", raw)), "reply": safe}


def failure_history(nodes):
    return {name: [diagnostic_record(item.get("command", ""), item.get("reply", ""), item.get("status", "reply"))
                   for item in getattr(node, "commands", [])[-3:]] for name, node in nodes.items()}


def reply(raw):
    # The classic reader supplies a fresh reply-marker/completed-line boundary.
    # Preserve ALL reply lines, not merely a160-byte first-line approximation.
    matches = list(re.finditer(r"(?:^|\n)[ \t]*->[ \t]*", raw))
    require(len(matches) == 1, "missing or ambiguous ASCII reply marker")
    text = raw[matches[0].end():].replace("\r", "")
    text = re.sub(r"\n>[ \t]*\Z", "", text).strip()
    require(text and len(text) <= 65536, "empty or overlong completed ASCII reply")
    return re.sub(r"^>[ \t]*", "", text)


def command(node, text, *, allow_error=False):
    value = reply(node.command(text, timeout=15))
    require(allow_error or not re.match(r"(?:ERR|Error|Err)\b", value),
            "command rejected: " + " ".join(text.split()[:2]))
    return value


def read_fresh_reply(port, timeout):
    deadline, data = time.monotonic() + timeout, bytearray()
    last_data, first_complete = None, None
    while time.monotonic() < deadline:
        chunk = port.read(min(port.in_waiting or 1, 4096))
        now = time.monotonic()
        if chunk:
            data.extend(chunk)
            last_data = now
            require(len(data) <= 65536, "ASCII capture exceeded bounded limit")
        text = data.decode("utf-8", "replace")
        marker = re.search(r"(?:^|\n)[ \t]*->[ \t]*", text)
        if marker:
            body = text[marker.end():]
            if re.search(r"(?:^|\n)>[ \t]+\Z", body):
                return text
            if "\n" in body:
                if first_complete is None:
                    first_complete = now
                # Classic repeaters print no terminal prompt. A completed
                # reply line plus150ms quiet captures bounded multiline output.
                if not chunk and now - last_data >= .15:
                    return text
                require(now - first_complete <= 1, "ASCII reply did not become quiet within1second")
    error = Failure("fresh ASCII reply marker/completed line not received")
    error.partial_reply = data.decode("utf-8", "replace")
    raise error


def serial_node_factory():
    from ethernet_primary_probe import SerialNode

    class ChainSerialNode(SerialNode):
        def __init__(self, path):
            import serial
            from serial.tools import list_ports
            from serial_session import assert_port_idle, open_configured_session
            matches = [item for item in list_ports.comports()
                       if os.path.realpath(item.device) == os.path.realpath(path)]
            require(len(matches) == 1, "selected classic USB identity absent or ambiguous")
            info = matches[0]
            self.identity = {"port": info.device, "usb_serial": info.serial_number,
                             "vid": info.vid, "pid": info.pid, "location": info.location}
            self.commands = []
            self.port = serial.Serial(port=None, baudrate=115200, timeout=.04,
                                      write_timeout=2, exclusive=True)
            self.port.port, self.port.rts = info.device, False
            self.port.dtr = info.vid in (0x303A, 0x2886, 0x239A)
            assert_port_idle(info.device)
            try:
                open_configured_session(self.port)
                # These are classic repeater consoles, not a Companion binary
                # transport. Never put TERM tokens in their command buffer.
                require(self.port.write(b"\r") == 1, "short classic CLI boundary write")
                self.port.flush()
                self.hello = self.read(.5, quiet=.15)
            except BaseException:
                self.port.close()
                raise

        def command(self, text, timeout=15):
            require(text and not any(value in text for value in "\r\n\0"), "one ASCII command required")
            # Full command echo can be truncated on the3401 UART. Drain old
            # output, then allow only one outstanding request and require a
            # fresh real reply marker plus completed line, not its echo.
            self.read(.3, quiet=.15)
            # Classic repeater main ignores LF; only CR executes the command.
            encoded = (text + "\r").encode("ascii")
            require(self.port.write(encoded) == len(encoded), "short ASCII command write")
            self.port.flush()
            try:
                value = read_fresh_reply(self.port, timeout)
            except Exception as error:
                self.commands.append({"command": text, "reply": getattr(error, "partial_reply", ""),
                                      "status": type(error).__name__})
                raise
            self.commands.append({"command": text, "reply": value, "status": "reply"})
            return value

        def close(self):
            # No Companion TERM-STOP token on the classic line editor either.
            self.port.close()

    return ChainSerialNode


def identity(path, *, serial=None, location=None):
    from serial.tools import list_ports
    matches = [item for item in list_ports.comports()
               if os.path.realpath(item.device) == os.path.realpath(path)]
    require(len(matches) == 1, "selected USB path absent or ambiguous")
    info = matches[0]
    if serial:
        require((info.serial_number or "").upper() == serial.upper(), "USB serial mismatch")
    else:
        require(location and info.location == location and info.vid == 0x1A86 and info.pid == 0x7523
                and not info.serial_number, "unserialed Indicator must match exact CH340 VID/PID/location")
    return {"port": info.device, "serial": info.serial_number, "vid": info.vid,
            "pid": info.pid, "location": info.location}


def safety(nodes):
    report = {}
    for name, frequency in (("a", 909.5), ("b", 908.5), ("target", 908.5)):
        node = nodes[name]
        guard = command(node, "get lab.tx")
        expected = f"HIL TX lock={frequency:.3f} MHz; off-frequency TX blocked; primary RX-only when off-frequency"
        require(guard == expected, name + ": immutable matching-frequency TX lock required")
        live = command(node, "get tempradio")
        match = re.fullmatch(r"([\d.]+),([\d.]+),(\d+),(\d+),(\d+)", live)
        require(match is not None and tuple(float(value) for value in match.groups())
                == (frequency, 500, 5, 5, 128), name + ": primary TempRadio tuple/preamble mismatch")
        secondary, temporary = command(node, "get radio2"), command(node, "get tempradio2")
        require(re.fullmatch(r"off(?:;[^\r\n]*)?", secondary) is not None and temporary == "off",
                name + ": radio2 must already be off")
        require(command(node, "get repeat") == "on", name + ": repeater forwarding must be on")
        report[name] = {"guard": guard, "live_primary": live, "radio2": secondary,
                        "tempradio2": temporary, "repeat": "on"}
        if name != "target":
            enabled, running = command(node, "get espnow.enabled"), command(node, "get espnow.running")
            require(enabled == running == "on", name + ": ESP-NOW must already be on and running")
            report[name].update(espnow_enabled=enabled, espnow_running=running)
    return report


def configure_manual(nodes):
    result = {}
    for name, node in nodes.items():
        for setting in ("autofetch off", "autoinstall off", "hops 3"):
            require(command(node, "ota config " + setting).startswith("OK"), "OTA policy was not acknowledged")
        value = command(node, "ota config")
        require(all(token in value for token in ("autofetch=off", "autoinstall=off", "hops=3")),
                "manual-only three-hop OTA policy did not read back")
        result[name] = value
    return result


def progress(node, mid):
    value = command(node, "ota stats")
    if "fetch idle" in value:
        return {"state": "idle", "have": 0, "total": 0, "mid": None, "reply": value}
    match = re.search(r"\bfetch ([^|]*?) (\d+)/(\d+)(?: [^|]*?)?\bid=([\da-fA-F]{8})\b", value)
    require(match is not None, "OTA stats lost complete fetch fields; refusing truncated/ambiguous progress")
    require(match[4].lower() == mid.lower(), "receiver is fetching a different manifest ID")
    have, total = int(match[2]), int(match[3])
    require(0 <= have <= total <= 65536, "invalid OTA block counters")
    return {"state": match[1], "have": have, "total": total, "mid": match[4].lower(), "reply": value}


def body_identity(node):
    value = command(node, "ota self")
    match = re.search(r"\bself body=(\d+) image=(\d+) base_hash=([\da-fA-F]{16})\b", value)
    require(match is not None, "running firmware identity unavailable")
    return {"body_bytes": int(match[1]), "image_bytes": int(match[2]), "hash": match[3].lower()}


def bridge_b(node, enabled):
    require(command(node, "set espnow.enabled " + ("on" if enabled else "off")) == "OK",
            "bridge B toggle was not acknowledged")
    desired = "on" if enabled else "off"
    require(command(node, "get espnow.enabled") == desired
            and command(node, "get espnow.running") == desired, "bridge B state did not read back")


def fresh_pull(target, mid):
    require(command(target, "ota cancel").startswith("OK"), "could not clear receiver OTA staging slot")
    require(progress(target, mid)["state"] == "idle", "OTA staging clear did not leave idle")
    value = command(target, "ota pull " + mid + " flash")
    require(value.startswith("OK pulling") and "mid=" + mid.lower() in value.lower(),
            "fresh selected manifest pull was not acknowledged")
    return value


def observe(target, mid, seconds, poll, record):
    deadline = time.monotonic() + seconds
    observations = []
    while True:
        sample = progress(target, mid)
        sample["at"] = time.monotonic()
        observations.append(sample)
        record(sample)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return observations
        time.sleep(min(poll, remaining))


def run_chain(nodes, mid, *, timeout, outage, poll, persist):
    report = {"schema": 1, "result": "RUNNING", "manifest_id": mid,
              "started_utc": datetime.now(timezone.utc).isoformat(), "events": [],
              "no_install_command": True, "rf_capture_independent": False,
              "proof_scope": "receiver block/hash verification with bridge-B off/on counterfactual; not independent RF capture",
              "settings_restored": False, "bridge_b_restored": False}
    restore = False
    target, b = nodes["target"], nodes["b"]

    def event(kind, **values):
        report["events"].append({"kind": kind, "at": time.monotonic(), **values})
        persist(report)

    try:
        report["safety_before"] = safety(nodes)
        report["running_before"] = {name: body_identity(node) for name, node in nodes.items()}
        report["manual_policy"] = configure_manual(nodes)
        persist(report)
        # Discover with both bridges enabled, before breaking the required link.
        deadline = time.monotonic() + min(timeout, 90)
        while True:
            catalog = command(target, "ota ls")
            if re.search(r"(?<![\da-fA-F])" + re.escape(mid) + r"(?![\da-fA-F])", catalog, re.I):
                event("catalog_visible", reply=catalog)
                break
            require(time.monotonic() < deadline, "selected manifest did not reach target catalog")
            time.sleep(1)
        restore = True  # Include partial-toggle failure in cleanup.
        bridge_b(b, False)
        event("initial_bridge_off")
        fresh_pull(target, mid)
        blocked = observe(target, mid, outage, poll, lambda sample: event("initial_off_progress", **sample))
        require(all(sample["have"] == 0 for sample in blocked), "received OTA blocks while required bridge B was off")
        report["initial_off_no_completed_blocks"] = True
        bridge_b(b, True)
        event("initial_bridge_on")
        if blocked[-1]["state"].startswith("failed"):
            require(command(target, "ota pull " + mid + " flash").startswith("OK"), "could not retry selected pull after initial outage")
            event("retry_after_initial_manifest_timeout")
        mid_proven = False
        for attempt in (1, 2):
            deadline, retried = time.monotonic() + timeout, False
            if attempt == 2:
                # A very fast first transfer can finish before one counter poll.
                # One bounded fresh refetch tries again with faster polling.
                event("fresh_refetch_for_missed_midpoint")
                fresh_pull(target, mid)
            while True:
                require(time.monotonic() < deadline, "bounded chain transfer deadline expired")
                sample = progress(target, mid)
                event("on_progress", attempt=attempt, **sample)
                if sample["state"] == "done":
                    require(sample["total"] > 0 and sample["have"] == sample["total"], "done did not include all blocks")
                    break
                if sample["state"].startswith("failed"):
                    require(not retried, "chain fetch failed again after one same-ID resume")
                    require(command(target, "ota pull " + mid + " flash").startswith("OK"), "same-ID resume was not acknowledged")
                    retried = True
                    event("same_id_resume_after_failure", previous_have=sample["have"])
                elif not mid_proven and 0 < sample["have"] < sample["total"]:
                    bridge_b(b, False)
                    event("midpoint_bridge_off", attempt=attempt, before=sample)
                    # Already transmitted frames may still finish. Ignore the
                    # first2s, then demand no new blocks for the remaining gap.
                    off = observe(target, mid, outage, poll, lambda current: event("midpoint_off_progress", **current))
                    settled = [current for current in off if current["at"] >= off[0]["at"] + min(2, outage / 3)]
                    require(settled, "no settled midpoint outage observation")
                    if settled[-1]["state"] == "done":
                        event("midpoint_not_proven", reason="in-flight bytes finished before settled outage")
                    else:
                        require(settled[0]["have"] > 0 and settled[0]["have"] < settled[0]["total"], "midpoint did not retain a partial image")
                        require(all(current["have"] == settled[0]["have"] for current in settled), "new blocks arrived during settled bridge-off gap")
                        mid_proven = True
                        report["midpoint_off_stalled"] = {"retained_blocks": settled[0]["have"], "total": settled[0]["total"],
                                                           "settled_seconds": settled[-1]["at"] - settled[0]["at"]}
                    bridge_b(b, True)
                    event("midpoint_bridge_on")
                time.sleep(min(poll, .05) if attempt == 2 else poll)
            if mid_proven:
                break
        report["final_progress"] = progress(target, mid)
        verified = command(target, "ota dev verify")
        require(all(re.search(r"\b" + name + r"=1\b", verified) for name in ("parsed", "root", "payload", "ok")),
                "staged container did not verify hashes/Merkle/integrity")
        report["staged_verification"] = verified
        report["running_after"] = {name: body_identity(node) for name, node in nodes.items()}
        require(report["running_after"] == report["running_before"], "running firmware changed during transfer-only test")
        report["safety_after"] = safety(nodes)
        report["result"] = "PASS" if mid_proven else "TRANSFER_VERIFIED_MIDPOINT_NOT_PROVEN"
    except (Exception, KeyboardInterrupt) as error:
        report["result"] = "FAIL"
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:500]}
        report["failure"]["recent_commands"] = failure_history(nodes)
    finally:
        if restore:
            try:
                bridge_b(b, True)
                report["bridge_b_restored"] = True
            except Exception as error:
                report["restore_failure"] = type(error).__name__
                report["result"] = "FAIL"
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        persist(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("a", "b", "target"):
        parser.add_argument("--" + name + "-port")
    parser.add_argument("--a-serial")
    parser.add_argument("--b-location", help="exact already ROM-MAC-verified unserialed CH340 physical location")
    parser.add_argument("--target-serial")
    parser.add_argument("--mid")
    parser.add_argument("--timeout", type=float, default=300, help="per-attempt transfer bound; max1800")
    parser.add_argument("--outage", type=float, default=8)
    parser.add_argument("--poll", type=float, default=.25)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--allow-transfer-and-clear-stage", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(all(math.isfinite(value) for value in (args.timeout, args.outage, args.poll))
                and 15 <= args.timeout <= 1800 and 6 <= args.outage <= 15 and .05 <= args.poll <= 2, "invalid bounded timing")
        if args.plan:
            print(json.dumps({"topology": "source909.5 -> bridgeA909.5 -> ESP-NOW -> bridgeB908.5 -> target908.5",
                "source_port_opened": False, "rf": "500/SF5/CR5/preamble128", "outage_seconds": args.outage,
                "auto_fetch_install": False, "install_commands": False, "clears_target_ota_stage": True}, indent=2))
            return 0
        require(args.allow_transfer_and_clear_stage, "explicit --allow-transfer-and-clear-stage consent required")
        require(all((args.a_port, args.b_port, args.target_port, args.a_serial, args.b_location,
                     args.target_serial, args.mid, args.report)), "all USB identities, MID and fresh report required")
        require(re.fullmatch(r"[\da-fA-F]{8}", args.mid) is not None, "MID must be exactly8hex digits")
        require(len({os.path.realpath(path) for path in (args.a_port, args.b_port, args.target_port)}) == 3,
                "select three distinct physical USB paths")
        require(args.a_serial.upper() != args.target_serial.upper(), "bridge and receiver USB serials must differ")
        require(not args.report.exists() and args.report.parent.is_dir(), "fresh report in existing directory required")
    except Failure as error:
        parser.error(str(error))
    nodes, usb = {}, {}
    with args.report.open("x", encoding="utf-8") as stream:
        try:
            os.chmod(args.report, 0o600)
        except OSError:
            pass

        def persist(data):
            data["usb"] = usb
            stream.seek(0)
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.truncate()
            stream.flush()

        try:
            SerialNode = serial_node_factory()
            specs = (("a", args.a_port, {"serial": args.a_serial}), ("b", args.b_port, {"location": args.b_location}),
                     ("target", args.target_port, {"serial": args.target_serial}))
            for name, port, expected in specs:
                usb[name] = identity(port, **expected)
            for name, port, expected in specs:
                nodes[name] = SerialNode(port)
                identity(port, **expected)
            result = run_chain(nodes, args.mid.lower(), timeout=args.timeout, outage=args.outage, poll=args.poll, persist=persist)
        except (Exception, KeyboardInterrupt) as error:
            result = {"result": "FAIL", "failure": {"type": type(error).__name__, "message": str(error)[:500]}}
            persist(result)
        finally:
            for node in nodes.values():
                try:
                    node.close()
                except Exception:
                    pass
    print(json.dumps({"result": result["result"], "report": str(args.report), "failure": result.get("failure")}))
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
