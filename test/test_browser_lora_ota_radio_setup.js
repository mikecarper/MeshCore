'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const setup = require('../docs/_javascript/lora_ota_radio_setup.js');
const key = '12'.repeat(32), sourceKey = '34'.repeat(32);
const selected = {frequency: 909.95, bandwidth: 250, sf: 5, cr: 5, minutes: 120, preamble: 128};
const primary = setup.commands(selected), secondary = setup.commands(selected, {sourceProfile: 1, targetProfile: 1});

function fixture(options = {}) {
  let clock = 10000, protectedWrite = false, proofs = 0, targetAckAt = null;
  const events = [], overrides = new Map(options.overrides || []);
  const preferences = Object.fromEntries(['source', 'target'].map(label => [label, {
    rxps: options.factoryDefaults ? 'on' : 'off', hops: options.factoryDefaults ? '3' : '0',
    autofetch: options.autoPolicies ? 'signed' : 'off', autoinstall: options.autoPolicies ? 'trusted' : 'off'}]));
  const saved = {source: [910.5250244, 62.5, 7, 5], target: [910.5250244, 62.5, 7, 5]};
  const states = {source: [null, null], target: [null, null]};
  const savedSecondary = {source: options.sourceBusy ? '> 908.15,250,7,5,rxtx,48' : '> off (manual)',
    target: options.targetBusy ? '> 908.15,250,7,5,rxtx,48' : '> off (manual)'};
  const supported = {source: options.sourceSecondary !== false, target: options.targetSecondary !== false};
  if (options.adopt) { states.source[1] = {started: 0}; states.target[1] = {started: 0}; }
  const link = {
    browserRawSource: !options.repeater, localCliSupported: true, otaSupported: true, otaMode: 'guarded',
    sourceKey, knownRepeater: key, relays: [], serving: false, guard: false, authenticated: false, otaRadioProfile: 0, otaTargetProfile: 0,
    supportsOtaRadio2: options.sourceSupports !== false,
    async probeOtaRadio() { events.push(['probe']); this.supportsOtaRadio2 = options.sourceSupports !== false; },
    async verifyPeer(k, password) {
      events.push(['verify', k, password, clock]); assert.equal(k, key); proofs++;
      if (events.some(row => row[0] === 'select')) {
        assert.ok(clock - targetAckAt >= (options.targetDelay ?? 2500), 'authentication preceded target retune');
        assert.ok(live('source', this.otaRadioProfile), 'source selected profile not live');
        assert.ok(live('target', this.otaTargetProfile), 'target selected profile not live');
      }
      this.guard = this.authenticated = true; return key;
    },
    async closeRadioSession() {
      events.push(['close']); if (options.closeFailure) throw Error('close failed');
      this.guard = this.authenticated = false;
      if (options.sourceWindowRace && targetAckAt !== null) overrides.set('source:get tempradio', '> 909.95,250,5,5,128');
    },
    async applyTemporaryRadio(command) {
      events.push(['protected-local', command]); assert.equal(this.guard, false);
      protectedWrite = true; this.guard = true;
      try { return await read('source', command); } finally { protectedWrite = false; this.guard = false; }
    },
    async selectOtaRadio(profile) {
      events.push(['select', profile]); assert.equal(this.guard, false); assert.ok(profile === 0 || profile === 1);
      if (options.selectFailure) throw Error('profile selection failed'); this.otaRadioProfile = profile;
    },
    async resetRemoteSession(k, password) {
      events.push(['reset', k, password]); assert.equal(this.guard, false);
      assert.equal(!!options.repeater, false, 'Repeater reset cannot reuse a released peer owner');
      if (options.resetFailure) throw Error('reset failed'); this.authenticated = false;
    },
    async localCommand(command) { return read('source', command); },
    async remoteCommand(k, password, command) {
      assert.equal(k, key); assert.equal(this.guard, true, 'remote request used a closed guard');
      return read('target', command);
    }
  };
  function live(label, profile) {
    const state = states[label][profile];
    return state && !options.pendingForever && clock - state.started >= (label === 'source' ? 1500 : 2000);
  }
  function config(label, profile) {
    const state = states[label][profile];
    if (!state) return profile ? '> off' : (label === 'source' ? 'TempRadio inactive' : '> off');
    const pre = label === 'source' ? options.sourcePreamble ?? 128 : options.targetPreamble ?? 128;
    const frequency = label === 'target' ? options.targetFrequency ?? 909.95 : 909.95;
    if (profile) return `> ${frequency},250.000,5,5,rxtx,${pre}; 0d2h0m (120 min) left`;
    if (!live(label, 0)) return 'TempRadio pending: 909.950,250.00,5,5,preamble=128';
    return label === 'source' ? `TempRadio active: 909.950,250.00,5,5 7190s left,preamble=${pre}` : `> ${frequency},250.000,5,5,${pre}`;
  }
  async function read(label, command) {
    events.push([label, command, clock]);
    if (overrides.has(label + ':' + command)) {
      const value = overrides.get(label + ':' + command); return typeof value === 'function' ? value() : value;
    }
    if (command === 'set radio.rxps off' || /^ota config (?:autofetch|autoinstall|hops) /.test(command)) {
      if (!options.ignorePrerequisiteWrite) {
        if (command === 'set radio.rxps off') preferences[label].rxps = 'off';
        else { const parts = command.split(' '); preferences[label][parts[2]] = parts[3]; }
      }
      if (options.prerequisiteTimeout === label + ':' + command) throw Error('Saved prerequisite timed out; result unknown');
      return 'OK - saved';
    }
    if (/^(?:tempradio |set tempradio2 )/.test(command)) {
      const profile = command.startsWith('set ') ? 1 : 0;
      assert.equal(command, setup.commands(selected, {sourceProfile: profile, targetProfile: profile})[label]);
      if (label === 'source' && !options.repeater) assert.equal(protectedWrite, true, 'Companion retune must use protective local guard');
      states[label][profile] = {started: clock};
      if (label === 'target') targetAckAt = clock;
      if (options[label + 'Timeout']) throw Error('Command timed out; result is unknown');
      return options[label + 'Ack'] ?? 'OK - temporary parameters applying';
    }
    if (command === 'get public.key') return '> ' + (events.some(row => row[0] === 'select') && options.wrongFreshKey ? '56'.repeat(32) : key.toUpperCase());
    if (command === 'get radio') {
      if (targetAckAt !== null && options.savedChanged && label === 'source') return '> 909.95,250,5,5';
      return '> ' + saved[label].join(',');
    }
    if (command === 'get tempradio') {
      if (targetAckAt !== null && states.source.some(Boolean) && options.pollFailure && label === 'source') throw Error('GET timeout');
      if (states.source.some(Boolean) && options.lateRead && label === 'source') clock += 35001;
      return config(label, 0);
    }
    if (command === 'get radio.rxps') return '> ' + preferences[label].rxps + ',level=8,preamble=32,rx=1252,sleep=6424';
    if (command === 'ota config') return `ota config: autofetch=${preferences[label].autofetch} autoinstall=${preferences[label].autoinstall} hops=${preferences[label].hops}`;
    if (command === 'ota stats') return options.sourceDownload ? 'fetch active id=12345678' : 'serve idle | fetch idle';
    if (command === 'ota folder') return options.sourceFolder ? 'folder=on' : 'folder=off';
    if (command === 'ota status') return options.targetDownload ? 'download: id=12345678 active' : 'storage:flash | no download';
    if (command === 'get radio2') return supported[label] ? savedSecondary[label] : 'Error: unsupported';
    if (command === 'get tempradio2') {
      if (states.source.some(Boolean) && label === 'source' && options.pollFailure) throw Error('GET timeout');
      if (states.source.some(Boolean) && label === 'source' && options.lateRead) clock += 35001;
      return supported[label] ? config(label, 1) : 'Error: unsupported';
    }
    if (command === 'get radio2.cross') return supported[label] ? '> off' : 'Error: unsupported';
    if (command === 'get radio2.status') {
      const isLive = live(label, 1), mode = isLive ? 'rxtx' : 'off';
      const pre = isLive ? (label === 'source' ? options.sourcePreamble ?? 128 : options.targetPreamble ?? 128) : 0;
      return `> ${mode}; RX=43,0 TX=0,0 switches=60089 errors=0 max=2930us preamble=32,${pre}; test=1/4,1/4 pending budget=2930us`;
    }
    if (['get radioat', 'get tempradioat'].includes(command)) return label === 'source' && !options.repeater ? 'Error: unsupported' : '> -none-';
    if (['get radioat2', 'get tempradioat2'].includes(command)) return supported[label] ? '> slots:' : 'Error: unsupported';
    throw Error('Unexpected command: ' + label + ':' + command);
  }
  return {link, events, overrides, states, savedSecondary, options: {now: () => clock, wait: async ms => {events.push(['wait', ms]); clock += ms;}},
    writes: () => events.filter(row => /^(?:tempradio(?:\.ifidle)? |set tempradio2 |set radio|normalradio)/.test(row[1] || '')),
    preferences, before: () => JSON.stringify({saved, savedSecondary})};
}

