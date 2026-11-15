'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {StreamDecoder, SeederData, RepeaterTransport, responseFrame, rawDeflate} = require('../docs/_javascript/lora_ota_serial.js');
const text = new TextEncoder();
const decode = bytes => new TextDecoder().decode(bytes);
const request = (op, args = []) => {
  const a = [77, 83, op, ...args];
  return Uint8Array.from([...a, a.slice(2).reduce((x, y) => x ^ y, 0)]);
};
const makeInfo = () => ({merkleRoot:Uint8Array.of(1,2,3,4), targetId:0x12345678,
  fwVersion:0x01020304, codecId:0, flags:1, payloadOffset:8, payloadSize:24, blockSize:16});

test('stream handles fragmentation, text, and bad checksums without treating requests as text', () => {
  const d = new StreamDecoder();
  const wire = request(3, [0,5,0,0,0,3,0]);
  assert.deepEqual(d.feed(text.encode('hello\r\n')), [{type:'line',line:'hello'}]);
  assert.deepEqual(d.feed(wire.subarray(0,4), 100), []);
  const result = d.feed(wire.subarray(4), 200);
  assert.equal(result[0].op, 3); assert.deepEqual(Array.from(result[0].args), [0,5,0,0,0,3,0]);
  const bad = request(1); bad[3] ^= 1;
  assert.deepEqual(d.feed(bad), []);
  assert.equal(d.feed(text.encode('after\n'))[0].line, 'after');
  assert.deepEqual(d.feed(Uint8Array.of(77,83), 1000), []);
  assert.equal(d.feed(text.encode('fresh\n'), 4001)[0].line, 'fresh');
});

test('folder metadata and reads match the firmware wire format', async () => {
  const data = Uint8Array.from({length:32}, (_,i) => i), s = new SeederData(makeInfo(), data);
  const [status, meta] = await s.request(2, Uint8Array.of(0)), d = new DataView(meta.buffer);
  assert.equal(status, 0); assert.equal(meta.length, 38); assert.deepEqual(Array.from(meta.slice(0,4)), [1,2,3,4]);
  assert.equal(d.getUint32(4,true),0x12345678); assert.equal(d.getUint32(8,true),0x01020304);
  assert.equal(d.getUint32(14,true),32); assert.equal(d.getUint32(18,true),205);
  assert.equal(d.getUint32(22,true),2); assert.equal(d.getUint32(26,true),8);
  assert.equal(d.getUint32(30,true),24); assert.equal(meta[34],4); assert.equal(meta[35],1);
  assert.deepEqual(await s.request(3, Uint8Array.of(0,5,0,0,0,3,0)), [0,Uint8Array.of(5,6,7)]);
  assert.equal((await s.request(3, Uint8Array.of(0,31,0,0,0,3,0)))[0],1);
  assert.equal((await s.request(9, Uint8Array.of(0,0,0,0,0,0,0)))[0],1);
  assert.equal(responseFrame(1,0,Uint8Array.of(1)).reduce((a,b) => a^b,0),0);
});

test('optional compressed blocks are bounded and cached', async () => {
  let calls = 0;
  const s = new SeederData(makeInfo(), new Uint8Array(32), async () => { calls++; return Uint8Array.of(9,8,7); });
  assert.deepEqual(await s.request(9, Uint8Array.of(0,0,0,0,0,0,0)),[0,Uint8Array.of(3,0)]);
  assert.deepEqual(await s.request(9, Uint8Array.of(0,0,0,1,0,2,0)),[0,Uint8Array.of(3,0,8,7)]);
  assert.equal(calls,1);
  assert.equal((await s.request(9,Uint8Array.of(0,0,0,2,0,2,0)))[0],1);
});

test('browser raw deflate produces a firmware-compatible raw stream', async () => {
  const original = text.encode('firmware block '.repeat(80));
  let compressed;
  try { compressed = await rawDeflate(original); }
  catch (_) { return; } // Older browsers use the ordinary uncompressed path.
  assert.deepEqual(require('node:zlib').inflateRawSync(compressed), Buffer.from(original));
});

test('clock check reads only and refuses an unsynchronized source', async () => {
  const format = ms => { const n=new Date(ms); return `${n.getUTCHours()}:${String(n.getUTCMinutes()).padStart(2,'0')} - ${n.getUTCDate()}/${n.getUTCMonth()+1}/${n.getUTCFullYear()} UTC`; };
  for (const offset of [-120000, 720000]) {
    const calls=[], link=new RepeaterTransport({});
    link.localCommand=async command => { calls.push(command); return format(Date.now()+offset); };
    await assert.rejects(link.checkClock(),/Source clock is out of sync/);
    assert.deepEqual(calls,['clock']);
    assert.equal(link.clockChecked,false);
  }
  const calls=[], link=new RepeaterTransport({});
  link.localCommand=async command => { calls.push(command); return format(Date.now()); };
  await link.checkClock();
  assert.equal(link.clockChecked,true);
  assert.deepEqual(calls,['clock']);
});

