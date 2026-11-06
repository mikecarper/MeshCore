#!/usr/bin/env python3
"""Finite all-Companion RF and media-transport bench. This transmits RF.

The default is an inert plan. Every endpoint is supplied explicitly; identity
is never selected by tty order. No flashing, erase, backup or remote execution
is performed. Results retain misses, duplicates and command failures. No RF
packet is automatically resent. Media bytes are pulled back through the real
Companion application queue and compared with the original fixture.

Run after installing the intended Companion image on every selected node.
The final policy deliberately leaves this isolated bench on its test channels,
radio2 off, primary AGC 16 s and old AGC off. It does not restore old firmware.
"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import statistics
import struct
import threading
import time

from companion_serial import Companion, check
from esp32_companion_serial_stress import (DeviceFrameReader, FrameTimeout,
    TransportCounters, encode_host_frame, make_app_start_spec,
    validate_app_start_response)
from serial_session import assert_port_idle
from split_radio_probe import make_probe, parse_probe, packet_stats, counter_delta
from companion_fanout_codec import (media_bundle, encode_fetch, parse_fetch,
    parse_chunk, assemble, channel_data_command, parse_channel_frame,
    group_text_command, parse_group_text_response, encode_chunk)

PRIMARY = (906.525, 62.5, 7, 5)
SECONDARY = (908.150, 250, 7, 5)
PULSE_PAGES = ('get agc.status', 'get agc.timing', 'get agc.recovery',
               'get agc.pulse', 'get agc.sleep', 'get agc.checks')
MEDIA_GATHER_SECONDS = 12


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='ascii') as out:
        os.fchmod(out.fileno(), 0o600)
        json.dump(value, out, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)
        out.write('\n'); out.flush(); os.fsync(out.fileno())
    os.replace(temporary, path)


def stripped(reply):
    return reply.strip().removeprefix('> ').strip()


def fields(reply):
    result = {}
    for part in stripped(reply).split(','):
        if '=' in part:
            key, value = part.split('=', 1)
            check(key not in result, 'duplicate diagnostic field')
            result[key] = int(value) if re.fullmatch(r'-?\d+', value) else value
    return result


def validate_config(config):
    check(config.get('schema') == 1, 'config schema must be1')
    check(isinstance(config.get('expected_version'), str) and config['expected_version'],
          'complete expected firmware version required')
    receivers = config.get('receivers')
    check(isinstance(receivers, list) and 1 <= len(receivers) <= 32, 'need1..32 receivers')
    nodes = [config.get('source')] + receivers
    labels, ports, identities = set(), set(), set()
    for node in nodes:
        check(isinstance(node, dict), 'missing explicit endpoint')
        check(re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', node.get('label', '')),
              'invalid endpoint label')
        port = node.get('port', '')
        check(port.startswith(('/dev/serial/by-id/', '/dev/serial/by-path/')),
              'stable by-id or by-path endpoint required')
        serial = node.get('usb_serial')
        physical = node.get('usb_location')
        check((isinstance(serial, str) and serial) or (isinstance(physical, str) and physical),
              'USB serial or explicit physical location required')
        check(serial or port.startswith('/dev/serial/by-path/'),
              'a serial-less endpoint must use by-path')
        check(isinstance(node.get('board_contains'), str) and node['board_contains'],
              'expected board label required')
        identity = ('serial', serial.upper()) if serial else ('location', physical)
        check(node['label'] not in labels and port not in ports and identity not in identities,
              'duplicate endpoint or physical identity')
        labels.add(node['label']); ports.add(port); identities.add(identity)
    check(config['source'].get('usb_serial', '').upper() == '28:84:85:B4:09:80',
          'this campaign requires the identified XIAO ESP32-S3 transmitter')
    for kind in ('voice', 'image'):
        fixture = config.get('media', {}).get(kind)
        check(isinstance(fixture, dict) and fixture.get('path') and fixture.get('sha256'),
              'explicit encoded media fixture and SHA256 required: ' + kind)
    return nodes


class Evidence:
    def __init__(self, folder, config):
        self.folder = folder
        folder.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.lock = threading.RLock()
        self.file = (folder / 'wire.private.jsonl').open('x', encoding='ascii', buffering=1)
        os.fchmod(self.file.fileno(), 0o600)
        self.frames = []
        self.report = {'schema': 1, 'started_utc': utc(), 'passed': False,
            'source_packets_requested': 0, 'expected_primary_packets': 40,
            'primary_rf': PRIMARY, 'media_rf': SECONDARY, 'nodes': {},
            'primary': {}, 'media': {}, 'restoration': {},
            'automatic_rf_retries': 0, 'limitations': [
                'Bench packet delivery does not establish receiver sensitivity.',
                'Media byte integrity does not prove phone playback or rendering.',
                'Different hardware paths and bench interference can affect delivery.',
                'No pre-existing contacts, media or channel messages are downloaded.']}
        save(folder / 'config.private.json', config)
        self.save()

    def event(self, kind, **values):
        with self.lock:
            row = {'kind': kind, 'monotonic': time.monotonic(), **values}
            self.file.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + '\n')
            if kind == 'rx': self.frames.append(row)
            return row

    def save(self):
        with self.lock: save(self.folder / 'report.private.json', self.report)


class Endpoint(Companion):
    """One exclusive reader; asynchronous evidence cannot be discarded by CLI."""
    def __init__(self, spec, evidence):
        from serial.tools import list_ports
        self.spec, self.label, self.evidence = spec, spec['label'], evidence
        resolved = os.path.realpath(spec['port'])
        matches = [p for p in list_ports.comports() if os.path.realpath(p.device) == resolved]
        check(len(matches) == 1, 'USB endpoint missing/ambiguous: ' + self.label)
        usb = matches[0]
        if spec.get('usb_serial'):
            check((usb.serial_number or '').upper() == spec['usb_serial'].upper(),
                  'USB serial changed: ' + self.label)
        if spec.get('usb_location'):
            check(usb.location == spec['usb_location'], 'USB physical location changed: ' + self.label)
        assert_port_idle(spec['port'])
        self.usb = usb
        super().__init__(spec['port'])
        self.condition = threading.Condition()
        self.responses, self.transaction = deque(), threading.Lock()
        self.failure, self.running, self.thread = None, False, None
        self.key = None

    def _read(self):
        try:
            while self.running:
                try: frame = self.reader.read_frame(self.port, .25)
                except FrameTimeout: continue
                self.evidence.event('rx', node=self.label, frame=frame.hex())
                if frame[0] < 0x80:
                    with self.condition:
                        check(len(self.responses) < 128, 'response queue exceeded bound')
                        self.responses.append(frame); self.condition.notify_all()
        except BaseException as error:
            with self.condition:
                self.failure = repr(error); self.condition.notify_all()

    def start(self, expected_version):
        # Match the established multi-Companion transport's JTAG DTR policy.
        self.port.rts = False
        self.port.dtr = self.spec.get('dtr', (self.usb.vid, self.usb.pid) != (0x303a, 0x1001))
        self.port.write(b'+++MESHCORE-TERM-STOP\r'); self.port.flush()
        end = time.monotonic() + .8
        discarded = bytearray()
        while time.monotonic() < end:
            chunk = self.port.read(min(self.port.in_waiting or 1, 4096))
            if chunk: discarded.extend(chunk)
            check(len(discarded) < 65536, 'unbounded terminal negotiation')
        check(b'set usb.logging off before Binary mode' not in discarded,
              'USB logging prevents Binary mode: ' + self.label)
        self.evidence.event('binary_negotiation', node=self.label,
                            drained=discarded.hex(), dtr=self.port.dtr)
        self.reader = DeviceFrameReader(TransportCounters())
        self.running = True
        self.thread = threading.Thread(target=self._read, name=self.label, daemon=True)
        self.thread.start()
        info = self.request(bytes((22, 14)), (13,))
        check(len(info) >= 80, 'incomplete DEVICE_INFO')
        self.max_channels = info[3]
        identity = self.request(make_app_start_spec('Companion-Fanout-HIL').payload, (5,))
        primary = validate_app_start_response(identity)
        self.key = identity[4:36]
        self.node_name = primary['node_name']
        version, board = self.cli('ver'), self.cli('board')
        check(version.startswith(expected_version + ' (Build: '),
              'full source/version differs: ' + self.label)
        check(self.spec['board_contains'].casefold() in board.casefold(),
              'board identity differs: ' + self.label)
        check(self.cli('get public.key').lower() == self.key.hex(), 'public-key handshake differs')
        return {'board': board, 'version': version, 'public_key': self.key.hex(),
                'primary': primary, 'max_channels': self.max_channels}

    def request(self, payload, expected, timeout=8):
        with self.transaction:
            with self.condition:
                check(not self.responses and not self.failure, 'unsynchronized session: ' + self.label)
                frame = encode_host_frame(payload)
                self.evidence.event('tx', node=self.label, payload=payload.hex())
                check(self.port.write(frame) == len(frame), 'incomplete Companion request')
                self.port.flush()
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and not self.failure:
                    if self.responses:
                        response = self.responses.popleft()
                        check(response[0] != 1, 'command rejected: ' + self.label + '/' + str(payload[0]))
                        check(response[0] in expected, 'unexpected response: ' + self.label)
                        return response
                    self.condition.wait(min(.1, max(0, deadline-time.monotonic())))
            raise RuntimeError('Companion request failed: ' + self.label + ': ' + str(self.failure))

    def cli_reply(self, command):
        # Explicitly preserve unsupported diagnostics; never hide other errors.
        frame = self.request(b'\x42' + command.encode('ascii'), (0x1d,), timeout=15)
        reply = frame[1:].decode('ascii', 'strict').strip()
        self.evidence.event('cli', node=self.label, command=command, reply=reply)
        return reply

    def cli(self, command):
        reply = self.cli_reply(command)
        check(not re.match(r'(?:(?:Error|ERROR|ERR|Err|Unknown)\b|\?\?)', reply),
              'CLI rejected ' + command + ': ' + self.label + ': ' + reply)
        return stripped(reply)

    def stats(self):
        return packet_stats(self.request(bytes((56, 2)), (24,)))

    def drain(self):
        messages = []
        for _ in range(256):
            frame = self.request(bytes((10,)), (10, 16, 17, 27))
            if frame[0] == 10:
                check(len(frame) == 1, 'malformed end-of-queue response')
                return messages
            messages.append(frame)
        raise RuntimeError('application queue did not drain within256 messages')

    def close(self):
        self.running = False
        if self.thread: self.thread.join(2)
        super().close()


def parallel(nodes, function):
    with ThreadPoolExecutor(max_workers=len(nodes)) as executor:
        futures = {node.label: executor.submit(function, node) for node in nodes}
        return {label: future.result() for label, future in futures.items()}


def pulse(node):
    replies = {command: node.cli_reply(command) for command in PULSE_PAGES}
    if stripped(replies[PULSE_PAGES[0]]) in ('Error: unsupported', 'ERROR: unsupported'):
        check(all('unsupported' in value.lower() for value in replies.values()),
              'inconsistent diagnostic support')
        return {'supported': False, 'raw': replies}
    status = fields(replies[PULSE_PAGES[0]])
    check(status.get('v') == 2 and status.get('method') == 'rxps', 'unexpected AGC status')
    return {'supported': True, 'raw': replies, 'status': status,
            'timing': fields(replies[PULSE_PAGES[1]]),
            'pulse': fields(replies[PULSE_PAGES[3]]),
            'sleep': fields(replies[PULSE_PAGES[4]]),
            'checks': fields(replies[PULSE_PAGES[5]])}


def configure(node, source=False):
    for command in ('set agc 0', 'set powersaving off', 'set radio2 off',
                    'set radio2.cross off'):
        node.cli(command)
    rxps = node.cli_reply('get radio.rxps')
    if 'unsupported' not in rxps.lower(): node.cli('set radio.rxps off')
    old = node.cli_reply('get agc.old')
    if 'unsupported' not in old.lower(): node.cli('set agc.old 0')
    node.cli('set radio 906.525,62.5,7,5,16')
    node.request(bytes((11,)) + struct.pack('<IIBBB', 906525, 62500, 7, 5, 0), (0,))
    if source:
        node.request(bytes((12,)) + struct.pack('b', -9), (0,))
        check(node.cli('get tx') == '-9', 'source TX power differs')
    node.cli('set tx.media radio2')
    node.cli('set tx.channel 0 radio')
    node.cli('set agc ' + ('0' if source else '16'))
    check(node.cli('get agc') == ('0' if source else '16'), 'saved AGC differs')
    rxps = node.cli_reply('get radio.rxps')
    check('unsupported' in rxps.lower() or stripped(rxps).startswith('off,'),
          'ordinary RXPS is enabled or its readback is invalid')
    # APP_START is the actual active primary readback, not only a saved ACK.
    identity = node.request(make_app_start_spec('Companion-Fanout-HIL').payload, (5,))
    actual = validate_app_start_response(identity)
    check((actual['frequency_khz'], actual['bandwidth_hz'], actual['spreading_factor'],
           actual['coding_rate']) == (906525, 62500, 7, 5), 'active RF tuple differs')
    return {'radio': node.cli('get radio'), 'rxps': rxps,
            'agc': node.cli('get agc'), 'old': node.cli_reply('get agc.old')}


def primary_results(evidence, run, receivers, before, after):
    result = {}
    for node in receivers:
        seqs = {}; malformed = 0
        for row in evidence.frames:
            if row['node'] != node.label: continue
            frame = bytes.fromhex(row['frame'])
            if not frame or frame[0] != 0x88: continue
            probe = parse_probe(frame[3:], expected_run=run)
            if probe is None: continue
            if probe.source != 'L' or not 0 <= probe.sequence < 40:
                malformed += 1; continue
            expected = make_probe(run, 'L', probe.sequence, direct=True,
                                  payload_size=40 if probe.sequence % 2 == 0 else 160)
            if frame[3:] != expected:
                malformed += 1; continue
            snr, rssi = struct.unpack('bb', frame[1:3])
            seqs.setdefault(probe.sequence, []).append({'snr_db': snr/4, 'rssi_dbm': rssi})
        seen = sorted(seqs)
        result[node.label] = {'received': len(seen), 'sent': 40,
            'missing_sequences': [i for i in range(40) if i not in seqs],
            'duplicate_observations': sum(len(v)-1 for v in seqs.values()),
            'invalid_matching_run_observations': malformed,
            'received_sequences': seen, 'per_sequence': seqs,
            'packet_counter_delta': counter_delta(before[node.label], after[node.label]),
            'snr_median_db': statistics.median(v[0]['snr_db'] for v in seqs.values()) if seqs else None,
            'rssi_median_dbm': statistics.median(v[0]['rssi_dbm'] for v in seqs.values()) if seqs else None}
    return result


def media_frames(node, slot, *, allow_text=False):
    messages = node.drain()
    values = []
    for frame in messages:
        if frame[0] == 27:
            parsed = parse_channel_frame(frame)
            check(parsed is not None and parsed['channel'] == slot and parsed['data_type'] == 0x5643,
                  'unexpected media queue channel')
            values.append(parsed)
        elif frame[0] == 17 and allow_text:
            parsed = parse_group_text_response(frame)
            check(parsed is not None and parsed['channel'] == slot,
                  'unexpected announcement channel')
            values.append(parsed)
        else:
            # This run uses freshly erased fixtures; downloading other content
            # is a protocol/scope failure and is never silently counted.
            raise RuntimeError('unexpected application queue message: ' + node.label)
    return values


class MediaGather:
    """Drain one clip's queues to fixed deadlines; never send or retry RF."""
    def __init__(self, source, receivers, bundle, slot, evidence, timestamp, manifest):
        self.source, self.nodes = source, [source] + list(receivers)
        self.bundle, self.slot, self.evidence = bundle, slot, evidence
        self.timestamp, self.manifest = timestamp, manifest
        self.announcement = source.node_name + ': ' + bundle['envelope']
        self.fetches = {}
        self.rows = {node.label: {'announcement': [], 'manifest': [], 'fetch': [], 'chunk': []}
                     for node in self.nodes}
        self.errors = {node.label: [] for node in self.nodes}
        self.seen = {node.label: set() for node in self.nodes}

    def observe(self, node, frame, phase):
        kind, identity, parsed, error = None, None, None, None
        if frame and frame[0] == 17:
            parsed = parse_group_text_response(frame)
            if (parsed is not None and node is not self.source
                    and parsed['channel'] == self.slot and parsed['profileTag'] == 0
                    and parsed['text'] == self.announcement
                    and parsed['timestamp'] == self.timestamp):
                kind, identity = 'announcement', 'announcement'
            else:
                error = 'unexpected announcement identity, channel, timestamp or tag'
        elif frame and frame[0] == 27:
            parsed = parse_channel_frame(frame)
            if (parsed is None or parsed['channel'] != self.slot
                    or parsed['data_type'] != 0x5643 or parsed['radio_profile'] != 1):
                error = 'unexpected media channel, type or profile'
            elif parsed['data'] == self.manifest and node is not self.source:
                kind, identity = 'manifest', 'manifest'
            elif parsed['data'] in self.fetches:
                kind, identity = 'fetch', 'fetch:' + self.fetches[parsed['data']]
            elif parsed['data'] in self.bundle['chunks'] and node is not self.source:
                kind, identity = 'chunk', 'chunk:' + str(parse_chunk(parsed['data'])['index'])
            else:
                error = 'unexpected media SID, manifest hash, fetch identity or chunk bytes'
        else:
            error = 'unexpected application queue message'
        # Retain exact wire bytes, including foreign and duplicate observations.
        # They cannot inflate success or be silently reclassified as clip data.
        duplicate = identity is not None and identity in self.seen[node.label]
        self.evidence.event('media_observation', node=node.label, phase=phase,
                            classification=kind, frame=frame.hex(), error=error,
                            duplicate=duplicate)
        if error:
            self.errors[node.label].append(error)
            # Keep the original chunk diagnostics even for a rejected profile
            # or corrupt packet; reassembly must not conceal those observations.
            if parsed and 'data' in parsed and parse_chunk(parsed['data']) is not None:
                self.rows[node.label]['chunk'].append(parsed)
            return
        self.rows[node.label][kind].append(parsed)
        if duplicate:
            self.errors[node.label].append('duplicate media observation: ' + identity)
        self.seen[node.label].add(identity)

    def gather(self, phase):
        started = time.monotonic()
        deadline = started + MEDIA_GATHER_SECONDS
        # Completion does not shorten the window: a late duplicate still
        # matters. Every poll drains all queues so small receivers do not fill.
        while time.monotonic() < deadline:
            queues = parallel(self.nodes, lambda node: node.drain())
            for node in self.nodes:
                for frame in queues[node.label]: self.observe(node, frame, phase)
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
        self.evidence.event('media_gather', phase=phase, started=started,
                            deadline=deadline, finished=time.monotonic())


