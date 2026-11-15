#!/usr/bin/env python3
"""Exercise forwarding pause/restoration through the real OTA page and USB protocol."""
import json
import os
import unittest

from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_radio_setup_runtime import (
    install_capability_before_radio2_harness,
    replace_once,
)
from test_browser_lora_ota_runtime import BROWSER


QUIET_KEY = 'meshcore-browser-lora-ota-quiet-v1'


def quiet_forwarding_harness(*, original='on', lost_pause_ack=False, disconnect_after_prepare=False,
                             restore_retry=False, reconnect_restore=False, missing_module=False):
    """Fake only target forwarding replies; keep channel setup, transfer and install real."""
    if original not in ('on', 'off'):
        raise ValueError(original)
    restore_retry = restore_retry or reconnect_restore
    if missing_module and (restore_retry or disconnect_after_prepare):
        raise ValueError('Missing-module scenario requires the ordinary complete update path')
    harness = install_capability_before_radio2_harness()
    harness = replace_once(harness, '(()=>{\n  const enc=', '''(()=>{
  // Advance fixture-only twenty-second polling; RF handoff/ACK timers stay real.
  const quietTimer=window.setTimeout;
  window.setTimeout=(fn,ms,...args)=>quietTimer(fn,ms===20000?100:ms,...args);
  const enc=''')
    harness = replace_once(harness,
        'let ignoredSwitches=0,blockedInstallReplies=0,mailboxPending=false;',
        "let repeat=%s,restoreAttempts=0,beforeExplicitRestore=null,userRestoreConfirmed=false;"
        "const forwardingWrites=[],forwardingReads=[],quietKey=%s;"
        'let ignoredSwitches=0,blockedInstallReplies=0,mailboxPending=false;'
        % (json.dumps(original), json.dumps(QUIET_KEY)))
    harness = replace_once(harness, "else if(text==='get radio')reply='> '+radio;", r'''
        else if(text==='get radio.flood')reply='unknown config: radio.flood';
        else if(text==='get repeat'){
          forwardingReads.push({repeat,installed,sourceTemp,targetTemp});reply='> '+repeat;
        }
        else if(text==='set repeat off'||text==='set repeat on'){
          forwardingWrites.push({command:text,record:JSON.parse(localStorage.getItem(quietKey)||'null'),
            updateRecord:JSON.parse(localStorage.getItem('meshcore-browser-lora-ota')||'null'),
            installed,sourceTemp,targetTemp,profile:selectedProfile,userConfirmed:userRestoreConfirmed,
            source:window.testLink.sourceKey,target:window.testLink.knownRepeater,
            leaseProof:structuredClone(window.testLink.otaLeaseProof||null)});
          if(RESTORE_RETRY&&text==='set repeat on'&&++restoreAttempts===1)reply='Error: fixture restoration not applied';
          else{
            repeat=text.endsWith('off')?'off':'on';
            if(LOST_PAUSE_ACK&&text==='set repeat off'){mailboxPending=true;reply='';}
            else reply='OK';
          }
        }
        else if(text==='get radio')reply='> '+radio;'''
        .replace('LOST_PAUSE_ACK', 'true' if lost_pause_ack else 'false')
        .replace('RESTORE_RETRY', 'true' if restore_retry else 'false'))
    harness = replace_once(harness,
        "if(!otaData)throw Error('installed before data');",
        "if(!otaData)throw Error('installed before data');"
        "if(repeat!=='off')throw Error('Target forwarding resumed before installation');")
    if missing_module:
        harness = replace_once(harness, "installed=true;reply='OK';",
            "installed=true;delete window.LoraOtaQuiet;reply='OK';")
    harness = replace_once(harness, 'otaData=true;',
        "if(repeat!=='off')throw Error('Target forwarding resumed during transfer');otaData=true;")
    harness = replace_once(harness,
        "    el('inspect').click();await wait(()=>!el('inspect').disabled);",
        "    el('quiet-forwarding').checked=true;"
        + ("window.testLink.options.remoteTimeout=250;" if lost_pause_ack else '')
        + "el('inspect').click();await wait(()=>!el('inspect').disabled);")
    if disconnect_after_prepare:
        harness = replace_once(harness,
            "el('update').click();await wait(()=>['complete','error'].includes(el('workflow-status').dataset.state));"
            "const workflowState=el('workflow-status').dataset.state;"
            "if(workflowState!=='complete')throw Error(el('workflow-status').textContent);",
            "const workflowState='prepared';")
    harness = replace_once(harness,
        "const workflowState=" + ("'prepared';" if disconnect_after_prepare else "el('workflow-status').dataset.state;"),
        "const workflowState=" + ("'prepared';" if disconnect_after_prepare else "el('workflow-status').dataset.state;")
        + "const quietAfterUpdate={repeat,record:JSON.parse(localStorage.getItem(quietKey)||'null'),"
        "status:el('quiet-forwarding-status').textContent,workflowStatus:el('workflow-status').textContent};")
    if restore_retry:
        reconnect = r'''
            const oldTransport=window.testLink;
            el('disconnect').click();await wait(()=>!el('connect').disabled);
            if(forwardingWrites.length!==2||repeat!=='off')throw Error('Disconnect retried restoration automatically');
            el('password').value='password';el('connect').click();await wait(()=>!el('inspect').disabled);
            beforeExplicitRestore.reconnect={fresh:window.testLink!==oldTransport,source:window.testLink.sourceKey,
              knownBeforeContact:window.testLink.knownRepeater,proofAt:timeline.length};
            if(!beforeExplicitRestore.reconnect.fresh||window.testLink.knownRepeater)
              throw Error('Expected a fresh unselected target connection');
            const restoreTarget=document.querySelector('button[data-contact-key="'+key+'"]');
            if(!restoreTarget)throw Error('Reconnect target contact missing');restoreTarget.click();
            beforeExplicitRestore.reconnect.inputKey=el('key').value;
            beforeExplicitRestore.reconnect.knownAfterContact=window.testLink.knownRepeater;
        ''' if reconnect_restore else ''
        harness = replace_once(harness,
            "if(workflowState!=='complete')throw Error(el('workflow-status').textContent);",
            "if(workflowState!=='error')throw Error('Expected pending forwarding restoration');"
            "beforeExplicitRestore={writes:forwardingWrites.length,record:JSON.parse(localStorage.getItem(quietKey)||'null')};"
            "if(!beforeExplicitRestore.record||beforeExplicitRestore.writes!==2)throw Error('Restoration retried automatically');"
            + reconnect +
            "await wait(()=>!el('restore-forwarding').disabled);"
            "if(el('restore-forwarding').hidden)throw Error('Restore forwarding action is hidden');"
            "userRestoreConfirmed=true;el('restore-forwarding').click();"
            "await wait(()=>!localStorage.getItem(quietKey)&&!el('disconnect').disabled);"
            "if(beforeExplicitRestore.reconnect){beforeExplicitRestore.reconnect.knownAfterRestore=window.testLink.knownRepeater;"
            "beforeExplicitRestore.reconnect.proofs=timeline.slice(beforeExplicitRestore.reconnect.proofAt)"
            ".filter(row=>row[0]==='remote'&&row[1]==='get public.key').length;}")
    if missing_module:
        harness = replace_once(harness,
            "if(workflowState!=='complete')throw Error(el('workflow-status').textContent);",
            "if(workflowState!=='error')throw Error('Pending restoration without its module was reported complete');"
            "await wait(()=>!el('disconnect').disabled);")
    harness = replace_once(harness,
        'timeline,cmds,frames:port.frames,fileReads,packageBuilds});',
        "forwardingWrites,forwardingReads,repeat,quietRecord:JSON.parse(localStorage.getItem(quietKey)||'null'),"
        'timeline,cmds,frames:port.frames,fileReads,packageBuilds});')
    return replace_once(harness,
        'workflowState,installed,otaData,blockedInstallReplies,inspectStatus,prepared,checked,profiles,defaults,timeline,',
        "quietDefault:el('quiet-forwarding').defaultChecked,beforeExplicitRestore,forwardingWrites,forwardingReads,repeat,quietAfterUpdate,"
        "quietRecord:JSON.parse(localStorage.getItem(quietKey)||'null'),"
        'workflowState,installed,otaData,blockedInstallReplies,inspectStatus,prepared,checked,profiles,defaults,timeline,')


