'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto').webcrypto;
const pkg = require('../docs/_javascript/lora_ota_package.js');
const delta = require('../docs/_javascript/lora_ota_delta.js');
const {RoutedSession, routedPolicy} = require('../docs/_javascript/lora_ota_routed_session.js');

const first='bb'.repeat(32), second='cc'.repeat(32), target='dd'.repeat(32), source='aa'.repeat(32);
async function firmware() {
  const bytes = new Uint8Array(100), v = new DataView(bytes.buffer), off = bytes.length - 56;
  bytes.set(Buffer.from('EndF'),off); v.setUint32(off+4,off,true);
  bytes.set(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes.slice(0,off))).slice(0,8),off+8);
  v.setUint32(off+16,0x01120001,true); v.setUint32(off+20,0x12345678,true);
  bytes.set(Buffer.from('board'),off+24); return bytes;
}
async function nrfImage(version,flags=0) {
  const bytes=new Uint8Array(16080), end=bytes.length-56, record=end-24, v=new DataView(bytes.buffer);
  bytes.fill(0xa4,0,record); bytes.set(Buffer.from('mOTALay1'),record);bytes[record+8]=1;bytes[record+9]=flags;
  v.setUint16(record+10,24,true);v.setUint32(record+12,0x26000,true);v.setUint32(record+16,0xED000,true);v.setUint32(record+20,0xED000,true);
  bytes.set(Buffer.from('EndF'),end);v.setUint32(end+4,end,true);
  bytes.set(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes.slice(0,end))).slice(0,8),end+8);
  v.setUint32(end+16,version,true);v.setUint32(end+20,0x12345678,true);bytes.set(Buffer.from('board'),end+24);
  return bytes;
}
async function fixture() {
  const image=await firmware(), info=await pkg.parseMota(await pkg.buildFullMota(image));
  const id=Buffer.from(info.merkleRoot).toString('hex'), body=Buffer.from(image.slice(-48,-40)).toString('hex');
  const calls=[], overrides=new Map(); let state='idle', reset=0, installed=false, serving=false, record=null;
  const reply=(label,cmd) => {
    calls.push(label+':'+cmd);
    const replacement=overrides.get(label+':'+cmd);
    if (replacement !== undefined) return replacement;
    if (cmd==='ota.peer radio') return 'OTApeer radio=radio';
    if (cmd==='get radio') return '910.525,62.5,7,5';
    if (cmd==='get tempradio') return 'TempRadio active: 910.525,62.5,7,5 10000s left';
    if (cmd==='get radio2' || cmd==='get tempradio2' || cmd==='get radio2.cross' || cmd==='get radio.rxps') return 'off';
    if (cmd==='ota config') return 'autofetch=off autoinstall=off hops=2';
    if (cmd==='get repeat') return 'on';
    if (cmd==='ota stats') return 'fetch idle';
    if (cmd==='ota folder') return 'folder=off serving=0:';
    if (cmd==='ota status') return 'hw=board target:12345678 | maxblk:1024 | '+(state==='idle'?'no download':'download: '+(state==='ready'?'ready to install':'receiving')+' id='+id);
    if (cmd==='ota self') return 'base_hash='+(installed?body:'1122334455667788');
    if (cmd==='ver') return installed?'v1.18.0.1':'v1.17.1.9';
    if (cmd==='ota ls') return id;
    if (cmd==='ota installhash') return 'ERR usage: ota installhash <64-hex-container-sha256>';
    if (cmd.startsWith('ota pull ')) { state='ready'; return 'OK'; }
    if (/^ota installhash [0-9a-f]{64}$/.test(cmd)) { installed=true; return 'OK'; }
    throw Error('Unexpected '+label+':'+cmd);
  };
  const link={aclOnly:true,opened:true,supportsRoutedPath:true,target,relays:[first,second],sourceKey:source,
    async proveAll() { calls.push('proveAll'); },
    async localCommand(cmd) { return reply('source',cmd); },
    async remoteCommand(key,password,cmd) { assert.equal(password,''); return reply(key,cmd); },
    async serve() { calls.push('serve'); serving=true; }, async stopServing() { calls.push('stopServing'); serving=false; },
    async resetRemoteSession(k) { assert.equal(k,target); calls.push('reset'); reset++; }};
  const session=new RoutedSession(link,target,'',info,{pollMs:1,journal:r=>{record=r;},readJournal:()=>record});
  const packageHash=Buffer.from(await crypto.subtle.digest('SHA-256',info.blob)).toString('hex');
  return {info,id,body,packageHash,link,session,calls,overrides,get reset(){return reset;},get serving(){return serving;},get record(){return record;},setRecord(r){record=r;},setState(s){state=s;},setInstalled(v){installed=v;}};
}

