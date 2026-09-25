#!/usr/bin/env python3
"""Check a real RAK4631/RAK3401 OTA store and its matching bootloader over USB.

Example: python3 tools/hil/verify_rak_ota_storage.py --port /dev/serial/by-id/...
         --serial 9AB3B64C641BA927 --expect w25q16
"""

import argparse
import json
import re
import time

from verified_nrf_flash import verify_port


def command(port, text):
    port.reset_input_buffer()
    port.write((text + "\r").encode("ascii"))
    end = time.monotonic() + 5
    data = bytearray()
    while time.monotonic() < end:
        data.extend(port.read(4096))
        decoded = data.decode("utf-8", "replace")
        if re.search(r"(?:^|\r?\n)\s*->\s*[^\r\n]+", decoded):
            return decoded
    raise RuntimeError(f"no reply to {text!r}: {data.decode('utf-8', 'replace')!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--expect", choices=("internal", "w25q16"), required=True)
    args = parser.parse_args()
    verify_port(args.port, args.serial)

    import serial
    with serial.Serial(args.port, 115200, timeout=0.1, write_timeout=2) as port:
        time.sleep(0.3)
        replies = {cmd: command(port, cmd) for cmd in
                   ("ota storage", "ota self", "ota status")}

    if args.expect == "w25q16":
        required = {
            "ota storage": ("QSPI W25Q16", "jedec=EF4015", "size=2048K"),
            "ota self": ("QSPI store:2048K", "bootloader: QSPI apply OK"),
            "ota status": ("bl:QSPI",),
        }
    else:
        required = {
            "ota storage": ("OTA storage: no external NOR", "internal capacity=256K"),
            "ota self": ("internal store:256K", "bootloader: delta apply OK"),
            "ota status": ("bl:internal",),
        }
    failures = [f"{cmd}: missing {needle!r}" for cmd, needles in required.items()
                for needle in needles if needle not in replies[cmd]]
    print(json.dumps({"serial": args.serial, "expected": args.expect,
                      "passed": not failures, "replies": replies,
                      "failures": failures}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