def disconnect_during_pause_read_harness():
    """A full-image Prepare has no worker AbortController; Disconnect must still cancel its pause."""
    harness = quiet_forwarding_harness()
    harness = replace_once(harness, 'const forwardingWrites=[],forwardingReads=[],quietKey=',
        'let cancellation=null;const forwardingWrites=[],forwardingReads=[],quietKey=')
    harness = replace_once(harness,
        "forwardingReads.push({repeat,installed,sourceTemp,targetTemp});reply='> '+repeat;", r'''
          forwardingReads.push({repeat,installed,sourceTemp,targetTemp});
          if(!cancellation&&sourceTemp==='active'&&targetTemp==='active'&&forwardingWrites.length===0){
            cancellation={sourceTemp,targetTemp,prepareStopHidden:el('prepare-stop').hidden,
              record:JSON.parse(localStorage.getItem(quietKey)||'null')};
            // This runs while the real remote GET is pending. close() changes
            // the disconnect generation immediately, then waits for Prepare;
            // the physical stream therefore remains open for this GET reply.
            el('disconnect').click();
            cancellation.afterClick={running:window.testLink.running,portOpened:window.testLink.portOpened,
              readerPresent:!!window.testLink.reader,writerPresent:!!window.testLink.writer};
          }
          reply='> '+repeat;''')
    return replace_once(harness,
        "el('quiet-forwarding').checked=true;el('inspect').click();await wait(()=>!el('inspect').disabled);",
        "el('quiet-forwarding').checked=true;el('inspect').click();"
        "await wait(()=>cancellation&&!el('connect').disabled);"
        "await report({cancellation,forwardingWrites,forwardingReads,repeat,installed,otaData,fileReads,packageBuilds,"
        "quietRecord:JSON.parse(localStorage.getItem(quietKey)||'null'),timeline,cmds,"
        "status:el('status').textContent,workflowState:el('workflow-status').dataset.state});return;")


