'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const fs=require('node:fs');
const mota=require('../docs/_javascript/lora_ota_package.js');
const delta=require('../docs/_javascript/lora_ota_delta.js');
const checks=require('../docs/_javascript/lora_ota_checks.js');
const auto=require('../docs/_javascript/lora_ota_auto_chain.js');
const bsdiff=require('../docs/_javascript/vendor/detools-js/bsdiff.js');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');

async function fixture(){
  let seed=24680;const rand=()=>{seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;return seed&255;};
  const length=48000,end=length-56,record=end-24,a=Uint8Array.from({length},rand),b=a.slice();
  for(let i=256;i<record/2;i++)b[i]^=rand();
  const c=b.slice();for(let i=Math.ceil(record/2);i<record;i++)c[i]^=rand();
  const stamp=(image,version)=>{
    const view=new DataView(image.buffer);
    image.set(Buffer.from('OTA: status | get <id> flash | install'),0);
    image.set(Buffer.from('invalid in-place patch geometry'),64);
    image.set(Buffer.from('mOTALay1'),record);image[record+8]=1;image[record+9]=0;view.setUint16(record+10,24,true);
    view.setUint32(record+12,0x26000,true);view.setUint32(record+16,0xED000,true);view.setUint32(record+20,0xED000,true);
    image.set(Buffer.from('EndF'),end);view.setUint32(end+4,end,true);
    image.set(crypto.createHash('sha256').update(image.subarray(0,end)).digest().subarray(0,8),end+8);
    view.setUint32(end+16,version,true);view.setUint32(end+20,0x12345678,true);image.fill(0,end+24);image.set(Buffer.from('board'),end+24);
    return image;
  };
  const base=stamp(a,0x01120001),mid=stamp(b,0x01120001),goal=stamp(c,0x01120002),parsed=await mota.parseEndF(base);
  const caps={otaConfirmed:true,nrf:true,external:false,sd:false,maxBlock:1024,codecs:[2],runtimeStorage:'internal',
    runtimeStageCeiling:0x3C000,targetId:parsed.targetId,hwId:parsed.hwId,version:parsed.fwVersion,bodyHash:checks.hex(parsed.bodyHash)};
  const index={schemaVersion:1,routes:[{from:sha(base),to:sha(goal),targetId:parsed.targetId,hwId:parsed.hwId,
    images:[{sha256:sha(mid),size:mid.length}]}]};
  const options={index,loadImage:async()=>mid,buildDelta:delta.buildInPlaceDelta};
  return {base,mid,goal,caps,index,options};
}

test('chooses two complete fitting patches when the direct update cannot fit',async()=>{
  const f=await fixture();
  await assert.rejects(delta.buildInPlaceDelta(f.base,f.goal,{...f.caps,searchWorkspaces:'fit'}),{code:'OTA_DELTA_SPACE'});
  const plan=await auto.prepare(f.base,f.goal,f.caps,f.options);
  assert.equal(plan.steps.length,2);assert.equal(plan.steps[0].edge.allowSameVersion,true);
  for(const step of plan.steps){
    assert.deepEqual(delta.applyInPlace(step.sourceImage,step.package.info.payload),step.targetImage);
    assert.ok(step.edge.inplacePlan.memorySize+Math.ceil(step.package.blob.length/4096)*4096<=f.caps.runtimeStageCeiling-0x26000);
  }
  assert.equal(sha(plan.steps.at(-1).targetImage),sha(f.goal));
});

test('unknown paths, bad image digests, changed identity and cancellation cannot send anything',async()=>{
  const f=await fixture();let builds=0;
  assert.equal(await auto.prepare(f.base,f.goal,f.caps,{...f.options,index:{schemaVersion:1,routes:[]}}),null);
  await assert.rejects(auto.prepare(f.base,f.goal,f.caps,{...f.options,loadImage:async()=>f.goal,
    buildDelta:()=>{builds++;}}),/SHA-256/);
  assert.equal(builds,0);
  await assert.rejects(auto.prepare(f.base,f.goal,{...f.caps,bodyHash:'00'.repeat(8)},f.options),/does not match/);
  const controller=new AbortController();controller.abort();
  await assert.rejects(auto.prepare(f.base,f.goal,f.caps,{...f.options,signal:controller.signal}),/stopped/);
});

test('deep search exposes the tradeoff between fewer installs and less data',()=>{
  const nodes=['a','b','c','d'].map(id=>({id}));
  const edges=[{from:'a',to:'b',radioDataBytes:100},{from:'a',to:'d',radioDataBytes:1000},
    {from:'b',to:'c',radioDataBytes:100},{from:'b',to:'d',radioDataBytes:500},{from:'c',to:'d',radioDataBytes:100}];
  const paths=auto.rankPaths(nodes,edges);
  assert.equal(paths.fewer.steps.length,1);assert.equal(paths.fewer.bytes,1000);
  assert.equal(paths.smaller.steps.length,3);assert.equal(paths.smaller.bytes,300);
});

