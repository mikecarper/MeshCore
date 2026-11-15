'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const quiet=require('../docs/_javascript/lora_ota_quiet.js');
const SOURCE='aa'.repeat(32),TARGET='bb'.repeat(32);
const clone=value=>value===null?null:JSON.parse(JSON.stringify(value));
function fixture(options={}){
  let data=null,forwarding=options.original||'on';
  const calls=[],events=[],messages=[];
  const store={read(){return clone(data);},write(value){events.push(['save',clone(value)]);if(options.storageFail)throw Error('Storage failed');data=clone(value);},
    clear(){events.push(['clear']);data=null;}};
  const link={sourceKey:SOURCE,knownRepeater:TARGET,running:true,serving:false,relays:[],
    async verifyPeer(key,password){calls.push('verify');assert.equal(key,TARGET);assert.equal(password,'secret');},
    async remoteCommand(key,password,command){
      assert.equal(key,TARGET);assert.equal(password,'secret');calls.push(command);events.push(['remote',command]);
      if(command==='get public.key')return options.wrongKey?'cc'.repeat(32):TARGET;
      if(command==='get radio.flood'){
        if(options.floodError)throw options.floodError;
        if(options.legacy||options.unsupportedBoth)return 'unknown config: radio.flood';
        return options.floodReply??'> '+forwarding;
      }
      if(command==='get repeat')return options.unsupportedBoth?'Unknown command':options.repeatReply??'> '+forwarding;
      if(/^set (?:radio\.flood|repeat) (?:on|off)$/.test(command)){
        assert(data,'Recovery record must be durable before a setter');
        assert.equal(data.source,SOURCE);assert.equal(data.target,TARGET);
        const desired=command.split(' ').at(-1);
        if(desired==='off'){assert.equal(data.state,'pause-pending');if(!options.offNoApply)forwarding='off';if(options.offLost)throw Error('Off acknowledgement timed out');}
        else{assert.equal(data.state,'restore-pending');assert.equal(data.restoreAttempted,true);if(!options.onNoApply)forwarding='on';if(options.onLost)throw Error('On acknowledgement timed out');}
        return 'OK';
      }
      throw Error('Unexpected command '+command);
    }};
  const settings={store,progress:message=>messages.push(message)};
  return {link,store,settings,calls,events,messages,options,get data(){return clone(data);},set data(value){data=clone(value);},
    get forwarding(){return forwarding;},set forwarding(value){forwarding=value;},
    plan(){return quiet.plan(link,TARGET,'secret',settings);},
    pause(plan,extra={}){return quiet.pause(link,TARGET,'secret',plan,{...settings,radioVerified:true,...extra});},
    restore(extra={}){return quiet.restore(link,TARGET,'secret',{...settings,...extra});}};
}
const setters=f=>f.calls.filter(command=>command.startsWith('set '));
async function paused(f){const plan=await f.plan();assert.equal((await f.pause(plan)).state,'paused');return plan;}

