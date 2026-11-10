'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const setup = require('../docs/_javascript/lora_ota_radio_setup.js');
const key = '12'.repeat(32), sourceKey = '34'.repeat(32);
const selected = {frequency: 909.95, bandwidth: 250, sf: 5, cr: 5, minutes: 120, preamble: 128};
const primary = setup.commands(selected), trial = setup.commands({...selected, minutes: 2}), secondary = setup.commands(selected, {sourceProfile: 1, targetProfile: 1});

function fixture(options = {}) {
  let clock = 10000, protectedWrite = false, proofs = 0, targetAckAt = null, targetSetters = 0, newProbes = 0, allProbes = 0;
  const events = [], overrides = new Map(options.overrides || []);
  const preferences = Object.fromEntries(['source', 'target'].map(label => [label, {
    rxps: options.factoryDefaults ? 'on' : 'off', hops: options.factoryDefaults ? '3' : '0',
    autofetch: options.autoPolicies ? 'signed' : 'off', autoinstall: options.autoPolicies ? 'trusted' : 'off'}]));
  const saved = {source: [910.5250244, 62.5, 7, 5], target: [910.5250244, 62.5, 7, 5]};
  const states = {source: [null, null], target: [null, null]};
  if (options.initialPrimary) for (const label of ['source', 'target']) states[label][0] = {started: 0, tuple: options.initialPrimary, minutes: 120};
  const requested = options.legacy ? {...selected, frequency: saved.source[0], bandwidth: saved.source[1], sf: saved.source[2], cr: saved.source[3]} : selected;
  const expectedTuple = [requested.frequency, requested.bandwidth, requested.sf, requested.cr];
  const savedSecondary = {source: options.sourceBusy ? '> 908.15,250,7,5,rxtx,48' : '> off (manual)',
    target: options.targetBusy ? '> 908.15,250,7,5,rxtx,48' : '> off (manual)'};
  const supported = {source: options.sourceSecondary !== false, target: options.targetSecondary !== false};
  if (options.adopt) { states.source[1] = {started: 0}; states.target[1] = {started: 0}; }
  const link = {
    browserRawSource: !options.repeater, localCliSupported: true, otaSupported: !options.legacy, otaMode: options.legacy ? 'legacy' : 'guarded',
    sourceKey, knownRepeater: key, relays: [], serving: false, guard: false, authenticated: false, otaRadioProfile: 0, otaTargetProfile: 0,
    supportsOtaRadio2: !options.legacy && options.sourceSupports !== false,
    async probeOtaRadio() { events.push(['probe']); this.supportsOtaRadio2 = !options.legacy && options.sourceSupports !== false; },
    async verifyPeer(k, password) {
      events.push(['verify', k, password, clock]); assert.equal(k, key); proofs++;
      if (!options.fastProbe && events.some(row => row[0] === 'select')) {
        assert.ok(clock - (targetAckAt ?? 0) >= (options.targetDelay ?? 2500), 'authentication preceded target retune');
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
      clock += options.resetDelay ?? 0;
      if (options.resetFailure) throw Error('reset failed'); this.authenticated = false;
    },
    async localCommand(command) { return read('source', command); },
    async remoteCommand(k, password, command) {
      assert.equal(k, key); assert.equal(this.guard, true, 'remote request used a closed guard');
      return read('target', command);
    }
  };
  if (options.fastProbe) link.probePeerRadio = async function(k, password, profile, {radio, preamble, readback} = {}) {
    events.push(['peer-probe', profile, clock]);
    assert.equal(k, key); assert.equal(password, 'pw');
    assert.deepEqual(radio, profile ? expectedTuple : saved.source);
    if (profile) {
      assert.equal(preamble, selected.preamble);
      assert.ok(live('source', 1), 'new-channel probe before source R2 became live');
    }
    assert.equal(states.source[0], null, 'dual-channel handshake retuned the original source radio');
    clock += options.probeDelay ?? 1000;
    this.guard = this.authenticated = false; this.otaRadioProfile = profile;
    if (++allProbes === options.probeUsbFailureAt) {
      this.failure = Error('USB disconnected during channel probe'); throw this.failure;
    }
    const targetProfile = states.target[1] ? 1 : 0;
    const targetLive = live('target', targetProfile);
    const reachable = profile ? targetLive && ++newProbes > (options.newProbeMisses ?? 0) :
      !targetLive || targetProfile === 1;
    if (!reachable) return {reachable: false};
    this.guard = this.authenticated = true;
    const reply = readback ? await readback(command => read('target', command)) : undefined;
    events.push(['peer-probe-result', profile, clock, reply?.state ?? null]);
    return {reachable: true, reply};
  };
  function live(label, profile) {
    const state = states[label][profile];
    return state && !options.pendingForever && clock - state.started >= (label === 'source' ? 1500 : options.targetApplyDelay ?? 2000);
  }
  function config(label, profile) {
    const state = states[label][profile];
    if (!state) return profile ? '> off' : (label === 'source' ? 'TempRadio inactive' : '> off');
    if (!profile && options.expirePrimaryLease && clock - state.started >= state.minutes * 60000)
      return '> off';
    if (!profile && label === 'target' && (options.targetReadbackPendingForever ||
        clock - state.started < (options.targetReadbackDelay ?? 0)))
      return options.targetReadbackOff ? '> off' : '> pending';
    const pre = label === 'source' ? options.sourcePreamble ?? 128 :
      clock - state.started < (options.targetOldPreambleUntil ?? 0) ? 64 : options.targetPreamble ?? 128;
    const tuple = [...(state.tuple || expectedTuple)];
    if (label === 'target' && options.targetFrequency) tuple[0] = options.targetFrequency;
    const frequency = tuple[0];
    if (profile) return `> ${tuple.join(',')},rxtx,${pre}; 0d2h0m (120 min) left`;
    if (!live(label, 0)) return 'TempRadio pending: 909.950,250.00,5,5,preamble=128';
    return label === 'source' ? `TempRadio active: ${tuple.join(',')} 7190s left,preamble=${pre}` : `> ${tuple.join(',')},${pre}`;
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
      return options.prerequisiteReply ?? 'OK - saved';
    }
    if (/^(?:tempradio |set tempradio2 )/.test(command)) {
      const profile = command.startsWith('set ') ? 1 : 0;
      const minutes = Number(command.split(',').at(-2));
      assert.equal(command, setup.commands({...requested, minutes}, {sourceProfile: profile, targetProfile: profile})[label]);
      assert.ok(minutes === (profile ? 120 : 2) || (!profile && minutes === 120 &&
        events.some(row => row[0] === 'select' || row[0] === 'peer-probe-result' && row[1] === 1 && row[3] === 'active')),
        'full primary window before link proof');
      if (label === 'source' && !options.repeater) assert.equal(protectedWrite, true, 'Companion retune must use protective local guard');
      if (label === 'target' && options.fastProbe && minutes === 2)
        assert.equal(link.otaRadioProfile, 0, 'target trial setter did not use the verified original source radio');
      if (label === 'target' && ++targetSetters <= (options.targetSetterIgnored ?? 0))
        throw Object.assign(Error('Remote command timed out; result is unknown'), {loraReplyTimeout: true});
      states[label][profile] = {started: clock, tuple: expectedTuple, minutes};
      if (label === 'target') targetAckAt = clock;
      if (label === 'target' && options.targetAckLost && targetSetters === 1)
        throw Object.assign(Error('Remote command timed out; result is unknown'), {loraReplyTimeout: true});
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
    if (options.legacy && label === 'source' && ['ota config', 'ota stats', 'ota folder'].includes(command)) return 'Error: unsupported';
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
  assert.deepEqual(primary, {target: 'tempradio 909.950,250,5,5,120,128', source: 'tempradio 909.950,250,5,5,120,128', manual: 'tempradio 909.950,250,5,5,120,128'});
  assert.equal(secondary.target, 'set tempradio2 909.950,250,5,5,rxtx,120,128');
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
    assert.equal(f.writes().filter(row => row[0] === 'target').length, row.targetProfile ? 1 : 2);
    assert.equal(f.writes().filter(row => row[0] === 'source').length, row.sourceProfile ? 1 : 2);
    assert.ok(f.writes().every(row => !row[1].includes('.ifidle')));
  }
});