test('minute-boundary clock retry requires a fresh in-range reply', async () => {
  const savedNow=Date.now, fixed=Date.UTC(2026,10,10,19,47,0); Date.now=()=>fixed;
  try {
    const link=new RepeaterTransport({}), replies=['19:46 - 10/11/2026 UTC','19:47 - 10/11/2026 UTC'];
    let reads=0; link.localCommand=async command=>{assert.equal(command,'clock');reads++;return replies.shift();};
    await link.checkClock(); assert.equal(reads,2); assert.equal(link.clockChecked,true);
    const originalTimer=globalThis.setTimeout;let staleReads=0;
    globalThis.setTimeout=(fn,_ms,...args)=>originalTimer(fn,0,...args);
    try {
      const stale=new RepeaterTransport({});stale.localCommand=async()=>{staleReads++;return '19:46 - 10/11/2026 UTC';};
      await assert.rejects(stale.checkClock(),/out of sync/);assert.equal(staleReads,6);assert.equal(stale.clockChecked,false);
    } finally { globalThis.setTimeout=originalTimer; }
    const future=new RepeaterTransport({});let futureReads=0;
    future.localCommand=async()=>{futureReads++;return '20:00 - 10/11/2026 UTC';};
    await assert.rejects(future.checkClock(),/out of sync/);assert.equal(futureReads,1);assert.equal(future.clockChecked,false);
    for(const reply of ['19:47:00 - 10/11/2026 UTC','19:47 - 31/11/2026 UTC',
      '19:47 - 10/11/2026 UTC 19:47 - 10/11/2026 UTC']) {
      const invalid=new RepeaterTransport({});invalid.localCommand=async()=>reply;
      await assert.rejects(invalid.checkClock(),/Cannot verify|invalid UTC/);assert.equal(invalid.clockChecked,false);
    }
  } finally { Date.now=savedNow; }
});

class FakePort {
  constructor() { this.queue = []; this.pending = null; this.frames = []; this.commands = []; this.canceled = false; this.folder = false; }
  push(value) { if (this.pending) { const p=this.pending; this.pending=null; p({value,done:false}); } else this.queue.push(value); }
  async open() {
    this.readable = {getReader:() => ({read:() => this.queue.length ? Promise.resolve({value:this.queue.shift(),done:false}) : new Promise(r => {this.pending=r;}),
      cancel:async () => {this.canceled=true; if(this.pending){this.pending({done:true});this.pending=null;}}, releaseLock(){}})};
    this.writable = {getWriter:() => ({write:async bytes => this.write(bytes), close:async()=>{},releaseLock(){}})};
  }
  async write(bytes) {
    if (bytes[0] === 109 && bytes[1] === 115) { this.frames.push(bytes); return; }
    const cmd = decode(bytes).trim(); this.commands.push(cmd);
    const source = 'a'.repeat(64), target = 'b'.repeat(64);
    let reply = ({'get public.key':source, 'ota.peer status':'OTApeer v=1 session=0 busy=0 peers=1',
      'ota.peer capabilities':'OTApeer known_repeater=1 routed_path=1', 'ota.peer path':'OTApeer path=off',
      'ota folder on':'OK folder attached', 'ota folder off':'OK folder detached', 'ota.peer close':'OTApeer closed'})[cmd];
    if (cmd === 'ota.peer status' && this.peerStatus) reply=this.peerStatus;
    if (cmd === 'ota.peer capabilities' && this.peerCaps) reply=this.peerCaps;
    if (cmd === 'ota folder') reply = 'folder='+(this.folder?'on':'off')+' serving=0:';
    if (cmd === 'ota folder on') this.folder = true;
    if (cmd === 'ota folder off') this.folder = false;
    if (cmd === 'clock') { const n=new Date(); reply = `${n.getUTCHours()}:${String(n.getUTCMinutes()).padStart(2,'0')} - ${n.getUTCDate()}/${n.getUTCMonth()+1}/${n.getUTCFullYear()} UTC`; }
    if (cmd.startsWith('ota.peer path ')) reply='OTApeer path='+cmd.slice('ota.peer path '.length);
    if (cmd.startsWith('ota.peer acl ') || cmd.startsWith('ota.peer cmd.acl ') || cmd.startsWith('ota.peer login.repeater ') || cmd.startsWith('ota.peer cmd ')) reply='OTApeer job=01020304 state=pending';
    if (cmd === 'ota.peer result') reply='OTApeer job=01020304 state=ok bytes=2 off=0 data=4F4B';
    if (cmd === 'ota.peer peers 0') reply=`OTApeer peer=0 key=${target} type=2 rssi=-45 snr=8`;
    if (this.replies && Object.hasOwn(this.replies,cmd)) reply=this.replies[cmd];
    if (!reply) throw new Error('unexpected test command: '+cmd);
    if (this.drop === cmd) { this.drop = null; return; }
    queueMicrotask(() => {
      if (this.interleave && cmd === 'ota.peer result') this.push(request(1));
      const echo = cmd.startsWith('ota.peer login.repeater ') ? cmd.replace(/\S+$/, '******') : cmd;
      this.push(text.encode(echo+'\r\n-> '+reply+'\r\n'));
    });
  }
  async close() { this.closed=true; }
}

