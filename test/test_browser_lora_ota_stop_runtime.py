#!/usr/bin/env python3
"""Stop and replace a partial update through the main UI without reopening USB."""
import os
import unittest

from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_runtime import BROWSER


def stop_replace_harness():
    script = HARNESS

    def replace(old, new):
        nonlocal script
        assert script.count(old) == 1, old
        script = script.replace(old, new, 1)

    replace("let expected,pkg,mid=''", "let firstTransfer=true,opens=0,closes=0,sourceSeconds=100,expected,pkg,mid=''")
    replace('async open(){this.readable=', 'async open(){opens++;this.readable=')
    replace('async close(){}', 'async close(){closes++;}')
    replace("else if(text==='ver')reply=installed?'v1.18.0.1':'v1.17.1.9';",
            "else if(text==='ver')reply=installed?'v1.18.0.2':'v1.17.1.9';")
    replace("else if(text.startsWith('ota pull ')){", """else if(text==='ota cancel'){
          state='idle';manifest=[];proof=leaves=otaData=false;advertSeen=false;firstTransfer=false;mid=hex(pkg.merkleRoot);reply='OK';
        }
        else if(text.startsWith('ota pull ')){""")
    replace("throw Error('wrong proof');proof=true;", "throw Error('wrong proof');proof=!firstTransfer;")
    replace("else if(text==='get tempradio')value='TempRadio active: '+radio+' 100s left,preamble=32';", """
        else if(text==='get tempradio')value='TempRadio active: '+radio+' '+sourceSeconds+'s left,preamble=32';
        else if(['get radio2','get tempradio2','get radio2.cross'].includes(text))value='> off';
        else if(['get radioat','get tempradioat','get radioat2','get tempradioat2'].includes(text))value='> -none-';
        else if(text==='tempradio 910.525,62.5,7,5,120,32'){sourceSeconds=7200;value='OK';}""")
    old = """el('transfer').click();await wait(()=>!el('install').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    el('install').click();await wait(()=>el('status').textContent.includes('Installed and verified:')||el('status').textContent.includes('Error:'));"""
    new = """if(!el('transfer-stop').hidden)throw Error('Stop is visible before transfer');
    el('update').click();await wait(()=>otaData&&state==='fetching'&&!el('transfer-stop').hidden);
    const firstID=mid,selectedFile=el('file').files[0],selectedKey=el('key').value,startedStop=performance.now();
    el('transfer-stop').click();el('transfer-stop').click();
    await wait(()=>el('workflow-status').dataset.state==='ready'&&el('workflow-status').textContent.startsWith('Transfer stopped')&&!el('inspect').disabled);
    const stopElapsed=performance.now()-startedStop,stoppedStatus=el('status').textContent;
    if(stopElapsed>3000)throw Error('Stop waited for the regular transfer poll: '+stopElapsed);
    if(opens!==1||closes!==0||el('key').value!==selectedKey||el('file').files[0]!==selectedFile)throw Error('Stop lost USB or update choices');
    if(cmds.includes('ota cancel')||cmds.some(c=>c==='ota install'||c.startsWith('ota installhash ')))throw Error('Stop canceled or installed the target');
    if(!el('transfer-stop').hidden||el('update').disabled===false)throw Error('Stopped controls are incorrect');
    if(!el('update-progress').hidden&&el('update-progress').textContent.includes('Stopping'))throw Error('Stopped transfer still shows stopping');
    if(stoppedStatus.includes('Error:'))throw Error('Normal Stop reported an error');
    const firstJournal=JSON.parse(localStorage.getItem('meshcore-browser-lora-ota'));
    if(firstJournal.phase!=='pull attempted'||firstJournal.id!==firstID)throw Error('Stop lost the recovery record');
    expected=expected.slice();expected[0]=1;
    const secondView=new DataView(expected.buffer),secondBody=new Uint8Array(await crypto.subtle.digest('SHA-256',expected.slice(0,44))).slice(0,8);
    expected.set(secondBody,52);body=hex(secondBody);secondView.setUint32(60,0x01120002,true);
    pkg=await MeshCoreMotaPackage.parseMota(await MeshCoreMotaPackage.buildFullMota(expected));
    const secondID=hex(pkg.merkleRoot),next=new DataTransfer();next.items.add(new File([expected],'replacement.bin'));
    el('file').files=next.files;el('file').dispatchEvent(new Event('change'));el('inspect').click();
    await wait(()=>!el('update').disabled||el('workflow-status').dataset.state==='error');
    if(el('update').textContent!=='Replace and install update')throw Error('Replacement was not prepared: '+el('status').textContent);
    el('update').click();await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
    if(el('workflow-status').dataset.state!=='complete')throw Error(el('status').textContent);
    if(!el('transfer-stop').hidden)throw Error('Stop is visible after install');
    const before=cmds.slice();el('transfer-stop').dispatchEvent(new Event('click'));
    if(cmds.length!==before.length)throw Error('Stop was accepted outside transfer');
    await report({status:el('status').textContent,cmds,frames:port.frames,opens,closes,stopElapsed,firstID,secondID,installed,stoppedStatus});return;"""
    replace(old, new)
    return script


class BrowserStopTransferTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_stop_and_replace_partial_download_uses_the_same_usb_connection(self):
        result = run_harness(stop_replace_harness(), timeout=90)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['installed'], result)
        self.assertEqual([result['opens'], result['closes']], [1, 0])
        self.assertEqual(sum(frame[0] == 1 for frame in result['frames']), 1)
        self.assertNotEqual(result['firstID'], result['secondID'])
        for package in ('firstID', 'secondID'):
            self.assertEqual(sum(command.lower() == 'ota pull ' + result[package] + ' flash'
                                 for command in result['cmds']), 1, result)
        self.assertEqual(result['cmds'].count('ota cancel'), 1)
        self.assertEqual(sum(command.startswith('ota installhash ') for command in result['cmds']), 1)
        self.assertLess(result['stopElapsed'], 3000)


if __name__ == '__main__':
    unittest.main()
