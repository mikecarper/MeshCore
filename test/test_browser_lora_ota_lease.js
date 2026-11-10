'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {maintain}=require('../docs/_javascript/lora_ota_lease.js');
const {DirectSession}=require('../docs/_javascript/lora_ota_session.js');
const SOURCE='aa'.repeat(32),TARGET='bb'.repeat(32),MID='01234567';
function fixture(options={}){
  const calls=[],blob=Uint8Array.from([1,2,3]),tuple=[908.5,250,7,5];let time=Date.now(),ttl=[15,17],renewed=[false,false];
  const record={phase:'pull attempted',source:SOURCE,target:TARGET,id:MID,
    packageHash:crypto.createHash('sha256').update(blob).digest('hex'),bodyHash:'dd'.repeat(8),
    radio:{tuple,preamble:128,sourceProfile:0,targetProfile:1,explicitProfiles:[true,true],verifiedAt:time-1000,expiresAt:time+15*60000}};
  const link={sourceKey:SOURCE,knownRepeater:TARGET,serving:true,failure:null,otaRadioProfile:0,otaTargetProfile:1,
    seeder:{info:{merkleRoot:Uint8Array.from([1,35,69,103]),blob}},
    async localCommand(c){return read(0,c);},async remoteCommand(k,p,c){assert.equal(k,TARGET);assert.equal(p,'secret');return read(1,c);},
    async withOtaAdminBurst(k,p,action){calls.push(['burst',k,p]);return action({local:c=>read(0,c),remote:c=>read(1,c)});}};
  async function read(index,c){
    calls.push([index,c]);
    if(c==='get public.key')return options.wrongKey?'ee'.repeat(32):TARGET;
    if(c==='ota status')return 'download: fetching 641/803 id='+(options.wrongDownload?'deadbeef':MID);
    if(c==='get tempradio')return 'TempRadio active: '+tuple.join(',')+' '+ttl[0]*60+'s left,preamble=128';
    if(c==='get tempradio2')return tuple.join(',')+',rxtx,128'+(options.automatic?' (auto)':'')+'; ('+ttl[1]+' min) left';
    if(c==='get radio2.status')return 'rxtx; RX=0,0 TX=0,0 switches=1 errors=0 max=200us preamble=32,128';
    if(c.startsWith('tempradio ')||c.startsWith('set tempradio2 ')){
      renewed[index]=true;if(!options.noApply)ttl[index]=120;
      if(options.lostAck)throw Error('Remote command timed out; result is unknown');return 'OK';
    }
    throw Error('Unexpected '+c);
  }
  return {link,record,calls,renewed,ttl,options:{now:()=>time,wait:async ms=>{time+=ms;},...options},get time(){return time;}};
}
test('renews only unchanged owned profiles in one native admin burst and journals fresh deadlines',async()=>{
  const f=fixture();let saved;
  const result=await maintain(f.link,TARGET,'secret',f.record,{...f.options,journal:r=>{saved=r;}});
  assert.equal(result.state,'renewed');assert.equal(result.renewed,2);assert.deepEqual(f.renewed,[true,true]);
  assert.equal(f.calls.filter(c=>c[0]==='burst').length,1);
  assert.deepEqual(f.calls.filter(c=>typeof c[1]==='string'&&/^(?:set tempradio2|tempradio) /.test(c[1])),
    [[1,'set tempradio2 908.500,250,7,5,rxtx,120,128'],[0,'tempradio 908.500,250,7,5,120,128']]);
  assert.ok(saved.radio.expiresAt>=f.time+118*60000);assert.equal(saved.id,MID);
  assert.equal(f.calls.some(c=>/ota (?:pull|cancel)|^(?:set radio |set radio2 )/.test(c[1])),false);
});
test('lost setter acknowledgement is resolved by lease readback without replay',async()=>{
  const f=fixture({lostAck:true});await maintain(f.link,TARGET,'secret',f.record,f.options);
  assert.equal(f.calls.filter(c=>typeof c[1]==='string'&&c[1].startsWith('set tempradio2 ')).length,1);
  assert.equal(f.calls.filter(c=>typeof c[1]==='string'&&c[1].startsWith('tempradio ')).length,1);
});
test('an unconfirmed setter is never replayed or followed by another endpoint write',async()=>{
  const f=fixture({lostAck:true,noApply:true});
  await assert.rejects(maintain(f.link,TARGET,'secret',f.record,f.options),/timed out|confirmed/);
  assert.deepEqual(f.renewed,[false,true]);
  assert.equal(f.calls.filter(c=>typeof c[1]==='string'&&c[1].startsWith('set tempradio2 ')).length,1);
});
test('wrong key, package or download cannot authorize renewal',async()=>{
  for(const kind of ['wrongKey','wrongDownload','package']){
    const f=fixture({[kind]:true});
    if(kind==='package')f.record.packageHash='ee'.repeat(32);
    await assert.rejects(maintain(f.link,TARGET,'secret',f.record,f.options),/key|download|firmware|preamble/i);
    assert.equal(f.renewed.some(Boolean),false);
  }
});
test('automatic or unknown primary preamble ownership preserves the old bounded flow without writes',async()=>{
  for(const kind of ['automatic','primary']){
    const f=fixture({[kind]:true});
    if(kind==='primary')f.record.radio.explicitProfiles=[false,true];
    assert.equal((await maintain(f.link,TARGET,'secret',f.record,f.options)).state,'unavailable');
    assert.equal(f.renewed.some(Boolean),false);
  }
});
test('fresh lease skips all I/O and normal expiry checks renew neither already long profile',async()=>{
  const f=fixture();f.record.radio.expiresAt=f.time+90*60000;
  assert.equal((await maintain(f.link,TARGET,'secret',f.record,f.options)).state,'not-due');assert.deepEqual(f.calls,[]);
  f.ttl[0]=f.ttl[1]=120;
  assert.equal((await maintain(f.link,TARGET,'secret',f.record,{...f.options,forceCheck:true})).state,'unchanged');
  assert.equal(f.renewed.some(Boolean),false);
});
test('Session performs lease maintenance during an active transfer without another pull',async()=>{
  const f=fixture();let saved=f.record;
  delete f.link.withOtaAdminBurst;
  const session=new DirectSession(f.link,TARGET,'secret',null,{readJournal:()=>saved,journal:r=>{saved=r;}});
  session.serving=true;
  await session.maintainTemporaryLease({state:'active',status:'download: downloading 1/803 id='+MID});
  assert.ok(saved.radio.expiresAt>Date.now()+118*60000);
  assert.equal(session.verifiedRadio.expiresAt,saved.radio.expiresAt);
  assert.equal(f.calls.some(c=>String(c[1]).includes('ota pull')),false);
});
test('a resumed Session leaves metadata uninterrupted and renews only after a data-phase poll',async()=>{
  const f=fixture();let saved=f.record,polls=0,detaches=0;
  f.link.serve=async()=>{};f.link.stopServing=async()=>{f.link.serving=false;detaches++;};
  const session=new DirectSession(f.link,TARGET,'secret',f.link.seeder.info,
    {readJournal:()=>saved,journal:r=>{saved=r;},pollMs:1});
  session.packageHash=f.record.packageHash;session.bodyHash=f.record.bodyHash;session.verifiedRadio=f.record.radio;
  session.inspect=async()=>({download:'active'});
  session.readDownload=async()=>{
    polls++;
    if(polls===1){assert.equal(f.calls.length,0);return {id:MID,state:'active',status:'download: starting 0/0 id='+MID};}
    if(polls===2){assert.equal(f.calls.length,0);return {id:MID,state:'active',status:'download: downloading 0/803 id='+MID};}
    assert.deepEqual(f.renewed,[true,true]);return {id:MID,state:'ready',status:'ready'};
  };
  await session.start({resume:true});
  assert.equal(saved.phase,'ready');assert.equal(polls,3);assert.equal(detaches,1);
  assert.equal(f.calls.some(c=>String(c[1]).includes('ota pull')),false);
  assert.ok(saved.radio.expiresAt>Date.now()+118*60000);
});
test('Stop during a lease read prevents subsequent remote reads or renewal writes',async()=>{
  const f=fixture();let entered,release;
  const waiting=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  delete f.link.withOtaAdminBurst;
  f.link.remoteCommand=async(_key,_password,command)=>{f.calls.push([1,command]);entered();await waiting;return TARGET;};
  const run=maintain(f.link,TARGET,'secret',f.record,f.options);run.catch(()=>{});await started;
  f.link.serving=false;release();
  await assert.rejects(run,/Stopped/);
  assert.deepEqual(f.calls,[[1,'get public.key']]);assert.equal(f.renewed.some(Boolean),false);
});
test('a reused Session clears an earlier unavailable lease when the new preparation proves ownership',async()=>{
  const f=fixture();let saved=f.record;
  saved.radio.explicitProfiles=[false,true];
  const session=new DirectSession(f.link,TARGET,'secret',f.link.seeder.info,
    {readJournal:()=>saved,journal:r=>{saved=r;},pollMs:1});
  session.serving=true;await session.maintainTemporaryLease({state:'active',status:'download: downloading 1/803 id='+MID});
  assert.equal(session.leaseMaintenanceUnavailable,true);assert.equal(f.renewed.some(Boolean),false);
  session.serving=false;f.link.serving=false;
  session.packageHash=saved.packageHash;session.bodyHash=saved.bodyHash;
  f.link.serve=async()=>{f.link.serving=true;};f.link.stopServing=async()=>{f.link.serving=false;};
  session.inspect=async()=>{session.verifiedRadio={...saved.radio,explicitProfiles:[true,true]};return {download:'active'};};
  let reads=0;
  session.readDownload=async()=>++reads===1?{id:MID,state:'active',status:'download: downloading 1/803 id='+MID}:{id:MID,state:'ready',status:'ready'};
  await session.start({resume:true});
  assert.deepEqual(f.renewed,[true,true]);assert.equal(session.leaseMaintenanceUnavailable,false);
});
test('a fresh metadata or seed-validation status defers an admitted helper before any setter',async()=>{
  for(const state of ['starting 0/0','validating seed 0/803','verifying staged blocks 0/803']){
    const f=fixture(),remote=f.link.remoteCommand;
    f.link.withOtaAdminBurst=async(_key,_password,action)=>action({local:c=>f.link.localCommand(c),
      remote:c=>c==='ota status'?Promise.resolve('download: '+state+' id='+MID):remote(TARGET,'secret',c)});
    assert.equal((await maintain(f.link,TARGET,'secret',f.record,f.options)).state,'waiting-data');
    assert.equal(f.renewed.some(Boolean),false);
    assert.equal(f.calls.some(c=>String(c[1]).startsWith('get tempradio')),false);
  }
});
