"""Bounded Companion USB transport for local hardware validation tools.

Importing this module never imports pyserial or opens a port. The caller must
verify the selected device and obtain consent for any radio changes separately.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

from esp32_companion_serial_stress import (
    DeviceFrameReader, FrameTimeout, TransportCounters, encode_host_frame,
    make_app_start_spec, validate_app_start_response,
)
from serial_session import open_configured_session


class Failure(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise Failure(message)


def cli_command_label(command):
    words = command.split()
    # Identify the setting, never its value (especially mt.channel keys).
    return " ".join(words[:2] if words and words[0] in ("get", "set", "send", "delete") else words[:1])


def safe_cli_reply(command, reply):
    # A diagnostic implementation may echo secrets even when firmware does not.
    words = command.split()
    if len(words) >= 5 and words[:2] == ["set", "mt.channel"]:
        reply = reply.replace(words[-1], "<redacted>")
    return re.sub(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,64}(?![0-9a-fA-F])", "<redacted>", reply)[:500]


def require_idle_port(path):
    # BusyBox fuser lacks '--'; the resolved absolute path is a separate argument.
    inspector = shutil.which("fuser") if sys.platform.startswith("linux") else None
    if inspector:
        result = subprocess.run([inspector, os.path.realpath(path)], capture_output=True, timeout=3)
        check(result.returncode == 1, "selected serial port is busy or ownership inspection failed")


def enter_binary_mode(port):
    """Release the ASCII terminal without resetting the connected board."""
    port.rts = False
    port.dtr = True
    token = b"+++MESHCORE-TERM-STOP\r"
    check(port.write(token) == len(token), "incomplete USB Binary-mode negotiation write")
    port.flush()
    time.sleep(0.3)
    deadline = time.monotonic() + 1
    discarded = 0
    tail = b""
    while time.monotonic() < deadline:
        chunk = port.read(min(port.in_waiting or 1, 4096))
        if not chunk:
            break
        discarded += len(chunk)
        check(discarded <= 65536, "USB terminal output exceeded bounded negotiation drain")
        tail = (tail + chunk)[-512:]
    check(b"set usb.logging off before Binary mode" not in tail,
          "USB logging owns this port; set usb.logging off in the terminal before this test")
    return {"explicit_stop_token": True, "discarded_terminal_bytes": discarded, "dtr": True}


class Companion:
    """Existing local Companion framing and CLI behavior, with safe USB startup."""

    def __init__(self, port):
        import serial
        self.port = serial.Serial(port=None, baudrate=115200, timeout=0.05,
                                  write_timeout=2, exclusive=True)
        self.port.port, self.port.dtr, self.port.rts = port, False, False
        open_configured_session(self.port)
        self.reader = DeviceFrameReader(TransportCounters())
        self.max_channels = 0

    def close(self):
        self.port.close()

    def request(self, payload, expected, timeout=5):
        encoded = encode_host_frame(payload)
        check(self.port.write(encoded) == len(encoded), "incomplete Companion write")
        self.port.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                frame = self.reader.read_frame(self.port, min(0.5, deadline - time.monotonic()))
            except FrameTimeout:
                continue
            if frame[0] >= 0x80:
                continue
            if frame[0] == 1:
                raise Failure("Companion command rejected, error " + str(frame[1:2].hex()))
            # A late startup reply can cross the next startup query. Ignore
            # only that opposite reply type, without resending or extending
            # the original deadline; every other response remains strict.
            if ((payload[0] == 1 and 5 in expected and 13 not in expected and frame[0] == 13)
                    or (payload[0] == 22 and 13 in expected and 5 not in expected and frame[0] == 5)):
                continue
            check(frame[0] in expected, "unexpected Companion response " + str(frame[0]))
            return frame
        raise Failure("Companion response timeout for command " + str(payload[0]))

    def start(self):
        negotiation = enter_binary_mode(self.port)
        # ASCII acknowledgements cannot become stale framed responses.
        self.reader = DeviceFrameReader(TransportCounters())
        info = self.request(bytes((22, 14)), (13,))
        check(len(info) >= 80, "truncated Companion device info")
        self.max_channels = info[3]
        frame = self.request(make_app_start_spec("Meshtext-Auto-HIL").payload, (5,))
        return {"protocol": info[1], "max_channels": self.max_channels,
                "model": info[20:60].split(b"\0")[0].decode("utf-8", "replace"),
                "firmware": info[60:80].split(b"\0")[0].decode("utf-8", "replace"),
                "primary": validate_app_start_response(frame),
                "usb_binary_negotiation": negotiation}

    def cli(self, command):
        label = cli_command_label(command)
        try:
            frame = self.request(b"\x42" + command.encode("utf-8"), (0x1d,))
            reply = frame[1:].decode("utf-8", "strict").strip()
        except Exception as error:
            raise Failure("Companion CLI " + label + " failed: "
                          + safe_cli_reply(command, str(error))) from error
        check(not re.match(r"(?:Error|ERR|Err)\b", reply),
              "Companion CLI " + label + " rejected: " + safe_cli_reply(command, reply))
        return reply

    def channel_name(self, slot):
        frame = self.request(bytes((31, slot)), (18,))
        check(len(frame) >= 50, "truncated channel info")
        return frame[2:34].split(b"\0")[0].decode("utf-8", "strict")