test('a lost local USB reply invalidates the stream before another command is sent', async () => {
  const port = new FakePort(), link = new RepeaterTransport(port, {knownRepeater:'b'.repeat(64), openSettle:0, commandTimeout:20});
  await link.open(); port.drop = 'ota folder';
  await assert.rejects(link.localCommand('ota folder'), /timed out/);
  const count = port.commands.length;
  try {
    await assert.rejects(link.localCommand('ota.peer status'), /timed out|reconnect/);
    assert.equal(port.commands.length, count);
    assert.ok(link.failure);
  } finally { await link.close(); }
});

test('only explicit missing Repeater OTA capabilities request a firmware update', async () => {
  for(const [status,needed] of [['Unknown command',true],['ERR unsupported',true],['broken response',false],['OTApeer v=1 session=1 busy=0 peers=0',false]]) {
    const port=new FakePort();port.peerStatus=status;const link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),aclOnly:true,openSettle:0});
    await assert.rejects(()=>link.open(),e=>(e.sourceUpdateRequired==='repeater')===needed);
  }
  for(const [caps,needed] of [['Unknown command',true],['ERR use ota.peer status|acl KEY',true],['OTApeer known_repeater=0',true],['invalid response',false]]) {
    const port=new FakePort();port.peerCaps=caps;const link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),aclOnly:true,openSettle:0});
    await assert.rejects(()=>link.open(),e=>(e.sourceUpdateRequired==='repeater')===needed);
  }
});

test('source folder reports missing firmware without mislabeling transient errors', async () => {
  const port=new FakePort(), link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),aclOnly:true,openSettle:0});await link.open();
  for(const reply of ['Unknown command','ERR not built with OTA_FOLDER_SERIAL']) {
    port.replies={'ota folder on':reply};await assert.rejects(()=>link.localCommand('ota folder on'),e=>e.sourceUpdateRequired==='repeater');
  }
  port.replies={'ota folder on':'ERR busy'};assert.equal(await link.localCommand('ota folder on'),'ERR busy');
  await link.close();
});

for (const status of ['OTApeer v=1 session=1 busy=0 peers=1', 'OTApeer v=1 session=0 busy=1 peers=0'])
  test('leaves an existing peer session or job untouched: '+status, async () => {
    const port=new FakePort();port.peerStatus=status;
    const link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),aclOnly:true,openSettle:0});
    await assert.rejects(link.open(),/already has an OTA peer session or job/);
    assert.equal(port.closed,true);
    assert.equal(port.commands.includes('ota.peer close'),false);
  });

test('connecting without starting a peer job does not close one', async () => {
  const port=new FakePort(),link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),aclOnly:true,openSettle:0});
  await link.open();await link.close();
  assert.equal(port.commands.includes('ota.peer close'),false);
});

test('blank password uses repeater ACL without selecting ACL mode', async () => {
  const port = new FakePort(), target = 'b'.repeat(64);
  const link = new RepeaterTransport(port, {knownRepeater:target, openSettle:0, pollMs:0});
  await link.open();
  assert.equal(await link.verifyPeer(target, ''), target);
  assert.equal(await link.remoteCommand(target, '', 'get tx'), 'OK');
  assert.ok(port.commands.includes('ota.peer acl ' + target));
  assert.ok(port.commands.includes('ota.peer cmd.acl ' + target + ' get tx'));
  assert.equal(port.commands.some(cmd => cmd.startsWith('ota.peer login')), false);
  await link.close();
});

test('nonempty password keeps repeater login and command flow', async () => {
  const port = new FakePort(), target = 'b'.repeat(64);
  const link = new RepeaterTransport(port, {knownRepeater:target, openSettle:0, pollMs:0});
  await link.open();
  assert.equal(await link.verifyPeer(target, 'example'), target);
  assert.equal(await link.remoteCommand(target, 'example', 'get tx'), 'OK');
  assert.ok(port.commands.includes('ota.peer login.repeater ' + target + ' example'));
  assert.ok(port.commands.includes('ota.peer cmd ' + target + ' get tx'));
  assert.equal(port.commands.some(cmd => cmd.startsWith('ota.peer acl ') || cmd.startsWith('ota.peer cmd.acl ')), false);
  await link.close();
});

