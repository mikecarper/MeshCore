'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
if (!globalThis.crypto) globalThis.crypto = require('node:crypto').webcrypto;
const {CompanionTransport} = require('../docs/_javascript/lora_ota_companion.js');
const enc = new TextEncoder(), key = 'bb'.repeat(32), source = 'aa'.repeat(32);
const start = count => Uint8Array.of(2, count & 255, count >> 8, 0, 0), end = Uint8Array.of(4, 1, 0, 0, 0);
function contact(k, name = 'Node') {
  const f = new Uint8Array(148); f[0] = 3; f.set(Buffer.from(k, 'hex'), 1);
  f[33] = 2; f[35] = 0; f.set(enc.encode(name), 100); return f;
}
function transport(respond, options = {}) {
  const t = new CompanionTransport({}, {commandTimeout: 100, contactsTimeout: 100, ...options});
  const sent = []; t.running = true;
  t.writer = {write: async bytes => { const frame = bytes.slice(3); sent.push(frame); await respond(t, frame); }};
  return {t, sent};
}
test('315-contact live stream restarts discard partial snapshots and retain fresh identity', async () => {
  const f = transport(t => {
    t.onFrame(start(320)); t.onFrame(contact(key.slice(0, 12) + 'ff'.repeat(26), 'Discarded collision'));
    t.onFrame(start(318)); t.onFrame(contact('cc'.repeat(32), 'Discarded node'));
    t.onFrame(start(315)); t.onFrame(contact(key, 'Selected target'));
    for (let i = 0; i < 314; i++) t.onFrame(contact(i.toString(16).padStart(64, '0')));
    t.onFrame(end);
  });
  const rows = await f.t.contacts(); assert.equal(rows.length, 315);
  assert.ok(!rows.some(c => c.name.startsWith('Discarded')));
  f.t.selectTarget(key); f.t.assertStandardTarget(); assert.equal(f.t.failure, null);
  assert.deepEqual(f.sent.map(f => f[0]), [4]);
});
test('malformed contact stream poisons the connection before another command can consume leftovers', async () => {
  for (const frames of [[Uint8Array.of(2)], [contact(key)], [start(1), contact(key), contact(key)], [start(0), Uint8Array.of(4)], [start(4097)]]) {
    const f = transport(t => { for (const frame of frames) t.onFrame(frame); });
    await assert.rejects(f.t.contacts(), /reconnect the USB source/);
    await assert.rejects(f.t.command(Uint8Array.of(10), 10), /reconnect the USB source/);
    assert.equal(f.sent.length, 1); assert.deepEqual(f.t.contactList, []);
  }
});
test('repeated contact restarts keep the original bounded deadline', async () => {
  let interval;
  const f = transport(t => { interval = setInterval(() => t.onFrame(start(315)), 5); }, {contactsTimeout: 35});
  try { await assert.rejects(f.t.contacts(), /Contact list timed out/); }
  finally { clearInterval(interval); }
  assert.equal(f.sent.length, 1); assert.ok(f.t.failure);
});

test('USB timeouts identify the stalled operation without disclosing payloads or retrying it', async () => {
  const secret = 'private-password-and-message';
  const cases = [
    [Uint8Array.of(1), 'reading the Companion identity'],
    [Uint8Array.of(22, 4), 'reading device information'],
    [Uint8Array.of(5), 'reading the Companion clock'],
    [Uint8Array.of(10), 'reading a queued message'],
    [Uint8Array.of(65, 1, 0x32, 0, ...enc.encode(secret)), 'sending a raw OTA packet'],
    [Uint8Array.of(26, ...enc.encode(secret)), 'sending an admin login'],
    [Uint8Array.of(2, ...enc.encode(secret)), 'sending an admin command'],
    [Uint8Array.of(52, ...enc.encode(secret)), 'setting up the OTA session'],
    [Uint8Array.of(66, ...enc.encode('get tempradio')), 'reading local get tempradio'],
    [Uint8Array.of(66, ...enc.encode('set password ' + secret)), 'running a local CLI command'],
    [Uint8Array.of(66, ...enc.encode('get radio ' + secret)), 'running a local CLI command'],
    [Uint8Array.of(255, ...enc.encode(secret)), 'waiting for a USB command reply'],
  ];
  for (const [payload, operation] of cases) {
    const f = transport(() => {}, {commandTimeout: 5});
    let failure;
    await assert.rejects(f.t.command(payload, 0), error => {
      failure = error;
      assert.equal(error.message, 'Companion response timed out while ' + operation + '; reconnect before retrying');
      assert.equal(error.otaUsbTimeout, true);
      assert.equal(error.loraReplyTimeout, undefined);
      assert.ok(!error.message.includes(secret));
      return true;
    });
    // A late frame cannot unpoison the USB stream or permit another write.
    f.t.onFrame(Uint8Array.of(0));
    await assert.rejects(f.t.command(Uint8Array.of(5), 9), error => error === failure);
    assert.equal(f.t.failure, failure);
    assert.equal(f.sent.length, 1);
  }
});

