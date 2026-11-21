#!/usr/bin/env python3
"""Check a privately generated locator provisioning HEX before programming the RAK Tag."""
import argparse
import hashlib


def check_record(network, image):
    base = {"findhub": 0xC8000, "sidewalk": 0xD1000}[network]
    end = image.maxaddr()
    if image.minaddr() != base or end is None or end >= base + 0x1000:
        raise ValueError(f"Expected a single RAK provisioning page beginning at {base:#x}")
    magic = bytes(image[base + i] for i in range(4))
    if network == "findhub":
        if magic != bytes.fromhex("fa57fa57") or end < base + 71:
            raise ValueError("Missing Fast Pair registration record")
        # Nordic leaves one alignment byte erased between the model ID and key.
        record = bytes(image[base + i] for i in range(72))
        if hashlib.sha256(record[:40]).digest() != record[40:]:
            raise ValueError("Invalid Fast Pair registration checksum")
    elif magic != b"SID0" or end < base + 8:
        raise ValueError("Missing Sidewalk manufacturing record")
    return base, end


def main():
    from intelhex import IntelHex, IntelHexError

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", choices=("findhub", "sidewalk"))
    parser.add_argument("hexfile")
    args = parser.parse_args()
    try:
        base, end = check_record(args.network, IntelHex(args.hexfile))
    except (OSError, ValueError, IntelHexError) as error:
        parser.exit(1, f"Provisioning check failed: {error}\n")
    print(f"RAK provisioning page verified at {base:#x}..{end:#x} (inclusive)")


if __name__ == "__main__":
    main()
