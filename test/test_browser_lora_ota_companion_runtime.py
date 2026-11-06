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
      else if(f[0]===4){const contact=(k,type,fav,path,name)=>{const c=new Uint8Array(148);c[0]=3;c.set(unhex(k),1);c[33]=type;c[34]=fav;c[35]=path;c.set(enc.encode(name),100);return c;};
        const rows=[contact('11'.repeat(32),2,0,3,'Far'),contact('22'.repeat(32),3,0,1,'Room'),contact('33'.repeat(32),4,0,0,'Sensor'),contact('44'.repeat(32),1,1,0,'Other companion'),contact(key,2,1,0,'Test repeater'),contact('55'.repeat(32),2,0,1,'Near')];
        queueMicrotask(()=>{this.push(wire(Uint8Array.of(2,rows.length,0,0,0)));for(const c of rows)this.push(wire(c));this.push(wire(Uint8Array.of(4,0,0,0,0)));});return;}
      else if(f[0]===5){out=new Uint8Array(5);out[0]=9;new DataView(out.buffer).setUint32(1,clock,true);}
      else if(f[0]===52){if(hex(f.slice(2,34))!==key)throw Error('wrong guard target');if(f[1]===1)guard=true;if(f[1]===3)guard=false;if(f[1]===4)corr=dec.decode(f.slice(34,36));out=Uint8Array.of(0);}
      else if(f[0]===26){if(!guard||dec.decode(f.slice(33))!=='password')throw Error('bad login');out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);
        queueMicrotask(()=>this.push(wire(Uint8Array.of(0x85,1,...unhex(key).slice(0,6),0,0,0,0,3,1))));}
      else if(f[0]===2){if(f[1]!==1||!guard||hex(f.slice(7,13))!==key.slice(0,12))throw Error('non-direct admin command');
        const command=dec.decode(f.slice(13));if(command.slice(0,3)!==corr+'|')throw Error('bad correlation');const text=command.slice(3);cmds.push(text);
        if(text==='get public.key')reply='> '+key;
        else if(text==='ota status')reply='OTA hw=board | target:12345678 | maxblk:1024 | '+(state==='idle'?'no download':'download: '+(state==='ready'?'ready to install':'fetching')+' id='+mid);
        else if(text==='ota self')reply='self body=44 image=100 base_hash='+(installed?body:'1122334455667788')+' | maxblk:1024';
        else if(text==='ver')reply=installed?'v1.18.0.1':'v1.17.1.9';
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
    const opts=[...el('contact').options].slice(1).map(o=>o.value);if(opts.join(',')!==[key,'55'.repeat(32),'11'.repeat(32),'22'.repeat(32),'33'.repeat(32)].join(','))throw Error('contact order or filtering incorrect: '+opts);
    el('contact-search').value='2222';el('contact-search').dispatchEvent(new Event('input'));if([...el('contact').options].slice(1).map(o=>o.value).join(',')!=='22'.repeat(32))throw Error('contact prefix search failed');
    el('contact-search').value='Test';el('contact-search').dispatchEvent(new Event('input'));el('contact').value=key;el('contact').dispatchEvent(new Event('change'));
    if(el('key').value!==key)throw Error('contact selection failed');el('inspect').click();await wait(()=>!el('transfer').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    el('transfer').click();await wait(()=>!el('install').disabled||el('status').textContent.includes('Error:'));
    if(el('status').textContent.includes('Error:'))throw Error(el('status').textContent);
    el('install').click();await wait(()=>el('status').textContent.includes('Installed and verified:')||el('status').textContent.includes('Error:'));
    const status=el('status').textContent;el('disconnect').click();await wait(()=>!el('connect').disabled);await report({status,cmds,installed,otaData,advertSeen,guard,frames:port.frames});
  }catch(e){await report({error:String(e),status:el('status').textContent,cmds,frames:port.frames});}});
})();
</script>'''


class CompanionBrowserRuntimeTest(unittest.TestCase):
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
        harness=harness.replace(start,start+"await report({status:el('status').textContent,cmds,frames:port.frames});return;")
        result=run_harness(harness,timeout=45)
        self.assertNotIn('error',result,result)
        self.assertIn('guarded zero-hop',result['status'])
        self.assertFalse(any(f[0] in (26,52,65) for f in result['frames']))


if __name__=='__main__':unittest.main()