test('scheduled secondary remains untouched and triggers primary fallback without clearing schedules', async () => {
  const f = fixture({overrides: [['target:get radioat2', '> slots: 1@2000000000']]});
  const row = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(row.targetProfile, 0); assert.equal(row.sourceProfile, 1);
  assert.equal(f.writes().filter(row => row[0] === 'target')[0][1], trial.target);
});

test('direct Repeater uses local setter and selected OTA profile without Companion guard helper', async () => {
  const f = fixture({repeater: true}); delete f.link.applyTemporaryRadio;
  const row = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(row.sourceProfile, 1); assert.equal(f.writes().filter(row => row[0] === 'source').length, 1);
  assert.equal(f.writes().some(row => row[0] === 'protected-local'), false);
  assert.equal(f.events.some(row => row[0] === 'reset'), false);
  assert.equal(f.events.filter(row => row[0] === 'verify').length, 3);
});

test('unknown capability/read errors and pending primary preflight refuse all writes', async () => {
  for (const [command, value] of [['get public.key', '> ' + '56'.repeat(32)], ['get radio', 'malformed'],
    ['get tempradio', 'TempRadio pending: 909.950,250,5,5'],
    ['ota config', 'autofetch=off autoinstall=off hops=0 hops=1'],
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
  for (const change of [{otaSupported: undefined}, {localCliSupported: false}, {serving: true}, {relays: [key]}, {sourceKey: key}, {knownRepeater: '56'.repeat(32)}, {applyTemporaryRadio: null}, {selectOtaRadio: null}]) {
    const f = fixture(); Object.assign(f.link, change);
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options)); assert.equal(f.writes().length, 0);
  }
});

