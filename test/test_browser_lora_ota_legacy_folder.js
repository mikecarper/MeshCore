'use strict';
// Exercise the real browser transport against an old Full Companion's two
// exclusive USB owners. Its binary owner rejects every command above 176 bytes;
// its separate MS/ms file protocol has the deployed 192-byte read allowance.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
if (!globalThis.crypto) globalThis.crypto = crypto.webcrypto;
const {CompanionTransport} = require('../docs/_javascript/lora_ota_companion.js');
const {DirectSession} = require('../docs/_javascript/lora_ota_session.js');
const mota = require('../docs/_javascript/lora_ota_package.js');
const {CompanionFolderSource} = require('../docs/_javascript/lora_ota_serial.js');
globalThis.LoraOtaCompanionSource = require('../docs/_javascript/lora_ota_companion_source.js');

const enc = new TextEncoder(), dec = new TextDecoder();
const SOURCE = '01234567' + 'aa'.repeat(28), TARGET = 'bb'.repeat(32);
const hex = bytes => Buffer.from(bytes).toString('hex');
const cat = (...parts) => Uint8Array.from(Buffer.concat(parts.map(p => Buffer.from(p))));
const le16 = n => Uint8Array.of(n &255, n >>>8 &255);
const le32 = n => Uint8Array.of(n &255, n >>>8 &255, n >>>16 &255, n >>>24 &255);
const framed = body => cat([62], le16(body.length), body);
const wait = async (predicate, timeout = 1500) => {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() >= deadline) throw Error('Legacy-folder test did not make progress');
    await new Promise(resolve => setTimeout(resolve, 1));
  }
};

