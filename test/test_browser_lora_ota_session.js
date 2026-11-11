'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto').webcrypto;
const pkg = require('../docs/_javascript/lora_ota_package.js');
const checks = require('../docs/_javascript/lora_ota_checks.js');
const {DirectSession, temp, download, policy, installHash} = require('../docs/_javascript/lora_ota_session.js');
const installUsage = 'ERR usage: ota installhash <64-hex-container-sha256>';

test('ESP32 metadata avoids bootloader query while unknown or conflicting metadata does not', async () => {
  const status='hw=Station_G2 | target:DBB6A855 | maxblk:2048',self='base_hash=1122334455667788';
  const metadata={profiles:{'Station_G2_repeater':{platform:'ESP32_PLATFORM'}}};
  const options={controlsUrl:'https://example.invalid/controls',fetchImpl:async()=>({ok:true,json:async()=>metadata})};
  let queries=0;
  const probe=async()=>{queries++;return 'Error: unsupported';};
  await checks.ensureSequentialTarget(status,self,probe,options);
  assert.equal(queries,0);
  metadata.profiles['Station_G2_sensor']={platform:'NRF52_PLATFORM'};
  await checks.ensureSequentialTarget(status,self,probe,options);
  assert.equal(queries,1);
  await assert.rejects(checks.ensureSequentialTarget(status,self+' bootloader: SD apply OK',probe,options),/Cannot confirm/);
  assert.equal(queries,2);
  assert.equal(await checks.isKnownEsp32(status,self,{...options,fetchImpl:async()=>{throw Error('offline');}}),false);
});

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
  assert.throws(() => policy('autofetch=on autoinstall=off hops=0'), /automatic/i);
  policy('autofetch=off autoinstall=off hops=0');
  assert.deepEqual(download('no download'), {state:'idle'});
  assert.deepEqual(download('download: ready to install id=ABCDEF01 |'), {state:'ready',id:'abcdef01'});
  assert.throws(() => download('download: pending'), /identity/);
});
test('classifies exact Companion pending and inactive replies without accepting their tuples', () => {
  for (const raw of ['TempRadio pending: 906.525,250.00,5,5,preamble=32',
                     'TempRadio inactive', '> TempRadio inactive', '> pending', '> off'])
    assert.throws(() => temp(raw), /An active temporary radio window is required/);
  assert.deepEqual(temp('TempRadio active: 906.525,250.00,5,5 7200s left,preamble=32'), [906.525,250,5,5]);
  assert.deepEqual(temp('TempRadio active: 906.525,250.00,5,5 7200s left'), [906.525,250,5,5]);
  assert.deepEqual(temp('> 906.525,250.000,5,5,32'), [906.525,250,5,5]);
});
test('unrecognized temporary replies have bounded printable diagnostics', () => {
  assert.throws(() => temp('unexpected\r\n\0\x1b\u202e'+ 'x'.repeat(400)), error => {
    assert.match(error.message, /Cannot read active temporary radio settings: unexpected/);
    assert(!/[\x00-\x1f\x7f-\x9f\u202e]/.test(error.message));
    assert(error.message.length < 230);return true;
  });
});