test('USB transport interleaves admin and folder traffic and detaches on close', async () => {
  const port = new FakePort(), target = 'b'.repeat(64);
  const link = new RepeaterTransport(port, {knownRepeater:target, aclOnly:true, openSettle:0, pollMs:0});
  await link.open(); assert.equal(link.sourceKey,'a'.repeat(64));
  assert.equal(await link.verifyPeer(target,''),target);
  assert.equal(await link.remoteCommand(target,'','get tx'),'OK');
  await link.setPath(['AABBCC']);
  await link.serve(makeInfo(), new Uint8Array(32));
  port.interleave = true;
  assert.equal(await link.remoteCommand(target,'','get tx'),'OK');
  assert.deepEqual(Array.from(port.frames.at(-1)),Array.from(responseFrame(1,0,Uint8Array.of(1))));
  await link.close();
  assert.ok(port.commands.includes('ota folder off')); assert.ok(port.commands.includes('ota.peer close'));
  assert.equal(port.canceled,true); assert.equal(port.closed,true);
});

test('rejects invalid peer identities, commands and folder geometry', async () => {
  assert.throws(() => new RepeaterTransport({}, {aclOnly:true}), /complete known repeater key/);
  assert.throws(() => new SeederData({...makeInfo(),payloadOffset:30}, new Uint8Array(32)), /Invalid verified/);
  const port = new FakePort(), target='b'.repeat(64), link=new RepeaterTransport(port,{knownRepeater:target,aclOnly:true,openSettle:0});
  await link.open();
  await assert.rejects(link.remoteCommand('c'.repeat(64),'','get tx'), /differs/);
  await assert.rejects(link.remoteCommand(target,'','get tx\nreboot'), /one nonempty line/);
  await link.close();
});

test('does not replace an existing source folder', async () => {
  const port=new FakePort(), link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),aclOnly:true,openSettle:0});
  await link.open(); port.folder=true;
  await assert.rejects(link.serve(makeInfo(),new Uint8Array(32)),/already has an attached/);
  await link.close(); assert.equal(port.folder,true); assert.equal(port.commands.includes('ota folder off'),false);
});

test('accepts masked login echoes while retaining a password session', async () => {
  const port=new FakePort(), target='b'.repeat(64), link=new RepeaterTransport(port,{knownRepeater:target,openSettle:0});
  await link.open(); assert.equal(await link.verifyPeer(target,'secret'),target);
  assert.equal(await link.remoteCommand(target,'secret','get tx'),'OK');
  assert.equal(port.commands.filter(x=>x.startsWith('ota.peer login.repeater ')).length,1);
  await link.close();
});

for (const aclOnly of [true, false]) test('re-proves a rebooted target once while preserving folder serving ('+(aclOnly?'ACL':'password')+')', async () => {
  const port=new FakePort(), target='b'.repeat(64), password=aclOnly?'':'secret';
  const link=new RepeaterTransport(port,{knownRepeater:target,aclOnly,openSettle:0});
  await link.open(); await link.verifyPeer(target,password); await link.serve(makeInfo(),new Uint8Array(32));
  const loginPrefix=aclOnly?'ota.peer acl ':'ota.peer login.repeater ';
  assert.equal(port.commands.filter(c=>c.startsWith(loginPrefix)).length,1);
  await link.resetRemoteSession(target,password);
  assert.equal(port.commands.filter(c=>c.startsWith(loginPrefix)).length,2);
  assert.equal(link.authenticatedKey,target); assert.equal(link.serving,true); assert.equal(port.folder,true);
  assert.equal(port.commands.filter(c=>c==='ota folder off').length,0);
  await link.remoteCommand(target,password,'get tx');
  assert.equal(port.commands.filter(c=>c.startsWith(loginPrefix)).length,2);
  await link.close(); assert.equal(port.commands.filter(c=>c==='ota folder off').length,1);
});

test('lost peer close acknowledgment clears cached authentication', async () => {
  const port=new FakePort(), target='b'.repeat(64);
  const link=new RepeaterTransport(port,{knownRepeater:target,openSettle:0,commandTimeout:20});
  await link.open(); await link.verifyPeer(target,'secret'); port.drop='ota.peer close';
  await assert.rejects(link.resetRemoteSession(target,'secret'),/timed out/);
  assert.equal(link.authenticatedKey,null); assert.equal(link.lastPeerUse,0);
  await link.close();
});

test('lost attach acknowledgement never detaches a potentially external folder', async () => {
  const port=new FakePort(), link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),aclOnly:true,openSettle:0,commandTimeout:20});
  await link.open(); port.drop='ota folder on';
  await assert.rejects(link.serve(makeInfo(),new Uint8Array(32)),/attachment is unconfirmed/);
  assert.equal(link.folderUnknown,true); assert.ok(link.seeder);
  await link.close(); assert.equal(port.folder,true); assert.equal(port.commands.includes('ota folder off'),false);
});