def send_media(source, receivers, bundle, slot, evidence):
    timestamp = int(time.time())
    # A manifest is an explicitly typed channel datagram, so routing and queue
    # retrieval are tested without pretending that its bytes are a photograph.
    manifest = {'schema': 1, 'kind': bundle['kind'], 'sid': bundle['sid'],
                'bytes': bundle['content_bytes'], 'chunks': bundle['count'],
                'sha256': bundle['sha256']}
    manifest_bytes = b'HIL1' + json.dumps(manifest, separators=(',', ':'), sort_keys=True).encode('ascii')
    # The full SHA manifest is saved host-side. Keep the on-air manifest within
    # the common163-byte application budget and bind it by SHA256 too.
    check(len(manifest_bytes) <= 163, 'manifest exceeds common application budget')
    gather = MediaGather(source, receivers, bundle, slot, evidence, timestamp, manifest_bytes)
    source.request(group_text_command(slot, bundle['envelope'], timestamp), (0,))
    gather.gather('announcement')
    source.request(channel_data_command(slot, manifest_bytes), (0,))
    gather.gather('manifest-before-fetch')
    # Each receiver pulls the complete clip explicitly. These are distinct
    # requester-specific requests, never automatic replacements for lost RF.
    request_results = {}
    for node in receivers:
        request = encode_fetch(bundle['sid'], node.key[:6])
        gather.fetches[request] = node.label
        node.request(channel_data_command(slot, request), (0,))
        gather.gather('fetch:' + node.label)
        request_results[node.label] = {'sha256': hashlib.sha256(request).hexdigest()}
    for index, chunk in enumerate(bundle['chunks']):
        source.request(channel_data_command(slot, chunk), (0,))
        gather.gather('chunks-final' if index == bundle['count'] - 1 else 'chunk:' + str(index))
    outcome = {}
    for node in receivers:
        frames = gather.rows[node.label]
        announcement_exact = len(frames['announcement']) == len(frames['manifest']) == 1
        request = encode_fetch(bundle['sid'], node.key[:6])
        request_results[node.label]['received_exactly'] = bool(
            sum(item['data'] == request for item in gather.rows[source.label]['fetch']) == 1
            and not gather.errors[source.label])
        request_results[node.label]['source_errors'] = gather.errors[source.label]
        evidence.report['media'][bundle['kind']]['observations'][node.label].extend(
            {'data_hex': frame['data'].hex(), 'radio_profile': frame['radio_profile'],
             'snr_db': frame['snr_db']} for frame in frames['chunk'])
        observations = evidence.report['media'][bundle['kind']]['observations'][node.label]
        chunks = [bytes.fromhex(item['data_hex']) for item in observations]
        try:
            check(not gather.errors[node.label], '; '.join(gather.errors[node.label]))
            check(all(item['radio_profile'] == 1 for item in observations), 'clip arrived on wrong profile')
            content = assemble(bundle, chunks)
            outcome[node.label] = {'integrity_passed': announcement_exact
                and request_results[node.label]['received_exactly'], 'retrieved_bytes': len(content),
                'retrieved_sha256': hashlib.sha256(content).hexdigest(),
                'fetch': request_results[node.label], 'announcement_and_manifest_exact':
                    announcement_exact, 'chunks_received': len(chunks)}
        except (ValueError, RuntimeError) as error:
            outcome[node.label] = {'integrity_passed': False, 'chunks_received': len(chunks),
                                   'error': str(error)}
    return outcome