function browserStorage() {
  const originalNavigator = globalThis.navigator, originalStorage = globalThis.localStorage;
  const values = new Map();
  Object.defineProperty(globalThis, 'navigator', {configurable: true, value: {
    locks: {request: async (_key, _options, action) => action({})},
  }});
  globalThis.localStorage = {getItem: k => values.get(k) ?? null,
    setItem: (k, value) => values.set(k, value), removeItem: k => values.delete(k)};
  return () => {
    Object.defineProperty(globalThis, 'navigator', {configurable: true, value: originalNavigator});
    if (originalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = originalStorage;
  };
}

async function packageInfo() {
  const body = Buffer.alloc(2301);
  for (let i = 0; i < body.length; i++) body[i] = i *17 &255;
  const end = Buffer.alloc(56); end.write('EndF'); end.writeUInt32LE(body.length, 4);
  crypto.createHash('sha256').update(body).digest().copy(end, 8, 0, 8);
  end.writeUInt32LE(0x01120002, 16); end.writeUInt32LE(0x12345678, 20);
  end.write('legacy_folder_receiver', 24);
  return mota.parseMota(await mota.buildFullMota(cat(body, end)));
}

class OldFullPort {
  constructor(options = {}) {
    this.options = options; this.mode = 'binary'; this.input = []; this.readerWaiter = null;
    this.binaryFrames = []; this.localCommands = []; this.remoteCommands = [];
    this.events = []; this.msResponses = []; this.radioFrames = []; this.inbox = [];
    this.attachCount = 0; this.detachCount = 0; this.offAttempts =0; this.pendingSourceRead = null;
    this.manifestParts = []; this.closed = false; this.pulling = false;
  }
  push(value) {
    if (this.readerWaiter) { const resolve = this.readerWaiter; this.readerWaiter = null; resolve({value, done: false}); }
    else this.input.push(value);
  }
  emit(value) {
    // Exercise headers, ASCII barriers and source replies crossing read calls.
    const bytes = Uint8Array.from(value);
    this.push(bytes.slice(0, 1)); if (bytes.length >1) this.push(bytes.slice(1, 3));
    if (bytes.length >3) this.push(bytes.slice(3));
  }
  async open() {
    this.closed = false;
    this.readable = {getReader: () => ({read: () => this.input.length
      ? Promise.resolve({value: this.input.shift(), done: false})
      : new Promise(resolve => { this.readerWaiter = resolve; }),
      cancel: async () => { if (this.readerWaiter) { this.readerWaiter({done: true}); this.readerWaiter = null; } },
      releaseLock() {}})};
    this.writable = {getWriter: () => ({write: async bytes => this.write(bytes), close: async () => {}, releaseLock() {}})};
  }
  async close() { this.closed = true; }
  sourceRequest(op, args, length, action) {
    assert.equal(this.mode, 'folder');
    assert.equal(this.pendingSourceRead, null, 'MCU has only one outstanding source request');
    this.pendingSourceRead = {op, length, action};
    const bytes = cat([77, 83, op], args); let xor = 0;
    for (const byte of bytes.slice(2)) xor ^= byte;
    this.emit(cat(bytes, [xor]));
  }
  startCatalog() {
    this.sourceRequest(1, [], 1, count => {
      assert.deepEqual([...count], [1]);
      this.sourceRequest(2, [0], 38, describe => {
        this.mid = describe.slice(0, 4);
        this.events.push('folder:attached');
        if (!this.options.dropAttach) this.emit(enc.encode('\r\nOK folder attached (serial): advertising 1/1 host mOTAs\r\n'));
        if (this.pulling && !this.options.suppressFileReads) this.readManifestPart(0);
      });
    });
  }
  readManifestPart(index) {
    const offset = index ===0 ?8 :184, size = index ===0 ?176 :21;
    this.sourceRequest(3, cat([0], le32(offset), le16(size)), size, bytes => {
      this.manifestParts[index] = bytes;
      // These bytes go from the MCU to LoRa, never through CMD_SEND_RAW_PACKET.
      this.radioFrames.push(cat([0x32, 0, 5], this.mid, [index, 2], bytes));
      if (index ===0) this.readManifestPart(1);
    });
  }
  async write(bytes) {
    assert.ok(!this.closed); bytes = Uint8Array.from(bytes);
    if (bytes[0] ===109 && bytes[1] ===115) {
      assert.equal(this.mode, 'folder', 'MS replies require the exclusive folder owner');
      assert.ok(this.pendingSourceRead, 'No unsolicited file reply');
      const pending = this.pendingSourceRead; this.pendingSourceRead = null;
      assert.equal(bytes[2], pending.op); assert.equal(bytes[3], 0);
      assert.equal(bytes.length, pending.length +5);
      assert.equal(bytes.reduce((a, b) => a ^b, 0), 0);
      this.msResponses.push(bytes); pending.action(bytes.slice(4, -1)); return;
    }
    if (bytes[0] !==60) {
      const command = dec.decode(bytes).trim(); this.events.push(command);
      if (command ==='ota folder on') {
        assert.equal(this.mode, 'binary'); this.mode = 'folder'; this.attachCount++;
        if (!this.options.dropAttachRequest) this.startCatalog(); return;
      }
      if (command ==='ota folder off') {
        this.offAttempts++;
        // A firmware which already observed USB disconnect may be Binary.
        // Its idle binary parser ignores this text; it does not acknowledge it.
        if (this.mode ==='binary') return;
        if (this.options.dropStopRequest) return;
        assert.equal(this.mode, 'folder'); this.detachCount++; this.mode = 'binary';
        this.pendingSourceRead = null;
        if (!this.options.dropDetach) this.emit(enc.encode('\r\nOK folder detached (primary source retained)\r\nOK - Binary mode\r\n'));
        return;
      }
      throw Error('Unexpected legacy ASCII command: ' +command);
    }
    assert.equal(this.mode, 'binary', 'Binary Companion frames must wait for the handoff barrier');
    assert.equal(bytes.length, bytes[1] +(bytes[2] <<8) +3);
    const frame = bytes.slice(3); this.binaryFrames.push(frame);
    assert.ok(frame.length <=176, 'Old firmware would discard this oversized Companion command');
    let answer;
    if (frame[0] ===1) {
      answer = new Uint8Array(58); answer[0] =5; answer.set(Buffer.from(this.options.identity ||SOURCE, 'hex'), 4);
      const v = new DataView(answer.buffer); v.setUint32(48, 910525, true); v.setUint32(52, 62500, true);
      answer[56] =7; answer[57] =5;
    } else if (frame[0] ===22) {
      answer = new Uint8Array(82); answer[0] =13; answer[1] =14;
      answer.set(enc.encode('24-Oct-2026'), 8); answer.set(enc.encode('v1.18.0.1-old-full'), 60);
    } else if (frame[0] ===5) answer = cat([9], le32(Math.floor(Date.now() /1000)));
    else if (frame[0] ===26) {
      answer = Uint8Array.of(6);
      queueMicrotask(() =>this.emit(framed(cat([0x85, 1], Buffer.from(TARGET, 'hex').subarray(0, 6), [0, 0, 0, 0, 3]))));
    } else if (frame[0] ===66) {
      const command = dec.decode(frame.slice(1)); this.localCommands.push(command);
      let reply = {'get ota.direct':'Unknown command', 'ver':'v1.18.0.1-halo-keymind-cascade-dev-03214a16 (Build: 24-Oct-2026)',
        'get usb.raw.max':'Unknown command', 'ota folder':'folder=off host=0/0 serving=0:',
        'get radio':'910.525,62.5,7,5', 'get tempradio':'TempRadio active: 910.525,62.5,7,5 7200s left,preamble=128',
        'ota config':'ota config: speed=1x packet=1x autofetch=off autoinstall=off hops=0',
        'ota stats':'fetch idle 0/0', 'get radio.rxps':'> off,off,65625,60000'}[command];
      if (command ==='ota folder' && this.options.folderProbe !==undefined) reply = this.options.folderProbe;
      if (command ==='get tempradio' && this.options.temporaryRadio) reply = this.options.temporaryRadio;
      if (command ===this.options.dropLocal) return;
      reply=this.options.localReply?.(command,this)??reply;
      if (reply ===undefined) throw Error('Unexpected local command: ' +command);
      answer = cat([29], enc.encode(reply));
    } else if (frame[0] ===4) {
      const contact = new Uint8Array(148); contact[0] =3; contact.set(Buffer.from(TARGET, 'hex'), 1);
      contact[33] =2; contact[35] =0; contact.set(enc.encode('Old OTA target'), 100);
      this.emit(framed(cat([2], le32(1)))); this.emit(framed(contact)); this.emit(framed(cat([4], le32(1)))); return;
    } else if (frame[0] ===2) {
      const tagged = dec.decode(frame.slice(13)), tag = tagged.slice(0, 3), command = tagged.slice(3);
      this.remoteCommands.push(command); this.events.push('remote:' +command);
      let reply = 'OK';
      if (command ==='ota status') reply = this.manifestParts.length ===2 ?'fetch=C 3/3' :'fetch=F 1/3';
      if (command ==='get public.key') reply = '> ' +TARGET;
      if (command ==='get tempradio') reply = this.options.temporaryRadio ||'TempRadio active: 910.525,62.5,7,5 7200s left,preamble=128';
      if (command ==='ota config') reply = 'autofetch=off autoinstall=off hops=0';
      if (command.startsWith('ota pull ')) { this.pulling = true; this.pendingLateInbox = true; }
      if (command ==='ota cancel') this.pulling = false;
      reply=this.options.remoteReply?.(command,this)??reply;
      this.inbox.push(cat([16, 0, 0, 0], Buffer.from(TARGET, 'hex').subarray(0, 6), [255, 1], le32(1), enc.encode(tag +reply)));
      answer = Uint8Array.of(6);
    } else if (frame[0] ===10) {
      answer = this.inbox.shift() || Uint8Array.of(10);
      if (this.options.lateInbox &&this.pendingLateInbox &&this.inbox.length ===0) {
        this.pendingLateInbox = false;
        this.inbox.push(cat([16, 0, 0, 0], Buffer.from('cc'.repeat(32), 'hex').subarray(0, 6),
          [255, 0], le32(1), enc.encode('Keep the message that raced folder attachment')));
      }
    }
    else if (frame[0] ===65) {
      // Small discovery frames are permitted before the MCU folder takes over.
      assert.ok(frame.length <=32, 'A large RF frame must be built by the old source MCU');
      this.events.push('raw:' +frame[4]); answer = Uint8Array.of(0);
    } else throw Error('Unexpected Companion command: ' +frame[0]);
    this.emit(framed(answer));
  }
}

async function source(options = {}) {
  const restore = browserStorage(), port = new OldFullPort(options);
  const link = new CompanionTransport(port, {openSettle: 0, commandTimeout: 100,
    contactsTimeout: 100, inboxTimeout: 100, inboxPollDelay: 0,
    investigationTimeout: 100, remoteTimeout: 100, folderPollDelayMs: 0,
    folderTransitionTimeout: 150, folderTransitionTimeoutMs: 150});
  try {
    await link.open(); await link.contacts(); link.selectTarget(TARGET);
    link.enableStandardInvestigation();
    Object.assign(link, {investigationAuthenticated: true, investigationVerified: true, investigationPassword: ''});
    return {link, port, restore, cleanup: async () => { await link.close(); restore(); }};
  } catch (error) { await link.close(); restore(); throw error; }
}

test('an old Full Companion is detected without retuning, claiming USB or changing the target', async () => {
  const f = await source();
  try {
    assert.equal(f.link.usbRawFrameMax, 176); assert.equal(f.link.usbFolderSupported, true);
    assert.ok(f.port.localCommands.includes('ota folder'));
    assert.deepEqual(f.port.remoteCommands, []); assert.equal(f.port.attachCount, 0);
    assert.ok(f.port.binaryFrames.every(frame =>frame.length <=176));
  } finally { await f.cleanup(); }
});

test('legacy USB keeps 176-byte commands while the MCU sends the complete fixed 176-byte manifest', async () => {
  const f = await source(), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    assert.equal(f.port.attachCount, 0, 'Do not surrender binary admin before pull is sent');
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    assert.equal(f.port.attachCount, 1);
    assert.ok(f.port.events.indexOf('remote:ota pull ' +hex(info.merkleRoot)) <f.port.events.indexOf('ota folder on'));
    assert.equal(hex(cat(...f.port.manifestParts)), hex(info.blob.slice(8, 205)));
    assert.deepEqual(f.port.radioFrames.map(f =>f.length), [185, 30]);
    assert.ok(f.port.msResponses.some(frame =>frame.length ===181), 'The file protocol carries176-byte reads independently');
    assert.ok(f.port.binaryFrames.every(frame =>frame.length <=176));
    const adverts = f.port.binaryFrames.filter(frame =>frame[0] ===65 &&frame[4] ===1);
    assert.ok(adverts.length); assert.equal(hex(adverts[0].slice(5, 9)), SOURCE.slice(0, 8));
    await f.link.stopServing(); assert.equal(f.port.mode, 'binary');
  } finally { await f.cleanup(); }
});

