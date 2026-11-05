#!/usr/bin/env python3
"""Capture one selected ESP32-S3 HWCDC boot without resetting the device.

USB descriptors establish the selected endpoint, never application readiness.
Infrastructure readiness requires a completed `ver` echo and version reply.
Companion readiness requires its framed device-info, app-start and version replies.
Companion evidence contains only allowlisted metadata, never identity/key frames.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from serial_session import assert_port_idle, open_configured_session


def normalized_serial(value):
    return re.sub('[^a-z0-9]', '', value.lower())


def selected_port(by_path, by_id, expected_serial):
    """Require both stable identities and the actual kernel USB serial."""
    from serial.tools import list_ports

    path_port = Path('/dev/serial/by-path', by_path).resolve(strict=True)
    id_port = Path('/dev/serial/by-id', by_id).resolve(strict=True)
    if path_port != id_port or not stat.S_ISCHR(path_port.stat().st_mode):
        raise RuntimeError('Selected by-id and physical USB path do not agree')
    matches = [port for port in list_ports.comports()
               if Path(port.device).resolve() == path_port]
    if not matches:
        raise FileNotFoundError('Selected USB endpoint is not yet enumerated')
    if (len(matches) != 1 or (matches[0].vid, matches[0].pid) != (0x303A, 0x1001)
            or not matches[0].serial_number
            or normalized_serial(matches[0].serial_number) != normalized_serial(expected_serial)):
        raise RuntimeError('Selected native USB serial identity changed')
    return str(path_port)


def completed_version(data):
    found = re.search(rb'(?:^|\r?\n)(?:> ?)?ver\r?\n  -> (?:> )?'
                      rb'(v[0-9][^\r\n]*)\r?\n', data)
    return found[1].decode('utf-8', errors='replace') if found else None


def trace_phases(data):
    return [{'phase': phase.decode('ascii'), 'milliseconds': int(milliseconds)}
            for phase, milliseconds in re.findall(
                rb'HIL_BOOT phase=([a-z0-9_]+) at=([0-9]+)\r?\n', data)]


def capture_infrastructure(args):
    import serial

    started = time.monotonic()
    deadline = started + args.seconds
    data = bytearray()
    port = None
    version = None
    probe_offset = None
    next_probe = started + 2
    report = {'schema': 1, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'status': 'capturing', 'version': None, 'phases': [], 'bytes': 0}
    # Do not overwrite prior evidence or follow a symlink to a different file.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb', buffering=0) as output:
            while time.monotonic() < deadline:
                if port is None:
                    try:
                        live = selected_port(args.usb_path, args.usb_id, args.expected_serial)
                    except FileNotFoundError:
                        time.sleep(.05)
                        continue
                    assert_port_idle(live)
                    candidate = serial.Serial(port=None, baudrate=115200, timeout=.05,
                                              write_timeout=1, exclusive=True)
                    candidate.port, candidate.dtr, candidate.rts = live, False, False
                    try:
                        # Check again immediately before open; never choose a tty by order.
                        if live != selected_port(args.usb_path, args.usb_id, args.expected_serial):
                            raise RuntimeError('Selected USB identity moved before open')
                        open_configured_session(candidate)
                    except (serial.SerialException, FileNotFoundError):
                        candidate.close()
                        # USB can disappear while the same device changes modes.
                        # Recheck all identity guards before another open.
                        time.sleep(.05)
                        continue
                    except BaseException:
                        candidate.close()
                        raise
                    port = candidate
                    probe_offset = None
                try:
                    chunk = port.read(min(port.in_waiting or 1, 4096))
                    if chunk:
                        if len(data) + len(chunk) > 1024 * 1024:
                            raise RuntimeError('Boot capture exceeded its 1 MiB bound')
                        output.write(chunk)
                        data.extend(chunk)
                        version = completed_version(data[probe_offset:]) if probe_offset is not None else None
                        if version:
                            break
                    if time.monotonic() >= next_probe:
                        # CR clears any unterminated startup input. This is read-only CLI.
                        if probe_offset is None:
                            probe_offset = len(data)
                        if port.write(b'\rver\r') != 5:
                            raise RuntimeError('Short startup version query')
                        next_probe = time.monotonic() + 5
                except serial.SerialException:
                    port.close()
                    port = None
                    # Reopen only an endpoint satisfying the same three identity checks.
                    next_probe = time.monotonic() + 2
            report.update(status='runtime_verified' if version else 'runtime_not_verified',
                          version=version, phases=trace_phases(data), bytes=len(data))
    except BaseException as error:
        report.update(status='capture_failed', failure=type(error).__name__,
                      phases=trace_phases(data), bytes=len(data))
        raise
    finally:
        if port is not None:
            port.close()
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        sidecar = Path(str(args.output) + '.safe.json')
        meta_fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(meta_fd, 'w', encoding='utf-8') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
        print(json.dumps(report), flush=True)
    return 0 if version else 1


class BootNotReady(TimeoutError):
    pass


class CompanionCapturePort:
    """Keep native HWCDC control lines low and never block in tcdrain()."""
    def __init__(self, port, deadline):
        self._port, self._deadline = port, deadline
        port.dtr = port.rts = False

    def __getattr__(self, name):
        return getattr(self._port, name)

    @property
    def dtr(self):
        return False

    @dtr.setter
    def dtr(self, value):
        self._port.dtr = False

    @property
    def rts(self):
        return False

    @rts.setter
    def rts(self, value):
        self._port.rts = False

    def read(self, size=1):
        if time.monotonic() >= self._deadline:
            raise BootNotReady('Companion boot deadline reached')
        return self._port.read(size)

    def write(self, data):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise BootNotReady('Companion boot deadline reached')
        self._port.write_timeout = min(1, remaining)
        return self._port.write(data)

    def flush(self):
        # pySerial.write already submitted these few bytes. Serial.flush uses
        # unbounded tcdrain on POSIX; the following framed response proves delivery.
        pass


def new_companion(live, deadline):
    from companion_serial import Companion

    class BoundedCompanion(Companion):
        def request(self, payload, expected, timeout=5):
            return companion_boot_request(self, payload, expected, deadline, timeout)

    client = BoundedCompanion(live)
    try:
        client.port = CompanionCapturePort(client.port, deadline)
    except BaseException:
        client.close()
        raise
    return client


def companion_boot_request(client, payload, expected, boot_deadline, timeout=5):
    """A post-reset read-only probe may see a late reply from an earlier probe.

    Discard only complete, validated startup metadata with a different response
    type. Never treat it as the answer to this command or retry a rejection.
    """
    from companion_serial import Failure, check
    from esp32_companion_serial_stress import (
        FrameTimeout, ProtocolError, encode_host_frame, validate_app_start_response,
        validate_battery_storage_response, validate_device_info_response,
        validate_device_time_response,
    )

    deadline = min(boot_deadline, time.monotonic() + timeout)
    if time.monotonic() >= deadline:
        raise BootNotReady('Companion boot deadline reached')
    encoded = encode_host_frame(payload)
    check(client.port.write(encoded) == len(encoded), 'incomplete Companion write')
    client.port.flush()
    validators = {5: validate_app_start_response, 9: validate_device_time_response,
                  12: validate_battery_storage_response, 13: validate_device_info_response}
    ignored_frames = ignored_bytes = 0
    while time.monotonic() < deadline:
        try:
            frame = client.reader.read_frame(client.port, min(.5, deadline - time.monotonic()))
        except FrameTimeout:
            continue
        check(bool(frame), 'empty Companion response')
        if frame[0] == 1:
            raise Failure('Companion command rejected, error ' + str(frame[1:2].hex()))
        if frame[0] in expected:
            return frame
        if frame[0] < 0x80:
            check(frame[0] in validators, 'unexpected Companion response ' + str(frame[0]))
            try:
                validators[frame[0]](frame)
            except ProtocolError as error:
                raise Failure('malformed Companion startup metadata') from error
        ignored_frames += 1
        ignored_bytes += len(frame)
        check(ignored_frames <= 64 and ignored_bytes <= 65536,
              'Companion startup unrelated-frame limit exceeded')
    raise Failure('Companion response timeout for command ' + str(payload[0]))


def capture_companion(args):
    import serial
    from companion_serial import Failure

    started = time.monotonic()
    deadline = started + args.seconds
    report = {'schema': 1, 'protocol': 'companion',
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'status': 'capturing', 'model': None, 'version': None,
              'attempts': 0, 'evidence_format': 'allowlisted_json'}
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            try:
                while time.monotonic() < deadline:
                    client = None
                    try:
                        live = selected_port(args.usb_path, args.usb_id, args.expected_serial)
                        assert_port_idle(live)
                        if live != selected_port(args.usb_path, args.usb_id, args.expected_serial):
                            raise RuntimeError('Selected USB identity moved before open')
                        report['attempts'] += 1
                        client = new_companion(live, deadline)
                        info = client.start()
                        version = client.cli('ver').removeprefix('> ').strip()
                        model = info.get('model', '')
                        if (not isinstance(model, str) or not model.strip() or len(model) > 80
                                or not re.fullmatch(r'v[0-9][^\r\n]{0,255}', version)):
                            raise RuntimeError('Companion returned invalid model/version metadata')
                        report.update(status='runtime_verified', model=model,
                                      version=version)
                        break
                    except FileNotFoundError:
                        pass  # The same selected USB endpoint is still booting.
                    except (serial.SerialException, BootNotReady):
                        pass  # Reopen only the same guarded endpoint before deadline.
                    except Failure as error:
                        # Retry startup silence only. Rejections and malformed replies
                        # are evidence of an incompatible runtime, never readiness.
                        if 'response timeout for command ' not in str(error):
                            raise
                    finally:
                        if client is not None:
                            client.close()
                    if time.monotonic() < deadline:
                        time.sleep(min(.1, deadline - time.monotonic()))
                if report['status'] != 'runtime_verified':
                    report['status'] = 'runtime_not_verified'
            except BaseException as error:
                report.update(status='capture_failed', failure=type(error).__name__)
                raise
            finally:
                report['elapsed_seconds'] = round(time.monotonic() - started, 3)
                json.dump(report, output, indent=2)
                output.write('\n')
    finally:
        sidecar = Path(str(args.output) + '.safe.json')
        meta_fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(meta_fd, 'w', encoding='utf-8') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
        print(json.dumps(report), flush=True)
    return 0 if report['status'] == 'runtime_verified' else 1


def capture(args):
    if getattr(args, 'protocol', 'infrastructure') == 'companion':
        return capture_companion(args)
    return capture_infrastructure(args)


def preflight(args):
    # A Companion capture loads these after flashing; verify them before erase.
    if args.protocol == 'companion':
        from companion_serial import Companion, Failure, check  # noqa: F401
        from esp32_companion_serial_stress import (  # noqa: F401
            FrameTimeout, ProtocolError, encode_host_frame, validate_app_start_response,
            validate_battery_storage_response, validate_device_info_response,
            validate_device_time_response,
        )
    # Check that evidence can be created without touching the requested names.
    with tempfile.TemporaryFile(dir=args.output.parent):
        pass
    live = selected_port(args.usb_path, args.usb_id, args.expected_serial)
    assert_port_idle(live)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--usb-path', required=True)
    parser.add_argument('--usb-id', required=True)
    parser.add_argument('--expected-serial', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--protocol', choices=('infrastructure', 'companion'),
                        default='infrastructure')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    for name in (args.usb_path, args.usb_id):
        if not name or '/' in name or name.startswith('.'):
            parser.error('USB identities must be exact link basenames')
    if (not args.output.is_absolute() or not args.output.parent.is_dir()
            or args.output.exists() or args.output.is_symlink()
            or Path(str(args.output) + '.safe.json').exists()
            or Path(str(args.output) + '.safe.json').is_symlink()):
        parser.error('Capture requires a new absolute file in an existing directory')
    if not 3 <= args.seconds <= 30 or not re.fullmatch('[0-9a-f]{12}', normalized_serial(args.expected_serial)):
        parser.error('Capture requires a chip MAC USB serial and a 3..30 second bound')
    if args.check_only:
        preflight(args)
        print('Selected HWCDC identity and capture dependencies verified; serial unopened.')
        return 0
    return capture(args)


if __name__ == '__main__':
    raise SystemExit(main())
