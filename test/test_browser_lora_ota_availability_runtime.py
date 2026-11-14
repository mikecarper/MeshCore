#!/usr/bin/env python3
"""Check early OTA capability messages through the actual browser protocols."""
import json
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_repeater_retry_runtime import repeater_harness
from test_browser_lora_ota_legacy_investigation_runtime import legacy_harness


def availability_harness(*, status=None, self_reply=None, fault='', companion=False):
    harness = legacy_harness() if companion else repeater_harness(fault)
    if companion:
        marker = "if(text==='get public.key')reply='> '+key;"
        replacement = marker
        if status is not None:
            replacement += "else if(text==='ota status')reply=" + json.dumps(status) + ';'
        if self_reply is not None:
            replacement += "else if(text==='ota self')reply=" + json.dumps(self_reply) + ';'
    else:
        marker = "cmds.push('remote '+cmd);"
        replacement = marker
        if status is not None:
            replacement += "if(cmd==='ota status')return " + json.dumps(status) + ';'
        if self_reply is not None:
            replacement += "if(cmd==='ota self')return " + json.dumps(self_reply) + ';'
    assert harness.count(marker) == 1
    harness = harness.replace(marker, replacement, 1)
    marker = 'await report({' + ('investigateClicks,' if companion else 'progress,')
    replacement = r'''await report({availability:{text:el('ota-availability').textContent,hidden:el('ota-availability').hidden,role:el('ota-availability').getAttribute('role')},gated:['inspect','file','base','chain-plan'].map(name=>el(name).disabled),''' + ('investigateClicks,' if companion else 'progress,')
    assert harness.count(marker) == 1
    return harness.replace(marker, replacement, 1)


class EarlyOtaAvailabilityBrowserTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def check_read_only(self, result):
        self.assertNotIn('error', result, result)
        commands = [command.removeprefix('remote ') for command in result['cmds']]
        self.assertFalse(any(command.startswith(('set ', 'ota pull ', 'ota install '))
                             for command in commands), result)
        self.assertFalse(any(frame[:4] in ([109, 115, 2, 0], [109, 115, 3, 0])
                             or frame[0] in (52, 65) for frame in result['frames']), result)
        return commands

    def test_unknown_ota_command_stops_before_unrelated_checks_on_both_transports(self):
        for companion in (False, True):
            with self.subTest(companion=companion):
                result = run_harness(availability_harness(status='Unknown command', companion=companion), timeout=90)
                commands = self.check_read_only(result)
                self.assertEqual(commands.count('ota status'), 1, result)
                self.assertNotIn('ota self', commands, result)
                self.assertNotIn('get bootloader.ver', commands, result)
                self.assertNotIn('ota config', commands, result)
                self.assertFalse(result['availability']['hidden'], result)
                self.assertEqual(result['availability']['role'], 'status', result)
                self.assertRegex(result['availability']['text'], r'(?i)LoRa.*(?:not|unavailable|unsupported)|(?:not|unavailable|unsupported).*LoRa')
                self.assertTrue(all(result['gated']), result)
                if not companion:
                    self.assertEqual(result['peerTimeout'], 60, result)

    def test_source_only_seeder_stops_before_self_and_package_selection(self):
        result = run_harness(availability_harness(status='OTA seeder | install:disabled | target:00000000 | maxblk:1024 | serving:0 (source only)'), timeout=90)
        commands = self.check_read_only(result)
        self.assertNotIn('ota self', commands, result)
        self.assertTrue(all(result['gated']), result)
        self.assertRegex(result['availability']['text'], r'(?i)source.only|seed|receive|install')

    def test_missing_bootloader_is_reported_on_the_first_status_reply(self):
        result = run_harness(availability_harness(status='OTA hw=board | target:12345678 | maxblk:1024 | bl:NONE | no download'), timeout=90)
        commands = self.check_read_only(result)
        self.assertNotIn('ota self', commands, result)
        self.assertNotIn('get bootloader.ver', commands, result)
        self.assertTrue(all(result['gated']), result)
        self.assertRegex(result['availability']['text'], r'(?i)bootloader')
        self.assertEqual(result['peerTimeout'], 60, result)

    def test_self_storage_failure_stops_before_remaining_diagnostics(self):
        result = run_harness(availability_harness(self_reply='self body=44 image=100 base_hash=1122334455667788 | storage unsafe: no usable flash'), timeout=90)
        commands = self.check_read_only(result)
        self.assertIn('ota status', commands, result)
        self.assertIn('ota self', commands, result)
        self.assertNotIn('ver', commands, result)
        self.assertNotIn('ota config', commands, result)
        self.assertTrue(all(result['gated']), result)
        self.assertRegex(result['availability']['text'], r'(?i)storage|flash')

    def test_missing_codec_proof_remains_unknown_and_keeps_bootloader_diagnostics(self):
        result = run_harness(availability_harness(
            status='OTA hw=board | target:12345678 | maxblk:1024 | bl:internal | no download',
            self_reply='self body=44 image=100 base_hash=1122334455667788 | bootloader: apply OK'), timeout=90)
        commands = self.check_read_only(result)
        self.assertIn('get bootloader.ver', commands, result)
        self.assertIn('ota bootloader status', commands, result)
        self.assertTrue(all(result['gated']), result)
        self.assertRegex(result['availability']['text'], r'(?i)not.*confirm')
        self.assertNotRegex(result['availability']['text'], r'(?i)does not support|unsupported')

    def test_exhausted_timeout_remains_unknown_and_does_not_claim_unsupported(self):
        result = run_harness(availability_harness(fault='later_exhaust'), timeout=90)
        commands = self.check_read_only(result)
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['counts']['ota status'], 11, result)
        self.assertIn('ota self', commands, result)
        self.assertRegex(result['availability']['text'], r'(?i)not.*confirm|unknown|not.*check|no reply|retry')
        self.assertNotRegex(result['availability']['text'], r'(?i)does not support|unsupported|no LoRa.*support')

    def test_supported_target_continues_existing_checks_and_enables_file_preflight(self):
        result = run_harness(availability_harness(), timeout=90)
        commands = self.check_read_only(result)
        self.assertTrue(result['complete'], result)
        self.assertIn('ota self', commands, result)
        self.assertIn('ver', commands, result)
        self.assertIn('ota installhash', commands, result)
        self.assertFalse(any(result['gated']), result)
        self.assertRegex(result['availability']['text'], r'(?i)support|available|ready|receiver detected')

    def test_preceding_cached_checks_script_does_not_break_investigation(self):
        harness = availability_harness()
        marker = "window.addEventListener('load',async()=>{"
        self.assertEqual(harness.count(marker), 1)
        harness = harness.replace(marker, marker+
            "delete LoraOtaChecks.coldRakStatus;delete LoraOtaChecks.prepareColdRakStorage;", 1)
        result = run_harness(harness, timeout=90)
        self.check_read_only(result)
        self.assertTrue(result['complete'], result)
        self.assertFalse(any(result['gated']), result)

    def test_cold_rak_prepares_storage_before_reporting_availability(self):
        harness = availability_harness()
        marker = "cmds.push('remote '+cmd);"
        self.assertEqual(harness.count(marker), 1)
        override = r'''if(cmd==='ota status')return 'OTA | this fw 00112233 (743K) hw=RAK4631 | target:1234ABCD | maxblk:2048 | bl:'+(counts[cmd]===1?'storage-ERR':'QSPI')+' blrc:00 | no download';
        if(cmd==='ota self')return 'self body=100 image=156 base_hash=0011223344556677 | maxblk:2048 abi=3 codecs=0x5 | '+(counts[cmd]===1?'storage unsafe: not probed':'QSPI store:2048K | bootloader: QSPI apply OK');
        if(cmd==='ota bootloader status')return 'BL board=239A0029 target=2D0DF000 name=4631_DFU crc=FF374B1B abi=3 caps=0A | staged:none mid=- hash=-';
        if(cmd==='ota qspi')return 'QSPI W25Q16 header jedec=EF4015 size=2048K sr1=00 stage=jedec';'''
        harness = harness.replace(marker, marker+override, 1)
        result = run_harness(harness, timeout=90)
        commands = self.check_read_only(result)
        self.assertTrue(result['complete'], result)
        self.assertEqual(commands.count('ota bootloader status'), 1, result)
        self.assertEqual(commands.count('ota status'), 2, result)
        self.assertEqual(commands.count('ota self'), 2, result)
        self.assertFalse(any(result['gated']), result)
        self.assertRegex(result['availability']['text'], r'(?i)receiver detected|supported')

    def test_rak_with_a_real_storage_failure_stays_blocked(self):
        result = run_harness(availability_harness(
            status='OTA | this fw 00112233 (743K) hw=RAK4631 | target:1234ABCD | maxblk:2048 | bl:storage-ERR blrc:00 | no download',
            self_reply='self body=100 image=156 base_hash=0011223344556677 | maxblk:2048 abi=3 codecs=0x5 | storage unsafe: no usable store'), timeout=90)
        commands = self.check_read_only(result)
        self.assertIn('ota self', commands, result)
        self.assertNotIn('ota bootloader status', commands, result)
        self.assertTrue(all(result['gated']), result)
        self.assertRegex(result['availability']['text'], r'(?i)storage.*not ready|storage.*unavailable')

    def test_cold_rak_is_prepared_through_an_older_usb_companion(self):
        harness = availability_harness(companion=True)
        marker = "if(text==='get public.key')reply='> '+key;"
        self.assertEqual(harness.count(marker), 1)
        override = r'''else if(text==='ota status')reply='OTA | this fw 00112233 (743K) hw=RAK4631 | target:1234ABCD | maxblk:2048 | bl:'+(cmds.filter(x=>x===text).length===1?'storage-ERR':'QSPI')+' blrc:00 | no download';
        else if(text==='ota self')reply='self body=100 image=156 base_hash=0011223344556677 | maxblk:2048 abi=3 codecs=0x5 | '+(cmds.filter(x=>x===text).length===1?'storage unsafe: not probed':'QSPI store:2048K | bootloader: QSPI apply OK');
        else if(text==='ota bootloader status')reply='BL board=239A0029 target=2D0DF000 name=4631_DFU crc=FF374B1B abi=3 caps=0A | staged:none mid=- hash=-';
        else if(text==='ota qspi')reply='QSPI W25Q16 header jedec=EF4015 size=2048K sr1=00 stage=jedec';'''
        harness = harness.replace(marker, marker+override, 1)
        result = run_harness(harness, timeout=90)
        commands = self.check_read_only(result)
        self.assertTrue(result['complete'], result)
        self.assertEqual(commands.count('ota bootloader status'), 1, result)
        self.assertEqual(commands.count('ota status'), 2, result)
        self.assertEqual(commands.count('ota self'), 2, result)
        self.assertFalse(any(result['gated']), result)
        self.assertRegex(result['availability']['text'], r'(?i)receiver detected|supported')

    def test_source_known_not_to_send_ota_blocks_before_any_remote_investigation(self):
        harness = availability_harness(companion=True)
        marker = "window.addEventListener('load',async()=>{try{"
        override = "const Transport=LoraOtaCompanion.CompanionTransport;LoraOtaCompanion.CompanionTransport=class extends Transport{async open(){await super.open();this.rawPacketSupport='unsupported';}};"
        self.assertEqual(harness.count(marker), 1)
        harness = harness.replace(marker, marker + override, 1)
        ready = "await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));"
        self.assertEqual(harness.count(ready), 1)
        harness = harness.replace(ready, "await wait(()=>!el('investigate').disabled||el('status').textContent.includes('Error:'));", 1)
        result = run_harness(harness, timeout=90)
        commands = self.check_read_only(result)
        self.assertEqual(commands, [], result)
        self.assertTrue(all(result['gated']), result)
        self.assertFalse(result['availability']['hidden'], result)
        self.assertRegex(result['availability']['text'], r'(?i)(?:source|Companion).*(?:cannot send|firmware)')
        self.assertFalse(any(frame[0] in (2, 10, 26) for frame in result['frames']), result)

    def test_reinvestigation_recovers_and_reenables_controls_when_support_is_restored(self):
        harness = availability_harness(status='Unknown command')
        first_status = 'if(cmd===\'ota status\')return "Unknown command";'
        self.assertEqual(harness.count(first_status), 1)
        harness = harness.replace(first_status,
            'if(cmd===\'ota status\'&&counts[cmd]===1)return "Unknown command";', 1)
        first_done = 'const event=await done;'
        self.assertEqual(harness.count(first_done), 1)
        retry = r'''const first=await done;
      const firstAvailability=el('ota-availability').textContent, firstCommands=cmds.slice(),firstGated=['inspect','file','base','chain-plan'].every(name=>el(name).disabled);
      if(el('investigate').disabled)throw Error('investigation retry is disabled');
      const secondDone=new Promise(resolve=>document.querySelector('[data-lora-ota]').addEventListener('lora-ota-investigated',resolve,{once:true}));
      el('investigate').click();const event=await secondDone;'''
        harness = harness.replace(first_done, retry, 1)
        marker = 'await report({availability:'
        self.assertEqual(harness.count(marker), 1)
        harness = harness.replace(marker, 'await report({firstAvailability,firstCommands,firstGated,availability:', 1)
        result = run_harness(harness, timeout=90)
        self.check_read_only(result)
        self.assertTrue(result['firstGated'], result)
        self.assertRegex(result['firstAvailability'], r'(?i)not available|unavailable')
        self.assertNotIn('remote ota self', result['firstCommands'], result)
        self.assertEqual(result['counts']['ota status'], 2, result)
        self.assertEqual(result['counts']['ota self'], 1, result)
        self.assertTrue(result['complete'], result)
        self.assertFalse(any(result['gated']), result)
        self.assertRegex(result['availability']['text'], r'(?i)receiver detected|supported')


if __name__ == '__main__':
    unittest.main()