function guardOwnerFixture(port={},hardware={peer:null,frames:[]},options={}){
  const t=new CompanionTransport(port,{commandTimeout:8,guardRecoveryTimeoutMs:500,...options});
  Object.assign(t,{sourceKey:source,knownRepeater:key,running:true,localCliSupported:true,otaSupported:true});
  t.writer={write:async bytes=>{
    const frame=bytes.slice(3);hardware.frames.push(frame);
    if(frame[0]===66){
      assert.equal(new TextDecoder().decode(frame.slice(1)),'get ota.direct');
      const status='> ota.direct v=2 active='+(hardware.peer?1:0)+' busy='+(hardware.busy?1:0);
      t.onFrame(Uint8Array.of(29,...enc.encode(status)));return;
    }
    assert.equal(frame[0],52);const target=Buffer.from(frame.slice(2,34)).toString('hex');
    if(frame[1]===1){
      hardware.peer=target;
      if(hardware.loseArm)return;
    }else{
      assert.equal(frame[1],3);
      if(hardware.peer&&hardware.peer!==target){t.onFrame(Uint8Array.of(1,2));return;}
      if(!hardware.closeNoApply)hardware.peer=null;
      if(hardware.loseClose)return;
    }
    t.onFrame(Uint8Array.of(0));
  }};
  return {t,port,hardware,status:()=>'> ota.direct v=2 active='+(hardware.peer?1:0)+' busy='+(hardware.busy?1:0),
    frames:()=>hardware.frames.filter(frame=>frame[0]===52),
    reconnect(){const next=guardOwnerFixture(port,hardware,options);next.t.knownRepeater='';return next;}};
}
test('same-page exact-port recovery clears only its owned old full target after a lost arm acknowledgement',async()=>{
  const f=guardOwnerFixture();f.hardware.loseArm=true;
  await assert.rejects(f.t.guardMode(1,Uint8Array.of(2)),error=>error.otaUsbTimeout===true);
  assert.equal(f.hardware.peer,key);
  const next=f.reconnect();assert.equal(next.t.knownRepeater,'');
  assert.equal(await next.t.recoverOwnedDirectGuard(next.status()),'> ota.direct v=2 active=0 busy=0');
  assert.equal(next.t.knownRepeater,'');assert.equal(next.t.otaRadioProfile,0);
  assert.deepEqual(f.frames().map(frame=>frame[1]),[1,3]);
  assert.equal(Buffer.from(f.frames()[1].slice(2)).toString('hex'),key);
  assert(f.hardware.frames.every(frame=>[52,66].includes(frame[0])));
});
test('unknown owners and another SerialPort object are never reset even when vendor and keys match',async()=>{
  const unknown=guardOwnerFixture();unknown.hardware.peer=key;
  assert.equal(await unknown.t.recoverOwnedDirectGuard(unknown.status()),unknown.status());assert.deepEqual(unknown.frames(),[]);
  const f=guardOwnerFixture();await f.t.guardMode(1,Uint8Array.of(2));
  const other=guardOwnerFixture({},f.hardware);
  assert.equal(await other.t.recoverOwnedDirectGuard(other.status()),other.status());assert.deepEqual(f.frames().map(frame=>frame[1]),[1]);
  const late=f.reconnect();await late.t.recoverOwnedDirectGuard(late.status());
  assert.deepEqual(f.frames().map(frame=>frame[1]),[1,3]);
});
test('a different source identity retires stale authority without resetting any guard',async()=>{
  const f=guardOwnerFixture();await f.t.guardMode(1,Uint8Array.of(2));
  const next=f.reconnect();next.t.sourceKey='cc'.repeat(32);
  assert.equal(await next.t.recoverOwnedDirectGuard(next.status()),next.status());
  const old=f.reconnect();await old.t.recoverOwnedDirectGuard(old.status());
  assert.deepEqual(f.frames().map(frame=>frame[1]),[1]);assert.equal(f.hardware.peer,key);
});
test('a host-reset idle guard retires ownership without close or selector writes',async()=>{
  const f=guardOwnerFixture();await f.t.guardMode(1,Uint8Array.of(2));f.hardware.peer=null;
  const next=f.reconnect();await next.t.recoverOwnedDirectGuard(next.status());
  f.hardware.peer='cc'.repeat(32);
  const later=f.reconnect();await later.t.recoverOwnedDirectGuard(later.status());
  assert.deepEqual(f.frames().map(frame=>frame[1]),[1]);assert.equal(f.hardware.peer,'cc'.repeat(32));
});
test('exact-key recovery cannot close a different active peer and rejection is never automatically repeated',async()=>{
  const f=guardOwnerFixture();await f.t.guardMode(1,Uint8Array.of(2));f.hardware.peer='cc'.repeat(32);
  const next=f.reconnect();await assert.rejects(next.t.recoverOwnedDirectGuard(next.status()),/rejected/);
  assert.equal(f.hardware.peer,'cc'.repeat(32));assert.equal(Buffer.from(f.frames()[1].slice(2)).toString('hex'),key);
  const later=f.reconnect();await assert.rejects(later.t.recoverOwnedDirectGuard(later.status()),/close is unconfirmed/);
  assert.deepEqual(f.frames().map(frame=>frame[1]),[1,3]);assert.equal(f.hardware.peer,'cc'.repeat(32));
});
test('a lost cleanup acknowledgement permits only idle reads on reconnect, never a second close',async()=>{
  for(const closeNoApply of [false,true]){
    const f=guardOwnerFixture();await f.t.guardMode(1,Uint8Array.of(2));
    Object.assign(f.hardware,{loseClose:true,closeNoApply});
    const next=f.reconnect();await assert.rejects(next.t.recoverOwnedDirectGuard(next.status()),error=>error.otaUsbTimeout===true);
    const later=f.reconnect();
    if(closeNoApply)await assert.rejects(later.t.recoverOwnedDirectGuard(later.status()),/close is unconfirmed/);
    else assert.equal(await later.t.recoverOwnedDirectGuard(later.status()),'> ota.direct v=2 active=0 busy=0');
    assert.deepEqual(f.frames().map(frame=>frame[1]),[1,3]);
    f.hardware.peer=null;await later.t.recoverOwnedDirectGuard(later.status());
  }
});
test('normal acknowledged close forgets ownership and an already-active foreign guard cannot be journaled by arm',async()=>{
  const f=guardOwnerFixture();await f.t.guardMode(1,Uint8Array.of(2));await f.t.guardMode(3);
  f.hardware.peer=key;const next=f.reconnect();await next.t.recoverOwnedDirectGuard(next.status());
  assert.deepEqual(f.frames().map(frame=>frame[1]),[1,3]);
  const foreign=guardOwnerFixture();foreign.hardware.peer=key;
  await assert.rejects(foreign.t.guardMode(1,Uint8Array.of(2)),/mailbox is already in use/);
  const later=foreign.reconnect();await later.t.recoverOwnedDirectGuard(later.status());assert.deepEqual(foreign.frames(),[]);
});
test('login waits through transient local radio work without closing a mailbox or retrying RF',async()=>{
  const t=new CompanionTransport({}, {guardIdleTimeoutMs:1000});
  Object.assign(t,{knownRepeater:key,otaSupported:true});
  const events=[];let idleReads=0,guard=false;
  t.localCommand=async(command,options)=>{
    events.push(['local',command,options?.timeoutMs]);
    if(guard)return '> ota.direct v=2 active=1 busy=0';
    return '> ota.direct v=2 active=0 busy='+(++idleReads<3?1:0);
  };
  t.checkClock=async()=>{};
  t.guardMode=async mode=>{events.push(['guard',mode]);if(mode===1)guard=true;};
  t.remoteReply=async command=>{events.push(['remote',command]);return key;};
  await t.login('');assert.equal(t.authenticated,true);assert.equal(idleReads,3);
  assert.deepEqual(events.filter(row=>row[0]==='guard').map(row=>row[1]),[1,2]);
  assert.deepEqual(events.filter(row=>row[0]==='remote'),[['remote','get public.key']]);
  assert(events.slice(0,3).every(row=>row[0]==='local'&&row[2]>0&&row[2]<=1000));
});
test('foreign active guard is rejected without a close and persistent radio busy has a bounded USB-only error',async()=>{
  for(const active of [true,false]){
    const t=new CompanionTransport({}, {guardIdleTimeoutMs:15});
    Object.assign(t,{knownRepeater:key,otaSupported:true});const events=[],options=t.options;
    t.localCommand=async command=>{events.push(command);return '> ota.direct v=2 active='+(active?1:0)+' busy=1';};
    t.guardMode=async()=>{throw Error('must not write a guard');};
    t.remoteReply=async()=>{throw Error('must not transmit RF');};
    await assert.rejects(t.login(''),error=>{
      assert.match(error.message,active?/mailbox is already in use/:/radio is still busy/);
      assert.equal(error.otaCommand,'get ota.direct');assert.equal(error.otaEndpoint,'USB Companion');
      assert.equal(error.loraReplyTimeout,undefined);assert.equal(error.otaUsbTimeout,undefined);return true;
    });assert.equal(t.options,options);assert.equal(t.authenticated,false);assert.equal(t.failure,null);
    assert(events.every(command=>command==='get ota.direct'));
  }
});
test('an outer guarded read budget caps the initial local idle wait without any radio write',async()=>{
  const t=new CompanionTransport({}, {knownRepeater:key,guardIdleTimeoutMs:5000});
  Object.assign(t,{otaSupported:true,running:true});const timeouts=[],options=t.options;
  t.localCommand=async(command,limits)=>{assert.equal(command,'get ota.direct');timeouts.push(limits.timeoutMs);return '> ota.direct v=2 active=0 busy=1';};
  t.guardMode=async()=>{throw Error('must not arm after the read deadline');};
  await assert.rejects(t.remoteCommand(key,'','ota self',{maxDurationMs:12}),error=>
    error.otaEndpoint==='USB Companion'&&!error.loraReplyTimeout&&/busy|deadline/.test(error.message));
  assert(timeouts.length>=1);assert(timeouts.every(timeout=>timeout>0&&timeout<=12));assert.equal(t.options,options);
});
test('guard cleanup local status requests use the remaining deadline rather than the ordinary USB timeout',async()=>{
  const f=transport(()=>{}, {commandTimeout:1000});f.t.localCliSupported=true;
  await assert.rejects(f.t.waitForDirectIdle(null,{maxDurationMs:15}),error=>{
    assert.equal(error.otaUsbTimeout,true);assert.equal(error.otaEndpoint,'USB Companion');
    assert.equal(error.otaCommand,'get ota.direct');assert(error.otaTimeoutMs>0&&error.otaTimeoutMs<=15);return true;
  });assert.equal(f.sent.length,1);assert.equal(f.sent[0][0],66);
});