def raw_media_check(source, receivers, bundle, evidence):
    """Exercise the other application API, including recognized raw fetches."""
    observations = {}
    chunk = parse_chunk(bundle['chunks'][0])
    payload = encode_chunk(bundle['sid'], chunk['index'], chunk['data'], salt=1)
    started = time.monotonic()
    source.request(bytes((25, 0)) + payload, (0,))
    # The continuous USB reader retains RAW_DATA pushes while RF delivery is
    # pending. Use the same fixed deadline as the queued-media phases.
    time.sleep(MEDIA_GATHER_SECONDS)
    for node in receivers:
        matches = [row for row in evidence.frames if row['node'] == node.label
            and row['monotonic'] >= started and bytes.fromhex(row['frame'])[0] == 0x84
            and bytes.fromhex(row['frame'])[4:] == payload]
        observations[node.label] = {'raw_clip_received': len(matches),
            'raw_clip_profile1': bool(matches and all(bytes.fromhex(row['frame'])[3] == 0xa1
                                                     for row in matches))}
    for node in receivers:
        request = encode_fetch(bundle['sid'], node.key[:6], missing=[0])
        started = time.monotonic()
        node.request(bytes((25, 0)) + request, (0,))
        time.sleep(MEDIA_GATHER_SECONDS)
        matches = [row for row in evidence.frames if row['node'] == source.label
            and row['monotonic'] >= started and bytes.fromhex(row['frame'])[0] == 0x84
            and bytes.fromhex(row['frame'])[4:] == request]
        observations[node.label].update(raw_fetch_received_by_source=len(matches),
            raw_fetch_profile1=bool(matches and all(bytes.fromhex(row['frame'])[3] == 0xa1
                                                   for row in matches)))
    return observations


