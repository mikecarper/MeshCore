'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto').webcrypto;
const mota=require('../docs/_javascript/lora_ota_package.js');
const delta=require('../docs/_javascript/lora_ota_delta.js');
const candidates=require('../docs/_javascript/lora_ota_candidates.js');

async function image(version,change) {
  const bytes=new Uint8Array(4152), end=bytes.length-56, view=new DataView(bytes.buffer);
  for(let i=0;i<end;i++) bytes[i]=(i*29+7)&255;
  for(let i=0;i<change;i++) bytes[100+i]^=(version&255);
  bytes.set(Buffer.from('EndF'),end); view.setUint32(end+4,end,true);
  bytes.set(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes.slice(0,end))).slice(0,8),end+8);
  view.setUint32(end+16,version,true); view.setUint32(end+20,0x12345678,true);
  bytes.set(Buffer.from('board'),end+24); return bytes;
}
async function hybridImage(version,seed,flags) {
  const bytes=new Uint8Array(16080),end=bytes.length-56,record=end-24,v=new DataView(bytes.buffer);
  let state=seed>>>0;
  for(let i=0;i<record;i++){state^=state<<13;state^=state>>>17;state^=state<<5;bytes[i]=state&255;}
  bytes.set(Buffer.from('mOTALay1'),record);bytes[record+8]=1;bytes[record+9]=flags;
  v.setUint16(record+10,24,true);v.setUint32(record+12,0x26000,true);v.setUint32(record+16,0xED000,true);v.setUint32(record+20,0xED000,true);
  bytes.set(Buffer.from('EndF'),end);v.setUint32(end+4,end,true);
  bytes.set(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes.slice(0,end))).slice(0,8),end+8);
  v.setUint32(end+16,version,true);v.setUint32(end+20,0x12345678,true);bytes.set(Buffer.from('board'),end+24);
  return bytes;
}
const capability={otaConfirmed:true,nrf:false,external:false,sd:false,maxBlock:1024,codecs:[0,1]};
async function fixture() {
  const a=await image(0x01110001,0), b=await image(0x01110002,8), c=await image(0x01110003,16);
  const first=await mota.parseEndF(a);
  return {images:[{id:'a',bytes:a,capabilities:{...capability}},{id:'b',bytes:b,capabilities:{...capability}},{id:'c',bytes:c}],
    current:{node:'a',version:first.fwVersion,targetId:first.targetId,hwId:first.hwId,bodyHash:Buffer.from(first.bodyHash).toString('hex')},goal:'c'};
}
const deps={buildDelta:(a,b,o)=>delta.buildSequentialDelta(a,b,o)};

test('prepares a bounded version DAG and rechecks each selected package',async()=>{
  const f=await fixture(), result=await candidates.prepare(f,deps);
  assert.ok(result.steps.length>=1); assert.equal(result.steps.at(-1).to,'c'); assert.equal(result.complete,true);
  assert.ok(result.warnings.some(s=>s.includes('legacy framing')));
  assert.ok(result.warnings.some(s=>s.includes('confirmed again after each reboot')));
  assert.equal(result.packages.size,new Set(result.steps.map(s=>s.packageHash)).size);
  const first=result.steps[0], live={...capability,...f.current};
  const checked=await candidates.checkStep(result,0,live);
  assert.equal(checked.source.id,'a'); assert.equal(checked.target.id,first.to);
  assert.equal(checked.info.targetId,f.current.targetId);
  await assert.rejects(candidates.checkStep(result,0,{...live,bodyHash:'00'.repeat(8)}),/no longer matches/);
  await assert.rejects(candidates.checkStep(result,0,{...live,otaConfirmed:false}),/Confirm/);
  const old=first.package.blob[0]; first.package.blob[0]^=1;
  await assert.rejects(candidates.checkStep(result,0,live),/changed/); first.package.blob[0]=old;
});

test('unknown intermediate OTA support is not assumed',async()=>{
  const f=await fixture(); delete f.images[1].capabilities;
  const result=await candidates.prepare(f,deps);
  assert.equal(result.complete,false); assert.ok(result.warnings.some(s=>s.includes('intermediate')));
  assert.equal(result.steps.length,1); assert.equal(result.steps[0].to,'c');
});

test('uses proved v2 geometry only when explicitly confirmed',async()=>{
  const f=await fixture(); f.images[0].capabilities={...capability,maxBlock:2048,wireConfirmed:true,wireProfile:'v2',pipeline:2};
  const result=await candidates.prepare(f,deps);
  assert.equal(result.steps[0].edge.blockSize,2048); assert.equal(result.steps[0].estimate.profile,'v2');
});