test('recommend prefers909.950 with separated fallback; commands strictly validate numeric values and profiles', () => {
  assert.equal(setup.recommend([910.525, 62.5, 7, 5]), 909.95);
  assert.equal(setup.recommend([909.95, 250, 5, 5]), 908.5);
  assert.equal(setup.recommend([909.7, 500, 5, 5]), 908.5);
  for (const tuple of [[868, 125, 7, 5], [433, 125, 7, 5], ['910.5', 125, 7, 5], [910.5, 999, 7, 5], null]) assert.equal(setup.recommend(tuple), null);
  assert.deepEqual(primary, {target: 'tempradio 909.95,250,5,5,120,128', source: 'tempradio 909.95,250,5,5,120,128', manual: 'tempradio 909.95,250,5,5,120,128'});
  assert.equal(secondary.target, 'set tempradio2 909.95,250,5,5,rxtx,120,128');
  for (const [field, values] of Object.entries({frequency: [NaN, Infinity, '909.95', 149, 2501], bandwidth: ['250', 249], sf: [4, 13, 5.5], cr: [3, 9], minutes: [0, 10081, 1.5], preamble: [0, 65536, '128']}))
    for (const value of values) assert.throws(() => setup.commands({...selected, [field]: value}));
  assert.throws(() => setup.commands(selected, {sourceProfile: 2}));
});

