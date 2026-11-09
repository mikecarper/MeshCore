#!/usr/bin/env python3
"""Exercise the real Companion browser UI, mailbox and zero-hop seeder in Chrome."""
import os
import unittest

from test_browser_lora_ota_runtime import BROWSER
from test_browser_lora_ota_nrf_runtime import run_harness

HARNESS = r'''<script>
(()=>{
  const enc=new TextEncoder(),dec=new TextDecoder(),key='bb'.repeat(32),source='aa'.repeat(32),cmds=[],radio='910.525,62.5,7,5';
  const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
  const unhex=s=>Uint8Array.from(s.match(/../g),x=>parseInt(x,16));
  const wire=b=>Uint8Array.of(62,b.length&255,b.length>>8,...b);
  let expected,pkg,mid='',body='',installed=false,state='idle',guard=false,corr='',reply='',clock=Math.floor(Date.now()/1000),advertSeen=false,otaData=false,proof=false,leaves=false,manifest=[];
  class Port{
    constructor(){this.q=[];this.waiter=null;this.frames=[];}
    push(v){if(this.waiter){const w=this.waiter;this.waiter=null;w({value:v,done:false});}else this.q.push(v);}
    async open(){this.readable={getReader:()=>({read:()=>this.q.length?Promise.resolve({value:this.q.shift(),done:false}):new Promise(r=>this.waiter=r),cancel:async()=>{if(this.waiter)this.waiter({done:true});},releaseLock(){}})};
      this.writable={getWriter:()=>({write:async b=>this.write(b),close:async()=>{},releaseLock(){}})};}
    async close(){}
    async write(bytes){
      if(bytes[0]!==60||bytes.length!==3+bytes[1]+256*bytes[2])throw Error('invalid binary frame');
      const f=bytes.slice(3);this.frames.push(Array.from(f));let out;
      if(f[0]===1){out=new Uint8Array(58);out[0]=5;out.set(unhex(source),4);const v=new DataView(out.buffer);v.setUint32(48,910525,true);v.setUint32(52,62500,true);out[56]=7;out[57]=5;}
      else if(f[0]===22){out=new Uint8Array(82);out[0]=13;out[1]=13;out.set(enc.encode('06-Nov-2026'),8);out.set(enc.encode('Test board'),20);out.set(enc.encode('v1.18.0.2-preview'),60);}
      else if(f[0]===4){const contact=(k,type,fav,path,name)=>{const c=new Uint8Array(148);c[0]=3;c.set(unhex(k),1);c[33]=type;c[34]=fav;c[35]=path;c.set(enc.encode(name),100);return c;};
        const rows=[contact('11'.repeat(32),2,0,3,'Far'),contact('22'.repeat(32),3,0,1,'Room'),contact('33'.repeat(32),4,0,0,'Sensor'),contact('44'.repeat(32),1,1,0,'Other companion'),contact(key,2,1,0,'Test repeater'),contact('55'.repeat(32),2,0,1,'Near')];
        queueMicrotask(()=>{this.push(wire(Uint8Array.of(2,rows.length,0,0,0)));for(const c of rows)this.push(wire(c));this.push(wire(Uint8Array.of(4,0,0,0,0)));});return;}
      else if(f[0]===5){out=new Uint8Array(5);out[0]=9;new DataView(out.buffer).setUint32(1,clock,true);}
      else if(f[0]===52){if(hex(f.slice(2,34))!==key)throw Error('wrong guard target');if(f[1]===1)guard=true;if(f[1]===3)guard=false;if(f[1]===4)corr=dec.decode(f.slice(34,36));out=Uint8Array.of(0);}
      else if(f[0]===26){if(!guard||!['','password'].includes(dec.decode(f.slice(33))))throw Error('bad login');out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);
        queueMicrotask(()=>this.push(wire(Uint8Array.of(0x85,1,...unhex(key).slice(0,6),0,0,0,0,3,1))));}
      else if(f[0]===2){if(f[1]!==1||!guard||hex(f.slice(7,13))!==key.slice(0,12))throw Error('non-direct admin command');
        const command=dec.decode(f.slice(13));if(command.slice(0,3)!==corr+'|')throw Error('bad correlation');const text=command.slice(3);cmds.push(text);
        if(text==='get public.key')reply='> '+key;
        else if(text==='ota status')reply='OTA hw=board | target:12345678 | maxblk:1024 | '+(state==='idle'?'no download':'download: '+(state==='ready'?'ready to install':'fetching')+' id='+mid);
        else if(text==='ota self')reply='self body=44 image=100 base_hash='+(installed?body:'1122334455667788')+' | maxblk:1024';
        else if(text==='ver')reply=installed?'v1.18.0.1':'v1.17.1.9';
        else if(text==='get bootloader.ver')reply='> OTAFIX2.4.12';
        else if(text==='ota bootloader status')reply='BL board=239A0071 target=1150F50E name=TOWER_V2_OTA abi=3 caps=09';
        else if(text==='ota key list')reply='no trusted signer keys yet';
        else if(text==='ota stats')reply='OTA | fetch idle';
        else if(text==='get tempradio'||text==='get radio')reply='> '+radio;
        else if(text==='get radio.rxps')reply='> off,1000,1000';
        else if(text==='ota config')reply='ota config: autofetch=off autoinstall=off hops=0';
        else if(text==='ota installhash')reply='ERR usage: ota installhash <64-hex-container-sha256>';
        else if(text==='ota ls')reply=advertSeen?mid:'No updates seen yet';
        else if(text.startsWith('ota pull ')){state='fetching';reply='OK';const request=(op,...rest)=>wire(Uint8Array.of(0x88,0,0,0x32,0,op,...unhex(mid),...rest));
          setTimeout(()=>{this.push(request(4,3,0));this.push(request(10,1,0));this.push(request(6,0,0,1,0));},10);}
        else if(/^ota installhash [0-9a-f]{64}$/.test(text)){if(!otaData)throw Error('installed before data');installed=true;reply='OK';}
        else throw Error('unexpected remote '+text);out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);
      } else if(f[0]===66){const text=dec.decode(f.slice(1));let value;
        if(text==='get ota.direct')value=`> ota.direct v=2 active=${guard?1:0} busy=0`;
        else if(text==='get ota.direct.capabilities')value='> ota.direct.cap guarded_cli=1 guarded_radio=1';
        else if(text.startsWith('get ota.direct.reply ')){const at=Number(text.split(' ').pop()),b=enc.encode(reply),part=b.slice(at,at+40);value=`> ota.reply req=${corr} state=ready bytes=${b.length} off=${at} data=${hex(part)||'-'}`;}
        else if(text==='ver')value='v1.18.0.2-dev-12345678 (Build: 06-Nov-2026)';
        else if(text==='get tempradio')value='TempRadio active: '+radio+' 100s left,preamble=32';
        else if(text==='get radio')value='> '+radio;
        else if(text==='get radio.rxps')value='Error: RX power saving is unsupported on this radio';
        else if(text==='ota config')value='ota config: speed=1x packet=1x mode=seeder-only autofetch=off autoinstall=off checkpoint=4 advert=0min hops=0';
        else if(text==='ota stats')value='OTA | fetch idle';
        else if(text==='ota folder')value='folder=off serving=0:';
        else throw Error('unexpected local '+text);out=Uint8Array.of(29,...enc.encode(value));
      }else if(f[0]===65){const rf=f.slice(2);if(rf[0]!==0x32||rf[1]!==0)throw Error('OTA was not direct');
        if(rf[2]===1)advertSeen=true;
        if(rf[2]===5){if(rf[8]!==2||rf[7]>1)throw Error('wrong manifest geometry');manifest[rf[7]]=rf.slice(9);}
        if(rf[2]===11){if(rf[7]!==0||rf[8]!==1||hex(rf.slice(9))!==hex(pkg.blob.slice(205,209)))throw Error('wrong leaves');leaves=true;}
        if(rf[2]===7){if(hex(rf.slice(3,7))!==mid)throw Error('wrong package');const data=rf.slice(11);if(hex(data)!==hex(expected))throw Error('wrong payload');otaData=true;}
        if(rf[2]===9){if(hex(rf.slice(3,7))!==mid||hex(rf.slice(7))!=='000000')throw Error('wrong proof');proof=true;}
        if(otaData&&proof&&leaves&&manifest.length===2&&manifest.every(Boolean)){
          const joined=new Uint8Array(manifest[0].length+manifest[1].length);joined.set(manifest[0]);joined.set(manifest[1],manifest[0].length);
          if(hex(joined)!==hex(pkg.blob.slice(8,205)))throw Error('wrong manifest');state='ready';}
        out=Uint8Array.of(0);
      }else throw Error('unexpected command '+f[0]);
      queueMicrotask(()=>this.push(wire(out)));
    }
  }
  const port=new Port();Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port},configurable:true});
  const el=n=>document.querySelector('[data-role="'+n+'"]');
  const wait=async fn=>{const deadline=Date.now()+45000;while(Date.now()<deadline){if(fn())return;await new Promise(r=>setTimeout(r,20));}throw Error('Timed out: '+el('status').textContent);};
  const report=async data=>{await fetch('/__result',{method:'POST',body:JSON.stringify(data)});};
  window.addEventListener('error',e=>report({error:e.message,cmds}));window.addEventListener('unhandledrejection',e=>report({error:String(e.reason),cmds}));
  window.addEventListener('load',async()=>{try{
    expected=new Uint8Array(100);const v=new DataView(expected.buffer),at=44;expected.set(enc.encode('EndF'),at);v.setUint32(at+4,at,true);
    const bh=new Uint8Array(await crypto.subtle.digest('SHA-256',expected.slice(0,at))).slice(0,8);expected.set(bh,at+8);body=hex(bh);v.setUint32(at+16,0x01120001,true);v.setUint32(at+20,0x12345678,true);expected.set(enc.encode('board'),at+24);
    pkg=await MeshCoreMotaPackage.parseMota(await MeshCoreMotaPackage.buildFullMota(expected));mid=hex(pkg.merkleRoot);
    const dt=new DataTransfer();dt.items.add(new File([expected],'firmware.bin'));el('file').files=dt.files;el('password').value='password';
    if(el('source').value!=='companion')throw Error('Companion is not default');el('connect').click();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    const buttons=()=>[...el('contact').querySelectorAll('button[data-contact-key]')];
    const keys=()=>buttons().map(b=>b.dataset.contactKey);
    const search=text=>{el('contact-search').value=text;el('contact-search').dispatchEvent(new Event('input',{bubbles:true}));};
    const visible=b=>{const r=b.getBoundingClientRect(),s=getComputedStyle(b);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility==='visible';};
    const opts=keys();if(opts.join(',')!==[key,'55'.repeat(32),'11'.repeat(32),'22'.repeat(32),'33'.repeat(32)].join(','))throw Error('contact order or filtering incorrect: '+opts);
    if(!buttons().every(visible))throw Error('contacts are not visible without opening a dropdown');
    search('2222');if(keys().join(',')!=='22'.repeat(32)||!visible(buttons()[0]))throw Error('visible contact prefix search failed');
    search('Test');if(keys().join(',')!==key||!visible(buttons()[0]))throw Error('visible contact name search failed');
    buttons()[0].click();if(el('key').value!==key||buttons()[0].getAttribute('aria-pressed')!=='true')throw Error('contact selection failed');
    search('');if(keys().length!==5||!buttons().find(b=>b.dataset.contactKey===key&&b.getAttribute('aria-pressed')==='true'))throw Error('selection did not persist when more contacts appeared');
    search('no such device');if(buttons().length||!el('contact-status').textContent.toLowerCase().includes('no matching')||el('key').value)throw Error('no-match search failed to clear selection');
    el('key').value=key;el('key').dispatchEvent(new Event('input',{bubbles:true}));if(el('key').value!==key)throw Error('manual public-key fallback failed');
    search('Test');buttons()[0].click();
    if(el('key').value!==key)throw Error('contact selection failed');el('inspect').click();await wait(()=>!el('transfer').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    if(!el('contact-search').disabled||buttons().some(b=>!b.disabled))throw Error('target selection remained enabled after connecting to target');
    el('transfer').click();await wait(()=>!el('install').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    el('install').click();await wait(()=>el('status').textContent.includes('Installed and verified:')||el('status').textContent.includes('Error:'));
    const status=el('status').textContent;el('disconnect').click();await wait(()=>!el('connect').disabled);await report({status,cmds,installed,otaData,advertSeen,guard,frames:port.frames});
  }catch(e){await report({error:String(e),status:el('status').textContent,cmds,frames:port.frames});}});
})();
</script>'''