function radioPreflight(options = {}) {
  const calls=[], progress=[];
  const config={sourceTemp:'TempRadio active: 906.525,250.00,5,5 7200s left,preamble=32',
    saved:'> 910.525,62.500,7,5',targetTemp:'> 906.525,250.000,5,5,32',
    sourceTemp2:'> 906.525,250.000,5,5,rxtx,128; 0d2h0m (120 min) left',
    targetTemp2:'> 906.525,250.000,5,5,rxtx,128; 0d2h0m (120 min) left',
    sourceStatus:'> rxtx; RX=3,5 TX=0,0 switches=10 errors=0 max=200us preamble=32,128; test=4/4,4/4 ready budget=220us',
    targetStatus:'> rxtx; RX=3,5 TX=0,0 switches=10 errors=0 max=200us preamble=32,128; test=4/4,4/4 ready budget=220us',
    sourceRxps:'> off',targetRxps:'> off',policy:'autofetch=off autoinstall=off hops=0',...options};
  const link={browserRawSource:true,otaSupported:true,localCliSupported:true,...options.link,
    async verifyPeer(){throw Error('Radio-only preflight must not perform identity/login operations');},
    async localCommand(command){
      calls.push(['source',command]);
      if(command==='get tempradio')return config.sourceTemp;if(command==='get radio')return config.saved;
      if(command==='get tempradio2')return config.sourceTemp2;if(command==='get radio2.status')return config.sourceStatus;
      if(command==='get radio.rxps')return config.sourceRxps;if(command==='ota config')return config.policy;
      throw Error('Unexpected source command '+command);
    },
    async remoteCommand(key,password,command){
      calls.push(['target',command]);assert.equal(key,'bb'.repeat(32));assert.equal(password,'secret');
      if(command==='get tempradio')return config.targetTemp;if(command==='get radio.rxps')return config.targetRxps;
      if(command==='get tempradio2')return config.targetTemp2;if(command==='get radio2.status')return config.targetStatus;
      if(command==='ota config')return config.policy;throw Error('Unexpected target command '+command);
    },async savedSourceRadio(){calls.push(['source','cached SELF_INFO']);return options.cached || [906.525,250,5,5];}};
  return {link,calls,progress,session:new DirectSession(link,'bb'.repeat(32),'secret',null,{progress:text=>progress.push(text)})};
}
test('package-independent guarded Companion preflight accepts active off-primary without mutations', async () => {
  const f=radioPreflight();const result=await f.session.inspectRadios();
  assert.deepEqual(result,{source:[906.525,250,5,5],target:[906.525,250,5,5],savedSource:[910.525,62.5,7,5],sourceReadback:'active'});
  assert.deepEqual(f.session.tempRadio,[906.525,250,5,5]);
  assert.deepEqual(f.calls,[['source','get tempradio'],['source','get radio'],['source','ota config'],
    ['target','get tempradio'],['target','ota config']]);
});
test('off-primary allowance requires guarded raw Companion and target matching still fails closed', async () => {
  for(const link of [{browserRawSource:false,otaSupported:true},{browserRawSource:true,otaSupported:false},
                    {browserRawSource:true,otaSupported:undefined}]) {
    const f=radioPreflight({link});await assert.rejects(f.session.inspectRadios(),/temporary radio must match its saved primary/);
    assert.equal(f.calls.some(([endpoint])=>endpoint==='target'),false);
  }
  const f=radioPreflight({targetTemp:'> 907.525,250.000,5,5,32'});
  await assert.rejects(f.session.inspectRadios(),/Temporary radio settings do not match between USB Companion and target/);
  assert.equal(f.session.tempRadio,null);
});
test('native API-probed repeater permits verified primary fallback away from saved settings', async () => {
  const f=radioPreflight({link:{browserRawSource:false,supportsOtaRadio2:true,otaRadioProfile:0}});
  const result=await f.session.inspectRadios();assert.deepEqual(result.source,[906.525,250,5,5]);
  assert.deepEqual(result.savedSource,[910.525,62.5,7,5]);assert.deepEqual(result.target,result.source);
  const old=radioPreflight({link:{browserRawSource:false,supportsOtaRadio2:false,otaRadioProfile:0}});
  await assert.rejects(old.session.inspectRadios(),/source temporary radio must match its saved primary/);
});
test('radio-only preflight labels inactive source and target and bounds unexpected replies', async () => {
  let f=radioPreflight({sourceTemp:'TempRadio pending: 906.525,250.00,5,5,preamble=32'});
  await assert.rejects(f.session.inspectRadios(),/active temporary radio window is required on the USB Companion/);
  f=radioPreflight({targetTemp:'TempRadio inactive'});
  await assert.rejects(f.session.inspectRadios(),/required on the target/);
  f=radioPreflight({targetTemp:'not-a-radio\r\n\0'+ 'x'.repeat(500)});
  await assert.rejects(f.session.inspectRadios(),error=>{
    assert.match(error.message,/target: Cannot read active temporary radio settings/);
    assert(error.message.length < 200);assert(!/[\r\n\0]/.test(error.message));return true;
  });
  for(const options of [{policy:'autofetch=on autoinstall=off hops=0'}]) {
    f=radioPreflight(options);await assert.rejects(f.session.inspectRadios(),/automatic OTA fetch/i);
  }
});
test('radio-only preflight preserves legacy cached-source fallback and explicit disclosure', async () => {
  const f=radioPreflight({link:{localCliSupported:false,otaSupported:false}});
  const result=await f.session.inspectRadios();assert.equal(result.sourceReadback,'saved-only');
  assert.deepEqual(result.source,[906.525,250,5,5]);
  assert.equal(f.calls.filter(([endpoint])=>endpoint==='source').length,1);
  assert(f.progress.some(text=>/Active temporary settings and preamble cannot be verified/.test(text)));
  assert(f.calls.some(([endpoint,command])=>endpoint==='target'&&command==='ota config'));
  const invalid=radioPreflight({link:{localCliSupported:false,otaSupported:false},cached:[NaN,250,5,5]});
  await assert.rejects(invalid.session.inspectRadios(),/USB Companion saved radio settings/);
});
test('selected temporary radio2 requires live RXTX proof and keeps the normal primary unchanged', async () => {
  for(const browserRawSource of [true,false]) {
    const f=radioPreflight({sourceTemp:'TempRadio inactive',targetTemp:'> off',
      link:{browserRawSource,otaRadioProfile:1,otaTargetProfile:1,supportsOtaRadio2:true}});
    const result=await f.session.inspectRadios();assert.deepEqual(result.source,[906.525,250,5,5]);
    assert.deepEqual(result.target,result.source);assert.deepEqual(result.savedSource,[910.525,62.5,7,5]);
    for(const endpoint of ['source','target']) {
      assert(f.calls.some(([label,command])=>label===endpoint&&command==='get tempradio2'));
      assert(f.calls.some(([label,command])=>label===endpoint&&command==='get radio2.status'));
      assert(f.calls.some(([label,command])=>label===endpoint&&command==='get tempradio'));
    }
    assert(f.calls.every(([,command])=>command.startsWith('get ')||command==='ota config'));
  }
});
test('source and target profiles are selected independently for older primary-only peers', async () => {
  for(const [sourceProfile,targetProfile]of [[1,0],[0,1]]) {
    const f=radioPreflight({sourceTemp:sourceProfile?'TempRadio inactive':'TempRadio active: 906.525,250.00,5,5 7200s left,preamble=128',
      targetTemp:targetProfile?'> off':'> 906.525,250.000,5,5,128',
      link:{otaRadioProfile:sourceProfile,otaTargetProfile:targetProfile,supportsOtaRadio2:true}});
    const result=await f.session.inspectRadios();assert.deepEqual(result.source,result.target);
    for(const [label,profile]of [['source',sourceProfile],['target',targetProfile]])
      assert.equal(f.calls.some(([endpoint,command])=>endpoint===label&&command==='get tempradio2'),profile===1);
  }
});
test('queued, unsupported, RX-only or mismatched secondary is never accepted as an active update channel', async () => {
  const base={sourceTemp:'TempRadio inactive',targetTemp:'> off',
    link:{otaRadioProfile:1,otaTargetProfile:1,supportsOtaRadio2:true}};
  for(const changes of [
    {sourceStatus:'> off; RX=0,0 TX=0,0 switches=0 errors=0 max=0us preamble=32,0'},
    {sourceStatus:'> radio2 change pending reply transmission; previous channel retained'},
    {sourceStatus:'> rxtx; RX=3,5 TX=0,0 switches=10 errors=0 max=200us preamble=32,64'},
    {sourceTemp2:'> 906.525,250.000,5,5,rx,128; 0d2h0m (120 min) left'},
    {sourceTemp2:'Error: unsupported'},
    {sourceTemp:'TempRadio active: 910.525,62.50,7,5 120s left,preamble=32'},
    {targetTemp2:'> 907.525,250.000,5,5,rxtx,128; 0d2h0m (120 min) left'},
    {targetStatus:'> radio2 change pending storage commit (retrying); previous channel retained'}
  ]) {
    const f=radioPreflight({...base,...changes});await assert.rejects(f.session.inspectRadios());
    assert.equal(f.session.tempRadio,null);
  }
  for(const link of [{otaRadioProfile:1},{otaRadioProfile:'1',supportsOtaRadio2:true},
                    {otaRadioProfile:1,supportsOtaRadio2:true,localCliSupported:false}])
    await assert.rejects(radioPreflight({link}).session.inspectRadios(),/radio2|Invalid selected/);
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
  assert.equal(calls.includes('get radio'), false); // Target's active temporary tuple is sufficient.
  assert.equal(calls.includes('local get radio'), true);
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

async function recoveryFixture(options = {}) {
  const info = await pkg.parseMota(await pkg.buildFullMota(await image()));
  const id = checks.hex(info.merkleRoot), source = 'aa'.repeat(32), target = 'bb'.repeat(32);
  const packageHash = checks.hex(new Uint8Array(await crypto.subtle.digest('SHA-256', info.blob)));
  const calls = [], progress = [];
  let state = options.state || 'failed', activeId = options.id || id, statusReads = 0;
  let record = {phase:'pull attempted', source, target, id, packageHash};
  const status = () => state === 'idle' ? 'no download' :
    'download: ' + (state === 'ready' ? 'ready to install' : state === 'failed' ? 'failed (manifest timeout)' : 'downloading') + ' 0/0 id=' + activeId;
  const link = {sourceKey:source, async verifyPeer() {},
    async localCommand(command) {
      calls.push('local ' + command);
      if (command === 'get radio' || command === 'get tempradio') return '910.525,62.5,7,5,120';
      if (command === 'ota stats') return 'fetch idle';
      if (command === 'ota folder') return 'folder=off';
      if (command === 'ota config') return 'autofetch=off autoinstall=off hops=0';
      throw Error('Unexpected local ' + command);
    },
    async remoteCommand(_key, _password, command) {
      calls.push(command);
      if (command === 'ota status') {
        options.status?.(api, ++statusReads);
        return 'hw=board target:12345678 | maxblk:1024 | ' + (options.statsFallback ? 'legacy status' : status());
      }
      if (command === 'ota stats') return status().replace('download:', 'fetch');
      if (command === 'ota self') return 'base_hash=1122334455667788';
      if (command === 'ver') return 'v1.17.1.9';
      if (command === 'get radio' || command === 'get tempradio') return '910.525,62.5,7,5,120';
      if (command === 'ota config') return 'autofetch=off autoinstall=off hops=0';
      if (command === 'ota installhash') return options.legacyInstall ? 'Unknown OTA command' : installUsage;
      if (command === 'ota ls') { options.discover?.(api); return id; }
      if (command === 'ota cancel') {
        if (options.cancel) return options.cancel(api);
        state = 'idle'; return 'OK dropped session';
      }
      if (command.startsWith('ota pull ')) {
        if (options.pull) return options.pull(api);
        state = 'ready'; activeId = id; return 'OK resuming mid=' + id;
      }
      throw Error('Unexpected ' + command);
    },
    async serve() { calls.push('serve'); }, async stopServing() { calls.push('stopServing'); }
  };
  const session = new DirectSession(link, target, '', info,
    {pollMs:1, journal:value => { record=value; }, readJournal:() => record, progress:value=>progress.push(value), onStage:options.onStage});
  const api = {info,id,source,target,packageHash,calls,progress,link,session,
    get record() { return record; }, setRecord(value) { record=value; },
    setState(value, selectedId = activeId) { state=value; activeId=selectedId; }};
  return api;
}

test('catalog search finds the exact manifest on a later page, case-insensitively', async () => {
  const f=await recoveryFixture({state:'idle'}); f.setRecord(null);
  const original=f.link.remoteCommand;
  f.link.remoteCommand=async(key,password,command)=>{
    if(command==='ota ls') { f.calls.push(command); return 'Updates 1/2 (3 src; refreshing):\n 1) 01020304 v1 full [same target]\n 2) 02030405 v1 full [same target]'; }
    if(command==='ota ls 2') { f.calls.push(command); return 'uPDaTEs 2/2 (3 src; refreshing):\n 3) '+f.id.toUpperCase()+' v1 full [same target]'; }
    return original(key,password,command);
  };
  await f.session.start();
  assert.ok(f.calls.indexOf('ota ls 2')>f.calls.indexOf('ota ls'));
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length,1);
});

test('failed-download recovery also searches later catalog pages before retrying', async () => {
  const f=await recoveryFixture(); const original=f.link.remoteCommand;
  f.link.remoteCommand=async(key,password,command)=>{
    if(command==='ota ls') { f.calls.push(command);return 'Updates 1/2 (1 src; refreshing):\n 1) 00000001 v1'; }
    if(command==='ota ls 2') { f.calls.push(command);return 'Updates 2/2 (1 src; refreshing):\n 3) '+f.id+' v1'; }
    return original(key,password,command);
  };
  await f.session.start({resume:true});
  assert.ok(f.calls.includes('ota ls 2'));
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length,1);
});

