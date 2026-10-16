#!/usr/bin/env python3
"""Guarded custom-PHY Meshtext goodput/loss benchmark, never a preset sweep.

Changes both selected radios and transmits ONLY on 909.5 MHz. The Companion
must already have the immutable lab TX lock and USA primary RX configuration.
No flashing, SSH, USB reset, primary change, or settings restoration is done.
Use --plan without hardware dependencies. Keys exist in RAM only, not reports.
Stock queue depletion is serial/device admission evidence, not proof of RF TX.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import secrets
import time

import meshtext_delivery as delivery

GUARD = "HIL TX lock=909.500 MHz; off-frequency TX blocked; primary RX-only when off-frequency"
PRIMARY = (Decimal("910.525"), Decimal("62.5"), 7, 5)
SFS = (7, 9, 10)
SIZES = (32, 160)


def check(condition, message):
    delivery.check(condition, message)


def radio_tuple(reply):
    match = re.fullmatch(r">?\s*(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)(?:[,; ].*)?", reply.strip())
    check(match, "missing or ambiguous primary radio tuple")
    return Decimal(match[1]), Decimal(match[2]), int(match[3]), int(match[4])


def primary_tx(reply):
    matches = re.findall(r"\bTX=(\d+),(\d+)(?=\s|;|$)", reply)
    check(len(matches) == 1, "missing or ambiguous per-profile TX counters")
    return int(matches[0][0])


def safety(companion):
    guard = companion.cli("get lab.tx")
    check(guard.removeprefix(">").strip() == GUARD, "exact immutable 909.500 MHz TX lock is required")
    primary = companion.cli("get radio")
    actual = radio_tuple(primary)
    check(abs(actual[0] - PRIMARY[0]) <= Decimal("0.00005") and actual[1:] == PRIMARY[1:],
          "preconfigure and reboot the Companion with USA primary 910.525,62.5,7,5")
    status = companion.cli("get radio2.status")
    check(primary_tx(status) == 0, "primary TX counter must remain zero; aborting benchmark")
    return {"guard": guard, "primary": primary, "profile_status": status, "primary_tx": 0}


def nominal_kbps(sf):
    return sf * 4 * 500 / (5 * (2 ** sf))


def check_stock_rf(stock, sf=None, key=None):
    delivery.require_stock_connected(stock)
    config = stock.iface.localNode.localConfig.lora
    check(not config.use_preset and abs(config.override_frequency - 909.5) < 0.00005
          and config.bandwidth == 500 and config.coding_rate == 5
          and abs(config.frequency_offset) < 0.00005, "stock must use custom 909.5 MHz / 500 kHz / CR5")
    check(config.spread_factor in (5, *SFS) and (sf is None or config.spread_factor == sf),
          "stock spreading factor mismatch")
    if key is not None:
        channel = stock.iface.localNode.channels[0]
        check(channel.role == 1 and channel.settings.name == "labtest"
              and channel.settings.psk == key, "stock private lab channel readback mismatch")
        check(config.hop_limit == 0 and config.tx_power == 2 and config.tx_enabled,
              "stock must have hops=0, TX=2 dBm, and TX enabled")


def configure_stock_sf(stock, key, sf):
    """Change only the fixed lab PHY, then wait for its deferred reboot."""
    check_stock_rf(stock)
    node = stock.iface.localNode
    node.beginSettingsTransaction()
    time.sleep(0.75)
    config = node.localConfig.lora
    config.use_preset = False
    config.bandwidth, config.spread_factor, config.coding_rate = 500, sf, 5
    config.override_frequency, config.frequency_offset, config.channel_num = 909.5, 0, 0
    config.hop_limit, config.tx_power, config.tx_enabled = 0, 2, True
    node.writeConfig("lora")
    time.sleep(0.75)
    node.commitSettingsTransaction()
    time.sleep(20)
    stock.iface.close()
    stock.iface = None

    def validate(observed):
        cfg, channel = observed.localConfig.lora, observed.channels[0]
        checks = {"custom_phy": not cfg.use_preset and cfg.bandwidth == 500
                  and cfg.spread_factor == sf and cfg.coding_rate == 5,
                  "frequency": abs(cfg.override_frequency - 909.5) < 0.00005
                  and abs(cfg.frequency_offset) < 0.00005 and cfg.channel_num == 0,
                  "tx": cfg.hop_limit == 0 and cfg.tx_power == 2 and cfg.tx_enabled,
                  "private_channel": channel.role == 1 and channel.settings.name == "labtest"
                  and channel.settings.psk == key,
                  "muted_role": observed.localConfig.device.role == 1}
        return [name for name, matched in checks.items() if not matched]

    result = delivery.reconnect_stock_readback(stock, validate)
    check_stock_rf(stock, sf, key)
    return result


def check_companion_rf(companion, sf):
    from meshtext_auto_radio2 import parse_profile
    actual = parse_profile(companion.cli("get radio2"))
    check(abs(actual[0] - 909.5) < 0.00005 and actual[1:5] == (500, sf, 5, "rxtx"),
          "Companion radio2 does not match the requested custom lab PHY")
    status = companion.cli("get radio2.status")
    check(re.search(r"\bpreamble=\d+,16(?:\s|;|$)", status), "radio2 must use the fixed 16-symbol MT preamble")
    check(companion.cli("get mt.hops").removeprefix(">").strip() == "0", "Companion must use hops=0")
    check(delivery.snapshot(companion)["delivery"]["count"] == 0, "Companion retries must be disabled")
    return {"saved_radio2": actual, "status": status}


def queue_snapshot(iface, packet_id=None):
    """Observe the SDK's documented queue without modifying it or resending.

    SDK reference: github.com/meshtastic/python/blob/master/meshtastic/mesh_interface.py
    QueueStatus/queue describe serial admission, not a physical TX-done event.
    """
    queue = getattr(iface, "queue", None)
    status = getattr(iface, "queueStatus", None)
    supported = isinstance(queue, Mapping)
    state = {"supported": supported, "packet_pending": None, "pending_entries": None}
    if supported:
        # Capture primitive items; the SDK reader can change the dictionary.
        try:
            items = list(queue.items())
        except RuntimeError:
            return state
        state["pending_entries"] = sum(bool(value) for _, value in items)
        if packet_id is not None:
            state["packet_pending"] = any(identity == packet_id and bool(value) for identity, value in items)
    if status is not None:
        state.update(free=int(status.free), maxlen=int(status.maxlen),
                     result=int(status.res), last_id=int(status.mesh_packet_id))
    return state


def wait_stock_queue(stock, timeout, packet_id=None):
    deadline = time.monotonic() + timeout
    latest = {}
    observed_packet = False
    while time.monotonic() < deadline:
        delivery.require_stock_connected(stock)
        latest = queue_snapshot(stock.iface, packet_id)
        check(latest["supported"], "stock SDK exposes no observable packet queue; TX completion uncertain")
        if packet_id is not None and latest.get("last_id") == packet_id:
            check(not latest.get("result"), "stock rejected the queued packet; no additional TX requested")
            observed_packet = True
        if packet_id is not None and latest["packet_pending"] is True:
            observed_packet = True
        if latest["pending_entries"] == 0 and (packet_id is None or latest["packet_pending"] is False) \
                and latest.get("free", 0) > 0 and (packet_id is None or observed_packet):
            return {**latest, "packet_id_depleted": packet_id is not None,
                    "packet_id_observed": observed_packet,
                    "meaning": "SDK/device admission only; RF completion unconfirmed without peer receipt"}
        time.sleep(0.02)
    raise delivery.Failure("stock packet queue did not drain before deadline; TX completion uncertain")


def wait_companion_text(companion, slot, sender, token, timeout):
    from meshtext_auto_radio2 import decode_channel_frame
    prefix = f"!{sender:08x}: ".encode()
    payload = token.encode("ascii")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = companion.request(b"\x0a", (7, 8, 10, 16, 17),
                                  timeout=min(5, max(0.1, deadline - time.monotonic())))
        decoded = decode_channel_frame(frame)
        if decoded and decoded["channel"] == slot and decoded["text"].startswith(prefix):
            observed = decoded["text"][len(prefix):]
            # A V3 event can truncate 160 wire bytes to its bounded app envelope.
            # Match a unique leading token, never claim the discarded tail arrived.
            if len(observed) >= 24 and observed == payload[:len(observed)]:
                return {"received": True, "application_bytes": len(observed),
                        "truncated": len(observed) < len(payload)}
        time.sleep(0.02)
    return {"received": False, "application_bytes": 0, "truncated": False}


def token_for(run_id, sf, direction, index, size):
    prefix = f"MS{run_id}:{sf}:{size}:{direction[0]}:{index:02d}:"
    check(len(prefix) <= size, "message size cannot contain the unique benchmark token")
    return prefix + "x" * (size - len(prefix))


def summary(rows, elapsed):
    received = [row for row in rows if row.get("received")]
    count = len(rows)
    delivered = sum(row["application_bytes"] for row in received)
    return {"attempted": count, "received": len(received), "lost": count - len(received),
            "loss_fraction": (count - len(received)) / count if count else None,
            "wire_payload_bytes_requested": sum(row["wire_bytes"] for row in rows),
            "application_bytes_delivered": delivered, "elapsed_seconds": round(elapsed, 6),
            "measured_application_goodput_bps": delivered * 8 / elapsed if elapsed else 0,
            "truncated_messages": sum(row.get("truncated", False) for row in received),
            "definition": "sequential host-observed goodput including receipt waits and pacing; not maximum PHY throughput"}


def benchmark(companion, stock, *, sf, size, direction, count, timeout, gap, slot, run_id, node_id, case, persist):
    rows = case["packets"]
    started = time.monotonic()
    for index in range(count):
        safety(companion)
        check_companion_rf(companion, sf)
        check_stock_rf(stock, sf)
        token = token_for(run_id, sf, direction, index, size)
        row = {"index": index, "wire_bytes": size, "received": False,
               "application_bytes": 0, "truncated": False, "tx_completion": "uncertain"}
        rows.append(row)
        before = time.monotonic()
        if direction == "companion-to-stock":
            original = delivery.snapshot(companion)
            companion.send_text(slot, token)
            packet = stock.wait_packet(node_id, lambda p: p.get("decoded", {}).get("payload") == token.encode(),
                                       before, timeout)
            completed = delivery.wait_idle(companion, timeout)
            row.update(received=packet is not None, application_bytes=size if packet else 0,
                       packet_id=packet.get("id") if packet else None,
                       tx_completion="DUT RF completion counter" if completed["traffic"]["tx"] > original["traffic"]["tx"] else "uncertain")
            check(row["tx_completion"] != "uncertain", "DUT TX completion uncertain; stopping benchmark")
        else:
            row["queue_before"] = wait_stock_queue(stock, timeout)
            packet = stock.send_text(token, 0)
            row["packet_id"] = int(packet.id)
            persist()
            row["queue_after"] = wait_stock_queue(stock, timeout, packet.id)
            row.update(wait_companion_text(companion, slot, stock.node, token, timeout))
            row["tx_completion"] = "peer receipt" if row["received"] else "uncertain: SDK queue drained, no RF receipt"
        row["elapsed_seconds"] = round(time.monotonic() - before, 6)
        persist()
        time.sleep(gap)
    case["measurement"] = summary(rows, time.monotonic() - started)


def run(args):
    key = secrets.token_bytes(args.key_bits // 8)
    data = {"schema": 1, "started_utc": datetime.now(timezone.utc).isoformat(), "result": "RUNNING",
            "run_id": secrets.token_hex(4), "key_bits": args.key_bits, "settings_restored": False,
            "phy": {"frequency_mhz": 909.5, "bandwidth_khz": 500, "cr": 5, "preamble_symbols": 16},
            "count_per_size_direction": args.count, "gap_seconds": args.gap, "cases": [],
            "limitations": ["Stock decoded events are not raw RF capture or independent TX-done evidence.",
                "Nominal LoRa bitrate and measured application goodput are different quantities.",
                "Inbound 160-byte wire messages may be truncated by the Companion app envelope.",
                "Missed stock sends without RF receipt remain TX-completion-uncertain, even after queue depletion."]}
    report = delivery.Report(args.report, data)
    companion = stock = None
    try:
        Companion, Stock = delivery.dependencies()
        delivery.require_idle_port(args.companion_port)
        delivery.require_idle_port(args.stock_port)
        companion = Companion(args.companion_port)
        data["companion"] = companion.start()
        data["initial_safety"] = safety(companion)
        check(args.channel_slot < companion.max_channels, "Companion channel slot out of range")
        for index in range(companion.max_channels):
            if index != args.channel_slot:
                check(not companion.channel_name(index).lstrip("#").startswith(("mt-", "r2-")),
                      "remove conflicting radio2/MT channels explicitly before this benchmark")
        stock = Stock(args.stock_port)
        check_stock_rf(stock)  # Refuse an initially preset/USA stock radio before any writes.
        data["stock_suppression"] = stock.configure_lab(key, "CLIENT_MUTE")
        node_id = companion.mt_node()
        check(node_id != stock.node, "radio sender IDs collide")
        for sf in args.sf:
            data["current_sf"] = sf
            safety(companion)
            delivery.wait_idle(companion, args.timeout)
            if companion.channel_name(args.channel_slot).lstrip("#").startswith("mt-"):
                companion.cli(f"delete mt.channel {args.channel_slot}")
            for command in ("set tempradio2 off", "set mt.region us", "set mt.hops 0", "set tx 2",
                            "set radio2.cross off", "set flood.retry.count 0", "set flood.retry.advert off",
                            f"set radio2 909.5,500,{sf},5,rxtx,32"):
                companion.cli(command)
            data.setdefault("stock_profiles", {})[str(sf)] = configure_stock_sf(stock, key, sf)
            companion.cli(f"set mt.channel {args.channel_slot} mt-labtest {key.hex()}")
            delivery.wait_active(companion, args.timeout)
            data.setdefault("companion_profiles", {})[str(sf)] = check_companion_rf(companion, sf)
            safety(companion)
            companion.send_text(args.channel_slot, f"MS{data['run_id']}:warmup:{sf}")
            time.sleep(0.4)
            delivery.wait_idle(companion, args.timeout)
            time.sleep(0.3)
            for size in SIZES:
                for direction in ("companion-to-stock", "stock-to-companion"):
                    case = report.case(f"SF{sf}-{size}-{direction}")
                    case.update(sf=sf, wire_bytes=size, direction=direction,
                                nominal_lora_kbps=nominal_kbps(sf), packets=[])
                    benchmark(companion, stock, sf=sf, size=size, direction=direction, count=args.count,
                              timeout=args.timeout, gap=args.gap, slot=args.channel_slot,
                              run_id=data["run_id"], node_id=node_id, case=case, persist=report.save)
                    case["safety_after"] = safety(companion)
                    case["result"] = "MEASURED"  # Packet loss is measured, not relabelled as a pass.
                    report.save()
        data["final_safety"] = safety(companion)
        data["result"] = "MEASURED"
    except (Exception, KeyboardInterrupt) as error:
        message = str(error)
        import base64
        for secret in (key.hex(), str(key), repr(key), base64.b64encode(key).decode()):
            message = message.replace(secret, "<redacted>")
        data["result"] = "FAIL"
        data["failure"] = {"type": type(error).__name__, "message": message[:1000]}
    finally:
        for device in (stock, companion):
            if device is not None:
                try:
                    device.close()
                except Exception as error:
                    data.setdefault("close_errors", []).append(type(error).__name__)
                    data["result"] = "FAIL"
        data["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report.save()
        report.stream.close()
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companion-port")
    parser.add_argument("--stock-port")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--channel-slot", type=int, default=1)
    parser.add_argument("--key-bits", type=int, choices=(128, 256), default=128)
    parser.add_argument("--sf", type=int, choices=SFS, nargs="+", default=list(SFS))
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=3)
    parser.add_argument("--gap", type=float, default=0.25)
    parser.add_argument("--allow-radio-changes", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    try:
        check(0 <= args.channel_slot <= 255 and 1 <= args.count <= 20, "invalid slot/count (count 1..20)")
        check(0 < args.timeout <= 30 and 0.25 <= args.gap <= 5, "timeout 0..30 s; pacing gap 0.25..5 s")
        check(len(set(args.sf)) == len(args.sf), "duplicate SF selection")
        if args.plan:
            print(json.dumps({"frequency_mhz": 909.5, "bandwidth_khz": 500, "cr": 5,
                              "spreading_factors": args.sf, "preamble_symbols": 16,
                              "sizes": SIZES, "count_per_direction_size": args.count,
                              "primary_required": "910.525,62.5,7,5; TX counter zero; immutable 909.500 MHz HIL guard",
                              "hops": 0, "retries": 0, "key_bits": args.key_bits,
                              "consent_required": "--allow-radio-changes", "settings_restored": False}, indent=2))
            return 0
        check(args.allow_radio_changes, "--allow-radio-changes is required before opening ports")
        check(args.companion_port and args.stock_port and args.report, "ports and a fresh report are required")
        check(os.path.realpath(args.companion_port) != os.path.realpath(args.stock_port), "ports must be distinct")
        check(not args.report.exists() and args.report.parent.is_dir(), "use a new report in an existing directory")
        result = run(args)
        print(json.dumps({"result": result["result"], "report": str(args.report),
                          "failure": result.get("failure"), "settings_restored": False}, indent=2))
        return 0 if result["result"] == "MEASURED" else 1
    except delivery.Failure as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
