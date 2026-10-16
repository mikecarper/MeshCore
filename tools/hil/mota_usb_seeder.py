#!/usr/bin/env python3
"""Serve one verified mOTA over a guarded Full Companion's existing USB source.

Enables a source which MAY TRANSMIT LoRa, with explicit consent. No flashing,
install, radio setters, network access, or new RF protocol. The source must
already be in live 909.5/500/SF5/CR5 primary TempRadio with an immutable HIL
909.5-MHz TX lock. Other nodes and install decisions remain operator-owned.
All container bytes and Zopfli1000 blocks are prepared in host RAM BEFORE USB
opens. Only the existing MS/ms COUNT/DESCRIBE/READ/DEFLATE operations are served.
Run --plan without hardware dependencies. Settings are not otherwise changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import time
from types import SimpleNamespace
import zlib

ROOT = Path(__file__).resolve().parents[2]
GUARD = "HIL TX lock=909.500 MHz; off-frequency TX blocked; primary RX-only when off-frequency"
RF = [909.5, 500, 5, 5]
MAX_CONTAINER = 4 * 1024 * 1024
LENGTHS = {1: 4, 2: 5, 3: 11, 9: 11}


class Failure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def xor(data):
    result = 0
    for byte in data:
        result ^= byte
    return result


class MemoryPath:
    """Read-only path facade: existing Catalog READ never rereads mutable disk."""
    def __init__(self, blob):
        self.blob = blob

    def stat(self):
        return SimpleNamespace(st_size=len(self.blob))

    def open(self, mode):
        require(mode == "rb", "memory catalog is read-only")
        return io.BytesIO(self.blob)


class CachedFile:
    def __init__(self, blob, descriptor, compressor):
        self.path, self.size, self.descriptor = MemoryPath(blob), len(blob), descriptor
        self.blocks = {}
        count, offset, length = struct.unpack_from("<III", descriptor, 22)
        block_size = 1 << descriptor[34]
        require(2 <= block_size <= 2048 and 0 < count <= 65536,
                "mOTA transport requires blocks2..2048 and at most65536 blocks")
        require(offset + length + 5 == len(blob), "invalid cached payload geometry")
        for index in range(count):
            raw = blob[offset + index * block_size:offset + min(length, (index + 1) * block_size)]
            require(raw, "empty cached payload block")
            encoded = compressor(raw, numiterations=1000)
            require(zlib.decompress(encoded, -15) == raw, "compressed block failed roundtrip")
            self.blocks[index] = encoded if 0 < len(encoded) < len(raw) else None

    def deflated_block(self, index):
        return self.blocks.get(index)


def prepare_catalog(path):
    # Imports remain behind --plan/consent; BLE itself is not needed.
    sys.path.insert(0, str(ROOT / "tools/ble_mota"))
    sys.path.insert(0, str(ROOT / "tools/mota"))
    import ble_mota_seeder as protocol
    import motalib
    require(path.is_file() and 0 < path.stat().st_size <= MAX_CONTAINER,
            "select one regular mOTA container no larger than4MiB")
    blob = path.read_bytes()
    parsed = motalib.parse_container(blob)
    problems = motalib.verify(parsed)
    require(not problems, "mOTA verification failed: " + "; ".join(problems))
    require(not parsed.manifest.is_bootloader, "this HIL source accepts application mOTA only")
    original = protocol.MotaFile.load(path)
    require(path.read_bytes() == blob, "mOTA changed during verification; refusing source")
    cached = CachedFile(blob, original.descriptor, protocol.raw_deflate_compress)
    return protocol.Catalog([cached], False), {
        "container_bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
        "manifest_id": original.descriptor[:4].hex(),
        "target_id": f"{parsed.manifest.target_id:08x}",
        "blocks": len(cached.blocks), "block_bytes": 1 << original.descriptor[34],
        "deflated_blocks": sum(value is not None for value in cached.blocks.values()),
        "cached_deflate_bytes": sum(len(value) for value in cached.blocks.values() if value),
        "zopfli_iterations": 1000, "immutable_ram_snapshot": True,
        "verification": "container hashes/Merkle/signature if present; delta result requires receiver's base",
    }


def source_safety(node, duration):
    guard = node.cli("get lab.tx")
    require(guard.strip().removeprefix("> ") == GUARD, "compiled909.5MHz HIL TX lock not confirmed")
    live = node.cli("get tempradio")
    match = re.fullmatch(r"\s*(?:>\s*)?TempRadio active: ([\d.]+),([\d.]+),(\d+),(\d+) (\d+)s left(?:[ ,;].*)?", live)
    require(match is not None, "source must already have an active primary TempRadio; pending/inactive is unsafe")
    require(abs(float(match[1]) - RF[0]) <= 0.00005
            and (float(match[2]), int(match[3]), int(match[4])) == tuple(RF[1:]),
            "live primary TempRadio must be909.5/500/SF5/CR5")
    lease = int(match[5])
    require(lease >= duration + 30, "source TempRadio lease must cover duration plus30seconds")
    return {"guard": guard, "live_tempradio": live, "lease_seconds": lease,
            "saved_primary": node.cli("get radio"), "status": node.cli("get radio2.status")}


class RequestParser:
    """Bounded resync for the four fixed-size existing source requests only."""
    def __init__(self):
        self.buffer = bytearray()
        self.discarded = self.bad_checksum = self.unsupported = 0
        self.text_tail = b""

    def noise(self, value):
        self.discarded += len(value)
        self.text_tail = (self.text_tail + value)[-512:]

    def feed(self, data):
        require(len(data) <= 4096, "serial read exceeded bounded chunk")
        self.buffer.extend(data)
        frames = []
        while self.buffer:
            start = self.buffer.find(b"MS")
            if start < 0:
                keep = 1 if self.buffer[-1:] == b"M" else 0
                self.noise(self.buffer[:-keep] if keep else self.buffer)
                self.buffer[:] = self.buffer[-1:] if keep else b""
                break
            if start:
                self.noise(self.buffer[:start])
                del self.buffer[:start]
            if len(self.buffer) < 3:
                break
            op = self.buffer[2]
            if op not in LENGTHS:
                self.unsupported += 1
                raise Failure("unsupported source op; storage/write requests are not accepted")
            length = LENGTHS[op]
            if len(self.buffer) < length:
                break
            frame = bytes(self.buffer[:length])
            if xor(frame[2:]) != 0:
                self.bad_checksum += 1
                self.discarded += 1
                del self.buffer[0]
                continue
            del self.buffer[:length]
            frames.append(frame)
        require(self.discarded <= 65536 and self.bad_checksum <= 100,
                "bounded serial resynchronization budget exceeded")
        return frames


def write_exact(port, data):
    require(port.write(data) == len(data), "short USB source write")
    port.flush()


def pump(port, catalog, seconds, stats, *, attaching=False, parser=None, persist=lambda: None):
    parser = RequestParser() if parser is None else parser
    started, deadline = time.monotonic(), time.monotonic() + seconds
    saved_at = started
    while time.monotonic() < deadline:
        chunk = port.read(min(port.in_waiting or 1, 4096))
        if not chunk:
            continue
        stats["usb_received_bytes"] += len(chunk)
        for frame in parser.feed(chunk):
            response = catalog.handle_request(frame)
            require(response is not None and len(response) <= 197,
                    "catalog returned an invalid source response")
            write_exact(port, response)
            key = str(frame[2])
            stats["requests"][key] = stats["requests"].get(key, 0) + 1
            stats["usb_sent_bytes"] += len(response)
            stats["error_responses"] += int(response[3] != 0)
        if attaching:
            tail = parser.text_tail
            require(b"ERR" not in tail and b"Error" not in tail, "source rejected USB folder attach")
            if b"OK folder attached" in tail or b"OK folder refreshed" in tail:
                require(stats["requests"].get("1", 0) > 0, "attach ACK did not include a serviced COUNT")
                stats["attach_seconds"] = time.monotonic() - started
                return
        if time.monotonic() - saved_at >= 1:
            stats["discarded_serial_bytes"] = parser.discarded
            stats["bad_request_checksums"] = parser.bad_checksum
            persist()
            saved_at = time.monotonic()
    stats["discarded_serial_bytes"] = parser.discarded
    stats["bad_request_checksums"] = parser.bad_checksum
    require(not attaching, "USB folder attach timed out")


def serve(node, catalog, duration, stats, persist=lambda: None):
    # Explicitly switch the already-initialized framed USB transport to ASCII,
    # without closing, resetting, or changing DTR. Do not wait for folder ACK:
    # its synchronous COUNT must be answered before the node can print it.
    port = node.port
    write_exact(port, b"+++MESHCORE-TERM-START\r")
    time.sleep(0.3)
    # Bounded ASCII drain, never retained/logged (may contain unrelated logs).
    discarded, deadline = 0, time.monotonic() + 1
    while time.monotonic() < deadline:
        chunk = port.read(min(port.in_waiting or 1, 4096))
        if not chunk:
            break
        discarded += len(chunk)
        require(discarded <= 65536, "terminal transition drain exceeded bound")
    stats["discarded_terminal_bytes"] = discarded
    requests = RequestParser()
    try:
        write_exact(port, b"ota folder on\r\n")
        pump(port, catalog, 12, stats, attaching=True, parser=requests, persist=persist)
        persist()
        print("USB mOTA folder attached; serving existing protocol", flush=True)
        pump(port, catalog, duration, stats, parser=requests, persist=persist)
    finally:
        # No outstanding response remains in our synchronous write pump. Even
        # after an error, this is only the standard source detach, not install.
        write_exact(port, b"ota folder off\r\n")
        stats["detach_sent"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-port")
    parser.add_argument("--source-serial")
    parser.add_argument("--mota", type=Path)
    parser.add_argument("--duration", type=float, default=300, help="bounded serving seconds1..3600")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--allow-source-transmit", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(math.isfinite(args.duration) and 1 <= args.duration <= 3600, "duration must be1..3600seconds")
        if args.plan:
            print(json.dumps({"rf": RF, "required_guard": GUARD, "existing_ops": LENGTHS,
                "duration_seconds": args.duration, "zopfli_iterations": 1000,
                "prepares_all_blocks_before_usb": True, "install_commands": False}, indent=2))
            return 0
        require(args.allow_source_transmit, "--allow-source-transmit is required before preparing/opening")
        require(all((args.source_port, args.source_serial, args.mota, args.report)),
                "source port, exact USB serial, mOTA and report are required")
        require(not args.report.exists() and args.report.parent.is_dir(), "select a fresh report in an existing directory")
    except Failure as error:
        parser.error(str(error))
    report = {"schema": 1, "result": "RUNNING", "rf": RF,
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "no_install_command": True, "duration_seconds": args.duration,
              "stats": {"requests": {}, "usb_received_bytes": 0, "usb_sent_bytes": 0,
                        "error_responses": 0, "discarded_serial_bytes": 0,
                        "bad_request_checksums": 0, "detach_sent": False}}
    node = None
    with args.report.open("x", encoding="utf-8") as stream:
        try:
            os.chmod(args.report, 0o600)
        except OSError:
            pass

        def save():
            stream.seek(0)
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.truncate()
            stream.flush()

        save()
        try:
            print("Verifying and preparing all Zopfli1000 blocks before opening USB", flush=True)
            catalog, report["catalog"] = prepare_catalog(args.mota)
            save()
            from companion_raw_radio2_sweep import safe_identity
            from meshtext_delivery import dependencies, require_idle_port
            report["usb"] = safe_identity(args.source_port, args.source_serial)
            require_idle_port(args.source_port)
            Companion, _ = dependencies()
            node = Companion(args.source_port)
            safe_identity(args.source_port, args.source_serial)
            report["companion"] = node.start()
            report["safety"] = source_safety(node, args.duration + 12)
            save()
            serve(node, catalog, args.duration, report["stats"], persist=save)
            report["result"] = "SERVED"
        except (Exception, KeyboardInterrupt) as error:
            report["result"] = "FAIL"
            # Do not retain arbitrary device text or packet bytes in errors.
            report["failure"] = {"type": type(error).__name__, "message": str(error)[:500]}
        finally:
            if node is not None:
                node.close()
            report["finished_utc"] = datetime.now(timezone.utc).isoformat()
            save()
    print(json.dumps({"result": report["result"], "report": str(args.report), "stats": report["stats"]}))
    return 0 if report["result"] == "SERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