test('catalog search rejects malformed pagination and does not match arbitrary substrings', async () => {
  const bad=[
    'Updates 0/2 (1 src; refreshing):\n 1) 12345678 v1',
    'Updates 1/0 (1 src; refreshing):\n 1) 12345678 v1',
    'Updates 1/129 (1 src; refreshing):\n 1) 12345678 v1',
    'Updates 1/2 (1 src; refreshing):\n 3) 12345678 v1',
    'Updates 1/2 (1 src; refreshing):\n 1) 12345678 v1\n 1) 23456789 v1',
    'Updates 1/2 (1 src; refreshing):\n 1) xx12345678 v1',
    'Updates nearby (1 src) - target 12345678',
  ];
  for(const reply of bad) {
    const f=await recoveryFixture(); f.link.remoteCommand=async()=>reply;
    await assert.rejects(f.session.catalogContains('12345678'),/catalog/);
  }
  for(const reply of ['Updates 1/2 (1 src; refreshing):\n 1) 00000001 v1',
    'Updates 2/1 (1 src; refreshing):\n 3) 00000002 v1']) {
    const f=await recoveryFixture(), responses=[reply,'Updates 1/2 (1 src; refreshing):\n 3) 12345678 v1'];
    f.link.remoteCommand=async()=>responses.shift();
    await assert.rejects(f.session.catalogContains('12345678'),/catalog/);
  }
});

test('absent package scans every advertised page and retains discovery timeout', async () => {
  const f=await recoveryFixture({state:'idle'}); f.setRecord(null);
  const original=f.link.remoteCommand;
  f.link.remoteCommand=async(key,password,command)=>{
    if(command==='ota ls'||command==='ota ls 2') { f.calls.push(command);return command==='ota ls' ?
      'Updates 1/2 (1 src; refreshing):\n 1) 00000001 v1' : 'Updates 2/2 (1 src; refreshing):\n 3) 00000003 v1'; }
    return original(key,password,command);
  };
  await assert.rejects(f.session.start(),/did not discover/);
  assert.equal(f.calls.filter(c=>c==='ota ls').length,12);
  assert.equal(f.calls.filter(c=>c==='ota ls 2').length,12);
  assert.ok(!f.calls.some(c=>c.startsWith('ota pull ')));
});

test('a shrinking catalog retries from page one without hiding unrelated errors', async () => {
  const f=await recoveryFixture({state:'idle'}); f.setRecord(null);
  const original=f.link.remoteCommand;let rounds=0;
  f.link.remoteCommand=async(key,password,command)=>{
    if(command==='ota ls') { f.calls.push(command);rounds++;return rounds===1 ?
      'Updates 1/2 (1 src; refreshing):\n 1) 00000001 v1' :
      'Updates 1/1 (1 src; refreshing):\n 1) '+f.id+' v1'; }
    if(command==='ota ls 2') { f.calls.push(command);return 'ERR update page 2 out of range (1-1)'; }
    return original(key,password,command);
  };
  await f.session.start();
  assert.deepEqual(f.calls.filter(c=>c==='ota ls'||c==='ota ls 2'),['ota ls','ota ls 2','ota ls']);
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length,1);
  for(const error of ['ERR update page 3 out of range (1-1)',
    'ERR update page 2 out of range (1-2)', 'ERR update page 2 out of range (1-0)',
    'ERR update page 2 out of range (1-1);reboot','ERR storage unavailable']) {
    const g=await recoveryFixture();let query=0;
    g.link.remoteCommand=async()=>++query===1 ? 'Updates 1/2 (1 src; refreshing):\n 1) 00000001 v1' : error;
    await assert.rejects(g.session.catalogContains('12345678'),/ERR/);
  }
});

test('catalog pagination is bounded and accepts legacy single-ID and empty replies', async () => {
  const f=await recoveryFixture(), calls=[];
  f.link.remoteCommand=async(_key,_password,command)=>{
    calls.push(command);const page=command==='ota ls'?1:Number(command.slice(7));
    return 'Updates '+page+'/128 (1 src; refreshing):\n '+(page*2-1)+') 00000001 v1';
  };
  assert.equal(await f.session.catalogContains('12345678'),false);
  assert.equal(calls.length,128);assert.equal(calls.at(-1),'ota ls 128');
  f.link.remoteCommand=async()=> 'No updates seen yet; retry `ota ls` shortly.';
  assert.equal(await f.session.catalogContains('12345678'),false);
  f.link.remoteCommand=async()=> 'ABCD1234';
  assert.equal(await f.session.catalogContains('abcd1234'),true);
  assert.equal(await f.session.catalogContains('abcd1235'),false);
});

test('transfer stages follow verified route, source, discovery and target download evidence', async () => {
  let f; const stages=[];
  f=await recoveryFixture({state:'idle',onStage:stage=>stages.push({stage,calls:f.calls.slice()})});
  f.setRecord(null);
  let reads=0;
  const original=f.link.remoteCommand;
  f.link.remoteCommand=async(key,password,command)=>{
    if(command.startsWith('ota pull ')) { f.calls.push(command); f.setState('active'); return 'OK'; }
    if(command==='ota status'&&f.calls.some(c=>c.startsWith('ota pull '))&&++reads>1)f.setState('ready');
    return original(key,password,command);
  };
  await f.session.start();
  assert.deepEqual(stages.map(s=>s.stage),['checking','preparing-source','discovering','requesting','transferring']);
  assert.ok(!stages[0].calls.includes('serve'));
  assert.ok(stages[1].calls.includes('ota self'));
  assert.ok(stages[2].calls.includes('serve'));
  assert.ok(stages[3].calls.includes('ota ls'));
  assert.ok(stages[4].calls.some(c=>c.startsWith('ota pull ')));
  assert.ok(stages[4].calls.includes('ota status'));
});

test('no transfer stage is announced when route checking or discovery fails', async () => {
  for(const failure of ['checking','discovering']) {
    const stages=[], f=await recoveryFixture({state:'idle',onStage:stage=>stages.push(stage)});
    f.setRecord(null);
    const original=f.link.remoteCommand;
    f.link.remoteCommand=async(key,password,command)=>{
      if ((failure==='checking'&&command==='ota self') || (failure==='discovering'&&command==='ota ls')) throw Error('read failed');
      return original(key,password,command);
    };
    await assert.rejects(f.session.start(),/read failed/);
    assert.ok(!stages.includes('transferring'));
  }
});

test('a different target download cannot be reported as the selected transfer', async () => {
  const stages=[], f=await recoveryFixture({state:'idle',onStage:stage=>stages.push(stage),
    pull:api=>{api.setState('active','00000000');return 'OK';}});
  f.setRecord(null);
  await assert.rejects(f.session.start(),/does not match/);
  assert.ok(!stages.includes('transferring'));
});