test('status briefly hands USB back to binary and resumes the same folder without repeating pull', async () => {
  const f = await source(), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    const before = f.port.events.length;
    assert.match(await f.link.remoteCommand(TARGET, '', 'ota status'), /fetch=C/);
    await wait(() =>f.port.attachCount ===2 &&f.port.mode ==='folder');
    const events = f.port.events.slice(before);
    assert.ok(events.indexOf('ota folder off') <events.indexOf('remote:ota status'));
    assert.ok(events.indexOf('remote:ota status') <events.indexOf('ota folder on'));
    assert.equal(f.port.remoteCommands.filter(cmd =>cmd.startsWith('ota pull ')).length, 1);
    assert.equal(f.port.binaryFrames.filter(frame =>frame[0] ===65 &&frame.length >32).length, 0);
  } finally { await f.cleanup(); }
});

test('browser adverts cannot interrupt a live native folder or emit binary raw commands through it', async () => {
  const f = await source(), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    const binaryBefore = f.port.binaryFrames.length, eventsBefore = f.port.events.length;
    await f.link.advertise();
    await f.link.sendRadio(f.link.seeder.advert());
    assert.equal(f.port.binaryFrames.length, binaryBefore);
    assert.deepEqual(f.port.events.slice(eventsBefore), []);
    assert.equal(f.port.attachCount, 1); assert.equal(f.port.detachCount, 0);
    assert.equal(f.link.failure, null); assert.equal(f.port.mode, 'folder');
    const read = new Promise(resolve =>f.port.sourceRequest(3,
      cat([0], le32(info.payloadOffset), le16(192)), 192, resolve));
    assert.equal(hex(await read), hex(info.blob.slice(info.payloadOffset, info.payloadOffset +192)),
      'Native MS reads continue serving the selected file after the browser timer fires');
  } finally { await f.cleanup(); }
});

test('an advert completing during native handoff cannot resurrect its browser timer', async () => {
  const link = new CompanionTransport({}); let finish;
  link.serving =true; link.seeder = {advert: () =>Uint8Array.of(0x32, 0, 1)};
  link.sendRadio = () =>new Promise(resolve =>{ finish =resolve; });
  try {
    const advert = link.advertise();
    assert.equal(typeof finish, 'function');
    link.usbFolderAttached =true;
    finish(); await advert;
    assert.equal(link.advertTimer, undefined, 'Do not create a new browser timer after the native source takes over');
    assert.equal(link.failure, null);
  } finally { clearTimeout(link.advertTimer); }
});