test('equal-version bridges are explicit and downgrades remain rejected',async()=>{
  const f=await fixture();
  await assert.rejects(delta.buildInPlaceDelta(f.base,f.mid,f.caps),/newer/);
  assert.ok((await delta.buildInPlaceDelta(f.base,f.mid,{...f.caps,allowSameVersion:true})).blob.length);
  await assert.rejects(delta.buildInPlaceDelta(f.goal,f.mid,{...f.caps,allowSameVersion:true}),/newer/);
  await assert.rejects(delta.buildInPlaceDelta(f.base,f.base,{...f.caps,allowSameVersion:true}),/already installed/);
});
test('deep geometry search checks every allowed flash page and reconstructs its selected patch',async()=>{
  const f=await fixture(),states=[];
  const result=await delta.buildInPlaceDelta(f.base,f.mid,{...f.caps,allowSameVersion:true,searchWorkspaces:'deep',progress:s=>states.push(s)});
  const minimum=Math.max(Math.ceil(f.base.length/4096)*4096+8192,Math.ceil(f.mid.length/4096)*4096);
  const count=(f.caps.runtimeStageCeiling-0x26000-4096-minimum)/4096+1;
  assert.equal(states.length,count);assert.deepEqual(states.map(s=>s.index),Array.from({length:count},(_,i)=>i+1));
  assert.deepEqual(delta.applyInPlace(f.base,result.payload),f.mid);
});

test('hybrid budget and older GAT bootloader proof require exact live geometry and identity',async()=>{
  const status='BL board=239A0029 target=D50D2D44 name=GAT562_DFU crc=03F81FDD abi=3 caps=0A | staged:none mid=- hash=-';
  const calls=[],read=async command=>{calls.push(command);return command==='ota ram'?"Unknown OTA command. Type 'ota help'.":status;};
  assert.equal(await checks.probeHybridRam(read),'OTA RAM: hybrid=1 abi=1 record=72 arena=65536');
  assert.deepEqual(calls,['ota ram','ota bootloader status']);
  for(const bad of [status.replace('03F81FDD','00000000'),status.replace('GAT562_DFU','4631_DFU'),status+' caps=0A'])
    assert.throws(()=>checks.legacyHybridRam(bad),/cannot confirm/);
  await assert.rejects(checks.probeHybridRam(async()=>{throw Error('timed out');}),/timed out/);
  await assert.rejects(checks.probeHybridRam(async()=>'OTA RAM: hybrid=0'),/cannot be confirmed/);
  assert.equal(checks.stagedFlashBytes(70000,16,0xED000,checks.legacyHybridRam(status)),8192);
  assert.equal(checks.stagedFlashBytes(70000,16,0xED000,null),73728);
  assert.throws(()=>checks.stagedFlashBytes(70000,16,0xD4000,checks.legacyHybridRam(status)),/expanded/);
});

test('reused suffix indexes produce the same controls as independent source sorts',()=>{
  const source=crypto.randomBytes(1024),index=bsdiff.qsufsort(source,source.length);
  for(const from of [0,1,31,500,1023,1024]){
    const suffix=Int32Array.from(Array.from(index).filter(x=>x>=from),x=>x-from),target=source.subarray(700,900);
    assert.deepEqual(bsdiff.createControl(source.subarray(from),target,suffix),bsdiff.createControl(source.subarray(from),target));
  }
});

test('archived bridge resources retain their forensic bytes, layouts and receiver identities',async()=>{
  const root=__dirname+'/../docs/_data/lora_ota_bridges/',index=JSON.parse(fs.readFileSync(root+'withdrawn.json','ascii'));
  const zlib=require('node:zlib');
  for(const route of index.routes)for(const entry of route.images){
    const file=fs.readFileSync(root+entry.sha256+'.json');assert.ok(file.every(b=>b<128));
    const resource=JSON.parse(file),raw=zlib.gunzipSync(Buffer.from(resource.data,'base64'),{maxOutputLength:entry.size});
    assert.equal(raw.length,entry.size);assert.equal(sha(raw),entry.sha256);
    const parsed=await mota.parseEndF(raw);assert.equal(parsed.targetId,route.targetId);assert.equal(parsed.hwId,route.hwId);
    assert.equal(delta.nrfLayout(raw).flags,16);assert.match(raw.toString('latin1'),/OTA: status/);
  }
});
