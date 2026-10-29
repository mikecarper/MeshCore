#!/usr/bin/env python3
"""Finite, identity-bound split-repeater HIL campaign. This script transmits RF.

No flashing, backups, offline-message reads or remote execution. The config must
identify every physical USB device. --companions-only configures five fixtures
without sending probe packets, while the DUTs are being prepared separately.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import struct
import threading
import time

from companion_serial import Companion, check, cli_command_label
from esp32_companion_serial_stress import (DeviceFrameReader, FrameTimeout,
    TransportCounters, make_app_start_spec, validate_app_start_response)
from serial_session import assert_port_idle, open_configured_session
from split_radio_probe import (companion_send_command, inspect_push, make_probe,
                               packet_stats, parse_probe, profile_counters)

DUTS = ('left', 'right', 'center')
FIXTURES = ('feed_left', 'feed_right', 'observe_media', 'observe_usa', 'observe_inputs')
RF = {'input_media': (902.750, 250, 7, 5), 'input_usa': (904.525, 62.5, 7, 5),
      'output_media': (908.150, 250, 7, 5), 'output_usa': (906.525, 62.5, 7, 5)}


def normalized(text):
    return re.sub('[^a-z0-9]', '', text.lower())


def verify_usb(spec):
    from serial.tools import list_ports
    path, expected = spec['port'], spec.get('usb_serial', spec.get('serial'))
    check(expected and path.startswith('/dev/serial/by-id/'), 'stable port and USB serial required')
    resolved = os.path.realpath(path)
    matches = [p for p in list_ports.comports() if os.path.realpath(p.device) == resolved]
    check(len(matches) == 1 and (matches[0].serial_number or '').upper() == expected.upper(),
          'selected USB identity changed or is ambiguous')
    assert_port_idle(path)
    return matches[0]


def rf_text(rf):
    return f'{rf[0]:.3f},{rf[1]:g},{rf[2]},{rf[3]}'


def parse_radio_diagnostic(reply):
    """Read physical driver state and counters, never USB packet timestamps."""
    match = re.fullmatch(r'(?:>\s*)?state=(\d+)\([^)]*\), recv=(\d+), sent=(\d+), '
        r'errors=(\d+), err_flags=(\d+), outbound=(yes|no), last_rx=\d+s ago', reply.strip())
    check(match is not None, 'missing or ambiguous physical radio diagnostics')
    state, recv, sent, errors, flags = map(int, match.groups()[:5])
    check(0 <= state <= 255 and all(0 <= value <= 0xffffffff for value in
          (recv, sent, errors, flags)), 'invalid physical radio diagnostic counters')
    return {'state': state, 'tx_wait': (state & 15) == 3, 'recv': recv,
            'sent': sent, 'errors': errors, 'err_flags': flags,
            'outbound': match[6] == 'yes'}


def assess_cad_samples(samples, blocker_sent, center_sent, blocker_minimum_end=None):
    """Conservative single-packet ordering proof from synchronous readbacks.

    `sent` advances only after the driver's TX_DONE IRQ is accepted. A center
    TX_WAIT is cached until TX_DONE is serviced. Bracketed TX_WAIT therefore
    proves driver-state overlap only. It proves RF overlap only inside the
    verified blocker's minimum-preamble window, whose earliest possible start
    is the host's command write. For a pass, observe blocker TX_DONE, then center idle with its old
    counter, then center TX_DONE. Missing that idle gap is inconclusive: packet
    USB notification order alone cannot establish either radio's TX interval.
    """
    def delta(sample, initial):
        return (sample['sent']-initial) & 0xffffffff

    overlap, driver_overlap, ordered = False, False, False
    blocker_done = center_idle_after_blocker = blocker_started = False
    latest_blocker = None
    pending_center_tx = None
    block_deltas, center_deltas = [], []
    for sample in samples:
        if sample['node'] == 'blocker':
            count = delta(sample, blocker_sent)
            block_deltas.append(count)
            blocker_started |= sample['tx_wait'] and count == 0
            if pending_center_tx is not None and latest_blocker is not None:
                bracketed = latest_blocker['tx_wait'] and sample['tx_wait'] and \
                    delta(latest_blocker, blocker_sent) == count == 0
                driver_overlap |= bracketed
                overlap |= bracketed and blocker_minimum_end is not None and \
                    sample.get('reply_completed_monotonic', float('inf')) < blocker_minimum_end
            pending_center_tx = None
            latest_blocker = sample
            blocker_done |= count == 1 and not sample['tx_wait']
        elif sample['node'] == 'center':
            count = delta(sample, center_sent)
            center_deltas.append(count)
            if sample['tx_wait'] and count == 0:
                pending_center_tx = sample
            if blocker_done and count == 0 and not sample['tx_wait']:
                center_idle_after_blocker = True
            if center_idle_after_blocker and count == 1:
                ordered = True
    one_blocker = bool(block_deltas) and max(block_deltas) == 1
    one_center = bool(center_deltas) and max(center_deltas) == 1
    verdict = ('failed' if overlap else 'passed' if blocker_started and one_blocker and
               one_center and ordered else 'inconclusive')
    return {'verdict': verdict, 'blocker_tx_wait_observed': blocker_started,
            'blocker_completed_once': one_blocker, 'center_completed_once': one_center,
            'driver_tx_wait_overlap': driver_overlap, 'simultaneous_tx_proven': overlap,
            'transmit_order_proven': ordered,
            'blocker_minimum_end_monotonic': blocker_minimum_end}


class Evidence:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        self.file = (self.folder/'events.jsonl').open('x', encoding='utf-8', buffering=1)
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.observations = []
        self.run = secrets.token_bytes(8)
        self.report = {'schema': 2, 'run_id': self.run.hex(),
                       'started_utc': datetime.now(timezone.utc).isoformat(),
                       'source_commit': None, 'rf': RF, 'nodes': {}, 'trials': [],
                       'hardware_cad_busy_test': 'not performed: no controlled continuous LoRa blocker', 'status': 'running'}
        self.save()

    def event(self, kind, **fields):
        record = {'monotonic': time.monotonic(), 'kind': kind, **fields}
        with self.condition:
            self.file.write(json.dumps(record, sort_keys=True)+'\n')
            if kind == 'probe_rx':
                self.observations.append(record)
            self.condition.notify_all()
        return record

    def receive(self, label, frame):
        # A scanning witness has no single configured receive profile. Only
        # an actual push84 A0/A1 tag proves the profile of that observation.
        configured = None if label == 'observe_inputs' else 0
        record = inspect_push(frame, expected_run=self.run, observer=label,
                              configured_profile=configured)
        if record is not None:
            self.event('probe_rx', **record)

    def save(self):
        with self.lock:
            temporary = self.folder/'report.safe.json.tmp'
            temporary.write_text(json.dumps(self.report, indent=2, sort_keys=True)+'\n')
            os.replace(temporary, self.folder/'report.safe.json')

    def records(self, source, sequence):
        with self.lock:
            return [x.copy() for x in self.observations
                    if x['source'] == source and x['sequence'] == sequence]

    def close(self):
        self.file.close()


class CaptureCompanion(Companion):
    """One reader owns USB throughout setup, commands and asynchronous RX."""
    def __init__(self, label, spec, evidence):
        self.label, self.spec, self.evidence = label, spec, evidence
        self.usb = verify_usb(spec)
        super().__init__(spec['port'])
        self.responses, self.condition = deque(), threading.Condition()
        self.request_lock = threading.Lock()
        self.running, self.failure, self.thread = False, None, None

    def _read(self):
        try:
            while self.running:
                try:
                    frame = self.reader.read_frame(self.port, 0.25)
                except FrameTimeout:
                    continue
                if not frame:
                    continue
                if frame[0] >= 0x80:
                    self.evidence.receive(self.label, frame)
                else:
                    with self.condition:
                        check(len(self.responses) < 64, 'unbounded Companion response queue')
                        self.responses.append(frame)
                        self.condition.notify_all()
        except BaseException as error:
            with self.condition:
                self.failure = type(error).__name__
                self.condition.notify_all()

    def start(self):
        # Equivalent to the shared helper's negotiation, respecting JTAG lines.
        self.port.rts = False
        self.port.dtr = self.spec.get('dtr', (self.usb.vid, self.usb.pid) != (0x303a, 0x1001))
        self.port.write(b'+++MESHCORE-TERM-STOP\r')
        self.port.flush()
        time.sleep(.3)
        deadline = time.monotonic()+.5
        count = 0
        while time.monotonic() < deadline:
            chunk = self.port.read(min(self.port.in_waiting or 1, 4096))
            count += len(chunk)
            check(count <= 65536, 'terminal negotiation exceeded its byte bound')
        self.reader = DeviceFrameReader(TransportCounters())
        self.running = True
        self.thread = threading.Thread(target=self._read, name=self.label, daemon=True)
        self.thread.start()
        info = self.request(bytes((22, 14)), (13,))
        check(len(info) >= 80, 'truncated Companion info')
        self.max_channels = info[3]
        identity = self.request(make_app_start_spec('Split-Radio-HIL').payload, (5,))
        return {'protocol': info[1], 'model': info[20:60].split(b'\0')[0].decode(errors='replace'),
                'firmware_short': info[60:80].split(b'\0')[0].decode(errors='replace'),
                'primary': validate_app_start_response(identity)}

    def request(self, payload, expected, timeout=8):
        from esp32_companion_serial_stress import encode_host_frame
        with self.request_lock:
            with self.condition:
                check(not self.responses and not self.failure, 'Companion session is not synchronized')
                frame = encode_host_frame(payload)
                check(self.port.write(frame) == len(frame), 'short Companion write')
                self.port.flush()
                deadline = time.monotonic()+timeout
                while time.monotonic() < deadline and not self.failure:
                    if self.responses:
                        response = self.responses.popleft()
                        check(response[0] != 1, 'Companion rejected command '+str(payload[0]))
                        check(response[0] in expected, 'unexpected Companion response')
                        return response
                    self.condition.wait(min(.1, max(0, deadline-time.monotonic())))
            raise RuntimeError('Companion transaction failed or timed out: '+self.label)

    def stats(self):
        return packet_stats(self.request(bytes((56, 2)), (24,)))

    def close(self):
        self.running = False
        if self.thread:
            self.thread.join(2)
        super().close()


class Infrastructure:
    """Keep the ASCII device closed between complete CLI transactions."""
    def __init__(self, label, spec, evidence):
        self.label, self.spec, self.evidence = label, spec, evidence
        verify_usb(spec)
        check(not spec.get('dtr', False), 'infrastructure DTR must stay low')
        self.port, self.failure, self._batch_active = None, False, False

    def _stage(self, command, stage):
        operation = {'node': self.label, 'command': cli_command_label(command), 'stage': stage}
        self.evidence.report['last_serial_operation'] = operation
        self.evidence.save()
        self.evidence.event('serial_session', **operation)

    @contextmanager
    def _session(self, command):
        if self._batch_active:
            check(self.port is not None, 'configuration batch lost its serial session')
            self._stage(command, 'batch_transaction')
            yield self.port
            return
        import serial
        self._stage(command, 'identity_check')
        verify_usb(self.spec)
        port = serial.Serial(port=None, baudrate=115200, timeout=.05,
                             write_timeout=2, exclusive=True)
        self.port = port
        try:
            port.port = self.spec['port']
            port.dtr, port.rts = False, False
            self._stage(command, 'opening')
            open_configured_session(port)
            self._stage(command, 'open')
            # End an unfinished local input line without changing settings.
            port.write(b'\r')
            port.flush()
            time.sleep(.05)
            yield port
        finally:
            try:
                port.close()
            finally:
                self.port = None
                self._stage(command, 'closed')

    @contextmanager
    def batch(self):
        """Hold one identity-bound port for a finite synchronous command group."""
        check(not self.failure and self.port is None and not self._batch_active,
              'cannot start a nested or desynchronized configuration batch')
        with self._session('configuration batch'):
            self._batch_active = True
            try:
                yield self
            finally:
                self._batch_active = False

    def close(self):
        if self.port is not None:
            self.port.close()
            self.port = None

    def cli(self, command, timeout=15, allow_error=False):
        check(not self.failure and command and not any(c in command for c in '\r\n\0'),
              'invalid or desynchronized CLI operation')
        # Exact echo then a completed arrow reply, never a silence/prompt guess.
        echo = re.compile(r'(?:^|\r?\n)(?:> ?)?'+re.escape(command)+r'\r?\n')
        completed = False
        try:
            with self._session(command) as port:
                data, deadline = bytearray(), time.monotonic()+timeout
                port.write((command+'\r').encode('ascii'))
                port.flush()
                while time.monotonic() < deadline:
                    data.extend(port.read(min(port.in_waiting or 1, 4096)))
                    check(len(data) <= 65536, 'CLI transaction exceeded capture bound')
                    text = data.decode('utf-8', errors='replace')
                    found = echo.search(text)
                    if found:
                        response = re.search(r'(?:^|\r?\n)  -> ([^\r\n]*)\r?\n', text[found.end():])
                        if response:
                            completed = True
                            reply = response[1].removeprefix('> ').strip()
                            if not allow_error:
                                check(not re.search(r'^(?:Error|ERROR|ERR|Err|Unknown|\?\?:)', reply),
                                      self.label+' rejected '+cli_command_label(command))
                            # Values and raw echoes are deliberately not persisted.
                            self.evidence.event('cli', node=self.label, command=cli_command_label(command),
                                reply='<redacted>' if 'secret' in command or 'password' in command else reply)
                            return reply
                raise TimeoutError(self.label+' CLI did not complete '+cli_command_label(command))
        except BaseException:
            self.failure = not completed
            raise

    def packet_stats(self):
        return json.loads(self.cli('stats-packets'))

    def reboot(self):
        check(not self.failure and not self._batch_active,
              'cannot reboot a desynchronized or batched infrastructure session')
        with self._session('reboot') as port:
            port.write(b'reboot\r')
            port.flush()
        time.sleep(2)
        deadline = time.monotonic()+50
        while True:
            try:
                self.failure = False
                # A live exact reply proves application readiness; merely
                # finding its persistent USB bridge is insufficient.
                self.cli('ver', timeout=min(5, max(.1, deadline-time.monotonic())))
                return
            except (OSError, RuntimeError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)


class Campaign:
    def __init__(self, config, evidence, right_only=False):
        self.config, self.evidence = config, evidence
        self.nodes, self.sequence, self.center_key = {}, 0, None
        self.secret = secrets.token_hex(8)[:15]
        self.pause = float(config.get('probe_pause', 2.5))
        self.fixture_preamble = config.get('fixture_preamble', 96)
        check(type(self.fixture_preamble) is int and 16 <= self.fixture_preamble <= 65528,
              'fixture_preamble must be an integer from 16 to 65528')
        self.right_only = right_only
        self.duts = ('right', 'center') if right_only else DUTS
        self.sources = ('R',) if right_only else ('L', 'R')
        evidence.report['coverage'] = 'right_center_only' if right_only else 'full_three_dut_topology'
        evidence.report['not_tested'] = ['left DUT reception/forwarding', 'left to center MediaMesh output'] if right_only else []
        check(1 <= self.pause <= 10, 'probe_pause must be 1..10 seconds')

    def open(self, companions_only=False):
        labels = FIXTURES if companions_only else self.duts+FIXTURES
        for label in labels:
            self.evidence.report['opening_node'] = label
            self.evidence.save()
            spec = self.config['nodes'][label]
            if label in FIXTURES:
                node = CaptureCompanion(label, spec, self.evidence)
                self.nodes[label] = node
                info = node.start()
                info['firmware'] = node.cli('ver')
                model = info['model']
            else:
                node = Infrastructure(label, spec, self.evidence)
                self.nodes[label] = node
                model, version = node.cli('board'), node.cli('ver')
                public = node.cli('get public.key')
                check(re.fullmatch('[0-9a-fA-F]{64}', public), 'invalid DUT public identity')
                info = {'model': model, 'firmware': version, 'public_key': public.upper()}
                expected = spec.get('firmware_contains', self.config.get('expected_firmware', 'a925bdef'))
                check(expected and expected in version, 'DUT firmware source does not match expected build: '+label)
                if label == 'center':
                    self.center_key = bytes.fromhex(public)
            expected_model = spec.get('board_contains', spec.get('board', ''))
            check(expected_model and normalized(expected_model) in normalized(model),
                  'USB board model does not match configuration: '+label)
            self.evidence.report['nodes'][label] = info
            self.evidence.report['opening_node'] = None
            self.evidence.save()

    def configure_companions(self):
        settings = {'feed_left': RF['input_media'], 'feed_right': RF['input_usa'],
                    'observe_media': RF['output_media'], 'observe_usa': RF['output_usa'],
                    'observe_inputs': RF['input_usa']}
        for label, rf in settings.items():
            node = self.nodes[label]
            node.cli('set radio2 off')
            node.cli('set radio2.cross off')
            node.cli('set flood.retry.count 0')
            node.cli('set radio '+rf_text(rf)+','+str(self.fixture_preamble))
            node.request(bytes((11,))+struct.pack('<IIBBB', round(rf[0]*1000), round(rf[1]*1000), rf[2], rf[3], 0), (0,))
            node.request(bytes((12,))+struct.pack('b', -5), (0,))
            check(re.fullmatch(r'(?:>\s*)?-5', node.cli('get tx').strip()), 'fixture TX power readback mismatch')
            if label == 'observe_inputs':
                node.cli('set radio2 '+rf_text(RF['input_media'])+',rx,'+str(self.fixture_preamble))
            self.evidence.report['nodes'][label]['configured'] = {
                'radio': node.cli('get radio'), 'radio2': node.cli('get radio2'),
                'radio2_status': node.cli('get radio2.status'), 'packets': node.stats()}
            self.evidence.save()
        time.sleep(3)

    def configure_duts(self):
        for label in self.duts:
            node = self.nodes[label]
            scope = node.batch() if self.config.get('batch_dut_configuration', False) else nullcontext(node)
            with scope:
                self._configure_dut(label)
        self.restore_routes()
        self.evidence.save()
        time.sleep(3)

    def _configure_dut(self, label):
        node = self.nodes[label]
        commands = ['set radio.mode off', 'set powersaving off', 'set radio.rxps off',
            'set advert.interval 0', 'set flood.advert.interval 0', 'set flood.retry.count 0',
            'set repeat off', 'set espnow.enabled off', 'set udp.enabled off',
            'set mqtt.enabled off', 'stop webconfig', 'clear bridge.routes',
            'set bridge.ingress off', 'set bridge.source rx', 'set bridge.format wrapped',
            'set bridge.channel 6', 'set bridge.delay 500', 'set bridge.secret '+self.secret,
            'set radio2 off', 'set radio2.cross off', 'set tx 2']
        for command in commands:
            node.cli(command, allow_error=command in ('stop webconfig', 'set mqtt.enabled off'))
        rf = RF['input_media'] if label == 'left' else RF['input_usa'] if label == 'right' else RF['output_usa']
        node.cli('set radio '+rf_text(rf)+',96')
        if label == 'center':
            node.cli('set radio2 '+rf_text(RF['output_media'])+',rxtx,auto')
            node.cli('set repeat on')
        else:
            node.cli('set espnow.enabled on')
            check(node.cli('get espnow.running') == 'on', 'source ESP-NOW did not start')
            mac = re.search(r'mac:([0-9A-Fa-f:]{17})', node.cli('get espnow.stats mac'))
            check(mac is not None, 'missing source station MAC')
            self.evidence.report['nodes'][label]['bridge_mac'] = mac[1].upper()
        node.cli('set radio.mode '+('tx' if label == 'center' else 'rx'))

    def restore_routes(self):
        center = self.nodes['center']
        for slot, label, profile in ((1, 'left', 'radio2'), (2, 'right', 'radio')):
            if label not in self.duts:
                continue
            mac = self.evidence.report['nodes'][label]['bridge_mac']
            center.cli(f'set bridge.route.{slot} mac:{mac} {profile}')

    def wait_radio2_mode(self, mode, timeout=10):
        """Confirm the runtime profile, not the immediately saved preference."""
        check(mode in ('off', 'rx', 'rxtx'), 'invalid radio2 barrier mode')
        center = self.nodes['center']
        started, deadline = time.monotonic(), time.monotonic()+timeout
        while time.monotonic() < deadline:
            status = center.cli('get radio2.status', timeout=min(3, max(.1, deadline-time.monotonic())))
            if status.removeprefix('> ').startswith(mode+';'):
                scan = center.cli('get radio2.scan', timeout=min(3, max(.1, deadline-time.monotonic())))
                enabled = scan.removeprefix('> ').strip() != 'off'
                if enabled == (mode != 'off'):
                    record = self.evidence.event('radio2_runtime_confirmed', mode=mode,
                        status=status, scan=scan, elapsed_seconds=round(time.monotonic()-started, 3))
                    self.evidence.report.setdefault('profile_barriers', []).append(record)
                    self.evidence.save()
                    return
            time.sleep(.2)
        raise TimeoutError('center radio2 runtime did not become '+mode)

    def snapshot(self):
        result = {}
        for label, node in self.nodes.items():
            if label in DUTS:
                result[label] = {'mode': node.cli('get radio.mode'), 'radio': node.cli('get radio'),
                    'radio2': node.cli('get radio2'), 'profiles': profile_counters(node.cli('get radio2.status')),
                    'packets': node.packet_stats(), 'core': json.loads(node.cli('stats-core'))}
            else:
                result[label] = {'packets': node.stats(),
                    'profiles': profile_counters(node.cli('get radio2.status'))}
        return result

    def hop_diagnostics(self, source, sequence, stage):
        """Optional intrusive diagnostics, outside the probe's timing window.

        Each command is retained with its collection interval. Compare a run
        with this option off before attributing a timing change to firmware;
        repeated CLI handling itself competes with the radio/bridge loop.
        """
        if not self.config.get('per_probe_diagnostics', False):
            return None
        started = time.monotonic()
        result = {'source': source, 'sequence': sequence, 'stage': stage,
                  'collection_started_monotonic': started, 'nodes': {}}
        for label in ('left' if source == 'L' else 'right', 'center'):
            if label not in self.nodes:
                continue
            node = self.nodes[label]
            scope = node.batch() if hasattr(node, 'batch') else nullcontext(node)
            with scope:
                values = {'packets': node.packet_stats(),
                    'radio': parse_radio_diagnostic(node.cli('stats-radio-diag')),
                    'profile_status': node.cli('get radio2.status'),
                    'espnow_rx': node.cli('get espnow.stats rx'),
                    'espnow_tx': node.cli('get espnow.stats tx'),
                    'espnow_drops': node.cli('get espnow.stats drops')}
                if label == 'center':
                    values['cad'] = node.cli('get cad')
            result['nodes'][label] = values
        result['collection_completed_monotonic'] = time.monotonic()
        result['collection_seconds'] = round(time.monotonic()-started, 6)
        self.evidence.event('hop_diagnostics', **result)
        return result

    def probe(self, source, *, expected_forward=True, phase='positive', direct=False,
              expected_output=None):
        sequence = self.sequence
        self.sequence += 1
        wire = make_probe(self.evidence.run, source, sequence, direct=direct)
        feeder = self.nodes['feed_left' if source == 'L' else 'feed_right']
        diagnostics_before = self.hop_diagnostics(source, sequence, 'before')
        started = time.monotonic()
        self.evidence.event('probe_tx_request', source=source, sequence=sequence, phase=phase,
                            direct=direct, wire_hex=wire.hex())
        feeder.request(companion_send_command(wire), (0,))
        correct = expected_output or ('observe_media' if source == 'L' else 'observe_usa')
        check(correct in ('observe_media', 'observe_usa'), 'invalid expected output witness')
        wrong = 'observe_usa' if correct == 'observe_media' else 'observe_media'
        deadline = started+float(self.config.get('probe_timeout', 12))
        while time.monotonic() < deadline:
            records = self.evidence.records(source, sequence)
            input_ok = any(x['observer'] == 'observe_inputs' and x['wire_hex'] == wire.hex() for x in records)
            good = any(self.center_key is not None and x['observer'] == correct and x['wire_hex'] and
                       parse_probe(bytes.fromhex(x['wire_hex'])).forwarded_by(self.center_key) for x in records)
            # An absence check needs its full observation window. Seeing the
            # input packet is not proof that no delayed output will follow.
            if expected_forward and not direct and input_ok and good:
                break
            with self.evidence.condition:
                self.evidence.condition.wait(.1)
        time.sleep(self.pause)
        records = self.evidence.records(source, sequence)
        input_ok = any(x['observer'] == 'observe_inputs' and x['wire_hex'] == wire.hex() for x in records)
        output = [x for x in records if self.center_key is not None and x['observer'] == correct and x['wire_hex'] and
                  parse_probe(bytes.fromhex(x['wire_hex'])).forwarded_by(self.center_key)]
        leaked = [x for x in records if x['observer'] == wrong]
        any_output = [x for x in records if x['observer'] in ('observe_media', 'observe_usa')]
        input_times = [x['monotonic'] for x in records if x['observer'] == 'observe_inputs'
                       and x['wire_hex'] == wire.hex() and 'monotonic' in x]
        output_times = [x['monotonic'] for x in output if 'monotonic' in x]
        within_deadline = not output_times or min(output_times) <= deadline
        passed = input_ok and (bool(output) and within_deadline if expected_forward and not direct
                              else not any_output) and not leaked
        item = {'phase': phase, 'source': source, 'sequence': sequence, 'direct': direct,
                'wire_hex': wire.hex(), 'mesh_hash': parse_probe(wire).mesh_hash,
                'expected_output': correct,
                'expected_forward': expected_forward, 'input_original_seen': input_ok,
                'correct_center_outputs': len(output), 'wrong_channel_observations': len(leaked),
                'output_observations': len(any_output), 'passed': passed,
                'elapsed_seconds': round(time.monotonic()-started, 3),
                'request_monotonic': started, 'observation_deadline_monotonic': deadline,
                'timing_basis': 'host USB notifications; not RF start/end timestamps',
                'input_notification_seconds': min(input_times)-started if input_times else None,
                'output_notification_seconds': min(output_times)-started if output_times else None,
                'input_to_output_notification_seconds': min(output_times)-min(input_times)
                    if input_times and output_times else None,
                'output_within_deadline': within_deadline if output else None,
                'observations': records}
        item['delivery_result'] = ('wrong_channel' if leaked else
            'input_not_witnessed' if not input_ok else
            'forbidden_output' if (direct or not expected_forward) and any_output else
            'blocked_as_expected' if direct or not expected_forward else
            'missing_output' if not output else
            'delivered' if within_deadline else 'late_output')
        if diagnostics_before is not None:
            item['hop_diagnostics_before'] = diagnostics_before
            item['hop_diagnostics_after'] = self.hop_diagnostics(source, sequence, 'after')
        if direct and source == 'L':
            item['radio2_tag_seen'] = any(x['observer'] == 'observe_inputs' and
                                         x['push'] == 0x84 and x['radio_profile'] == 1 for x in records)
            if self.config.get('require_radio2_tag', True):
                item['passed'] &= item['radio2_tag_seen']
        self.evidence.report['trials'].append(item)
        self.evidence.save()
        print(json.dumps(item), flush=True)
        return item

    def set_fixture_rf(self, label, rf, preamble=None):
        node = self.nodes[label]
        preamble = self.fixture_preamble if preamble is None else preamble
        check(type(preamble) is int and 16 <= preamble <= 65528, 'invalid test preamble')
        node.cli('set radio '+rf_text(rf)+','+str(preamble))
        node.request(bytes((11,))+struct.pack('<IIBBB', round(rf[0]*1000),
            round(rf[1]*1000), rf[2], rf[3], 0), (0,))
        reply = node.cli('get radio').removeprefix('> ').strip()
        values = reply.split(',')
        check(len(values) >= 4 and abs(float(values[0])-rf[0]) < .0001 and
              abs(float(values[1])-rf[1]) < .01 and tuple(map(int, values[2:4])) == rf[2:4],
              'fixture RF readback mismatch: '+label)
        self.evidence.event('fixture_rf_readback', node=label, radio=reply, preamble=preamble)

    def fixture_primary_preamble(self, label):
        # Older Full Companions expose the effective pair through status but
        # do not implement the newer saved-preference radio.preamble getter.
        status = self.nodes[label].cli('get radio2.status')
        matches = re.findall(r'\bpreamble=(\d+),(\d+)(?=\s|;|$)', status)
        check(len(matches) == 1, 'missing or ambiguous fixture preamble readback')
        primary, secondary = map(int, matches[0])
        self.evidence.event('fixture_preamble_readback', node=label, primary=primary, secondary=secondary)
        return primary

    def source_rx_challenges(self):
        # Repeat is enabled during this challenge: the direction gate, rather
        # than repeat=off, must keep the source's physical transmitter silent.
        for source in self.sources:
            label = 'left' if source == 'L' else 'right'
            node = self.nodes[label]
            before = node.packet_stats()
            node.cli('set repeat on')
            try:
                trial = self.probe(source, phase='rx_mode_repeat_enabled')
                after = node.packet_stats()
                key = bytes.fromhex(self.evidence.report['nodes'][label]['public_key'])
                forwarded = [x for x in self.evidence.records(source, trial['sequence'])
                    if x['observer'] == 'observe_inputs' and x['wire_hex'] and
                    parse_probe(bytes.fromhex(x['wire_hex'])).forwarded_by(key)]
                trial['source_tx_unchanged'] = after['sent'] == before['sent']
                trial['source_forward_observations'] = len(forwarded)
                trial['passed'] &= trial['source_tx_unchanged'] and not forwarded
                self.evidence.save()
            finally:
                node.cli('set repeat off')

    def center_rx_challenges(self):
        # Inject on the center's actual tunings. An output witness must see
        # each original over RF while the TX-only center's RX stays unchanged.
        center = self.nodes['center']
        try:
            for source, profile, observer in (('L', 'output_media', 'observe_media'),
                                               ('R', 'output_usa', 'observe_usa')):
                feeder = 'feed_left' if source == 'L' else 'feed_right'
                self.set_fixture_rf(feeder, RF[profile])
                time.sleep(2)
                before = center.packet_stats()
                sequence = self.sequence
                self.sequence += 1
                wire = make_probe(self.evidence.run, source, sequence)
                started = time.monotonic()
                self.evidence.event('probe_tx_request', source=source, sequence=sequence,
                    phase='tx_mode_rf_reception_challenge', profile=profile, wire_hex=wire.hex())
                self.nodes[feeder].request(companion_send_command(wire), (0,))
                time.sleep(float(self.config.get('probe_timeout', 12))+self.pause)
                records = self.evidence.records(source, sequence)
                witness = any(x['observer'] == observer and x['wire_hex'] == wire.hex() for x in records)
                forwarded = [x for x in records if x['wire_hex'] and
                    parse_probe(bytes.fromhex(x['wire_hex'])).forwarded_by(self.center_key)]
                wrong = [x for x in records if x['observer'] in ('observe_media', 'observe_usa')
                         and x['observer'] != observer]
                after = center.packet_stats()
                item = {'phase': 'tx_mode_rf_reception_challenge', 'source': source,
                    'sequence': sequence, 'direct': False, 'expected_forward': False,
                    'expected_output': observer, 'expected_original_on_output': True,
                    'original_rf_witness_seen': witness, 'center_rx_unchanged': after['recv'] == before['recv'],
                    'center_forward_observations': len(forwarded), 'wrong_channel_observations': len(wrong),
                    'passed': witness and after['recv'] == before['recv'] and not forwarded and not wrong,
                    'elapsed_seconds': round(time.monotonic()-started, 3)}
                self.evidence.report['trials'].append(item)
                self.evidence.save()
                print(json.dumps(item), flush=True)
        finally:
            self.set_fixture_rf('feed_left', RF['input_media'])
            self.set_fixture_rf('feed_right', RF['input_usa'])
            time.sleep(2)
        for source in self.sources:
            self.probe(source, phase='restored_input_rf_control')

    def cad_busy_challenge(self):
        """Finite LoRa blocker with physical TX-state/counter ordering proof."""
        center = self.nodes['center']
        feeder = self.nodes['feed_left']
        poll_interval = float(self.config.get('cad_poll_interval', .25))
        check(.1 <= poll_interval <= 1, 'cad_poll_interval must be 0.1..1 seconds')
        samples = []

        def sample(node, role):
            began = time.monotonic()
            value = parse_radio_diagnostic(node.cli('stats-radio-diag'))
            value.update(node=role, request_started_monotonic=began,
                         reply_completed_monotonic=time.monotonic())
            samples.append(value)
            self.evidence.event('physical_radio_sample', **value)
            return value

        def busy_count(timeout=15):
            reply = center.cli('get cad', timeout=timeout)
            match = re.search(r'# channel busy:\s*(\d+)', reply)
            check(reply.startswith('on (required') and match is not None,
                  'mandatory CAD state/counter unavailable')
            return int(match[1])

        before = center.packet_stats()
        busy_before = busy_count()
        block_sequence, request_sequence = self.sequence, self.sequence+1
        self.sequence += 2
        blocker = make_probe(self.evidence.run, 'L', block_sequence, direct=True)
        request = make_probe(self.evidence.run, 'R', request_sequence)
        started, busy_peak = time.monotonic(), busy_before
        blocker_requested_at = None
        result = {'status': 'running', 'blocker_sequence': block_sequence,
                  'request_sequence': request_sequence, 'preamble_symbols': 8192,
                  'nominal_preamble_seconds': 8192*128/62500,
                  'busy_before': busy_before, 'poll_interval_seconds': poll_interval,
                  'timing_basis': 'synchronous physical driver TX_WAIT and TX_DONE counters',
                  'usb_witness_order_is_rf_timing_proof': False}
        self.evidence.report['hardware_cad_busy_test'] = result
        self.evidence.save()
        try:
            self.set_fixture_rf('feed_left', RF['output_usa'], preamble=8192)
            check(self.fixture_primary_preamble('feed_left') == 8192,
                  'long blocker preamble readback mismatch')
            blocker_baseline = sample(feeder, 'blocker')
            center_baseline = sample(center, 'center')
            check(not blocker_baseline['tx_wait'] and not blocker_baseline['outbound'] and
                  not center_baseline['tx_wait'] and not center_baseline['outbound'],
                  'CAD trial must start with both transmit queues and radios idle')
            self.evidence.event('probe_tx_request', source='L', sequence=block_sequence,
                phase='cad_busy_blocker', direct=True, wire_hex=blocker.hex())
            blocker_request_started_at = time.monotonic()
            feeder.request(companion_send_command(blocker), (0,))
            blocker_requested_at = time.monotonic()
            start_deadline = time.monotonic()+8
            while True:
                live = sample(feeder, 'blocker')
                check(live['sent'] == blocker_baseline['sent'],
                      'blocker completed before its physical TX_WAIT was observed')
                if live['tx_wait']:
                    break
                check(time.monotonic() < start_deadline, 'blocker never entered physical TX_WAIT')
                time.sleep(poll_interval)
            self.evidence.event('probe_tx_request', source='R', sequence=request_sequence,
                phase='cad_busy_forward_request', direct=False, wire_hex=request.hex())
            self.nodes['feed_right'].request(companion_send_command(request), (0,))
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                # A center TX_WAIT surrounded by blocker TX_WAIT readbacks
                # brackets the cached TX states. RF overlap additionally
                # requires this to occur before the conservative earliest
                # end of the blocker's verified long preamble.
                sample(feeder, 'blocker')
                sample(center, 'center')
                sample(feeder, 'blocker')
                busy_peak = max(busy_peak, busy_count(timeout=min(3, max(.1, deadline-time.monotonic()))))
                time.sleep(poll_interval)
            blocks = self.evidence.records('L', block_sequence)
            requests = self.evidence.records('R', request_sequence)
            block_times = [x['monotonic'] for x in blocks if x['observer'] == 'observe_usa'
                           and x['wire_hex'] == blocker.hex()]
            input_times = [x['monotonic'] for x in requests if x['observer'] == 'observe_inputs'
                           and x['wire_hex'] == request.hex()]
            forward_times = [x['monotonic'] for x in requests if x['observer'] == 'observe_usa'
                and x['wire_hex'] and parse_probe(bytes.fromhex(x['wire_hex'])).forwarded_by(self.center_key)]
            media = [x for x in blocks+requests if x['observer'] == 'observe_media']
            after = center.packet_stats()
            delay = min(forward_times)-min(input_times) if forward_times and input_times else None
            timing = assess_cad_samples(samples, blocker_baseline['sent'], center_baseline['sent'],
                blocker_request_started_at+result['nominal_preamble_seconds'])
            invariants = {'blocker_rf_witness_seen': bool(block_times),
                'request_input_rf_witness_seen': bool(input_times),
                'center_forward_rf_witness_seen': bool(forward_times),
                'cad_busy_increased': busy_peak > busy_before,
                'blocker_physical_tx_wait_seen': timing['blocker_tx_wait_observed'],
                'blocker_completed_one_physical_tx': timing['blocker_completed_once'],
                'center_completed_one_physical_tx': timing['center_completed_once'],
                'physical_simultaneous_tx_absent': not timing['simultaneous_tx_proven'],
                'center_rf_rx_unchanged': after['recv'] == before['recv'],
                'media_output_absent': not media}
            status = ('failed' if not all(invariants.values()) else timing['verdict'])
            result.update(status=status, invariants=invariants, physical_timing=timing,
                physical_samples=samples, busy_peak=busy_peak,
                input_to_output_notification_seconds=delay,
                blocker_to_forward_notification_seconds=min(forward_times)-min(block_times)
                    if forward_times and block_times else None,
                blocker_witness_monotonic=min(block_times) if block_times else None,
                forward_witness_monotonic=min(forward_times) if forward_times else None,
                observations=blocks+requests,
                elapsed_seconds=round(time.monotonic()-started, 3))
            self.evidence.save()
            print(json.dumps({'phase': 'cad_busy', **{key: value for key, value in result.items()
                if key not in ('physical_samples', 'observations')},
                'physical_sample_count': len(samples)}), flush=True)
        except BaseException as error:
            result.update(status='execution_failed', error=type(error).__name__)
            self.evidence.save()
            raise
        finally:
            # The blocker may still be on-air if an earlier CLI failed. Wait
            # past its finite preamble before changing its tuning.
            if blocker_requested_at is not None and time.monotonic()-blocker_requested_at < 19:
                time.sleep(19-(time.monotonic()-blocker_requested_at))
            self.set_fixture_rf('feed_left', RF['input_media'])
            check(self.fixture_primary_preamble('feed_left') == self.fixture_preamble,
                  'normal feeder preamble restoration failed')
        self.probe('R', phase='cad_busy_restored_control')

    def fixture_test(self):
        self.evidence.report['coverage'] = 'fixtures_input_rf_only'
        self.evidence.report['not_tested'] = ['DUT input reception and bridge forwarding',
            'center RF output', 'DUT direction enforcement', 'DUT saved-setting reboot']
        self.evidence.report['baseline'] = self.snapshot()
        for _ in range(3):
            for source in ('L', 'R'):
                self.probe(source, expected_forward=False, phase='fixture_input_rf')
        self.probe('L', expected_forward=False, phase='fixture_radio2_tag', direct=True)
        time.sleep(float(self.config.get('final_drain', 3)))
        final = self.snapshot()
        self.evidence.report['final'] = final
        baseline = self.evidence.report['baseline']
        invariants = {
            'left_feeder_transmitted': final['feed_left']['packets']['sent'] > baseline['feed_left']['packets']['sent'],
            'right_feeder_transmitted': final['feed_right']['packets']['sent'] > baseline['feed_right']['packets']['sent'],
            'input_radio2_received': final['observe_inputs']['profiles']['rx'][1] > baseline['observe_inputs']['profiles']['rx'][1],
            'input_primary_received': final['observe_inputs']['profiles']['rx'][0] > baseline['observe_inputs']['profiles']['rx'][0],
            'observers_did_not_transmit': all(final[label]['packets']['sent'] == baseline[label]['packets']['sent']
                for label in ('observe_media', 'observe_usa', 'observe_inputs')),
            'output_channels_quiet': not any(x['observer'] in ('observe_media', 'observe_usa')
                                            for x in self.evidence.observations)}
        self.evidence.report['invariants'] = invariants
        passed = all(invariants.values()) and all(x['passed'] for x in self.evidence.report['trials'])
        self.evidence.report['status'] = 'fixtures_input_rf_passed' if passed else 'failed'
        self.evidence.save()

    def run(self):
        center = self.nodes['center']
        self.evidence.report['baseline'] = self.snapshot()
        for source in self.sources:
            self.probe(source, expected_forward=False, phase='bridge_off')
        center.cli('set espnow.enabled on')
        check(center.cli('get espnow.running') == 'on', 'center ESP-NOW did not start')
        for _ in range(int(self.config.get('positive_per_source', 5))):
            for source in self.sources:
                self.probe(source)
        self.source_rx_challenges()
        self.center_rx_challenges()
        if self.config.get('cad_busy_test', False):
            self.cad_busy_challenge()
        if self.right_only:
            mac = self.evidence.report['nodes']['right']['bridge_mac']
            center.cli(f'set bridge.route.2 mac:{mac} radio2')
            try:
                self.probe('R', phase='right_source_radio2_route_substitute', expected_output='observe_media')
            finally:
                self.restore_routes()
        self.probe('L', expected_forward=False, phase='radio2_direct_witness', direct=True)
        center.cli('set radio2.cross on')
        for source in self.sources:
            self.probe(source, phase='cross_on_isolation')
        center.cli('set radio2.cross off')
        center.cli('set radio2 off')
        self.wait_radio2_mode('off')
        if not self.right_only:
            self.probe('L', expected_forward=False, phase='radio2_unavailable')
        self.probe('R', phase='radio2_unavailable_usa_control')
        center.cli('set radio2 '+rf_text(RF['output_media'])+',rxtx,auto')
        self.wait_radio2_mode('rxtx')
        if not self.right_only:
            # Exercise the first real bridge packet after the runtime enable
            # barrier, then a steady control. Do not warm the output with a
            # different probe first or silently increase its delivery deadline.
            self.probe('L', phase='radio2_reenabled_first_packet')
            self.probe('L', phase='radio2_reenabled_steady_control')
        center.cli('del bridge.route.'+('2' if self.right_only else '1'))
        self.probe('R' if self.right_only else 'L', expected_forward=False, phase='unmapped_sender')
        if not self.right_only:
            self.probe('R', phase='unmapped_sender_usa_control')
        self.restore_routes()
        self.evidence.report['before_reboot'] = self.snapshot()
        if self.config.get('reboot', True):
            for label in self.duts:
                self.nodes[label].reboot()
                public = self.nodes[label].cli('get public.key')
                check(public.upper() == self.evidence.report['nodes'][label]['public_key'],
                      'DUT identity changed on warm reboot')
            check(all(self.nodes[label].cli('get radio.mode') == 'rx' for label in self.duts if label != 'center') and
                  center.cli('get radio.mode') == 'tx', 'direction did not survive reboot')
            self.evidence.report['after_reboot_before_probes'] = self.snapshot()
            for source in self.sources:
                self.probe(source, phase='warm_reboot')
        time.sleep(float(self.config.get('final_drain', 3)))
        self.evidence.report['final'] = self.snapshot()
        baseline, before = self.evidence.report['baseline'], self.evidence.report['before_reboot']
        invariants = {
            'right_lora_tx_unchanged': before['right']['packets']['sent'] == baseline['right']['packets']['sent'],
            'center_rf_rx_unchanged': before['center']['packets']['recv'] == baseline['center']['packets']['recv'],
            'input_observer_radio2_rx_increased': before['observe_inputs']['profiles']['rx'][1] >
                baseline['observe_inputs']['profiles']['rx'][1],
            'observers_did_not_transmit': all(before[label]['packets']['sent'] == baseline[label]['packets']['sent']
                for label in ('observe_media', 'observe_usa', 'observe_inputs'))}
        if not self.right_only:
            invariants['left_lora_tx_unchanged'] = before['left']['packets']['sent'] == baseline['left']['packets']['sent']
        if self.config.get('reboot', True):
            final = self.evidence.report['final']
            invariants['post_reboot_rx_sources_silent'] = all(final[label]['packets']['sent'] == 0
                                                            for label in self.duts if label != 'center')
            invariants['post_reboot_center_no_rf_receive'] = final['center']['packets']['recv'] == 0
        self.evidence.report['invariants'] = invariants
        self.evidence.report['cad'] = center.cli('get cad')
        invariants['center_cad_required'] = self.evidence.report['cad'].startswith('on (required')
        if self.config.get('cad_busy_test', False):
            invariants['hardware_cad_busy_passed'] = self.evidence.report['hardware_cad_busy_test']['status'] == 'passed'
        # Recheck all captured observations after the entire run, including
        # outputs which arrived after an earlier trial's local deadline.
        for trial in self.evidence.report['trials']:
            records = self.evidence.records(trial['source'], trial['sequence'])
            trial['observations'] = records
            correct = trial.get('expected_output', 'observe_media' if trial['source'] == 'L' else 'observe_usa')
            wrong = 'observe_usa' if correct == 'observe_media' else 'observe_media'
            forbidden = [x for x in records if x['observer'] == wrong or (
                trial.get('expected_original_on_output') and x['wire_hex'] and
                parse_probe(bytes.fromhex(x['wire_hex'])).forwarded_by(self.center_key)) or (
                not trial.get('expected_original_on_output') and
                (trial['direct'] or not trial['expected_forward']) and
                x['observer'] in ('observe_media', 'observe_usa'))]
            trial['late_or_wrong_output_observations'] = len(forbidden)
            trial['passed'] = trial['passed'] and not forbidden
            if 'request_monotonic' in trial:
                delivered = [x for x in records if x['observer'] == correct and x['wire_hex'] and
                    parse_probe(bytes.fromhex(x['wire_hex'])).forwarded_by(self.center_key)]
                times = [x['monotonic'] for x in delivered]
                if times:
                    trial['output_notification_seconds'] = min(times)-trial['request_monotonic']
                    trial['output_within_deadline'] = min(times) <= trial['observation_deadline_monotonic']
                trial['delivery_result'] = ('forbidden_output' if forbidden else
                    'input_not_witnessed' if not trial['input_original_seen'] else
                    'blocked_as_expected' if not trial['expected_forward'] or trial['direct'] else
                    'missing_output' if not times else
                    'delivered' if trial['output_within_deadline'] else 'late_output')
        passed = all(invariants.values()) and all(x['passed'] for x in self.evidence.report['trials'])
        self.evidence.report['status'] = ('partial_passed' if self.right_only else 'passed') if passed else 'failed'
        self.evidence.save()

    def quiesce(self):
        result = {}
        # The transmitting center is stopped first. Reopen a desynchronized
        # session once and verify its public identity before changing it.
        for label in ('center', 'left', 'right'):
            if label not in self.nodes:
                continue
            node = self.nodes[label]
            try:
                if node.failure:
                    node.close()
                    node = Infrastructure(label, self.config['nodes'][label], self.evidence)
                    self.nodes[label] = node
                    public = node.cli('get public.key', timeout=8)
                    check(public.upper() == self.evidence.report['nodes'][label]['public_key'],
                          'quiescence identity mismatch')
                node.cli('set radio.mode off', timeout=8)
                check(node.cli('get radio.mode', timeout=8) == 'off', 'radio off not confirmed')
                result[label] = 'LoRa off confirmed'
            except BaseException as error:
                result[label] = 'unconfirmed: '+type(error).__name__
        self.evidence.report['failure_quiescence'] = result
        self.evidence.save()

    def close(self):
        for node in reversed(list(self.nodes.values())):
            try:
                node.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--companions-only', action='store_true')
    parser.add_argument('--fixtures-test', action='store_true', help='Test five fixture radios without opening DUT ports')
    parser.add_argument('--right-only', action='store_true', help='Test right/center while left DUT is unavailable')
    args = parser.parse_args()
    parser.error('--companions-only and --fixtures-test are mutually exclusive') if args.companions_only and args.fixtures_test else None
    config = json.loads(args.config.read_text())
    evidence = Evidence(args.output)
    evidence.report['source_commit'] = config.get('source_commit')
    campaign = Campaign(config, evidence, args.right_only)
    try:
        campaign.open(args.companions_only or args.fixtures_test)
        campaign.configure_companions()
        if args.companions_only:
            evidence.report['status'] = 'fixtures_configured_no_probe_rf'
            evidence.save()
        elif args.fixtures_test:
            campaign.fixture_test()
        else:
            campaign.configure_duts()
            campaign.run()
        if evidence.report['status'] == 'failed':
            campaign.quiesce()
    except BaseException as error:
        evidence.report['status'] = 'execution_failed'
        evidence.report['failure'] = {'type': type(error).__name__, 'message': str(error)[:250],
            'opening_node': evidence.report.get('opening_node'),
            'serial_operation': evidence.report.get('last_serial_operation')}
        evidence.save()
        campaign.quiesce()
        raise
    finally:
        campaign.close()
        evidence.close()
    return 0 if evidence.report['status'] in ('passed', 'partial_passed', 'fixtures_configured_no_probe_rf',
                                              'fixtures_input_rf_passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
