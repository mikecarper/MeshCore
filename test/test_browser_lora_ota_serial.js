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
    if (cmd === 'ota folder') reply = 'folder='+(this.folder?'on':'off')+' serving=0:';
    if (cmd === 'ota folder on') this.folder = true;
    if (cmd === 'ota folder off') this.folder = false;
    if (cmd === 'clock') { const n=new Date(); reply = `${n.getUTCHours()}:${String(n.getUTCMinutes()).padStart(2,'0')} - ${n.getUTCDate()}/${n.getUTCMonth()+1}/${n.getUTCFullYear()} UTC`; }
    if (cmd.startsWith('ota.peer path ')) reply='OTApeer path='+cmd.slice('ota.peer path '.length);
    if (cmd.startsWith('ota.peer acl ') || cmd.startsWith('ota.peer cmd.acl ') || cmd.startsWith('ota.peer login.repeater ') || cmd.startsWith('ota.peer cmd ')) reply='OTApeer job=01020304 state=pending';
    if (cmd === 'ota.peer result') reply='OTApeer job=01020304 state=ok bytes=2 off=0 data=4F4B';
    if (cmd === 'ota.peer peers 0') reply=`OTApeer peer=0 key=${target} type=2 rssi=-45 snr=8`;
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

test('lost detach acknowledgement is reconciled if the folder is off', async () => {
  const port=new FakePort(), link=new RepeaterTransport(port,{knownRepeater:'b'.repeat(64),aclOnly:true,openSettle:0,commandTimeout:20});
  await link.open(); await link.serve(makeInfo(),new Uint8Array(32)); port.drop='ota folder off';
  await link.stopServing(); assert.equal(link.serving,false); assert.equal(port.folder,false);
  await link.close(); assert.equal(port.commands.filter(c=>c==='ota folder off').length,1);
});