test('a plain target setter is sent once and never retried or replaced on unsupported/lost reply', async () => {
  for (const options of [{targetAck: 'Error: unsupported'}, {targetAck: 'pending'}, {targetTimeout: true}]) {
    const f = fixture({...options, targetSecondary: false});
    await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), /partially active/);
    assert.equal(f.writes().length, 1); assert.equal(f.writes()[0][1], trial.target);
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

test('pending or off target readback can settle without repeating either short-window setter', async () => {
  for (const targetReadbackOff of [false, true]) {
    const f = fixture({sourceSupports: false, targetSecondary: false,
      targetReadbackDelay: 5500, targetReadbackOff});
    const result = await setup.prepare(f.link, key, 'pw', selected, f.options);
    assert.equal(result.ready, true);
    for (const endpoint of ['source', 'target']) {
      const writes = f.writes().filter(row => row[0] === endpoint).map(row => row[1]);
      assert.deepEqual(writes, [trial[endpoint], primary[endpoint]]);
    }
    const select = f.events.findIndex(row => row[0] === 'select');
    assert.ok(f.events.slice(select).filter(row => row[0] === 'target' && row[1] === 'get tempradio').length >= 3);
  }
});

test('older target showing its previous active preamble is checked until the accepted channel applies', async () => {
  const f = fixture({sourceSupports: false, targetSecondary: false, targetOldPreambleUntil: 5500});
  const result = await setup.prepare(f.link, key, 'pw', selected, f.options);
  assert.equal(result.ready, true);
  const select = f.events.findIndex(row => row[0] === 'select');
  assert.ok(f.events.slice(select).filter(row => row[0] === 'target' && row[1] === 'get tempradio').length >= 3);
  for (const endpoint of ['source', 'target'])
    assert.deepEqual(f.writes().filter(row => row[0] === endpoint).map(row => row[1]), [trial[endpoint], primary[endpoint]]);
});

test('target pending readback has a bounded read-only wait and reports expected and observed state', async () => {
  const f = fixture({sourceSupports: false, targetSecondary: false, targetReadbackPendingForever: true});
  await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options), error => {
    assert.match(error.message, /Target/i);
    assert.match(error.message, /pending/i);
    assert.match(error.message, /909\.950,250,5,5/);
    assert.match(error.message, /128/);
    assert.match(error.message, /35/);
    return true;
  });
  const select = f.events.findIndex(row => row[0] === 'select');
  const reads = f.events.slice(select).filter(row => row[0] === 'target' && row[1] === 'get tempradio');
  assert.ok(reads.length > 1 && reads.length <= 72);
  assert.ok(f.options.now() - reads[0][2] >= 35000 && f.options.now() - reads[0][2] <= 35500);
  for (const endpoint of ['source', 'target'])
    assert.deepEqual(f.writes().filter(row => row[0] === endpoint).map(row => row[1]), [trial[endpoint]]);
});

