#!/usr/bin/env python3
"""Check USB Companion ASCII/binary handoff with real, unmodified MeshCLI.

Install meshcore-cli in a virtual environment first. This script issues only
local information queries and mode switches; it neither flashes nor sends LoRa
messages. The optional second port is opened as a simultaneous logging reader.
Start after a fresh reboot. Use --test-dtr only on nRF52; changing these control
lines can invoke an ESP32 hardware bootloader.
"""
import argparse
import asyncio
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time

import serial
from meshcore import MeshCore


def open_tty(port, dtr=True):
    tty = serial.Serial(port=None, baudrate=115200, timeout=2, exclusive=True)
    tty.port = port
    # ESP32 interprets some RTS/DTR combinations as reset/download control.
    # Keep RTS deasserted before opening; toggling DTR while leaving the
    # pyserial default RTS high can reboot the board instead of testing USB.
    tty.rts = False
    tty.dtr = dtr
    tty.open()
    return tty


def drain(tty, duration):
    data = bytearray()
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        data.extend(tty.read(max(1, min(4096, tty.in_waiting))))
    return bytes(data)


def battery_reply(tty):
    tty.write(b'<\x01\x00\x14')
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        header = tty.read(3)
        assert len(header) == 3 and header[0] == ord('>'), 'Missing or contaminated binary header: ' + header.hex()
        size = int.from_bytes(header[1:], 'little')
        assert 1 <= size <= 300, 'Invalid binary frame length'
        payload = tty.read(size)
        assert len(payload) == size, 'Truncated binary reply'
        if payload[0] == 12:
            return
        # A real radio may push a received-message notification before its
        # query reply. It is valid only as a complete, clean binary frame.
    raise AssertionError('Missing battery/storage reply')


async def stock_connections(port, cycles):
    for _ in range(cycles):
        client = await MeshCore.create_serial(port, baudrate=115200)
        assert client is not None, 'Stock MeshCLI library failed to connect'
        try:
            assert client.self_info, 'Stock client did not receive its first reply'
        finally:
            await client.disconnect()
        await asyncio.sleep(0.02)


def run(args):
    results = {
        'port': args.port,
        'logging_port': args.logging_port,
        'meshcore_cli': importlib.metadata.version('meshcore-cli'),
        'meshcore': importlib.metadata.version('meshcore'),
        'checks': [],
    }
    logging_tty = None
    logging_thread = None
    stop_logging = threading.Event()
    logging_counts = {'bytes': 0, 'errors': 0}
    if args.logging_port:
        logging_tty = open_tty(args.logging_port)
        logging_tty.timeout = 0.05

        def read_logging():
            try:
                while not stop_logging.is_set():
                    logging_counts['bytes'] += len(logging_tty.read(max(1, min(4096, logging_tty.in_waiting))))
            except serial.SerialException:
                logging_counts['errors'] += 1

        logging_thread = threading.Thread(target=read_logging, daemon=True)
        logging_thread.start()
    try:
        with open_tty(args.port) as tty:
            tty.timeout = 0.02
            initial = drain(tty, 0.2)
            assert not initial, 'Fresh connection emitted unsolicited text or stale data'
            tty.write(b'ver\r')
            response = drain(tty, 0.4)
            assert b'Companion v' in response, 'Default ASCII terminal did not answer'
            results['checks'].append('default ASCII accepts ver without START')
            tty.write(b'+++MESHCORE-TERM-STOP\r')
            assert b'OK - Binary mode' in drain(tty, 0.3), 'STOP acknowledgement missing'
            tty.timeout = 2
            for _ in range(args.cycles):
                battery_reply(tty)
            results['checks'].append(f'{args.cycles} binary queries after explicit STOP')

        if args.test_dtr:
            for dtr in (False, True):
                for cycle in range(args.cycles):
                    results['active_case'] = dict(dtr=dtr, cycle=cycle + 1)
                    with open_tty(args.port, dtr) as tty:
                        # No startup delay or input flush: exercise the first
                        # frame while the device settles a close/reopen.
                        battery_reply(tty)
                results['checks'].append(f'{args.cycles} immediate binary reconnects with DTR={dtr}')
        results.pop('active_case', None)

        asyncio.run(stock_connections(args.port, args.cycles))
        results['checks'].append(f'{args.cycles} stock library reconnects')
        cli = shutil.which('meshcli')
        assert cli, 'meshcli is not installed in this environment'
        for _ in range(3):
            completed = subprocess.run([cli, '-j', '-s', args.port, 'infos'], capture_output=True, text=True, timeout=20)
            assert completed.returncode == 0 and json.loads(completed.stdout).get('public_key'), 'Stock meshcli infos failed'
        results['checks'].append('3 unmodified meshcli infos invocations')
        assert not logging_counts['errors'], 'Logging interface disconnected during primary-port tests'
        results['success'] = True
    finally:
        stop_logging.set()
        if logging_thread:
            logging_thread.join(timeout=1)
        if logging_tty:
            logging_tty.close()
        results['logging'] = logging_counts
        if args.output:
            Path(args.output).write_text(json.dumps(results, indent=2) + '\n')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--logging-port')
    parser.add_argument('--cycles', type=int, default=10)
    parser.add_argument('--test-dtr', action='store_true',
                        help='Exercise nRF52 DTR-high/low reconnects. Do not use on ESP32: these control-line transitions can trigger its hardware bootloader.')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error('--cycles must be positive')
    print(json.dumps(run(args), indent=2))
