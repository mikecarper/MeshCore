'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {detectSource,adoptDetectedSource}=require('../docs/_javascript/lora_ota_port.js');
const {RepeaterTransport}=require('../docs/_javascript/lora_ota_serial.js');
const encode=s=>new TextEncoder().encode(s),decode=b=>new TextDecoder().decode(b);
const query=Uint8Array.of(60,2,0,22,4),sourceKey='a'.repeat(64),targetKey='b'.repeat(64);
function binaryFrame(payload){return Uint8Array.from([62,payload.length&255,payload.length>>8,...payload]);}
function deviceInfo(){const b=new Uint8Array(82);b[0]=13;b[1]=14;b.set(encode('24-Oct-2026'),8);b.set(encode('Heltec V4'),20);return binaryFrame(b);}
const fast={openSettle:2,binaryTimeout:8,cliTimeout:15};
class Port{
  constructor(kind='companion'){this.kind=kind;this.openCount=0;this.closeCount=0;this.writes=[];this.onWrite=null;}
  async open(){
    this.openCount++;
    this.readable=new ReadableStream({start:c=>{this.controller=c;},cancel:()=>{this.canceled=true;}});
    this.writable=new WritableStream({write:b=>{
      this.writes.push(Uint8Array.from(b));
      if(this.onWrite)return this.onWrite(b);
      if(Buffer.from(b).equals(Buffer.from(query))){if(this.kind==='companion')this.send(deviceInfo());return;}
      if(this.kind!=='repeater')return;
      const cmd=decode(b).trim();
      let reply=({'get public.key':sourceKey,'ota.peer status':'OTApeer v=1 session=0 busy=0 peers=0','ota.peer capabilities':'OTApeer known_repeater=1 routed_path=1','ota.peer path':'OTApeer path=off','ota.peer close':'OTApeer closed'})[cmd];
      if(reply)this.send(encode(cmd+'\r\n-> '+reply+'\r\n'));
    }});
    if(this.initial)this.send(this.initial);
  }
  send(bytes){this.controller.enqueue(Uint8Array.from(bytes));}
  async close(){this.closeCount++;assert.equal(this.readable.locked,false);assert.equal(this.writable.locked,false);this.readable=null;this.writable=null;}
}
async function releaseOwner(owner){await owner.reader.cancel();owner.reader.releaseLock();owner.writer.releaseLock();await owner.port.close();}

test('automatic Companion detection uses only local DEVICE_QUERY and transfers the same open streams once',async()=>{
  const port=new Port(),token=await detectSource(port,fast);
  assert.equal(token.kind,'companion');assert.equal(port.openCount,1);assert.deepEqual(port.writes,[query]);
  const owner={port,options:{detectedSource:token}};
  assert.equal(adoptDetectedSource(owner,'companion'),true);assert.equal(owner.portOpened,true);
  assert.equal(port.readable.locked,true);assert.equal(port.writable.locked,true);
  await token.close();assert.equal(port.closeCount,0);
  assert.throws(()=>adoptDetectedSource({port,options:{detectedSource:token}},'companion'),/no longer valid/);
  await releaseOwner(owner);assert.equal(port.closeCount,1);
});

test('fragmented binary identity preserves unrelated bytes for the transport',async()=>{
  const port=new Port(),prefix=binaryFrame(Uint8Array.of(128));
  port.onWrite=async b=>{assert.deepEqual(b,query);const info=deviceInfo();port.send(prefix);port.send(info.slice(0,2));port.send(info.slice(2,40));port.send(info.slice(40));};
  const token=await detectSource(port,fast),owner={port,options:{detectedSource:token}};adoptDetectedSource(owner,'companion');
  assert.deepEqual(owner.sourceProbeBytes,prefix);await releaseOwner(owner);
});

test('automatic Repeater detection cleans the probe line and requires a complete echoed identity',async()=>{
  const port=new Port('repeater'),token=await detectSource(port,fast);
  assert.equal(token.kind,'repeater');assert.equal(port.openCount,1);
  assert.deepEqual(port.writes,[query,encode('\r\nget public.key\r\n')]);
  const owner={port,options:{detectedSource:token}};adoptDetectedSource(owner,'repeater');assert.equal(owner.detectedSourceKey,sourceKey);
  await releaseOwner(owner);
});

test('a prompt-style Companion CLI identity is not mistaken for a Repeater',async()=>{
  const port=new Port('unknown');port.onWrite=async b=>{if(b[0]!==60)port.send(encode('get public.key\r\n> '+sourceKey+'\r\n> '));};
  await assert.rejects(detectSource(port,fast),/Cannot identify/);assert.equal(port.closeCount,1);
});

for(const initial of [Uint8Array.of(77,83,1,1),Uint8Array.of(77,83,3)])test('an existing native folder owner is left untouched: '+initial.length,async()=>{
  const port=new Port();port.initial=initial;
  await assert.rejects(detectSource(port,fast),/active firmware source/);
  assert.equal(port.writes.length,0);assert.equal(port.closeCount,1);
});