test('native old Full sources serve on a verified temporary channel without changing their saved channel', async () => {
  const f = await source({temporaryRadio: 'TempRadio active: 909.950,250,5,5 7200s left,preamble=128'});
  const info = await packageInfo();
  try {
    assert.deepEqual(await f.link.savedSourceRadio(), [910.525, 62.5, 7, 5]);
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    assert.deepEqual(f.link.sourceTempRadio.radio, [909.95, 250, 5, 5]);
    assert.deepEqual(await f.link.savedSourceRadio(), [910.525, 62.5, 7, 5]);
    assert.ok(!f.port.binaryFrames.some(frame =>frame[0] ===7), 'No saved radio setting is written');
  } finally { await f.cleanup(); }
});

test('Session preflight permits off-primary native folder OTA while unprobed old raw sources stay blocked', async () => {
  for (const nativeFolder of [true, false]) {
    const f = await source({temporaryRadio: 'TempRadio active: 909.950,250,5,5 7200s left,preamble=128'});
    try {
      f.link.usbFolderSupported = nativeFolder;
      const session = new DirectSession(f.link, TARGET, '', null);
      if (nativeFolder) {
        const result = await session.inspectRadios();
        assert.deepEqual(result.source, [909.95, 250, 5, 5]);
        assert.deepEqual(result.target, [909.95, 250, 5, 5]);
        assert.deepEqual(result.savedSource, [910.525, 62.5, 7, 5]);
      } else {
        await assert.rejects(session.inspectRadios(), /temporary radio must match its saved primary/i);
        assert.deepEqual(f.port.remoteCommands, []);
      }
      assert.equal(f.port.attachCount, 0, 'Radio inspection stays read-only');
      assert.ok(f.port.remoteCommands.every(command =>command.startsWith('get ') ||command ==='ota config'));
    } finally { await f.cleanup(); }
  }
});

test('an interrupted handoff poisons the session instead of replaying pull or writing through the wrong owner', async () => {
  const f = await source({dropAttach: true}), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    await assert.rejects(f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash'), /folder|timed out|reconnect/i);
    assert.equal(f.port.attachCount, 1);
    assert.equal(f.port.remoteCommands.filter(cmd =>cmd.startsWith('ota pull ')).length, 1);
    const count = f.port.binaryFrames.length;
    await assert.rejects(f.link.remoteCommand(TARGET, '', 'ota status'), /folder|timed out|reconnect/i);
    assert.equal(f.port.binaryFrames.length, count);
  } finally { await f.cleanup(); }
});

test('an unsupported folder source still stops before changing the remote device', async () => {
  const f = await source({folderProbe: 'Unknown command'}), info = await packageInfo();
  try {
    assert.equal(f.link.usbFolderSupported, false);
    await assert.rejects(f.link.serve(info, info.blob, null), error =>error.sourceUpdateRequired ==='companion');
    assert.deepEqual(f.port.remoteCommands, []); assert.equal(f.port.attachCount, 0);
    assert.ok(f.port.binaryFrames.every(frame =>frame.length <=176));
  } finally { await f.cleanup(); }
});

test('cancel detaches the owned folder before its one remote command and permits another selected package', async () => {
  const f = await source(), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    await f.link.stopServing();
    const stoppedAt = f.port.events.length;
    await f.link.remoteCommand(TARGET, '', 'ota cancel');
    assert.equal(f.port.remoteCommands.filter(cmd =>cmd ==='ota cancel').length, 1);
    assert.equal(f.port.mode, 'binary');
    assert.ok(!f.port.events.slice(stoppedAt).includes('ota folder on'));
    const changed = {...info, blob: info.blob.slice()};
    await f.link.serve(changed, changed.blob, null);
    assert.equal(f.port.mode, 'binary', 'A replacement waits for its own admitted pull');
    await f.link.stopServing();
  } finally { await f.cleanup(); }
});

test('a missing binary-return barrier prevents status or any other binary write', async () => {
  const f = await source({dropDetach: true}), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    const sent = f.port.binaryFrames.length;
    await assert.rejects(f.link.remoteCommand(TARGET, '', 'ota status'), /folder|timed out|reconnect/i);
    assert.equal(f.port.binaryFrames.length, sent, 'Do not guess that the binary owner has resumed');
    assert.equal(f.port.detachCount, 1, 'Never replay an uncertain ownership command');
    assert.equal(f.port.remoteCommands.filter(cmd =>cmd.startsWith('ota pull ')).length, 1);
  } finally { await f.cleanup(); }
});

test('physical USB loss rejects an in-flight folder handoff with the disconnect cause', async () => {
  const f = await source({dropAttach: true}), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    const pull = f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    const rejected = assert.rejects(pull, /Companion disconnected/i);
    await wait(() =>f.port.attachCount ===1 &&f.port.readerWaiter !==null);
    const waitingReader = f.port.readerWaiter; f.port.readerWaiter =null;
    waitingReader({done: true});
    await rejected;
    assert.equal(f.port.attachCount, 1);
    assert.equal(f.port.remoteCommands.filter(cmd =>cmd.startsWith('ota pull ')).length, 1);
  } finally { await f.cleanup(); }
});

