#!/usr/bin/env python3
"""Exercise actual nRF planning and fail-closed installation in Chrome."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER, HARNESS
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_nrf_runtime import run_harness


def chain_harness(*,attested=True,bad_identity=False,external=False):
    harness=one_replace(HARNESS,
        'window.setTimeout=(fn,ms,...a)=>originalTimer(fn,ms===20000?5:ms,...a);',
        'window.setTimeout=(fn,ms,...a)=>originalTimer(fn,[2000,20000,90000].includes(ms)?5:ms,...a);')
    harness=one_replace(harness,'Date.now()+15000','Date.now()+90000')
    harness=one_replace(harness,
        "let folder=false, mid='', state='idle', installed=false, newHash='', job='', jobResult='';",
        "let folder=false, mid='', state='idle', installed=false, newHash='', job='', jobResult='', stages=[],stageIndex=0,targetTemp=true,total=0,served=[],totals=[],simModule,actualApplications=0,simWorkers=0,simMessages=0;\n"
        "  const RealWorker=window.Worker; window.Worker=class extends RealWorker { constructor(url,...args){super(url,...args);this.isSim=String(url).includes('otafix_sim/worker.js');if(this.isSim)simWorkers++;} postMessage(msg,...args){if(this.isSim)simMessages++;return super.postMessage(msg,...args);} };")
    harness=one_replace(harness,'    remote(cmd){','    async remote(cmd){')
    harness=one_replace(harness,":this.remote(cmd.slice(('ota.peer cmd.acl '+target+' ').length))",":await this.remote(cmd.slice(('ota.peer cmd.acl '+target+' ').length))")
    harness=one_replace(harness,
        "'OTA hw=board | target:12345678 | maxblk:1024 | '",
        "'OTA hw=board | target:12345678 | maxblk:1024 | bl:apply | '")
    harness=one_replace(harness,
        "(installed?newHash:'1122334455667788')+' | maxblk:1024'",
        "stages[stageIndex].body+' | maxblk:1024 | bootloader: apply OK (abi=3 codecs=0x4 stage=D4000)'")
    harness=one_replace(harness,
        "if(cmd==='ver') return installed?'v1.18.0.1':'v1.17.1.9';",
        "if(cmd==='ver')return stages[stageIndex].ver;\n"
        "      if(cmd==='get bootloader.ver')return '> 0.11.0-OTAFIX2.4.12';\n"
        "      if(cmd==='ota bootloader status')return 'BL board=239A0029 target=2D0DF000 name=4631_DFU crc="+('DEADBEEF' if bad_identity else 'FF374B1B')+" abi=3 caps=0A | staged:none mid=-- hash=--';")
    harness=one_replace(harness,
        "if(cmd==='get tempradio')return radio;",
        "if(cmd==='get tempradio')return targetTemp?radio:'off';\n"
        "      if(cmd==='get tempradioat'||cmd==='get radioat')return 'slots:';\n"
        "      if(cmd.startsWith('tempradio.ifidle ')){targetTemp=true;return 'OK - applying';}")
    harness=one_replace(harness,
        "if(/^ota installhash [0-9a-f]{64}$/.test(cmd)){installed=true;return 'OK';}",
        """if(/^ota installhash [0-9a-f]{64}$/.test(cmd)){
          if(stageIndex>=2)throw Error('unexpected install');
          const pkg=Uint8Array.from(served), normal=pkg.slice();normal.fill(0,201,205);
          if(hex(new Uint8Array(await crypto.subtle.digest('SHA-256',normal)))!==cmd.slice(16))throw Error('install hash mismatch');
          const {exports:e}=await WebAssembly.instantiate(simModule,{}), mem=new Uint8Array(e.memory.buffer);
          const old=stages[stageIndex].image, expected=stages[stageIndex+1].image;
          mem.set(old,e.sim_base());mem.set(pkg,e.sim_package());
          if(e.sim_apply(old.length,pkg.length,0xD4000)!==1||e.sim_status()!==0xB8||e.sim_commit_size()!==expected.length)
            throw Error('simulated target rejected update');
          const output=mem.subarray(e.sim_result(),e.sim_result()+expected.length);
          if(output.some((value,i)=>value!==expected[i]))throw Error('simulated target image mismatch');
          actualApplications++;stageIndex++;state='idle';targetTemp=false;return 'OK';}""")
    harness=one_replace(harness,
        "if(cmd.startsWith('ota pull ')){if(!mid||!cmd.toLowerCase().includes(mid))throw Error('wrong pull');state='fetching';queueMicrotask(()=>this.push(req(3,[0,0,0,0,0,4,0])));return 'OK';}",
        "if(cmd.startsWith('ota pull ')){if(!mid||!cmd.toLowerCase().includes(mid))throw Error('wrong pull');state='fetching';queueMicrotask(()=>this.nextRead());return 'OK';}")
    harness=one_replace(harness,
        "    async write(bytes){",
        """    nextRead(){const off=served.length,n=Math.min(192,total-off);
      if(n<=0)throw Error('invalid source length');
      this.push(req(3,[0,off&255,(off>>>8)&255,(off>>>16)&255,(off>>>24)&255,n&255,(n>>>8)&255]));}
    async write(bytes){""")
    harness=one_replace(harness,
        "if(bytes[2]===2&&bytes[3]===0)mid=hex(bytes.slice(4,8));",
        "if(bytes[2]===2&&bytes[3]===0){mid=hex(bytes.slice(4,8));if(bytes[16]!==2)throw Error('not in-place');total=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength).getUint32(18,true);served=[];}")
    harness=one_replace(harness,
        "if(bytes[2]===3&&bytes[3]===0){if(dec.decode(bytes.slice(4,8))!=='mOTA')throw Error('wrong package bytes');state='ready';}",
        """if(bytes[2]===3&&bytes[3]===0){served.push(...bytes.slice(4,-1));
          if(served.length<total)queueMicrotask(()=>this.nextRead());
          else {if(served.length!==total||dec.decode(Uint8Array.from(served.slice(0,4)))!=='mOTA'||served[64]!==2)throw Error('wrong package');totals.push(total);state='ready';}}
""")
    begin=harness.index("    try {\n      const image=new Uint8Array(100)")
    end=harness.index("    } catch(e){await report(",begin)
    if begin<0 or end<0: raise AssertionError('Could not find original browser harness body')
    replacement="""    try {
      simModule=await WebAssembly.compile(await (await fetch('/_javascript/otafix_sim/build/otafix-2.4.12-rak4631-internal.wasm')).arrayBuffer());
      let random=24680;const rand=()=>{random^=random<<13;random^=random>>>17;random^=random<<5;return random>>>0;};
      const length=356000,at=length-56,layout=at-24;
      const a=Uint8Array.from({length},()=>rand()&255),b=a.slice();
      for(let i=128;i<layout/2;i++)b[i]^=rand()&255;
      const c=b.slice();for(let i=Math.ceil(layout/2);i<layout;i++)c[i]^=rand()&255;
      const make=async (image,packed,ver)=>{
        const v=new DataView(image.buffer);
        image.set(enc.encode('OTA: status | stats | ls | get <id> flash | install'),0);
        image.set(enc.encode('invalid in-place patch geometry'),64);
        image.set(enc.encode('ota installhash <64-hex-container-sha256>'),112);
        image.set(enc.encode('tempradio.ifidle '),160);
        image.set(enc.encode('mOTALay1'),layout);v.setUint8(layout+8,1);v.setUint8(layout+9,0);v.setUint16(layout+10,24,true);
        v.setUint32(layout+12,0x26000,true);v.setUint32(layout+16,0xED000,true);v.setUint32(layout+20,0xED000,true);
        image.set(enc.encode('EndF'),at);v.setUint32(at+4,at,true);
        const body=new Uint8Array(await crypto.subtle.digest('SHA-256',image.slice(0,at))).slice(0,8);
        image.set(body,at+8);v.setUint32(at+16,packed,true);v.setUint32(at+20,0x12345678,true);
        image.fill(0,at+24);image.set(enc.encode('board'),at+24);return {image,body:hex(body),ver};
      };
      stages=[await make(a,0x01110109,'v1.17.1.9'),await make(b,0x01120001,'v1.18.0.1'),await make(c,0x01120002,'v1.18.0.2')];
      const put=(name,stage)=>{const dt=new DataTransfer();dt.items.add(new File([stage.image],name+'.bin'));return dt.files;};
      el('base').files=put('base',stages[0]);el('chain-files').files=put('intermediate',stages[1]);el('file').files=put('goal',stages[2]);el('key').value=target;
      el('chain-attest').checked=ATTESTED;
      el('connect').click();await wait(()=>!el('chain-plan').disabled);
      el('chain-plan').click();await wait(()=>!el('chain-run').disabled||el('status').textContent.includes('Error:'));
      if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
      const planned=el('status').textContent;
      el('chain-run').click();await wait(()=>el('status').textContent.includes('Update sequence complete;')||el('status').textContent.includes('Error:'));
      await report({status:el('status').textContent,planned,cmds,stageIndex,folder,actualApplications,simWorkers,simMessages,totals});

"""
    if not attested or bad_identity or external:
        replacement=one_replace(replacement,
          "el('chain-plan').click();await wait(()=>!el('chain-run').disabled||el('status').textContent.includes('Error:'));",
          "el('chain-plan').click();await wait(()=>el('status').textContent.includes('Error:'));"
          "await report({status:el('status').textContent,cmds,stageIndex,folder,runDisabled:el('chain-run').disabled});return;")
    replacement=replacement.replace('ATTESTED','true' if attested else 'false')
    harness=harness[:begin]+replacement+harness[end:]
    if external:
        harness=one_replace(harness,"maxblk:1024 | bl:apply |", "maxblk:1024 | bl:QSPI |")
        harness=one_replace(harness,"bootloader: apply OK (abi=3 codecs=0x4 stage=D4000)","QSPI store:2048K | bootloader: QSPI apply OK (abi=3 codecs=0x4)")
        harness=one_replace(harness,"if(cmd==='get bootloader.ver')return '> 0.11.0-OTAFIX2.4.12';", "if(cmd==='ota qspi')return 'QSPI jedec=856015 size=2048K sr1=00 stage=jedec';\n      if(cmd==='get bootloader.ver')return '> 0.11.0-OTAFIX2.4.12';")
    return harness


class BrowserLoraOtaChainRuntimeTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('A Chromium-family browser is required')
            self.skipTest('Chromium-family browser unavailable')

    def test_actual_planner_and_two_simulated_installs(self):
        result=run_harness(chain_harness(),timeout=120)
        self.assertNotIn('error',result,result)
        self.assertIn('base -> intermediate-1 (delta); intermediate-1 -> goal (delta)',result['planned'])
        self.assertIn('Update sequence complete; verified 2 installation(s)',result['status'])
        self.assertEqual(result['stageIndex'],2)
        self.assertEqual(result['actualApplications'],2)
        self.assertEqual(result['simMessages'],4)
        self.assertGreaterEqual(result['simWorkers'],2)
        self.assertEqual(len(result['totals']),2)
        self.assertFalse(result['folder'])
        installs=[c for c in result['cmds'] if c.startswith('remote ota installhash ')]
        self.assertEqual(len(installs),2)
        self.assertNotIn('remote ota install',result['cmds'])

    def test_unknown_bootloader_identity_is_refused(self):
        result=run_harness(chain_harness(bad_identity=True),timeout=45)
        self.assertNotIn('error',result,result)
        self.assertIn('Error:',result['status'])
        self.assertTrue(result['runDisabled'])
        self.assertEqual(result['stageIndex'],0)
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))

    def test_external_storage_uses_single_update(self):
        result=run_harness(chain_harness(external=True),timeout=45)
        self.assertNotIn('error',result,result)
        self.assertIn('use the single-update controls',result['status'])
        self.assertTrue(result['runDisabled'])
        self.assertEqual(result['stageIndex'],0)
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))

    def test_unattested_intermediate_cannot_be_planned_or_installed(self):
        result=run_harness(chain_harness(attested=False),timeout=45)
        self.assertNotIn('error',result,result)
        self.assertIn('tested on this exact hardware',result['status'])
        self.assertTrue(result['runDisabled'])
        self.assertEqual(result['stageIndex'],0)
        self.assertFalse(result['folder'])
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))
        self.assertNotIn('remote ota install',result['cmds'])


if __name__=='__main__':unittest.main()