test('folder traffic that arrives after the first probe prevents every further write',async()=>{
  const port=new Port();port.onWrite=async()=>port.send(Uint8Array.of(77,83,1,1));
  await assert.rejects(detectSource(port,fast),/active firmware source/);
  assert.deepEqual(port.writes,[query]);assert.equal(port.closeCount,1);
});

test('unknown devices receive no APP_START, folder, peer or radio mutations',async()=>{
  const port=new Port('unknown');await assert.rejects(detectSource(port,fast),/Cannot identify/);
  assert.deepEqual(port.writes,[query,encode('\r\nget public.key\r\n')]);assert.equal(port.closeCount,1);
});

test('cancellation while waiting for input closes only the acquired USB handle',async()=>{
  const port=new Port('unknown'),controller=new AbortController();
  const result=detectSource(port,{...fast,binaryTimeout:1000,signal:controller.signal});
  setTimeout(()=>controller.abort(),15);await assert.rejects(result,{name:'AbortError'});
  assert.equal(port.openCount,1);assert.equal(port.closeCount,1);
});

test('a blocked probe write is bounded and canceled without waiting for its completion',async()=>{
  const port=new Port('unknown');port.onWrite=()=>new Promise(()=>{});
  await assert.rejects(detectSource(port,fast),/write timed out/);assert.equal(port.closeCount,1);
});

test('canceling before connection never acquires a USB handle',async()=>{
  const port=new Port(),controller=new AbortController();controller.abort();
  await assert.rejects(detectSource(port,{...fast,signal:controller.signal}),{name:'AbortError'});
  assert.equal(port.openCount,0);assert.equal(port.closeCount,0);
});

test('unused detection tokens close idempotently and cannot be applied to another port or source type',async()=>{
  const port=new Port(),token=await detectSource(port,fast);
  assert.throws(()=>adoptDetectedSource({port,options:{detectedSource:token}},'repeater'),/no longer valid/);
  assert.throws(()=>adoptDetectedSource({port:{},options:{detectedSource:token}},'companion'),/no longer valid/);
  await token.close();await token.close();assert.equal(port.closeCount,1);
});

test('a detected Repeater connects without reopening USB and may select its target afterward',async()=>{
  const port=new Port('repeater'),token=await detectSource(port,fast);
  const link=new RepeaterTransport(port,{detectedSource:token,deferredTarget:true,openSettle:0});
  await link.open();assert.equal(port.openCount,1);assert.equal(link.sourceKey,sourceKey);assert.equal(link.knownRepeater,null);
  link.selectTarget(targetKey);assert.equal(link.knownRepeater,targetKey);
  assert.throws(()=>link.selectTarget('c'.repeat(64)),/Disconnect/);
  const jobs=[];link.checkClock=async()=>{};link.job=async c=>{jobs.push(c);return 'OK';};
  await link.verifyPeer(targetKey,'password');assert.deepEqual(jobs,['ota.peer login.repeater '+targetKey+' password']);
  await link.close();await token.close();assert.equal(port.closeCount,1);
});

test('manual Repeater override never sends the automatic protocol probe',async()=>{
  const port=new Port('repeater'),link=new RepeaterTransport(port,{knownRepeater:targetKey,openSettle:0});
  await link.open();assert.equal(port.openCount,1);assert.ok(port.writes.every(b=>b[0]!==60));await link.close();
});

test('identity changing between detection and transport validation aborts without a peer operation',async()=>{
  const port=new Port('repeater'),token=await detectSource(port,fast);
  port.onWrite=async b=>port.send(encode(decode(b).trim()+'\r\n-> '+'c'.repeat(64)+'\r\n'));
  const link=new RepeaterTransport(port,{detectedSource:token,deferredTarget:true,openSettle:0});
  await assert.rejects(link.open(),/identity changed/);assert.equal(port.closeCount,1);
  assert.ok(port.writes.every(b=>!decode(b).includes('ota.peer')));
});

test('a late Companion identity remains valid after the CLI fallback begins',async()=>{
  const port=new Port('unknown');port.onWrite=async b=>{if(b[0]===60)setTimeout(()=>port.send(deviceInfo()),12);};
  const token=await detectSource(port,{...fast,cliTimeout:100});assert.equal(token.kind,'companion');await token.close();
});

test('MS bytes inside a complete binary identity cannot be mistaken for a folder owner',async()=>{
  const port=new Port('unknown');port.onWrite=async()=>{const frame=deviceInfo();frame.set([77,83,1],7);port.send(frame.slice(0,12));port.send(frame.slice(12));};
  const token=await detectSource(port,fast);assert.equal(token.kind,'companion');await token.close();
});

test('a device-info frame received before our probe is not accepted as its reply',async()=>{
  const port=new Port('unknown');port.initial=deviceInfo();
  await assert.rejects(detectSource(port,fast),/Cannot identify/);assert.equal(port.closeCount,1);
});