test('an unanswered read-only folder probe cannot be mistaken for unsupported firmware or trigger a handoff', async () => {
  const restore = browserStorage(), port = new OldFullPort({dropLocal: 'ota folder'});
  const link = new CompanionTransport(port, {openSettle: 0, commandTimeout: 15});
  try {
    await assert.rejects(link.open(), /timed out|reconnect/i);
    assert.equal(port.localCommands.filter(c =>c ==='ota folder').length, 1);
    assert.equal(port.attachCount, 0); assert.deepEqual(port.remoteCommands, []);
  } finally { await link.close(); restore(); }
});

test('a source reporting another attached folder is left untouched', async () => {
  const restore = browserStorage(), port = new OldFullPort({folderProbe: 'folder=on host=1/1 serving=1:'});
  const link = new CompanionTransport(port, {openSettle: 0, commandTimeout: 100});
  try {
    await assert.rejects(link.open(), /already.*folder/i);
    assert.equal(port.attachCount, 0); assert.equal(port.detachCount, 0);
    assert.deepEqual(port.remoteCommands, []);
  } finally { await link.close(); restore(); }
});

test('post-install identity verification can refresh the old admin session without losing folder ownership', async () => {
  const f = await source(), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    const before = f.port.events.length;
    await f.link.resetRemoteSession(TARGET, '');
    assert.equal(f.link.investigationVerified, true);
    assert.ok(f.port.events.slice(before).includes('remote:get public.key'));
    assert.equal(f.port.mode, 'folder');
    assert.equal(f.port.remoteCommands.filter(cmd =>cmd.startsWith('ota pull ')).length, 1);
  } finally { await f.cleanup(); }
});

test('resuming a verified active download attaches its file source without replaying pull', async () => {
  const f = await source(), info = await packageInfo();
  try {
    f.port.pulling = true; // The previous browser connection already admitted it.
    await f.link.serve(info, info.blob, null, {resume: true});
    await wait(() =>f.port.manifestParts.length ===2);
    assert.equal(f.port.attachCount, 1);
    assert.equal(f.port.remoteCommands.filter(cmd =>cmd.startsWith('ota pull ')).length, 0);
    assert.equal(hex(cat(...f.port.manifestParts)), hex(info.blob.slice(8, 205)));
    await f.link.stopServing(); assert.equal(f.port.mode, 'binary');
  } finally { await f.cleanup(); }
});

test('a message arriving after the pull reply is preserved before firmware can drain the offline queue', async () => {
  const f = await source({lateInbox: true}), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    assert.equal(f.port.inbox.length, 0);
    const messages = f.link.getPreservedMessages();
    assert.equal(messages.length, 1);
    assert.equal(messages[0].text, 'Keep the message that raced folder attachment');
  } finally { await f.cleanup(); }
});

test('corrupt or incomplete MS requests never receive file bytes and an uncertain start is not replayed', async () => {
  const info = await packageInfo();
  for (const broken of [Uint8Array.of(77, 83, 3, 0), Uint8Array.of(77, 83, 1, 9)]) {
    const writes = [];
    const folder = new CompanionFolderSource(async bytes =>writes.push(bytes), info, info.blob, {transitionTimeout: 15});
    const started = folder.start();
    await wait(() =>writes.length ===1);
    folder.feed(broken);
    await assert.rejects(started, /not confirmed|reconnect/i);
    assert.equal(writes.length, 1);
    assert.equal(dec.decode(writes[0]).trim(), 'ota folder on');
    await assert.rejects(folder.start(), /not confirmed|reconnect/i);
    await assert.rejects(folder.stop(), /not confirmed|reconnect/i);
    assert.equal(writes.length, 1, 'A local timeout cannot authorize replaying a mode switch');
  }
});

test('the host file protocol rejects read overruns without exposing unselected firmware bytes', async () => {
  const info = await packageInfo(), writes = [];
  const folder = new CompanionFolderSource(async bytes =>writes.push(bytes), info, info.blob, {transitionTimeout: 100});
  const started = folder.start();
  await wait(() =>writes.length ===1);
  const request = cat([77, 83, 3, 0], le32(info.blob.length -1), le16(2));
  let xor = 0; for (const byte of request.slice(2)) xor ^=byte;
  folder.feed(cat(request, [xor]));
  await wait(() =>writes.length ===2);
  assert.deepEqual([...writes[1].slice(0, 4)], [109, 115, 3, 1]);
  assert.equal(writes[1].length, 5, 'A rejected read returns status only');
  folder.feed(enc.encode('OK folder attached (serial): advertising 1/1 host mOTAs\r\n'));
  await started;
  const stopped = folder.stop();
  await wait(() =>writes.length ===3);
  folder.feed(enc.encode('OK - Binary mode\r\n'));
  await stopped; assert.equal(folder.mode, 'binary');
});

test('continuous file reads cannot postpone a progress check indefinitely', async () => {
  const info = await packageInfo();
  const folder = new CompanionFolderSource(async () =>{}, info, info.blob,
    {pollDelayMs: 1000, maxWaitMs: 25});
  folder.mode = 'folder'; folder.startedAt = folder.lastReadAt = Date.now();
  const begin = Date.now(), activity = setInterval(() =>{ folder.lastReadAt = Date.now(); }, 3);
  try {
    await folder.waitServeWindow();
    assert.ok(Date.now() -begin <200, 'The absolute bound wins even while transfer reads continue');
  } finally { clearInterval(activity); }
});

test('stopping interrupts a quiet-window wait without waiting for another radio packet', async () => {
  const info = await packageInfo();
  const folder = new CompanionFolderSource(async () =>{}, info, info.blob,
    {pollDelayMs: 10000, maxWaitMs: 10000});
  folder.mode = 'folder'; folder.startedAt = folder.lastReadAt = Date.now();
  const begin = Date.now(), waiting = folder.waitServeWindow();
  folder.interruptWait();
  await waiting; assert.ok(Date.now() -begin <200);
});

