#!/usr/bin/env python3
"""Drive the real updater UI with an old Companion's native USB folder."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_workflow_runtime import legacy_setup_harness


def replace_once(script, old, new):
    if script.count(old) != 1:
        raise AssertionError('Expected exactly one fixture anchor: ' + old[:80])
    return script.replace(old, new, 1)


def native_folder_harness(drop_status=False):
    script = legacy_setup_harness(rxps_on=False, matching=False)
    script = replace_once(script, 'Date.now()+45000', 'Date.now()+20000')
    script = replace_once(script, "let inbox=[],", "let folderMode=false,folderOns=0,folderOffs=0,folderReads=0,latePullAck=null,droppedStatus=false;let inbox=[],")
    script = replace_once(script, "else if(text==='get usb.raw.max')value='> 188';", "else if(text==='get usb.raw.max')value='> 176';")
    script = replace_once(script, "else if(text==='ota stats')value='Unknown command';", "else if(text==='ota stats')value='OTA | fetch idle';")
    script = replace_once(script, "else if(text==='ota folder')value='Unknown command';", "else if(text==='ota folder')value='folder=off serving=0:';")
    script = replace_once(script, "else if(text==='ota config')value='Unknown command';", "else if(text==='ota config')value='ota config: autofetch=off autoinstall=off hops=0';")
    script = replace_once(script, "installed=true;reply='OK';", "installed=true;targetTemp='910.525,62.5,7,5';reply='OK';")
    start = "else if(text.startsWith('ota pull ')){state='fetching';reply='OK';const request=(op,...rest)=>wire(Uint8Array.of(0x88,0,0,0x32,0,op,...unhex(mid),...rest));\n          setTimeout(()=>{this.push(request(4,3,0));this.push(request(10,1,0));this.push(request(6,0,0,1,0));},10);}"
    script = replace_once(script, start, "else if(text.startsWith('ota pull ')){state='fetching';reply='OK';}")
    old = "inbox.push(message(key,1,tag+('full_transfer'==='identity'&&text==='get public.key'?'> '+'dd'.repeat(32):reply)));"
    new = """{
            const result=message(key,1,tag+reply);
            if(text.startsWith('ota pull '))latePullAck=result;
            else if(DROP_STATUS&&text==='ota status'&&state!=='idle'&&!droppedStatus)droppedStatus=true;
            else inbox.push(result);
          }""".replace('DROP_STATUS', 'true' if drop_status else 'false')
    script = replace_once(script, old, new)
    anchor = "    async write(bytes){\n      if(bytes[0]!==60||bytes.length!==3+bytes[1]+256*bytes[2])throw Error('invalid binary frame');"
    replacement = r'''    async write(bytes){
      const request=(op,args=[])=>{let x=op;for(const b of args)x^=b;return Uint8Array.of(77,83,op,...args,x);};
      if(bytes[0]===109&&bytes[1]===115){
        if(!folderMode||bytes[3]!==0)throw Error('invalid native folder response');
        const op=bytes[2];
        if(op===1)queueMicrotask(()=>this.push(request(2,[0])));
        else if(op===2){
          if(hex(bytes.slice(4,8))!==mid)throw Error('wrong offered manifest');
          queueMicrotask(()=>{
            this.push(enc.encode('\r\nOK folder attached (serial): advertising 1/1 host mOTAs\r\n'));
            if(latePullAck){inbox.push(latePullAck);latePullAck=null;}
            if(state==='fetching')this.push(request(3,[0,8,0,0,0,4,0]));
          });
        }else if(op===3){
          if(hex(bytes.slice(4,8))!==hex(pkg.blob.slice(8,12)))throw Error('wrong native file bytes');
          folderReads++;otaData=true;state='ready';
        }else throw Error('unexpected native folder response '+op);
        return;
      }
      if(bytes[0]!==60){
        const line=dec.decode(bytes).trim();
        if(line==='ota folder on'){if(folderMode)throw Error('folder already on');folderMode=true;folderOns++;queueMicrotask(()=>this.push(request(1)));return;}
        if(line==='ota folder off'){if(!folderMode)throw Error('folder already off');folderMode=false;folderOffs++;queueMicrotask(()=>this.push(enc.encode('\r\nOK folder detached\r\nOK - Binary mode\r\n')));return;}
        throw Error('unexpected folder control '+line);
      }
      if(folderMode)throw Error('binary request during folder ownership');
      if(bytes.length!==3+bytes[1]+256*bytes[2])throw Error('invalid binary frame');'''
    script = replace_once(script, anchor, replacement)
    marker = "window.addEventListener('load',async()=>{try{"
    inject = "const Transport=LoraOtaCompanion.CompanionTransport;LoraOtaCompanion.CompanionTransport=class extends Transport{constructor(port,options){super(port,{...options,investigationTimeout:500,folderPollDelayMs:10,folderMaxWaitMs:100});}};"
    script = replace_once(script, marker, marker + inject)
    script = replace_once(script, "await report({timeline,sourceRxps,", "await report({folderOns,folderOffs,folderReads,droppedStatus,timeline,sourceRxps,")
    return script


class NativeFolderBrowserRuntime(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1':
                self.fail('Chromium required')
            self.skipTest('Chromium unavailable')

    def test_lost_pull_reply_and_one_lost_status_keep_native_source_serving(self):
        for drop_status in (False, True):
            with self.subTest(drop_status=drop_status):
                result = run_harness(native_folder_harness(drop_status), timeout=90)
                self.assertNotIn('error', result, result)
                self.assertTrue(result['installed'], result)
                self.assertTrue(result['otaData'], result)
                self.assertGreaterEqual(result['folderOns'], 1, result)
                self.assertGreaterEqual(result['folderReads'], 1, result)
                self.assertEqual(result['folderOns'], result['folderOffs'], result)
                self.assertEqual(result['droppedStatus'], drop_status, result)
                if drop_status:
                    self.assertGreaterEqual(result['folderOns'], 2, 'The source must resume around a lost status reply')
                self.assertEqual(sum(c.startswith('ota pull ') for c in result['cmds']), 1, result)
                self.assertFalse(any(c == 'ota cancel' for c in result['cmds']), result)


if __name__ == '__main__':
    unittest.main()