test('slow inbox reply uses one dequeue and preserves unrelated messages; timeout leaves a reservation', async () => {
  const savedNavigator = globalThis.navigator, savedStorage = globalThis.localStorage;
  let store = new Map();
  Object.defineProperty(globalThis, 'navigator', {configurable: true, value: {locks: {request: async (_k, _o, fn) => fn({})}}});
  globalThis.localStorage = {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k)};
  try {
    for (const lost of [false, true]) {
      store = new Map(); let tag, dequeues = 0;
      const f = transport((t, frame) => {
        if (frame[0] === 2) { tag = new TextDecoder().decode(frame.slice(13)).slice(0, 3); t.onFrame(Uint8Array.of(6)); return; }
        assert.equal(frame[0], 10); dequeues++;
        if (lost) return;
        const message = dequeues === 1
          ? Uint8Array.of(17, 0, 0, 0, 1, 255, 0, 0, 0, 0, 0, ...enc.encode('Keep this message'))
          : Uint8Array.of(16, 0, 0, 0, ...Buffer.from(key, 'hex').slice(0, 6), 255, 1, 0, 0, 0, 0, ...enc.encode(tag + 'OK'));
        setTimeout(() => t.onFrame(message), 35); // Later than the ordinary command deadline.
      }, {commandTimeout: 15, inboxTimeout: 100, inboxPollDelay: 0, investigationTimeout: 1000});
      Object.assign(f.t, {sourceKey: source, knownRepeater: key, investigationEnabled: true,
        investigationAuthenticated: true, investigationVerified: true, contactList: [{publicKey: key}]});
      f.t.contacts = async () => f.t.contactList;
      if (lost) {
        await assert.rejects(f.t.standardReply('ota status'), /queued message may have been removed/);
        assert.equal(dequeues, 1); assert.ok(f.t.failure);
        const rows = JSON.parse([...store.values()][0]); assert.equal(rows.length, 1); assert.equal(rows[0].pending, true);
      } else {
        assert.equal(await f.t.standardReply('ota status'), 'OK'); assert.equal(dequeues, 2);
        assert.equal(f.t.failure, null);
        const rows = JSON.parse([...store.values()][0]); assert.equal(rows.length, 1); assert.ok(rows[0].frameHex); assert.ok(!rows[0].pending);
      }
    }
  } finally {
    Object.defineProperty(globalThis, 'navigator', {configurable: true, value: savedNavigator});
    if (savedStorage === undefined) delete globalThis.localStorage; else globalThis.localStorage = savedStorage;
  }
});