def investigation_harness(mock_catalog=True):
    # Exercise the real page without attaching a firmware file or starting an update.
    file_setup = "const dt=new DataTransfer();dt.items.add(new File([expected],'firmware.bin'));el('file').files=dt.files;el('password').value='password';"
    before_inspect = "if(el('key').value!==key)throw Error('contact selection failed');el('inspect').click();await wait(()=>!el('transfer').disabled||el('status').textContent.includes('Error:'));"
    assert HARNESS.count(file_setup) == 1
    assert HARNESS.count(before_inspect) == 1
    probe = r'''if(el('key').value!==key)throw Error('contact selection failed');
    if(el('file').files.length)throw Error('investigation requires a firmware file');
    if(el('investigate').closest('section')!==el('investigation').closest('section')||el('investigate').closest('section')!==document.querySelector('[data-lora-ota] > section'))throw Error('investigation controls are not in step 1');
    const done=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('investigation did not finish')),45000);document.querySelector('[data-lora-ota]').addEventListener('lora-ota-investigated',event=>{clearTimeout(timer);resolve(event);},{once:true});});
    if(el('investigate').disabled)throw Error('investigate button unavailable');el('investigate').click();const event=await done;
    if(el('investigation').hidden||!el('transfer').disabled||!el('install').disabled)throw Error('investigation did not show results or enabled an update');
    await report({result:el('investigation').textContent,status:el('status').textContent,cmds,frames:port.frames,guard,complete:event.detail.complete});return;'''
    harness=HARNESS.replace(file_setup,"el('password').value='password';").replace(before_inspect,probe)
    if mock_catalog:
        marker="if(el('investigate').disabled)throw Error('investigate button unavailable');"
        mock="const originalFetch=window.fetch.bind(window);window.fetch=(url,options)=>String(url).startsWith('https://mikecarper.github.io/meshfirmware/')?Promise.reject(Error('catalog unavailable in test')):originalFetch(url,options);"
        assert harness.count(marker)==1
        harness=harness.replace(marker,mock+marker,1)
    return harness