test('lost detach acknowledgement requires a fresh USB stream before readback', async () => {
  const port=new FakePort(), link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),aclOnly:true,openSettle:0,commandTimeout:20});
  await link.open(); await link.serve(makeInfo(),new Uint8Array(32)); port.drop='ota folder off';
  await assert.rejects(link.stopServing(), /detach is unconfirmed/);
  assert.equal(link.serving,false); assert.equal(port.folder,false); assert.ok(link.failure);
  assert.equal(port.commands.at(-1),'ota folder off');
  await link.close(); assert.equal(port.commands.filter(c=>c==='ota folder off').length,1);
});


test('OTA radio selection is idle, confirmed and reset on USB close', async () => {
  const link=new RepeaterTransport({});link.running=true;
  let profile='radio';const calls=[];
  link.localCommand=async command=>{
    calls.push(command);
    if(command==='ota.peer close')return 'OTApeer closed';
    if(command==='ota.peer radio')return 'OTApeer radio='+profile;
    assert.ok(['ota.peer radio radio','ota.peer radio radio2'].includes(command));
    profile=command.slice('ota.peer radio '.length);return 'OTApeer radio='+profile;
  };
  assert.equal(await link.probeOtaRadio(),true);assert.deepEqual(calls,['ota.peer radio']);
  link.ownsPeer=true;link.authenticatedKey='b'.repeat(64);
  await assert.rejects(()=>link.selectOtaRadio(1),/Close the OTA session/);
  link.serving=true;await assert.rejects(()=>link.closeRadioSession(),/Stop the transfer/);
  link.serving=false;await link.closeRadioSession();assert.equal(link.ownsPeer,false);assert.equal(link.authenticatedKey,null);
  await link.selectOtaRadio(1);assert.equal(link.otaRadioProfile,1);
  await link.close();assert.equal(profile,'radio');assert.equal(link.otaRadioProfile,0);
  assert.deepEqual(calls.slice(-3),['ota.peer close','ota.peer radio radio','ota.peer radio']);
});

test('only explicit unsupported replies permit primary-only OTA profile fallback', async () => {
  for(const reply of ['Unknown command','ERR unsupported','ERR use ota.peer status|acl KEY']){
    const link=new RepeaterTransport({});link.localCommand=async()=>reply;
    assert.equal(await link.probeOtaRadio(),false);
    await link.selectOtaRadio(0);await assert.rejects(()=>link.selectOtaRadio(1),/cannot select/);
  }
  const link=new RepeaterTransport({});link.localCommand=async()=> 'OTApeer invalid';
  await assert.rejects(()=>link.probeOtaRadio(),/Cannot confirm/);
});

test('USB failures keep their original object and redact login keys and password arguments', async () => {
  for(const command of ['ota.peer login.repeater '+'b'.repeat(64)+' PRIVATE_PASS','ota.peer result 128']){
    const link=new RepeaterTransport({}, {commandTimeout:5});link.running=true;link.writer={write:async()=>{}};
    await assert.rejects(link.localCommand(command),error=>{
      assert.equal(error,link.failure);assert.equal(error.message,'Repeater command timed out; reconnect before retrying; its result is unknown');assert.equal(error.otaEndpoint,'USB Repeater');assert.equal(error.otaTimeoutMs,5);
      assert.equal(error.otaCommand,command.startsWith('ota.peer login')?'ota.peer login.repeater <arguments hidden>':'ota.peer result <arguments hidden>');
      assert(!JSON.stringify(error).includes('PRIVATE_PASS'));assert(!JSON.stringify(error).includes('b'.repeat(64)));return true;
    });link.running=false;
  }
});
test('temporary radio setter failures identify the setting but hide its arguments',async()=>{
  const link=new RepeaterTransport({}, {commandTimeout:5});link.running=true;link.writer={write:async()=>{}};
  await assert.rejects(link.localCommand('set tempradio2 PRIVATE_SETTINGS'),error=>{assert.equal(error.otaCommand,'set tempradio2 <arguments hidden>');assert(!JSON.stringify(error).includes('PRIVATE_SETTINGS'));return true;});link.running=false;
});

test('USB mailbox context survives a remote identity request and snapshots Radio 2 before cleanup', async () => {
  const link=new RepeaterTransport({}, {remoteTimeout:1234});link.supportsOtaRadio2=true;link.otaRadioProfile=1;link.otaPath='routed (2 hops; configured)';
  const original=Object.assign(Error('Original source mailbox failure'),{otaStep:'Reading source reply mailbox',otaCommand:'ota.peer result',otaEndpoint:'USB Repeater',otaTimeoutMs:17,otaRadio:'USB/local command (no LoRa transmission)'});
  const calls=[];link.localCommand=async command=>{calls.push(command);if(command==='ota.peer result'){link.otaRadioProfile=0;throw original;}return 'OTApeer job=01020304 state=pending';};
  await assert.rejects(link.job('ota.peer cmd '+'b'.repeat(64)+' get public.key'),error=>{
    assert.equal(error,original);assert.equal(error.message,'Original source mailbox failure');assert.equal(error.otaEndpoint,'USB Repeater');assert.equal(error.otaTimeoutMs,17);assert.equal(error.otaRequestCommand,'get public.key');assert.equal(error.otaRequestStep,'Verifying target identity');assert.match(error.otaRequestRadio,/Source: Radio 2/);assert.match(error.otaPath,/routed.*2 hops/);return true;
  });assert.equal(calls.length,2);
});

