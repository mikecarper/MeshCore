#!/usr/bin/env python3
"""Verify primary MeshCore chat while Ethernet RF traffic uses radio2.

TRANSMITS LoRa and saves channel-0 TX policy=radio on the two explicit lab
Companions. Does not flash, reset, edit WiFi, read channel secrets, or configure
RF. Both primary and Ethernet tuples MUST already be 909.5/500/SF5/CR5; the
script refuses all test transmission unless both devices report those tuples.
Only an opposite radio's structured CHANNEL MSG reception proves chat delivery;
a command echo or the sender's local Sent response never passes the check.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from ethernet_kiss_probe import KissEndpoint, run_probe
from serial_session import assert_port_idle, open_configured_session, read_command_reply


def assert_lab_radio(reply):
    match = re.search(r"(?:^|\n)\s*(?:->\s*)?>\s*(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)(?=[,;\s]|$)", reply)
    if not match or tuple(float(value) for value in match.groups()) != (909.5, 500, 5, 5):
        raise RuntimeError("Refusing TX: both primary and Ethernet must report 909.5,500,5,5")


def received_channel_marker(text, marker):
    messages = re.findall(r"CHANNEL MSG -> [^\r\n]*\((?:FLOOD|DIRECT)\)\r?\n[ \t]*([^\r\n]*)", text)
    return any(re.search(r"(?<![A-Za-z0-9_-])" + re.escape(marker) + r"(?![A-Za-z0-9_-])", item)
               for item in messages)


class SerialNode:
    def __init__(self, path):
        import serial
        from serial.tools import list_ports
        matches = [item for item in list_ports.comports()
                   if os.path.realpath(item.device) == os.path.realpath(path)]
        if len(matches) != 1:
            raise RuntimeError("Selected USB identity is absent or ambiguous: " + path)
        info = matches[0]
        self.identity = dict(port=info.device, usb_serial=info.serial_number, vid=info.vid,
                             pid=info.pid, location=info.location)
        self.commands = []
        self.port = serial.Serial(port=None, baudrate=115200, timeout=0.04, exclusive=True)
        self.port.port = info.device
        self.port.rts = False
        self.port.dtr = info.vid in (0x303A, 0x2886, 0x239A)
        assert_port_idle(info.device)
        open_configured_session(self.port)
        self.read(0.3)
        self.port.write(b"+++MESHCORE-TERM-START\n")
        self.port.flush()
        self.hello = self.read(3)

    def read(self, timeout=1, quiet=0.2):
        deadline = time.monotonic() + timeout
        quiet_until = deadline
        data = bytearray()
        while time.monotonic() < min(deadline, quiet_until):
            chunk = self.port.read(min(self.port.in_waiting or 1, 4096))
            if chunk:
                data.extend(chunk)
                quiet_until = time.monotonic() + quiet
        return data.decode("utf-8", errors="replace")

    def command(self, text, timeout=10):
        if any(value in text for value in "\r\n\0"):
            raise ValueError("single-line commands only")
        self.port.write((text + "\n").encode("ascii"))
        self.port.flush()
        reply = read_command_reply(self.port, text, timeout)
        self.commands.append(dict(command=text, reply=reply))
        return reply

    def close(self):
        try:
            self.port.write(b"+++MESHCORE-TERM-STOP\n")
            self.port.flush()
        finally:
            self.port.close()


def snapshot(node):
    return {command: node.command(command) for command in
            ("get name", "get radio", "get ethernet.radio", "get ethernet", "get tx.channel 0", "channels")}


def run_primary(a, b, a_endpoint, b_endpoint, *, timeout=10, factory=KissEndpoint):
    report = {"usb": {"a": a.identity, "b": b.identity}, "chat": [], "passed": False}
    nodes = {"a": a, "b": b}
    # All four RF assertions happen before opening KISS or queueing any chat.
    report["before"] = {name: snapshot(node) for name, node in nodes.items()}
    for state in report["before"].values():
        assert_lab_radio(state["get radio"])
        assert_lab_radio(state["get ethernet.radio"])
        if "ethernet=on; active (RX time-shared)" not in state["get ethernet"]:
            raise RuntimeError("Ethernet radio2 must already be enabled and active")
    for node in nodes.values():
        reply = node.command("set tx.channel 0 radio")
        if "Error" in reply or "ERROR" in reply or "OK" not in reply:
            raise RuntimeError("Could not save channel-0 primary-only policy: " + reply)
        if "radio; active=radio" not in node.command("get tx.channel 0"):
            raise RuntimeError("Channel 0 primary-only routing was not confirmed")
        if "channels on" not in node.command("show channels on"):
            raise RuntimeError("Could not enable incoming channel display")
    endpoints = []
    result = {}
    worker = None
    try:
        endpoints = [factory(a_endpoint)]
        endpoints.append(factory(b_endpoint))
        for node in nodes.values():
            reply = node.command("get ethernet")
            if "TCP 5003 connected" not in reply:
                raise RuntimeError("Both Ethernet KISS sessions must be connected before chat")

        def ethernet_worker():
            try:
                result["ethernet"] = run_probe(*endpoints, count=1, timeout=3, interval=0.1)
            except Exception as error:
                result["ethernet_error"] = str(error)

        worker = threading.Thread(target=ethernet_worker, daemon=True)
        worker.start()
        token = secrets.token_hex(8)
        for source, destination in (("a", "b"), ("b", "a")):
            marker = f"ETH-PRIMARY-{token}-{source}"
            # Only the destination's serial output is consulted for reception.
            destination_node = nodes[destination]
            destination_node.read(0.1)
            concurrent = worker.is_alive()
            sent = time.monotonic()
            sender_reply = nodes[source].command("channel 0 " + marker)
            received_text = ""
            deadline = sent + timeout
            while time.monotonic() < deadline and not received_channel_marker(received_text, marker):
                received_text += destination_node.read(min(0.4, deadline - time.monotonic()))
            report["chat"].append(dict(source=source, destination=destination, marker=marker,
                                        ethernet_worker_active_at_send=concurrent,
                                        sender_reply=sender_reply, receiver_output=received_text,
                                        received=received_channel_marker(received_text, marker),
                                        elapsed_seconds=round(time.monotonic() - sent, 3)))
        worker.join(timeout=60)
        if worker.is_alive():
            raise RuntimeError("Ethernet worker did not finish within bounded test window")
        report.update(result)
        report["after"] = {name: snapshot(node) for name, node in nodes.items()}
        for name in nodes:
            assert_lab_radio(report["after"][name]["get radio"])
            assert_lab_radio(report["after"][name]["get ethernet.radio"])
        report["passed"] = bool(result.get("ethernet", {}).get("passed")) and all(
            item["received"] and item["ethernet_worker_active_at_send"] for item in report["chat"])
        return report
    finally:
        for endpoint in endpoints:
            endpoint.close()
        if worker and worker.is_alive():
            worker.join(timeout=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-port", required=True)
    parser.add_argument("--b-port", required=True)
    parser.add_argument("--a-endpoint", required=True)
    parser.add_argument("--b-endpoint", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args(argv)
    if args.timeout <= 0 or os.path.realpath(args.a_port) == os.path.realpath(args.b_port):
        parser.error("positive timeout and two distinct serial ports are required")
    a = b = None
    try:
        a = SerialNode(args.a_port)
        b = SerialNode(args.b_port)
        result = run_primary(a, b, args.a_endpoint, args.b_endpoint, timeout=args.timeout)
    except Exception as error:
        result = {"passed": False, "error": str(error)}
    finally:
        for node in (b, a):
            if node:
                node.close()
    result["commands"] = {name: node.commands for name, node in (("a", a), ("b", b)) if node}
    output = json.dumps(result, indent=2)
    args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