def catalog_harness(fault=''):
    harness=investigation_harness(mock_catalog=False)
    harness=harness.replace("let expected,pkg,mid=''", "let testTargetId=0,receivedBytes=0,expected,pkg,mid=''", 1)
    harness=harness.replace("expected=new Uint8Array(100);const v=new DataView(expected.buffer),at=44;", "testTargetId=await LoraOtaCatalog.idForEnv('board_repeater');expected=new Uint8Array(256);const v=new DataView(expected.buffer),at=200;expected.set(enc.encode('OTA: status | get <id> flash | install | ota installhash <64-hex-container-sha256>'),0);", 1)
    harness=harness.replace("v.setUint32(at+20,0x12345678,true)","v.setUint32(at+20,testTargetId,true)",1)
    harness=harness.replace("'OTA hw=board | target:12345678 | maxblk:1024 | '","'OTA hw=board | target:'+testTargetId.toString(16).padStart(8,'0')+' | maxblk:1024 | '",1)
    harness=harness.replace("this.push(request(6,0,0,1,0))","this.push(request(6,0,0,3,0))",1)
    old_data="const data=rf.slice(11);if(hex(data)!==hex(expected))throw Error('wrong payload');otaData=true;"
    new_data="const data=rf.slice(11),offset=rf[9]|rf[10]<<8;if(hex(data)!==hex(expected.slice(offset,offset+data.length)))throw Error('wrong payload');receivedBytes+=data.length;otaData=receivedBytes===expected.length;"
    assert harness.count(old_data)==1
    harness=harness.replace(old_data,new_data,1)
    setup=r'''
    const catalogName='board_repeater-ota-v1.18.0.1.bin',catalogVersion='v1.18.0.1';
    const catalogUrl='https://github.com/mikecarper/MeshCore/releases/download/v1.18.0.1/'+catalogName;
    const mirrorImage=expected.slice();
    if(CATALOG_FAULT==='identity')new DataView(mirrorImage.buffer).setUint32(at+20,0x13572468,true);
    const imageHash=hex(new Uint8Array(await crypto.subtle.digest('SHA-256',mirrorImage)));
    const describedHash=CATALOG_FAULT==='digest'?'00'.repeat(32):imageHash;
    const piece={path:describedHash+'.gz',sha256:describedHash,gzipSha256:describedHash,size:mirrorImage.length,gzipSize:mirrorImage.length};
    const catalog={items:[{project:'MeshCore',provider:'Keymind Cascade',role:'repeater',action:'flash',name:catalogName,version:catalogVersion,url:catalogUrl}]};
    const controls={familyTag:catalogVersion,profiles:{board_repeater:{otaRole:'lora-receiver',updateMethods:['lora']}}};
    const index={schemaVersion:1,entries:[{url:catalogUrl,...piece}]};
    const catalogRequests=[],realFetch=window.fetch.bind(window);
    window.fetch=async(input,options)=>{const url=String(input);
      if(url.startsWith('https://mikecarper.github.io/meshfirmware/')||url.startsWith('https://api.github.com/repos/mikecarper/MeshCore/releases/'))catalogRequests.push(url);
      if(url==='https://mikecarper.github.io/meshfirmware/catalog.json')return new Response(JSON.stringify(catalog));
      if(url==='https://mikecarper.github.io/MeshCore/_data/firmware_controls.json')return new Response(JSON.stringify(controls));
      if(url==='https://api.github.com/repos/mikecarper/MeshCore/releases/tags/v1.18.0.1'){
        if(CATALOG_FAULT==='metadata')return new Response('unavailable',{status:503});
        return new Response(JSON.stringify({tag_name:catalogVersion,draft:false,prerelease:CATALOG_FAULT==='prerelease',assets:[{name:catalogName,browser_download_url:catalogUrl}]}));
      }
      if(url==='https://mikecarper.github.io/meshfirmware/firmware/index.json')return new Response(JSON.stringify(index));
      if(url==='https://mikecarper.github.io/meshfirmware/firmware/'+piece.path){
        if(CATALOG_FAULT==='stale'){el('key').value='cc'.repeat(32);el('key').dispatchEvent(new Event('input',{bubbles:true}));}
        return new Response(mirrorImage);
      }
      return realFetch(input,options);
    };'''.replace('CATALOG_FAULT',repr(fault))
    marker="pkg=await MeshCoreMotaPackage.parseMota(await MeshCoreMotaPackage.buildFullMota(expected));mid=hex(pkg.merkleRoot);"
    assert harness.count(marker)==1
    harness=harness.replace(marker,marker+setup,1)
    harness=harness.replace("result:el('investigation').textContent,status:","catalogRequests,catalog:el('firmware-catalog-status').textContent,choiceHidden:el('firmware-choice-wrap').hidden,choices:el('firmware-choice').options.length,result:el('investigation').textContent,status:",1)
    if fault=='stale':
        harness=harness.replace("if(el('investigation').hidden||!el('transfer').disabled||!el('install').disabled)","if(!el('investigation').hidden||!el('transfer').disabled||!el('install').disabled)",1)
    return harness


