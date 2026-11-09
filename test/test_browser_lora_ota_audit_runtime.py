#!/usr/bin/env python3
"""Exercise interruptions and conflicting actions in the actual updater UI."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_workflow_runtime import legacy_setup_harness, workflow_harness
from test_browser_lora_ota_delta_runtime import delta_harness


class BrowserOtaAudit(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def test_all_update_actions_wait_for_an_advanced_control_read(self):
        anchor = "el('transfer').click();await wait(()=>!el('install').disabled||el('status').textContent.includes('Error:'));"
        self.assertEqual(HARNESS.count(anchor), 1)
        action = """const write=port.write.bind(port);let release;
    port.write=async bytes=>{
      if(bytes[3]===66&&dec.decode(bytes.slice(4))==='get repeat'){
        await new Promise(r=>{release=r;});
        port.push(wire(Uint8Array.of(29,...enc.encode('on'))));return;
      }
      return write(bytes);
    };
    el('repeat-node').value='source';el('repeat-kind').value='repeat';el('repeat-read').click();await wait(()=>release);
    const blocked=Object.fromEntries(['inspect','transfer','resume','install','update','find-route'].map(name=>[name,el(name).disabled]));
    const before=port.frames.length;el('inspect').click();el('transfer').click();
    await new Promise(r=>setTimeout(r,50));const additional=port.frames.length-before;
    release();await wait(()=>!el('repeat-read').disabled);
    await report({blocked,additional,transferRestored:!el('transfer').disabled,status:el('status').textContent});return;
    """
        result = run_harness(HARNESS.replace(anchor, action + anchor, 1), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertTrue(all(result['blocked'].values()), result)
        self.assertEqual(result['additional'], 0, result)
        self.assertTrue(result['transferRestored'], result)

    def test_invalid_firmware_is_rejected_before_radio_or_policy_changes(self):
        for fault in ('corrupt', 'wrong_target'):
            with self.subTest(fault=fault):
                script = legacy_setup_harness(rxps_on=True)
                original = "new File([expected],'firmware.bin')"
                self.assertEqual(script.count(original), 1)
                image = "Uint8Array.of(1,2,3)" if fault == 'corrupt' else "(()=>{const wrong=expected.slice();new DataView(wrong.buffer).setUint32(wrong.length-36,0x87654321,true);return wrong;})()"
                script = script.replace(original, "new File([" + image + "],'firmware.bin')", 1)
                anchor = "el('inspect').click();await wait(()=>!el('inspect').disabled);"
                self.assertEqual(script.count(anchor), 1)
                script = script.replace(anchor, anchor + """
    await report({timeline,frames:port.frames,status:el('status').textContent,blocked:el('update').disabled&&el('transfer').disabled});return;
    """, 1)
                result = run_harness(script, timeout=45)
                self.assertNotIn('error', result, result)
                self.assertTrue(result['blocked'], result)
                self.assertIn('Error:', result['status'], result)
                self.assertFalse(any(row[0] in ('local', 'remote') for row in result['timeline']), result)
                self.assertFalse(any(frame[0] in (52, 65) for frame in result['frames']), result)

    def test_preparing_a_completed_update_verifies_without_another_install(self):
        script = workflow_harness()
        anchor = "if(el('workflow-status').dataset.state!=='complete')throw Error(el('workflow-status').textContent);"
        self.assertEqual(script.count(anchor), 1)
        script = script.replace(anchor, anchor + """
      const installs=cmds.filter(c=>/^remote ota install(?:$|hash [0-9a-f]{64}$)/.test(c)).length;
      el('inspect').click();await wait(()=>!el('inspect').disabled);
      if(el('workflow-status').dataset.state!=='complete')throw Error(el('workflow-status').textContent);
      await report({installs,after:cmds.filter(c=>/^remote ota install(?:$|hash [0-9a-f]{64}$)/.test(c)).length,blocked:el('update').disabled&&el('install').disabled,notice:el('workflow-status').textContent});return;
      """, 1)
        result = run_harness(script, timeout=45)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['installs'], 1, result)
        self.assertEqual(result['after'], 1, result)
        self.assertTrue(result['blocked'], result)
        self.assertIn('already installed and verified', result['notice'])

    def test_disconnect_while_reading_a_file_does_not_start_radio_setup(self):
        script = legacy_setup_harness(rxps_on=True)
        anchor = "el('inspect').click();await wait(()=>!el('inspect').disabled);"
        self.assertEqual(script.count(anchor), 1)
        action = """const file=el('file').files[0],read=file.arrayBuffer.bind(file);let release;
    file.arrayBuffer=async()=>{await new Promise(r=>release=r);return read();};
    el('inspect').click();await wait(()=>release);
    const before=port.frames.length;el('disconnect').click();release();
    await wait(()=>!el('connect').disabled);
    await report({timeline,after:port.frames.slice(before),status:el('status').textContent});return;
    """
        result = run_harness(script.replace(anchor, action, 1), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertFalse(any(row[0] in ('local', 'remote') for row in result['timeline']), result)
        self.assertFalse(any(frame[0] in (2, 26, 65, 66) for frame in result['after']), result)
        self.assertIn('Disconnected.', result['status'], result)

    def test_completed_differential_is_recognized_with_the_old_base_still_selected(self):
        script = delta_harness()
        anchor = "el('install').click();await wait(()=>el('status').textContent.includes('Installed and verified:'));"
        self.assertEqual(script.count(anchor), 1)
        script = script.replace(anchor, anchor + """
      const before=cmds.length,workers=workerCount;
      el('inspect').click();await wait(()=>!el('inspect').disabled);
      await report({notice:el('workflow-status').textContent,state:el('workflow-status').dataset.state,
        after:cmds.slice(before),workers,workerCount,blocked:el('update').disabled&&el('install').disabled});return;
      """, 1)
        result = run_harness(script, timeout=45)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['state'], 'complete', result)
        self.assertEqual(result['workers'], result['workerCount'], result)
        self.assertTrue(result['blocked'], result)
        self.assertFalse(any(command.startswith(('remote ota pull ', 'remote ota installhash ')) for command in result['after']), result)


if __name__ == '__main__':
    unittest.main()
