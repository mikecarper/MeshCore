/* Explicit, guarded temporary-channel setup. Importing performs no I/O. */
(function (root, factory) {
  const profiles = typeof module === 'object' && module.exports ? require('./lora_ota_region_profiles.js') : root.LoraOtaRegionProfiles;
  const api = factory(profiles);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaRadioSetup = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (profiles) {
  'use strict';
  const bandwidths = [7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500];
  const fullKey = value => typeof value === 'string' && /^[0-9a-f]{64}$/i.test(value);
  const bare = value => String(value).trim().replace(/^>\s*/, '').trim();
  const same = (a, b) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= (i === 0 ? 0.001 : 0.00001));
  const formatRadio = tuple => [Number(tuple[0]).toFixed(3), ...tuple.slice(1)].join(',');
  const diagnostic = value => String(value).replace(/[^\x20-\x7e]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 160);
  function need(ok, message) { if (!ok) throw Error(message); }
  function settings(value) {
    need(value && typeof value === 'object' && !Array.isArray(value), 'Choose temporary radio settings.');
    const {frequency, bandwidth, sf, cr, minutes, preamble} = value;
    need([frequency, bandwidth, sf, cr, minutes, preamble].every(n => typeof n === 'number' && Number.isFinite(n)),
      'Temporary radio settings must be finite numbers.');
    need(frequency >= 150 && frequency <= 2500 && bandwidths.includes(bandwidth) &&
      Number.isInteger(sf) && sf >= 5 && sf <= 12 && Number.isInteger(cr) && cr >= 4 && cr <= 8 &&
      Number.isInteger(minutes) && minutes >= 1 && minutes <= 10080 &&
      Number.isInteger(preamble) && preamble >= 1 && preamble <= 65535, 'Temporary radio settings are outside the supported ranges.');
    return {frequency, bandwidth, sf, cr, minutes, preamble};
  }
  function commands(value, {sourceProfile = 0, targetProfile = 0} = {}) {
    const v = settings(value);
    need([sourceProfile, targetProfile].every(n => n === 0 || n === 1), 'Invalid OTA radio profile.');
    const tuple = [v.frequency.toFixed(3), v.bandwidth, v.sf, v.cr];
    const primaryCommand = 'tempradio ' + [...tuple, v.minutes, v.preamble].join(',');
    const secondaryCommand = 'set tempradio2 ' + [...tuple, 'rxtx', v.minutes, v.preamble].join(',');
    return {target: targetProfile ? secondaryCommand : primaryCommand,
      source: sourceProfile ? secondaryCommand : primaryCommand, manual: primaryCommand};
  }
  function recommend(savedTuple) {
    if (!Array.isArray(savedTuple) || savedTuple.length !== 4 || savedTuple.some(n => typeof n !== 'number' || !Number.isFinite(n)) ||
        savedTuple[0] < 902 || savedTuple[0] > 928 || !bandwidths.includes(savedTuple[1]) ||
        !Number.isInteger(savedTuple[2]) || savedTuple[2] < 5 || savedTuple[2] > 12 ||
        !Number.isInteger(savedTuple[3]) || savedTuple[3] < 4 || savedTuple[3] > 8) return null;
    // A North American example for BW250, separated from the saved channel's
    // occupied bandwidth. This does not establish regional permission or RF quiet.
    const separation = (savedTuple[1] + 250) / 2000 + 0.025;
    return [909.95, 908.5, 907.5, 911.5].find(f => Math.abs(f - savedTuple[0]) > separation) ?? null;
  }
  function recommendProfile(savedTuple, options = {}) {
    try {
      if (!options || typeof options !== 'object' || Array.isArray(options)) return null;
      // Retain the previous (savedTuple, activeReadback) call shape for pages
      // already using the temporary-channel helper.
      const region = options.region === undefined ? 'auto' : options.region;
      const active = options.active === undefined && options.state ? options : options.active;
      if (region !== 'auto') return profiles?.recommend(savedTuple, {region,hardware:options.hardware}) || null;
      if (active?.state === 'active') {
        const tuple = active.tuple;
        if (!Array.isArray(tuple) || tuple.length !== 4) return null;
        settings({frequency:tuple[0],bandwidth:tuple[1],sf:tuple[2],cr:tuple[3],minutes:1,preamble:active.preamble});
        return {id:'active',label:'Current temporary channel',frequency:tuple[0],bandwidth:tuple[1],sf:tuple[2],cr:tuple[3],
          preamble:active.preamble,selection:'active',matched:false,ambiguous:false};
      }
      return profiles?.recommend(savedTuple, {hardware:options.hardware}) || null;
    } catch (_) { return null; }
  }
  function primary(reply) {
    const m = bare(reply).match(/^(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)$/);
    need(m, 'Cannot verify saved primary radio settings.');
    const tuple = m.slice(1).map(Number);
    settings({frequency: tuple[0], bandwidth: tuple[1], sf: tuple[2], cr: tuple[3], minutes: 1, preamble: 1});
    return tuple;
  }
  function temporary(reply) {
    const text = bare(reply);
    if (/^(?:off|inactive|TempRadio inactive)$/i.test(text)) return {state: 'off'};
    if (/^(?:pending|TempRadio pending:)(?:\s|$)/i.test(text)) return {state: 'pending'};
    // CommonCLI returns five numeric fields; Full Companion names the
    // preamble after its active-window text. Neither may omit the preamble.
    const m = text.match(/^(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+),(\d+)$/) ||
      text.match(/^(?:TempRadio active:\s*)?(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)(?:\s+\d+s left)?,preamble=(\d+)$/i);
    need(m, 'Cannot verify temporary radio state and preamble.');
    const tuple = m.slice(1, 5).map(Number), preamble = Number(m[5]);
    settings({frequency: tuple[0], bandwidth: tuple[1], sf: tuple[2], cr: tuple[3], minutes: 1, preamble});
    return {state: 'active', tuple, preamble};
  }
  function off(reply) { return /^(?:off(?: \((?:manual|automatic allowed)\))?|inactive)$/i.test(bare(reply)); }
  function emptySchedule(reply) { return /^(?:-none-|none|slots:)$/i.test(bare(reply)); }
  function policy(reply) {
    const text = bare(reply), result = {};
    for (const name of ['hops', 'autofetch', 'autoinstall']) {
      const values = [...text.matchAll(new RegExp('\\b' + name + '=([a-z0-9]+)\\b', 'gi'))];
      need(values.length === 1, 'Cannot verify the OTA ' + name + ' policy.');
      result[name] = values[0][1].toLowerCase();
    }
    need(/^[0-8]$/.test(result.hops) && ['off', 'any', 'signed'].includes(result.autofetch) &&
      ['off', 'trusted'].includes(result.autoinstall), 'Unknown OTA policy; setup cannot continue.');
    return result;
  }
  function keyProof(reply, key) {
    const text = bare(reply);
    need(fullKey(text) && text.toLowerCase() === key, 'The selected target did not prove its complete public key.');
  }
  function unsupported(reply, command = '') {
    const text = bare(reply).toLowerCase();
    return /^(?:error: (?:unsupported|radio profiles unsupported|profiles unsupported)|err:? unsupported|unsupported|unknown command)$/.test(text) ||
      !!command && (text === 'unknown config' || text === 'unknown config: ' + command.replace(/^get /, '').toLowerCase());
  }
  function requiredReply(reply, command, source = false) {
    if (unsupported(reply, command)) {
      const error = Error((source ? 'USB ' + (source === 'repeater' ? 'Repeater' : 'Companion') : 'Target') + ' firmware cannot report ' + command +
        '. Update its firmware using USB or another supported method before using automatic LoRa updates.');
      if (source) error.sourceUpdateRequired = source === 'repeater' ? 'repeater' : 'companion';
      throw error;
    }
    return reply;
  }
  function secondary(reply, command = 'get radio2') {
    const text = bare(reply);
    if (unsupported(reply, command)) return {state: 'unsupported'};
    if (off(reply)) return {state: 'off', intent: text.toLowerCase()};
    const m = text.match(/^(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+),(rx|rxtx),(\d+)( \(auto\))?((?:;[^\r\n]*)?)$/i);
    need(m, 'Cannot verify secondary radio settings.');
    const tuple = m.slice(1, 5).map(Number), preamble = Number(m[6]);
    settings({frequency: tuple[0], bandwidth: tuple[1], sf: tuple[2], cr: tuple[3], minutes: 1, preamble});
    return {state: 'configured', tuple, mode: m[5].toLowerCase(), preamble,
      automatic: !!m[7], autoOwned: /; auto-owned(?:;|$)/i.test(m[8])};
  }
  async function readTemporary(read, profile) {
    need(profile === 0 || profile === 1, 'Invalid OTA radio profile.');
    const raw = await read(profile ? 'get tempradio2' : 'get tempradio');
    if (unsupported(raw, profile ? 'get tempradio2' : 'get tempradio')) return {state: 'unsupported', profile, raw};
    if (!profile) {
      let state;
      try { state = temporary(raw); }
      catch (error) { error.message += ' Readback: ' + diagnostic(raw); throw error; }
      return {...state, radio: state.tuple, profile, raw};
    }
    const config = secondary(raw, 'get tempradio2');
    if (config.state === 'off') return {state: 'off', profile, raw};
    need(config.state === 'configured' && config.mode === 'rxtx', 'Temporary secondary radio must use RXTX mode.');
    const liveStatus = await read('get radio2.status'), text = bare(liveStatus);
    if (/^radio2 change pending (?:storage commit \(retrying\)|reply transmission); previous channel retained$/i.test(text))
      return {state: 'pending', profile, raw, liveStatus};
    const m = text.match(/^(off|rx|rxtx); RX=\d+,\d+ TX=\d+,\d+ switches=\d+ errors=\d+ max=\d+us preamble=(\d+),(\d+)(?:;[^\r\n]*)?$/i);
    need(m, 'Cannot verify live secondary radio state.');
    // The temporary getter can already show a pending configuration. Only an
    // unused-off -> live-RXTX transition with its effective preamble is credited.
    // These cached CLI fields do not prove a hardware frequency or generation.
    const active = m[1].toLowerCase() === 'rxtx' && Number(m[3]) === config.preamble;
    return {state: active ? 'active' : 'pending', tuple: config.tuple, radio: config.tuple, preamble: config.preamble, profile, raw, liveStatus};
  }
  function schedule(reply, command) {
    if (unsupported(reply, command)) return {unsupported: true};
    if (emptySchedule(reply)) return {empty: true};
    const text = bare(reply);
    need(/^slots:(?: \d+@\d+)*$/.test(text) || /^\d+:[0-9.,@ -]+$/.test(text), 'Cannot verify scheduled radio settings.');
    return {empty: false, value: text};
  }
  function secondarySignature(config) {
    if (config.state !== 'configured') return JSON.stringify(config);
    return JSON.stringify({...config, preamble: config.automatic ? 0 : config.preamble});
  }
  function crossing(reply) {
    if (unsupported(reply, 'get radio2.cross')) return 'unsupported';
    const result = bare(reply).toLowerCase();
    need(['off', 'on', 'auto'].includes(result), 'Cannot verify secondary crossing policy.');
    return result;
  }
  async function prepare(link, key, password, value, {progress = () => {}, useActivePrimary = false,
      wait = ms => new Promise(r => setTimeout(r, ms)), now = () => Date.now()} = {}) {
    let v = settings(value), expected = [v.frequency, v.bandwidth, v.sf, v.cr];
    const companion = link?.browserRawSource === true, legacy = companion && link.otaSupported === false && link.usesUsbFolderFallback !== true;
    const unprobedLegacy = legacy && link.usesUsbFolderFallback !== true;
    need(link && !link.serving && !link.failure && (!link.relays || (Array.isArray(link.relays) && link.relays.length === 0)) &&
      (!companion || (link.localCliSupported === true && (legacy || link.usesUsbFolderFallback === true || (link.otaSupported === true && (link.otaMode === undefined || link.otaMode === 'guarded'))))),
      'Automatic channel setup requires an idle direct connection with USB radio controls.');
    need(fullKey(key) && fullKey(link.sourceKey) && fullKey(link.knownRepeater) && key.toLowerCase() === link.knownRepeater.toLowerCase() &&
      key.toLowerCase() !== link.sourceKey.toLowerCase(), 'Select the complete target public key before preparing the channel.');
    for (const method of ['localCommand', 'remoteCommand', 'verifyPeer', 'closeRadioSession', 'resetRemoteSession', 'selectOtaRadio',
      ...(companion ? ['applyTemporaryRadio'] : [])]) need(typeof link[method] === 'function', 'This connection cannot safely prepare a temporary channel.');
    key = key.toLowerCase();
    const remote = (command, options) => link.remoteCommand(key, password, command, options), local = command => link.localCommand(command);
    let targetAttempted = false, sourceAttempted = false, targetSwitchWrites = 0;
    const changedSettings = [];
    async function baseline(label, read) {
      const saved = primary(await read('get radio'));
      const temporaryPrimary = await readTemporary(read, 0);
      const sourceKind = label === 'source' && (companion ? 'companion' : 'repeater');
      requiredReply(temporaryPrimary.raw, 'get tempradio', sourceKind);
      need(['off', 'active'].includes(temporaryPrimary.state), label + ' has a pending or unverified primary temporary radio.');
      const config = await read('ota config');
      const otaPolicy = unprobedLegacy && label === 'source' && unsupported(config) ? null : policy(config);
      let existingDownload = false, idleSourceVerified = false;
      if (label === 'source') {
        const stats = bare(await read('ota stats')), fetch = [...stats.matchAll(/\bfetch\s+([a-z]+)\b/gi)];
        need(unprobedLegacy && unsupported(stats) || fetch.length === 1 && fetch[0][1].toLowerCase() === 'idle', 'Source has an active or unverified OTA download.');
        const folder = bare(await read('ota folder'));
        need(unprobedLegacy && unsupported(folder) || /^folder=off(?:\s|$)/i.test(folder), 'Source has an attached or unverified OTA folder.');
        idleSourceVerified = fetch.length === 1 && fetch[0][1].toLowerCase() === 'idle' && /^folder=off(?:\s|$)/i.test(folder);
      } else {
        const status = bare(await read('ota status'));
        const idle = /\bno download\b/i.test(status) && !/\bdownload:/i.test(status);
        existingDownload = !idle && /\bdownload:\s*[^|\r\n]+/i.test(status) && !/\bno download\b/i.test(status);
        need(idle || existingDownload, 'Target OTA download state cannot be verified.');
      }
      const savedSecondary = secondary(await read('get radio2'));
      const temporarySecondary = secondary(await read('get tempradio2'), 'get tempradio2');
      const cross = crossing(await read('get radio2.cross'));
      need(cross !== 'on', 'Automatic setup cannot use existing radio2 crossing on; turn crossing off or use manual setup.');
      need(cross !== 'auto' || temporarySecondary.state !== 'configured',
        'Automatic crossing with an existing temporary radio2 would duplicate OTA traffic; turn crossing off or use manual setup.');
      const schedules = {};
      for (const command of ['get radioat', 'get tempradioat', 'get radioat2', 'get tempradioat2']) {
        const reply = await read(command), status = schedule(reply, command);
        // Full Companion implements no primary scheduler. Its exact shared
        // callback rejection is a known absence, not an unknown-state bypass.
        const absentPrimary = companion && label === 'source' && (command === 'get radioat' || command === 'get tempradioat') && unsupported(reply, command);
        if (command === 'get radioat' || command === 'get tempradioat')
          need(absentPrimary || status.empty === true, label + ' has scheduled or unverified primary settings.');
        else need(!status.unsupported || savedSecondary.state === 'unsupported' || temporarySecondary.state === 'unsupported',
          label + ' secondary schedules cannot be verified.');
        schedules[command] = status;
      }
      const secondaryFree = savedSecondary.state === 'off' && temporarySecondary.state === 'off' && cross === 'off' &&
        schedules['get radioat2'].empty === true && schedules['get tempradioat2'].empty === true;
      const matchingSecondary = temporarySecondary.state === 'configured' && same(temporarySecondary.tuple.slice(0, 3), expected.slice(0, 3));
      const overlappingSecondary = [savedSecondary, temporarySecondary].some(config => config.state === 'configured' &&
        Math.abs(config.tuple[0] - v.frequency) <= (config.tuple[1] + v.bandwidth) / 2000 + 0.001);
      let adoptSecondary = false;
      if (matchingSecondary && savedSecondary.state === 'off' && cross === 'off' &&
          schedules['get radioat2'].empty === true && schedules['get tempradioat2'].empty === true &&
          same(temporarySecondary.tuple, expected) && temporarySecondary.preamble === v.preamble && temporarySecondary.mode === 'rxtx') {
        adoptSecondary = (await readTemporary(read, 1)).state === 'active';
      }
      const adoptPrimary = temporaryPrimary.state === 'active' && same(temporaryPrimary.tuple, expected) && temporaryPrimary.preamble === v.preamble;
      const overlapsPrimary = Math.abs(saved[0] - v.frequency) <= (saved[1] + v.bandwidth) / 2000 + 0.001;
      return {saved, temporaryPrimary, savedSecondary, temporarySecondary, cross, schedules, secondaryFree, matchingSecondary, overlappingSecondary,
        overlapsPrimary, adoptPrimary, adoptSecondary, otaPolicy, existingDownload, idleSourceVerified};
    }
    function sameSaved(a, b) {
      return same(a.saved, b.saved) && secondarySignature(a.savedSecondary) === secondarySignature(b.savedSecondary) && a.cross === b.cross &&
        JSON.stringify(a.schedules) === JSON.stringify(b.schedules);
    }
    function required(state) {
      return state.otaPolicy === null || state.otaPolicy.hops === '0' &&
        state.otaPolicy.autofetch === 'off' && state.otaPolicy.autoinstall === 'off';
    }
    async function prerequisites(endpoint, read, before) {
      const changes = [];
      // RXPS is a user preference, not an OTA prerequisite. Radio2 suspends
      // duty cycling itself; primary updates use the verified temporary link.
      // Older firmware may expose RXPS differently or reject a live setter.
      for (const name of before.otaPolicy ? ['autofetch', 'autoinstall', 'hops'] : []) {
        const desired = name === 'hops' ? '0' : 'off';
        if (before.otaPolicy[name] !== desired) changes.push({setting: 'ota.' + name, before: before.otaPolicy[name], after: desired,
          command: 'ota config ' + name + ' ' + desired, restoreCommand: 'ota config ' + name + ' ' + before.otaPolicy[name]});
      }
      for (const change of changes) {
        const entry = {...change, endpoint, key: endpoint === 'source' ? link.sourceKey.toLowerCase() : key, confirmed: false};
        changedSettings.push(entry);
        progress('Changing saved ' + endpoint + ' setting ' + change.setting + ' to ' + change.after + '. Setup changes will be retained.');
        // A lost or differently worded acknowledgement does not tell us
        // whether a setter applied. Send it once, then resolve using GETs.
        try { await read(change.command); } catch (_) { /* Read back below. */ }
        for (let attempt = 0; attempt < 3 && !link.failure; attempt++) {
          if (attempt) await wait(250);
          try {
            const observed = policy(await read('ota config'))[change.setting.slice(4)];
            if (observed === change.after) { entry.confirmed = true; break; }
          } catch (_) { /* Only read-only verification may be retried. */ }
        }
        if (!entry.confirmed) {
          entry.uncertain = true;
          throw Error('Could not verify ' + (endpoint === 'source' ? 'USB source' : 'target') + ' ' + change.setting +
            '=' + change.after + ' using ota config. ' + (link.failure ? 'Reconnect the USB source, then select Prepare update.' :
              'Select Prepare update to recheck the current settings.'));
        }
        progress((endpoint === 'source' ? 'USB source' : 'Target') + ' ' + change.setting + '=' + change.after + ' verified.');
      }
    }
    async function verifyActive(read, profile, label, trialEnd = null) {
      const start = now();
      need(Number.isFinite(start), 'Invalid temporary-channel clock.');
      let previous = start, state;
      const details = () => ' Requested: ' + formatRadio(expected) + ',preamble=' + v.preamble +
        '. Reported: ' + (state.state === 'active' ? formatRadio(state.tuple) + ',preamble=' + state.preamble : state.state) +
        '. Readback: ' + diagnostic(state.raw);
      for (let attempt = 0; attempt < 71; attempt++) {
        const before = now();
        need(Number.isFinite(before) && before >= previous, 'Invalid temporary-channel clock.');
        if (attempt && before - start >= 35000) break;
        state = await readTemporary(read, profile);
        const after = now();
        need(Number.isFinite(after) && after >= before, 'Invalid temporary-channel clock.');
        previous = after;
        if (state.state === 'active' && same(state.tuple, expected) && state.preamble === v.preamble) return state;
        if (trialEnd !== null && after >= trialEnd && state.state === 'off')
          throw Error(label + ' temporary trial window expired before verification.' + details());
        // Older targets cannot report a pending primary mutation: they keep
        // showing off or the previous active tuple until queued TX drains.
        // Poll only GETs, including old active readbacks; never repeat a setter
        // or accept a different tuple/preamble as proof of the new channel.
        if (!attempt) progress('Waiting for ' + label.toLowerCase() + ' temporary radio to apply the requested settings.' + details());
        if (after - start >= 35000) break;
        await wait(Math.min(500, 35000 - (after - start)));
      }
      throw Error(label + ' selected temporary radio differs from the requested settings after 35 seconds.' + details());
    }
    function samePrimary(a, b) {
      return a.state === b.state && (a.state === 'off' || same(a.tuple, b.tuple) && a.preamble === b.preamble);
    }
    async function preserved(read, before, chosen, label) {
      need(same(primary(await read('get radio')), before.saved) &&
        secondarySignature(secondary(await read('get radio2'))) === secondarySignature(before.savedSecondary) &&
        crossing(await read('get radio2.cross')) === before.cross,
        label + ' saved primary, secondary or crossing settings changed.');
      if (!chosen) need(secondarySignature(secondary(await read('get tempradio2'), 'get tempradio2')) === secondarySignature(before.temporarySecondary),
        label + ' existing temporary secondary settings changed.');
    }
    try {
      progress('Checking both radios before changing the temporary channel.');
      if (typeof link.probeOtaRadio === 'function') await link.probeOtaRadio();
      await link.verifyPeer(key, password); keyProof(await remote('get public.key'), key);
      // Legacy raw-packet transmission is tied to the saved primary tuple.
      // Align both temporary windows automatically; never rewrite that tuple.
      // Modern sources can keep a user's already-active temporary channel.
      const current = useActivePrimary && !legacy ? await readTemporary(local, 0) : null;
      if (legacy || current?.state === 'active') {
        expected = legacy ? primary(await local('get radio')) : current.tuple;
        v = settings({...v, frequency: expected[0], bandwidth: expected[1], sf: expected[2], cr: expected[3],
          preamble: legacy ? v.preamble : current.preamble});
      }
      progress('Preparing both radios on ' + formatRadio(expected) + (legacy ? ' (Companion compatibility channel).' : '.'));
      const source = await baseline('source', local), target = await baseline('target', remote);
      need(!legacy || same(source.saved, expected), 'Companion saved channel changed during setup.');
      const sourceProfile = !legacy && source.temporaryPrimary.state === 'off' && link.supportsOtaRadio2 === true &&
        (source.adoptSecondary || source.secondaryFree && !source.overlapsPrimary) ? 1 : 0;
      const targetProfile = target.temporaryPrimary.state === 'off' &&
        (target.adoptSecondary || target.secondaryFree && !target.overlapsPrimary) ? 1 : 0;
      const adoptSource = sourceProfile ? source.adoptSecondary : source.adoptPrimary;
      const adoptTarget = targetProfile ? target.adoptSecondary : target.adoptPrimary;
      for (const [label, state, profile] of [['USB source', source, sourceProfile], ['Target', target, targetProfile]]) {
        if (profile) progress(label + ': using temporary radio2; the normal channel stays available.');
        else progress(label + ': using a primary-channel trial (' + (state.temporaryPrimary.state === 'active' ? 'primary temporary channel already active' :
          state.overlapsPrimary ? 'update channel overlaps the normal channel' :
          state.savedSecondary.state === 'unsupported' || state.temporarySecondary.state === 'unsupported' ? 'radio2 unsupported' :
          label === 'USB source' && link.supportsOtaRadio2 !== true ? 'firmware cannot select radio2 for OTA' : 'radio2 already in use') + ').');
      }
      need(!source.matchingSecondary || (sourceProfile === 1 && source.adoptSecondary), 'An existing matching source secondary must be confirmed and selected; no duplicate primary window was created.');
      need(!target.matchingSecondary || (targetProfile === 1 && target.adoptSecondary), 'An existing matching target secondary must be confirmed and selected; no duplicate primary window was created.');
      need(sourceProfile !== 0 || !source.overlappingSecondary, 'The chosen channel overlaps an existing source secondary; choose another channel before setup.');
      need(targetProfile !== 0 || !target.overlappingSecondary, 'The chosen channel overlaps an existing target secondary; choose another channel before setup.');
      // Reconnecting native USB can reset only the RAM profile selector while
      // the leases and download survive. Adopt those exact live leases without
      // changing a setting; package/journal matching remains Session.inspect's
      // separate allowExisting check before any data is sent.
      need(!target.existingDownload || (adoptSource && adoptTarget && required(source) && required(target)),
        'Target has an existing OTA download; only unchanged matching active temporary channels can be adopted.');
      const write = commands(v, {sourceProfile, targetProfile});
      // A failed primary-channel trial must return the remote to its normal
      // channel promptly. Radio2 retains the normal channel throughout.
      const trialMinutes = Math.min(2, v.minutes);
      const short = commands({...v, minutes: trialMinutes}, {sourceProfile, targetProfile});
      const trial = {target: targetProfile ? write.target : short.target, source: sourceProfile ? write.source : short.source};
      // Both complete baselines and the selected full key precede every saved
      // prerequisite write. Target changes come first; source changes cannot
      // invalidate a still-needed guarded remote exchange.
      await prerequisites('target', remote, target);
      await link.closeRadioSession();
      await prerequisites('source', local, source);
      await link.verifyPeer(key, password); keyProof(await remote('get public.key'), key);
      need(required({otaPolicy: policy(await remote('ota config'))}), 'Target prerequisite settings changed before radio setup.');
      const dualProbe = companion && !legacy && sourceProfile === 1 && typeof link.probePeerRadio === 'function';
      if (dualProbe) {
        // Prepare the USB radio first: primary keeps the known working link,
        // while temporary radio2 can test the requested channel immediately.
        await link.closeRadioSession();
        const sourceFresh = await baseline('source', local);
        need(sameSaved(sourceFresh, source) && samePrimary(sourceFresh.temporaryPrimary, source.temporaryPrimary) &&
          secondarySignature(sourceFresh.temporarySecondary) === secondarySignature(source.temporarySecondary) &&
          (sourceFresh.secondaryFree || sourceFresh.adoptSecondary) && required(sourceFresh),
          'Source radio or prerequisite settings changed before setup; no source write was sent.');
        progress('Preparing temporary radio2 on the USB Companion; its primary channel stays available.');
        if (!adoptSource) {
          sourceAttempted = true;
          need(/^(?:>\s*)?OK(?:\s|$)/i.test(String(await link.applyTemporaryRadio(trial.source)).trim()),
            'Source temporary setup was not confirmed.');
        }
        const sourceLive = await verifyActive(local, sourceProfile, 'Source');
        const oldPreamble = Number(/\bpreamble=(\d+),/i.exec(String(sourceLive.liveStatus))?.[1] || 128);
        const started = now(), deadline = started + 90000;
        need(Number.isFinite(started), 'Invalid temporary-channel clock.');
        let previous = started, lastSwitch = started, misses = 0, verified = false;
        const clock = () => {
          const at = now();
          need(Number.isFinite(at) && at >= previous, 'Invalid temporary-channel clock.');
          previous = at; return at;
        };
        const matches = state => state?.state === 'active' && Array.isArray(state.tuple) &&
          same(state.tuple, expected) && state.preamble === v.preamble;
        const probe = async profile => {
          const remaining = deadline - clock();
          if (remaining <= 0) return {reachable:false};
          const result = await link.probePeerRadio(key, password, profile, {
            radio: profile ? expected : source.saved, preamble: profile ? v.preamble : oldPreamble,
            maxDurationMs: remaining, readback: read => readTemporary(read, targetProfile)
          });
          need(!link.failure, link.failure?.message || 'USB communication stopped during the channel check.');
          need(clock() <= deadline, 'Temporary-channel link check exceeded 90 seconds.');
          need(result && typeof result.reachable === 'boolean', 'Cannot verify the temporary-channel probe result.');
          return result;
        };
        const requestSwitch = async () => {
          need(clock() < deadline && targetSwitchWrites < 3, 'Temporary-channel switch retry limit reached.');
          targetAttempted = true; targetSwitchWrites++; lastSwitch = clock();
          progress((targetSwitchWrites === 1 ? 'Requesting' : 'Requesting again after a fresh check of the old channel:') +
            ' target temporary settings on ' + formatRadio(expected) + '.');
          try {
            const reply = typeof link.requestRemoteRadio === 'function'
              ? await link.requestRemoteRadio(key, password, trial.target,
                {radio:source.saved, preamble:oldPreamble, maxDurationMs:Math.max(0, deadline - clock())})
              : await remote(trial.target, {replyTimeoutMs: Math.min(5000, deadline - clock())});
            need(/^(?:>\s*)?OK(?:\s|$)/i.test(String(reply).trim()), 'Target temporary setup was not confirmed: ' + diagnostic(reply));
          } catch (error) {
            // A lost RF reply can mean that the switch already happened. Test
            // the requested channel before considering another deliberate set.
            if (link.failure || !error?.loraReplyTimeout) throw error;
            progress('The switch reply was not received. Checking the requested channel before any further switch request.');
          }
        };
        // This proof is on the unchanged primary, after source radio2 setup.
        // The probe also pins all following admin TX to that proven profile.
        const original = await probe(0);
        need(original.reachable, 'The target did not respond on its original channel; no target switch request was sent.');
        need(original.reply && ['off', 'pending', 'active'].includes(original.reply.state), 'Target temporary state on its original channel is unknown.');
        if (!adoptTarget && !matches(original.reply) && original.reply?.state !== 'pending') await requestSwitch();
        progress('Checking the requested frequency through temporary radio2; the original primary stays available.');
        while (clock() < deadline) {
          const requested = await probe(1);
          if (requested.reachable && matches(requested.reply)) { verified = true; break; }
          misses++;
          if (misses % 3 === 0 && clock() < deadline) {
            const original = await probe(0);
            if (original.reachable) {
              const state = original.reply;
              need(state && ['off', 'pending', 'active'].includes(state.state), 'Target temporary state on its original channel is unknown.');
              // A positive pending response is progress. Repeating the setter
              // would continually restart the target's deferred switch timer.
              if (!adoptTarget && !matches(state) && state.state !== 'pending' &&
                  clock() - lastSwitch >= 5000 && targetSwitchWrites < 3) await requestSwitch();
            }
          }
          if (clock() < deadline) await wait(Math.min(250, deadline - clock()));
        }
        need(verified, 'The target did not confirm the requested frequency and preamble within 90 seconds.');
        link.otaTargetProfile = targetProfile;
        progress('The requested frequency is confirmed by the target.');
        // probePeerRadio left the exact target authenticated on source radio2.
        // Do not replace that positive proof with another long login exchange.
        await verifyActive(local, sourceProfile, 'Source');
      } else {
        // The ordinary commands are deliberately sent once. A lost reply never
        // authorizes a second command, another profile or guessed restoration.
        if (!adoptTarget) {
          targetAttempted = true;
          progress(targetProfile ? 'Opening the target temporary radio2 update window.' : 'Testing the target primary channel with a ' + trialMinutes + '-minute temporary window.');
          need(/^(?:>\s*)?OK(?:\s|$)/i.test(String(await remote(trial.target)).trim()), 'Target temporary setup was not confirmed.');
        }
        const acknowledgedAt = now(); need(Number.isFinite(acknowledgedAt), 'Invalid temporary-channel clock.');
        await link.closeRadioSession();
        const sourceFresh = await baseline('source', local);
        need(sameSaved(sourceFresh, source) && samePrimary(sourceFresh.temporaryPrimary, source.temporaryPrimary) &&
          secondarySignature(sourceFresh.temporarySecondary) === secondarySignature(source.temporarySecondary) &&
          (!sourceProfile || sourceFresh.secondaryFree || sourceFresh.adoptSecondary) && required(sourceFresh), 'Source radio or prerequisite settings changed before setup; no source write was sent.');
        progress('Preparing the source on the selected temporary profile.');
        if (!adoptSource) {
          sourceAttempted = true;
          const reply = await (companion ? link.applyTemporaryRadio(trial.source) : local(trial.source));
          need(/^(?:>\s*)?OK(?:\s|$)/i.test(String(reply).trim()), 'Source temporary setup was not confirmed.');
        }
        const start = now(); need(Number.isFinite(start) && start >= acknowledgedAt, 'Invalid temporary-channel clock.');
        let previous = start, active = false;
        for (let attempt = 0; attempt < 71; attempt++) {
          const before = now(); need(Number.isFinite(before) && before >= previous && before - start < 35000, 'Source temporary radio did not become active within 35 seconds.');
          previous = before;
          const state = await readTemporary(local, sourceProfile), after = now();
          need(Number.isFinite(after) && after >= previous && after - start <= 35000, 'Source temporary-radio read exceeded 35 seconds.'); previous = after;
          if (state.state === 'active') {
            need(same(state.tuple, expected) && state.preamble === v.preamble, 'Source temporary radio differs from the selected settings.');
            active = true; break;
          }
          need(state.state === 'pending' || state.state === 'off', 'Source temporary radio state is unknown.');
          await wait(Math.min(500, 35000 - (after - start)));
        }
        need(active, 'Source temporary radio did not become active within 35 seconds.');
        const beforeSettle = now(), left = Math.max(0, acknowledgedAt + 2500 - beforeSettle);
        need(Number.isFinite(beforeSettle) && beforeSettle >= previous && beforeSettle - start + left <= 35000, 'Temporary-channel settling exceeded 35 seconds.');
        if (left) await wait(left);
        const settled = now(); need(Number.isFinite(settled) && settled >= beforeSettle && settled >= acknowledgedAt + 2500 && settled - start <= 35000, 'Target settling was not confirmed.');
        await link.closeRadioSession();
        link.otaTargetProfile = targetProfile;
        await link.selectOtaRadio(sourceProfile);
        // Closing a Repeater peer relinquishes ownership; its verifyPeer()
        // performs a fresh login. resetRemoteSession() requires that old owner.
        // Older encrypted CLI authorization survives a radio-only change.
        // Its login acknowledgement may use the normal primary radio, so
        // retain that verified ACL and prove the full key on the new channel.
        if (companion && link.otaSupported === true) await link.resetRemoteSession(key, password);
        await link.verifyPeer(key, password);
        keyProof(await remote('get public.key'), key);
        await verifyActive(remote, targetProfile, 'Target', !targetProfile && !adoptTarget ? acknowledgedAt + trialMinutes * 60000 : null);
        await verifyActive(local, sourceProfile, 'Source');
      }
      if (targetProfile) need((await readTemporary(remote, 0)).state === 'off', 'Target primary temporary radio changed.');
      if (sourceProfile) need((await readTemporary(local, 0)).state === 'off', 'Source primary temporary radio changed.');
      await preserved(local, source, sourceProfile, 'Source'); await preserved(remote, target, targetProfile, 'Target');
      // Only a fresh authenticated round trip on the new channel permits a
      // longer primary lease. These are deliberate extensions, not retries.
      if (v.minutes > trialMinutes && ((!targetProfile && !adoptTarget) || (!sourceProfile && !adoptSource))) {
        progress('Link test passed. Extending the primary update window to ' + v.minutes + ' minutes.');
        if (!targetProfile && !adoptTarget)
          need(/^(?:>\s*)?OK(?:\s|$)/i.test(String(await remote(write.target)).trim()), 'Target update-window extension was not confirmed.');
        await link.closeRadioSession();
        if (!sourceProfile && !adoptSource) {
          const reply = await (companion ? link.applyTemporaryRadio(write.source) : local(write.source));
          need(/^(?:>\s*)?OK(?:\s|$)/i.test(String(reply).trim()), 'Source update-window extension was not confirmed.');
        }
        await wait(2500);
        if (companion && link.otaSupported === true) await link.resetRemoteSession(key, password);
        await link.verifyPeer(key, password); keyProof(await remote('get public.key'), key);
        await verifyActive(remote, targetProfile, 'Target'); await verifyActive(local, sourceProfile, 'Source');
      }
      let refreshedSourcePrimary = false;
      if (!unprobedLegacy && !sourceProfile && adoptSource && source.idleSourceVerified) {
        // A matching source window can be left over from installation with
        // only a few minutes remaining. It is our idle USB radio; refresh only
        // that exact tuple with an explicit preamble before handing it a file.
        const fresh = await baseline('source', local);
        need(fresh.idleSourceVerified && fresh.adoptPrimary && sameSaved(fresh, source) &&
          samePrimary(fresh.temporaryPrimary, source.temporaryPrimary) &&
          secondarySignature(fresh.temporarySecondary) === secondarySignature(source.temporarySecondary),
          'Source changed or became busy before its matching update window could be refreshed.');
        await link.closeRadioSession();
        sourceAttempted = true;
        progress('Refreshing the idle USB source update window to ' + v.minutes + ' minutes.');
        const reply = await (companion ? link.applyTemporaryRadio(write.source) : local(write.source));
        need(/^(?:>\s*)?OK(?:\s|$)/i.test(String(reply).trim()), 'Source update-window refresh was not confirmed.');
        await verifyActive(local, sourceProfile, 'Source');
        await link.verifyPeer(key, password); keyProof(await remote('get public.key'), key);
        await verifyActive(remote, targetProfile, 'Target');
        await preserved(local, source, sourceProfile, 'Source'); await preserved(remote, target, targetProfile, 'Target');
        refreshedSourcePrimary = true;
      }
      progress('Both temporary profiles verified. Saved radio channels are unchanged.');
      const prior=link.otaLeaseProof,previouslyOwned=!!(prior&&prior.source===link.sourceKey&&prior.target===key&&
        same(prior.tuple||[],expected)&&prior.preamble===v.preamble&&prior.sourceProfile===sourceProfile&&prior.targetProfile===targetProfile);
      link.otaLeaseProof={source:link.sourceKey,target:key,tuple:expected.slice(),preamble:v.preamble,
        sourceProfile,targetProfile,explicitProfiles:[!adoptSource||refreshedSourcePrimary||previouslyOwned&&prior.explicitProfiles?.[0]===true,
          !adoptTarget||previouslyOwned&&prior.explicitProfiles?.[1]===true]};
      return {ready: true, source: expected.slice(), target: expected.slice(), savedSource: source.saved, savedTarget: target.saved,
        savedSourceSecondary: source.savedSecondary, savedTargetSecondary: target.savedSecondary,
        sourceProfile, targetProfile, preamble: v.preamble, minutes: v.minutes, changedSettings,
        adoptedSource: adoptSource, adoptedTarget: adoptTarget, refreshedSourcePrimary};
    } catch (error) {
      const partial = targetAttempted || sourceAttempted ? ' Temporary setup may be partially active; check both radios before any further write. ' +
        (targetSwitchWrites > 1 ? 'Further switch requests followed fresh checks on the original channel; no settings were automatically restored.' : 'No write was retried or automatically restored.') : '';
      const policies = changedSettings.some(change => !change.confirmed) ? ' Unverified setting changes are listed in the diagnostic log.' : '';
      const result = Error((error?.message || 'Temporary channel setup failed.') + partial + policies);
      if (error.sourceUpdateRequired) result.sourceUpdateRequired = error.sourceUpdateRequired;
      result.changedSettings = changedSettings;
      result.targetSetupAttempted = targetAttempted; result.sourceSetupAttempted = sourceAttempted; throw result;
    }
  }
  return {recommend, recommendProfile, regions:profiles?.profiles || [], commands, prepare, readTemporary, formatRadio, primary, same, requiredReply,
    settings, temporary, secondary, crossing, schedule};
});
