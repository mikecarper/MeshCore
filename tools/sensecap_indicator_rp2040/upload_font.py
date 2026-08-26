#!/usr/bin/env python3
"""Upload a checked font asset to the Indicator RP2040 SD service."""

from __future__ import annotations

import argparse
import sys
import time
import zlib
from pathlib import Path

import serial


MAX_FONT_BYTES = 1536 * 1024


def read_line(port: serial.Serial, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    line = bytearray()
    while time.monotonic() < deadline:
        value = port.read(1)
        if not value:
            continue
        if value == b"\n":
            return line.rstrip(b"\r").decode("ascii", errors="replace")
        line += value
    raise TimeoutError("font service did not respond")


def upload(port_name: str, font_path: Path, timeout: float) -> None:
    data = font_path.read_bytes()
    if not 64 <= len(data) <= MAX_FONT_BYTES:
        raise ValueError(f"font size must be between 64 and {MAX_FONT_BYTES} bytes")
    crc = zlib.crc32(data) & 0xFFFFFFFF

    with serial.Serial(
        port_name,
        115200,
        timeout=0.2,
        write_timeout=timeout,
    ) as port:
        time.sleep(0.25)
        # Terminate any partial command left by an interrupted terminal or
        # uploader before beginning the transactional transfer.
        port.write(b"\n")
        port.flush()
        time.sleep(0.05)
        port.reset_input_buffer()
        port.write(f"MCFONT PUT {len(data)} {crc:08x}\n".encode("ascii"))
        port.flush()
        response = read_line(port, timeout)
        if response != "READY":
            raise RuntimeError(f"font service refused upload: {response}")

        sent = 0
        while sent < len(data):
            sent += port.write(data[sent : sent + 4096])
        port.flush()

        response = read_line(port, timeout)
        if response != "OK":
            raise RuntimeError(f"font service failed to install font: {response}")

        port.write(b"MCFONT INFO\n")
        port.flush()
        expected = f"MCFONT 1 {len(data)} {crc:08x}"
        response = read_line(port, timeout)
        if response != expected:
            raise RuntimeError(
                f"font verification failed: expected {expected!r}, got {response!r}"
            )

    print(f"Installed {font_path} ({len(data)} bytes, CRC32 {crc:08x})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, help="RP2040 USB serial port")
    parser.add_argument(
        "--font",
        type=Path,
        default=Path("variants/sensecap_indicator-espnow/sd/ui-font.vlw"),
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    try:
        upload(args.port, args.font, args.timeout)
    except (OSError, ValueError, RuntimeError, TimeoutError, serial.SerialException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
