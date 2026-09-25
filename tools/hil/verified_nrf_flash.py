#!/usr/bin/env python3
"""Check a Pi-connected nRF52's *physical* serial before selecting a flash image.

USB product names are not board identities: several Heltec nRF52 boards expose
the same ``HT-n5262`` product string.  This gate requires a reviewed inventory
entry for the stable chip serial and checks the selected artifact against it.
Run it before either serial DFU or a UF2 copy.
"""

import argparse
import json
import os
from pathlib import Path


def verify(inventory, serial_number, board, artifact_name):
    devices = inventory.get("devices", {})
    serial_key = serial_number.upper()
    device = next((entry for key, entry in devices.items()
                   if key.upper() == serial_key), None)
    if device is None:
        raise ValueError(f"unrecorded USB serial {serial_number}; identify the physical board first")
    actual_board = device["board"]
    if board != actual_board:
        raise ValueError(f"serial {serial_number} is {actual_board}, not {board}")
    artifact = Path(artifact_name).name.lower()
    if artifact.startswith("update-"):
        artifact = artifact[len("update-"):]
    kind = "bootloader" if "_bootloader" in artifact else "firmware"
    allowed = device.get(f"{kind}_prefixes", [])
    if not any(artifact.startswith(prefix.lower() + "_") or
               artifact.startswith(prefix.lower() + "-") for prefix in allowed):
        raise ValueError(f"{artifact_name} is not a {actual_board} {kind} artifact; expected one of {allowed}")
    return actual_board


def verify_port(port_path, serial_number):
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise ValueError("pyserial is required to verify the connected USB port") from exc
    expected = os.path.realpath(port_path)
    matches = [port for port in list_ports.comports()
               if os.path.realpath(port.device) == expected]
    if len(matches) != 1:
        raise ValueError(f"USB port {port_path} is absent or ambiguous")
    observed = (matches[0].serial_number or "").upper()
    if observed != serial_number.upper():
        raise ValueError(f"USB port {port_path} has serial {observed!r}, expected {serial_number}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--serial", required=True, help="Stable USB chip serial")
    parser.add_argument("--board", required=True, help="Reviewed physical board name")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--port", required=True, help="Current application or DFU serial port")
    args = parser.parse_args(argv)
    try:
        inventory = json.loads(args.inventory.read_text())
        verify(inventory, args.serial, args.board, args.artifact.name)
        verify_port(args.port, args.serial)
        if not args.artifact.is_file():
            raise ValueError(f"artifact does not exist: {args.artifact}")
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"REFUSED: {exc}\n")
    print(f"VERIFIED: {args.serial} = {args.board}; {args.artifact.name}; {args.port}")


if __name__ == "__main__":
    main()