async function radioProbeFixture(options = {}) {
  const events=[], t=new CompanionTransport({}, {remoteTimeout:75000,commandTimeout:12345});
  Object.assign(t,{knownRepeater:key,sourceKey:source,otaSupported:true,supportsOtaRadio2:true,
    authenticated:true,guard:true,running:true});
  const originalOptions=t.options;
  t.guardMode=async mode=>{
    events.push(['guard',mode]);if(options.guardFailure)throw options.guardFailure;
    if(options.guard)return options.guard(mode,t);
  };
  t.localCommand=async command=>{
    events.push(['local',command]);
    if(options.localFailure){t.failure=options.localFailure;throw options.localFailure;}
    if(options.local)return options.local(command,t);
    if(command==='get ota.direct')return '> ota.direct v=2 active='+(t.guard?1:0)+' busy=0';
    if(command==='get ota.direct.radio')return '> ota.direct.radio v=1 profile='+(t.otaRadioProfile?'radio2':'radio');
    if(command==='set ota.direct.radio radio2'||command==='set ota.direct.radio radio'){
      t.otaRadioProfile=command.endsWith('radio2')?1:0;return 'OK - ota.direct.radio '+(t.otaRadioProfile?'radio2':'radio');
    }
    throw Error('Unexpected local '+command);
  };
  t.remoteReply=async command=>{
    events.push(['remote',command,t.options.remoteTimeout]);
    if(options.remote)return options.remote(command,t);
    return command==='get public.key'?'> '+key:command==='get tempradio2'?'> 909.950,250,7,5,rxtx,128':'> rxtx';
  };
  t.login=async()=>{throw Error('Probe must reuse the admitted admin session');};
  await t.verifyPeer(key,'password');events.length=0;
  return {t,events,originalOptions};
}
const probeSettings={radio:[909.95,250,7,5],preamble:128};

