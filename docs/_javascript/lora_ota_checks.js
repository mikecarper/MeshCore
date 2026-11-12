/* Shared, fail-closed checks for a browser LoRa updater. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaChecks = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const keyPattern = /^[0-9a-f]{64}$/i;
  function hex(bytes) { return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join(''); }
  // Only offer a diagnosis when the receiver reported that exact reason.
  // Older targets may report only "failed", so leave unknown reasons alone.
  function failureAdvice(detail) {
    const text = String(detail || '').replace(/[^\x20-\x7e]/g, ' ').replace(/\s+/g, ' ').trim();
    const match = /^failed\s*(?:\(([^)]*)\)|:\s*(.*?)(?=\s+\d+\/\d+\b|\s+id=|$))/i.exec(text);
    const reason = (match?.[1] || match?.[2] || '').trim().toLowerCase();
    switch (reason) {
      case 'storage error':
        return 'Check target storage with get storage.layout and ota self; staging space or storage may be unavailable.';
      case 'manifest timeout':
      case 'leaves timeout':
        return 'Check the LoRa path and keep both radios on the update channel. Retry the same package; saved blocks may be reused.';
      case 'radio session changed':
        return 'Check that both radios stay on the same update channel, including temporary-radio settings, before retrying.';
      case 'unsupported codec':
      case 'unsupported hash':
      case 'invalid geometry':
        return 'Check the target firmware and select a compatible update package before trying again.';
      case 'not a newer version':
        return 'Check the target firmware version and select a newer compatible package.';
      case 'image too large':
        return 'Check target storage and select a smaller compatible package.';
      case 'invalid manifest':
      case 'integrity check':
        return 'Check the package and source connection before retrying; do not install an unverified update.';
      default:
        return '';
    }
  }
  function route(target, relays, source, width = 3) {
    if (!keyPattern.test(target) || !keyPattern.test(source) || !Array.isArray(relays) || relays.length > 8 || ![1, 2, 3].includes(width)) throw Error('Invalid route or public key');
    const ids = [source, ...relays, target].map(k => { if (!keyPattern.test(k)) throw Error('A full public key is required'); return k.toLowerCase(); });
    if (new Set(ids.map(k => k.slice(0, width * 2))).size !== ids.length) throw Error('Route identities have matching prefixes');
    return relays.map(k => k.slice(0, width * 2).toUpperCase());
  }
  function version(text) {
    const m = /\b[vV]?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?\b/.exec(text);
    if (!m || m.slice(1).some(v => v && Number(v) > 255)) throw Error('Cannot determine firmware version');
    return m.slice(1).reduce((n, v) => n * 256 + Number(v || 0), 0);
  }
  // A missing, ambiguous or malformed capability is unknown, never a 2 KiB
  // permission. These values are live application limits, not board guesses.
  function explicitMaxBlock(text) {
    if (/^\s*(?:>\s*)?(?:ERR(?:OR)?\b|unknown\b|unsupported\b)/i.test(String(text || ''))) return null;
    const tokens = [...String(text || '').matchAll(/\bmaxblk:([^\s|]+)/gi)].map(m => m[1]);
    const labels = [...String(text || '').matchAll(/\bmaxblk:/gi)];
    if (!tokens.length || tokens.length !== labels.length || tokens.some(token => !/^\d+$/.test(token))) return null;
    const values = tokens.map(Number);
    if (new Set(values).size !== 1) return null;
    const value = values[0];
    return Number.isInteger(value) && value >= 1 && value <= 2048 && !(value & (value - 1)) ? value : null;
  }
  function destination(status, self, ver) {
    const target = /\btarget:([0-9a-f]{8})\b/i.exec(status), hw = /\bhw=([^ |]+)/i.exec(status), body = /\bbase_hash=([0-9a-f]{16})\b/i.exec(self);
    if (!target || !hw || !body) throw Error('Target identity or installed firmware hash unavailable');
    const combined = status + ' ' + self;
    if (/(?:QSPI|SD) store:ERR|storage unsafe:|\bNO\s+(?:(?:QSPI|SD|delta)\s+)?(?:mota-)?apply\b|\bbl:(?:NONE|storage-ERR|NO-QSPI|NO-SD)\b/i.test(combined)) throw Error('Target storage or bootloader is not ready');
    const nrf = /bootloader:|\bbl:/i.test(combined);
    const blocks = [...combined.matchAll(/\bmaxblk:(\d+)\b/gi)].map(m => Number(m[1]));
    if (new Set(blocks).size > 1) throw Error('Target reports conflicting block limits');
    const maxBlock = blocks[0] || 1024;
    if (maxBlock > 2048 || maxBlock < 1 || (maxBlock & (maxBlock - 1))) throw Error('Unsupported target block limit');
    const caps = /abi=(\d+)\s+codecs=0x([0-9a-f]+)/i.exec(combined);
    if (nrf && (!caps || Number(caps[1]) < 3)) throw Error('Target did not confirm its supported bootloader codecs');
    return { targetId: parseInt(target[1], 16), hwId: hw[1], baseHash: body[1].toLowerCase(), version: version(ver), nrf, maxBlock, codecs: nrf ? parseInt(caps[2], 16) : 7, external: /(?:QSPI|SD) apply OK/i.test(combined), sd: /SD apply OK|\bbl:SD\b/i.test(combined) };
  }
  // Only an explicit command rejection proves that the receiver is absent.
  // Missing replies, access errors and incomplete metadata remain unknown.
  function availability(status, self) {
    const reply = String(status || '').trim().replace(/^>\s*/, '');
    if (/^(?:(?:err(?:or)?)[\s:–-]+)?(?:unknown(?:\s+(?:command|cmd))?|unsupported(?:\s+command)?|(?:command\s+)?not supported)[.!]?$/i.test(reply) ||
        /^Unknown OTA command(?:\. Type `ota help`\.|; use ota help)$/i.test(reply) ||
        /^(?:LoRa OTA is not included in this build; tempradio cannot enable it\. Use an OTA-enabled repeater firmware|LoRa OTA absent; tempradio cannot enable it\. Install OTA repeater firmware|LoRa OTA absent; use OTA repeater; tempradio cannot enable it)$/.test(reply))
      return {state:'unsupported', message:'LoRa updates are not available on this target’s current firmware. Install a compatible LoRa OTA receiver build using USB or another supported update method first.'};
    if (/\bOTA\s+seeder\b|\binstall:disabled\b|\bsource-only; local install disabled\b/i.test(reply + ' ' + String(self || '')))
      return {state:'unsupported', message:'LoRa updates are not available for this target: its firmware can send updates but cannot receive or install them. Install an OTA receiver build using USB or another supported update method first.'};
    const target=/\btarget:([0-9a-f]{8})\b/i.exec(reply), hardware=/\bhw=([^ |]+)/i.exec(reply);
    if (!target || target[1]==='00000000' || !hardware || hardware[1]==='?')
      return {state:'unknown', message:'LoRa update support could not be confirmed from the target’s reply. Check access and connectivity, then investigate again.'};
    const combined = reply + ' ' + String(self || '');
    if (/(?:QSPI|SD) store:ERR|storage unsafe:|\bNO\s+(?:(?:QSPI|SD|delta)\s+)?(?:mota-)?apply\b|\bbl:(?:NONE|storage-ERR|NO-QSPI|NO-SD)\b/i.test(combined))
      return {state:'unavailable', message:'LoRa updates are unavailable: the target reports that its storage or OTA bootloader is not ready. Check its bootloader and storage before updating.'};
    if (self === undefined)
      return {state:'unknown', message:'Checking whether this target can receive LoRa updates…'};
    try {
      destination(reply, String(self || ''), '0.0.0');
      return {state:'supported', message:'LoRa OTA receiver detected. Firmware compatibility and radio settings still need to pass their checks.'};
    } catch (error) {
      if (/bootloader codecs/.test(error.message))
        return {state:'unknown', message:'LoRa update support could not be confirmed: the target has not reported its supported OTA bootloader codecs. Check the bootloader diagnostics before updating.'};
      if (/block limit/.test(error.message))
        return {state:'unavailable', message:'LoRa updates are unavailable in this browser: ' + error.message + '. Check the target’s OTA bootloader and firmware.'};
      return {state:'unknown', message:'LoRa update support could not be confirmed. The target did not provide a usable OTA status and installed-image identity. Check access and connectivity, then investigate again.'};
    }
  }
  // Used by manual package checks too, so skipping investigation does not
  // defer an explicit receiver rejection until after unrelated target reads.
  async function readReceiver(remote) {
    const normalize = value => String(value || '').trim().replace(/^>\s*/, '');
    const status = normalize(await remote('ota status'));
    const first = availability(status);
    const reject = result => { const error=Error(result.message);error.otaAvailability=result;throw error; };
    if (first.state==='unsupported' || first.state==='unavailable')reject(first);
    const self = normalize(await remote('ota self'));
    const result = availability(status, self);
    if(result.state!=='supported')reject(result);
    return {status,self};
  }
  function validate(info, dest, verifiedSigner) {
    if (!info || !dest || !(info.baseHash instanceof Uint8Array)) throw Error('Invalid package metadata');
    if (info.isBootloader) throw Error('Bootloader updates are not supported by this updater');
    if (info.signed && !verifiedSigner) throw Error('A signed package requires its full trusted signing key');
    if (info.targetId !== dest.targetId || info.hwId !== dest.hwId) throw Error('Package hardware or target does not match');
    if (info.fwVersion <= dest.version) throw Error('Package is not newer than the installed firmware');
    if (info.blockSize > dest.maxBlock) throw Error('Package block size exceeds target limit');
    if (!info.isFull && hex(info.baseHash.subarray(0, 8)) !== dest.baseHash) throw Error('Differential package does not match the exact installed firmware');
    if (dest.nrf && !(dest.codecs & (1 << info.codecId))) throw Error('Installed bootloader does not support the package codec');
    if (dest.nrf && info.isFull && !dest.external) throw Error('This nRF52 requires an in-place differential update');
    if (!info.isFull && info.codecId !== (dest.nrf ? 2 : 1)) throw Error('Differential codec does not match the target');
    if (dest.sd && !verifiedSigner) throw Error('This SD installer requires a verified trusted signing key');
    return true;
  }
  function confirmSequentialTarget(status, self, bootloaderReply) {
    if (/\bbl:|bootloader:/i.test(status + ' ' + self) || !/^\s*(?:err(?:or)?\s*:?\s*)unsupported\s*$/i.test(bootloaderReply))
      throw Error('Cannot confirm the target supports the ESP32 sequential delta codec');
  }
  // The reported hardware tag must match exactly one family in the published
  // build metadata and that family must be ESP32 only. Unknown or conflicting
  // metadata is not evidence: callers retain their conservative probe.
  async function isKnownEsp32(status, self, options = {}) {
    const hw=/\bhw=([^\s|]+)/i.exec(status);
    if (!hw || hw[1] === '?' || !/\btarget:[0-9a-f]{8}\b/i.test(status) ||
        !/\bbase_hash=[0-9a-f]{16}\b/i.test(self) || /\bbl:|bootloader:/i.test(status+' '+self)) return false;
    try {
      const url = options.controlsUrl || new URL('../_data/firmware_controls.json', globalThis.location.href);
      const response=await (options.fetchImpl || fetch)(url,{signal:AbortSignal.timeout(5000)});
      if (!response.ok) return false;
      const profiles=(await response.json())?.profiles;
      if (!profiles || typeof profiles !== 'object' || Array.isArray(profiles)) return false;
      const platforms=new Set();
      for (const [name, profile] of Object.entries(profiles)) {
        const match=/^(.+?)[_-](?:partition_expander|repeater|repeatr|room_server|room_svr|sensor|terminal_chat|kiss_modem|companion_radio|companion|comp_radio)(?=[_-]|$)/i.exec(name);
        if (!match) continue;
        let family=match[1];
        family=({'heltec_wsl3':'Heltec_v3','heltec_t114_without_display':'Heltec_t114','rak_4631':'RAK4631'})[family.toLowerCase()]||family;
        if (family.toLowerCase() === hw[1].toLowerCase()) platforms.add(profile?.platform || '');
      }
      return platforms.size === 1 && platforms.has('ESP32_PLATFORM');
    } catch (_) { return false; }
  }
  async function ensureSequentialTarget(status, self, getBootloader, options = {}) {
    if (await isKnownEsp32(status, self, options)) return;
    confirmSequentialTarget(status, self, await getBootloader());
  }
  function internalStage(status, self, dest) {
    if (!dest.nrf || !(dest.codecs & 4) || dest.external || /\bbl:(?:QSPI|SD)\b|\b(?:QSPI|SD) store\b/i.test(status + ' ' + self) || !/\bbl:(?:internal|apply)\b|\binternal store:/i.test(status + ' ' + self))
      throw Error('nRF52 in-place generation requires confirmed internal storage and bootloader support');
    const match = /\bstage=([0-9a-f]{5,8})\b/i.exec(self);
    const ceiling = match ? parseInt(match[1], 16) : 0xD4000;
    if (ceiling !== 0xD4000 && ceiling !== 0xED000) throw Error('Unsupported nRF52 staging ceiling');
    return ceiling;
  }
  function qspiCapacity(status, self, diagnostics, dest) {
    if (!dest.nrf || !(dest.codecs & 4) || !dest.external || dest.sd || /\bbl:internal\b|\binternal store:|\bSD store\b/i.test(status + ' ' + self) ||
        !/\bbl:QSPI\b/i.test(status) || !/bootloader:\s*QSPI apply OK\b/i.test(self) ||
        !/^\s*QSPI\b/i.test(diagnostics) || /\berror=/i.test(diagnostics))
      throw Error('nRF52 QSPI storage and in-place apply support are not confirmed');
    const match = /\bsize=(\d+)K\b/i.exec(diagnostics);
    const bytes = match ? Number(match[1]) * 1024 : 0;
    if (!Number.isSafeInteger(bytes) || bytes <= 0) throw Error('QSPI capacity is unavailable');
    return bytes;
  }
  function needsHybridRam(plan, info) {
    return !!plan && plan.storage !== 'qspi' && !!(plan.layoutFlags & 16) && info?.blob?.length > 4096;
  }
  function confirmHybridRam(reply) {
    if (typeof reply !== 'string' || !/^(?:>[ \t]*)?OTA[ \t]+RAM:[ \t]+hybrid=1[ \t]+abi=1[ \t]+record=72[ \t]+arena=65536$/i.test(reply.trim()))
      throw Error('This hybrid nRF52 package requires bootloader RAM capability that cannot be confirmed here');
    return reply.trim();
  }
  async function readHybridRam(plan, info, getReply) {
    if (!needsHybridRam(plan, info)) return null;
    let reply;
    try { reply = await getReply(); }
    catch (_) { throw Error('This hybrid nRF52 package requires bootloader RAM capability that cannot be confirmed here'); }
    return confirmHybridRam(reply);
  }
  function validateInternalPlan(plan, info, status, self, dest, qspiDiagnostics, hybridRamReply) {
    if (!plan) return;
    if (plan.storage === 'qspi') {
      const capacity = qspiCapacity(status, self, qspiDiagnostics || '', dest);
      if (!Number.isInteger(plan.app) || !Number.isInteger(plan.layoutEnd) || !Number.isInteger(plan.memorySize) ||
          plan.memorySize > plan.layoutEnd - plan.app || info.blob.length > capacity || 205 + info.blockCount * 4 > 4096)
        throw Error('The current nRF52 QSPI storage or workspace no longer fits this differential');
      return;
    }
    const ceiling = Math.min(internalStage(status, self, dest), plan.layoutCeiling);
    if (needsHybridRam(plan, info)) confirmHybridRam(hybridRamReply);
    if (!Number.isInteger(plan.app) || !Number.isInteger(plan.memorySize) || !Number.isInteger(plan.layoutCeiling) ||
        plan.memorySize + Math.ceil(info.blob.length / 4096) * 4096 > ceiling - plan.app)
      throw Error('The current nRF52 staging space no longer fits this differential');
  }
  return { route, destination, availability, readReceiver, validate, confirmSequentialTarget, isKnownEsp32, ensureSequentialTarget, internalStage, qspiCapacity, needsHybridRam, confirmHybridRam, readHybridRam, validateInternalPlan, version, hex, failureAdvice, explicitMaxBlock };
});