test('a persistent active target preamble mismatch is bounded, detailed and never opens the full window', async () => {
  const f = fixture({sourceSupports: false, targetSecondary: false, targetPreamble: 64});
  await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options), error => {
    assert.match(error.message, /Target/i);
    assert.match(error.message, /909\.950,250,5,5/);
    assert.match(error.message, /128/);
    assert.match(error.message, /64/);
    return true;
  });
  const select = f.events.findIndex(row => row[0] === 'select');
  const reads = f.events.slice(select).filter(row => row[0] === 'target' && row[1] === 'get tempradio');
  assert.ok(reads.length > 1 && reads.length <= 72);
  assert.ok(f.options.now() - reads[0][2] >= 35000 && f.options.now() - reads[0][2] <= 35500);
  for (const endpoint of ['source', 'target'])
    assert.deepEqual(f.writes().filter(row => row[0] === endpoint).map(row => row[1]), [trial[endpoint]]);
});

test('a legacy primary trial expiring during reauthentication is identified and not renewed', async () => {
  const f = fixture({legacy: true, targetSecondary: false, resetDelay: 120001, expirePrimaryLease: true});
  await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options), error => {
    assert.match(error.message, /Target/i);
    assert.match(error.message, /(?:expired|expiry|elapsed|ended)/i);
    assert.match(error.message, /trial window/i);
    assert.match(error.message, /Reported: off/);
    return true;
  });
  for (const endpoint of ['source', 'target']) {
    const writes = f.writes().filter(row => row[0] === endpoint).map(row => row[1]);
    assert.deepEqual(writes, ['tempradio 910.525,62.5,7,5,2,128']);
  }
});

test('dual-channel setup prepares source R2 before switching the target and completes immediately on new-channel proof', async () => {
  const f = fixture({fastProbe: true, targetApplyDelay: 0, probeDelay: 0}), before = f.before();
  const result = await setup.prepare(f.link, key, 'pw', selected, f.options);
  assert.equal(result.ready, true); assert.equal(result.sourceProfile, 1); assert.equal(result.targetProfile, 1);
  const sourceWrite = f.events.findIndex(row => row[0] === 'source' && row[1] === secondary.source);
  const targetWrite = f.events.findIndex(row => row[0] === 'target' && row[1] === secondary.target);
  const firstProbe = f.events.slice(targetWrite + 1).find(row => row[0] === 'peer-probe');
  assert.ok(sourceWrite >= 0 && sourceWrite < targetWrite);
  assert.equal(firstProbe[1], 1); assert.ok(firstProbe[2] - f.events[targetWrite][2] < 2500);
  assert.equal(f.events.slice(targetWrite + 1).filter(row => row[0] === 'peer-probe' && row[1] === 0).length, 0);
  assert.equal(f.states.source[0], null); assert.equal(f.before(), before);
  assert.equal(f.writes().filter(row => row[0] === 'source').length, 1);
  assert.equal(f.writes().filter(row => row[0] === 'target').length, 1);
  assert.ok(!f.events.some(row => row[0] === 'wait' && row[1] === 2500));
});

