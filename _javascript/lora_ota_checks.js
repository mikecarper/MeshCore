/* Shared, fail-closed checks for a browser LoRa updater. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaChecks = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const keyPattern = /^[0-9a-f]{64}$/i;
  function hex(bytes) { return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join(''); }
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
  function validateInternalPlan(plan, info, status, self, dest, qspiDiagnostics) {
    if (!plan) return;
    if (plan.storage === 'qspi') {
      const capacity = qspiCapacity(status, self, qspiDiagnostics || '', dest);
      if (!Number.isInteger(plan.app) || !Number.isInteger(plan.layoutEnd) || !Number.isInteger(plan.memorySize) ||
          plan.memorySize > plan.layoutEnd - plan.app || info.blob.length > capacity || 205 + info.blockCount * 4 > 4096)
        throw Error('The current nRF52 QSPI storage or workspace no longer fits this differential');
      return;
    }
    const ceiling = Math.min(internalStage(status, self, dest), plan.layoutCeiling);
    if ((plan.layoutFlags & 16) && info.blob.length > 4096)
      throw Error('This hybrid nRF52 package requires bootloader RAM capability that cannot be confirmed here');
    if (!Number.isInteger(plan.app) || !Number.isInteger(plan.memorySize) || !Number.isInteger(plan.layoutCeiling) ||
        plan.memorySize + Math.ceil(info.blob.length / 4096) * 4096 > ceiling - plan.app)
      throw Error('The current nRF52 staging space no longer fits this differential');
  }
  return { route, destination, validate, confirmSequentialTarget, isKnownEsp32, ensureSequentialTarget, internalStage, qspiCapacity, validateInternalPlan, version, hex };
});