test('target failure preserves its reason and fallback diagnostics without another pull or cancellation', async () => {
  for (const legacy of [false, true]) {
    const f = await recoveryFixture({state:'idle', pull:api=>{api.setState('failed'); return 'OK';}});
    f.setRecord(null);
    const original = f.link.remoteCommand;
    f.link.remoteCommand = async (key, password, command) => {
      if (f.calls.some(c=>c.startsWith('ota pull ')) && command === 'ota status') {
        f.calls.push(command);
        return legacy ? 'OTA | status unavailable' :
          'OTA | download: failed (manifest timeout) 0/0 id=' + f.id;
      }
      if (f.calls.some(c=>c.startsWith('ota pull ')) && command === 'ota stats') {
        f.calls.push(command);
        return 'OTA stats | fetch failed:manifest timeout 0/0 id=' + f.id;
      }
      return original(key, password, command);
    };
    await assert.rejects(f.session.start(), /Target reported a failed download: failed.*manifest timeout/);
    assert.ok(f.progress.some(line=>line.startsWith('Target OTA status: OTA |')));
    assert.equal(f.progress.some(line=>line.includes('Target OTA stats: OTA stats | fetch failed:manifest timeout')), legacy);
    assert.ok(f.progress.some(line=>/Next step:.*LoRa path.*update channel/.test(line)));
    assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
    assert.ok(f.calls.includes('stopServing'));
    assert.ok(!f.calls.includes('ota cancel'));
    assert.equal(f.record.phase, 'pull attempted');
  }
});

test('failure diagnostics extract reasons after long status prefixes and sanitize controls', async () => {
  const f = await recoveryFixture({state:'idle', pull:api=>{api.setState('failed'); return 'OK';}});
  f.setRecord(null);
  const original = f.link.remoteCommand;
  f.link.remoteCommand = async (key, password, command) => {
    if (f.calls.some(c=>c.startsWith('ota pull ')) && command === 'ota status')
      return 'OTA | ' + 'x'.repeat(300) + ' | download: failed (storage\nerror) 0/0 id=' + f.id;
    return original(key, password, command);
  };
  await assert.rejects(f.session.start(), /failed \(storage error\)/);
  assert.ok(f.progress.some(line=>/Next step:.*get storage\.layout and ota self/.test(line)));
  assert.ok(!f.calls.includes('get storage.layout'));
  assert.ok(!f.calls.includes('ota stats'));
});

test('reason-less failure can use same-ID stats but never hide failure on stats error', async () => {
  for (const result of ['same', 'other', 'timeout', 'disconnect']) {
    const f = await recoveryFixture({state:'idle', pull:api=>{api.setState('failed'); return 'OK';}});
    f.setRecord(null);
    if (result === 'disconnect') f.link.stopServing = async () => { throw Error('detach failed'); };
    const original = f.link.remoteCommand;
    f.link.remoteCommand = async (key, password, command) => {
      if (f.calls.some(c=>c.startsWith('ota pull ')) && command === 'ota status')
        return 'OTA | download: failed 0/0 id=' + f.id;
      if (f.calls.some(c=>c.startsWith('ota pull ')) && command === 'ota stats') {
        f.calls.push(command);
        if (result === 'timeout' || result === 'disconnect') {
          f.link.failure = Error(result === 'timeout' ? 'Remote timed out' : 'USB disconnected');
          throw f.link.failure;
        }
        return 'fetch failed:manifest timeout 0/0 id=' + (result === 'same' ? f.id : '1234abcd');
      }
      return original(key, password, command);
    };
    await assert.rejects(f.session.start(), error => {
      assert.match(error.message, /^Target reported a failed download: failed/);
      assert.equal(error.message.includes('manifest timeout'), result === 'same');
      assert.equal(error.message.includes('Run ota stats on the target for details.'), result !== 'same');
      return true;
    });
    assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
    assert.ok(!f.calls.includes('ota cancel'));
    if (result === 'timeout' || result === 'disconnect')
      assert.ok(f.progress.some(line=>line.startsWith('Target OTA stats unavailable:')));
    if (result === 'disconnect') assert.ok(f.progress.some(line=>line.includes('Could not detach the package: detach failed')));
  }
});

test('reason-less failure on legacy Companion reports immediately without optional retrying stats', async () => {
  const f = await recoveryFixture({state:'idle', pull:api=>{api.setState('failed'); return 'OK';}});
  f.setRecord(null);
  const original = f.link.remoteCommand;
  f.link.remoteCommand = async (key, password, command) => {
    if (f.calls.some(c=>c.startsWith('ota pull ')) && command === 'ota status') {
      f.link.browserRawSource = true; f.link.otaSupported = false;
      return 'OTA | download: failed 0/0 id=' + f.id;
    }
    if (command === 'ota stats') throw Error('optional stats should not be requested');
    return original(key, password, command);
  };
  await assert.rejects(f.session.start(), /Target reported a failed download: failed 0\/0.*Run ota stats on the target for details/);
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
  assert.ok(!f.calls.includes('ota cancel'));
});

test('uncertain pull reports the same-ID failed state without retrying or canceling', async () => {
  const f = await recoveryFixture({state:'idle', pull:api=>{
    api.setState('failed');
    throw Error('Remote command timed out; result is unknown');
  }});
  f.setRecord(null);
  await assert.rejects(f.session.start(), /Target reported a failed download: failed \(manifest timeout\)/);
  assert.ok(f.progress.some(line=>line.includes('Target OTA status:') && line.includes('manifest timeout')));
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
  assert.ok(!f.calls.includes('ota cancel'));
  assert.equal(f.record.phase, 'pull attempted');
});

test('uncertain retry waits for active state instead of treating its previous failure as new', async () => {
  let reads = 0;
  const f = await recoveryFixture({pull:()=>{throw Error('Remote command timed out; result is unknown');},
    status:api=>{
      if (api.calls.some(c=>c.startsWith('ota pull '))) {
        if (++reads === 2) api.setState('active');
        if (reads === 4) api.setState('ready');
      }
    }});
  await f.session.start({resume:true});
  assert.equal(f.record.phase, 'ready');
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
  assert.ok(!f.calls.includes('ota cancel'));
  assert.equal(reads, 4);
});

test('uncertain retry with only its prior failed state reports unknown, not a new failure', async () => {
  const f = await recoveryFixture({pull:()=>{throw Error('Remote command timed out; result is unknown');}});
  await assert.rejects(f.session.start({resume:true}), /Target has not confirmed the update; result is unknown/);
  assert.ok(!f.progress.some(line=>line.startsWith('Target OTA status:')));
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
  assert.ok(!f.calls.includes('ota cancel'));
});

test('uncertain retry reports failed reason after a matching target acknowledgement', async () => {
  const f = await recoveryFixture({pull:()=>{throw Error('Remote command timed out; result is unknown');},
    status:api=>{if (api.calls.some(c=>c.startsWith('ota pull '))) api.link.pullTargetAcknowledged = true;}});
  await assert.rejects(f.session.start({resume:true}), /Target reported a failed download: failed \(manifest timeout\)/);
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
  assert.ok(!f.calls.includes('ota cancel'));
});

test('an acknowledgement arriving during a stale status read does not turn the old failure into a new one', async () => {
  let reads = 0;
  const f = await recoveryFixture({pull:()=>{throw Error('Remote command timed out; result is unknown');},
    status:api=>{
      if (!api.calls.some(c=>c.startsWith('ota pull '))) return;
      if (++reads === 1) api.link.pullTargetAcknowledged = true; // After this status request began.
      if (reads === 2) api.setState('active');
      if (reads === 3) api.setState('ready');
    }});
  await f.session.start({resume:true});
  assert.equal(f.record.phase, 'ready');
  assert.equal(reads, 3);
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
  assert.ok(!f.calls.includes('ota cancel'));
});

