const test = require('node:test');
const assert = require('node:assert/strict');
const {control} = require('../docs/_javascript/lora_ota_repeat.js');
const source = 'a'.repeat(64), target = 'b'.repeat(64), relay = 'c'.repeat(64);
function fake() {
  let state = 'on', calls = [];
  const command = async (where, cmd) => {
    calls.push([where, cmd]);
    if (cmd === 'get repeat') return '> ' + state;
    if (cmd === 'set repeat off') { state = 'off'; return 'OK'; }
    if (cmd === 'set repeat on') { state = 'on'; return 'OK'; }
    throw Error('unexpected');
  };
  return {sourceKey:source, calls, localCommand:c => command('local', c),
    verifyPeer:async k => calls.push(['proof', k]),
    remoteCommand:(k, p, c) => command(k, c)};
}
test('forwarding changes require explicit action and verify their result', async () => {
  const link = fake();
  assert.equal(await control(link, source, '', [], 'read'), 'on');
  assert.deepEqual(link.calls, [['local', 'get repeat']]);
  assert.equal(await control(link, target, '', [], 'off'), 'off');
  assert.deepEqual(link.calls.slice(1), [['proof', target], [target, 'get repeat'], [target, 'set repeat off'], [target, 'get repeat']]);
  assert.equal(await control(link, target, '', [], 'on'), 'on');
});
test('never disable forwarding on a relay in the selected admin route', async () => {
  const link = fake();
  await assert.rejects(control(link, relay, '', [relay], 'off'), /selected route/);
  assert.deepEqual(link.calls, []);
});
test('never repeat a write after a lost acknowledgement', async () => {
  const link = fake(); let writes = 0;
  link.localCommand = async cmd => {
    if (cmd === 'get repeat') return '> on';
    writes++; throw Error('timeout');
  };
  await assert.rejects(control(link, source, '', [], 'off'), /not confirmed/);
  assert.equal(writes, 1);
});
test('radio-specific flood selection uses the distinct setting', async () => {
  const link = fake();
  link.localCommand = async cmd => {
    link.calls.push(['local', cmd]);
    if (cmd === 'get radio.flood') return '> on';
    throw Error('unexpected');
  };
  assert.equal(await control(link, source, '', [], 'read', 'radio.flood'), 'on');
  assert.deepEqual(link.calls, [['local', 'get radio.flood']]);
  await assert.rejects(control(link, source, '', [], 'read', 'bad'), /Invalid/);
});
