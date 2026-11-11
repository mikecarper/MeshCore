'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {DirectSession} = require('../docs/_javascript/lora_ota_session.js');

function fixture() {
  const calls = [], id = '01020304', failure = Error('USB disconnected');
  const link = {sourceKey:'aa'.repeat(32),
    async remoteCommand(_key, _password, command) { calls.push(command); return command === 'ota ls' ? id : 'OK'; },
    async serve() { calls.push('serve'); }, async stopServing() { calls.push('detach'); },
    async resetRemoteSession() { calls.push('proof'); this.failure = failure; throw failure; }};
  const session = new DirectSession(link, 'bb'.repeat(32), '', {merkleRoot:Uint8Array.of(1,2,3,4)}, {pollMs:1});
  session.inspect = async () => ({download:'idle'});
  session.readDownload = async () => ({state:'idle'});
  session.planRebootChannel = async () => ({change:false, savedTarget:[910.525,62.5,7,5]});
  session.installMode = 'hash'; session.installHash = 'ab'.repeat(32);
  return {session, link, calls, id, failure};
}

test('disconnect during the final pre-pull read never starts a download', async () => {
  const f = fixture();
  f.session.readDownload = async () => { f.session.stop(); return {state:'idle'}; };
  await assert.rejects(f.session.start(), /stopped/i);
  assert.ok(!f.calls.some(c => c.startsWith('ota pull ')));
  assert.ok(f.calls.includes('detach'));
});

test('disconnect during ready read does not report a successful transfer', async () => {
  const f = fixture(); let reads = 0;
  f.session.readDownload = async () => {
    if (++reads === 1) return {state:'idle'};
    f.session.stop(); return {id:f.id, state:'ready', status:'ready'};
  };
  await assert.rejects(f.session.start(), /stopped/i);
  assert.equal(f.calls.filter(c => c.startsWith('ota pull ')).length, 1);
  assert.ok(f.calls.includes('detach'));
});

test('disconnect while checking the reboot channel never sends install', async () => {
  const f = fixture();
  f.session.inspect = async () => ({download:'ready'});
  f.session.readDownload = async () => ({state:'ready', id:f.id});
  f.session.planRebootChannel = async () => { f.session.stop(); return {change:false}; };
  await assert.rejects(f.session.install(), /stopped/i);
  assert.ok(!f.calls.some(c => c.startsWith('ota install')));
});

test('lost USB during reboot verification stops immediately instead of polling a failed connection', async () => {
  const f = fixture();
  f.session.inspect = async () => ({download:'ready'});
  f.session.readDownload = async () => ({state:'ready', id:f.id});
  const cleanup = setTimeout(() => f.session.stop(), 100);
  try { await assert.rejects(f.session.install(), error => error === f.failure); }
  finally { clearTimeout(cleanup); }
  assert.equal(f.calls.filter(c => c === 'proof').length, 1);
  assert.equal(f.calls.filter(c => c.startsWith('ota install')).length, 1);
});

test('disconnect while install reply is pending never retunes the source', async () => {
  const f = fixture();
  f.session.inspect = async () => ({download:'ready'});
  f.session.readDownload = async () => ({state:'ready', id:f.id});
  f.session.planRebootChannel = async () => ({change:true});
  f.link.remoteCommand = async (_key, _password, command) => {
    f.calls.push(command); f.session.stop(); return 'OK';
  };
  f.session.reconnectChannel = async () => { f.calls.push('retune'); };
  await assert.rejects(f.session.install(), /stopped/i);
  assert.equal(f.calls.filter(c => c.startsWith('ota install')).length, 1);
  assert.ok(!f.calls.includes('retune'));
});