test('both unused secondary profiles are preferred, live pending is not credited, saved profiles remain unchanged', async () => {
  const f = fixture(), before = f.before(), row = await setup.prepare(f.link, key, 'pw', selected, f.options);
  assert.equal(row.ready, true); assert.equal(row.sourceProfile, 1); assert.equal(row.targetProfile, 1);
  assert.deepEqual(f.writes().map(row => row.slice(0, 2)), [['target', secondary.target], ['protected-local', secondary.source], ['source', secondary.source]]);
  assert.equal(f.before(), before); assert.equal(f.link.otaTargetProfile, 1); assert.equal(f.link.otaRadioProfile, 1);
  assert.equal(f.events.filter(row => row[0] === 'verify').length, 3);
  assert.ok(f.events.find(row => row[0] === 'wait'));
  assert.ok(f.events.findIndex(row => row[0] === 'select') > f.events.findIndex(row => row[1] === secondary.source));
  assert.ok(f.events.some(row => row[0] === 'target' && row[1] === 'get radio2.status'));
});

test('source and target choices are independent; busy or explicitly unsupported secondary uses primary exactly once', async () => {
  for (const options of [{sourceSupports: false}, {targetBusy: true}, {sourceBusy: true}, {sourceSecondary: false, sourceSupports: false, targetSecondary: false}, {sourceBusy: true, targetBusy: true}]) {
    const f = fixture(options), before = f.before(), row = await setup.prepare(f.link, key, '', selected, f.options);
    assert.equal(row.sourceProfile, options.sourceSupports === false || options.sourceBusy ? 0 : 1);
    assert.equal(row.targetProfile, options.targetBusy || options.targetSecondary === false ? 0 : 1);
    assert.equal(f.before(), before);
    assert.equal(f.writes().filter(row => row[0] === 'target').length, 1);
    assert.equal(f.writes().filter(row => row[0] === 'source').length, 1);
    assert.ok(f.writes().every(row => !row[1].includes('.ifidle')));
  }
});