test('acknowledgement arriving during the last allowed stale read permits one fresh failure check', async () => {
  let reads = 0;
  const f = await recoveryFixture({pull:()=>{throw Error('Remote command timed out; result is unknown');},
    status:api=>{
      if (api.calls.some(c=>c.startsWith('ota pull ')) && ++reads === 3)
        api.link.pullTargetAcknowledged = true;
    }});
  await assert.rejects(f.session.start({resume:true}), /Target reported a failed download: failed \(manifest timeout\)/);
  assert.equal(reads, 4);
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
  assert.ok(!f.calls.includes('ota cancel'));
});

test('uncertain pull polls idle read-only with a bound and never repeats the pull', async () => {
  for (const eventuallyReady of [true, false]) {
    const f = await recoveryFixture({state:'idle', pull:()=>{throw Error('Remote command timed out; result is unknown');}});
    f.setRecord(null);
    const original = f.link.remoteCommand; let checks = 0;
    f.link.remoteCommand = async (key, password, command) => {
      if (command === 'ota status' && f.calls.some(c=>c.startsWith('ota pull ')) && ++checks === 2 && eventuallyReady)
        f.setState('ready');
      return original(key, password, command);
    };
    if (eventuallyReady) await f.session.start();
    else await assert.rejects(f.session.start(), /Target has not confirmed the update; result is unknown/);
    assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
    assert.ok(!f.calls.includes('ota cancel'));
    assert.equal(checks, 3); // Ready is read once more in the normal poll.
  }
});

test('explicit pull rejection containing timeout is not treated as uncertain', async () => {
  const f = await recoveryFixture({state:'idle', pull:()=>{throw Error('ERR manifest timeout');}});
  f.setRecord(null);
  await assert.rejects(f.session.start(), /^Error: ERR manifest timeout$/);
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
  assert.ok(!f.calls.includes('ota cancel'));
});

test('same-package failed download is offered for recovery and explicitly pulled once without cancellation', async () => {
  for (const options of [{}, {legacyInstall:true, statsFallback:true}]) {
    const f = await recoveryFixture(options);
    assert.equal((await f.session.inspect({allowExisting:true})).download, 'failed');
    assert.ok(!f.calls.includes('serve') && !f.calls.includes('ota cancel'));
    await f.session.start({resume:true});
    assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
    assert.equal(f.calls.filter(c=>c==='serve').length, 1);
    assert.ok(!f.calls.includes('ota cancel'));
    assert.equal(f.record.phase, 'ready');
    assert.equal(f.record.packageHash, f.packageHash);
  }
});

test('failed recovery requires the original exact browser record and explicit resume', async () => {
  const f = await recoveryFixture(), correct = f.record;
  for (const record of [null, {...correct,target:'cc'.repeat(32)}, {...correct,source:'cc'.repeat(32)},
      {...correct,source:undefined}, {...correct,id:'11223344'}, {...correct,packageHash:'00'.repeat(32)},
      {...correct,phase:'prepared'}, {...correct,phase:'install attempted'}]) {
    f.setRecord(record);
    await assert.rejects(f.session.start({resume:true}), /recovery record/);
  }
  f.setRecord(correct);
  await assert.rejects(f.session.start(), /already has another OTA download/);
  f.setState('failed', '1234abcd');
  await assert.rejects(f.session.start({resume:true}), /already has another OTA download/);
  assert.ok(!f.calls.includes('serve') && !f.calls.includes('ota cancel'));
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 0);
});

test('failed recovery stops before pull when the target session changes during discovery', async () => {
  for (const [state,id] of [['idle'], ['active'], ['ready'], ['failed','1234abcd']]) {
    const f = await recoveryFixture({discover:api=>api.setState(state,id || api.id)});
    await assert.rejects(f.session.start({resume:true}), /state changed|another OTA download/);
    assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 0);
    assert.ok(f.calls.includes('stopServing') && !f.calls.includes('ota cancel'));
  }
});

test('failed recovery never repeats a pull whose USB acknowledgement is lost', async () => {
  const f = await recoveryFixture({pull:api=>{
    api.setState('active'); api.link.failure = Error('Companion response timed out; reconnect before retrying');
    throw api.link.failure;
  }});
  await assert.rejects(f.session.start({resume:true}), /Companion response timed out/);
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
  assert.ok(f.calls.includes('stopServing') && !f.calls.includes('ota cancel'));
  assert.equal(f.record.phase, 'pull attempted');
});

test('ready recovery does not serve, discover or pull again', async () => {
  const f = await recoveryFixture({state:'ready'});
  await f.session.start({resume:true});
  assert.ok(!f.calls.includes('serve') && !f.calls.includes('ota ls') && !f.calls.includes('ota cancel'));
  assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 0);
});

test('an explicit replacement is available after all checks and cancels only the observed session once', async () => {
  for (const state of ['active','failed','ready']) {
    const f = await recoveryFixture({state,id:'1234abcd'});
    const result = await f.session.inspect({allowExisting:true, allowReplacement:true});
    assert.equal(result.download, 'replace');
    assert.deepEqual(result.existingDownload, {id:'1234abcd',state});
    assert.ok(!f.calls.includes('serve') && !f.calls.includes('ota cancel'));
    await f.session.start({replace:result.existingDownload});
    assert.equal(f.calls.filter(c=>c==='ota cancel').length, 1);
    assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length, 1);
    assert.ok(f.calls.indexOf('ota cancel') < f.calls.indexOf('serve'));
    assert.equal(f.record.phase, 'ready');
  }
});

test('same-MID downloads without an original record need explicit replacement', async () => {
  const f = await recoveryFixture(); f.setRecord(null);
  const result = await f.session.inspect({allowExisting:true, allowReplacement:true});
  assert.deepEqual(result.existingDownload, {id:f.id,state:'failed'});
  await f.session.start({replace:result.existingDownload});
  assert.equal(f.calls.filter(c=>c==='ota cancel').length, 1);
  assert.equal(f.record.packageHash, f.packageHash);
});

test('replacement never cancels a changed or unidentified download', async () => {
  for (const mutation of [f=>f.setState('active','eeeeeeee'), f=>f.setState('ready','1234abcd'), f=>f.setState('idle')]) {
    const f = await recoveryFixture({id:'1234abcd'});
    const result = await f.session.inspect({allowReplacement:true}); mutation(f);
    await assert.rejects(f.session.start({replace:result.existingDownload}), /state changed/);
    assert.ok(!f.calls.includes('ota cancel') && !f.calls.includes('serve'));
  }
  const f = await recoveryFixture({id:'1234abcd'});
  await assert.rejects(f.session.start({replace:{id:'unknown',state:'failed'}}), /Invalid/);
  assert.equal(f.calls.length,0);
});

test('replacement checks the snapshot once more immediately before cancellation', async () => {
  const f = await recoveryFixture({id:'1234abcd',status:(api,reads)=>{
    if (reads === 5) api.setState('active','eeeeeeee');
  }});
  const result = await f.session.inspect({allowReplacement:true});
  await assert.rejects(f.session.start({replace:result.existingDownload}), /state changed/);
  assert.ok(!f.calls.includes('ota cancel') && !f.calls.includes('serve'));
});

test('unsupported or unanswered USB cancellation stops replacement without serving or retrying', async () => {
  for (const failure of ['unsupported','USB']) {
    const f = await recoveryFixture({id:'1234abcd',cancel:api=>{
      if (failure === 'unsupported') return 'Unknown OTA command';
      api.link.failure = Error('Companion response timed out; reconnect before retrying');
      throw api.link.failure;
    }});
    const result = await f.session.inspect({allowReplacement:true});
    await assert.rejects(f.session.start({replace:result.existingDownload}), /Unknown|Companion response timed out/);
    assert.equal(f.calls.filter(c=>c==='ota cancel').length,1);
    assert.ok(!f.calls.includes('serve'));
    assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length,0);
  }
});

