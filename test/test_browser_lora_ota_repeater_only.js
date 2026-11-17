'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {RepeaterTransport}=require('../docs/_javascript/lora_ota_serial.js');
const radioOnly=require('../docs/_javascript/lora_ota_secondary_only.js');
const SOURCE='aa'.repeat(32),TARGET='bb'.repeat(32),TOKEN='1212121212121212';
function fixture({acl=false}={}){
 const map=new Map(),calls=[],state=['off','off'];
 const storage={getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};
 const helper=radioOnly.create({storage,random:b=>b.fill(18),wait:async()=>{}});
 const link=new RepeaterTransport({}, {knownRepeater:TARGET,aclOnly:acl});
 Object.assign(link,{running:true,portOpened:true,writer:{},sourceKey:SOURCE,ownsPeer:true,otaPathValue:'OFF',
   otaRadioProfile:1,otaTargetProfile:1,supportsOtaRadio2:true,authenticatedKey:acl?null:TARGET,
   diagnosticPassword:acl?'':'secret',lastPeerUse:Date.now()});
 const reply=(i,text)=>{if(text==='get radio2.only')return '> '+state[i];
   const match=/^set radio2.only (on|off) ([0-9A-F]{16})$/.exec(text);assert.ok(match,text);
   state[i]=match[1]==='on'?'on '+match[2]:'off';return 'OK - radio2.only '+match[1];};
 link.localCommand=async(text)=>{calls.push(['local',text]);if(text==='ota.peer path'){link.otaPathValue='OFF';return 'OTApeer path=off';}
   if(text==='ota.peer radio')return 'OTApeer radio=radio2';return reply(0,text);};
 link.job=async command=>{calls.push(['remote',command]);const text=command.split(' ').slice(3).join(' ');
   if(text==='get public.key')return '> '+TARGET;return reply(1,text);};
 return {link,helper,calls,state};
}
for(const acl of [false,true])test('guarded repeater '+(acl?'ACL':'password')+' activation and cleanup',async()=>{
 const f=fixture({acl}),password=acl?'':'secret';
 await f.helper.activate(f.link,TARGET,password,{sourceProfile:1,targetProfile:1});
 assert.deepEqual(f.state,['on '+TOKEN,'on '+TOKEN]);
 assert.ok(f.calls.some(([ep,c])=>ep==='remote'&&c.startsWith('ota.peer '+(acl?'cmd.acl':'cmd')+' ')));
 await f.helper.restore(f.link,TARGET,password);assert.deepEqual(f.state,['off','off']);assert.equal(f.helper.load(),null);
});
test('repeater burst refuses changed target, path and expired session before mutation',async()=>{
 for(const change of ['target','path','password']){
  const f=fixture();if(change==='target')f.link.knownRepeater='cc'.repeat(32);
  if(change==='path')f.link.localCommand=async()=> 'OTApeer path=1122';
  if(change==='password')f.link.lastPeerUse=Date.now()-241000;
  await assert.rejects(f.helper.activate(f.link,TARGET,'secret',{sourceProfile:1,targetProfile:1}));
  assert.equal(f.calls.some(([,c])=>c.includes('set radio2.only')),false);assert.equal(f.helper.load(),null);
 }
});
test('uncertain repeater setter quarantines transport and retains recovery record',async()=>{
 const f=fixture(),base=f.link.job;f.link.job=async command=>{
   if(command.includes('set radio2.only on'))throw Error('OTA request timed out');return base(command);
 };
 await assert.rejects(f.helper.activate(f.link,TARGET,'secret',{sourceProfile:1,targetProfile:1}),/timed out/);
 assert.ok(f.link.peerFailure);assert.equal(f.link.failure,null);assert.equal(f.helper.load().targetOwned,true);assert.equal(f.helper.load().sourceOwned,false);
 await assert.rejects(f.helper.restore(f.link,TARGET,'secret'));assert.deepEqual(f.state,['off','off']);
});
test('guarded repeater aborts after profile or target changes during a reply',async()=>{
 for(const change of ['target','profile','targetProfile']){
  const f=fixture(),base=f.link.job;f.link.job=async command=>{
   const reply=await base(command);if(command.endsWith('get public.key')){
    if(change==='target')f.link.knownRepeater='cc'.repeat(32);else if(change==='profile')f.link.otaRadioProfile=0;else f.link.otaTargetProfile=0;
   }return reply;
  };
  await assert.rejects(f.helper.activate(f.link,TARGET,'secret',{sourceProfile:1,targetProfile:1}));
  assert.equal(f.calls.some(([,c])=>c.includes('set radio2.only')),false);
 }
});