test('scheduled secondary remains untouched and triggers primary fallback without clearing schedules', async () => {
  const f = fixture({overrides: [['target:get radioat2', '> slots: 1@2000000000']]});
  const row = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(row.targetProfile, 0); assert.equal(row.sourceProfile, 1);
  assert.equal(f.writes().filter(row => row[0] === 'target')[0][1], primary.target);
});

test('direct Repeater uses local setter and selected OTA profile without Companion guard helper', async () => {
  const f = fixture({repeater: true}); delete f.link.applyTemporaryRadio;
  const row = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(row.sourceProfile, 1); assert.equal(f.writes().filter(row => row[0] === 'source').length, 1);
  assert.equal(f.writes().some(row => row[0] === 'protected-local'), false);
  assert.equal(f.events.some(row => row[0] === 'reset'), false);
  assert.equal(f.events.filter(row => row[0] === 'verify').length, 3);
});

test('unknown capability/read errors and active primary preflight refuse all writes', async () => {
  for (const [command, value] of [['get public.key', '> ' + '56'.repeat(32)], ['get radio', 'malformed'],
    ['get tempradio', 'TempRadio pending: 909.950,250,5,5'], ['get tempradio', '> 909.95,250,5,5,128'],
    ['get radio.rxps', '> possibly on'], ['ota config', 'autofetch=off autoinstall=off hops=0 hops=1'],
    ['get radio2', 'perhaps off'], ['get tempradio2', 'maybe unsupported'], ['get radio2.cross', 'unset'],
    ['get radioat', '> 1:910.5,125,7,5@2000000000'], ['get tempradioat', 'Error: unsupported'],
    ['get radioat2', 'Error: unsupported'], ['get tempradioat2', 'malformed']]) {
    const f = fixture({overrides: [['target:' + command, value]]});
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options)); assert.equal(f.writes().length, 0, command);
  }
  const f = fixture({overrides: [['source:get tempradioat2', () => {throw Error('GET timeout');}]]});
  await assert.rejects(setup.prepare(f.link, key, '', selected, f.options)); assert.equal(f.writes().length, 0);
});

test('unsafe transports, identity and missing protective API refuse before writes', async () => {
  for (const change of [{otaSupported: false}, {localCliSupported: false}, {serving: true}, {relays: [key]}, {sourceKey: key}, {knownRepeater: '56'.repeat(32)}, {applyTemporaryRadio: null}, {selectOtaRadio: null}]) {
    const f = fixture(); Object.assign(f.link, change);
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options)); assert.equal(f.writes().length, 0);
  }
});

test('a plain target setter is sent once and never retried or replaced on unsupported/lost reply', async () => {
  for (const options of [{targetAck: 'Error: unsupported'}, {targetAck: 'pending'}, {targetTimeout: true}]) {
    const f = fixture({...options, targetSecondary: false});
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), /partially active/);
    assert.equal(f.writes().length, 1); assert.equal(f.writes()[0][1], primary.target);
  }
});

test('secondary setter failure never switches to primary, retries or guesses a restore', async () => {
  for (const options of [{targetAck: 'Error: unsupported'}, {targetTimeout: true}, {sourceTimeout: true}, {sourceAck: 'Error: busy'}, {closeFailure: true}, {sourceWindowRace: true}]) {
    const f = fixture(options);
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options));
    assert.equal(f.writes().filter(row => row[0] === 'target').length, options.closeFailure ? 0 : 1);
    assert.ok(f.writes().filter(row => row[0] === 'source').length <= 1);
    assert.ok(f.writes().every(row => !row[1].startsWith('tempradio ') && !row[1].startsWith('normalradio')));
  }
});