test('preflight accepts only explicit routed OTA policies',()=>{
  routedPolicy('hops=2 autofetch=off autoinstall=off',2);
  routedPolicy('mode=seeder-only hops=3',2);
  assert.throws(()=>routedPolicy('hops=1 autofetch=off autoinstall=off',2),/cover/);
  assert.throws(()=>routedPolicy('hops=2 autofetch=on autoinstall=off',2),/automatic/);
  assert.throws(()=>routedPolicy('hops=2 autofetch=off autofetch=any autoinstall=off',2),/automatic/);
  assert.throws(()=>routedPolicy('hops=2 hops=3 autofetch=off autoinstall=off',2),/cover/);
});

test('rejects a relay on a different primary even if its temporary radio matches',async()=>{
  const f=await fixture();
  f.overrides.set(first+':get radio','910.250,62.5,7,5');
  f.overrides.set(first+':get tempradio','TempRadio active: 910.250,62.5,7,5 10000s left');
  await assert.rejects(f.session.start(),/same radio settings/); assert.equal(f.calls.includes('serve'),false);
});

test('all participants are checked and transfer does not mutate radios or policies',async()=>{
  const f=await fixture(); await f.session.start();
  assert.equal(f.serving,false); assert.equal(f.calls.filter(c=>c.endsWith('ota pull '+f.id.toUpperCase()+' flash')).length,1);
  assert.equal(f.calls.filter(c=>c==='proveAll').length,1);
  for (const label of ['source',first,second,target]) {
    for (const command of ['get radio','get tempradio','get radio2','get tempradio2','get radio2.cross','get radio.rxps','ota config'])
      assert.ok(f.calls.includes(label+':'+command),label+' missing '+command);
  }
  for(const label of [first,second]) { assert.ok(f.calls.includes(label+':get repeat')); assert.ok(f.calls.includes(label+':ota stats')); }
  assert.equal(f.calls.some(c=>/:(?:set |tempradio |normalradio|ota config (?:hops|autofetch|autoinstall) )/.test(c)),false);
});

test('routed updates refuse a target without verified install before serving',async()=>{
  const f=await fixture();
  f.overrides.set(target+':ota installhash','Unknown command');
  await assert.rejects(f.session.start(),/does not support verified OTA installation/);
  assert.equal(f.calls.includes('serve'),false);
  assert.equal(f.calls.some(c=>c.includes(':ota pull ')||c.endsWith(':ota install')),false);
});

test('refuses unsafe participant state before attaching a source folder',async()=>{
  const cases=[
    ['source:ota.peer radio','OTApeer radio=radio2',/primary/],
    [first+':get radio','910.250,62.5,7,5',/temporary radio/],
    [second+':get tempradio','pending',/temporary radio/],
    [target+':get radio2','911.150,250,7,5,rxtx',/radio2/],
    [first+':get tempradio2','pending',/radio2/],
    [second+':get radio2.cross','on',/radio2/],
    [first+':get radio.rxps','on',/saving/],
    [second+':ota config','autofetch=off autoinstall=off hops=1',/cover/],
    [target+':ota config','autofetch=off autoinstall=on hops=2',/automatic/],
    [first+':get repeat','off',/repeating/],
    [second+':ota stats','fetch active',/OTA download/],
    ['source:ota folder','folder=on serving=1:',/attached/]
  ];
  for (const [k,v,expected] of cases) {
    const f=await fixture(); f.overrides.set(k,v);
    await assert.rejects(f.session.start(),expected,k); assert.equal(f.calls.includes('serve'),false,k);
  }
});

test('re-proves the target after install and validates its exact firmware',async()=>{
  const f=await fixture(); await f.session.start(); const result=await f.session.install();
  assert.equal(result.baseHash,f.body); assert.equal(f.reset,1);
  assert.equal(f.record.phase,'installed'); assert.equal(f.record.packageHash,f.packageHash);
  assert.equal(f.calls.filter(c=>c.startsWith(target+':ota installhash ')).length,1);
  assert.ok(f.calls.indexOf('reset') > f.calls.findIndex(c=>c.startsWith(target+':ota installhash ')));
});

test('resume and install require the original full package recovery record',async()=>{
  const f=await fixture(); f.setState('ready');
  const correct={phase:'ready',target,source,id:f.id,packageHash:f.packageHash};
  const bad=[null, {...correct,target:first}, {...correct,source:first}, {...correct,id:'11223344'},
    {...correct,packageHash:'00'.repeat(32)}, {...correct,phase:'prepared'}];
  for (const record of bad) {
    f.setRecord(record);
    await assert.rejects(f.session.start({resume:true}),/recovery record/);
    await assert.rejects(f.session.install(),/recovery record/);
  }
  assert.equal(f.calls.some(c=>c.endsWith(':ota install')),false);
  assert.equal(f.calls.includes('serve'),false);
  f.setRecord(correct); await f.session.start({resume:true});
  assert.equal(f.record.packageHash,f.packageHash); assert.equal(f.record.phase,'ready');
  await f.session.install(); assert.equal(f.record.packageHash,f.packageHash); assert.equal(f.record.phase,'installed');
});