class CompanionBrowserRuntimeTest(unittest.TestCase):
    def test_empty_password_checks_guarded_acl_with_passwordless_login(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        harness=investigation_harness()
        self.assertEqual(harness.count("el('password').value='password';"),1)
        result=run_harness(harness.replace("el('password').value='password';","el('password').value='';",1),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['complete'],result)
        self.assertIn('get public.key',result['cmds'],result)
        self.assertIn('ota status',result['cmds'],result)
        self.assertTrue(any(f[0]==26 and len(f)==33 for f in result['frames']),result)
        self.assertFalse(any(f[0]==65 for f in result['frames']),result)
        self.assertTrue(any(f[0]==52 and f[1]==1 for f in result['frames']),result)
        self.assertTrue(any(f[0]==52 and f[1]==2 for f in result['frames']),result)

    def test_empty_password_guarded_acl_rejects_mismatched_identity(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        harness=investigation_harness()
        self.assertEqual(harness.count("el('password').value='password';"),1)
        self.assertEqual(harness.count("if(text==='get public.key')reply='> '+key;"),1)
        harness=harness.replace("el('password').value='password';","el('password').value='';",1)
        harness=harness.replace("if(text==='get public.key')reply='> '+key;","if(text==='get public.key')reply='> '+'cc'.repeat(32);",1)
        result=run_harness(harness,timeout=90)
        self.assertNotIn('error',result,result)
        self.assertFalse(result['complete'],result)
        self.assertNotIn('ota status',result['cmds'],result)
        self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_connection_progress_is_visible_until_contacts_arrive(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        port = "const port=new Port();Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port},configurable:true});"
        delayed_port = r'''const port=new Port();let releaseOpen,releaseContacts;
  const openGate=new Promise(r=>releaseOpen=r),contactsGate=new Promise(r=>releaseContacts=r),open=port.open.bind(port);
  port.open=async()=>{await openGate;await open();};
  Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port},configurable:true});'''
        queue = "queueMicrotask(()=>{this.push(wire(Uint8Array.of(2,rows.length,0,0,0)));for(const c of rows)this.push(wire(c));this.push(wire(Uint8Array.of(4,0,0,0,0)));});return;}"
        delayed_queue = "contactsGate.then(()=>{this.push(wire(Uint8Array.of(2,rows.length,0,0,0)));for(const c of rows)this.push(wire(c));this.push(wire(Uint8Array.of(4,0,0,0,0)));});return;}"
        start = "if(el('source').value!=='companion')throw Error('Companion is not default');el('connect').click();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));"
        inspect = r'''const seen=[];new MutationObserver(()=>seen.push(el('contact-status').textContent)).observe(el('contact-status'),{childList:true,subtree:true,characterData:true});el('connect').click();
    const progress=()=>el('contact-status').textContent,shown=()=>{const e=el('contact-status'),r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility==='visible';};
    await wait(()=>/opening usb port.*1 of 3/i.test(progress()));if(!shown()||!el('connect').disabled)throw Error('USB opening indicator not visible');
    releaseOpen();await wait(()=>/loading/i.test(progress()));if(!seen.some(s=>/connecting to radio/i.test(s)))throw Error('radio handshake indicator not visible');if(!shown()||!el('connect').disabled||el('contact').querySelector('button'))throw Error('contacts loading indicator not visible');
    releaseContacts();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));
    await report({progress:progress(),count:el('contact').querySelectorAll('button').length,connectDisabled:el('connect').disabled,status:el('status').textContent});return;'''
        for source, replacement in ((port,delayed_port),(queue,delayed_queue),(start,inspect)):
            self.assertEqual(HARNESS.count(source),1)
        harness=HARNESS.replace(port,delayed_port).replace(queue,delayed_queue).replace(start,inspect)
        result=run_harness(harness,timeout=45)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['count'],5,result)
        self.assertRegex(result['progress'],r'(?i)\b5\s+devices?\s+found')
        self.assertTrue(result['connectDisabled'],result)

    def test_connection_cancellation_and_failure_allow_retry(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        port = "const port=new Port();Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port},configurable:true});"
        start = "if(el('source').value!=='companion')throw Error('Companion is not default');el('connect').click();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));"
        inspect = r'''el('connect').click();await wait(()=>!el('connect').disabled);
    const failure=el('contact-status').textContent;if(!/cancel|failed|unable|could not/i.test(failure))throw Error('connection result not visible: '+failure);
    el('connect').click();await wait(()=>!el('inspect').disabled);
    await report({failure,progress:el('contact-status').textContent,count:el('contact').querySelectorAll('button').length});return;'''
        self.assertEqual(HARNESS.count(port),1)
        self.assertEqual(HARNESS.count(start),1)
        for reason in ('cancel','failure','open_failure'):
            with self.subTest(reason=reason):
                if reason in ('cancel','failure'):
                    fault = "throw new DOMException('No port selected','NotFoundError')" if reason=='cancel' else "throw Error('USB is busy')"
                    simulated = "const port=new Port();let attempts=0;Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>{if(!attempts++){"+fault+";}return port;}},configurable:true});"
                else:
                    simulated = "const port=new Port();let attempts=0;const open=port.open.bind(port);port.open=async()=>{if(attempts++<3)throw new DOMException('Failed to open serial port.','NetworkError');return open();};Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port},configurable:true});"
                harness=HARNESS.replace(port,simulated).replace(start,inspect)
                # A canceled serial reader cannot retain its pending read when
                # the same test port is opened again for a retry.
                harness=harness.replace("cancel:async()=>{if(this.waiter)this.waiter({done:true});}",
                                        "cancel:async()=>{if(this.waiter){const waiter=this.waiter;this.waiter=null;waiter({done:true});}}")
                result=run_harness(harness,timeout=45)
                self.assertNotIn('error',result,result)
                self.assertEqual(result['count'],5,result)
                self.assertRegex(result['progress'],r'(?i)\b5\s+devices?\s+found')

    def test_usb_open_retries_are_bounded_and_release_only_owned_ports(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        original = "const port=new Port();Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port},configurable:true});"
        start = "if(el('source').value!=='companion')throw Error('Companion is not default');el('connect').click();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));"
        self.assertEqual(HARNESS.count(original),1)
        self.assertEqual(HARNESS.count(start),1)
        for fault in ('transient','permanent','not_retryable','handshake'):
            with self.subTest(fault=fault):
                setup = r'''const port=new Port(),openTimes=[];let closeCalls=0,chooserCalls=0;
  const open=port.open.bind(port),close=port.close.bind(port);
  port.open=async(...args)=>{openTimes.push(Date.now());
    if(FAULT==='transient'&&openTimes.length<3||FAULT==='permanent')throw new DOMException('Failed to open serial port.','NetworkError');
    if(FAULT==='not_retryable')throw new DOMException('Port is already open.','InvalidStateError');
    return open(...args);};
  port.close=async()=>{closeCalls++;return close();};
  if(FAULT==='handshake')port.write=async()=>{throw Error('Handshake failed after opening');};
  Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>{chooserCalls++;return port;}},configurable:true});'''.replace('FAULT',repr(fault))
                inspect = r'''const progress=[];
    new MutationObserver(()=>progress.push(el('contact-status').textContent)).observe(el('contact-status'),{childList:true,subtree:true,characterData:true});
    el('connect').click();
    await wait(()=>!el('connect').disabled||!el('inspect').disabled);
    const openStatus=el('contact-status').textContent,connected=!el('inspect').disabled;
    const beforeClose=closeCalls;if(connected){el('disconnect').click();await wait(()=>!el('connect').disabled);}
    await report({openTimes,closeCalls,beforeClose,chooserCalls,connected,status:openStatus,progress});return;'''
                result=run_harness(HARNESS.replace(original,setup).replace(start,inspect),timeout=45)
                self.assertNotIn('error',result,result)
                self.assertEqual(result['chooserCalls'],1,result)
                times=result['openTimes']
                if fault in ('transient','permanent'):
                    self.assertEqual(len(times),3,result)
                    self.assertGreaterEqual(times[1]-times[0],850,result)
                    self.assertGreaterEqual(times[2]-times[1],850,result)
                    self.assertTrue(any('2 of 3' in s for s in result['progress']),result)
                else:
                    self.assertLessEqual(len(times),1,result)
                if fault=='transient':
                    self.assertTrue(result['connected'],result)
                    self.assertEqual(result['beforeClose'],0,result)
                    self.assertEqual(result['closeCalls'],1,result)
                else:
                    self.assertFalse(result['connected'],result)
                    self.assertRegex(result['status'],r'(?i)(port|serial|connection)')
                    self.assertEqual(result['closeCalls'],1 if fault=='handshake' else 0,result)
                    if fault=='permanent': self.assertRegex(result['status'],r'(?i)(in use|another app|browser tab)')

    def test_disconnect_stops_pending_usb_open_retries(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        original="const port=new Port();Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port},configurable:true});"
        start="if(el('source').value!=='companion')throw Error('Companion is not default');el('connect').click();await wait(()=>!el('inspect').disabled||el('status').textContent.includes('Error:'));"
        setup=r'''const port=new Port();let attempts=0,closes=0;
  port.open=async()=>{attempts++;throw new DOMException('Failed to open serial port.','NetworkError');};
  port.close=async()=>{closes++;};
  Object.defineProperty(navigator,'serial',{value:{requestPort:async()=>port},configurable:true});'''
        check=r'''el('connect').click();await wait(()=>attempts===1);
    el('disconnect').click();await wait(()=>!el('connect').disabled);
    await new Promise(resolve=>setTimeout(resolve,1200));
    await report({attempts,closes,status:el('contact-status').textContent,connected:!el('inspect').disabled});return;'''
        self.assertEqual(HARNESS.count(original),1)
        self.assertEqual(HARNESS.count(start),1)
        result=run_harness(HARNESS.replace(original,setup).replace(start,check),timeout=45)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['attempts'],1,result)
        self.assertEqual(result['closes'],0,result)
        self.assertFalse(result['connected'],result)

    def test_auto_selected_catalog_firmware_completes_simulated_update(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        harness=catalog_harness()
        report="await report({catalogRequests,catalog:el('firmware-catalog-status').textContent,choiceHidden:el('firmware-choice-wrap').hidden,choices:el('firmware-choice').options.length,result:el('investigation').textContent,status:el('status').textContent,cmds,frames:port.frames,guard,complete:event.detail.complete});return;"
        self.assertEqual(harness.count(report),1)
        proceed="if(!/downloaded.*identity verified/i.test(el('firmware-catalog-status').textContent))throw Error('firmware was not selected');el('inspect').click();await wait(()=>!el('transfer').disabled||el('status').textContent.includes('Error:'));"
        result=run_harness(harness.replace(report,proceed),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertIn('Installed and verified:',result['status'])
        self.assertTrue(result['installed'])
        self.assertTrue(result['otaData'])

    def test_investigate_downloads_matching_catalog_image(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        result=run_harness(catalog_harness(),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['complete'],result)
        self.assertRegex(result['catalog'],r'(?i)downloaded.*board_repeater.*identity verified')
        self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_investigate_rejects_catalog_image_with_wrong_identity_or_digest(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        for fault in ('identity','digest'):
            with self.subTest(fault=fault):
                result=run_harness(catalog_harness(fault),timeout=90)
                self.assertNotIn('error',result,result)
                self.assertNotRegex(result['catalog'],r'(?i)downloaded.*identity verified')
                self.assertRegex(result['catalog'],r'(?i)(identity|hash|failed|error)')
                self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_investigate_rejects_stale_download_after_target_change(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        result=run_harness(catalog_harness('stale'),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertNotRegex(result['catalog'],r'(?i)downloaded.*identity verified')
        self.assertRegex(result['catalog'],r'(?i)target changed')
        self.assertTrue(result['choiceHidden'],result)
        self.assertEqual(result['choices'],0,result)
        self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_investigate_does_not_auto_select_prerelease_or_unverified_release(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        for fault in ('prerelease','metadata'):
            with self.subTest(fault=fault):
                result=run_harness(catalog_harness(fault),timeout=90)
                self.assertNotIn('error',result,result)
                self.assertNotRegex(result['catalog'],r'(?i)downloaded.*identity verified')
                self.assertRegex(result['catalog'],r'(?i)choose.*firmware')
                self.assertFalse(result['choiceHidden'],result)
                self.assertGreaterEqual(result['choices'],2,result)
                self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_investigate_legacy_target_can_select_catalog_image(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        old="else if(text==='ota installhash')reply='ERR usage: ota installhash <64-hex-container-sha256>';"
        harness=catalog_harness()
        self.assertEqual(harness.count(old),1)
        result=run_harness(harness.replace(old,"else if(text==='ota installhash')reply='Unknown OTA command';"),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertRegex(result['catalog'],r'(?i)downloaded.*identity verified')
        self.assertRegex(result['result'],r'(?i)legacy install|older firmware')
        self.assertTrue(any('/firmware/' in url for url in result['catalogRequests']),result)
        self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_legacy_target_catalog_image_passes_preflight_with_warning(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        old="else if(text==='ota installhash')reply='ERR usage: ota installhash <64-hex-container-sha256>';"
        harness=catalog_harness().replace(old,"else if(text==='ota installhash')reply='Unknown OTA command';")
        report="await report({catalogRequests,catalog:el('firmware-catalog-status').textContent,choiceHidden:el('firmware-choice-wrap').hidden,choices:el('firmware-choice').options.length,result:el('investigation').textContent,status:el('status').textContent,cmds,frames:port.frames,guard,complete:event.detail.complete});return;"
        self.assertEqual(harness.count(report),1)
        manual=r'''if(!/downloaded.*identity verified/i.test(el('firmware-catalog-status').textContent))throw Error('firmware was not selected');
    el('inspect').click();await wait(()=>!el('transfer').disabled||el('status').textContent.includes('Error:'));
    await report({status:el('status').textContent,transferDisabled:el('transfer').disabled,frames:port.frames});return;'''
        result=run_harness(harness.replace(report,manual),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertNotIn('Error:', result['status'], result)
        self.assertRegex(result['status'],r'(?i)older target|legacy installer')
        self.assertFalse(result['transferDisabled'],result)
        self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_investigate_without_firmware_only_reads_target(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        result=run_harness(investigation_harness(),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['complete'],result)
        self.assertIn('board',result['result'])
        self.assertIn('OTAFIX2.4.12',result['result'])
        self.assertIn('> get public.key (attempt 1 of 7)',result['result'])
        self.assertIn('> ota status (attempt 1 of 11)',result['result'])
        self.assertRegex(result['result'],r'(?i)install verification:.*supported')
        self.assertIn('Next: select firmware, then Prepare update.',result['result'])
        self.assertIn('A successful check enables Update remote firmware.',result['result'])
        required={'ota status','ota self','ver','get bootloader.ver','ota bootloader status','ota config','ota key list','get tempradio','get radio.rxps','ota installhash'}
        self.assertTrue(required.issubset(result['cmds']),result)
        self.assertTrue(all(c in required | {'get public.key'} for c in result['cmds']),result)
        self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_connected_guarded_companion_shows_version_and_selected_mode(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        harness=investigation_harness().replace("result:el('investigation').textContent,status:",
            "sourceInfo:el('source-info').textContent,queueWarningVisible:!el('legacy-warning').hidden,secondaryInvestigation:!!el('legacy-investigate'),result:el('investigation').textContent,status:",1)
        result=run_harness(harness,timeout=90)
        self.assertNotIn('error',result,result)
        self.assertIn('v1.18.0.2-dev-12345678',result['sourceInfo'])
        self.assertRegex(result['sourceInfo'],r'(?i)guarded OTA.*selected automatically')
        self.assertTrue(any(f[0]==22 for f in result['frames']),result)
        self.assertTrue(result['complete'],result)
        self.assertFalse(result['queueWarningVisible'],result)
        self.assertFalse(result['secondaryInvestigation'],result)

    def test_investigate_legacy_target_does_not_claim_verified_install(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        old="else if(text==='ota installhash')reply='ERR usage: ota installhash <64-hex-container-sha256>';"
        self.assertEqual(HARNESS.count(old),1)
        harness=investigation_harness().replace(old,"else if(text==='ota installhash')reply='Unknown OTA command. Type `ota help`.';")
        old_bl="else if(text==='ota bootloader status')reply='BL board=239A0071 target=1150F50E name=TOWER_V2_OTA abi=3 caps=09';"
        self.assertEqual(harness.count(old_bl),1)
        harness=harness.replace(old_bl,"else if(text==='ota bootloader status')reply='Unknown command';")
        result=run_harness(harness,timeout=90)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['complete'],result)
        self.assertNotRegex(result['result'],r'(?i)install verification:\s*supported')
        self.assertRegex(result['result'],r'(?i)install verification:.*(?:unsupported|unavailable|unknown|not supported)')
        self.assertRegex(result['result'],r'(?i)bootloader.*unknown|unknown.*bootloader')
        self.assertNotIn('ota pull', ' '.join(result['cmds']))
        self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_investigate_interrupted_read_reports_remaining_checks_unchecked(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        old="else if(text.startsWith('get ota.direct.reply ')){const at=Number(text.split(' ').pop()),b=enc.encode(reply),part=b.slice(at,at+40);value=`> ota.reply req=${corr} state=ready bytes=${b.length} off=${at} data=${hex(part)||'-'}`;}"
        self.assertEqual(HARNESS.count(old),1)
        fault="else if(text.startsWith('get ota.direct.reply ')){if(cmds.includes('ota self'))value=`> ota.reply req=${corr} state=idle bytes=0 off=0 data=-`;else {const at=Number(text.split(' ').pop()),b=enc.encode(reply),part=b.slice(at,at+40);value=`> ota.reply req=${corr} state=ready bytes=${b.length} off=${at} data=${hex(part)||'-'}`;}}"
        result=run_harness(investigation_harness().replace(old,fault),timeout=90)
        self.assertNotIn('error',result,result)
        self.assertFalse(result['complete'],result)
        self.assertRegex(result['result'],r'(?i)check interrupted')
        self.assertRegex(result['result'],r'(?i)install verification:\s*not checked')
        self.assertNotIn('ota installhash',result['cmds'])
        self.assertFalse(any(f[0]==65 for f in result['frames']),result)

    def test_companion_full_update(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        result=run_harness(HARNESS,timeout=90)
        self.assertNotIn('error',result,result)
        self.assertIn('Installed and verified:',result['status'])
        self.assertTrue(result['advertSeen'])
        self.assertTrue(result['otaData'])
        self.assertTrue(result['installed'])
        self.assertFalse(result['guard'])
        self.assertEqual(len([c for c in result['cmds'] if c.startswith('ota installhash ')]),1)

    def test_sensor_without_ota_is_rejected_before_transfer(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        harness=HARNESS.replace("contact(key,2,1,0,'Test repeater')", "contact(key,4,1,0,'Test repeater')")
        old="else if(text==='ota status')reply='OTA hw=board | target:12345678 | maxblk:1024 | '+(state==='idle'?'no download':'download: '+(state==='ready'?'ready to install':'fetching')+' id='+mid);"
        self.assertEqual(harness.count(old),1)
        harness=harness.replace(old,"else if(text==='ota status')reply='Unknown command';")
        start="el('inspect').click();await wait(()=>!el('transfer').disabled||el('status').textContent.includes('Error:'));"
        self.assertEqual(harness.count(start),1)
        harness=harness.replace(start,start+"await report({status:el('status').textContent,cmds,frames:port.frames,installed,otaData});return;")
        result=run_harness(harness,timeout=45)
        self.assertNotIn('error',result,result)
        self.assertIn('Error:',result['status'])
        self.assertTrue(any(f[0]==52 and f[1]==1 and f[34]==4 for f in result['frames']))
        self.assertFalse(result['installed'])
        self.assertFalse(result['otaData'])
        self.assertFalse(any(f[0]==65 for f in result['frames']))

    def test_older_companion_still_lists_contacts_without_ota(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER') == '1': self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')
        old="if(text==='get ota.direct')value=`> ota.direct v=2 active=${guard?1:0} busy=0`;"
        self.assertEqual(HARNESS.count(old),1)
        harness=HARNESS.replace(old,"if(text==='get ota.direct')value='Unknown command';")
        start="el('inspect').click();await wait(()=>!el('transfer').disabled||el('status').textContent.includes('Error:'));"
        self.assertEqual(harness.count(start),1)
        # Connecting and browsing stay read-only. Prepare update now performs
        # legacy identity verification itself, so stop before that user action.
        harness=harness.replace(start,"await report({status:el('status').textContent,cmds,frames:port.frames});return;"+start)
        result=run_harness(harness,timeout=45)
        self.assertNotIn('error',result,result)
        self.assertIn('raw-packet compatibility',result['status'])
        self.assertIn('Loaded 5 eligible contacts.',result['status'])
        self.assertEqual(result['cmds'],[])
        self.assertFalse(any(f[0] in (2,10,26,52,65) for f in result['frames']))


if __name__=='__main__':unittest.main()