test('35s poll deadline, late reads and failed profile selection retain failures without extra writes', async () => {
  for (const options of [{pendingForever: true}, {lateRead: true}, {pollFailure: true}, {selectFailure: true}, {resetFailure: true}]) {
    const f = fixture(options);
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), /partially active/);
    assert.equal(f.writes().filter(row => row[0] === 'target').length, 1);
    assert.equal(f.writes().filter(row => row[0] === 'source').length, 1);
    assert.ok(f.events.filter(row => row[1] === 'get tempradio2').length < 80);
  }
});

test('post-retune key, live target preamble and both saved baselines are mandatory', async () => {
  for (const options of [{wrongFreshKey: true}, {targetFrequency: 908.5}, {targetPreamble: 64}, {savedChanged: true}]) {
    const f = fixture(options);
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options));
    assert.equal(f.writes().filter(row => row[0] === 'target').length, 1);
    assert.ok(f.writes().filter(row => row[0] === 'source').length <= 1);
  }
});

test('shared secondary parser requires live RXTX and configured preamble, not just the pending getter', async () => {
  const replies = new Map([['get tempradio2', '> 909.950,250.000,7,5,rxtx,128; 0d2h0m (120 min) left'],
    ['get radio2.status', '> off; RX=0,0 TX=0,0 switches=0 errors=0 max=0us preamble=32,0']]);
  const read = async c => replies.get(c);
  assert.equal((await setup.readTemporary(read, 1)).state, 'pending');
  replies.set('get radio2.status', '> rxtx; RX=0,0 TX=0,0 switches=1 errors=0 max=2900us preamble=32,128; test=1/4,1/4 pending budget=2900us');
  const state = await setup.readTemporary(read, 1); assert.equal(state.state, 'active'); assert.deepEqual(state.radio, [909.95, 250, 7, 5]);
  replies.set('get radio2.status', '> radio2 change pending reply transmission; previous channel retained');
  assert.equal((await setup.readTemporary(read, 1)).state, 'pending');
  replies.set('get radio2.status', 'unverified'); await assert.rejects(setup.readTemporary(read, 1));
  replies.set('get tempradio2', '> 909.950,250.000,7,5,rx,128'); await assert.rejects(setup.readTemporary(read, 1));
});

test('factory RXPS-on and hops3 plus known automatic policies are changed once after both baselines', async () => {
  const f = fixture({factoryDefaults: true, autoPolicies: true}), before = f.before();
  const result = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(result.changedSettings.length, 8); assert.equal(f.before(), before);
  for (const endpoint of ['source', 'target']) {
    assert.deepEqual(f.preferences[endpoint], {rxps: 'off', hops: '0', autofetch: 'off', autoinstall: 'off'});
    for (const command of ['set radio.rxps off', 'ota config autofetch off', 'ota config autoinstall off', 'ota config hops 0'])
      assert.equal(f.events.filter(row => row[0] === endpoint && row[1] === command).length, 1);
  }
  const firstWrite = f.events.findIndex(row => row[1] === 'set radio.rxps off');
  assert.ok(f.events.slice(0, firstWrite).some(row => row[0] === 'target' && row[1] === 'get tempradioat2'));
  assert.ok(f.events.slice(0, firstWrite).some(row => row[0] === 'source' && row[1] === 'ota folder'));
  for (const change of result.changedSettings) {
    assert.equal(change.confirmed, true); assert.equal(change.key, change.endpoint === 'source' ? sourceKey : key);
    assert.ok(change.restoreCommand); assert.notEqual(change.before, change.after);
  }
  assert.equal(result.changedSettings.find(row => row.setting === 'radio.rxps').restoreCommand, 'set radio.rxps level 8 preamble 32');
  const targetLastPolicy = f.events.findIndex(row => row[0] === 'target' && row[1] === 'ota config hops 0');
  const sourceFirstPolicy = f.events.findIndex(row => row[0] === 'source' && row[1] === 'set radio.rxps off');
  assert.ok(targetLastPolicy < sourceFirstPolicy);
  assert.ok(f.events.slice(targetLastPolicy, sourceFirstPolicy).some(row => row[0] === 'close'));
  assert.ok(f.events.slice(sourceFirstPolicy, f.events.findIndex(row => row[1] === secondary.target)).some(row => row[0] === 'verify'));
});