test('Session Stop interrupts a real pending folder poll and restores Binary without target cancellation', async () => {
  const f = await source(), info = await packageInfo();
  let deadline;
  try {
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    const folder = f.link.usbFolderSource;
    folder.options.pollDelayMs =120000; folder.options.maxWaitMs =120000;
    folder.startedAt =folder.lastReadAt =Date.now();
    const session = new DirectSession(f.link, TARGET, '', info);
    session.serving =true; // The transfer owns the package already admitted above.
    const pending = session.remote('ota status');
    const rejected = assert.rejects(pending, /Stopped/i);
    await wait(() =>typeof folder.wakeWait ==='function');
    const commandsBefore = f.port.remoteCommands.length;
    const stopped = session.stop();
    assert.equal(session.stop(), stopped, 'Repeated Stop shares its one detach task');
    await Promise.race([
      Promise.all([stopped, rejected]),
      new Promise((_resolve, reject) =>{ deadline =setTimeout(() =>reject(Error('Session Stop did not interrupt the folder wait')), 500); }),
    ]);
    assert.equal(session.cancelled, true); assert.equal(session.serving, false);
    assert.equal(f.link.serving, false); assert.equal(f.port.mode, 'binary');
    assert.equal(f.port.detachCount, 1);
    assert.equal(f.port.remoteCommands.length, commandsBefore, 'The queued status read is stopped before transmission');
    assert.equal(f.port.remoteCommands.filter(cmd =>cmd.startsWith('ota pull ')).length, 1);
    assert.ok(!f.port.remoteCommands.includes('ota cancel'));
    assert.equal(f.port.attachCount, 1, 'Stop must not reattach the file source');
  } finally { clearTimeout(deadline); await f.cleanup(); }
});

test('Session Stop during resumed native attachment detaches as soon as its own handoff succeeds', async () => {
  const f=await source({dropAttach:true}),info=await packageInfo();let deadline;
  try {
    const session=new DirectSession(f.link,TARGET,'',info);
    session.inspect=async()=>({download:'active'});
    session.readDownload=async()=>{throw Error('Stopped attachment must not begin a transfer status/quiet wait');};
    const active=session.start({resume:true});active.catch(()=>{});
    await wait(()=>f.link.usbFolderSource?.mode==='starting'&&f.port.events.includes('folder:attached'));
    assert.equal(session.serving,false);assert.equal(f.link.serving,true);
    const stopped=session.stop();assert.equal(session.stop(),stopped);
    await new Promise(resolve=>setTimeout(resolve,10));
    assert.equal(f.port.detachCount,0,'An unacknowledged handoff is not yet confirmed ownership');
    f.port.emit(enc.encode('\r\nOK folder attached (serial): advertising 1/1 host mOTAs\r\n'));
    await Promise.race([stopped,new Promise((_resolve,reject)=>{
      deadline=setTimeout(()=>reject(Error('Stop waited past the bounded local handoff')),500);
    })]);
    await assert.rejects(active,/Stopped/);
    assert.equal(f.port.mode,'binary');assert.equal(f.port.detachCount,1);assert.equal(f.port.attachCount,1);
    assert.equal(session.serving,false);assert.equal(f.link.serving,false);
    assert.deepEqual(f.port.remoteCommands,[],'Stop sends no target status, pull or cancel command');
    assert.ok(f.port.binaryFrames.every(frame=>frame.length<=176));
  } finally {clearTimeout(deadline);await f.cleanup();}
});

test('stopping an unstarted Session does not detach another owner of the file source', async () => {
  const f = await source(), info = await packageInfo();
  try {
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    const session = new DirectSession(f.link, TARGET, '', info);
    assert.equal(session.serving, false); assert.equal(f.link.serving, true);
    await session.stop();
    assert.equal(session.cancelled, true);
    assert.equal(f.link.serving, true); assert.equal(f.port.mode, 'folder');
    assert.equal(f.port.detachCount, 0);
    assert.ok(!f.port.remoteCommands.includes('ota cancel'));
  } finally { await f.cleanup(); }
});

test('folder polling leaves longer receive windows for slower radio settings and larger blocks', () => {
  const link = new CompanionTransport({}); link.radio = [910.525, 250, 5, 5]; link.preamble =32;
  const fast = link.folderServeWindowMs({blockSize: 1024});
  assert.ok(fast >=5000);
  assert.ok(link.folderServeWindowMs({blockSize: 2048}) >fast);
  link.radio = [910.525, 62.5, 12, 5];
  const slow = link.folderServeWindowMs({blockSize: 1024});
  assert.ok(slow >fast *10, 'Do not interrupt slow RF trains on a fixed fast-radio timer');
  link.preamble =128;
  assert.ok(link.folderServeWindowMs({blockSize: 1024}) >slow);
});

test('reconnect recovers only the same owned port after uncertain attach or detach', async () => {
  const info = await packageInfo();
  for (const fault of ['attach', 'detach']) {
    const f = await source(fault ==='attach' ?{dropAttach: true} :{dropStopRequest: true});
    let reconnected;
    try {
      await f.link.serve(info, info.blob, null);
      const pull = f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
      if (fault ==='attach') await assert.rejects(pull, /folder.*not confirmed/i);
      else {
        await pull; await wait(() =>f.port.manifestParts.length ===2);
        await assert.rejects(f.link.remoteCommand(TARGET, '', 'ota status'), /folder.*not confirmed/i);
      }
      await f.link.close();
      assert.equal(f.port.closed, true); assert.equal(f.port.mode, 'folder');
      f.port.options.dropStopRequest =false;
      const offBefore = f.port.offAttempts, binaryBefore = f.port.binaryFrames.length;
      reconnected = new CompanionTransport(f.port, {openSettle: 0, commandTimeout: 100, folderRecoveryTimeoutMs: 20});
      await reconnected.open();
      assert.equal(reconnected.sourceKey, SOURCE);
      assert.equal(f.port.offAttempts, offBefore +1, 'One cleanup command for this exact previously owned port');
      assert.equal(f.port.mode, 'binary');
      assert.equal(f.port.binaryFrames[binaryBefore][0], 1, 'Read identity only after the ownership barrier');
      assert.equal(f.port.remoteCommands.filter(cmd =>cmd.startsWith('ota pull ')).length, 1);
    } finally { await reconnected?.close(); await f.link.close(); f.restore(); }
  }
});