test('a guarded radio probe reuses admission but proves the full target on each candidate channel', async()=>{
  const f=await radioProbeFixture();
  const result=await f.t.probePeerRadio(key,'password',1,{...probeSettings,readback:async read=>({
    temporary:await read('get tempradio2'),live:await read('get radio2.status')})});
  assert.deepEqual(result,{reachable:true,reply:{temporary:'> 909.950,250,7,5,rxtx,128',live:'> rxtx'}});
  assert.deepEqual(f.events.map(row=>row.slice(0,2)),[
    ['guard',3],['local','get ota.direct'],['local','set ota.direct.radio radio2'],['local','get ota.direct.radio'],
    ['guard',1],['guard',2],['local','get ota.direct'],['remote','get public.key'],['remote','get tempradio2'],['remote','get radio2.status']]);
  assert.equal(f.t.otaRadioProfile,1);assert.equal(f.t.guard,true);assert.equal(f.t.authenticated,true);
  assert.equal(f.t.options,f.originalOptions);assert.equal(f.t.options.remoteTimeout,75000);
  assert.ok(f.events.filter(row=>row[0]==='remote').every(row=>row[2]===5000));
});

test('probe RF timeouts close the guard, preserve admission and restore ordinary command budgets', async()=>{
  const timeout=Object.assign(Error('Remote command timed out; result is unknown'),{loraReplyTimeout:true});
  const f=await radioProbeFixture({remote:()=>{throw timeout;}});
  const result=await f.t.probePeerRadio(key,'password',1,probeSettings);
  assert.equal(result.reachable,false);assert.equal(result.error,timeout);
  assert.equal(timeout.otaStep,'Verifying target identity');assert.equal(timeout.otaCommand,'get public.key');
  assert.equal(timeout.otaEndpoint,'remote target');assert.equal(timeout.otaTimeoutMs,5000);
  assert.equal(timeout.otaPath,'direct (0 hops; enforced by guarded session)');
  assert.match(timeout.otaRadio,/Source: Radio 2 .*reception not confirmed/);
  assert.ok(!JSON.stringify(timeout).includes('password'));
  assert.equal(f.t.guard,false);assert.equal(f.t.authenticated,false);assert.equal(f.t.options,f.originalOptions);
  assert.equal(f.events.filter(row=>row[0]==='remote').length,1);
  assert.deepEqual(f.events.at(-1),['guard',3]);
  assert.equal(f.t.radioProbeAdmission.key,key);
});