def already_installed_goal_harness(*, restore_fails=False):
    """A saved base file enters the selected-goal shortcut with forwarding still paused."""
    harness = quiet_forwarding_harness(original='off', disconnect_after_prepare=True)
    harness = replace_once(harness,
        "el('quiet-forwarding').checked=true;el('inspect').click();",
        "installed=true;const oldBase=new DataTransfer();"
        "oldBase.items.add(new File([Uint8Array.of(0)],'old-installed.bin'));el('base').files=oldBase.files;"
        "localStorage.setItem(quietKey,JSON.stringify({version:1,source,target:key,setting:'repeat',original:'on',"
        "state:'paused',restoreAttempted:false}));"
        "el('quiet-forwarding').checked=true;el('inspect').click();")
    harness = replace_once(harness,
        "if(!prepared||inspectStatus.includes('Error:'))throw Error(inspectStatus);", '')
    harness = replace_once(harness, "const workflowState='prepared';",
        "const workflowState=el('workflow-status').dataset.state;")
    if restore_fails:
        harness = replace_once(harness,
            "if(false&&text==='set repeat on'&&++restoreAttempts===1)",
            "if(true&&text==='set repeat on'&&++restoreAttempts===1)")
    return harness


def failure_after_first_pause_harness():
    """Fail a real later RF read before the first Prepare releases its busy state."""
    harness = quiet_forwarding_harness(disconnect_after_prepare=True)
    harness = replace_once(harness, 'const forwardingWrites=[],forwardingReads=[],quietKey=',
        'let injectedFailure=false;const forwardingWrites=[],forwardingReads=[],quietKey=')
    harness = replace_once(harness, "else if(text==='get tempradio2')reply=", r'''
        else if(text==='get tempradio2'&&repeat==='off'&&forwardingWrites.length&&!injectedFailure){
          injectedFailure=true;reply='Error: fixture later radio verification failed';
        }
        else if(text==='get tempradio2')reply=''')
    harness = replace_once(harness,
        'installed,sourceTemp,targetTemp,profile:selectedProfile,userConfirmed:userRestoreConfirmed,',
        "prepareBusy:el('inspect').disabled,guarded:window.testLink.guard,"
        'installed,sourceTemp,targetTemp,profile:selectedProfile,userConfirmed:userRestoreConfirmed,')
    harness = replace_once(harness,
        "if(!prepared||inspectStatus.includes('Error:'))throw Error(inspectStatus);", '')
    harness = replace_once(harness, "const workflowState='prepared';",
        "const workflowState=el('workflow-status').dataset.state;")
    return replace_once(harness, "quietDefault:el('quiet-forwarding').defaultChecked,",
        "injectedFailure,quietDefault:el('quiet-forwarding').defaultChecked,")


class BrowserQuietForwardingRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def assert_complete(self, result):
        self.assertNotIn('error', result, result)
        self.assertTrue(result['prepared'], result)
        self.assertTrue(result['quietDefault'], result)
        self.assertEqual(result['workflowState'], 'complete', result)
        self.assertTrue(result['installed'], result)
        self.assertTrue(result['otaData'], result)
        self.assertIsNone(result['quietAfterUpdate']['record'], result)
        self.assertIsNone(result['quietRecord'], result)

    def assert_pause_record(self, snapshot):
        record = snapshot['record']
        self.assertEqual(record['version'], 1)
        self.assertEqual(record['source'], 'aa' * 32)
        self.assertEqual(record['target'], 'bb' * 32)
        self.assertEqual(record['setting'], 'repeat')
        self.assertEqual(record['original'], 'on')
        self.assertEqual(record['state'], 'pause-pending')
        self.assertFalse(record['restoreAttempted'])
        self.assertNotIn('password', json.dumps(record).lower())

    def test_pause_follows_live_channel_proof_and_original_is_restored_after_verified_install(self):
        result = run_harness(quiet_forwarding_harness(), timeout=75)
        self.assert_complete(result)
        self.assertEqual(result['repeat'], 'on', result)
        writes = result['forwardingWrites']
        self.assertEqual([row['command'] for row in writes], ['set repeat off', 'set repeat on'], result)
        off, on = writes
        self.assert_pause_record(off)
        self.assertEqual((off['sourceTemp'], off['targetTemp'], off['profile']), ('active', 'active', 1), result)
        self.assertEqual(off['leaseProof']['sourceProfile'], 1, result)
        self.assertEqual(off['leaseProof']['targetProfile'], 1, result)
        self.assertEqual(off['leaseProof']['tuple'], [909.95, 500, 5, 7], result)
        self.assertFalse(off['installed'], result)
        self.assertTrue(on['installed'], result)
        self.assertEqual(on['updateRecord']['phase'], 'installed', result)
        self.assertEqual(on['record']['state'], 'restore-pending', result)
        self.assertTrue(on['record']['restoreAttempted'], result)
        self.assertTrue(any(row['repeat'] == 'off' and not row['installed'] for row in result['forwardingReads']), result)
        timeline = result['timeline']
        off_at = timeline.index(['remote', 'set repeat off'])
        active_at = max(i for i, row in enumerate(timeline[:off_at]) if row == ['source-active'])
        self.assertIn(['remote', 'get public.key'], timeline[active_at + 1:off_at], result)
        self.assertIn(['remote', 'get radio.flood'], timeline[:off_at], result)
        self.assertIn(['remote', 'get repeat'], timeline[:off_at], result)
        self.assertFalse(any(row[0] == 'local' and row[1] in ('get repeat', 'set repeat off', 'set repeat on')
                             for row in timeline if len(row) > 1), result)

    def test_an_originally_off_target_is_never_enabled_or_given_a_restoration_write(self):
        result = run_harness(quiet_forwarding_harness(original='off'), timeout=75)
        self.assert_complete(result)
        self.assertEqual(result['repeat'], 'off', result)
        self.assertEqual(result['forwardingWrites'], [], result)
        self.assertTrue(result['forwardingReads'], result)
        self.assertTrue(all(row['repeat'] == 'off' for row in result['forwardingReads']), result)

    def test_a_lost_pause_ack_is_resolved_by_readback_without_repeating_the_setter(self):
        result = run_harness(quiet_forwarding_harness(lost_pause_ack=True), timeout=75)
        self.assert_complete(result)
        self.assertEqual(result['repeat'], 'on', result)
        self.assertEqual([row['command'] for row in result['forwardingWrites']],
                         ['set repeat off', 'set repeat on'], result)
        self.assert_pause_record(result['forwardingWrites'][0])

    def test_disconnect_after_prepare_restores_the_original_without_starting_an_update(self):
        result = run_harness(quiet_forwarding_harness(disconnect_after_prepare=True), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['prepared'], result)
        self.assertEqual(result['workflowState'], 'prepared', result)
        self.assertFalse(result['installed'], result)
        self.assertFalse(result['otaData'], result)
        self.assertEqual(result['repeat'], 'on', result)
        self.assertIsNone(result['quietRecord'], result)
        self.assertEqual([row['command'] for row in result['forwardingWrites']],
                         ['set repeat off', 'set repeat on'], result)
        self.assert_pause_record(result['forwardingWrites'][0])
        self.assertFalse(any(command.startswith(('ota pull ', 'ota installhash ')) for command in result['cmds']), result)

    def test_failed_restoration_stays_pending_until_the_user_selects_restore_forwarding(self):
        result = run_harness(quiet_forwarding_harness(restore_retry=True), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertTrue(result['otaData'], result)
        self.assertEqual(result['workflowState'], 'error', result)
        self.assertEqual(result['beforeExplicitRestore']['writes'], 2, result)
        pending = result['beforeExplicitRestore']['record']
        self.assertEqual(pending['state'], 'restore-pending', result)
        self.assertTrue(pending['restoreAttempted'], result)
        self.assertEqual([row['command'] for row in result['forwardingWrites']],
                         ['set repeat off', 'set repeat on', 'set repeat on'], result)
        self.assertEqual(result['repeat'], 'on', result)
        self.assertIsNone(result['quietRecord'], result)

    def test_missing_restoration_module_with_a_pending_record_never_reports_update_complete(self):
        result = run_harness(quiet_forwarding_harness(missing_module=True), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertTrue(result['otaData'], result)
        self.assertEqual(result['workflowState'], 'error', result)
        self.assertIn('restoration support did not load', result['quietAfterUpdate']['workflowStatus'], result)
        self.assertEqual(result['repeat'], 'off', result)
        self.assertEqual([row['command'] for row in result['forwardingWrites']], ['set repeat off'], result)
        self.assertEqual(result['quietRecord']['state'], 'paused', result)
        self.assertEqual(result['quietRecord']['original'], 'on', result)
        self.assertFalse(result['quietRecord']['restoreAttempted'], result)
        self.assertEqual(result['quietAfterUpdate']['record'], result['quietRecord'], result)

    def test_reconnect_restore_selects_and_proves_the_saved_target_only_after_an_explicit_user_action(self):
        result = run_harness(quiet_forwarding_harness(reconnect_restore=True), timeout=60)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertEqual(result['workflowState'], 'error', result)
        recovery = result['beforeExplicitRestore']
        self.assertEqual(recovery['writes'], 2, result)
        self.assertEqual(recovery['record']['state'], 'restore-pending', result)
        self.assertTrue(recovery['record']['restoreAttempted'], result)
        connection = recovery['reconnect']
        self.assertTrue(connection['fresh'], result)
        self.assertEqual(connection['source'], 'aa' * 32, result)
        self.assertFalse(connection['knownBeforeContact'], result)
        self.assertFalse(connection['knownAfterContact'], result)
        self.assertEqual(connection['inputKey'], 'bb' * 32, result)
        self.assertEqual(connection['knownAfterRestore'], 'bb' * 32, result)
        self.assertGreaterEqual(connection['proofs'], 1, result)
        writes = result['forwardingWrites']
        self.assertEqual([row['command'] for row in writes], ['set repeat off', 'set repeat on', 'set repeat on'], result)
        self.assertEqual([row['userConfirmed'] for row in writes], [False, False, True], result)
        self.assertEqual((writes[-1]['source'], writes[-1]['target']), ('aa' * 32, 'bb' * 32), result)
        self.assertEqual(writes[-1]['record']['original'], 'on', result)
        self.assertEqual(result['repeat'], 'on', result)
        self.assertIsNone(result['quietRecord'], result)

    def test_disconnect_during_pause_read_cancels_off_before_the_usb_stream_is_closed(self):
        result = run_harness(disconnect_during_pause_read_harness(), timeout=60)
        self.assertNotIn('error', result, result)
        cancelled = result['cancellation']
        self.assertEqual((cancelled['sourceTemp'], cancelled['targetTemp']), ('active', 'active'), result)
        self.assertTrue(cancelled['prepareStopHidden'], result)
        self.assertIsNone(cancelled['record'], result)
        self.assertEqual(cancelled['afterClick'], {
            'running':True, 'portOpened':True, 'readerPresent':True, 'writerPresent':True}, result)
        self.assertEqual(result['forwardingWrites'], [], result)
        self.assertEqual(result['repeat'], 'on', result)
        self.assertIsNone(result['quietRecord'], result)
        self.assertFalse(result['installed'], result)
        self.assertFalse(result['otaData'], result)
        self.assertEqual((result['fileReads'], result['packageBuilds']), (1, 1), result)
        self.assertIn('cancel', result['status'].lower(), result)
        self.assertNotEqual(result['workflowState'], 'complete', result)
        self.assertFalse(any(command.startswith(('ota pull ', 'ota installhash ')) for command in result['cmds']), result)

    def test_already_installed_selected_goal_restores_its_pending_record_before_reporting_complete(self):
        result = run_harness(already_installed_goal_harness(), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['workflowState'], 'complete', result)
        self.assertIn('already reports the selected firmware', result['quietAfterUpdate']['workflowStatus'], result)
        self.assertEqual((result['fileReads'], result['packageBuilds']), (1, 0), result)
        self.assertFalse(result['otaData'], result)
        self.assertEqual([row['command'] for row in result['forwardingWrites']], ['set repeat on'], result)
        restored = result['forwardingWrites'][0]
        self.assertEqual(restored['record']['original'], 'on', result)
        self.assertEqual(restored['record']['state'], 'restore-pending', result)
        self.assertEqual(result['quietAfterUpdate']['repeat'], 'on', result)
        self.assertIsNone(result['quietAfterUpdate']['record'], result)
        self.assertIsNone(result['quietRecord'], result)
        self.assertFalse(any(command.startswith(('ota pull ', 'ota installhash ', 'set tempradio2 ', 'tempradio '))
                             for command in result['cmds']), result)

    def test_already_installed_selected_goal_cannot_report_complete_if_restoration_fails(self):
        result = run_harness(already_installed_goal_harness(restore_fails=True), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['workflowState'], 'error', result)
        self.assertIn('restoration is still pending', result['quietAfterUpdate']['workflowStatus'], result)
        self.assertEqual((result['fileReads'], result['packageBuilds']), (1, 0), result)
        self.assertEqual([row['command'] for row in result['forwardingWrites']], ['set repeat on'], result)
        self.assertEqual(result['repeat'], 'off', result)
        self.assertEqual(result['quietRecord']['state'], 'restore-pending', result)
        self.assertTrue(result['quietRecord']['restoreAttempted'], result)
        self.assertFalse(result['otaData'], result)

    def test_first_prepare_failure_after_pause_restores_before_releasing_the_live_guard(self):
        result = run_harness(failure_after_first_pause_harness(), timeout=45)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['injectedFailure'], result)
        self.assertEqual(result['workflowState'], 'error', result)
        self.assertFalse(result['prepared'], result)
        writes = result['forwardingWrites']
        self.assertEqual([row['command'] for row in writes], ['set repeat off', 'set repeat on'], result)
        self.assertTrue(all(row['prepareBusy'] and row['guarded'] for row in writes), result)
        self.assertTrue(all(not row['userConfirmed'] and not row['installed'] for row in writes), result)
        self.assertEqual(writes[-1]['target'], 'bb' * 32, result)
        self.assertEqual(result['repeat'], 'on', result)
        self.assertIsNone(result['quietAfterUpdate']['record'], result)
        self.assertIsNone(result['quietRecord'], result)
        self.assertFalse(result['otaData'], result)


if __name__ == '__main__':
    unittest.main()
