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
  t.guardMode=async mode=>{events.push(['guard',mode]);if(options.guardFailure)throw options.guardFailure;};
  t.localCommand=async command=>{
    events.push(['local',command]);
    if(options.localFailure){t.failure=options.localFailure;throw options.localFailure;}
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
  assert.ok(f.events.filter(row=>row[0]==='remote').every(row=>row[2]===1000));
});

test('probe RF timeouts close the guard, preserve admission and restore ordinary command budgets', async()=>{
  const timeout=Object.assign(Error('Remote command timed out; result is unknown'),{loraReplyTimeout:true});
  const f=await radioProbeFixture({remote:()=>{throw timeout;}});
  assert.deepEqual(await f.t.probePeerRadio(key,'password',1,probeSettings),{reachable:false});
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
  await assert.rejects(f.t.probePeerRadio(key,'password',1,{...probeSettings,readback:read=>read('ota cancel')}),/Only temporary-radio readbacks/);
  assert.deepEqual(f.events.filter(row=>row[0]==='remote').map(row=>row[1]),['get public.key']);
  assert.equal(f.t.guard,false);assert.equal(f.t.options,f.originalOptions);
});

test('probe deadlines scale with airtime and an exhausted outer budget sends no RF', async()=>{
  const f=await radioProbeFixture();
  await f.t.probePeerRadio(key,'password',0,{radio:[910.525,62.5,12,5],preamble:128});
  assert.ok(f.events.filter(row=>row[0]==='remote').every(row=>row[2]>40000&&row[2]<75000));
  f.events.length=0;
  assert.deepEqual(await f.t.probePeerRadio(key,'password',1,{...probeSettings,maxDurationMs:0}),{reachable:false});
  assert.deepEqual(f.events,[['guard',3]]);assert.equal(f.t.options,f.originalOptions);
});

test('a bounded radio setter requires proven primary access and sends exactly one request', async()=>{
  const command='tempradio 909.950,250,7,5,2,128';
  const f=await radioProbeFixture({remote:()=> 'OK - temporary channel applying'});
  assert.equal(await f.t.requestRemoteRadio(key,'password',command,probeSettings),'OK - temporary channel applying');
  assert.deepEqual(f.events,[['remote',command,2500]]);assert.equal(f.t.options,f.originalOptions);
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
  assert.deepEqual(f.events,[['remote',command,2500]]);
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