test('dual-channel setup keeps checking the new channel and checks the original only after failed new probes', async () => {
  const f = fixture({fastProbe: true, targetSecondary: false, targetApplyDelay: 14000});
  const result = await setup.prepare(f.link, key, 'pw', selected, f.options);
  assert.equal(result.ready, true); assert.equal(result.sourceProfile, 1); assert.equal(result.targetProfile, 0);
  const targetWrite = f.events.findIndex(row => row[0] === 'target' && row[1] === trial.target);
  const probes = f.events.slice(targetWrite + 1).filter(row => row[0] === 'peer-probe');
  assert.equal(probes[0][1], 1); assert.ok(probes.some(row => row[1] === 0));
  for (let n = 0; n < probes.length; n++) if (probes[n][1] === 0) {
    assert.ok(n > 0 && probes[n - 1][1] === 1, 'original-channel check preceded a failed new-channel check');
  }
  const oldReplies = f.events.slice(targetWrite + 1).filter(row => row[0] === 'peer-probe-result' && row[1] === 0);
  assert.ok(oldReplies.length > 0); assert.ok(oldReplies.every(row => row[3] === 'pending'));
  const setters = f.events.filter(row => row[0] === 'target' && row[1].startsWith('tempradio '));
  assert.deepEqual(setters.map(row => row[1]), [trial.target, primary.target]);
  assert.equal(f.states.source[0], null);
});

test('a missed target switch is repeated only after fresh original-channel proof and the five-second grace', async () => {
  const f = fixture({fastProbe: true, targetSecondary: false, targetSetterIgnored: 1, targetApplyDelay: 0});
  const result = await setup.prepare(f.link, key, 'pw', selected, f.options);
  assert.equal(result.ready, true);
  const trials = f.events.filter(row => row[0] === 'target' && row[1] === trial.target);
  assert.equal(trials.length, 2); assert.ok(trials[1][2] - trials[0][2] >= 5000);
  const first = f.events.indexOf(trials[0]), second = f.events.indexOf(trials[1]);
  const proof = f.events.slice(first + 1, second).filter(row => row[0] === 'peer-probe-result' && row[1] === 0);
  assert.ok(proof.length > 0); assert.equal(proof.at(-1)[3], 'off');
  assert.equal(f.writes().filter(row => row[0] === 'source').length, 1);
  assert.equal(f.states.source[0], null);
});

test('three missed target switches stop rather than repeating setup indefinitely', async () => {
  const f = fixture({fastProbe: true, targetSecondary: false, targetSetterIgnored: 100});
  await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options));
  const trials = f.events.filter(row => row[0] === 'target' && row[1] === trial.target);
  assert.equal(trials.length, 3);
  for (let n = 1; n < trials.length; n++) {
    assert.ok(trials[n][2] - trials[n - 1][2] >= 5000);
    const previous = f.events.indexOf(trials[n - 1]), current = f.events.indexOf(trials[n]);
    assert.ok(f.events.slice(previous, current).some(row => row[0] === 'peer-probe-result' && row[1] === 0 && row[3] === 'off'));
  }
  assert.ok(!f.events.some(row => row[0] === 'target' && row[1] === primary.target));
  assert.equal(f.writes().filter(row => row[0] === 'source').length, 1);
});

test('an accepted pending target switch is never sent again even when its acknowledgement was lost', async () => {
  const f = fixture({fastProbe: true, targetSecondary: false, targetAckLost: true, targetApplyDelay: 100000});
  await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options));
  const targetWrite = f.events.findIndex(row => row[0] === 'target' && row[1] === trial.target);
  const oldReplies = f.events.slice(targetWrite + 1).filter(row => row[0] === 'peer-probe-result' && row[1] === 0);
  assert.ok(oldReplies.length > 0); assert.ok(oldReplies.every(row => row[3] === 'pending'));
  assert.deepEqual(f.events.filter(row => row[0] === 'target' && row[1].startsWith('tempradio ')).map(row => row[1]), [trial.target]);
  assert.equal(f.states.source[0], null);
});

test('USB failure during a channel probe stops without another target switch command', async () => {
  const f = fixture({fastProbe: true, targetSecondary: false, probeUsbFailureAt: 2, targetApplyDelay: 100000});
  await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options), /USB disconnected/);
  assert.equal(f.events.filter(row => row[0] === 'peer-probe').length, 2);
  assert.deepEqual(f.events.filter(row => row[0] === 'target' && row[1].startsWith('tempradio ')).map(row => row[1]), [trial.target]);
  assert.equal(f.states.source[0], null);
});