test('explicit cancellation errors never authorize a new pull even when the target reports idle', async () => {
  for (const reply of ['ERR flash erase timeout', 'ERR disconnected while invalidating staged data',
      'flash erase timed out', 'disconnected while invalidating staged data']) {
    const f = await recoveryFixture({id:'1234abcd',cancel:api=>{
      api.setState('idle');
      return reply;
    }});
    const result = await f.session.inspect({allowReplacement:true});
    await assert.rejects(f.session.start({replace:result.existingDownload}),
      error=>error.message.includes(reply));
    assert.equal(f.calls.filter(c=>c==='ota cancel').length,1);
    assert.ok(!f.calls.includes('serve'));
    assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length,0);
  }
});

test('a lost cancel reply always stops replacement and is never retried', async () => {
  for (const cancelled of [true,false]) {
    const f = await recoveryFixture({id:'1234abcd',cancel:api=>{
      if (cancelled) api.setState('idle');
      throw Error('Remote command timed out; result is unknown');
    }});
    const result = await f.session.inspect({allowReplacement:true});
    await assert.rejects(f.session.start({replace:result.existingDownload}), /Remote command timed out/);
    assert.equal(f.calls.filter(c=>c==='ota cancel').length,1);
    assert.ok(!f.calls.includes('serve'));
    assert.equal(f.calls.filter(c=>c.startsWith('ota pull ')).length,0);
    assert.equal(f.record.phase,'replacement cancel attempted');
  }
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
  let saved = {phase:'install attempted', target:'bb'.repeat(32), source:link.sourceKey, id, packageHash, bodyHash};
  const session = new DirectSession(link, 'bb'.repeat(32), '', p, {readJournal:() => saved, journal:r => { saved = r; }});
  assert.equal((await session.inspect({allowExisting:true})).download, 'installed');
  assert.equal(saved.phase, 'installed');
  assert.equal((await session.inspect({allowExisting:true})).download, 'installed');
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
      if(c==='get radio')return radio.split(',').slice(0,4).join(',');if(c==='get tempradio')return radio;
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

test('ambiguous installhash response is refused before serving or pulling', async () => {
  const p=await pkg.parseMota(await pkg.buildFullMota(await image()));
  const calls=[], config='autofetch=off autoinstall=off hops=0', radio='910.525,62.5,7,5,120';
  const link={sourceKey:'aa'.repeat(32),async verifyPeer(){},async localCommand(c){calls.push('local '+c);return c==='get tempradio'||c==='get radio'?radio:c==='get radio.rxps'?'off':config;},
    async remoteCommand(k,pw,c){calls.push(c);if(c==='ota status')return 'hw=board target:12345678 maxblk:1024 no download';if(c==='ota self')return 'base_hash=1122334455667788';if(c==='ver')return 'v1.17.1.9';if(c==='get radio')return radio.split(',').slice(0,4).join(',');if(c==='get tempradio')return radio;if(c==='get radio.rxps')return 'off';if(c==='ota config')return config;if(c==='ota installhash')return 'ERR usage: ota install';throw Error('Unexpected '+c);},
    async serve(){throw Error('must not serve');}};
  const session=new DirectSession(link,'bb'.repeat(32),'',p,{pollMs:1});
  await assert.rejects(session.start(),/Cannot determine whether the target supports/);
  assert(!calls.some(c=>c.startsWith('ota pull')||c==='ota install'||c.startsWith('ota installhash ')));
});

test('older target uses one legacy install after the exact staged ID is confirmed', async () => {
  const bytes=await image(), p=await pkg.parseMota(await pkg.buildFullMota(bytes));
  const target='bb'.repeat(32),source='aa'.repeat(32),id=Buffer.from(p.merkleRoot).toString('hex');
  const packageHash=Buffer.from(await crypto.subtle.digest('SHA-256',p.blob)).toString('hex');
  const bodyHash=Buffer.from(bytes.slice(-48,-40)).toString('hex'), radio='910.525,62.5,7,5,120';
  let installed=false, record={phase:'ready',target,source,id,packageHash}; const calls=[], progress=[];
  const link={sourceKey:source,browserRawSource:true,async verifyPeer(){},async resetRemoteSession(){},
    async localCommand(c){calls.push('local '+c);if(c==='get tempradio'||c==='get radio')return radio;if(c==='get radio.rxps')return 'off';throw Error('Unexpected source command '+c);},
    async remoteCommand(k,pw,c){calls.push(c);
      if(c==='ota status')return 'hw=board target:12345678 maxblk:1024 '+(installed?'no download':'download: ready to install id='+id);
      if(c==='ota self')return 'base_hash='+(installed?bodyHash:'1122334455667788');
      if(c==='ver')return installed?'v1.18.0.1':'v1.17.1.9';
      if(c==='get radio')return radio.split(',').slice(0,4).join(',');if(c==='get tempradio')return radio;
      if(c==='get radio.rxps')return 'off';
      if(c==='ota config')return 'autofetch=off autoinstall=off hops=0';
      if(c==='ota installhash')return 'Unknown OTA command. Type `ota help`.';
      if(c==='ota install'){installed=true;throw Error('Remote command timed out; result is unknown');}
      throw Error('Unexpected target command '+c);
    }};
  const session=new DirectSession(link,target,'',p,{pollMs:1,readJournal:()=>record,journal:r=>{record=r;},progress:s=>progress.push(s)});
  assert.equal((await session.install()).baseHash,bodyHash);
  assert.equal(calls.filter(c=>c==='ota install').length,1);
  assert(!calls.some(c=>c.startsWith('local ota ')));
  assert(progress.some(s=>/cannot bind the full container hash/.test(s)));
  assert.equal(record.phase,'installed');
});

test('an unanswered installhash probe never selects legacy install', async () => {
  const session=new DirectSession({async remoteCommand(){throw Error('Remote command timed out; result is unknown');}},'bb'.repeat(32),'',{});
  await assert.rejects(session.requireInstallHash(),/timed out/);
  assert.notEqual(session.installMode,'legacy');
});

test('an uncertain legacy install is not sent again on a later attempt', async () => {
  const p=await pkg.parseMota(await pkg.buildFullMota(await image()));
  const target='bb'.repeat(32),source='aa'.repeat(32),id=Buffer.from(p.merkleRoot).toString('hex');
  const packageHash=Buffer.from(await crypto.subtle.digest('SHA-256',p.blob)).toString('hex');
  let record={phase:'ready',target,source,id,packageHash},installs=0,session;
  const radio='910.525,62.5,7,5,120';
  const link={sourceKey:source,browserRawSource:true,async verifyPeer(){},
    async localCommand(c){return c==='get radio.rxps'?'off':radio;},
    async remoteCommand(k,pw,c){
      if(c==='ota status')return 'hw=board target:12345678 maxblk:1024 download: ready to install id='+id;
      if(c==='ota self')return 'base_hash=1122334455667788';if(c==='ver')return 'v1.17.1.9';
      if(c==='get radio')return radio.split(',').slice(0,4).join(',');if(c==='get tempradio')return radio;if(c==='get radio.rxps')return 'off';
      if(c==='ota config')return 'autofetch=off autoinstall=off hops=0';
      if(c==='ota installhash')return 'Unknown command';
      if(c==='ota install'){installs++;session.stop();throw Error('Remote command timed out; result is unknown');}
      throw Error('Unexpected '+c);
    }};
  const options={pollMs:1,readJournal:()=>record,journal:r=>{record=r;}};
  session=new DirectSession(link,target,'',p,options);
  await assert.rejects(session.install(),/Stopped/);
  assert.equal(record.phase,'install attempted');
  session=new DirectSession(link,target,'',p,options);
  await assert.rejects(session.install(),/recovery record/);
  assert.equal(installs,1);
});

test('older raw Companion uses saved SELF_INFO and discloses unverified source settings', async () => {
  const p=await pkg.parseMota(await pkg.buildFullMota(await image()));
  const radio='910.525,62.5,7,5,120',progress=[],calls=[];let targetTemp='off';
  const link={sourceKey:'aa'.repeat(32),browserRawSource:true,localCliSupported:false,
    async verifyPeer(){},async savedSourceRadio(){return [910.525,62.5,7,5];},
    async localCommand(c){throw Error('Unexpected source CLI '+c);},
    async remoteCommand(k,pw,c){calls.push(c);
      if(c==='ota status')return 'hw=board target:12345678 maxblk:1024 no download';
      if(c==='ota self')return 'base_hash=1122334455667788';if(c==='ver')return 'v1.17.1.9';
      if(c==='get tempradio')return targetTemp;if(c==='get radio')return radio;
      if(c==='get radio.rxps')return 'off';if(c==='ota config')return 'autofetch=off autoinstall=off hops=0';
      if(c==='ota installhash')return installUsage;throw Error('Unexpected '+c);
    }};
  const session=new DirectSession(link,'bb'.repeat(32),'',p,{progress:s=>progress.push(s)});
  await assert.rejects(session.inspect(),/temporary radio window is required on the target/);
  targetTemp=radio;
  assert.equal((await session.inspect()).download,'idle');
  assert(progress.some(s=>/Active temporary settings and preamble cannot be verified/.test(s)));
  assert(calls.includes('ota config'));
});

test('RXPS on or unavailable never blocks channel verification or requests a setting change', async () => {
  for (const rxps of ['radio.rxps off,level=6,preamble=16,rx=41704,sleep=11882', 'on,65625,60000', 'unknown config: radio.rxps']) {
    for (const secondary of [false, true]) {
      const f = radioPreflight({sourceRxps: rxps, targetRxps: rxps, ...(secondary ? {
        sourceTemp: 'TempRadio inactive', targetTemp: '> off',
        link: {otaRadioProfile: 1, otaTargetProfile: 1, supportsOtaRadio2: true}
      } : {})});
      const result = await f.session.inspectRadios(); assert.deepEqual(result.source, result.target);
      assert.ok(!f.calls.some(([, command]) => /radio.rxps/.test(command)));
    }
  }
});

test('missing framed CLI getters on older Companions retain the source-update hint', async () => {
  for (const changes of [{sourceTemp:'Unknown command'}]) {
    const f = radioPreflight(changes);
    await assert.rejects(f.session.inspectRadios(), error => error.sourceUpdateRequired === 'companion');
  }
});

async function rebootFixture({sourceKind = 'guarded', oldTarget = false, differentSaved = false, lostReply = false, noControls = false, savedTargetOverride = ''} = {}) {
  const bytes = await image(), p = await pkg.parseMota(await pkg.buildFullMota(bytes));
  const target = 'bb'.repeat(32), source = 'aa'.repeat(32), password = 'admin';
  const id = Buffer.from(p.merkleRoot).toString('hex'), packageHash = Buffer.from(await crypto.subtle.digest('SHA-256', p.blob)).toString('hex');
  const bodyHash = Buffer.from(bytes.slice(-48, -40)).toString('hex');
  const savedSource = '910.525,62.5,7,5', savedTarget = savedTargetOverride || (differentSaved ? '907.525,125,8,5' : savedSource);
  const updateRadio = sourceKind === 'legacy' ? savedSource : '909.950,250,5,5';
  const profile2 = sourceKind === 'radio2' || sourceKind === 'repeater';
  let primary = profile2 ? null : updateRadio, secondary = profile2, installed = false, owned = true, session;
  let record = {phase:'ready', target, source, id, packageHash};
  const calls = [];
  function reachable() {
    const active = link.otaRadioProfile === 1 ? updateRadio : primary || savedSource;
    if (active !== (installed ? savedTarget : updateRadio)) {
      session.stop(); throw Error('RF channel mismatch after reboot');
    }
  }
  const link = {sourceKey:source, browserRawSource:sourceKind !== 'repeater', otaSupported:sourceKind !== 'legacy',
    localCliSupported:true, supportsOtaRadio2:profile2, otaRadioProfile:profile2 ? 1 : 0, otaTargetProfile:profile2 ? 1 : 0,
    async verifyPeer(k, pw) { assert.equal(k, target); assert.equal(pw, password); reachable(); owned = true; calls.push('proof'); },
    async resetRemoteSession(k, pw) {
      if (sourceKind === 'repeater') assert.equal(owned, true, 'reset cannot reuse a released repeater session');
      return this.verifyPeer(k, pw);
    },
    async closeRadioSession() { owned = false; calls.push('close'); },
    async selectOtaRadio(profile) { assert.equal(owned, false); this.otaRadioProfile = profile; calls.push('select ' + profile); },
    async applyTemporaryRadio(command) { return this.localCommand(command); },
    async localCommand(command) {
      calls.push('local ' + command);
      if (command === 'get radio') return savedSource;
      if (command === 'get tempradio') return primary ? 'TempRadio active: ' + primary + ' 600s left,preamble=128' : 'TempRadio inactive';
      if (command === 'get tempradio2') return secondary ? '> ' + updateRadio + ',rxtx,128; 120 min left' : '> off';
      if (command === 'set tempradio2 off') { secondary = false; return 'OK'; }
      if (command === 'get radio2.status') return '> rxtx; RX=0,0 TX=0,0 switches=0 errors=0 max=0us preamble=32,128';
      if (command === 'get radio.rxps') return 'off';
      if (command === 'ota config') return 'autofetch=off autoinstall=off hops=0';
      if (command === 'ota stats') return 'fetch idle';
      if (command === 'ota folder') return 'folder=off';
      if (command.startsWith('tempradio ')) {
        assert.equal(installed, true, 'retuned before the install command');
        assert.equal(command, 'tempradio ' + savedTarget + ',10,128');
        primary = savedTarget;
        if (lostReply) throw Error('Command timed out; result is unknown');
        return 'OK';
      }
      throw Error('Unexpected source command: ' + command);
    },
    async remoteCommand(k, pw, command) {
      assert.equal(k, target); assert.equal(pw, password); reachable(); calls.push('remote ' + command);
      if (command === 'get radio') return savedTarget;
      if (command === 'get tempradio') return profile2 ? '> off' : '> ' + updateRadio + ',128';
      if (command === 'get tempradio2' || command === 'get radio2.status') return this.localCommand(command);
      if (command === 'get radio.rxps' || command === 'ota config') return this.localCommand(command);
      if (command === 'ota status') return 'hw=board target:12345678 maxblk:1024 ' + (installed ? 'no download' : 'download: ready to install id=' + id);
      if (command === 'ota self') return 'base_hash=' + (installed ? bodyHash : '1122334455667788');
      if (command === 'ver') return installed ? 'v1.18.0.1' : 'v1.17.1.9';
      if (command === 'ota installhash') return oldTarget ? 'Unknown OTA command. Type `ota help`.' : installUsage;
      if (command === (oldTarget ? 'ota install' : 'ota installhash ' + await installHash(p.blob))) {
        installed = true;
        if (lostReply) throw Error('Remote command timed out; result is unknown');
        return 'OK';
      }
      throw Error('Unexpected target command: ' + command);
    }
  };
  if (noControls) delete link.closeRadioSession;
  session = new DirectSession(link, target, password, p, {pollMs:1, readJournal:() => record, journal:r => {record = r;}});
  return {session, link, calls, installed:() => installed, record:() => record, savedTarget};
}

for (const sourceKind of ['legacy', 'guarded', 'radio2', 'repeater']) for (const oldTarget of [false, true]) {
  test(sourceKind + ' source verifies reboot on the saved channel of ' + (oldTarget ? 'old' : 'new') + ' target', async () => {
    for (const differentSaved of [false, true]) {
      const f = await rebootFixture({sourceKind, oldTarget, differentSaved, lostReply:differentSaved});
      await f.session.install();
      assert.equal(f.record().phase, 'installed');
      assert.equal(f.calls.filter(c => c === 'remote ota install' || c.startsWith('remote ota installhash ')).length, 1);
      const writes = f.calls.filter(c => c.startsWith('local tempradio '));
      const expectedWrites = sourceKind === 'guarded' || differentSaved ? 1 : 0;
      assert.equal(writes.length, expectedWrites);
      assert.equal(f.calls.filter(c => c === 'proof').length, 2, 'fresh identity proof after reboot');
      assert(!f.calls.some(c => c.startsWith('local set radio ')));
      if (sourceKind !== 'legacy' || differentSaved) assert.equal(f.link.otaRadioProfile, 0);
    }
  });
}

test('cannot install if this source cannot reach the target saved channel afterward', async () => {
  const f = await rebootFixture({noControls:true});
  await assert.rejects(f.session.install(), /cannot automatically reconnect/);
  assert.equal(f.installed(), false); assert.equal(f.record().phase, 'ready');
});

test('malformed saved target channel is rejected before installation', async () => {
  const f = await rebootFixture(), remote = f.link.remoteCommand.bind(f.link);
  f.link.remoteCommand = (key, password, command) => command === 'get radio' ? '> unknown config: radio' : remote(key, password, command);
  await assert.rejects(f.session.install(), /unknown config/);
  assert.equal(f.installed(), false); assert.equal(f.record().phase, 'ready');
});


test('source radio2 stays selected when the reboot channel already matches', async () => {
  const f = await rebootFixture({sourceKind:'radio2', savedTargetOverride:'909.950,250,5,5'});
  await f.session.install();
  assert.equal(f.record().phase, 'installed');
  assert.equal(f.link.otaRadioProfile, 1);
  assert(!f.calls.includes('close'));
});

test('overlapping temporary source radio2 is ended before the primary reconnect', async () => {
  const f = await rebootFixture({sourceKind:'radio2', savedTargetOverride:'909.960,125,8,5'});
  await f.session.install();
  assert.equal(f.record().phase, 'installed');
  assert.equal(f.calls.filter(c => c === 'local set tempradio2 off').length, 1);
  assert(f.calls.indexOf('local set tempradio2 off') < f.calls.indexOf('local tempradio 909.960,125,8,5,10,128'));
  assert(!f.calls.includes('local set radio2 off'));
});

test('reusing a stopped Session waits for its old read and detach before serving a new package', async () => {
  const events=[];let read,detach,reads=0,stops=0;
  const waitingRead=new Promise(resolve=>{read=resolve;}),waitingDetach=new Promise(resolve=>{detach=resolve;});
  const id='01234567';
  const link={sourceKey:'aa'.repeat(32),async serve(){events.push('serve');},async stopServing(){
    events.push('stop');if(++stops===1)await waitingDetach;
  }};
  const session=new DirectSession(link,'bb'.repeat(32),'',{merkleRoot:Uint8Array.from([1,35,69,103]),blob:new Uint8Array()});
  session.inspect=async()=>({download:'active'});
  session.readDownload=async()=>++reads===1?waitingRead:{id,state:'ready',status:'ready'};
  const first=session.start({resume:true});first.catch(()=>{});
  while(!reads)await new Promise(resolve=>setImmediate(resolve));
  const stopping=session.stop(),second=session.start({resume:true});
  await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(events,['serve','stop']);
  read({id,state:'active',status:'active'});
  await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(events,['serve','stop']);
  detach();await stopping;await assert.rejects(first,/Stopped/);await second;
  assert.deepEqual(events,['serve','stop','serve','stop']);
  assert.equal(stops,2);assert.equal(session.serving,false);assert.equal(session.stopServingTask,null);
});

test('a running Session cannot start another package over its active source', async () => {
  let finish,reads=0;
  const pending=new Promise(resolve=>{finish=resolve;});
  const link={sourceKey:'aa'.repeat(32),async serve(){},async stopServing(){}};
  const session=new DirectSession(link,'bb'.repeat(32),'',{merkleRoot:Uint8Array.from([1,35,69,103]),blob:new Uint8Array()});
  session.inspect=async()=>({download:'active'});
  session.readDownload=async()=>{reads++;return pending;};
  const active=session.start({resume:true});active.catch(()=>{});
  while(!reads)await new Promise(resolve=>setImmediate(resolve));
  await assert.rejects(session.start({resume:true}),/already running/);
  await session.stop();finish({id:'01234567',state:'active',status:'active'});
  await assert.rejects(active,/Stopped/);
});

test('Stop during its pending serve detaches on successful handoff without waiting for a transfer read', async () => {
  const events=[];let attach,entered;
  const attaching=new Promise(resolve=>{attach=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  const link={sourceKey:'aa'.repeat(32),async serve(){events.push('attach');entered();await attaching;events.push('attached');},
    async stopServing(){events.push('detach');}};
  const session=new DirectSession(link,'bb'.repeat(32),'',{merkleRoot:Uint8Array.from([1,35,69,103]),blob:new Uint8Array()});
  session.inspect=async()=>({download:'active'});
  session.readDownload=async()=>{throw Error('Stop must not enter a status/quiet transfer read');};
  const active=session.start({resume:true});active.catch(()=>{});await started;
  const stopped=session.stop();assert.equal(session.serving,false);
  assert.equal(session.stop(),stopped,'Stop shares the same handoff/detach operation');
  await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(events,['attach']);
  attach();await stopped;
  assert.deepEqual(events,['attach','attached','detach']);
  await assert.rejects(active,/Stopped/);assert.equal(session.serving,false);
});

test('a rejected pending serve never authorizes detaching another source owner', async () => {
  let reject,entered,detaches=0;
  const attaching=new Promise((_resolve,fail)=>{reject=fail;}),started=new Promise(resolve=>{entered=resolve;});
  const link={sourceKey:'aa'.repeat(32),serving:true,async serve(){entered();await attaching;},async stopServing(){detaches++;}};
  const session=new DirectSession(link,'bb'.repeat(32),'',{merkleRoot:Uint8Array.from([1,35,69,103]),blob:new Uint8Array()});
  session.inspect=async()=>({download:'active'});
  const active=session.start({resume:true});active.catch(()=>{});await started;
  const stopped=session.stop();reject(Error('Another package already owns the source'));
  await stopped;await assert.rejects(active,/Another package/);
  assert.equal(detaches,0);assert.equal(link.serving,true);assert.equal(session.serving,false);
});

test('Stop racing normal final cleanup shares one detach request', async () => {
  let finish,entered,detaches=0;
  const detaching=new Promise(resolve=>{finish=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  const link={sourceKey:'aa'.repeat(32),async serve(){},async stopServing(){detaches++;entered();await detaching;}};
  const session=new DirectSession(link,'bb'.repeat(32),'',{merkleRoot:Uint8Array.from([1,35,69,103]),blob:new Uint8Array()});
  session.inspect=async()=>({download:'active'});
  session.readDownload=async()=>({id:'01234567',state:'ready',status:'ready'});
  const active=session.start({resume:true});await started;
  const stopped=session.stop();finish();await stopped;await active;
  assert.equal(detaches,1);assert.equal(session.serving,false);
});

test('Stop wakes the normal twenty-second transfer poll and retains the target download', async () => {
  const commands=[];let detaches=0;
  const link={sourceKey:'aa'.repeat(32),async serve(){},async stopServing(){detaches++;},
    async remoteCommand(_key,_password,command){commands.push(command);throw Error('No target mutation is permitted');}};
  const session=new DirectSession(link,'bb'.repeat(32),'',{merkleRoot:Uint8Array.from([1,35,69,103]),blob:new Uint8Array()},
    {pollMs:20000});
  delete session.pollWake; // Old live sessions can adopt the new methods safely.
  session.inspect=async()=>({download:'active'});
  session.readDownload=async()=>({id:'01234567',state:'active',status:'active'});
  const active=session.start({resume:true});active.catch(()=>{});
  while(typeof session.pollWake!=='function')await new Promise(resolve=>setImmediate(resolve));
  const start=Date.now();await session.stop();await assert.rejects(active,/Stopped/);
  assert.ok(Date.now()-start<250,'Stop must not wait for the twenty-second timer');
  assert.equal(detaches,1);assert.deepEqual(commands,[]);assert.equal(session.pollWake,null);
});
