'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');
const api=require('../docs/_javascript/lora_ota_secondary_only.js');
const source='a'.repeat(64),target='b'.repeat(64);
function fixture(){
 const data=new Map(),calls=[],states=['off','off'];
 const storage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};
 const helper=api.create({storage,random:b=>b.fill(18),wait:async()=>{}});
 const read=async(i,cmd)=>{calls.push([i,cmd]);if(cmd==='get radio2.only')return '> '+states[i];
  const m=/^set radio2.only (on|off) ([A-F0-9]{16})$/.exec(cmd);assert.ok(m);states[i]=m[1]==='on'?'on '+m[2]:'off';return 'OK - radio2.only '+m[1];};
 const link={sourceKey:source,knownRepeater:target,browserRawSource:true,otaSupported:true,running:true,localCommand:c=>read(0,c),remoteCommand:(k,p,c)=>read(1,c)};
 return {helper,link,calls,states,read};
}
test('only verified secondary endpoints are pinned and ownership is restored',async()=>{
 const f=fixture();await f.helper.activate(f.link,target,'',{sourceProfile:1,targetProfile:0});
 assert.equal(f.states[0],'on 1212121212121212');assert.equal(f.states[1],'off');
 assert.equal(f.helper.load().sourceOwned,true);assert.equal(f.helper.load().targetOwned,false);
 await f.helper.restore(f.link,target,'');assert.deepEqual(f.states,['off','off']);assert.equal(f.helper.load(),null);
});
test('new profile generation re-arms only owned tokens; refresh repeats idempotent on',async()=>{
 const f=fixture();await f.helper.activate(f.link,target,'',{sourceProfile:1,targetProfile:1});
 f.states[1]='off';await f.helper.rearm(source,target,1,c=>f.read(1,c),()=>{});
 assert.equal(f.states[1],'on 1212121212121212');const n=f.calls.length;
 await f.helper.rearm(source,target,1,c=>f.read(1,c),()=>{},()=>{},true);
 assert.ok(f.calls.slice(n).some(([,c])=>c==='set radio2.only on 1212121212121212'));
});
test('foreign owners are never overwritten or cleared',async()=>{
 const f=fixture();f.states[1]='on AAAAAAAAAAAAAAAA';await f.helper.activate(f.link,target,'',{sourceProfile:1,targetProfile:1});
 assert.equal(f.states[1],'on AAAAAAAAAAAAAAAA');assert.equal(f.helper.load().targetOwned,false);
 await f.helper.restore(f.link,target,'');assert.equal(f.states[1],'on AAAAAAAAAAAAAAAA');
});
test('old or unsupported firmware is skipped, with no setting write',async()=>{
 const f=fixture();f.link.localCommand=async c=>{f.calls.push([0,c]);return 'Error: radio2.only unsupported';};
 const result=await f.helper.activate(f.link,target,'',{sourceProfile:1,targetProfile:0});
 assert.equal(result.state,'ready');assert.equal(f.helper.load(),null);assert.equal(f.calls.length,1);
});
test('when target cannot be reached, source is still restored and its record retained',async()=>{
 const f=fixture();await f.helper.activate(f.link,target,'',{sourceProfile:1,targetProfile:1});
 f.link.remoteCommand=async()=>{throw Error('target timeout')};
 await assert.rejects(f.helper.restore(f.link,target,''),/timeout/);assert.equal(f.states[0],'off');
 assert.equal(f.helper.load().sourceOwned,false);assert.equal(f.helper.load().targetOwned,true);
});
test('failed ownership proof forbids any command',async()=>{
 const f=fixture();f.link.knownRepeater='c'.repeat(64);
 await assert.rejects(f.helper.activate(f.link,target,'',{sourceProfile:1,targetProfile:1}),/connection changed/);assert.equal(f.calls.length,0);
});
test('a lost refresh acknowledgement is not confirmed by the old on state',async()=>{
 const f=fixture();await f.helper.activate(f.link,target,'',{sourceProfile:0,targetProfile:1});
 let writes=0;const uncertain=async c=>{if(c.startsWith('set radio2.only')){writes++;throw Error('timed out');}return f.read(1,c);};
 await assert.rejects(f.helper.rearm(source,target,1,uncertain,()=>{},()=>{},true),/timed out/);
 assert.equal(writes,1);
});