test('remote job timeouts and rejected mutations keep safe command and configured path metadata', async () => {
  const link=new RepeaterTransport({}, {remoteTimeout:0});link.localCommand=async command=>command.startsWith('ota.peer path ')?'OTApeer path='+command.slice('ota.peer path '.length):'OTApeer job=01020304 state=pending';link.supportsRoutedPath=true;
  await link.setPath(['AA','BB']);
  await assert.rejects(link.job('ota.peer cmd '+'b'.repeat(64)+' get public.key'),error=>{assert.equal(error.message,'OTA request timed out; its result is unknown');assert.equal(error.otaStep,'Verifying target identity');assert.equal(error.otaCommand,'get public.key');assert.equal(error.otaEndpoint,'remote target');assert.match(error.otaPath,/routed.*2 hops/);assert.match(error.otaRadio,/not confirmed/);return true;});
  link.remoteTimeout=1234;link.localCommand=async command=>command==='ota.peer result'?'OTApeer job=01020304 state=error bytes=10 off=0 data=4552522064656e696564':'OTApeer job=01020304 state=pending';
  await assert.rejects(link.job('ota.peer cmd '+'b'.repeat(64)+' set wifi.password PRIVATE_WIFI_SECRET'),error=>{assert.equal(error.message,'ERR denied');assert.equal(error.otaCommand,'set <arguments hidden>');assert.equal(error.otaEndpoint,'remote target');assert(!JSON.stringify(error).includes('PRIVATE_WIFI_SECRET'));assert(!JSON.stringify(error).includes('b'.repeat(64)));return true;});
});

test('a failed login is labelled separately and does not include its password', async () => {
  const link=new RepeaterTransport({}, {knownRepeater:'b'.repeat(64)}),original=Error('Rejected login');link.job=async()=>{throw original;};
  await assert.rejects(link.login('b'.repeat(64),'PRIVATE_PASS'),error=>{assert.equal(error,original);assert.equal(error.otaStep,'Logging in to target');assert.equal(error.otaCommand,'login <arguments hidden>');assert.equal(error.otaEndpoint,'remote target');assert(!JSON.stringify(error).includes('PRIVATE_PASS'));return true;});
});

test('direct local path configuration from routed wrappers updates later remote error metadata', async () => {
  const port=new FakePort(),link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),openSettle:0,remoteTimeout:0});await link.open();
  assert.equal(link.otaPath,'direct (0 hops; configured)');
  const before=port.commands.length;await link.localCommand('ota.peer path A1,B2,C3');
  assert.deepEqual(port.commands.slice(before),['ota.peer path A1,B2,C3'],'diagnostics do not read or write an extra command');
  assert.equal(link.otaPath,'routed (3 hops; configured)');
  await assert.rejects(link.job('ota.peer cmd '+'b'.repeat(64)+' get public.key'),error=>{assert.equal(error.otaPath,'routed (3 hops; configured)');return /timed out/.test(error.message);});
  await link.localCommand('ota.peer path off');assert.equal(link.otaPath,'direct (0 hops; configured)');
  port.replies={'ota.peer path':'OTApeer path=AA11,BB22'};await link.localCommand('ota.peer path');assert.equal(link.otaPath,'routed (2 hops; configured)');await link.close();
});

test('unconfirmed path reads or writes clear stale route claims without changing their result', async () => {
  for(const reply of ['ERR bad state','OTApeer path=off','OTApeer path=AA,BBBB','OTApeer path=AA,AA']){
    const port=new FakePort(),link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),openSettle:0,remoteTimeout:0});await link.open();
    port.replies={'ota.peer path A1,B2':reply};assert.equal(await link.localCommand('ota.peer path A1,B2'),reply);assert.equal(link.otaPath,null);
    await assert.rejects(link.job('ota.peer cmd '+'b'.repeat(64)+' ota status'),error=>{assert.equal(error.otaPath,'path unknown');return /timed out/.test(error.message);});
    await link.close();
  }
  for(const reply of ['Unknown command','OTApeer path=AA,BBBB','OTApeer path=AA,AA']){
    const port=new FakePort(),link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),openSettle:0});await link.open();
    port.replies={'ota.peer path':reply};assert.equal(await link.localCommand('ota.peer path'),reply);assert.equal(link.otaPath,null);await link.close();
  }
  const port=new FakePort(),link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),openSettle:0,commandTimeout:5});await link.open();
  port.drop='ota.peer path';await assert.rejects(link.localCommand('ota.peer path'),/timed out/);assert.equal(link.otaPath,null);await link.close();
});