test('rejects a wrong live base and malformed inputs before building',async()=>{
  const f=await fixture();
  await assert.rejects(candidates.prepare({...f,current:{...f.current,bodyHash:'00'.repeat(8)}},deps),/does not match/);
  await assert.rejects(candidates.prepare({...f,images:[f.images[0],f.images[0]]},deps),/unique ID/);
  const noCaps=await fixture(); delete noCaps.images[0].capabilities;
  await assert.rejects(candidates.prepare(noCaps,deps),/installed target OTA/);
  const aborted=new AbortController(); aborted.abort();
  await assert.rejects(candidates.prepare({...f,signal:aborted.signal},deps),/cancelled/);
});

test('marks unbuilt candidate edges as an incomplete comparison',async()=>{
  const f=await fixture();
  const result=await candidates.prepare(f,{buildDelta:async()=>{throw Error('worker unavailable');}});
  assert.equal(result.complete,false); assert.ok(result.warnings.some(s=>s.includes('worker unavailable')));
  assert.ok(result.steps.every(s=>s.kind==='full'));
});

test('target compatibility is checked again after each intermediate',async()=>{
  const f=await fixture(), result=await candidates.prepare(f,deps);
  const live={...capability,...f.current};
  await assert.rejects(candidates.checkStep(result,0,{...live,codecs:[]}),/codec/);
  await assert.rejects(candidates.checkStep(result,0,{...live,sd:true}),/trusted/);
});

test('hybrid internal package above one flash page is excluded without runtime MOTARAMA proof',async()=>{
  for(const flags of [0x10,0x30]) {
    const base=await hybridImage(0x01110001,1,flags), next=await hybridImage(0x01110002,2,flags), parsed=await mota.parseEndF(base);
    const c={otaConfirmed:true,nrf:true,external:false,sd:false,maxBlock:1024,codecs:[2],
      runtimeStorage:'internal',runtimeStageCeiling:0xED000};
    const current={node:'a',version:parsed.fwVersion,targetId:parsed.targetId,hwId:parsed.hwId,bodyHash:Buffer.from(parsed.bodyHash).toString('hex')};
    const worker=(a,b,o)=>delta.buildInPlaceDelta(a,b,o);
    assert.ok((await worker(base,next,c)).blob.length>4096);
    await assert.rejects(candidates.prepare({images:[{id:'a',bytes:base,capabilities:c},{id:'b',bytes:next}],current,goal:'b'},{buildDelta:worker}),/MOTARAMA/);
  }
});

test('small hybrid internal packages and confirmed adaptive QSPI remain available',async()=>{
  for(const [flags,storage,changed] of [[0x10,'internal',false],[0x30,'internal',false],[0x30,'qspi',true]]) {
    const base=await hybridImage(0x01110001,1,flags), next=await hybridImage(0x01110002,changed?2:1,flags), parsed=await mota.parseEndF(base);
    const c={otaConfirmed:true,nrf:true,external:storage==='qspi',sd:false,maxBlock:1024,codecs:[2],runtimeStorage:storage,
      ...(storage==='qspi'?{runtimeQspiApply:true,runtimeStorageBytes:2*1024*1024}:{runtimeStageCeiling:0xED000})};
    const current={node:'a',version:parsed.fwVersion,targetId:parsed.targetId,hwId:parsed.hwId,bodyHash:Buffer.from(parsed.bodyHash).toString('hex')};
    const result=await candidates.prepare({images:[{id:'a',bytes:base,capabilities:c},{id:'b',bytes:next}],current,goal:'b'},
      {buildDelta:(a,b,o)=>delta.buildInPlaceDelta(a,b,o)});
    assert.equal(result.steps[0].kind,'delta');
    assert.equal(result.steps[0].package.blob.length>4096,storage==='qspi');
    await candidates.checkStep(result,0,{...c,...current});
    const geometry=result.steps[0].edge.nrfGeometry;
    result.steps[0].edge.nrfGeometry=null;
    await assert.rejects(candidates.checkStep(result,0,{...c,...current}),/geometry is missing/);
    result.steps[0].edge.nrfGeometry=geometry;
    if(storage==='internal') {
      const plan=result.steps[0].edge.inplacePlan, saved=plan.layoutFlags; plan.layoutFlags=0;
      await assert.rejects(candidates.checkStep(result,0,{...c,...current}),/workspace no longer matches/);
      plan.layoutFlags=saved;
    }
    result.steps[0].sourceImage[0]^=1;
    await assert.rejects(candidates.checkStep(result,0,{...c,...current}),/source image/);
  }
});