test('reconnect verifies a prior install against the full package and installed body',async()=>{
  const f=await fixture(); f.setInstalled(true);
  const prior={phase:'install attempted',target,source,id:f.id,packageHash:f.packageHash,bodyHash:f.body};
  const bad=[null,{...prior,phase:'ready'},{...prior,target:first},{...prior,source:first},
    {...prior,id:'11223344'},{...prior,packageHash:'00'.repeat(32)},{...prior,bodyHash:'00'.repeat(8)}];
  for (const record of bad) {
    f.setRecord(record);
    await assert.rejects(f.session.inspect({allowExisting:true}),/not newer/);
  }
  f.setRecord(prior); f.overrides.set(target+':ota self','base_hash=9999999999999999');
  await assert.rejects(f.session.inspect({allowExisting:true}),/not newer/);
  f.overrides.delete(target+':ota self');
  f.setRecord(prior); const result=await f.session.inspect({allowExisting:true});
  assert.equal(result.download,'installed'); assert.equal(result.baseHash,f.body);
  assert.equal(f.record.phase,'installed'); assert.equal(f.record.packageHash,f.packageHash);
  assert.equal(f.calls.includes('serve'),false);
  assert.equal(f.calls.some(c=>c.endsWith(':ota install')),false);
});

test('requires full explicit ACL route configuration',async()=>{
  const f=await fixture();
  assert.throws(()=>new RoutedSession({...f.link,aclOnly:false},target,'',f.info),/ACL/);
  assert.throws(()=>new RoutedSession(f.link,target,'secret',f.info),/ACL/);
  assert.throws(()=>new RoutedSession({...f.link,relays:[]},target,'',f.info),/route/);
});

test('routed sequential delta requires positive ESP32 bootloader evidence',async()=>{
  const f=await fixture(), next=await firmware(), base=next.slice();
  new DataView(base.buffer).setUint32(base.length-56+16,0x01110109,true);
  const built=await delta.buildSequentialDelta(base,next), info=await pkg.parseMota(built.blob);
  f.overrides.set(target+':ota self','base_hash='+Buffer.from(base.slice(-48,-40)).toString('hex'));
  f.overrides.set(target+':get bootloader.ver','ERR unsupported');
  const session=new RoutedSession(f.link,target,'',info,{image:next});
  await session.inspect(); assert.ok(f.calls.includes(target+':get bootloader.ver'));
  f.overrides.set(target+':get bootloader.ver','0.4.3');
  await assert.rejects(session.inspect(),/Cannot confirm.*sequential/);
});

test('routed nRF52 in-place checks live internal workspace before serving',async()=>{
  const f=await fixture(), base=await nrfImage(0x01110109), next=await nrfImage(0x01120001);
  const built=await delta.buildInPlaceDelta(base,next,{runtimeStorage:'internal',runtimeStageCeiling:0xED000});
  const info=await pkg.parseMota(built.blob), layout=delta.nrfLayout(base);
  f.overrides.set(target+':ota status','hw=board target:12345678 | maxblk:1024 | bl:internal | no download');
  f.overrides.set(target+':ota self','base_hash='+Buffer.from(base.slice(-48,-40)).toString('hex')+' | bootloader: internal abi=3 codecs=0x5 | stage=ED000');
  const plan={storage:'internal',app:layout.app,layoutCeiling:layout.ceiling,memorySize:built.memorySize};
  const session=new RoutedSession(f.link,target,'',info,{image:next,inplacePlan:plan});
  await session.inspect();
  plan.memorySize=0xF0000;
  await assert.rejects(session.inspect(),/staging space/); assert.equal(f.calls.includes('serve'),false);
});

test('routed nRF52 QSPI checks fresh capacity before serving',async()=>{
  const f=await fixture(), base=await nrfImage(0x01110109,4), next=await nrfImage(0x01120001,4);
  const built=await delta.buildInPlaceDelta(base,next,{runtimeStorage:'qspi',runtimeQspiApply:true,runtimeStorageBytes:2048*1024});
  const info=await pkg.parseMota(built.blob), layout=delta.nrfLayout(base);
  f.overrides.set(target+':ota status','hw=board target:12345678 | maxblk:1024 | bl:QSPI | no download');
  f.overrides.set(target+':ota self','base_hash='+Buffer.from(base.slice(-48,-40)).toString('hex')+' | bootloader: QSPI apply OK abi=3 codecs=0x5');
  f.overrides.set(target+':ota qspi','QSPI jedec=856015 size=2048K sr1=00');
  const plan={storage:'qspi',app:layout.app,layoutEnd:layout.end,memorySize:built.memorySize};
  const session=new RoutedSession(f.link,target,'',info,{image:next,inplacePlan:plan});
  await session.inspect(); assert.ok(f.calls.includes(target+':ota qspi'));
  f.overrides.set(target+':ota qspi','QSPI jedec=856015 size=0K sr1=00');
  await assert.rejects(session.inspect(),/capacity/); assert.equal(f.calls.includes('serve'),false);
});