test('reconnect also verifies a source that already returned to binary after a lost detach reply', async () => {
  const f = await source({dropDetach: true}), info = await packageInfo(); let reconnected;
  try {
    await f.link.serve(info, info.blob, null);
    await f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash');
    await wait(() =>f.port.manifestParts.length ===2);
    await assert.rejects(f.link.remoteCommand(TARGET, '', 'ota status'), /folder.*not confirmed/i);
    await f.link.close(); assert.equal(f.port.mode, 'binary');
    const offBefore = f.port.offAttempts;
    reconnected = new CompanionTransport(f.port, {openSettle: 0, commandTimeout: 100, folderRecoveryTimeoutMs: 20});
    await reconnected.open();
    assert.equal(reconnected.sourceKey, SOURCE); assert.equal(f.port.offAttempts, offBefore +1);
    assert.equal(reconnected.failure, null, 'A read-only identity proof resolves the ambiguous cleanup');
  } finally { await reconnected?.close(); await f.link.close(); f.restore(); }
});

test('a recovered port must prove the original full source key before reconnect can proceed', async () => {
  const f = await source({dropAttach: true}), info = await packageInfo(); let reconnected;
  try {
    await f.link.serve(info, info.blob, null);
    await assert.rejects(f.link.remoteCommand(TARGET, '', 'ota pull ' +hex(info.merkleRoot) +' flash'), /folder.*not confirmed/i);
    await f.link.close();
    f.port.options.identity = 'cc'.repeat(32);
    const sentBefore = f.port.binaryFrames.length;
    reconnected = new CompanionTransport(f.port, {openSettle: 0, commandTimeout: 100, folderRecoveryTimeoutMs: 20});
    await assert.rejects(reconnected.open(), /different identity/i);
    assert.deepEqual(f.port.binaryFrames.slice(sentBefore).map(frame =>frame[0]), [1]);
    assert.equal(f.port.remoteCommands.filter(cmd =>cmd.startsWith('ota pull ')).length, 1);
    assert.equal(f.port.closed, true);
  } finally { await reconnected?.close(); await f.link.close(); f.restore(); }
});

module.exports = {OldFullPort, packageInfo, source, TARGET, SOURCE};

// A successful USB write may physically reach the MCU before its Promise
// resolves on the host. The next native request then belongs to a newer
// response-queue generation than the one Stop originally awaited.
test('folder Stop drains newly chained native reads before the off control',async()=>{
  const info=await packageInfo(),writes=[];let source;
  const request=(op,args=[])=>cat([77,83,op],args,[[op,...args].reduce((a,b)=>a^b,0)]);
  source=new CompanionFolderSource(async bytes=>{
    if(bytes[0]===109){
      writes.push('ms'+bytes[2]+':'+source.mode);
      if(bytes[2]===1)source.feed(request(2,[0]));
      if(bytes[2]===2)source.feed(request(3,[0,0,0,0,0,1,0]));
    }else{
      assert.equal(dec.decode(bytes).trim(),'ota folder off');writes.push('off:'+source.mode);
      source.feed(enc.encode('OK - Binary mode\r\n'));
    }
  },info,info.blob);
  source.mode='folder';source.feed(request(1));
  await source.stop();await source.responses;
  assert.deepEqual(writes,['ms1:folder','ms2:folder','ms3:folder','off:stopping']);
  assert.equal(source.mode,'binary');
});

test('off barrier waits for a racing native reply before allowing the next app command',async()=>{
  const info=await packageInfo(),writes=[];let source,finishWrite,stopped=false;
  const request=Uint8Array.of(77,83,1,1);
  source=new CompanionFolderSource(async bytes=>{
    if(bytes[0]===109){
      writes.push('native');
      // Published firmware retains hasPendingResponse() across off and keeps
      // Binary input quarantined until this complete response is consumed.
      await new Promise(resolve=>{finishWrite=resolve;});return;
    }
    assert.equal(dec.decode(bytes).trim(),'ota folder off');writes.push('off');
    // The MCU had already emitted this request when it processed off. Its
    // acknowledgement can reach the browser before the queued reply writes.
    source.feed(request);source.feed(enc.encode('OK - Binary mode\r\n'));
  },info,info.blob);
  source.mode='folder';const stopping=source.stop().then(()=>{stopped=true;});
  await wait(()=>!!finishWrite);
  assert.equal(source.mode,'binary','The receive decoder switches at the ordered barrier');
  assert.equal(source.blocksAppCommands(),true);assert.equal(stopped,false);
  const link=new CompanionTransport({});link.running=true;link.usbFolderSource=source;
  link.writer={write:async()=>{throw Error('App commands must not overtake the native response');}};
  await assert.rejects(link.commandAny(Uint8Array.of(5),[9]),/return to Binary mode/);
  await assert.rejects(link.contactsBinary(),/return to Binary mode/);
  finishWrite();await stopping;
  assert.deepEqual(writes,['off','native']);assert.equal(source.blocksAppCommands(),false);
  source.feed(request);await source.responses;
  assert.deepEqual(writes,['off','native'],'Requests after the ordered barrier cannot acquire the old owner');
});