test('new-channel reachability with the wrong target preamble does not authorize a full update window', async () => {
  const f = fixture({fastProbe: true, targetSecondary: false, targetApplyDelay: 0, targetPreamble: 64});
  await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options));
  assert.ok(f.events.some(row => row[0] === 'peer-probe-result' && row[1] === 1 && row[3] === 'active'));
  assert.deepEqual(f.events.filter(row => row[0] === 'target' && row[1].startsWith('tempradio ')).map(row => row[1]), [trial.target]);
  assert.equal(f.states.source[0], null);
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

test('OTA policies are changed once after both baselines while RXPS remains untouched', async () => {
  const f = fixture({factoryDefaults: true, autoPolicies: true}), before = f.before();
  const result = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(result.changedSettings.length, 6); assert.equal(f.before(), before);
  for (const endpoint of ['source', 'target']) {
    assert.deepEqual(f.preferences[endpoint], {rxps: 'on', hops: '0', autofetch: 'off', autoinstall: 'off'});
    for (const command of ['ota config autofetch off', 'ota config autoinstall off', 'ota config hops 0'])
      assert.equal(f.events.filter(row => row[0] === endpoint && row[1] === command).length, 1);
  }
  const firstWrite = f.events.findIndex(row => row[1] === 'ota config autofetch off');
  assert.ok(f.events.slice(0, firstWrite).some(row => row[0] === 'target' && row[1] === 'get tempradioat2'));
  assert.ok(f.events.slice(0, firstWrite).some(row => row[0] === 'source' && row[1] === 'ota folder'));
  for (const change of result.changedSettings) {
    assert.equal(change.confirmed, true); assert.equal(change.key, change.endpoint === 'source' ? sourceKey : key);
    assert.ok(change.restoreCommand); assert.notEqual(change.before, change.after);
  }
  assert.ok(!f.events.some(row => /radio.rxps/.test(row[1] || '')));
  const targetLastPolicy = f.events.findIndex(row => row[0] === 'target' && row[1] === 'ota config hops 0');
  const sourceFirstPolicy = f.events.findIndex(row => row[0] === 'source' && row[1] === 'ota config autofetch off');
  assert.ok(targetLastPolicy < sourceFirstPolicy);
  assert.ok(f.events.slice(targetLastPolicy, sourceFirstPolicy).some(row => row[0] === 'close'));
  assert.ok(f.events.slice(sourceFirstPolicy, f.events.findIndex(row => row[1] === secondary.target)).some(row => row[0] === 'verify'));
});

test('lost or differently worded policy ACK is resolved by readback without replaying setters', async () => {
  for (const endpoint of ['source', 'target']) for (const options of [
    {prerequisiteTimeout: endpoint + ':ota config hops 0'}, {prerequisiteReply: '> hops=0'}, {prerequisiteReply: ''}
  ]) {
    const f = fixture({factoryDefaults: true, ...options});
    const result = await setup.prepare(f.link, key, '', selected, f.options);
    assert.equal(result.ready, true); assert.ok(result.changedSettings.every(row => row.confirmed));
    assert.equal(f.events.filter(row => row[0] === endpoint && row[1] === 'ota config hops 0').length, 1);
    assert.equal(f.preferences.source.rxps, 'on'); assert.equal(f.preferences.target.rxps, 'on');
  }
});

test('policy readback retries GET only and proceeds after delayed application or a lost read', async () => {
  for (const readThrows of [false, true]) {
    const f = fixture({factoryDefaults: true}); let reads = 0;
    f.overrides.set('target:ota config', () => {
      if (f.preferences.target.hops === '0' && reads++ === 0) {
        if (readThrows) throw Error('Read timeout');
        return 'autofetch=off autoinstall=off hops=3';
      }
      return 'autofetch=off autoinstall=off hops=' + f.preferences.target.hops;
    });
    assert.equal((await setup.prepare(f.link, key, '', selected, f.options)).ready, true);
    assert.equal(f.events.filter(row => row[0] === 'target' && row[1] === 'ota config hops 0').length, 1);
    assert.ok(reads >= 2);
  }
});