test('repeater burst gives slow RF jobs the full bounded deadline',async()=>{
 const f=fixture(),base=f.link.job,budgets=[];
 f.link.remoteTimeout=75000;
 f.link.job=async command=>{budgets.push(f.link.remoteTimeout);return base(command);};
 await f.helper.activate(f.link,TARGET,'secret',{sourceProfile:1,targetProfile:1});
 assert.ok(budgets.length>=3);assert.ok(budgets.every(value=>value===40000));
 assert.equal(f.link.remoteTimeout,75000);
});
test('guarded ACL jobs accept replies delayed 15 seconds without a retry',async()=>{
 const f=fixture({acl:true}),saved=Date.now;let clock=saved(),pending='',offset=0,writes=0;
 Date.now=()=>clock;f.link.pollMs=1;
 const job=RepeaterTransport.prototype.job.bind(f.link);
 f.link.job=(cmd,opts)=>job(cmd,opts);
 f.link.localCommand=async text=>{
   if(text==='ota.peer path'){f.link.otaPathValue='OFF';return 'OTApeer path=off';}
   if(text==='ota.peer radio')return 'OTApeer radio=radio2';
   if(text.startsWith('ota.peer cmd.acl ')){
     writes++;const cmd=text.split(' ').slice(3).join(' ');
     if(cmd==='get public.key')pending=TARGET;
     else if(cmd==='get radio2.only')pending='> off';
     else throw Error('unexpected '+cmd);
     offset=0;return 'OTApeer job=01020304 state=pending';
   }
   if(text.startsWith('ota.peer result')){
     clock+=15000;const bytes=Buffer.from(pending);const part=bytes.subarray(offset,offset+48);
     const reply='OTApeer job=01020304 state=ok bytes='+bytes.length+' off='+offset+' data='+part.toString('hex');
     offset+=part.length;return reply;
   }
   throw Error('unexpected '+text);
 };
 try{
   const value=await f.link.withRadio2OnlyBurst(TARGET,'',({remote})=>remote('get radio2.only'));
   assert.equal(value,'> off');assert.equal(writes,2);assert.equal(f.link.failure,null);
 }finally{Date.now=saved;}
});
test('a quarantined peer job permits local owned folder detach and blocks late mailbox reuse',async()=>{
 const f=fixture(),link=f.link,calls=[];
 link.localCommand=RepeaterTransport.prototype.localCommand.bind(link);
 link.job=RepeaterTransport.prototype.job.bind(link);
 link.writer={write:async bytes=>{
   const cmd=new TextDecoder().decode(bytes).trim();calls.push(cmd);
   const reply=cmd==='ota folder off'?'OK folder detached':cmd==='ota folder'?'folder=off serving=0:':null;
   assert.ok(reply,'unexpected local command '+cmd);
   queueMicrotask(()=>{link.waiter?.(cmd);link.waiter?.('-> '+reply);});
 }};
 link.serving=true;link.seeder={};link.peerFailure=Error('OTA request timed out');
 await assert.rejects(link.localCommand('ota.peer result'),/timed out/);
 await assert.rejects(link.job('ota.peer cmd '+TARGET+' get public.key'),/timed out/);
 await link.stopServing();assert.equal(link.serving,false);assert.equal(link.folderUnknown,false);
 assert.deepEqual(calls,['ota folder off']);
});
test('a cancelled serving burst sends no commands after its pending read returns',async()=>{
 const f=fixture();f.link.serving=true;let release,entered;
 const pending=new Promise(r=>release=r),started=new Promise(r=>entered=r),base=f.link.job;
 f.link.job=async command=>{if(command.endsWith('get public.key')){entered();await pending;}return base(command);};
 const task=f.helper.activate(f.link,TARGET,'secret',{sourceProfile:1,targetProfile:1});
 await started;f.link.serving=false;release();await assert.rejects(task,/changed|Stopped/);
 assert.equal(f.calls.some(([,command])=>command.includes('set radio2.only')),false);
});
test('source profile changes during the final target read cannot confirm radio2-only activation',async()=>{
 const f=fixture();let targetReads=0;const base=f.link.job;
 f.link.job=async command=>{
   const reply=await base(command);
   if(command.endsWith('get radio2.only')&&++targetReads===2)f.link.otaRadioProfile=0;
   return reply;
 };
 await assert.rejects(f.helper.activate(f.link,TARGET,'secret',{sourceProfile:1,targetProfile:1}),/changed|radio/i);
 assert.equal(targetReads,2);
 assert.equal(f.calls.some(([endpoint,command])=>endpoint==='local'&&command.startsWith('set radio2.only on ')),false);
 assert.equal(f.helper.load().targetOwned,true);
});
