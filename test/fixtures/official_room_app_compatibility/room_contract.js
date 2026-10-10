// SHA-pinned stock app room-message and login witness; no app parser copies.
// Reuse only the existing adapter's Dart runtime/scheduler setup. The methods
// below are extracted unchanged from the pinned app and run inside its VM.
"use strict";
const assert = require("assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const runtimeFile = path.join(__dirname, "../official_app_compatibility/app_contract.js");
const runtime = fs.readFileSync(runtimeFile, "utf8");
const boundary = runtime.indexOf("\nfunction connection() {");
assert(boundary > 0, "Existing app VM runtime boundary changed; review the adapter");
const moduleBoundary = {exports: {}};
vm.runInNewContext(runtime.slice(0, boundary) +
  "\nmodule.exports = {A, B, J, t, $, type, context, extract, constructor};", {
  require, process, module: moduleBoundary, console, Uint8Array, DataView,
  ArrayBuffer, Math, Date, Promise, queueMicrotask
});
const {A, B, J, t, $, type, context, extract, constructor} = moduleBoundary.exports;
type.c = type;
type.b = value => value instanceof Uint8Array || Boolean(value && value.then);
B.j.cg = (bytes, start, end) => bytes.slice(start, end);
B.O = {aA: text => new TextEncoder().encode(text)};
// UTF-8 is a Dart standard-library boundary, not the room protocol decoder.
B.a1 = {Mf: (_, bytes) => new TextDecoder("utf-8").decode(bytes)};
B.a6 = B.W = {a: false};
$.lw = $.c5 = () => {};
$.a4 = null;
A.au = value => Promise.resolve(value);
A.aN = bytes => Buffer.from(bytes).toString("hex");
A.jV = (items, predicate) => items.find(item => predicate.$1(item)) || null;
// SQLite's map and insert boundaries retain values produced by real app code.
A.z = () => ({fields: {}, l(_, key, value) { this.fields[key] = value.a; }});
context.Date = {now: () => 1900000000000};
for (const name of ["VU", "Nu", "be", "ax", "VW", "bZ7", "aS1", "aS2", "aS3", "aS4"])
  constructor(name);
for (const name of ["VW", "bZ7", "aS1", "aS2", "aS3", "aS4"])
  vm.runInContext(extract(`A.${name}.prototype={`), context);
Object.assign(A, vm.runInContext(`({${extract("czU(a,b,c,d,e,f,g,h,i,j,k,l,m,n){")}})`, context));
const methods = vm.runInContext(`({${extract("\nbA0(a,b,c){")},${extract("\nbCv(a,b,c){")}})`, context);

function connection() {
  const client = new A.wc(), listeners = new Map();
  client.a = {
    cu(_, key, factory) { if (!listeners.has(key)) listeners.set(key, factory.$0()); },
    i: (_, key) => listeners.get(key), Z: (_, key) => listeners.has(key),
    ey: (_, fn) => { for (const [key, value] of listeners) fn.$2(key, value); }
  };
  for (const name of ["eY", "N", "bCk", "bu", "dT", "W7"])
    client[name] = A.b0V.prototype[name];
  client.writes = [];
  client.d6 = async bytes => { client.writes.push(Buffer.from(bytes)); };
  return client;
}
async function settle() { for (let i = 0; i < 30; ++i) await Promise.resolve(); }
const serverKey = Uint8Array.from({length: 32}, (_, i) => i === 0 ? 77 : 0);
const ownerKey = Uint8Array.from({length: 32}, (_, i) => i === 0 ? 1 : 0);

async function decode(frame) {
  const client = connection(), decoded = [];
  client.eY(0, 7, {$1: value => decoded.push(value)}, t.z);
  client.aL9(Buffer.from(frame, "hex"));
  await settle();
  assert.equal(decoded.length, 1, "Actual app must emit one contact-message response");
  return decoded[0];
}

async function persist(message) {
  const rows = [], room = {b: serverKey, c: 3};
  const database = {
    gFh: () => null,
    IO: () => ({GL: async (_, row) => { rows.push(row.eI(null).fields); return rows.length; }}),
    abv: async () => false,
    abP(owner, contact, decoded) { return methods.bA0.call(this, owner, contact, decoded); },
    a33: async () => null
  };
  $.e_ = {d: ownerKey}; $.aj = database;
  // This is the stock app's ordinary/backlog message intake, with widget
  // notifications disabled and its SQL duplicate-query boundary mocked.
  await methods.bCv.call({k2: [room]}, message, false, false);
  return rows;
}

async function buildLogins() {
  const result = [];
  for (const password of ["", "admin", "legacy"]) {
    const client = connection();
    client.WM(serverKey, password).catch(() => {}); await settle();
    assert.equal(client.writes.length, 1);
    result.push({password, command: client.writes[0].toString("hex")});
  }
  return result;
}

async function checkPosts() {
  let count = 0;
  const ordering = [];
  for (const record of input.posts) {
    const message = await decode(record.frame);
    assert.equal(message.c, 2, "Topic must retain the standard signed-plain type");
    assert.equal(message.d, record.timestamp);
    assert.equal(message.e, Buffer.from(record.text, "hex").toString("utf8"));
    assert.equal(Buffer.from(message.f).toString("hex"), record.author);
    assert.equal(Buffer.from(message.a).toString("hex"), Buffer.from(serverKey).subarray(0, 6).toString("hex"));
    assert.equal(message.b, record.flood ? 2 : -1);
    assert.equal(message.r, record.version >= 3 ? 10.75 : null);
    const rows = await persist(message); assert.equal(rows.length, 1);
    assert.equal(rows[0].sender_timestamp, record.timestamp);
    assert.equal(rows[0].text, message.e);
    assert.equal(rows[0].room_post_author_pub_key_prefix, record.author);
    assert.equal(rows[0].from, Buffer.from(serverKey).toString("hex"));
    assert.equal(rows[0].to, Buffer.from(ownerKey).toString("hex"));
    assert.equal(rows[0].txt_type, 2);
    assert.equal(rows[0].status, "received");
    assert.equal(rows[0].timestamp, 1900000000000);
    if (record.group === "ordered") ordering.push(rows[0].sender_timestamp);
    if (record.group === "maximum" || record.group === "utf8")
      assert.equal(Buffer.byteLength(rows[0].text, "utf8"), 151);
    ++count;
  }
  assert.equal(ordering.length, 4);
  for (let i = 1; i < ordering.length; ++i) assert(ordering[i] > ordering[i - 1]);

  const sample = input.posts.find(record => record.group === "maximum" && record.version === 3);
  const wrongType = Buffer.from(sample.frame, "hex"); wrongType[11] = 0;
  const plain = await decode(wrongType.toString("hex"));
  assert.equal(plain.c, 0); assert.equal(plain.f, null);
  assert.notEqual(plain.e, Buffer.from(sample.text, "hex").toString("utf8"));
  const wrongServer = Buffer.from(sample.frame, "hex"); wrongServer[4] ^= 0x80;
  assert.equal((await persist(await decode(wrongServer.toString("hex")))).length, 0);
  const shortSigned = Buffer.from(sample.frame, "hex").subarray(0, 19);
  const client = connection(); assert.throws(() => client.aL9(shortSigned));
  return {post_cases: count, ordered_timestamps: ordering, negative_controls: 3};
}

async function checkLogins() {
  let count = 0;
  for (const record of input.logins) {
    const client = connection(); let completed = false;
    const pending = client.WM(serverKey, record.password).then(value => { completed = true; return value; });
    await settle(); assert.equal(client.writes[0].toString("hex"), record.command);
    client.aL9(Buffer.from(record.sent, "hex")); await settle();
    const unrelated = connection(); let unrelatedCompleted = false;
    unrelated.WM(serverKey, record.password).then(() => { unrelatedCompleted = true; }).catch(() => {});
    await settle(); unrelated.aL9(Buffer.from(record.sent, "hex")); await settle();
    const wrongServer = Buffer.from(record.frame, "hex"); wrongServer[2] ^= 0x80;
    // WM registers a one-shot success listener. A mismatched event consumes
    // it before the actual callback ignores the server prefix; use a separate
    // request so that stock behavior does not contaminate the valid case.
    unrelated.aL9(wrongServer); await settle(); assert.equal(unrelatedCompleted, false);
    assert.equal(completed, false);
    client.aL9(Buffer.from(record.frame, "hex")); await settle();
    assert.equal(completed, true, `Actual app login must complete: ${record.password || "guest"}`);
    const login = await pending;
    assert.equal(login.a, record.admin);
    assert.equal(Buffer.from(login.b).toString("hex"), Buffer.from(serverKey).subarray(0, 6).toString("hex"));
    assert.equal(login.c, record.timestamp);
    assert.equal(login.d, record.permissions);
    assert.equal(login.e, record.firmware_level);
    ++count;
  }
  assert.equal(count, 3);
  return {login_cases: count, guest_and_admin_distinct: true, wrong_server_ignored: true};
}

async function main() {
  if (input.mode === "build_logins") return console.log(JSON.stringify(await buildLogins()));
  if (input.mode === "posts") return console.log(JSON.stringify(await checkPosts()));
  if (input.mode === "logins") return console.log(JSON.stringify(await checkLogins()));
  throw Error(`Unknown room app mode: ${input.mode}`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
