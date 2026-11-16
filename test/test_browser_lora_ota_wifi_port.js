'use strict';
const assert = require('node:assert/strict');
const {create, validateUrl} = require('../docs/_javascript/lora_ota_wifi_port.js');
const URL = 'ws://127.0.0.1:8765/abcdefghijklmnopqrstuvwx';

class FakeSocket {
  static instances = [];
  constructor(url) {
    this.url = url; this.readyState = 0; this.bufferedAmount = 0; this.sent = [];
    FakeSocket.instances.push(this);
  }
  start() { this.readyState = 1; this.onopen?.(); }
  send(data) { this.sent.push(Uint8Array.from(data)); }
  message(data) { this.onmessage?.({data}); }
  disconnect() { this.readyState = 3; this.onclose?.(); }
  close() { if (this.readyState !== 3) this.disconnect(); }
}

async function main() {
  assert.equal(validateUrl(URL), URL);
  assert.equal(validateUrl('192.168.1.96'), 'ws://192.168.1.96:5005/ota');
  assert.equal(validateUrl('10.2.3.4'), 'ws://10.2.3.4:5005/ota');
  assert.equal(validateUrl('172.31.2.5'), 'ws://172.31.2.5:5005/ota');
  assert.equal(validateUrl('ws://192.168.1.96:5005/ota'), 'ws://192.168.1.96:5005/ota');
  for (const bad of ['http://127.0.0.1:8765/abcdefghijklmnopqrstuvwx',
    'ws://localhost:8765/abcdefghijklmnopqrstuvwx',
    'ws://192.168.1.2:8765/abcdefghijklmnopqrstuvwx',
    'ws://127.0.0.1:8766/abcdefghijklmnopqrstuvwx',
    'ws://127.0.0.1:8765/short', URL + '?token=x', URL + '#x',
    '8.8.8.8','172.32.0.1','192.168.001.2','https://192.168.1.2',
    'ws://192.168.1.2:5005/ota?x=1','ws://192.168.1.2:5005/other'])
    assert.throws(() => create(bad, {WebSocket:FakeSocket}), /private IPv4/);

  const port = create(URL, {WebSocket:FakeSocket, openTimeout:100, drainTimeout:100});
  const opening = port.open({baudRate:115200, bufferSize:4096});
  const socket = FakeSocket.instances.at(-1);
  socket.start(); await opening;
  assert.equal(port.connected, true);
  const reader = port.readable.getReader(), writer = port.writable.getWriter();
  socket.message(Uint8Array.of(1, 2, 3).buffer);
  assert.deepEqual((await reader.read()).value, Uint8Array.of(1, 2, 3));
  await writer.write(Uint8Array.of(4, 5));
  assert.deepEqual(socket.sent, [Uint8Array.of(4, 5)]);

  // A disconnected socket fails pending reads and must not accept more writes.
  const read = reader.read(); socket.disconnect();
  await assert.rejects(read, /disconnected/);
  await assert.rejects(writer.write(Uint8Array.of(6)), /disconnected/);
  reader.releaseLock(); writer.releaseLock(); await port.close();

  // A reconnect creates a new socket, never reuses a stale stream.
  const reopened = port.open(); const second = FakeSocket.instances.at(-1);
  assert.notEqual(second, socket); second.start(); await reopened;
  const secondReader = port.readable.getReader(), secondWriter = port.writable.getWriter();
  await secondReader.cancel(); secondReader.releaseLock();
  try { await secondWriter.close(); } catch (_) {}
  secondWriter.releaseLock(); await port.close();

  // Fail closed on non-binary data and excessive unread bytes.
  const reopenBad = port.open(); const badSocket = FakeSocket.instances.at(-1);
  badSocket.start(); await reopenBad;
  const badReader = port.readable.getReader(), badRead = badReader.read();
  badSocket.message('unexpected text');
  await assert.rejects(badRead, /invalid or excessive/);
  badReader.releaseLock(); await port.close();

  const slow = create(URL, {WebSocket:FakeSocket, drainTimeout:20});
  const slowOpen = slow.open(); const slowSocket = FakeSocket.instances.at(-1);
  slowSocket.start(); await slowOpen;
  const slowWriter = slow.writable.getWriter();
  slowSocket.bufferedAmount = 1024 * 1024;
  await assert.rejects(slowWriter.write(Uint8Array.of(1)), /timed out/);
  assert.equal(slow.connected, false);
  slowWriter.releaseLock(); await slow.close();

  const tooSlow = create(URL, {WebSocket:FakeSocket, openTimeout:10});
  await assert.rejects(tooSlow.open(), /timed out/);
  assert.equal(tooSlow.connected, false);
  console.log('PASS Wi-Fi bridge serial-compatible streams');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
