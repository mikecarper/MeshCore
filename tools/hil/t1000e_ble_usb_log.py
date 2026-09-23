#!/usr/bin/env python3
"""Print the local T1000-E USB debug stream for a bounded interval."""

import argparse
import sys
import time

import serial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--seconds", type=int, default=55)
    args = parser.parse_args()
    deadline = time.monotonic() + args.seconds
    with serial.Serial(args.port, 115200, timeout=0.1) as port:
        port.dtr = True
        port.rts = False
        while time.monotonic() < deadline:
            data = port.read(port.in_waiting or 1)
            if data:
                sys.stdout.write(data.decode(errors="replace"))
                sys.stdout.flush()


if __name__ == "__main__":
    main()
