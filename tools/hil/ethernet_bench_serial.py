"""Identity-recorded USB CLI for the explicitly erasable Ethernet RF bench.

No reset/erase/flash is performed. WiFi credentials are read only from a local
JSON file and never included in the printed command/response log.
"""
import argparse
import json
import os
from pathlib import Path
import time

import serial
from serial.tools import list_ports

from serial_session import (SerialCommandTimeout, assert_port_idle,
                            open_configured_session, read_command_reply)


def read_quiet(port, timeout=3):
    until = time.monotonic() + timeout
    # Wait the full response deadline for the first byte; silence at attach is
    # not proof that a cooperative USB console has finished opening its session.
    quiet = until
    data = bytearray()
    while time.monotonic() < min(until, quiet):
        chunk = port.read(min(port.in_waiting or 1, 4096))
        if chunk:
            data.extend(chunk)
            quiet = time.monotonic() + 0.35
    return data.decode("utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--command", action="append", default=[])
    parser.add_argument("--wifi-json", type=Path)
    parser.add_argument("--wait", type=float, default=10)
    args = parser.parse_args()
    matches = [p for p in list_ports.comports()
               if os.path.realpath(p.device) == os.path.realpath(args.port)]
    if len(matches) != 1:
        raise SystemExit("Selected USB identity is absent or ambiguous")
    info = matches[0]
    secrets = []
    commands = list(args.command)
    if args.wifi_json:
        credentials = json.loads(args.wifi_json.read_text())
        ssid, password = credentials["ssid"], credentials["password"]
        if not isinstance(ssid, str) or not isinstance(password, str) or any(
                c in ssid + password for c in "\r\n\0"):
            raise SystemExit("Invalid credential file")
        secrets = [password] if password else []
        commands.extend(["set wifi.ssid " + ssid, "set wifi.pwd " + password, "set wifi on"])
    if args.wait <= 0 or any(not command or any(value in command for value in "\r\n\0") for command in commands):
        raise SystemExit("Use single-line nonempty commands and a positive --wait")

    def redact(text):
        for secret in secrets:
            text = text.replace(secret, "[redacted]")
        return text

    port = serial.Serial(port=None, baudrate=115200, timeout=0.04, exclusive=True)
    port.port = info.device
    port.rts = False
    port.dtr = info.vid in (0x303A, 0x2886, 0x239A)
    output = {"port": info.device, "usb_serial": info.serial_number,
              "vid": info.vid, "pid": info.pid, "commands": []}
    try:
        assert_port_idle(info.device)
        open_configured_session(port)
        read_quiet(port, 0.5)
        port.write(b"+++MESHCORE-TERM-START\n")
        port.flush()
        output["hello"] = redact(read_quiet(port))
        for command in commands:
            port.write((command + "\n").encode())
            port.flush()
            try:
                reply = read_command_reply(port, command, args.wait)
            except SerialCommandTimeout as error:
                output["commands"].append({"command": redact(command),
                                           "reply": redact(error.partial_reply), "completed": False})
                output["error"] = str(error)
                break  # Never misattribute this pending reply to another command.
            output["commands"].append({"command": redact(command), "reply": redact(reply), "completed": True})
    finally:
        if port.is_open:
            try:
                port.write(b"+++MESHCORE-TERM-STOP\n")
                port.flush()
            except Exception:
                pass
        try:
            port.close()
        except Exception:
            pass
    print(json.dumps(output), flush=True)
    return 1 if "error" in output else 0


if __name__ == "__main__":
    raise SystemExit(main())