def run(config, folder):
    specs = validate_config(config)
    fixtures = {}
    for index, kind in enumerate(('voice', 'image')):
        item = config['media'][kind]
        content = Path(item['path']).read_bytes()
        check(hashlib.sha256(content).hexdigest() == item['sha256'], 'fixture SHA256 differs')
        fixtures[kind] = media_bundle(kind, content, secrets.randbits(32),
            mode=item.get('mode', 1), duration=item.get('duration', 2), aspect=item.get('aspect', 100))
    evidence = Evidence(folder, config)
    nodes = []
    try:
        for spec in specs:
            node = Endpoint(spec, evidence); nodes.append(node)
            evidence.report['nodes'][node.label] = node.start(config['expected_version'])
        source, receivers = nodes[0], nodes[1:]
        check(len({node.key for node in nodes}) == len(nodes), 'duplicate application public keys')
        for node in nodes:
            evidence.report['nodes'][node.label]['configured'] = configure(node, node is source)
            # Freshly erased devices must not contain somebody else's messages.
            check(node.drain() == [], 'application queue was not initially empty')
        time.sleep(3)
        before = parallel(nodes, lambda node: node.stats())
        pulse_before = parallel(receivers, pulse)
        run_id = secrets.token_bytes(8)
        evidence.report['run_id'] = run_id.hex()
        evidence.report['pulse_before'] = pulse_before
        primary_started = time.monotonic()
        last = 0.0
        for sequence in range(40):
            time.sleep(max(0, last + 1.0 - time.monotonic()))
            wire = make_probe(run_id, 'L', sequence, direct=True,
                              payload_size=40 if sequence % 2 == 0 else 160)
            last = time.monotonic()
            evidence.event('primary_request', sequence=sequence, wire=wire.hex())
            source.request(bytes((65, 0)) + wire, (0,))
            evidence.report['source_packets_requested'] += 1
        # Fixed extra listening time permits at least three scheduled16s
        # opportunities without adding or replacing any of the40 RF probes.
        time.sleep(max(3, primary_started + 51 - time.monotonic()))
        after = parallel(nodes, lambda node: node.stats())
        pulse_after = parallel(receivers, pulse)
        evidence.report['primary'] = primary_results(evidence, run_id, receivers, before, after)
        evidence.report['source_packet_delta'] = counter_delta(before[source.label], after[source.label])
        check(evidence.report['source_packet_delta']['sent'] == 40,
              'source did not complete exactly40 physical transmissions')
        evidence.report['pulse_after'] = pulse_after
        exposure = {}
        for node in receivers:
            old, new = pulse_before[node.label], pulse_after[node.label]
            check(old['supported'] == new['supported'], 'AGC diagnostic support changed')
            if new['supported']:
                completed = new['status']['ok'] - old['status']['ok']
                failed = new['status']['fail'] - old['status']['fail']
                witnesses = {'cycles': new['pulse']['cycles'] - old['pulse']['cycles'],
                    'rtc': new['checks']['rtc'] - old['checks']['rtc'],
                    'verify': new['checks']['verify'] - old['checks']['verify']}
                exposure[node.label] = {'observed': True, 'completed': completed, 'failed': failed,
                    'natural_witness_deltas': witnesses,
                    'minimum_required': 2, 'passed': completed >= 2 and failed == 0
                        and min(witnesses.values()) >= 2}
            else:
                exposure[node.label] = {'observed': False,
                    'reason': 'firmware has no supported pulse diagnostics; configured interval is not execution proof'}
        evidence.report['agc_exposure'] = exposure
        evidence.save()
        for node in nodes:
            # Stop periodic maintenance before enabling the second profile;
            # this is a media routing/integrity check, not an AGC experiment.
            node.cli('set agc 0')
            node.cli('set radio2 908.150,250,7,5,rxtx,auto')
            node.cli('set radio2.cross off')
            node.cli('set tx.media radio2')
        time.sleep(3)
        slot = int(config.get('channel_slot', 1))
        check(all(slot < node.max_channels for node in nodes), 'media channel exceeds capacity')
        name = ('LabMedia-' + run_id.hex()[:8]).encode('ascii').ljust(32, b'\0')
        key = secrets.token_bytes(16)
        for node in nodes:
            node.request(bytes((32, slot)) + name + key, (0,))
            node.cli('set tx.channel ' + str(slot) + ' radio2')
            check(node.drain() == [], 'queue not empty before media phase')
        for kind, bundle in fixtures.items():
            evidence.report['media'][kind] = {'fixture_sha256': bundle['sha256'],
                'envelope': bundle['envelope'], 'chunks': bundle['count'],
                'content_bytes': bundle['content_bytes'],
                'observations': {node.label: [] for node in receivers}}
            evidence.report['media'][kind]['outcomes'] = send_media(source, receivers, bundle,
                                                                        slot, evidence)
            evidence.report['media'][kind]['raw_api'] = raw_media_check(source, receivers,
                                                                      bundle, evidence)
            evidence.save()
        evidence.report['passed'] = (
            all(row['received'] == 40 and not row['invalid_matching_run_observations']
                for row in evidence.report['primary'].values())
            and all(not row.get('observed') or row['passed'] for row in exposure.values())
            and all(row['integrity_passed'] for clip in evidence.report['media'].values()
                    for row in clip['outcomes'].values())
            and all(row['raw_clip_received'] == 1 and row['raw_clip_profile1']
                    and row['raw_fetch_received_by_source'] == 1 and row['raw_fetch_profile1']
                    for clip in evidence.report['media'].values() for row in clip['raw_api'].values()))
    except BaseException as error:
        evidence.report['failure'] = {'type': type(error).__name__, 'detail': str(error)}
        raise
    finally:
        for node in nodes:
            result = {'passed': False}
            try:
                for command in ('set agc 0', 'set radio2 off', 'set radio2.cross off',
                                'set tx.media auto', 'set agc 16'):
                    node.cli(command)
                old = node.cli_reply('get agc.old')
                if 'unsupported' not in old.lower(): node.cli('set agc.old 0')
                result.update(passed=node.cli('get agc') == '16',
                              agc=node.cli('get agc'), old=node.cli_reply('get agc.old'),
                              radio2=node.cli('get radio2'), rxps=node.cli_reply('get radio.rxps'))
            except BaseException as error:
                result['failure'] = repr(error)
            evidence.report['restoration'][node.label] = result
            node.close()
        evidence.report['passed'] &= (len(nodes) == len(specs) and
            all(row['passed'] for row in evidence.report['restoration'].values()))
        evidence.report['finished_utc'] = utc()
        evidence.save(); evidence.file.close()
    return evidence.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    nodes = validate_config(config)
    if not args.execute:
        print(json.dumps({'inert': True, 'endpoints': [n['label'] for n in nodes],
            'primary': PRIMARY, 'secondary': SECONDARY, 'source_tx_power_dbm': -9,
            'primary_packets': 40, 'automatic_rf_retries': 0,
            'media': 'two finite manifest/fetch/chunk integrity transfers; no playback claim'}, indent=2))
        return
    report = run(config, args.output)
    print(json.dumps({'passed': report['passed'], 'report': str(args.output/'report.private.json')}))
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__':
    main()
