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