test('primary flood pause is planned read-only, stored before its single setter and restored exactly',async()=>{
  const f=fixture(),plan=await f.plan();
  assert.equal(plan.setting,'radio.flood');assert.equal(plan.original,'on');assert.equal(f.data,null);assert.deepEqual(setters(f),[]);
  assert.equal(f.calls[0],'verify');assert.equal(f.calls[1],'get public.key');
  assert.equal((await f.pause(plan)).state,'paused');assert.equal(f.data.state,'paused');
  assert.deepEqual(setters(f),['set radio.flood off']);
  assert.equal((await f.restore()).state,'restored');assert.equal(f.data,null);
  assert.deepEqual(setters(f),['set radio.flood off','set radio.flood on']);
  assert(!JSON.stringify(f.events).includes('secret'));
  assert(!f.calls.some(command=>command.includes('radio2.flood')||command.startsWith('set repeat')));
});
test('only explicit unsupported per-radio control falls back to the global repeat setting',async()=>{
  const f=fixture({legacy:true});await paused(f);assert.equal(f.data.setting,'repeat');
  assert(f.messages.some(message=>message.includes('both radio profiles')));
  await f.restore();assert.deepEqual(setters(f),['set repeat off','set repeat on']);
  assert(!f.calls.some(command=>command==='set radio.flood off'||command.includes('radio2')));
});
test('the exact legacy firmware unknown-setting reply permits only its named getter fallback',async()=>{
  const f=fixture({floodReply:'Error: unknown setting: radio.flood'}),plan=await f.plan();
  assert.equal(plan.setting,'repeat');assert(f.calls.includes('get repeat'));
  const g=fixture({floodReply:'Error: unknown setting: another-setting'});
  await assert.rejects(g.plan(),/Cannot confirm/);assert(!g.calls.includes('get repeat'));
});
test('ambiguous, unknown, empty and failed per-radio reads never select global fallback',async()=>{
  for(const floodReply of ['unknown','pending','',null,42,'ERR permission denied','on; pending','Unknown timeout']){
    const f=fixture({floodReply});
    if(floodReply===null)f.options.floodReply={};
    await assert.rejects(f.plan(),/Cannot confirm/);assert(!f.calls.includes('get repeat'));assert.deepEqual(setters(f),[]);
  }
  const f=fixture({floodError:Error('Remote command timed out')});
  await assert.rejects(f.plan(),/timed out/);assert(!f.calls.includes('get repeat'));
});
test('explicit unsupported controls on both APIs skip nonrepeaters without any setting write',async()=>{
  const f=fixture({unsupportedBoth:true}),plan=await f.plan();assert.equal(plan.state,'skipped');
  assert.equal((await f.pause(plan)).state,'skipped');assert.equal((await f.restore()).state,'none');
  assert.equal(f.data,null);assert.deepEqual(setters(f),[]);
});
test('already-off settings never produce a write, durable record or later enable',async()=>{
  for(const legacy of [false,true]){
    const f=fixture({original:'off',legacy}),plan=await f.plan();
    assert.equal((await f.pause(plan)).state,'already-off');assert.equal((await f.restore()).state,'none');
    assert.equal(f.data,null);assert.deepEqual(setters(f),[]);
  }
});
test('pause requires a verified radio handoff and a fresh unchanged forwarding readback',async()=>{
  const f=fixture(),plan=await f.plan();
  await assert.rejects(f.pause(plan,{radioVerified:false}),/Verify the update radio/);
  f.forwarding='off';await assert.rejects(f.pause(plan),/changed since preparation/);
  assert.deepEqual(setters(f),[]);assert.equal(f.data,null);
});
test('a lost pause acknowledgement is resolved by GET only; repeating pause never repeats its setter',async()=>{
  const f=fixture({legacy:true,offLost:true}),plan=await f.plan();
  assert.equal((await f.pause(plan)).state,'paused');assert.equal((await f.pause(plan)).state,'paused');
  assert.deepEqual(setters(f),['set repeat off']);
});
test('an unresolved pause persists recovery but never blindly repeats Off',async()=>{
  const f=fixture({offLost:true,offNoApply:true}),plan=await f.plan();
  assert.equal((await f.pause(plan)).state,'pending');assert.equal(f.data.state,'pause-pending');
  assert.equal((await f.pause(plan)).state,'pending');assert.deepEqual(setters(f),['set radio.flood off']);
  await assert.rejects(f.plan(),/Restore the previous/);
  assert.equal((await f.restore()).state,'restored');assert.equal(f.data,null);
  assert.deepEqual(setters(f),['set radio.flood off']);
});
test('a lost restoration acknowledgement is resolved without a second On setter',async()=>{
  const f=fixture({onLost:true});await paused(f);
  assert.equal((await f.restore()).state,'restored');assert.equal(f.data,null);
  assert.deepEqual(setters(f),['set radio.flood off','set radio.flood on']);
});
test('unresolved restoration needs an explicit user retry and an already-restored setting just clears recovery',async()=>{
  const f=fixture({legacy:true,onLost:true,onNoApply:true});await paused(f);
  assert.equal((await f.restore()).state,'pending');assert.equal(f.data.restoreAttempted,true);
  assert.equal((await f.restore()).state,'pending');assert.deepEqual(setters(f),['set repeat off','set repeat on']);
  f.options.onLost=false;f.options.onNoApply=false;
  assert.equal((await f.restore({retry:true})).state,'restored');assert.equal(f.data,null);
  assert.deepEqual(setters(f),['set repeat off','set repeat on','set repeat on']);
  const g=fixture();await paused(g);g.forwarding='on';await g.restore();assert.deepEqual(setters(g),['set radio.flood off']);
});
test('corrupt or foreign records never authorize any writes or guessed controls',async()=>{
  const original={version:1,source:SOURCE,target:TARGET,setting:'repeat',original:'on',state:'paused',restoreAttempted:false};
  for(const mutate of [record=>{record.setting='repeat; reboot';},record=>{record.original='off';},
    record=>{record.source='cc'.repeat(32);},record=>{record.target='cc'.repeat(32);},record=>{record.password='secret';},
    record=>{record.restoreAttempted=true;},record=>{record.version=2;},record=>{delete record.state;}]){
    const f=fixture();f.data=original;const value=f.data;mutate(value);f.data=value;
    await assert.rejects(f.restore(),/record/);assert.deepEqual(setters(f),[]);assert(f.data);
  }
});
test('wrong target proof, changed source and unhealthy or non-direct sources fail before mutations',async()=>{
  const wrong=fixture({wrongKey:true});await assert.rejects(wrong.plan(),/complete public key/);assert.deepEqual(setters(wrong),[]);
  for(const mutate of [f=>{f.link.sourceKey='cc'.repeat(32);},f=>{f.link.knownRepeater='cc'.repeat(32);},
    f=>{f.link.running=false;},f=>{f.link.failure=Error('USB gone');},f=>{f.link.serving=true;},
    f=>{f.link.seeder={};},f=>{f.link.folderUnknown=true;},f=>{f.link.relays=['dd'.repeat(32)];},f=>{f.link.relays='invalid';}]){
    const f=fixture();await paused(f);const before=setters(f).length;mutate(f);
    await assert.rejects(f.restore(),/record|healthy|USB/);assert.equal(setters(f).length,before);assert(f.data);
  }
});
test('failed durable storage and a no-op store stop before the pause setter',async()=>{
  const f=fixture({storageFail:true}),plan=await f.plan();await assert.rejects(f.pause(plan),/Storage failed/);assert.deepEqual(setters(f),[]);
  const g=fixture(),other=await g.plan();g.store.write=()=>{};
  await assert.rejects(g.pause(other),/could not be saved/);assert.deepEqual(setters(g),[]);
});
test('an identity change during an awaited read cannot authorize a subsequent setter',async()=>{
  const f=fixture(),plan=await f.plan(),remote=f.link.remoteCommand;
  f.link.remoteCommand=async(...args)=>{const result=await remote(...args);if(args[2]==='get radio.flood')f.link.sourceKey='cc'.repeat(32);return result;};
  await assert.rejects(f.pause(plan),/healthy/);assert.deepEqual(setters(f),[]);assert.equal(f.data,null);
});
test('concurrent pause and restore requests are serialized and issue one setter each',async()=>{
  const f=fixture(),plan=await f.plan();
  assert.deepEqual((await Promise.all([f.pause(plan),f.pause(plan)])).map(result=>result.state),['paused','paused']);
  assert.deepEqual((await Promise.all([f.restore(),f.restore()])).map(result=>result.state),['restored','none']);
  assert.deepEqual(setters(f),['set radio.flood off','set radio.flood on']);
});
test('pause cancellation checks must be functions and false or thrown checks stop before I/O',async()=>{
  for(const canContinue of [false,true,'yes']){
    const f=fixture();
    await assert.rejects(quiet.plan(f.link,TARGET,'secret',{...f.settings,canContinue}),/cancellation check is invalid/);
    assert.deepEqual(f.calls,[]);
  }
  for(const canContinue of [()=>false,()=>{throw Error('Stopped');}]){
    const f=fixture();
    await assert.rejects(quiet.plan(f.link,TARGET,'secret',{...f.settings,canContinue}),error=>error.otaQuietCancelled===true);
    assert.deepEqual(f.calls,[]);assert.equal(f.data,null);
  }
});
test('cancellation after identity proof or the pre-pause getter prevents Off and recovery writes',async()=>{
  const first=fixture();let aborted=false;
  first.link.verifyPeer=async()=>{aborted=true;};
  await assert.rejects(quiet.plan(first.link,TARGET,'secret',{...first.settings,canContinue:()=>!aborted}),/cancelled/);
  assert.deepEqual(setters(first),[]);assert.equal(first.data,null);assert(!first.calls.includes('get public.key'));
  const f=fixture(),plan=await f.plan(),remote=f.link.remoteCommand;aborted=false;
  f.link.remoteCommand=async(...args)=>{const value=await remote(...args);if(args[2]==='get radio.flood')aborted=true;return value;};
  await assert.rejects(f.pause(plan,{canContinue:()=>!aborted}),/cancelled/);
  assert.deepEqual(setters(f),[]);assert.equal(f.data,null);
});
test('cancellation after saving recovery prevents Off but retains a recoverable record',async()=>{
  const f=fixture(),plan=await f.plan(),write=f.store.write;let aborted=false;
  f.store.write=value=>{write(value);aborted=true;};
  await assert.rejects(f.pause(plan,{canContinue:()=>!aborted}),/cancelled/);
  assert.deepEqual(setters(f),[]);assert.equal(f.data.state,'pause-pending');
  // Cleanup remains allowed after Stop and needs no setter because forwarding never changed.
  assert.equal((await f.restore({canContinue:()=>false})).state,'restored');assert.equal(f.data,null);
});
test('cancellation after Off confirmation retains recovery and never enables forwarding outside restore',async()=>{
  const f=fixture(),plan=await f.plan(),remote=f.link.remoteCommand;let aborted=false,offSent=false;
  f.link.remoteCommand=async(...args)=>{
    const value=await remote(...args);
    if(args[2]==='set radio.flood off')offSent=true;
    if(offSent&&args[2]==='get radio.flood')aborted=true;
    return value;
  };
  const result=await f.pause(plan,{canContinue:()=>!aborted});
  assert.equal(result.state,'pending');assert.equal(result.error.otaQuietCancelled,true);
  assert.equal(f.data.state,'pause-pending');assert.equal(f.forwarding,'off');
  assert.deepEqual(setters(f),['set radio.flood off']);
  assert.equal((await f.restore({canContinue:()=>false})).state,'restored');
  assert.deepEqual(setters(f),['set radio.flood off','set radio.flood on']);assert.equal(f.data,null);
});
test('exhausted transport read retries are not multiplied by forwarding confirmation polls',async()=>{
  for(const fields of [{otaAttempt:11,otaMaxAttempts:11},{attempts:7},{otaBudgetExhausted:true}]){
    const f=fixture(),plan=await f.plan(),remote=f.link.remoteCommand;let sent=false,reads=0;
    f.link.remoteCommand=async(...args)=>{
      if(sent&&args[2]==='get radio.flood'){reads++;throw Object.assign(Error('Remote command timed out'),fields);}
      const value=await remote(...args);if(args[2]==='set radio.flood off')sent=true;return value;
    };
    assert.equal((await f.pause(plan)).state,'pending');assert.equal(reads,1);assert.equal(f.data.state,'pause-pending');
  }
  const f=fixture({offNoApply:true}),plan=await f.plan(),before=f.calls.filter(command=>command==='get radio.flood').length;
  assert.equal((await f.pause(plan)).state,'pending');
  assert.equal(f.calls.filter(command=>command==='get radio.flood').length-before,4,'one pre-pause GET and three successful old-state polls');
});