test('unknown policy and existing OTA work stop before any prerequisite or radio mutation', async () => {
  for (const options of [
    {factoryDefaults: true, overrides: [['target:ota config', 'autofetch=on autoinstall=off hops=3']]},
    {factoryDefaults: true, sourceDownload: true}, {factoryDefaults: true, sourceFolder: true}, {factoryDefaults: true, targetDownload: true}
  ]) {
    const f = fixture(options); await assert.rejects(setup.prepare(f.link, key, '', selected, f.options));
    assert.equal(f.events.filter(row => /^(?:set |tempradio |ota config (?:autofetch|autoinstall|hops) )/.test(row[1] || '')).length, 0);
  }
});

test('unverified policy stops with an actionable error and no channel writes', async () => {
  const f = fixture({factoryDefaults: true, ignorePrerequisiteWrite: true});
  await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), error => {
    assert.match(error.message, /Could not verify target ota.hops=0 using ota config/);
    assert.match(error.message, /Select Prepare update to recheck/);
    assert.doesNotMatch(error.message, /review the recorded changes before proceeding/);
    assert.equal(error.changedSettings[0].confirmed, false);
    assert.equal(error.changedSettings[0].uncertain, true); return true;
  });
  assert.equal(f.events.filter(row => row[1] === 'ota config hops 0').length, 1);
  assert.equal(f.writes().length, 0);
});

test('USB loss asks for reconnect; fresh preparation adopts already applied policies', async () => {
  const f = fixture({factoryDefaults: true});
  f.overrides.set('source:ota config hops 0', () => {
    f.preferences.source.hops = '0';
    f.link.failure = Error('USB disconnected'); throw f.link.failure;
  });
  await assert.rejects(setup.prepare(f.link, key, '', selected, f.options), error => {
    assert.match(error.message, /Reconnect the USB source, then select Prepare update/);
    assert.equal(error.changedSettings.at(-1).confirmed, false); return true;
  });
  const lost = f.events.findIndex(row => row[0] === 'source' && row[1] === 'ota config hops 0');
  assert.equal(f.events.length, lost + 1); // Do not read from a failed serial connection.
  assert.equal(f.writes().length, 0);
  f.link.failure = null; f.overrides.delete('source:ota config hops 0');
  const result = await setup.prepare(f.link, key, '', selected, f.options);
  assert.equal(result.ready, true); assert.deepEqual(result.changedSettings, []);
  for (const endpoint of ['source', 'target'])
    assert.equal(f.events.filter(row => row[0] === endpoint && row[1] === 'ota config hops 0').length, 1);
});

