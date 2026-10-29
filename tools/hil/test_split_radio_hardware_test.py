"""No-hardware checks for split-repeater runner framing and RF verdicts."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import split_radio_hardware_test as runner
from split_radio_probe import make_probe


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Condition:
    def __init__(self, clock):
        self.clock = clock

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def wait(self, seconds):
        self.clock.sleep(seconds)


class SplitRadioHardwareRunnerTests(unittest.TestCase):
    def test_physical_radio_parser_accepts_irq_tx_state_and_rejects_partial_reply(self):
        parsed = runner.parse_radio_diagnostic(
            'state=19(INT_READY), recv=12, sent=4294967295, errors=0, err_flags=0, '
            'outbound=yes, last_rx=0s ago')
        self.assertTrue(parsed['tx_wait'])
        self.assertEqual(parsed['sent'], 0xffffffff)
        self.assertTrue(parsed['outbound'])
        for reply in ('state=3(TX_WAIT), sent=0',
                      'state=3(TX_WAIT), recv=0, sent=-1, errors=0, err_flags=0, outbound=no, last_rx=0s ago'):
            with self.subTest(reply=reply), self.assertRaises(RuntimeError):
                runner.parse_radio_diagnostic(reply)

    def test_cad_order_requires_physical_idle_gap_after_blocker_tx_done(self):
        def sample(node, sent, tx=False):
            return {'node': node, 'sent': sent, 'tx_wait': tx, 'reply_completed_monotonic': 4}

        blocked = sample('blocker', 10, True)
        completed = sample('blocker', 11)
        center_idle, center_tx, center_done = (sample('center', 20), sample('center', 20, True),
                                               sample('center', 21))
        for samples, expected in (
            ([blocked, center_idle, blocked, completed, center_idle, center_tx, center_done], 'passed'),
            ([blocked, center_tx, blocked, completed, center_done], 'failed'),
            # TX_DONE notification order cannot tell whether TX began before
            # the blocker completed. An observer's later USB report cannot
            # supply the missing physical idle gap.
            ([blocked, completed, center_done], 'inconclusive'),
            ([blocked, completed, center_idle, center_done, sample('center', 22)], 'inconclusive')):
            with self.subTest(expected=expected):
                result = runner.assess_cad_samples(samples, 10, 20, blocker_minimum_end=17)
                self.assertEqual(result['verdict'], expected)
                self.assertEqual(result['simultaneous_tx_proven'], expected == 'failed')
        uncertain = runner.assess_cad_samples([blocked, center_tx, blocked, completed, center_done], 10, 20)
        self.assertEqual(uncertain['verdict'], 'inconclusive')
        self.assertTrue(uncertain['driver_tx_wait_overlap'])
        self.assertFalse(uncertain['simultaneous_tx_proven'])

    def test_cad_order_handles_wrapped_physical_counters_and_rejects_reboots(self):
        samples = [{'node': 'blocker', 'sent': 0xffffffff, 'tx_wait': True},
                   {'node': 'blocker', 'sent': 0, 'tx_wait': False},
                   {'node': 'center', 'sent': 0xffffffff, 'tx_wait': False},
                   {'node': 'center', 'sent': 0, 'tx_wait': False}]
        self.assertEqual(runner.assess_cad_samples(samples, 0xffffffff, 0xffffffff)['verdict'], 'passed')
        self.assertEqual(runner.assess_cad_samples(samples, 12, 13)['verdict'], 'inconclusive')

    def test_configured_fixture_preamble_applies_to_primary_radio2_and_restoration(self):
        class Evidence:
            report = {'nodes': {label: {} for label in runner.FIXTURES}}

            def event(self, *args, **kwargs):
                pass

            def save(self):
                pass

        class Fixture:
            def __init__(self):
                self.commands = []
                self.radio = ''

            def cli(self, command):
                self.commands.append(command)
                if command.startswith('set radio '):
                    self.radio = ','.join(command[10:].split(',')[:4])
                if command == 'get radio':
                    return self.radio
                if command == 'get tx':
                    return '-5'
                return 'off'

            def request(self, *args):
                return b'\0'

            def stats(self):
                return {}

        campaign = runner.Campaign({'fixture_preamble': 256}, Evidence())
        campaign.nodes = {label: Fixture() for label in runner.FIXTURES}
        with patch.object(runner.time, 'sleep'):
            campaign.configure_companions()
        for node in campaign.nodes.values():
            self.assertTrue(any(command.startswith('set radio ') and command.endswith(',256')
                                for command in node.commands))
        self.assertIn('set radio2 902.750,250,7,5,rx,256', campaign.nodes['observe_inputs'].commands)
        campaign.set_fixture_rf('feed_left', runner.RF['output_usa'], preamble=8192)
        campaign.set_fixture_rf('feed_left', runner.RF['input_media'])
        self.assertIn('set radio 906.525,62.5,7,5,8192', campaign.nodes['feed_left'].commands)
        self.assertEqual(campaign.nodes['feed_left'].commands[-2], 'set radio 902.750,250,7,5,256')

    def test_radio2_barrier_waits_for_runtime_disable_not_saved_preference(self):
        clock = Clock()

        class Evidence:
            report = {}

            def event(self, kind, **kwargs):
                return {'kind': kind, **kwargs}

            def save(self):
                pass

        class Center:
            commands = []

            def cli(self, command, timeout=15):
                self.commands.append(command)
                if command == 'get radio2':
                    return 'off (manual)'  # Already saved, but not yet live.
                if command == 'get radio2.status':
                    return ('rxtx' if clock.now < 2 else 'off')+'; RX=0,0 TX=0,0'
                if command == 'get radio2.scan':
                    return 'off' if clock.now >= 2 else 'slow=radio'
                raise AssertionError(command)

        campaign = runner.Campaign({}, Evidence())
        campaign.nodes['center'] = Center()
        with patch.object(runner.time, 'monotonic', clock.monotonic), \
                patch.object(runner.time, 'sleep', clock.sleep):
            campaign.wait_radio2_mode('off')
        self.assertGreaterEqual(clock.now, 2)
        self.assertNotIn('get radio2', campaign.nodes['center'].commands)
        self.assertEqual(campaign.evidence.report['profile_barriers'][0]['mode'], 'off')

    def test_infrastructure_batch_keeps_only_one_port_and_closes_on_error(self):
        clock = Clock()
        port = Mock()
        port.in_waiting = 128
        port.read.side_effect = [b'ver\r\n  -> test firmware\r\n',
                                 b'ver\r\n  -> test firmware\r\n',
                                 b'bad\r\n  -> Err - invalid\r\n']
        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.Evidence(Path(temporary)/'run')
            try:
                with patch.object(runner, 'verify_usb') as identity, \
                        patch('serial.Serial', return_value=port) as create, \
                        patch.object(runner, 'open_configured_session') as opened, \
                        patch.object(runner.time, 'monotonic', clock.monotonic), \
                        patch.object(runner.time, 'sleep', clock.sleep):
                    node = runner.Infrastructure('center', {'port': '/test'}, evidence)
                    with node.batch():
                        self.assertEqual(node.cli('ver'), 'test firmware')
                        self.assertEqual(node.cli('ver'), 'test firmware')
                        port.close.assert_not_called()
                        self.assertIs(node.port, port)
                        with self.assertRaises(RuntimeError):
                            with node.batch():
                                self.fail('nested batch was allowed')
                    self.assertIsNone(node.port)
                    self.assertFalse(node._batch_active)
                    self.assertEqual(create.call_count, 1)
                    self.assertEqual(opened.call_count, 1)
                    port.close.assert_called_once()
                    self.assertEqual(identity.call_count, 2)
                    with self.assertRaises(RuntimeError):
                        with node.batch():
                            node.cli('bad')
                    self.assertIsNone(node.port)
                    self.assertFalse(node._batch_active)
                    self.assertEqual(port.close.call_count, 2)
                    self.assertFalse(node.failure)  # Complete error reply, not a lost transaction.
            finally:
                evidence.close()

    def test_infrastructure_opens_only_for_one_transaction_and_closes_on_timeout(self):
        clock = Clock()
        ports = []

        class Port:
            def __init__(self, **kwargs):
                self.dtr = self.rts = None
                self.response = b''
                self.closed = False
                self.writes = []
                ports.append(self)

            @property
            def in_waiting(self):
                return len(self.response)

            def write(self, data):
                self.writes.append(data)
                if data == b'ver\r':
                    self.response = b'ver\r\n  -> test firmware\r\n'
                return len(data)

            def flush(self):
                pass

            def read(self, count):
                clock.sleep(.1)
                data, self.response = self.response[:count], self.response[count:]
                return data

            def close(self):
                self.closed = True

        def safe_open(port):
            self.assertFalse(port.dtr)
            self.assertFalse(port.rts)

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.Evidence(Path(temporary)/'run')
            try:
                with patch.object(runner, 'verify_usb') as identity, \
                        patch('serial.Serial', Port), patch.object(runner, 'open_configured_session', safe_open), \
                        patch.object(runner.time, 'monotonic', clock.monotonic), \
                        patch.object(runner.time, 'sleep', clock.sleep):
                    node = runner.Infrastructure('right', {'port': '/test'}, evidence)
                    self.assertEqual(ports, [])
                    self.assertIsNone(node.port)
                    self.assertEqual(node.cli('ver'), 'test firmware')
                    self.assertTrue(ports[0].closed)
                    self.assertIsNone(node.port)
                    with self.assertRaises(TimeoutError):
                        node.cli('get radio', timeout=.5)
                    self.assertTrue(ports[1].closed)
                    self.assertIsNone(node.port)
                    self.assertTrue(node.failure)
                    self.assertEqual(identity.call_count, 3)
                    self.assertEqual(evidence.report['last_serial_operation']['node'], 'right')
                    self.assertEqual(ports[0].writes, [b'\r', b'ver\r'])
            finally:
                evidence.close()

    def test_infrastructure_reboot_opens_its_own_session_and_checks_application_reply(self):
        clock = Clock()
        ports = []

        class Port:
            def __init__(self, **kwargs):
                self.response, self.writes, self.closed = b'', [], False
                ports.append(self)

            @property
            def in_waiting(self):
                return len(self.response)

            def write(self, data):
                self.writes.append(data)
                if data == b'ver\r':
                    self.response = b'ver\r\n  -> test firmware\r\n'
                return len(data)

            def flush(self):
                pass

            def read(self, count):
                clock.sleep(.1)
                data, self.response = self.response[:count], self.response[count:]
                return data

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.Evidence(Path(temporary)/'run')
            try:
                with patch.object(runner, 'verify_usb'), patch('serial.Serial', Port), \
                        patch.object(runner, 'open_configured_session'), \
                        patch.object(runner.time, 'monotonic', clock.monotonic), \
                        patch.object(runner.time, 'sleep', clock.sleep):
                    node = runner.Infrastructure('center', {'port': '/test'}, evidence)
                    node.reboot()
                    self.assertEqual(len(ports), 2)
                    self.assertIn(b'reboot\r', ports[0].writes)
                    self.assertIn(b'ver\r', ports[1].writes)
                    self.assertTrue(all(port.closed for port in ports))
                    self.assertIsNone(node.port)
                    self.assertFalse(node.failure)
            finally:
                evidence.close()

    def test_dual_input_witness_does_not_invent_a_configured_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.Evidence(Path(temporary)/'run')
            try:
                wire = make_probe(evidence.run, 'R', 1)
                evidence.receive('observe_inputs', b'\x88\x00\x9c'+wire)
                record = evidence.observations[0]
                self.assertIsNone(record['configured_profile'])
                self.assertIsNone(record['radio_profile'])
                self.assertEqual(record['wire_hex'], wire.hex())
            finally:
                evidence.close()

    def test_negative_trial_observes_full_horizon_and_catches_late_output(self):
        clock = Clock()

        class Evidence:
            run = bytes(range(8))
            condition = Condition(clock)
            report = {'trials': []}

            def event(self, *args, **kwargs):
                pass

            def records(self, source, sequence):
                wire = make_probe(self.run, source, sequence)
                records = []
                if clock.now >= .3:
                    records.append({'observer': 'observe_inputs', 'wire_hex': wire.hex()})
                if clock.now >= 5:
                    records.append({'observer': 'observe_usa', 'wire_hex': wire.hex()})
                return records

            def save(self):
                pass

        class Feeder:
            def request(self, command, expected):
                self.command = command
                return b'\0'

        campaign = runner.Campaign({'probe_pause': 1, 'probe_timeout': 6}, Evidence())
        campaign.nodes = {'feed_left': Feeder()}
        campaign.center_key = bytes(range(32))
        with patch.object(runner.time, 'monotonic', clock.monotonic), \
                patch.object(runner.time, 'sleep', clock.sleep):
            result = campaign.probe('L', expected_forward=False)
        self.assertGreaterEqual(result['elapsed_seconds'], 6)
        self.assertFalse(result['passed'])
        self.assertEqual(result['wrong_channel_observations'], 1)

    def test_positive_trial_does_not_pass_late_delivery_during_drain(self):
        clock = Clock()
        center_key = bytes(range(32))

        class Evidence:
            run = bytes(range(8))
            condition = Condition(clock)
            report = {'trials': []}

            def event(self, *args, **kwargs):
                pass

            def records(self, source, sequence):
                original = make_probe(self.run, source, sequence)
                forward = original[:1]+b'\x01'+center_key[:1]+original[2:]
                records = [{'observer': 'observe_inputs', 'wire_hex': original.hex(), 'monotonic': .2}]
                if clock.now >= 6.5:
                    records.append({'observer': 'observe_media', 'wire_hex': forward.hex(), 'monotonic': 6.5})
                return records

            def save(self):
                pass

        campaign = runner.Campaign({'probe_pause': 1, 'probe_timeout': 6}, Evidence())
        campaign.nodes = {'feed_left': Mock()}
        campaign.center_key = center_key
        with patch.object(runner.time, 'monotonic', clock.monotonic), \
                patch.object(runner.time, 'sleep', clock.sleep):
            result = campaign.probe('L')
        self.assertFalse(result['passed'])
        self.assertEqual(result['delivery_result'], 'late_output')
        self.assertEqual(result['output_notification_seconds'], 6.5)
        self.assertEqual(result['input_to_output_notification_seconds'], 6.3)
        self.assertEqual(result['correct_center_outputs'], 1)
        self.assertEqual(len(result['observations']), 2)

    def test_optional_hop_diagnostics_keep_per_hop_counters_and_collection_interval(self):
        clock = Clock()

        class Evidence:
            report = {}
            events = []

            def event(self, kind, **fields):
                self.events.append({'kind': kind, **fields})

        class Node:
            commands = None

            def __init__(self):
                self.commands = []

            def packet_stats(self):
                clock.sleep(.1)
                return {'recv': 7, 'sent': 3}

            def cli(self, command):
                self.commands.append(command)
                clock.sleep(.1)
                if command == 'stats-radio-diag':
                    return ('state=0(IDLE), recv=7, sent=3, errors=0, err_flags=0, '
                            'outbound=no, last_rx=0s ago')
                return command+' counters=1'

        campaign = runner.Campaign({'per_probe_diagnostics': True}, Evidence())
        campaign.nodes = {'left': Node(), 'right': Node(), 'center': Node()}
        with patch.object(runner.time, 'monotonic', clock.monotonic):
            result = campaign.hop_diagnostics('L', 3, 'before')
        self.assertEqual(set(result['nodes']), {'left', 'center'})
        self.assertEqual(result['nodes']['left']['packets'], {'recv': 7, 'sent': 3})
        self.assertIn('espnow_rx', result['nodes']['center'])
        self.assertIn('espnow_tx', result['nodes']['left'])
        self.assertIn('espnow_drops', result['nodes']['left'])
        self.assertIn('cad', result['nodes']['center'])
        self.assertGreater(result['collection_seconds'], 1)
        self.assertEqual(campaign.nodes['right'].commands, [])
        self.assertEqual(len(campaign.evidence.events), 1)
        campaign.config['per_probe_diagnostics'] = False
        self.assertIsNone(campaign.hop_diagnostics('L', 4, 'before'))
        self.assertEqual(len(campaign.evidence.events), 1)

    def test_fixture_only_open_never_touches_a_dut(self):
        class Evidence:
            report = {'nodes': {}}

            def save(self):
                pass

        class Companion:
            def __init__(self, label, spec, evidence):
                self.label = label

            def start(self):
                return {'model': 'test fixture'}

            def cli(self, command):
                return 'test firmware'

            def close(self):
                pass

        config = {'nodes': {label: {'board_contains': 'test fixture'} for label in runner.FIXTURES}}
        campaign = runner.Campaign(config, Evidence())
        with patch.object(runner, 'CaptureCompanion', Companion), \
                patch.object(runner, 'Infrastructure', side_effect=AssertionError('DUT opened')):
            campaign.open(companions_only=True)
            self.assertEqual(set(campaign.nodes), set(runner.FIXTURES))
            campaign.close()

    def test_route_override_requires_the_selected_rf_witness(self):
        clock = Clock()
        center_key = bytes(range(32))

        class Evidence:
            run = bytes(range(8))
            condition = Condition(clock)
            report = {'trials': []}

            def event(self, *args, **kwargs):
                pass

            def records(self, source, sequence):
                original = make_probe(self.run, source, sequence)
                forwarded = original[:1]+b'\x01'+center_key[:1]+original[2:]
                return [{'observer': 'observe_inputs', 'wire_hex': original.hex()},
                        {'observer': 'observe_media', 'wire_hex': forwarded.hex()}]

            def save(self):
                pass

        class Feeder:
            def request(self, command, expected):
                return b'\0'

        campaign = runner.Campaign({'probe_pause': 1, 'probe_timeout': 6}, Evidence(), right_only=True)
        campaign.nodes = {'feed_right': Feeder()}
        campaign.center_key = center_key
        with patch.object(runner.time, 'monotonic', clock.monotonic), \
                patch.object(runner.time, 'sleep', clock.sleep):
            result = campaign.probe('R', expected_output='observe_media')
        self.assertTrue(result['passed'])
        self.assertEqual(result['correct_center_outputs'], 1)
        self.assertEqual(result['wrong_channel_observations'], 0)

    def test_center_rf_challenge_requires_independent_witness_and_restores_input(self):
        clock = Clock()

        class Evidence:
            run = bytes(range(8))
            report = {'trials': []}

            def event(self, *args, **kwargs):
                pass

            def records(self, source, sequence):
                wire = make_probe(self.run, source, sequence)
                return [{'observer': 'observe_media' if source == 'L' else 'observe_usa',
                         'wire_hex': wire.hex()}]

            def save(self):
                pass

        class Center:
            def packet_stats(self):
                return {'recv': 9}

        class Feeder:
            def request(self, command, expected):
                return b'\0'

        campaign = runner.Campaign({'probe_pause': 1, 'probe_timeout': 6}, Evidence(), right_only=True)
        campaign.nodes = {'center': Center(), 'feed_left': Feeder(), 'feed_right': Feeder()}
        campaign.center_key = bytes(range(32))
        with patch.object(runner.time, 'monotonic', clock.monotonic), \
                patch.object(runner.time, 'sleep', clock.sleep), \
                patch.object(campaign, 'set_fixture_rf') as tune, patch.object(campaign, 'probe') as control:
            campaign.center_rx_challenges()
        self.assertEqual(tune.call_args_list[-2].args, ('feed_left', runner.RF['input_media']))
        self.assertEqual(tune.call_args_list[-1].args, ('feed_right', runner.RF['input_usa']))
        control.assert_called_once_with('R', phase='restored_input_rf_control')
        self.assertTrue(all(trial['passed'] for trial in campaign.evidence.report['trials']))
        self.assertEqual(len(campaign.evidence.report['trials']), 2)

    def test_cad_busy_uses_physical_order_not_usb_order_and_restores_feeder(self):
        for forward_time, blocker_notification, forward_notification, expected in (
                (18.0, 25.0, 18.5, 'passed'),
                (4.0, 17.0, 25.0, 'failed'),
                (17.0, 17.0, 17.5, 'inconclusive')):
            with self.subTest(forward_time=forward_time, expected=expected):
                clock = Clock()
                center_key = bytes(range(32))

                class Evidence:
                    run = bytes(range(8))
                    report = {'trials': []}

                    def event(self, *args, **kwargs):
                        pass

                    def records(self, source, sequence):
                        wire = make_probe(self.run, source, sequence, direct=source == 'L')
                        if source == 'L':
                            return [{'observer': 'observe_usa', 'wire_hex': wire.hex(),
                                     'monotonic': blocker_notification}]
                        forward = wire[:1]+b'\x01'+center_key[:1]+wire[2:]
                        return [{'observer': 'observe_inputs', 'wire_hex': wire.hex(), 'monotonic': 2.0},
                                {'observer': 'observe_usa', 'wire_hex': forward.hex(),
                                 'monotonic': forward_notification}]

                    def save(self):
                        pass

                class Center:
                    cad_requests = 0

                    def packet_stats(self):
                        return {'recv': 0}

                    def cli(self, command, timeout=15):
                        if command == 'stats-radio-diag':
                            transmitting = forward_time <= clock.now < forward_time+.5
                            sent = int(clock.now >= forward_time+.5)
                            return (f'state={3 if transmitting else 0}(test), recv=0, sent={sent}, '
                                    'errors=0, err_flags=0, outbound=no, last_rx=0s ago')
                        if command != 'get cad':
                            raise AssertionError(command)
                        value = self.cad_requests
                        self.cad_requests += 1
                        return f'on (required by radio.mode tx). # channel busy: {value}'

                class Feeder:
                    preamble = 96
                    started = False

                    def request(self, command, expected):
                        self.started = True
                        return b'\0'

                    def cli(self, command):
                        if command == 'stats-radio-diag':
                            transmitting = self.started and clock.now < 17
                            sent = int(self.started and clock.now >= 17)
                            return (f'state={3 if transmitting else 0}(test), recv=0, sent={sent}, '
                                    'errors=0, err_flags=0, outbound=no, last_rx=0s ago')
                        # Simulate the older Full fixture's command surface.
                        if command == 'get radio.preamble':
                            return 'Unknown command'
                        self_command = 'get radio2.status'
                        if command != self_command:
                            raise AssertionError(command)
                        return f'off; preamble={self.preamble},32 RX=0,0 TX=0,0'

                campaign = runner.Campaign({'probe_pause': 1}, Evidence())
                campaign.center_key = center_key
                campaign.nodes = {'center': Center(), 'feed_left': Feeder(), 'feed_right': Feeder()}

                def tune(label, rf, preamble=96):
                    campaign.nodes[label].preamble = preamble

                with patch.object(runner.time, 'monotonic', clock.monotonic), \
                        patch.object(runner.time, 'sleep', clock.sleep), \
                        patch.object(campaign, 'set_fixture_rf', side_effect=tune) as restore, \
                        patch.object(campaign, 'probe'):
                    campaign.cad_busy_challenge()
                result = campaign.evidence.report['hardware_cad_busy_test']
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['physical_timing']['transmit_order_proven'], expected == 'passed')
                self.assertEqual(result['physical_timing']['simultaneous_tx_proven'], expected == 'failed')
                self.assertFalse(result['usb_witness_order_is_rf_timing_proof'])
                self.assertEqual(campaign.nodes['feed_left'].preamble, 96)
                self.assertEqual(restore.call_args.args, ('feed_left', runner.RF['input_media']))


if __name__ == '__main__':
    unittest.main()