const diagnosticTarget='b'.repeat(64),diagnosticSource='a'.repeat(64);
function admittedDiagnosticLink(options={}){
  const link=new RepeaterTransport({}, {knownRepeater:diagnosticTarget,...options});
  Object.assign(link,{running:true,writer:{},sourceKey:diagnosticSource,ownsPeer:true,authenticatedKey:diagnosticTarget,diagnosticPassword:'admin',lastPeerUse:Date.now(),clockChecked:true,otaPath:'direct (0 hops; configured)',otaPathValue:'OFF'});
  return link;
}
function scriptedIdentityJob(link,replies,calls){
  let active;
  link.localCommand=async command=>{
    calls.push(command);
    const request=/^ota\.peer cmd(?:\.acl)? [a-f0-9]{64} (ver|board|ota status)$/.exec(command);
    if(request){active=request[1];return 'OTApeer job=01020304 state=pending';}
    if(command==='ota.peer result'){
      const bytes=Buffer.from(replies[active]);
      return `OTApeer job=01020304 state=ok bytes=${bytes.length} off=0 data=${bytes.toString('hex')}`;
    }
    throw Error('Unexpected diagnostic command');
  };
}
test('failure identity reads use one admitted command and restore transport timing without login or profile changes',async()=>{
  const link=admittedDiagnosticLink({commandTimeout:12000,remoteTimeout:40000,pollMs:700}),calls=[];
  scriptedIdentityJob(link,{ver:'v1.18.0.2',board:'RAK 3401'},calls);
  assert.equal(await link.diagnosticRead(diagnosticTarget,'admin','ver',{timeoutMs:37}),'v1.18.0.2');
  assert.equal(await link.diagnosticRead(diagnosticTarget,'admin','board',{timeoutMs:37}),'RAK 3401');
  assert.deepEqual(calls,['ota.peer cmd '+diagnosticTarget+' ver','ota.peer result','ota.peer cmd '+diagnosticTarget+' board','ota.peer result']);
  assert.equal(link.commandTimeout,12000);assert.equal(link.remoteTimeout,40000);assert.equal(link.pollMs,700);assert.equal(link.diagnosticDeadline,null);assert.equal(link.diagnosticCanContinue,null);
  assert.deepEqual(link.cachedFailureIdentity(diagnosticTarget),{sourceKey:diagnosticSource,targetKey:diagnosticTarget,version:'v1.18.0.2',board:'RAK 3401',boardOrigin:'board command',origin:'cached from earlier in this USB session'});
});
test('failure diagnostics reject mutations, wrong targets, expired or unsafe sessions without any I/O',async()=>{
  for(const change of [{running:false},{writer:null},{failure:Error('USB disconnected')},{ownsPeer:false},{authenticatedKey:null},{diagnosticPassword:'other'},{lastPeerUse:Date.now()-240000},{serving:true},{seeder:{}},{folderUnknown:true},{diagnosticReadUncertain:true}]){
    const link=admittedDiagnosticLink();Object.assign(link,change);let calls=0;link.job=async()=>{calls++;return 'unexpected';};
    await assert.rejects(link.diagnosticRead(diagnosticTarget,'admin','ver'));assert.equal(calls,0);
  }
  const link=admittedDiagnosticLink();let calls=0;link.job=async()=>{calls++;return 'unexpected';};
  for(const command of ['ota install','ota pull 11223344 flash','set tx 9','ver extra','board\nreboot'])await assert.rejects(link.diagnosticRead(diagnosticTarget,'admin',command),/only ver and board/);
  await assert.rejects(link.diagnosticRead('c'.repeat(64),'admin','ver'),/Target differs/);assert.equal(calls,0);
});
test('diagnostic admission rechecks a disconnect guard after waiting for the admin queue',async()=>{
  const link=admittedDiagnosticLink();let release,entered,allowed=true,calls=0;
  const started=new Promise(resolve=>entered=resolve),gate=new Promise(resolve=>release=resolve);
  const previous=link.withAdmin(async()=>{entered();await gate;});await started;
  link.job=async()=>{calls++;return 'v1.18.0.2';};
  const reading=link.diagnosticRead(diagnosticTarget,'admin','ver',{canContinue:()=>allowed});allowed=false;release();await previous;
  await assert.rejects(reading,/connection or request changed/);assert.equal(calls,0);assert.equal(link.diagnosticDeadline,null);
});
test('diagnostic USB timeout is bounded and does not retry or send another diagnostic command',async()=>{
  const port=new FakePort(),link=new RepeaterTransport(port,{knownRepeater:diagnosticTarget,openSettle:0,commandTimeout:1000,remoteTimeout:5000});
  await link.open();await link.verifyPeer(diagnosticTarget,'admin');
  const command='ota.peer cmd '+diagnosticTarget+' ver',before=port.commands.length;port.drop=command;
  const started=Date.now();await assert.rejects(link.diagnosticRead(diagnosticTarget,'admin','ver',{timeoutMs:15}),error=>error===link.failure&&/timed out/.test(error.message));
  assert.ok(Date.now()-started<300);assert.deepEqual(port.commands.slice(before),[command]);assert.equal(link.commandTimeout,1000);assert.equal(link.remoteTimeout,5000);assert.equal(link.diagnosticDeadline,null);
  await assert.rejects(link.diagnosticRead(diagnosticTarget,'admin','board'));assert.deepEqual(port.commands.slice(before),[command]);await link.close();
});
test('a timed out native diagnostic job remains uncertain and cannot be replaced by another read',async()=>{
  const port=new FakePort(),link=new RepeaterTransport(port,{knownRepeater:diagnosticTarget,openSettle:0,commandTimeout:1000,remoteTimeout:5000,pollMs:1000});
  await link.open();await link.verifyPeer(diagnosticTarget,'admin');port.replies={'ota.peer result':'OTApeer job=01020304 state=pending bytes=0 off=0 data=-'};
  const before=port.commands.length,started=Date.now();
  await assert.rejects(link.diagnosticRead(diagnosticTarget,'admin','ver',{timeoutMs:20}),/timed out/);assert.ok(Date.now()-started<300);
  const count=port.commands.length;await assert.rejects(link.diagnosticRead(diagnosticTarget,'admin','board'),/uncertain/);assert.equal(port.commands.length,count);
  assert.equal(port.commands.slice(before).filter(command=>command.startsWith('ota.peer cmd ')).length,1);assert.equal(link.diagnosticReadUncertain,true);assert.equal(link.failure,null);await link.close();
});
test('known ver and board replies are reused without LoRa, copied safely and bound to the same source and target',async()=>{
  const link=admittedDiagnosticLink(),calls=[];
  scriptedIdentityJob(link,{ver:'v1.18.0.2',board:'RAK 3401'},calls);
  await link.remoteCommand(diagnosticTarget,'admin','ver');await link.job('ota.peer cmd.acl '+diagnosticTarget+' board');
  const known=link.cachedFailureIdentity(diagnosticTarget);known.board='tampered';assert.equal(link.cachedFailureIdentity(diagnosticTarget).board,'RAK 3401');
  const before=calls.length;link.failure=Error('USB disconnected');
  assert.equal(await link.diagnosticRead(diagnosticTarget,'admin','ver'),'v1.18.0.2');assert.equal(await link.diagnosticRead(diagnosticTarget,'admin','board'),'RAK 3401');assert.equal(calls.length,before);
  assert.equal(link.cachedFailureIdentity('c'.repeat(64)),null);link.sourceKey='d'.repeat(64);assert.equal(link.cachedFailureIdentity(diagnosticTarget),null);
});
test('identity cache rejects failed or empty responses and uses reported ota status hardware only as a labelled fallback',async()=>{
  const link=admittedDiagnosticLink();
  for(const reply of ['', 'ERR unavailable','Unknown command','unsupported','OK'])link.rememberFailureIdentity(diagnosticTarget,'ver',reply);
  assert.equal(link.cachedFailureIdentity(diagnosticTarget),null);
  for(const reply of ['', 'ERR denied','Unknown command','unsupported'])link.rememberFailureIdentity(diagnosticTarget,'board',reply);
  assert.equal(link.cachedFailureIdentity(diagnosticTarget),null);
  for(const reply of ['OTA hw=unknown','OTA hw=unset','OTA hw=RAK3401 hw=RAK4631'])link.rememberFailureIdentity(diagnosticTarget,'ota status',reply);
  assert.equal(link.cachedFailureIdentity(diagnosticTarget),null);
  link.rememberFailureIdentity(diagnosticTarget,'ota status','OTA hw=RAK3401 | target:12345678');
  assert.equal(link.cachedFailureIdentity(diagnosticTarget).board,'hardware ID RAK3401 (reported by ota status)');assert.equal(link.cachedFailureIdentity(diagnosticTarget).boardOrigin,'ota status hardware ID');
  let calls=0;link.job=async()=>{calls++;throw Error('must reuse known data');};
  assert.match(await link.diagnosticRead(diagnosticTarget,'admin','board'),/reported by ota status/);assert.equal(calls,0);
  link.rememberFailureIdentity(diagnosticTarget,'board','RAK WisMesh 3401');link.rememberFailureIdentity(diagnosticTarget,'ota status','OTA hw=OTHER');assert.equal(link.cachedFailureIdentity(diagnosticTarget).board,'RAK WisMesh 3401');
});
