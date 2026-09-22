"""Receive fixed RAK4631 packets after T096 SX1262 fast profile hops.

Run only on the MercerMesh fixture with the two matching HIL applications.
The RAK source transmits 16-byte packets only when this script requests them.
"""

import argparse
import json
import time

import serial
from serial.tools import list_ports


def fixture_port(serial_number, topology):
    matches = [p for p in list_ports.comports()
               if p.serial_number == serial_number and p.location
               and p.location.startswith(topology)]
    if len(matches) != 1 or matches[0].vid != 0x239A or matches[0].pid != 0x8029:
        raise RuntimeError(f"Missing unique HIL application: {serial_number}")
    return matches[0].device


def request(port, command):
    port.write((command + "\n").encode("ascii"))
    raw = port.readline().decode("ascii", errors="replace").strip()
    if not raw:
        raise RuntimeError(f"No reply to {command!r}")
    result = json.loads(raw)
    if "error" in result:
        raise RuntimeError(f"{command}: {result}")
    return result


def run(start, count):
    rx_path = fixture_port("651F8E496197F882", "1-1.2.3")
    tx_path = fixture_port("9AB3B64C641BA927", "1-1.3.1")
    with serial.Serial(rx_path, 115200, timeout=8) as rx, \
         serial.Serial(tx_path, 115200, timeout=8) as tx:
        rx.reset_input_buffer()
        tx.reset_input_buffer()
        rx_info = request(rx, "info")
        tx_info = request(tx, "info")
        if rx_info.get("bench") != "t096-sx1262-fs-packet-v1":
            raise RuntimeError(f"Unexpected RX application: {rx_info}")
        if tx_info.get("bench") != "production-profile-switch-v8" or not tx_info.get("fixed_tx"):
            raise RuntimeError(f"Unexpected TX application: {tx_info}")
        if not tx_info.get("prepared"):
            prepared = request(tx, "txprepare 0")
            if not prepared.get("prepared"):
                raise RuntimeError(f"TX preparation failed: {prepared}")
        elif tx_info.get("channel") != 0:
            raise RuntimeError(f"Wrong fixed TX channel: {tx_info}")

        results = []
        seq = start
        for optimized in (False, True):
            assert request(rx, f"fs {int(optimized)}").get("fs") is optimized
            assert request(rx, f"packetcache {int(optimized)}").get("packet_cache") is optimized
            if not request(rx, "pilot").get("pilot"):
                raise RuntimeError("Receiver pilot setup failed")
            delivered = 0
            for _ in range(count):
                for profile in (1, 0):
                    hop = request(rx, f"pilothop {profile}")
                    if not hop.get("ok") or hop.get("fast") != 1:
                        raise RuntimeError(f"Bad fast hop: {hop}")
                    if hop.get("fs") != int(optimized) or hop.get("packet_skips") != int(optimized):
                        raise RuntimeError(f"Wrong experimental path: {hop}")
                sent = request(tx, f"scantx 0 {seq} 16 32 10 125")
                if sent.get("sent") != seq or sent.get("rc") != 0 or sent.get("failed"):
                    raise RuntimeError(f"TX failed: {sent}")
                if sent.get("rf_commands") or sent.get("modulation_commands"):
                    raise RuntimeError(f"Fixed TX was retuned: {sent}")
                for _ in range(5):
                    packet = request(rx, f"poll {seq}")
                    if packet.get("valid"):
                        delivered += 1
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"Packet {seq} not received after hop: {packet}")
                seq += 1
            results.append({"optimized": optimized, "delivered": delivered, "sent": count,
                            "fast_hops": 2 * count})
        print(json.dumps({"board": "Heltec T096 SX1262", "spi_hz": rx_info["spi_hz"],
                          "source": "RAK4631", "modes": results}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=2)
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.count <= 50 or args.start < 1:
        parser.error("use 1-50 packets per mode and a positive start sequence")
    run(args.start, args.count)