test('wrong identities, rejected access and USB failures are fatal probe errors', async()=>{
  const cases=[
    {remote:()=>'> '+'cc'.repeat(32)},
    {remote:()=> 'Error: permission denied'},
    {remote:()=>{throw Error('Target says timeout while erasing');}},
    {localFailure:Error('Companion response timed out; reconnect before retrying')},
  ];
  for(const options of cases){
    const f=await radioProbeFixture(options);
    await assert.rejects(f.t.probePeerRadio(key,'password',1,probeSettings),/full public key|permission denied|Target says timeout|Companion response timed out/);
    assert.equal(f.t.authenticated,false);assert.equal(f.t.options,f.originalOptions);
  }
});

test('probe admission and readback whitelist cannot authorize mutation or legacy use', async()=>{
  for(const mutate of [t=>{t.radioProbeAdmission=null;},t=>{t.radioProbeAdmission.password='different';},
      t=>{t.otaSupported=false;},t=>{t.supportsOtaRadio2=false;},t=>{t.serving=true;}]){
    const f=await radioProbeFixture();mutate(f.t);
    await assert.rejects(f.t.probePeerRadio(key,'password',1,probeSettings),/Verify|guarded Companion/);
    assert.deepEqual(f.events,[]);
  }
  const f=await radioProbeFixture();
  await assert.rejects(f.t.probePeerRadio(key,'password',1,{...probeSettings,readback:'set tx 22'}),/Only temporary-radio readbacks/);
  assert.deepEqual(f.events,[]);
  await assert.rejects(f.t.probePeerRadio(key,'password',1,{...probeSettings,readback:read=>read('ota cancel private-value')}),error=>{
    assert.match(error.message,/Only temporary-radio readbacks/);
    assert.equal(error.otaStep,'Validating radio-probe readback');
    assert.equal(error.otaCommand,'CLI command <arguments hidden>');assert.equal(error.otaEndpoint,'browser');
    assert.ok(!JSON.stringify(error).includes('private-value'));return true;
  });
  assert.deepEqual(f.events.filter(row=>row[0]==='remote').map(row=>row[1]),['get public.key']);
  assert.equal(f.t.guard,false);assert.equal(f.t.options,f.originalOptions);
});

test('probe deadlines scale with airtime and an exhausted outer budget sends no RF', async()=>{
  const f=await radioProbeFixture();
  await f.t.probePeerRadio(key,'password',0,{radio:[910.525,62.5,12,5],preamble:128});
  assert.ok(f.events.filter(row=>row[0]==='remote').every(row=>row[2]>40000&&row[2]<75000));
  f.events.length=0;
  const exhausted=await f.t.probePeerRadio(key,'password',1,{...probeSettings,maxDurationMs:0});
  assert.equal(exhausted.reachable,false);assert.equal(exhausted.error.loraReplyTimeout,true);
  assert.equal(exhausted.error.otaStep,'Checking radio probe deadline');assert.equal(exhausted.error.otaTimeoutMs,0);
  assert.deepEqual(f.events,[['guard',3]]);assert.equal(f.t.options,f.originalOptions);
});

test('the five-second probe allowance does not delay fast replies or extend the outer deadline', async()=>{
  const savedNow=Date.now;let clock=10000;
  Date.now=()=>clock;
  try{
    const f=await radioProbeFixture({remote:(command,t)=>{
      if(command==='get public.key'){
        assert.equal(t.options.remoteTimeout,5000);clock+=1500;return '> '+key;
      }
      assert.equal(command,'get tempradio');assert.equal(t.options.remoteTimeout,4500);return '> off';
    }});
    assert.deepEqual(await f.t.probePeerRadio(key,'password',0,{...probeSettings,maxDurationMs:6000}),
      {reachable:true,reply:'> off'});
    assert.equal(clock,11500);assert.equal(f.t.options,f.originalOptions);
    assert.deepEqual(f.events.filter(row=>row[0]==='remote').map(row=>row[2]),[5000,4500]);
  }finally{Date.now=savedNow;}
});

