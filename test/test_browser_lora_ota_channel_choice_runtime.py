#!/usr/bin/env python3
"""Automatic update profiles respect the band, active window and user choice."""
import os
import unittest

from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness


def channel_harness(frequency, bandwidth, sf, cr, *, active=None, override=None, region=None, reset=False, source_board=None):
    harness = HARNESS
    if source_board:
        harness = harness.replace("out.set(enc.encode('Test board'),20);", "out.set(enc.encode(%r),20);" % source_board, 1)
    original = "v.setUint32(48,910525,true);v.setUint32(52,62500,true);out[56]=7;out[57]=5;"
    assert harness.count(original) == 1
    harness = harness.replace(original, "v.setUint32(48,%d,true);v.setUint32(52,%d,true);out[56]=%d;out[57]=%d;" %
                              (round(frequency * 1000), round(bandwidth * 1000), sf, cr), 1)
    original = "else if(text==='get tempradio')value='TempRadio active: '+radio+' 100s left,preamble=32';"
    assert harness.count(original) == 1
    reply = 'TempRadio inactive' if active is None else 'TempRadio active: %s,%s,%s,%s 100s left,preamble=64' % tuple(active)
    harness = harness.replace(original, "else if(text==='get tempradio')value=" + repr(reply) + ';', 1)
    original = "if(el('source').value!=='companion')throw Error('Companion is not default');"
    assert harness.count(original) == 1
    setup = ''
    if override:
        values = dict(zip(('radio-frequency', 'radio-bandwidth', 'radio-sf', 'radio-cr'), override))
        setup = ''.join("el(%r).value=%r;" % (name, str(value)) for name, value in values.items())
        setup += "el('radio-frequency').dispatchEvent(new Event('input'));"
    if region:
        setup += "el('radio-region').value=%r;if(el('radio-region').value!==%r)throw Error('Missing region option');el('radio-region').dispatchEvent(new Event('change'));await wait(()=>!el('radio-region').disabled);" % (region, region)
    if setup:
        harness = harness.replace(original, setup + original, 1)
    original = "const buttons=()=>[...el('contact').querySelectorAll('button[data-contact-key]')];"
    assert harness.count(original) == 1
    if reset:
        harness = harness.replace(original, "if(el('radio-reset').hidden)throw Error('Reset manual overrides is unavailable');el('radio-reset').click();await wait(()=>!el('radio-reset').disabled);" + original, 1)
    harness = harness.replace(original, """const suggested={frequency:el('radio-frequency').value,
      bandwidth:Number(el('radio-bandwidth').value),sf:Number(el('radio-sf').value),cr:Number(el('radio-cr').value),
      preamble:Number(el('radio-preamble').value),region:el('radio-region').value,regionStatus:el('radio-region-status').textContent,
      commands:el('radio-commands').textContent,frames:port.frames.slice()};
      el('disconnect').click();await wait(()=>!el('connect').disabled);
      suggested.afterDisconnect=['radio-frequency','radio-bandwidth','radio-sf','radio-cr'].map(name=>el(name).value);
      await report(suggested);return;""" + original, 1)
    return harness


class BrowserChannelChoiceTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_eu_saved_channel_is_automatically_selected(self):
        self.check_channel([869.618, 62.5, 8, 8], [869.525, 250, 5, 7])

    def test_433_saved_channel_is_automatically_selected(self):
        self.check_channel([433.175, 250, 8, 6], [433.175, 250, 8, 6])

    def test_active_temporary_window_takes_precedence(self):
        result = self.check_channel([869.618, 62.5, 8, 8], [868.850, 125, 9, 6], active=[868.850, 125, 9, 6])
        self.assertEqual(result['preamble'], 64)

    def test_explicit_override_takes_precedence(self):
        self.check_channel([869.618, 62.5, 8, 8], [869.850, 125, 8, 6],
                           active=[868.850, 125, 9, 6], override=[869.850, 125, 8, 6])

    def test_unknown_custom_tuple_does_not_guess_a_country(self):
        self.check_channel([865, 125, 10, 5], [865, 125, 10, 5])

    def test_us_canada_recognized_profile_uses_500_khz(self):
        result = self.check_channel([910.525, 62.5, 7, 5], [909.950, 500, 5, 7])
        self.assertIn('Country cannot be determined', result['regionStatus'])

    def test_shared_australia_brazil_profile_does_not_guess_the_country(self):
        result = self.check_channel([923.125, 62.5, 8, 8], [921.500, 500, 5, 7])
        self.assertIn('Country cannot be determined', result['regionStatus'])

    def test_explicit_region_overrides_active_window(self):
        self.check_channel([923.125, 62.5, 8, 8], [921.500, 250, 5, 7],
                           active=[920, 125, 9, 5], region='australia')

    def test_direct_rf_fields_override_explicit_region(self):
        result = self.check_channel([923.125, 62.5, 8, 8], [922, 125, 8, 6],
                                    override=[922, 125, 8, 6], region='australia')
        self.assertIn('manually selected', result['regionStatus'])

    def test_reset_manual_overrides_applies_the_selected_region_without_reconnecting(self):
        self.check_channel([923.125, 62.5, 8, 8], [921.500, 250, 5, 7],
                           override=[922, 125, 8, 6], region='australia', reset=True)

    def test_raw_only_legacy_source_refuses_an_incompatible_explicit_region(self):
        from test_browser_lora_ota_workflow_runtime import legacy_setup_harness
        harness = legacy_setup_harness(matching=True)
        original = "el('inspect').click();"
        self.assertEqual(harness.count(original), 1)
        harness = harness.replace(original, """el('radio-region').value='us-ca';el('radio-region').dispatchEvent(new Event('change'));
          await wait(()=>!el('radio-region').disabled);""" + original, 1)
        result = run_harness(harness, timeout=90)
        self.assertIn('can use only its saved primary channel', result['status'], result)
        self.assertFalse(any(command.startswith(('tempradio ', 'set radio ', 'set radio2 ', 'ota pull '))
                             for command in result['cmds']), result)
        for frame in result['frames']:
            if frame[0] == 66:
                self.assertFalse(bytes(frame[1:]).decode().startswith(('tempradio ', 'set tempradio2 ')), result)

    def test_known_sx127x_companion_automatically_uses_sf7(self):
        result = self.check_channel([910.525, 62.5, 7, 5], [909.950, 500, 7, 7], source_board='Heltec V2')
        self.assertIn('SX127x', result['regionStatus'])

    def test_sx1262_companion_retains_sf5(self):
        self.check_channel([910.525, 62.5, 7, 5], [909.950, 500, 5, 7], source_board='Heltec V4')

    def test_repeater_reads_board_once_for_generated_sf5_and_caches_it(self):
        from test_browser_lora_ota_runtime import HARNESS as repeater_harness
        script = repeater_harness.replace("'board':'Test board'", "'board':'Heltec V2'", 1)
        original = "'get tempradio':radio,'get radio':radio,"
        self.assertEqual(script.count(original), 1)
        script = script.replace(original, "'get tempradio':'off','get radio':radio.split(',').slice(0,4).join(','),", 1)
        original = "el('inspect').click();await wait(()=>!el('transfer').disabled);"
        self.assertEqual(script.count(original), 1)
        script = script.replace(original, """if(el('radio-sf').value!=='7')throw Error('Known SX127x repeater was assigned SF5');
          el('radio-region').value='australia';el('radio-region').dispatchEvent(new Event('change'));
          await wait(()=>!el('radio-region').disabled);
          if(el('radio-sf').value!=='7')throw Error('Region override lost the hardware fallback');
          await report({cmds,sf:el('radio-sf').value,profile:el('radio-region-status').textContent});return;""", 1)
        result = run_harness(script, timeout=45)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['cmds'].count('local board'), 1)
        self.assertFalse(any(command.startswith('remote ') or command.startswith('local ota.peer acl ')
                             for command in result['cmds']), result)

    def test_manual_frequency_entered_during_region_read_is_not_overwritten(self):
        script=channel_harness(910.525,62.5,7,5)
        original="let expected,pkg,mid=''"
        self.assertEqual(script.count(original),1)
        script=script.replace(original,"let temporaryReads=0,releaseTemporary=null,expected,pkg,mid=''",1)
        original='queueMicrotask(()=>this.push(wire(out)));'
        self.assertEqual(script.count(original),1)
        script=script.replace(original,"""if(f[0]===66&&dec.decode(f.slice(1))==='get tempradio'&&++temporaryReads===2){
          releaseTemporary=()=>this.push(wire(out));return;
        }"""+original,1)
        original="const suggested={frequency:el('radio-frequency').value,"
        self.assertEqual(script.count(original),1)
        script=script.replace(original,"""el('radio-region').value='us-ca';el('radio-region').dispatchEvent(new Event('change'));
          await wait(()=>releaseTemporary!==null);
          el('radio-frequency').value='908.500';el('radio-frequency').dispatchEvent(new Event('input'));
          releaseTemporary();await wait(()=>!el('radio-region').disabled);
          """+original,1)
        result=run_harness(script,timeout=45)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['frequency'],'908.500',result)
        self.assertIn('manually selected',result['regionStatus'])
        self.assertIn('tempradio 908.500,500,5,7',result['commands'])

    def check_channel(self, saved, expected, **options):
        frequency, bandwidth, sf, cr = expected
        result = run_harness(channel_harness(*saved, **options), timeout=45)
        self.assertNotIn('error', result, result)
        if options.get('override') and not options.get('reset'):
            self.assertEqual(float(result['frequency']), frequency)
        else:
            self.assertEqual(result['frequency'], '%.3f' % frequency)
        self.assertEqual([result['bandwidth'], result['sf'], result['cr']], [bandwidth, sf, cr])
        self.assertIn('%.3f,%s,%s,%s' % (frequency, bandwidth, sf, cr), result['commands'])
        self.assertEqual(result['afterDisconnect'], ['', '250', '7', '5'])
        self.assertFalse(any(frame[0] in (26, 65) or frame[0] == 66 and
                             bytes(frame[1:]).decode().startswith(('set ', 'tempradio '))
                             for frame in result['frames']), result)
        return result


if __name__ == '__main__':
    unittest.main()
