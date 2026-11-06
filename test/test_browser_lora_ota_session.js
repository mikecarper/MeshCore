'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto').webcrypto;
const pkg = require('../docs/_javascript/lora_ota_package.js');
const {DirectSession, temp, download, policy, installHash} = require('../docs/_javascript/lora_ota_session.js');
const installUsage = 'ERR usage: ota installhash <64-hex-container-sha256>';

async function image() {
  const bytes = new Uint8Array(100), v = new DataView(bytes.buffer), off = bytes.length - 56;
  bytes.set(Buffer.from('EndF'), off); v.setUint32(off + 4, off, true);
  bytes.set(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes.slice(0, off))).slice(0, 8), off + 8);
  v.setUint32(off + 16, 0x01120001, true); v.setUint32(off + 20, 0x12345678, true);
  bytes.set(Buffer.from('board'), off + 24); return bytes;
}
test('validates temporary radio, policy and download identity conservatively', () => {
  assert.deepEqual(temp('> 910.525,62.5,7,5,100'), [910.525,62.5,7,5]);
  assert.throws(() => temp('pending'), /active/);
  assert.throws(() => policy('autofetch=on autoinstall=off hops=0'), /automatic/);
  policy('autofetch=off autoinstall=off hops=0');
  assert.deepEqual(download('no download'), {state:'idle'});
  assert.deepEqual(download('download: ready to install id=ABCDEF01 |'), {state:'ready',id:'abcdef01'});
  assert.throws(() => download('download: pending'), /identity/);
});
test('checks target and serves only matching verified package', async () => {
  const p = await pkg.parseMota(await pkg.buildFullMota(await image()));
  const id = Buffer.from(p.merkleRoot).toString('hex');
  let started = false, stopped = false, state = 'idle'; const calls = [];
  const config = 'ota config: autofetch=off autoinstall=off hops=0';
  const radio = '910.525,62.5,7,5,120';
  const link = {
    sourceKey:'aa'.repeat(32), async verifyPeer() {},
    async localCommand(c) { calls.push('local '+c); return (c === 'get tempradio' || c === 'get radio') ? radio : c === 'get radio.rxps' ? 'off,1000,1000' : c === 'ota stats' ? 'fetch idle' : c === 'ota folder' ? 'folder=off' : config; },
    async remoteCommand(k,pw,c) {
      calls.push(c);
      if (c === 'ota status') return 'hw=board target:12345678 | maxblk:1024 | ' + (state === 'idle' ? 'no download' : 'download: ready to install id=' + id);
      if (c === 'ota self') return 'base_hash=1122334455667788';
      if (c === 'ver') return 'v1.17.1.9';
      if (c === 'get tempradio' || c === 'get radio') return radio;
      if (c === 'get radio.rxps') return 'off,1000,1000';
      if (c === 'ota config') return config;
      if (c === 'ota installhash') return installUsage;
      if (c === 'ota ls') return id;
      if (c.startsWith('ota pull ')) { state = 'ready'; return 'OK'; }
      throw Error('Unexpected ' + c);
    },
    async serve() { started = true; }, async stopServing() { stopped = true; }
  };
  const session = new DirectSession(link, 'bb'.repeat(32), '', p, {pollMs:1});
  await session.start(); assert(started && stopped); assert.equal(calls.filter(c => c.startsWith('ota pull ')).length, 1);
  assert.equal(calls.includes('ota install'), false);
});
test('resumes only the exact existing download and does not send a second pull', async () => {
  const p = await pkg.parseMota(await pkg.buildFullMota(await image()));
  const id = Buffer.from(p.merkleRoot).toString('hex');
  let reads = 0, pulls = 0, served = false; const config = 'autofetch=off autoinstall=off hops=0', radio = '910.525,62.5,7,5,120';
  const link = {sourceKey:'aa'.repeat(32), async verifyPeer() {},
    async localCommand(c) { return (c === 'get tempradio' || c === 'get radio') ? radio : c === 'get radio.rxps' ? 'off,1000,1000' : c === 'ota stats' ? 'fetch idle' : c === 'ota folder' ? 'folder=off' : config; },
    async remoteCommand(k,pw,c) {
      if (c === 'ota status') return 'hw=board target:12345678 | maxblk:1024 | download: ' + (++reads > 2 ? 'ready to install' : 'receiving') + ' id=' + id;
      if (c === 'ota self') return 'base_hash=1122334455667788';
      if (c === 'ver') return 'v1.17.1.9';
      if (c === 'get tempradio' || c === 'get radio') return radio;
      if (c === 'get radio.rxps') return 'off,1000,1000';
      if (c === 'ota config') return config;
      if (c === 'ota installhash') return installUsage;
      if (c.startsWith('ota pull')) { pulls++; return 'OK'; }
      throw Error('Unexpected ' + c);
    }, async serve() { served = true; }, async stopServing() {} };
  const packageHash = Buffer.from(await crypto.subtle.digest('SHA-256', p.blob)).toString('hex');
  const unowned = new DirectSession(link, 'bb'.repeat(32), '', p, {pollMs:1, readJournal:() => ({phase:'pull attempted', target:'bb'.repeat(32), source:'aa'.repeat(32), id, packageHash:'00'.repeat(32)})});
  await assert.rejects(unowned.start({resume:true}), /recovery record/);
  reads = 0;
  const session = new DirectSession(link, 'bb'.repeat(32), '', p, {pollMs:1, readJournal:() => ({phase:'pull attempted', target:'bb'.repeat(32), source:'aa'.repeat(32), id, packageHash})});
  await session.start({resume:true}); assert.equal(pulls, 0); assert(served);
});
test('reconnect verifies an uncertain install without issuing another install', async () => {
  const imageBytes = await image(), p = await pkg.parseMota(await pkg.buildFullMota(imageBytes));
  const id = Buffer.from(p.merkleRoot).toString('hex'), packageHash = Buffer.from(await crypto.subtle.digest('SHA-256', p.blob)).toString('hex');
  const bodyHash = Buffer.from(imageBytes.slice(-48, -40)).toString('hex');
  const calls = [], link = {sourceKey:'aa'.repeat(32), async verifyPeer() {}, async remoteCommand(k,pw,c) {
    calls.push(c);
    if (c === 'ota status') return 'hw=board target:12345678 maxblk:1024 no download';
    if (c === 'ota self') return 'base_hash=' + bodyHash;
    if (c === 'ver') return 'v1.18.0.1';
    throw Error('Unexpected command ' + c);
  }};
  let saved;
  const session = new DirectSession(link, 'bb'.repeat(32), '', p, {readJournal:() => ({phase:'install attempted', target:'bb'.repeat(32), source:link.sourceKey, id, packageHash, bodyHash}), journal:r => { saved = r; }});
  assert.equal((await session.inspect({allowExisting:true})).download, 'installed');
  assert.equal(saved.phase, 'installed');
  assert(!calls.includes('ota install'));
});