test('lost prerequisite write ACK stops without retry, radio mutation or guessed restoration', async () => {
  const f = fixture({factoryDefaults: true, prerequisiteTimeout: 'target:set radio.rxps off'});
  await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), error => {
    assert.equal(error.changedSettings.length, 1); assert.equal(error.changedSettings[0].uncertain, true);
    assert.equal(error.changedSettings[0].confirmed, false); return /Saved prerequisite/.test(error.message);
  });
  assert.equal(f.events.filter(row => row[0] === 'target' && row[1] === 'set radio.rxps off').length, 1);
  assert.equal(f.events.filter(row => /^(?:tempradio |set tempradio2 )/.test(row[1] || '')).length, 0);
  assert.equal(f.preferences.target.rxps, 'off'); // applied despite lost ACK; never undo it blindly
});

test('unknown policy and existing OTA work stop before any prerequisite or radio mutation', async () => {
  for (const options of [
    {factoryDefaults: true, overrides: [['target:ota config', 'autofetch=on autoinstall=off hops=3']]},
    {factoryDefaults: true, overrides: [['target:get radio.rxps', 'on,unverified']]},
    {factoryDefaults: true, sourceDownload: true}, {factoryDefaults: true, sourceFolder: true}, {factoryDefaults: true, targetDownload: true}
  ]) {
    const f = fixture(options); await assert.rejects(setup.prepare(f.link, key, '', selected, f.options));
    assert.equal(f.events.filter(row => /^(?:set |tempradio |ota config (?:autofetch|autoinstall|hops) )/.test(row[1] || '')).length, 0);
  }
});

test('failed prerequisite readback is not retried; known unsupported RXPS needs no write', async () => {
  const f = fixture({factoryDefaults: true, ignorePrerequisiteWrite: true});
  await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), /failed readback/);
  assert.equal(f.events.filter(row => row[1] === 'set radio.rxps off').length, 1);
  const supported = fixture({overrides: [['source:get radio.rxps', '> off,unsupported']]});
  const result = await setup.prepare(supported.link, key, '', selected, supported.options);
  assert.deepEqual(result.changedSettings, []);
});

test('matching active secondary leases are adopted after fresh identity proof without rewriting or duplicating primary', async () => {
  const f = fixture({adopt: true}), result = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(result.sourceProfile, 1); assert.equal(result.targetProfile, 1);
  assert.equal(result.adoptedSource, true); assert.equal(result.adoptedTarget, true);
  assert.deepEqual(result.changedSettings, []); assert.equal(f.writes().length, 0);
  assert.equal(f.events.filter(row => row[0] === 'verify').length, 3);
  for (const options of [{adopt: true, sourceSupports: false}, {adopt: true, pendingForever: true}, {adopt: true, targetPreamble: 64}]) {
    const failed = fixture(options); await assert.rejects(setup.prepare(failed.link, key, '', selected, failed.options), /duplicate/);
    assert.equal(failed.writes().length, 0);
  }
});

