#!/usr/bin/env python3
"""Recover a real page's lost USB ACK without replaying an OTA operation."""
import json
import os
import unittest

from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_quiet_runtime import quiet_forwarding_harness
from test_browser_lora_ota_radio_setup_runtime import replace_once
from test_browser_lora_ota_runtime import BROWSER


def usb_timeout_recovery_harness(*, reset_selector=False, automatic_open_fails=False, manual_disconnect=False):
    harness = quiet_forwarding_harness(disconnect_after_prepare=True)
    # This USB recovery model reports a fixed 909.950/BW500 secondary. Select
    # it explicitly so the automatic +200 kHz handoff cannot bypass the ACK
    # failure under test by waiting for a different modeled radio tuple.
    harness = replace_once(harness, "el('quiet-forwarding').checked=true;",
        "el('radio-frequency').value='909.950';el('radio-bandwidth').value='500';"
        "el('radio-sf').value='5';el('radio-cr').value='7';"
        "el('radio-frequency').dispatchEvent(new Event('input'));"
        "el('quiet-forwarding').checked=true;")
    harness = replace_once(harness, 'const forwardingWrites=[],forwardingReads=[],quietKey=',
        'let opens=0,appStarts=0,newSessionArms=0,droppedRawAck=null,rejectedOpens=0,chooserCalls=0,recoveryFrameAt=0,'
        'wrongProfileCommands=0,manualRetryAllowed=%s,explicitDisconnect=null;'
        'const recoveryCloses=[],appStartStates=[],postDisconnectFrames=[],physicalCloses=[];'
        'const forwardingWrites=[],forwardingReads=[],quietKey=' % ('false' if automatic_open_fails else 'true'))
    harness = replace_once(harness, '    async open(){this.readable=',
        "    async open(){opens++;if(opens>1)recoveryFrameAt=this.frames.length;if(droppedRawAck&&!manualRetryAllowed){rejectedOpens++;"
        "throw new DOMException('Fixture automatic USB open failed','NetworkError');}this.readable=")
    harness = replace_once(harness, '    async close(){}',
        '    async close(){physicalCloses.push({opens,afterExplicitDisconnect:!!explicitDisconnect});}')
    harness = replace_once(harness, 'const f=bytes.slice(3);this.frames.push(Array.from(f));let out;',
        'const f=bytes.slice(3);this.frames.push(Array.from(f));'
        'if(explicitDisconnect)postDisconnectFrames.push(Array.from(f));let out;')
    harness = replace_once(harness, 'if(f[0]===1){out=new Uint8Array(58);',
        "if(f[0]===1){appStarts++;if(appStarts>1&&%s){guard=false;selectedProfile=0;}"
        "appStartStates.push({guard,profile:selectedProfile});out=new Uint8Array(58);"
        % ('true' if reset_selector else 'false'))
    harness = replace_once(harness, "if(f[1]===1)guard=true;",
        "if(f[1]===1){if(appStarts>1)newSessionArms++;guard=true;}")
    harness = replace_once(harness, "if(f[1]===3){guard=false;timeline.push(['guard-close']);}",
        "if(f[1]===3){if(appStarts>1&&guard&&newSessionArms===0)"
        "recoveryCloses.push({target:hex(f.slice(2,34)),profile:selectedProfile});"
        "guard=false;timeline.push(['guard-close']);}")
    harness = replace_once(harness, "else if(f[0]===65){const rf=f.slice(2);",
        "else if(f[0]===65){const rf=f.slice(2);if(!droppedRawAck){"
        "droppedRawAck={at:Date.now(),guard,profile:selectedProfile,operation:rf[2],length:f.length};"
        "if(rf[2]===1)advertSeen=true;return;}")
    if manual_disconnect:
        harness = replace_once(harness, "if(rf[2]===1)advertSeen=true;return;}",
            "if(rf[2]===1)advertSeen=true;explicitDisconnect={failureBeforeClick:!!window.testLink.failure,"
            "portOpened:window.testLink.portOpened,guard,profile:selectedProfile};"
            "el('disconnect').click();return;}")
    harness = replace_once(harness,
        "if(text==='get public.key'&&selectedProfile===1&&targetTemp==='off')",
        "if(appStarts>1&&selectedProfile!==1){wrongProfileCommands++;mailboxPending=true;reply='';}"
        "else if(text==='get public.key'&&selectedProfile===1&&targetTemp==='off')")
    harness = replace_once(harness, 'requestPort:async()=>port',
        'requestPort:async()=>{chooserCalls++;return port;}')
    harness = replace_once(harness, 'value:{requestPort:async()=>{chooserCalls++;return port;}}',
        'value:{getPorts:async()=>chooserCalls?[port]:[],requestPort:async()=>{chooserCalls++;return port;}}')
    # Configure only host budgets. The real transport still queues each binary
    # command, times out its lost ACK, reconnects and proves its target itself.
    harness = replace_once(harness, 'super(...args);window.testLink=this;',
        'super(...args);this.options={...this.options,commandTimeout:250,remoteTimeout:250,'
        'openSettle:0,openRetryDelayMs:0,guardRecoveryTimeoutMs:500};window.testLink=this;')
    manual = r'''
      await wait(()=>droppedRawAck&&!el('connect').disabled);
      fallback={guard,repeat,record:JSON.parse(localStorage.getItem(quietKey)||'null'),
        rejectedOpens,connectEnabled:!el('connect').disabled};
      manualRetryAllowed=true;manualRetryClicked=true;el('connect').click();
      await wait(()=>window.testLink!==failedTransport&&!window.testLink.failure&&
        window.testLink.sourceKey===source&&!el('inspect').disabled);
      if(localStorage.getItem(quietKey)){
        userRestoreConfirmed=true;manualRestoreClicked=true;el('restore-forwarding').click();
      }
    ''' if automatic_open_fails else ''
    explicit_restore = r'''
      await wait(()=>droppedRawAck&&window.testLink!==failedTransport&&!window.testLink.failure&&
        window.testLink.sourceKey===source&&!el('inspect').disabled&&!el('restore-forwarding').disabled&&
        el('status').textContent.includes('USB source identity confirmed after retry.'));
      automaticRecovery={record:JSON.parse(localStorage.getItem(quietKey)||'null'),repeat,
        writes:forwardingWrites.map(row=>row.command),frames:port.frames.slice(recoveryFrameAt),
        notice:el('quiet-forwarding-status').textContent,restoreHidden:el('restore-forwarding').hidden,
        restoreDisabled:el('restore-forwarding').disabled,elapsed:Date.now()-droppedRawAck.at};
      userRestoreConfirmed=true;manualRestoreClicked=true;el('restore-forwarding').click();
    ''' if not automatic_open_fails else ''
    scenario = r'''
      const profiles=[window.testLink.otaRadioProfile,window.testLink.otaTargetProfile];
      const failedTransport=window.testLink,beforeUpdateRecord=JSON.parse(localStorage.getItem(quietKey)||'null');
      let fallback=null,automaticRecovery=null,manualRetryClicked=false,manualRestoreClicked=false;
      el('update').click();
      MANUAL_RECOVERY
      EXPLICIT_RESTORE
      await wait(()=>droppedRawAck&&window.testLink!==failedTransport&&!window.testLink.failure&&
        window.testLink.sourceKey===source&&!localStorage.getItem(quietKey)&&!el('inspect').disabled);
      const originalFailure={message:failedTransport.failure?.message,otaCommand:failedTransport.failure?.otaCommand,
        otaEndpoint:failedTransport.failure?.otaEndpoint,otaUsbTimeout:failedTransport.failure?.otaUsbTimeout};
      const elapsed=Date.now()-droppedRawAck.at;
      await report({profiles,beforeUpdateRecord,droppedRawAck,opens,appStarts,appStartStates,recoveryCloses,
        rejectedOpens,chooserCalls,wrongProfileCommands,fallback,automaticRecovery,manualRetryClicked,manualRestoreClicked,
        elapsed,originalFailure,forwardingWrites,repeat,installed,otaData,cmds,timeline,frames:port.frames,
        sourceProfile:window.testLink.otaRadioProfile,targetProfile:window.testLink.otaTargetProfile,physicalProfile:selectedProfile,
        quietRecord:JSON.parse(localStorage.getItem(quietKey)||'null'),
        updateRecord:JSON.parse(localStorage.getItem('meshcore-browser-lora-ota')||'null'),
        healthy:window.testLink.failure===null,errorDetails:el('error-details').textContent,
        workflowState:el('workflow-status').dataset.state,status:el('status').textContent});return;
    '''.replace('MANUAL_RECOVERY', manual).replace('EXPLICIT_RESTORE', explicit_restore)
    if manual_disconnect:
        scenario = r'''
          const profiles=[window.testLink.otaRadioProfile,window.testLink.otaTargetProfile];
          const failedTransport=window.testLink;el('update').click();
          await wait(()=>explicitDisconnect&&!el('connect').disabled);
          await report({profiles,explicitDisconnect,opens,appStarts,chooserCalls,physicalCloses,postDisconnectFrames,
            droppedRawAck,forwardingWrites,repeat,installed,otaData,cmds,frames:port.frames,
            quietRecord:JSON.parse(localStorage.getItem(quietKey)||'null'),
            timedOut:failedTransport.failure?.otaUsbTimeout===true,
            workflowState:el('workflow-status').dataset.state,status:el('status').textContent});return;
        '''
    harness = replace_once(harness,
        'forwardingWrites,forwardingReads,repeat,quietRecord:',
        'opens,appStarts,rejectedOpens,manualRetryAllowed,droppedRawAck,recoveryCloses,wrongProfileCommands,'
        'forwardingWrites,forwardingReads,repeat,quietRecord:')
    return replace_once(harness,
        'const profiles=[window.testLink.otaRadioProfile,window.testLink.otaTargetProfile];'
        "const workflowState='prepared';", scenario)


class BrowserUsbTimeoutRecoveryRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def assert_recovered_without_replaying_update(self, result, *, passive_selection=True):
        self.assertNotIn('error', result, result)
        self.assertEqual(result['profiles'], [1, 1], result)
        self.assertEqual(result['beforeUpdateRecord']['state'], 'paused', result)
        self.assertEqual(result['droppedRawAck']['operation'], 1, result)
        self.assertTrue(result['droppedRawAck']['guard'], result)
        self.assertEqual(result['droppedRawAck']['profile'], 1, result)
        self.assertTrue(result['healthy'], result)
        self.assertEqual(result['chooserCalls'], 1, result)
        self.assertEqual(result['wrongProfileCommands'], 0, result)
        if passive_selection:
            self.assertEqual((result['sourceProfile'], result['targetProfile']), (1, 1), result)
        self.assertEqual(result['physicalProfile'], 1, result)
        self.assertEqual(result['repeat'], 'on', result)
        self.assertIsNone(result['quietRecord'], result)
        self.assertEqual([row['command'] for row in result['forwardingWrites']], ['set repeat off', 'set repeat on'], result)
        self.assertEqual(result['forwardingWrites'][-1]['record']['original'], 'on', result)
        self.assertEqual(result['originalFailure']['otaEndpoint'], 'USB Companion', result)
        self.assertTrue(result['originalFailure']['otaUsbTimeout'], result)
        self.assertIn('raw OTA packet', result['originalFailure']['message'], result)
        self.assertIn('raw OTA packet', result['errorDetails'], result)
        self.assertEqual(sum(frame[0] == 65 for frame in result['frames']), 1, result)
        self.assertFalse(any(command.startswith(('ota pull ', 'ota installhash ')) for command in result['cmds']), result)
        self.assertFalse(result['installed'], result)
        self.assertFalse(result['otaData'], result)
        self.assertNotEqual(result['workflowState'], 'complete', result)
        self.assertEqual(result['updateRecord']['phase'], 'prepared', result)

    def assert_automatic_recovery_keeps_forwarding_pending(self, result):
        recovery = result['automaticRecovery']
        self.assertIsNotNone(recovery, result)
        self.assertEqual(recovery['record'], result['beforeUpdateRecord'], result)
        self.assertEqual(recovery['record']['state'], 'paused', result)
        self.assertFalse(recovery['record']['restoreAttempted'], result)
        self.assertEqual(recovery['repeat'], 'off', result)
        self.assertEqual(recovery['writes'], ['set repeat off'], result)
        self.assertFalse(any(frame[0] in (2, 26, 65) for frame in recovery['frames']), result)
        self.assertIn('restoration', recovery['notice'].lower(), result)
        self.assertFalse(recovery['restoreHidden'], result)
        self.assertFalse(recovery['restoreDisabled'], result)
        self.assertTrue(result['manualRestoreClicked'], result)
        self.assertTrue(result['forwardingWrites'][-1]['userConfirmed'], result)
        self.assertLess(recovery['elapsed'], 10000, result)

    def test_usb_ack_timeout_recovers_its_owned_guard_then_waits_for_explicit_forwarding_restore(self):
        result = run_harness(usb_timeout_recovery_harness(), timeout=45)
        self.assert_recovered_without_replaying_update(result)
        self.assert_automatic_recovery_keeps_forwarding_pending(result)
        self.assertEqual(result['opens'], 2, result)
        self.assertEqual(result['appStartStates'][1], {'guard':True, 'profile':1}, result)
        self.assertEqual(result['recoveryCloses'], [{'target':'bb' * 32, 'profile':1}], result)

    def test_usb_reset_selector_is_reselected_locally_before_explicit_target_restore(self):
        result = run_harness(usb_timeout_recovery_harness(reset_selector=True), timeout=45)
        self.assert_recovered_without_replaying_update(result)
        self.assert_automatic_recovery_keeps_forwarding_pending(result)
        self.assertEqual(result['appStartStates'][1], {'guard':False, 'profile':0}, result)
        self.assertEqual(result['recoveryCloses'], [], result)

    def test_failed_automatic_open_keeps_manual_same_port_reconnect_and_restore_available(self):
        result = run_harness(usb_timeout_recovery_harness(automatic_open_fails=True), timeout=45)
        self.assert_recovered_without_replaying_update(result, passive_selection=False)
        self.assertTrue(result['manualRetryClicked'], result)
        self.assertTrue(result['fallback']['connectEnabled'], result)
        self.assertGreater(result['fallback']['rejectedOpens'], 0, result)
        self.assertTrue(result['fallback']['guard'], result)
        self.assertEqual(result['fallback']['repeat'], 'off', result)
        self.assertEqual(result['fallback']['record']['state'], 'paused', result)

    def test_explicit_disconnect_before_the_ack_deadline_never_automatically_reopens_usb(self):
        result = run_harness(usb_timeout_recovery_harness(manual_disconnect=True), timeout=30)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['profiles'], [1, 1], result)
        self.assertEqual(result['explicitDisconnect'], {
            'failureBeforeClick':False, 'portOpened':True, 'guard':True, 'profile':1}, result)
        self.assertTrue(result['timedOut'], result)
        self.assertEqual((result['opens'], result['appStarts'], result['chooserCalls']), (1, 1, 1), result)
        self.assertTrue(result['physicalCloses'], result)
        self.assertTrue(all(item['afterExplicitDisconnect'] for item in result['physicalCloses']), result)
        self.assertEqual(result['postDisconnectFrames'], [], result)
        self.assertEqual([row['command'] for row in result['forwardingWrites']], ['set repeat off'], result)
        self.assertEqual(result['repeat'], 'off', result)
        self.assertEqual(result['quietRecord']['state'], 'paused', result)
        self.assertEqual(result['quietRecord']['original'], 'on', result)
        self.assertFalse(result['quietRecord']['restoreAttempted'], result)
        self.assertEqual(sum(frame[0] == 65 for frame in result['frames']), 1, result)
        self.assertFalse(any(command.startswith(('ota pull ', 'ota installhash ')) for command in result['cmds']), result)
        self.assertFalse(result['installed'], result)
        self.assertFalse(result['otaData'], result)
        self.assertNotEqual(result['workflowState'], 'complete', result)


if __name__ == '__main__':
    unittest.main()