test('RXPS settings and missing getters never gate either update profile or cause writes', async () => {
  for (const primaryOnly of [false, true]) for (const raw of [
    'on,level=8,preamble=16,rx=65625,sleep=60000', 'off,65625,60000',
    'radio.rxps on,1000,2000', 'unknown config: radio.rxps', 'Error: RXPS unsupported'
  ]) {
    const f = fixture({factoryDefaults: true, sourceSupports: !primaryOnly, targetSecondary: !primaryOnly,
      overrides: [['source:get radio.rxps', raw], ['target:get radio.rxps', raw]]});
    const result = await setup.prepare(f.link, key, '', selected, f.options);
    assert.equal(result.ready, true); assert.equal(result.sourceProfile, primaryOnly ? 0 : 1);
    assert.equal(result.targetProfile, primaryOnly ? 0 : 1);
    assert.ok(!f.events.some(row => /radio.rxps/.test(row[1] || '')));
    assert.equal(f.preferences.source.rxps, 'on'); assert.equal(f.preferences.target.rxps, 'on');
  }
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

test('compatibility source aligns existing manual windows automatically without rewriting saved channels', async () => {
  const f = fixture({legacy: true, initialPrimary: [909.95, 250, 5, 5], factoryDefaults: true});
  const before = f.before(), result = await setup.prepare(f.link, key, 'pw', selected, f.options);
  assert.equal(result.ready, true); assert.equal(result.sourceProfile, 0); assert.equal(result.targetProfile, 0);
  assert.deepEqual(result.source, [910.5250244, 62.5, 7, 5]); assert.equal(f.before(), before);
  const radioWrites = f.events.filter(row => ['source', 'target'].includes(row[0]) && row[1].startsWith('tempradio '));
  assert.deepEqual(radioWrites.map(row => row.slice(0, 2)), [
    ['target', 'tempradio 910.525,62.5,7,5,2,128'], ['source', 'tempradio 910.525,62.5,7,5,2,128'],
    ['target', 'tempradio 910.525,62.5,7,5,120,128'], ['source', 'tempradio 910.525,62.5,7,5,120,128']]);
  const shortSource = f.events.indexOf(radioWrites[1]), longTarget = f.events.indexOf(radioWrites[2]);
  assert.ok(f.events.slice(shortSource, longTarget).some(row => row[0] === 'target' && row[1] === 'get public.key'));
  assert.ok(f.events.slice(shortSource, longTarget).some(row => row[0] === 'target' && row[1] === 'get tempradio'));
  assert.equal(f.preferences.source.rxps, 'on'); assert.equal(f.preferences.target.rxps, 'on');
  assert.ok(!f.events.some(row => row[0] === 'source' && /^ota config /.test(row[1] || '')));
});

test('primary link failure leaves only short leases and never opens the full window', async () => {
  for (const options of [{wrongFreshKey: true}, {targetFrequency: 908.5}, {targetPreamble: 64}, {resetFailure: true}]) {
    const f = fixture({...options, sourceSupports: false, targetSecondary: false});
    await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options));
    assert.ok(f.writes().length > 0);
    assert.ok(f.writes().every(row => !row[1].endsWith(',120,128')));
  }
});

test('already matching primary windows need only prerequisite changes and live proof', async () => {
  const f = fixture({initialPrimary: [909.95, 250, 5, 5], factoryDefaults: true});
  const result = await setup.prepare(f.link, key, 'pw', selected, {...f.options, useActivePrimary: true});
  assert.equal(result.adoptedSource, true); assert.equal(result.adoptedTarget, true);
  assert.deepEqual(result.source, [909.95, 250, 5, 5]);
  assert.ok(!f.events.some(row => /^(?:tempradio |set tempradio2 )/.test(row[1] || '')));
  assert.equal(f.preferences.source.rxps, 'on'); assert.equal(f.preferences.target.rxps, 'on');
});

test('radio frequencies are formatted to three decimal places', () => {
  assert.equal(setup.formatRadio([910.525024414, 250, 5, 5]), '910.525,250,5,5');
  assert.equal(setup.formatRadio([909.95, 62.5, 7, 5]), '909.950,62.5,7,5');
});

test('old CommonCLI unknown-config replies disable optional radio2 without blocking primary setup', async () => {
  const f = fixture({legacy: true, sourceSupports: false, targetSecondary: false});
  for (const endpoint of ['source', 'target']) for (const name of ['radio2', 'tempradio2', 'radio2.cross', 'radioat2', 'tempradioat2'])
    f.overrides.set(endpoint + ':get ' + name, 'unknown config: ' + name);
  const result = await setup.prepare(f.link, key, 'pw', selected, f.options);
  assert.equal(result.sourceProfile, 0); assert.equal(result.targetProfile, 0);
  assert.equal(result.ready, true);
});

test('missing old source getters require an upgrade; timeouts are never treated as unsupported', async () => {
  for (const command of ['get tempradio']) for (const reply of ['Unknown command', 'unknown config', 'unknown config: ' + command.slice(4)]) {
    const f = fixture({legacy: true, overrides: [['source:' + command, reply]]});
    await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options), error => {
      assert.equal(error.sourceUpdateRequired, 'companion');
      assert.match(error.message, /Update its firmware/); return true;
    });
    assert.equal(f.writes().length, 0);
  }
  for (const reply of ['unknown config: unrelated', 'Access denied', 'request timed out']) {
    const f = fixture({overrides: [['target:get radio2', reply]]});
    await assert.rejects(setup.prepare(f.link, key, 'pw', selected, f.options));
    assert.equal(f.writes().length, 0);
  }
});
