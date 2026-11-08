/* Explicit, guarded temporary-channel setup. Importing performs no I/O. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaRadioSetup = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const bandwidths = [7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500];
  const fullKey = value => typeof value === 'string' && /^[0-9a-f]{64}$/i.test(value);
  const bare = value => String(value).trim().replace(/^>\s*/, '').trim();
  const same = (a, b) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= (i === 0 ? 0.001 : 0.00001));
  const formatRadio = tuple => [Number(tuple[0]).toFixed(3), ...tuple.slice(1)].join(',');
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
  function rxps(reply) {
    // CompanionTransport already maps an explicitly unsupported RXPS control
    // to this known continuous-receive state; it requires no setting write.
    if (/^off,unsupported$/i.test(bare(reply))) return {enabled: 'off', raw: bare(reply), unsupported: true, restoreCommand: null};
    const text = bare(reply).replace(/^radio\.rxps(?:\.config)?\s+/i, ''), m = text.match(/^(on|off)(?:,(\d+),(\d+)|,level=(\d+),preamble=(\d+),rx=(\d+),sleep=(\d+)(?:,mode=(?:continuous-fast|continuous-fallback|duty-cycle))?(?:,effective-level=(\d+))?(?:,effective-preamble=(\d+))?)?$/i);
    need(m, 'Cannot verify RX power-saving settings.');
    need(m.slice(2).filter(n => n !== undefined).every(n => Number.isSafeInteger(Number(n)) && Number(n) <= 0xffffffff) &&
      (m[4] === undefined || (Number(m[4]) <= 10 && [0, 16, 32].includes(Number(m[5])))) &&
      (m[8] === undefined || (Number(m[8]) >= 1 && Number(m[8]) <= 10)) &&
      (m[9] === undefined || (Number(m[9]) >= 1 && Number(m[9]) <= 65535)), 'Unknown RX power-saving fields.');
    const enabled = m[1].toLowerCase();
    // Pair-only replies can report effective periods rather than the saved
    // level. Do not invent a command that would overwrite that saved mode.
    let restoreCommand = null;
    if (enabled === 'on' && m[4] && Number(m[4]) > 0) {
      need(Number(m[4]) <= 10 && [0, 16, 32].includes(Number(m[5])), 'Unknown RX power-saving level.');
      restoreCommand = 'set radio.rxps level ' + Number(m[4]) + (Number(m[5]) ? ' preamble ' + Number(m[5]) : '');
    } else if (enabled === 'on' && (m[2] || m[6])) {
      const receive = Number(m[2] ?? m[6]), sleep = Number(m[3] ?? m[7]);
      need(Number.isSafeInteger(receive) && Number.isSafeInteger(sleep) && receive > 0 && sleep > 0, 'Unknown custom RX power-saving periods.');
    }
    return {enabled, raw: text, restoreCommand};
  }
  function keyProof(reply, key) {
    const text = bare(reply);
    need(fullKey(text) && text.toLowerCase() === key, 'The selected target did not prove its complete public key.');
  }
  function unsupported(reply) {
    return /^(?:Error: (?:unsupported|radio profiles unsupported|profiles unsupported)|ERR:? unsupported|unsupported|Unknown command)$/i.test(bare(reply));
  }
  function secondary(reply) {
    const text = bare(reply);
    if (unsupported(reply)) return {state: 'unsupported'};
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
    if (unsupported(raw)) return {state: 'unsupported', profile, raw};
    if (!profile) { const state = temporary(raw); return {...state, radio: state.tuple, profile, raw}; }
    const config = secondary(raw);
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
  function schedule(reply) {
    if (unsupported(reply)) return {unsupported: true};
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
    if (unsupported(reply)) return 'unsupported';
    const result = bare(reply).toLowerCase();
    need(['off', 'on', 'auto'].includes(result), 'Cannot verify secondary crossing policy.');
    return result;
  }
  async function prepare(link, key, password, value, {progress = () => {}, useActivePrimary = false,
      wait = ms => new Promise(r => setTimeout(r, ms)), now = () => Date.now()} = {}) {
    let v = settings(value), expected = [v.frequency, v.bandwidth, v.sf, v.cr];
    const companion = link?.browserRawSource === true, legacy = companion && link.otaSupported === false;
    need(link && !link.serving && !link.failure && (!link.relays || (Array.isArray(link.relays) && link.relays.length === 0)) &&
      (!companion || (link.localCliSupported === true && (legacy || (link.otaSupported === true && (link.otaMode === undefined || link.otaMode === 'guarded'))))),
      'Automatic channel setup requires an idle direct connection with USB radio controls.');
    need(fullKey(key) && fullKey(link.sourceKey) && fullKey(link.knownRepeater) && key.toLowerCase() === link.knownRepeater.toLowerCase() &&
      key.toLowerCase() !== link.sourceKey.toLowerCase(), 'Select the complete target public key before preparing the channel.');
    for (const method of ['localCommand', 'remoteCommand', 'verifyPeer', 'closeRadioSession', 'resetRemoteSession', 'selectOtaRadio',
      ...(companion ? ['applyTemporaryRadio'] : [])]) need(typeof link[method] === 'function', 'This connection cannot safely prepare a temporary channel.');
    key = key.toLowerCase();
    const remote = command => link.remoteCommand(key, password, command), local = command => link.localCommand(command);
    let targetAttempted = false, sourceAttempted = false;
    const changedSettings = [];
    async function baseline(label, read) {
      const saved = primary(await read('get radio'));
      const temporaryPrimary = await readTemporary(read, 0);
      need(['off', 'active'].includes(temporaryPrimary.state), label + ' has a pending or unverified primary temporary radio.');
      const powerSaving = rxps(await read('get radio.rxps')), config = await read('ota config');
      const otaPolicy = legacy && label === 'source' && unsupported(config) ? null : policy(config);
      let existingDownload = false;
      if (label === 'source') {
        const stats = bare(await read('ota stats')), fetch = [...stats.matchAll(/\bfetch\s+([a-z]+)\b/gi)];
        need(legacy && unsupported(stats) || fetch.length === 1 && fetch[0][1].toLowerCase() === 'idle', 'Source has an active or unverified OTA download.');
        const folder = bare(await read('ota folder'));
        need(legacy && unsupported(folder) || /^folder=off(?:\s|$)/i.test(folder), 'Source has an attached or unverified OTA folder.');
      } else {
        const status = bare(await read('ota status'));
        const idle = /\bno download\b/i.test(status) && !/\bdownload:/i.test(status);
        existingDownload = !idle && /\bdownload:\s*[^|\r\n]+/i.test(status) && !/\bno download\b/i.test(status);
        need(idle || existingDownload, 'Target OTA download state cannot be verified.');
      }
      const savedSecondary = secondary(await read('get radio2'));
      const temporarySecondary = secondary(await read('get tempradio2'));
      const cross = crossing(await read('get radio2.cross'));
      need(cross !== 'on', 'Automatic setup cannot use existing radio2 crossing on; turn crossing off or use manual setup.');
      need(cross !== 'auto' || temporarySecondary.state !== 'configured',
        'Automatic crossing with an existing temporary radio2 would duplicate OTA traffic; turn crossing off or use manual setup.');
      const schedules = {};
      for (const command of ['get radioat', 'get tempradioat', 'get radioat2', 'get tempradioat2']) {
        const reply = await read(command), status = schedule(reply);
        // Full Companion implements no primary scheduler. Its exact shared
        // callback rejection is a known absence, not an unknown-state bypass.
        const absentPrimary = companion && label === 'source' && (command === 'get radioat' || command === 'get tempradioat') && unsupported(reply);
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
        overlapsPrimary, adoptPrimary, adoptSecondary, powerSaving, otaPolicy, existingDownload};
    }
    function sameSaved(a, b) {
      return same(a.saved, b.saved) && secondarySignature(a.savedSecondary) === secondarySignature(b.savedSecondary) && a.cross === b.cross &&
        JSON.stringify(a.schedules) === JSON.stringify(b.schedules);
    }
    function required(state) {
      return state.powerSaving.enabled === 'off' && (state.otaPolicy === null || state.otaPolicy.hops === '0' &&
        state.otaPolicy.autofetch === 'off' && state.otaPolicy.autoinstall === 'off');
    }
    async function prerequisites(endpoint, read, before) {
      const changes = [];
      if (before.powerSaving.enabled !== 'off') changes.push({setting: 'radio.rxps', before: before.powerSaving.enabled,
        beforeRaw: before.powerSaving.raw, after: 'off', command: 'set radio.rxps off', restoreCommand: before.powerSaving.restoreCommand});
      for (const name of before.otaPolicy ? ['autofetch', 'autoinstall', 'hops'] : []) {
        const desired = name === 'hops' ? '0' : 'off';
        if (before.otaPolicy[name] !== desired) changes.push({setting: 'ota.' + name, before: before.otaPolicy[name], after: desired,
          command: 'ota config ' + name + ' ' + desired, restoreCommand: 'ota config ' + name + ' ' + before.otaPolicy[name]});
      }
      for (const change of changes) {
        const entry = {...change, endpoint, key: endpoint === 'source' ? link.sourceKey.toLowerCase() : key, confirmed: false};
        changedSettings.push(entry);
        progress('Changing saved ' + endpoint + ' setting ' + change.setting + ' to ' + change.after + '. Setup changes will be retained.');
        try {
          need(/^(?:>\s*)?OK(?:\s|$)/i.test(String(await read(change.command)).trim()), 'Saved prerequisite change was not acknowledged.');
          const observed = change.setting === 'radio.rxps' ? rxps(await read('get radio.rxps')).enabled :
            policy(await read('ota config'))[change.setting.slice(4)];
          need(observed === change.after, 'Saved prerequisite change failed readback.');
          entry.confirmed = true;
        } catch (error) { entry.uncertain = true; throw error; }
      }
    }
    async function verifyActive(read, profile, label) {
      const state = await readTemporary(read, profile);
      need(state.state === 'active' && same(state.tuple, expected) && state.preamble === v.preamble,
        label + ' selected temporary radio differs from the requested settings.');
      return state;
    }
    function samePrimary(a, b) {
      return a.state === b.state && (a.state === 'off' || same(a.tuple, b.tuple) && a.preamble === b.preamble);
    }
    async function preserved(read, before, chosen, label) {
      need(same(primary(await read('get radio')), before.saved) &&
        secondarySignature(secondary(await read('get radio2'))) === secondarySignature(before.savedSecondary) &&
        crossing(await read('get radio2.cross')) === before.cross,
        label + ' saved primary, secondary or crossing settings changed.');
      if (!chosen) need(secondarySignature(secondary(await read('get tempradio2'))) === secondarySignature(before.temporarySecondary),
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
      need(required({powerSaving: rxps(await remote('get radio.rxps')), otaPolicy: policy(await remote('ota config'))}), 'Target prerequisite settings changed before radio setup.');
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
      if (companion) await link.resetRemoteSession(key, password);
      await link.verifyPeer(key, password);
      keyProof(await remote('get public.key'), key);
      await verifyActive(remote, targetProfile, 'Target'); await verifyActive(local, sourceProfile, 'Source');
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
        if (companion) await link.resetRemoteSession(key, password);
        await link.verifyPeer(key, password); keyProof(await remote('get public.key'), key);
        await verifyActive(remote, targetProfile, 'Target'); await verifyActive(local, sourceProfile, 'Source');
      }
      progress('Both temporary profiles verified. Saved radio channels are unchanged.');
      return {ready: true, source: expected.slice(), target: expected.slice(), savedSource: source.saved, savedTarget: target.saved,
        savedSourceSecondary: source.savedSecondary, savedTargetSecondary: target.savedSecondary,
        sourceProfile, targetProfile, preamble: v.preamble, minutes: v.minutes, changedSettings,
        adoptedSource: adoptSource, adoptedTarget: adoptTarget};
    } catch (error) {
      const partial = targetAttempted || sourceAttempted ? ' Temporary setup may be partially active; check both radios before any further write. No write was retried or automatically restored.' : '';
      const policies = changedSettings.length ? ' Saved prerequisite changes may remain applied; review the recorded changes before proceeding.' : '';
      const result = Error((error?.message || 'Temporary channel setup failed.') + partial + policies);
      result.changedSettings = changedSettings;
      result.targetSetupAttempted = targetAttempted; result.sourceSetupAttempted = sourceAttempted; throw result;
    }
  }
  return {recommend, commands, prepare, readTemporary, formatRadio, rxps};
});