test('a late readback cannot establish reachability beyond the absolute probe budget', async()=>{
  const savedNow=Date.now;let clock=10000;
  Date.now=()=>clock;
  try{
    const f=await radioProbeFixture({remote:command=>{
      clock+=command==='get public.key'?3500:3000;
      return command==='get public.key'?'> '+key:'> off';
    }});
    const result=await f.t.probePeerRadio(key,'password',0,{...probeSettings,maxDurationMs:6000});
    assert.equal(result.reachable,false);assert.equal(result.error.loraReplyTimeout,true);
    assert.equal(result.error.otaCommand,'get tempradio');assert.equal(result.error.otaEndpoint,'remote target');
    assert.equal(result.error.otaStep,'Waiting for remote command reply');assert.equal(result.error.otaTimeoutMs,2500);
    assert.match(result.error.otaRadio,/Source: primary radio .*reception not confirmed/);
    assert.equal(f.events.filter(row=>row[0]==='remote').length,2);
    assert.equal(f.t.guard,false);assert.equal(f.t.authenticated,false);assert.equal(f.t.options,f.originalOptions);
  }finally{Date.now=savedNow;}
});

test('source-idle timeouts keep local operation context and cannot be retried as target RF failures', async()=>{
  const savedNow=Date.now;let clock=10000;
  Date.now=()=>clock;
  try{
    const f=await radioProbeFixture({local:(command,t)=>{
      assert.equal(command,'get ota.direct');clock+=5000;
      return '> ota.direct v=2 active='+(t.guard?1:0)+' busy=1';
    }});
    await assert.rejects(f.t.probePeerRadio(key,'password',0,probeSettings),error=>{
      assert.equal(error.loraReplyTimeout,true);assert.equal(error.otaEndpoint,'USB Companion');
      assert.equal(error.otaCommand,'get ota.direct');assert.equal(error.otaStep,'Waiting for source radio to become idle');
      assert.equal(error.otaTimeoutMs,5000);assert.match(error.otaRadio,/Source: primary radio/);return true;
    });
    assert.equal(f.events.filter(row=>row[0]==='remote').length,0);
    assert.equal(f.t.guard,false);assert.equal(f.t.authenticated,false);assert.equal(f.t.options,f.originalOptions);
  }finally{Date.now=savedNow;}
});

test('invalid or backwards probe clocks are fatal contextual failures without additional RF requests', async()=>{
  const savedNow=Date.now;
  try{
    const invalid=await radioProbeFixture();Date.now=()=>NaN;
    await assert.rejects(invalid.t.probePeerRadio(key,'password',0,probeSettings),error=>{
      assert.equal(error.message,'Invalid radio probe clock');assert.equal(error.otaEndpoint,'browser');
      assert.equal(error.otaStep,'Checking radio probe deadline');assert.match(error.otaRadio,/Source: primary radio/);return true;
    });
    assert.deepEqual(invalid.events,[]);assert.equal(invalid.t.options,invalid.originalOptions);
    let clock=10000;Date.now=()=>clock;
    const backwards=await radioProbeFixture({remote:()=>{clock--;return '> '+key;}});
    await assert.rejects(backwards.t.probePeerRadio(key,'password',1,probeSettings),error=>{
      assert.equal(error.message,'Invalid radio probe clock');assert.equal(error.otaCommand,'get public.key');
      assert.equal(error.otaStep,'Verifying target identity');assert.equal(error.otaEndpoint,'remote target');return true;
    });
    assert.equal(backwards.events.filter(row=>row[0]==='remote').length,1);
    assert.equal(backwards.t.guard,false);assert.equal(backwards.t.authenticated,false);
    assert.equal(backwards.t.options,backwards.originalOptions);
  }finally{Date.now=savedNow;}
});

test('probe cleanup errors stay fatal while retaining the interrupted request context', async()=>{
  const timeout=Object.assign(Error('Remote command timed out; result is unknown'),{loraReplyTimeout:true});
  const cleanup=Error('USB guard close failed');let closes=0;
  const f=await radioProbeFixture({remote:()=>{throw timeout;},guard:mode=>{
    if(mode===3&&++closes===2)throw cleanup;
  }});
  await assert.rejects(f.t.probePeerRadio(key,'password',1,probeSettings),error=>{
    assert.equal(error,cleanup);assert.equal(error.otaStep,'Closing guarded radio probe');
    assert.equal(error.otaEndpoint,'USB Companion');assert.equal(error.otaRequestCommand,'get public.key');
    assert.equal(error.otaRequestStep,'Verifying target identity');assert.match(error.otaRequestRadio,/Source: Radio 2/);return true;
  });
  assert.equal(timeout.otaCommand,'get public.key');assert.equal(timeout.otaEndpoint,'remote target');
  assert.equal(f.events.filter(row=>row[0]==='remote').length,1);assert.equal(f.t.options,f.originalOptions);
});