test('guarded optional GETs and single setters have five-second budgets without implicit reauthentication',async()=>{
  const f=fixture(),remote=f.link.remoteCommand,options=[];
  f.link.browserRawSource=true;f.link.otaSupported=true;
  f.link.remoteCommand=async(...args)=>{options.push([args[2],args[3]]);return remote(...args);};
  await paused(f);await f.restore();
  for(const [command,budget] of options)
    assert.deepEqual(budget,command.startsWith('get ')?{maxDurationMs:5000,allowReacquire:false}:
      {optionalReplyTimeoutMs:5000,allowReacquire:false},command);
  assert.deepEqual(setters(f),['set radio.flood off','set radio.flood on']);
});

test('automatic proof uses only existing authenticated reads while explicit restoration may reacquire',async()=>{
  const f=fixture();f.link.browserRawSource=true;f.link.otaSupported=true;
  const plan=await quiet.plan(f.link,TARGET,'secret',{...f.settings,allowReacquire:false});
  await f.pause(plan,{allowReacquire:false});
  await f.restore({allowReacquire:false});
  assert(!f.calls.includes('verify'));assert(f.calls.includes('get public.key'));
  const g=fixture();await paused(g);const before=g.calls.filter(c=>c==='verify').length;
  await g.restore({allowReacquire:true});
  assert.equal(g.calls.filter(c=>c==='verify').length,before+1);
});

test('USB failures and changed identity retain their halting classification rather than optional-warning errors',async()=>{
  const f=fixture(),usb=Object.assign(Error('USB acknowledgement timed out'),{otaUsbTimeout:true});
  f.link.failure=usb;
  await assert.rejects(f.plan(),cause=>cause===usb);
  const g=fixture({wrongKey:true});
  await assert.rejects(g.plan(),cause=>cause.otaQuietIdentityChanged===true);
  assert.deepEqual(setters(f),[]);assert.deepEqual(setters(g),[]);
});
