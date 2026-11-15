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


def automatic_harness(*, equal_version=False, bad_resource=False, interrupt=False, three_steps=False,
                      missing_route=False, search_disabled=False, missing_ram=False, version_fallback=False):
    script = chain_harness()
    script = one_replace(script,"'get tempradio':radio,'get radio':radio,",
                         "'get tempradio':radio,'get radio':radio.split(',').slice(0,4).join(','),")
    script = one_replace(script,
        "if(cmd==='get tempradio')return targetTemp?radio:'off';",
        """if(cmd==='get tempradio')return targetTemp?radio:'off';
      if(cmd==='get public.key')return target;
      if(['get radio2','get tempradio2','get radio2.cross','get radioat2','get tempradioat2'].includes(cmd))return 'Error: unsupported';
      if(cmd.startsWith('tempradio ')){targetTemp=true;return 'OK - applying';}""")
    script = one_replace(script,
        "if(cmd==='ota folder')reply=",
        """if(['get radio2','get tempradio2','get radio2.cross','get radioat2','get tempradioat2'].includes(cmd))reply='Error: unsupported';
      if(cmd==='ota.peer radio'||cmd==='ota.peer radio radio')reply='OTApeer radio=radio';
      if(cmd.startsWith('tempradio '))reply='OK - applying';
      if(cmd==='get radioat'||cmd==='get tempradioat')reply='slots:';
      if(cmd==='ota folder')reply=""")
    start = script.index("      const put=(name,stage)=>")
    end = script.index("    } catch(e){await report(", start)
    replacement = r'''
      const digest=async bytes=>hex(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)));
      const hashes=await Promise.all(stages.map(stage=>digest(stage.image)));
      const index={schemaVersion:1,routes:[{from:hashes[0],to:hashes.at(-1),targetId:0x12345678,hwId:'board',
        images:stages.slice(1,-1).map((stage,i)=>({sha256:hashes[i+1],size:stage.image.length}))}]};
      const resources=new Map();for(let i=1;i<stages.length-1;i++){
        const gzip=new Uint8Array(await new Response(new Blob([stages[i].image]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer());
        resources.set(hashes[i],{encoding:'gzip-base64',data:btoa(Array.from(gzip,x=>String.fromCharCode(x)).join(''))});
      }
      const fetchOriginal=window.fetch.bind(window);let bridgeReads=0;
      window.fetch=(input,options)=>{
        const url=String(input),root='https://mikecarper.github.io/MeshCore/_data/lora_ota_bridges/';
        if(url===root+'index.json')return Promise.resolve(new Response(JSON.stringify(index)));
        const hash=url.startsWith(root)?url.slice(root.length,-5):'';
        if(resources.has(hash)){bridgeReads++;return Promise.resolve(new Response(JSON.stringify(BAD_RESOURCE?{encoding:'gzip-base64',data:'H4sIAAAA'}:resources.get(hash))));}
        return fetchOriginal(input,options);
      };
      const put=(name,stage)=>{const dt=new DataTransfer();dt.items.add(new File([stage.image],name+'.bin'));return dt.files;};
      el('base').files=put('base',stages[0]);el('file').files=put('goal',stages.at(-1));el('key').value=target;
      el('chain-attest').checked=false;
      if(SEARCH_DISABLED)el('auto-search').checked=false;
      el('connect').click();await wait(()=>!el('inspect').disabled);
      el('inspect').click();await wait(()=>!el('update').disabled||el('workflow-status').dataset.state==='error');
      const preparationLog=el('status').textContent;
      if(EXPECT_ERROR){await report({status:el('status').textContent,details:el('error-details').textContent,
        cmds,actualApplications,bridgeReads,disabled:el('update').disabled});return;}
      if(PREPARE_ONLY){await report({status:el('status').textContent,cmds,actualApplications,
        disabled:el('update').disabled,summary:el('update-summary').textContent});return;}
      if(el('update').disabled)throw Error(el('status').textContent);
      const prepared=el('update-summary').textContent;
      let interrupted=false;
      if(INTERRUPT){
        const remote=port.remote.bind(port);port.remote=async cmd=>{
          if(cmd.startsWith('ota pull ')&&stageIndex===1&&!interrupted){interrupted=true;return 'ERR simulated transfer interruption';}
          return remote(cmd);
        };
      }
      el('update').click();el('update').click();
      await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
      let recovered='';
      if(INTERRUPT){
        if(stageIndex!==1)throw Error('Interruption did not retain the first installed bridge');
        // Clear selected base and rebuild from IndexedDB through the normal
        // Prepare action. No intermediate files or advanced actions are used.
        el('base').value='';el('inspect').click();
        await wait(()=>!el('update').disabled||el('workflow-status').dataset.state==='error'&&!el('inspect').disabled);
        recovered=el('update').textContent;
        el('update').click();await wait(()=>el('workflow-status').dataset.state==='complete'||el('workflow-status').dataset.state==='error');
      }
      await report({status:el('status').textContent,notice:el('workflow-status').textContent,summary:el('update-summary').textContent,
        prepared,recovered,cmds,stageIndex,folder,actualApplications,totals,bridgeReads,preparationLog});
'''
    replacement = replacement.replace('BAD_RESOURCE', 'true' if bad_resource else 'false').replace('INTERRUPT', 'true' if interrupt else 'false')
    replacement = replacement.replace('SEARCH_DISABLED', 'true' if search_disabled else 'false')
    replacement = replacement.replace('PREPARE_ONLY', 'true' if version_fallback else 'false')
    replacement = replacement.replace('EXPECT_ERROR', 'true' if bad_resource or missing_route or search_disabled or missing_ram else 'false')
    script = script[:start] + replacement + script[end:]
    if missing_route:
        script = one_replace(script, 'JSON.stringify(index)', "JSON.stringify({schemaVersion:1,routes:[]})")
    if missing_ram or version_fallback:
        script = one_replace(script, 'v.setUint8(layout+9,0)', 'v.setUint8(layout+9,16)')
        script = one_replace(script, "if(cmd==='get bootloader.ver')return '> 0.11.0-OTAFIX2.4.12';",
            "if(cmd==='ota ram')return \"Unknown OTA command. Type 'ota help'.\";\n"
            "      if(cmd==='get bootloader.ver')return '> 0.11.0-OTAFIX2.4."+('6' if version_fallback else '5')+"';")
        if version_fallback:
            script = one_replace(script, 'stage=D4000', 'stage=ED000')
    if equal_version:
        script = one_replace(script, "make(a,0x01110109,'v1.17.1.9')", "make(a,0x01120001,'v1.18.0.1')")
    if three_steps:
        script = one_replace(script,'if(stageIndex>=2)', 'if(stageIndex>=stages.length-1)')
        script = one_replace(script,'const length=356000,', 'const length=460000,')
        script = one_replace(script,'i<layout/2;i++', 'i<layout/3;i++')
        script = one_replace(script,'const c=b.slice();for(let i=Math.ceil(layout/2);i<layout;i++)c[i]^=rand()&255;',
            'const c=b.slice();for(let i=Math.ceil(layout/3);i<2*layout/3;i++)c[i]^=rand()&255;\n'
            '      const d=c.slice();for(let i=Math.ceil(2*layout/3);i<layout;i++)d[i]^=rand()&255;')
        script = one_replace(script,"await make(c,0x01120002,'v1.18.0.2')",
            "await make(c,0x01120001,'v1.18.0.1'),await make(d,0x01120002,'v1.18.0.2')")
    return script


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

    def test_ordinary_buttons_automatically_load_and_install_two_complete_images(self):
        result = run_harness(automatic_harness(equal_version=True), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertIn('2 automatic update steps', result['prepared'])
        self.assertIn('Searching intermediate updates automatically', result['preparationLog'])
        self.assertNotIn('An intermediate update is needed.', result['preparationLog'])
        self.assertIn('Update complete', result['notice'])
        self.assertEqual(result['actualApplications'], 2)
        self.assertEqual(result['bridgeReads'], 1)
        self.assertFalse(result['folder'])
        self.assertEqual(len([c for c in result['cmds'] if c.startswith('remote ota installhash ')]), 2)

    def test_no_published_route_reports_exact_images_and_search_result(self):
        result = run_harness(automatic_harness(missing_route=True), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['disabled'])
        for value in ['base.bin', 'goal.bin', '1.17.1.9', '1.18.0.2', 'SHA-256',
                      'No published intermediates', 'Flash']:
            self.assertIn(value, result['details'])
        self.assertIn('No fitting automatic update path is published', result['status'])
        self.assertEqual(result['actualApplications'], 0)
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))

    def test_disabled_search_and_worker_geometry_are_in_the_failure_report(self):
        result = run_harness(automatic_harness(search_disabled=True), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['disabled'])
        for value in ['base.bin', 'goal.bin', 'Disabled in Update path options', 'workspace', 'staged']:
            self.assertIn(value, result['details'])
        self.assertEqual(result['bridgeReads'], 0)
        self.assertEqual(result['actualApplications'], 0)

    def test_missing_retained_ram_proof_reports_the_failed_capability_check(self):
        result = run_harness(automatic_harness(missing_ram=True), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['disabled'])
        self.assertIn('retained RAM', result['details'])
        self.assertIn('OTAFIX2.4.5', result['details'])
        self.assertEqual(result['actualApplications'], 0)
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))

    def test_older_application_automatically_uses_bootloader_version_cutoff(self):
        result = run_harness(automatic_harness(version_fallback=True), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertFalse(result['disabled'])
        self.assertIn('automatic update steps', result['summary'])
        self.assertIn('remote ota ram', result['cmds'])
        self.assertIn('remote get bootloader.ver', result['cmds'])
        self.assertIn('remote ota bootloader status', result['cmds'])
        self.assertEqual(result['actualApplications'], 0)

    def test_bad_intermediate_resource_blocks_every_firmware_write(self):
        result = run_harness(automatic_harness(bad_resource=True), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['disabled'])
        self.assertEqual(result['actualApplications'], 0)
        self.assertFalse(any(c.startswith('remote ota pull ') for c in result['cmds']))

    def test_prepare_automatically_recovers_the_saved_intermediate_after_interruption(self):
        result = run_harness(automatic_harness(interrupt=True), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['recovered'], 'Continue firmware update')
        self.assertIn('Update complete', result['notice'])
        self.assertEqual(result['actualApplications'], 2)
        self.assertEqual(len([c for c in result['cmds'] if c.startswith('remote ota installhash ')]), 2)

    def test_automatic_search_runs_three_steps_when_two_cannot_fit(self):
        result = run_harness(automatic_harness(three_steps=True), timeout=120)
        self.assertNotIn('error', result, result)
        self.assertIn('3 automatic update steps', result['prepared'])
        self.assertIn('Update complete', result['notice'])
        self.assertEqual(result['actualApplications'], 3)
        self.assertEqual(result['bridgeReads'], 2)
        self.assertFalse(result['folder'])


if __name__=='__main__':unittest.main()