test('a lost off barrier retains uncertain ownership without answering canceled file reads',async()=>{
  const info=await packageInfo(),writes=[];
  const source=new CompanionFolderSource(async bytes=>{writes.push(Array.from(bytes));},info,info.blob,{transitionTimeout:10});
  source.mode='folder';await assert.rejects(source.stop(),/stop was not confirmed/);
  assert.equal(source.mode,'stopping');assert.equal(source.ownsStream(),true);
  source.feed(Uint8Array.of(77,83,1,1));await source.responses;
  source.feed(enc.encode('OK - Binary mode\r\n'));
  assert.equal(source.mode,'stopping','A late unowned acknowledgement must not silently restore Binary');
  assert.equal(writes.length,1);
});

for(const [reply,needsUpdate] of [
  ['ERR mOTA is out of memory',false],['ERR mOTA needs 128 free queue slots',false],['ERR busy',false],
  ['Unknown command',true],['ERR unsupported',true],['ERR not built with OTA_FOLDER_SERIAL',true],
])test('post-preservation folder failure classifies firmware support accurately: '+reply,async()=>{
  const link=new CompanionTransport({});let preserved=0;
  link.usbRawFrameMax=176;link.usbFolderSupported=true;link.usbFolderProbePending=true;
  link.preserveFolderInbox=async()=>{preserved++;};link.localCommand=async command=>{
    assert.equal(command,'ota folder');return reply;
  };
  await assert.rejects(link.ensureUsbFolderReady(),error=>{
    assert.ok(error.message.includes(reply));assert.equal(error.sourceUpdateRequired==='companion',needsUpdate);return true;
  });
  assert.equal(preserved,1);assert.equal(link.usbFolderProbePending,true);assert.equal(link.usbFolderAttached,false);
});

test('a contact refresh queued before folder handoff cannot emit Binary CMD4 into the file stream',async()=>{
  const f=await source(),info=await packageInfo();let release;
  try{
    await f.link.serve(info,info.blob,null);await f.link.txQueue;
    f.link.queue=new Promise(resolve=>{release=resolve;});
    const before=f.port.binaryFrames.filter(frame=>frame[0]===4).length;
    const refresh=f.link.contacts(),rejected=assert.rejects(refresh,/return to Binary mode before a contact refresh/);
    f.link.usbFolderAttached=true;await f.link.startUsbFolder();
    assert.equal(f.port.mode,'folder');release();await rejected;
    assert.equal(f.port.binaryFrames.filter(frame=>frame[0]===4).length,before);
    assert.equal(f.link.failure,null);assert.equal(f.port.detachCount,0);
    const read=new Promise(resolve=>f.port.sourceRequest(3,cat([0],le32(info.payloadOffset),le16(16)),16,resolve));
    assert.equal(hex(await read),hex(info.blob.slice(info.payloadOffset,info.payloadOffset+16)));
  }finally{release?.();await f.cleanup();}
});

test('lease maintenance uses one authenticated native-folder pause and resumes the same file without another pull',async()=>{
  const {maintain}=require('../docs/_javascript/lora_ota_lease.js');
  const info=await packageInfo(),ttl=[900,900],tuple='910.525,62.5,7,5';
  const reply=index=>command=>{
    if(command==='get tempradio')return 'TempRadio active: '+tuple+' '+ttl[index]+'s left,preamble=128';
    if(command==='ota status')return 'hw=legacy_folder_receiver | download: fetching 1/3 id='+hex(info.merkleRoot);
    if(command.startsWith('tempradio ')){ttl[index]=7200;return 'OK - same temporary lease';}
  };
  const f=await source({localReply:reply(0),remoteReply:reply(1)});
  try{
    f.link.investigationPermission='admin';
    await f.link.serve(info,info.blob,null);
    await f.link.remoteCommand(TARGET,'','ota pull '+hex(info.merkleRoot)+' flash');
    await wait(()=>f.port.manifestParts.length===2&&!f.port.pendingSourceRead);
    const before=[f.port.attachCount,f.port.detachCount],at=Date.now();
    const record={phase:'pull attempted',source:SOURCE,target:TARGET,id:hex(info.merkleRoot),
      packageHash:crypto.createHash('sha256').update(info.blob).digest('hex'),bodyHash:'dd'.repeat(8),
      radio:{tuple:[910.525,62.5,7,5],preamble:128,sourceProfile:0,targetProfile:0,
        explicitProfiles:[true,true],verifiedAt:at,expiresAt:at+899000}};
    const result=await maintain(f.link,TARGET,'',record);
    await wait(()=>!f.port.pendingSourceRead);
    assert.equal(result.state,'renewed');assert.equal(result.renewed,2);
    assert.equal(f.port.detachCount,before[1]+1);assert.equal(f.port.attachCount,before[0]+1);
    assert.equal(f.port.mode,'folder');assert.equal(f.link.failure,null);
    assert.equal(f.port.remoteCommands.filter(c=>c.startsWith('ota pull ')).length,1);
    assert.equal(f.port.remoteCommands.filter(c=>c.startsWith('tempradio ')).length,1);
    assert.equal(f.port.localCommands.filter(c=>c.startsWith('tempradio ')).length,1);
    assert.equal(f.port.remoteCommands.includes('ota cancel'),false);
    assert.ok(f.port.binaryFrames.every(frame=>frame.length<=176));
    const read=new Promise(resolve=>f.port.sourceRequest(3,cat([0],le32(info.payloadOffset),le16(16)),16,resolve));
    assert.equal(hex(await read),hex(info.blob.slice(info.payloadOffset,info.payloadOffset+16)));
  }finally{await f.cleanup();}
});