test('post-install uses one reset-and-proof exchange with the selected password', async () => {
  const imageBytes=await image(), p=await pkg.parseMota(await pkg.buildFullMota(imageBytes));
  const target='bb'.repeat(32), source='aa'.repeat(32), password='secret';
  const id=Buffer.from(p.merkleRoot).toString('hex'), packageHash=Buffer.from(await crypto.subtle.digest('SHA-256',p.blob)).toString('hex');
  const bodyHash=Buffer.from(imageBytes.slice(-48,-40)).toString('hex');
  let installed=false, verifies=0, resets=0, installs=0, record={phase:'ready',target,source,id,packageHash};
  const radio='910.525,62.5,7,5,120', config='autofetch=off autoinstall=off hops=0';
  const link={sourceKey:source,
    async verifyPeer(k,pw){assert.equal(k,target);assert.equal(pw,password);verifies++;},
    async resetRemoteSession(k,pw){assert.equal(k,target);assert.equal(pw,password);resets++;},
    async localCommand(c){return c==='get tempradio'||c==='get radio'?radio:c==='get radio.rxps'?'off':c==='ota stats'?'fetch idle':c==='ota folder'?'folder=off':config;},
    async remoteCommand(k,pw,c){
      assert.equal(k,target);assert.equal(pw,password);
      if(c==='ota status')return 'hw=board target:12345678 maxblk:1024 '+(installed?'no download':'download: ready to install id='+id);
      if(c==='ota self')return 'base_hash='+(installed?bodyHash:'1122334455667788');
      if(c==='ver')return installed?'v1.18.0.1':'v1.17.1.9';
      if(c==='get radio'||c==='get tempradio')return radio;
      if(c==='get radio.rxps')return 'off';
      if(c==='ota config')return config;
      if(c==='ota installhash')return installUsage;
      if(c==='ota installhash '+await installHash(p.blob)){installs++;installed=true;return 'OK';}
      throw Error('Unexpected '+c);
    }};
  const session=new DirectSession(link,target,password,p,{pollMs:1,readJournal:()=>record,journal:r=>{record=r;}});
  const result=await session.install();
  assert.equal(result.baseHash,bodyHash); assert.equal(installs,1); assert.equal(verifies,1); assert.equal(resets,1);
  assert.equal(record.phase,'installed');
});

test('container install hash normalizes only the approval marker', async () => {
  const p=await pkg.parseMota(await pkg.buildFullMota(await image()));
  const normalized=p.blob.slice(); normalized.fill(0,201,205);
  const expected=Buffer.from(await crypto.subtle.digest('SHA-256',normalized)).toString('hex');
  assert.equal(await installHash(p.blob),expected);
  const approved=p.blob.slice(); approved.fill(123,201,205);
  assert.equal(await installHash(approved),expected);
  approved[210]^=1;
  assert.notEqual(await installHash(approved),expected);
});

test('older target without installhash is refused before serving or pulling', async () => {
  const p=await pkg.parseMota(await pkg.buildFullMota(await image()));
  const calls=[], config='autofetch=off autoinstall=off hops=0', radio='910.525,62.5,7,5,120';
  const link={sourceKey:'aa'.repeat(32),async verifyPeer(){},async localCommand(c){calls.push('local '+c);return c==='get tempradio'||c==='get radio'?radio:c==='get radio.rxps'?'off':config;},
    async remoteCommand(k,pw,c){calls.push(c);if(c==='ota status')return 'hw=board target:12345678 maxblk:1024 no download';if(c==='ota self')return 'base_hash=1122334455667788';if(c==='ver')return 'v1.17.1.9';if(c==='get radio'||c==='get tempradio')return radio;if(c==='get radio.rxps')return 'off';if(c==='ota config')return config;if(c==='ota installhash')return 'ERR usage: ota install';throw Error('Unexpected '+c);},
    async serve(){throw Error('must not serve');}};
  const session=new DirectSession(link,'bb'.repeat(32),'',p,{pollMs:1});
  await assert.rejects(session.start(),/does not support verified OTA installation/);
  assert(!calls.some(c=>c.startsWith('ota pull')||c==='ota install'||c.startsWith('ota installhash ')));
});
