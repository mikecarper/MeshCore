'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const catalog=require('../docs/_data/lora_ota_bridges/index.json');
const checks=require('../docs/_javascript/lora_ota_checks.js');
const auto=require('../docs/_javascript/lora_ota_auto_chain.js');
const {ChainRunner}=require('../docs/_javascript/lora_ota_chain.js');
const {DirectSession}=require('../docs/_javascript/lora_ota_session.js');
const route=catalog.routes[0], withdrawn=route.images.map(image=>image.sha256);
const bytes=hash=>Uint8Array.from(Buffer.from(hash,'hex'));
const reject=error=>error.code==='OTA_WITHDRAWN_APPLICATION'&&/USB recovery/.test(error.message);

test('withdrawn GAT bridge destinations are rejected through normal package validation',()=>{
  const dest={targetId:route.targetId,hwId:route.hwId,version:1,baseHash:'11'.repeat(8),maxBlock:1024,nrf:true,codecs:4};
  const info={targetId:dest.targetId,hwId:dest.hwId,fwVersion:2,baseHash:bytes(dest.baseHash),blockSize:1024,isFull:false,codecId:2};
  for(const hash of withdrawn)assert.throws(()=>checks.validate({...info,imageHash:bytes(hash)},dest,false),reject);
  // The published destination remains available when its direct package fits.
  assert.equal(checks.validate({...info,imageHash:bytes(route.to)},dest,false),true);
});

test('the published path and a cached pre-withdrawal catalog stop before downloading bridges',async()=>{
  const base={imageHash:bytes(route.from),targetId:route.targetId,hwId:route.hwId},goal={imageHash:bytes(route.to)};
  assert.equal(route.disabled,true);
  assert.throws(()=>auto.routeFor(catalog,base,goal),reject);
  const stale=structuredClone(catalog);delete stale.routes[0].disabled;delete stale.routes[0].disabledReason;
  assert.throws(()=>auto.routeFor(stale,base,goal),reject);
  assert.equal(auto.routeFor({schemaVersion:1,routes:[]},base,goal),null);
});

function runnerFixture(){
  const source='aa'.repeat(32),target='bb'.repeat(32),calls=[];
  const prepared={nodes:[{id:'base',imageHash:route.from},{id:'goal',imageHash:route.to}],
    steps:[{from:'base',to:'goal',packageHash:'cc'.repeat(32),package:{info:{imageHash:bytes(route.to)}}}]};
  const saved={phase:'step starting',index:0,source,target},stepSaved={phase:'ready',id:'9df841df'};
  const args={prepared,link:{sourceKey:source},targetKey:target,
    readLive:async()=>{calls.push('read');},createSession:async()=>{calls.push('session');},
    readStepJournal:()=>stepSaved,writeStepJournal:()=>calls.push('write step'),
    readChainJournal:()=>saved,writeChainJournal:()=>calls.push('write chain')};
  return {args,calls,saved,stepSaved};
}

test('cached withdrawn plans cannot begin or resume and their recovery records are preserved',async()=>{
  for(const resume of [false,true]){
    const f=runnerFixture(),saved=structuredClone(f.saved),step=structuredClone(f.stepSaved);
    f.args.prepared.nodes[1].imageHash=withdrawn[0];
    assert.throws(()=>new ChainRunner(f.args),reject);
    assert.deepEqual(f.calls,[]);assert.deepEqual(f.saved,saved);assert.deepEqual(f.stepSaved,step);
    const clean=runnerFixture(),runner=new ChainRunner(clean.args);
    // Recheck an in-memory path as well as persisted plans loaded by a new page.
    clean.args.prepared.steps[0].package.info.imageHash=bytes(withdrawn[0]);
    await assert.rejects(runner.run({resume}),reject);assert.deepEqual(clean.calls,[]);
  }
});

test('a previously prepared ready bridge cannot send install or inspect after withdrawal',async()=>{
  const calls=[],journal={phase:'ready',id:'9df841df'},session=new DirectSession(
    {sourceKey:'aa'.repeat(32)},'bb'.repeat(32),'',{imageHash:bytes(withdrawn[0])},
    {readJournal:()=>journal,journal:()=>calls.push('journal')});
  session.inspect=async()=>{calls.push('inspect');};
  await assert.rejects(session.install(),reject);
  assert.deepEqual(calls,[]);assert.equal(journal.phase,'ready');
});