test('actual RXPS effective fields and continuous modes retain the saved restore setting', async () => {
  for (const mode of ['continuous-fast', 'continuous-fallback', 'duty-cycle']) {
    const f = fixture({factoryDefaults: true});
    f.overrides.set('source:get radio.rxps', () => `> ${f.preferences.source.rxps},level=6,preamble=16,rx=41704,sleep=11882,mode=${mode},effective-level=8,effective-preamble=64`);
    const result = await setup.prepare(f.link, key, '', selected, f.options);
    assert.equal(result.changedSettings.find(row => row.endpoint === 'source' && row.setting === 'radio.rxps').restoreCommand,
      'set radio.rxps level 6 preamble 16');
  }
  for (const suffix of [',mode=unknown', ',effective-level=11', ',effective-preamble=0', ',effective-level=8,effective-level=8', ',secret=value']) {
    const f = fixture({factoryDefaults: true, overrides: [['source:get radio.rxps', '> on,level=6,preamble=16,rx=41704,sleep=11882' + suffix]]});
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), /power-saving/);
    assert.equal(f.writes().length, 0);
  }
});

test('forced radio crossing blocks all saved and temporary writes while automatic crossing uses primary fallback', async () => {
  for (const label of ['source', 'target']) {
    const f = fixture({factoryDefaults: true, overrides: [[label + ':get radio2.cross', '> on']]});
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), /crossing on/);
    assert.equal(f.events.filter(row => /^(?:set |tempradio |ota config \w+ )/.test(row[1] || '')).length, 0);
  }
  const f = fixture({overrides: [['source:get radio2.cross', '> auto'], ['target:get radio2.cross', '> auto']]});
  const result = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(result.sourceProfile, 0); assert.equal(result.targetProfile, 0);
  assert.equal(f.events.filter(row => /cross /.test(row[1] || '')).length, 0);
});

test('existing download adopts only both exact active secondary leases with unchanged required preferences', async () => {
  const f = fixture({adopt: true, targetDownload: true}), result = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(result.sourceProfile, 1); assert.equal(result.targetProfile, 1);
  assert.equal(result.adoptedSource, true); assert.equal(result.adoptedTarget, true);
  assert.deepEqual(result.changedSettings, []);
  assert.equal(f.events.filter(row => /^(?:set |tempradio |ota config \w+ )/.test(row[1] || '')).length, 0);
  assert.ok(f.events.some(row => row[0] === 'select' && row[1] === 1));
  for (const options of [{targetDownload: true}, {adopt: true, targetDownload: true, factoryDefaults: true},
    {adopt: true, targetDownload: true, autoPolicies: true}, {adopt: true, targetDownload: true, sourceBusy: true}]) {
    const failed = fixture(options); await assert.rejects(setup.prepare(failed.link, key, '', selected, failed.options));
    assert.equal(failed.events.filter(row => /^(?:set |tempradio |ota config \w+ )/.test(row[1] || '')).length, 0);
  }
});

test('automatic crossing with an existing temporary secondary and overlapping busy secondaries block before changes', async () => {
  for (const label of ['source', 'target']) {
    for (const options of [
      {adopt: true, overrides: [[label + ':get radio2.cross', '> auto']]},
      {overrides: [[label + ':get radio2', '> 909.900,250,7,5,rxtx,48']]},
      {overrides: [[label + ':get tempradio2', '> 909.900,250,7,5,rxtx,48; 0d1h0m (60 min) left']]}
    ]) {
      const f = fixture({...options, factoryDefaults: true});
      await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), /crossing|overlaps/);
      assert.equal(f.events.filter(row => /^(?:set |tempradio |ota config \w+ )/.test(row[1] || '')).length, 0);
    }
  }
});

test('pair-only or plain RXPS readback never invents a saved-mode restore command', async () => {
  for (const raw of ['on', 'on,41704,11882']) {
    const f = fixture({factoryDefaults: true});
    f.overrides.set('source:get radio.rxps', () => f.preferences.source.rxps === 'on' ? '> ' + raw : '> off');
    const result = await setup.prepare(f.link, key, '', selected, f.options);
    const change = result.changedSettings.find(row => row.endpoint === 'source' && row.setting === 'radio.rxps');
    assert.equal(change.restoreCommand, null); assert.equal(change.beforeRaw, raw);
  }
});