test('a bounded radio setter requires proven primary access and sends exactly one request', async()=>{
  const command='tempradio 909.950,250,7,5,2,128';
  const f=await radioProbeFixture({remote:()=> 'OK - temporary channel applying'});
  assert.equal(await f.t.requestRemoteRadio(key,'password',command,probeSettings),'OK - temporary channel applying');
  assert.deepEqual(f.events,[['remote',command,5000]]);assert.equal(f.t.options,f.originalOptions);
  for(const change of [t=>{t.guard=false;},t=>{t.authenticated=false;},t=>{t.otaRadioProfile=1;},
      t=>{t.radioProbeAdmission=null;},t=>{t.radioProbeAdmission.password='different';}]){
    const g=await radioProbeFixture();change(g.t);
    await assert.rejects(g.t.requestRemoteRadio(key,'password',command,probeSettings),/Prove the target/);
    assert.deepEqual(g.events,[]);
  }
  for(const text of ['set tx 22','ota cancel','tempradio 909.950,250,7,5,2,128;reboot']){
    const g=await radioProbeFixture();
    await assert.rejects(g.t.requestRemoteRadio(key,'password',text,probeSettings),/Only a guarded/);
    assert.deepEqual(g.events,[]);
  }
});

test('a bounded setter never retries a lost reply or changes USB timeout handling', async()=>{
  const command='set tempradio2 909.950,250,7,5,rxtx,120,128';
  const timeout=Object.assign(Error('Remote command timed out; result is unknown'),{loraReplyTimeout:true});
  const f=await radioProbeFixture({remote:()=>{throw timeout;}});
  await assert.rejects(f.t.requestRemoteRadio(key,'password',command,probeSettings),error=>error===timeout);
  assert.deepEqual(f.events,[['remote',command,5000]]);
  assert.equal(f.t.options,f.originalOptions);assert.equal(f.t.options.commandTimeout,12345);
  f.events.length=0;
  await assert.rejects(f.t.requestRemoteRadio(key,'password',command,{...probeSettings,maxDurationMs:500}),error=>error.loraReplyTimeout===true);
  assert.deepEqual(f.events,[]);
});

test('login and return-path timeouts are classified as RF timeouts', async()=>{
  for(const password of ['password','']){
    const t=new CompanionTransport({}, {remoteTimeout:5});
    Object.assign(t,{knownRepeater:key,otaSupported:true});
    t.checkClock=async()=>{};t.guardMode=async()=>{};t.command=async()=>Uint8Array.of(6);
    let reads=0;
    t.localCommand=async()=>reads++===0?'> ota.direct v=2 active=0 busy=0':'> ota.direct v=2 active=1 busy=1';
    await assert.rejects(t.login(password),error=>error.loraReplyTimeout===true&&/login timed out|Return path timed out/.test(error.message));
  }
});

test('a small raw ACK cannot establish the full OTA frame capacity', async()=>{
  const f=transport(t=>t.onFrame(Uint8Array.of(0)));
  Object.assign(f.t,{serving:true,otaSupported:true,radio:[909.95,250,7,5],preamble:128});
  await f.t.sendRadioOrdered(Uint8Array.of(0x32,0,1));
  assert.equal(f.t.rawPacketSupport,'accepted');
  assert.throws(()=>f.t.requireOtaPacketCapacity(),error=>error.sourcePacketCapacity===true);
  const raw=new Uint8Array(186);raw[0]=0x32;raw[2]=7;
  f.t.nextTx=0;
  await assert.rejects(f.t.sendRadioOrdered(raw),error=>error.sourcePacketCapacity===true);
  assert.equal(f.sent.length,1);assert.equal(f.sent[0].length,5);
  f.t.usbRawFrameMax=188;
  f.t.requireOtaPacketCapacity();await f.t.sendRadioOrdered(raw);
  assert.equal(f.sent.length,2);assert.equal(f.sent[1].length,188);
  assert.deepEqual(f.sent[1].slice(0,2),Uint8Array.of(65,1));
});
