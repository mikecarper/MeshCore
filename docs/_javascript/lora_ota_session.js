/* A deliberately conservative direct LoRa OTA session. */
(function (root, factory) {
  const checks = typeof module === 'object' && module.exports ? require('./lora_ota_checks.js') : root.LoraOtaChecks;
  const api = factory(checks);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaSession = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (checks) {
  'use strict';
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  function value(text) { const s = String(text).trim().replace(/^>\s*/, ''); if (!s || /^(error|err\b|unknown\b|unsupported\b)/i.test(s)) throw Error(s || 'Empty response'); return s; }
  function temp(text) {
    const s = value(text).replace(/^TempRadio active:\s*/i, '');
    if (/^(?:off|inactive|pending)\b/i.test(s)) throw Error('An active temporary radio window is required');
    const m = s.match(/^(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)/);
    if (!m) throw Error('Cannot read active temporary radio settings');
    return m.slice(1).map(Number);
  }
  function download(status, stats = '') {
    if (/\bno download\b/i.test(status)) return {state: 'idle'};
    let section = /\bdownload:\s*([^|]+)/i.exec(status)?.[1] || '';
    if (!/\bid=([0-9a-f]{8})\b/i.test(section)) section = /\bfetch\s+([^|]+)/i.exec(stats)?.[1] || '';
    if (/^idle\b/i.test(section)) return {state: 'idle'};
    const m = /\bid=([0-9a-f]{8})\b/i.exec(section);
    if (!m) throw Error('Cannot confirm the target download identity');
    return {id: m[1].toLowerCase(), state: /^failed\b/i.test(section) ? 'failed' : /^(?:ready to install|done)\b/i.test(section) ? 'ready' : 'active'};
  }
  function policy(config) {
    if (!/\bhops=0\b/i.test(config)) throw Error('Set OTA hops to 0 on both radios for a direct update');
    if (!/mode=seeder-only/i.test(config) && (!/\bautofetch=off\b/i.test(config) || !/\bautoinstall=off\b/i.test(config))) throw Error('Turn off automatic OTA fetch and install before starting');
  }
  async function expectedBody(info, image) {
    let data = info.isFull ? info.payload : image;
    if (!(data instanceof Uint8Array)) throw Error('Select the new firmware image to verify the differential update');
    if (data.length !== info.imageSize || data.length < 56) throw Error('The new firmware image size does not match the package');
    const cryptoApi = globalThis.crypto?.subtle || (typeof require === 'function' ? require('node:crypto').webcrypto.subtle : null);
    if (!cryptoApi) throw Error('Web Crypto is unavailable');
    const digest = new Uint8Array(await cryptoApi.digest('SHA-256', data));
    if (checks.hex(digest) !== checks.hex(info.imageHash)) throw Error('The new firmware image does not match the package');
    const off = data.length - 56, view = new DataView(data.buffer, data.byteOffset + off, 56);
    const hardware = new TextDecoder().decode(data.slice(off + 24, off + 56)).replace(/\0+$/, '');
    if (String.fromCharCode(...data.slice(off, off + 4)) !== 'EndF' || view.getUint32(4, true) !== off || view.getUint32(16, true) !== info.fwVersion || view.getUint32(20, true) !== info.targetId || hardware !== info.hwId) throw Error('New firmware identity trailer is invalid');
    const body = new Uint8Array(await cryptoApi.digest('SHA-256', data.subarray(0, off)));
    if (checks.hex(body.subarray(0, 8)) !== checks.hex(data.subarray(off + 8, off + 16))) throw Error('New firmware body hash is invalid');
    return checks.hex(body.subarray(0, 8));
  }
  async function installHash(blob) {
    if (!(blob instanceof Uint8Array) || blob.length < 210) throw Error('Invalid mOTA container for installation');
    const normalized = blob.slice();
    normalized.fill(0, 201, 205); // Target approval marker can change after receipt.
    const api = globalThis.crypto?.subtle || (typeof require === 'function' ? require('node:crypto').webcrypto.subtle : null);
    if (!api) throw Error('Web Crypto is unavailable');
    return checks.hex(new Uint8Array(await api.digest('SHA-256', normalized)));
  }
  class DirectSession {
    constructor(link, key, password, info, options = {}) {
      this.link = link; this.key = key; this.password = password; this.info = info;
      this.signerVerified = !!options.signerVerified; this.image = options.image; this.compress = options.compress || null; this.inplacePlan = options.inplacePlan || null;
      this.pollMs = options.pollMs || 20000; this.transferTimeoutMs = options.transferTimeoutMs || 4 * 60 * 60 * 1000; this.progress = options.progress || (() => {}); this.journal = options.journal || (() => {}); this.readJournal = options.readJournal || (() => null);
      this.cancelled = false; this.serving = false;
    }
    async remote(command) { return value(await this.link.remoteCommand(this.key, this.password, command)); }
    async local(command) { return value(await this.link.localCommand(command)); }
    async requireInstallHash() {
      const reply = String(await this.link.remoteCommand(this.key, this.password, 'ota installhash')).trim().replace(/^>\s*/, '');
      if (reply !== 'ERR usage: ota installhash <64-hex-container-sha256>')
        throw Error('Target firmware does not support verified OTA installation; update it before using this updater');
    }
    async readDownload() {
      const status = await this.remote('ota status');
      let parsed;
      try { parsed = download(status); } catch (_) { parsed = download(status, await this.remote('ota stats')); }
      return { ...parsed, status };
    }
    async inspect(options = {}) {
      await this.link.verifyPeer(this.key, this.password);
      const status = await this.remote('ota status'), self = await this.remote('ota self'), ver = await this.remote('ver');
      const dest = checks.destination(status, self, ver);
      const cryptoApi = globalThis.crypto?.subtle || (typeof require === 'function' ? require('node:crypto').webcrypto.subtle : null);
      if (!cryptoApi) throw Error('Web Crypto is unavailable');
      this.packageHash = checks.hex(new Uint8Array(await cryptoApi.digest('SHA-256', this.info.blob)));
      this.installHash = await installHash(this.info.blob);
      const prior = this.readJournal();
      if (options.allowExisting && prior?.phase === 'install attempted' && prior.target?.toLowerCase() === this.key.toLowerCase() && prior.source?.toLowerCase() === this.link.sourceKey?.toLowerCase() && prior.id === checks.hex(this.info.merkleRoot) && prior.packageHash === this.packageHash && dest.targetId === this.info.targetId && dest.hwId === this.info.hwId && dest.version === this.info.fwVersion) {
        this.bodyHash = await expectedBody(this.info, this.image);
        if (dest.baseHash === this.bodyHash && prior.bodyHash === this.bodyHash) {
          this.journal({phase:'installed', target:this.key, source:this.link.sourceKey, id:prior.id, packageHash:this.packageHash, bodyHash:this.bodyHash});
          return {...dest, download:'installed'};
        }
      }
      checks.validate(this.info, dest, this.signerVerified);
      checks.validateInternalPlan(this.inplacePlan, this.info, status, self, dest,
        this.inplacePlan?.storage === 'qspi' ? await this.remote('ota qspi') : null);
      if (!this.info.isFull && this.info.codecId === 1)
        checks.confirmSequentialTarget(status, self, await this.link.remoteCommand(this.key, this.password, 'get bootloader.ver'));
      if (this.info.signed) {
        const trusted = await this.remote('ota key list');
        if (!trusted.toLowerCase().split(/\s+/).includes(checks.hex(this.info.signer.subarray(0, 8)))) throw Error('The target does not list the supplied trusted signer; add it on the target first');
      }
      this.bodyHash = await expectedBody(this.info, this.image);
      for (const command of [c => this.local(c), c => this.remote(c)]) {
        const radio = temp(await command('get tempradio'));
        const primary = temp(await command('get radio'));
        if (radio.some((v, i) => Math.abs(v - primary[i]) > 0.001)) throw Error('For this direct prototype, the temporary radio must match each radio’s saved primary channel');
        const rxps = await command('get radio.rxps');
        if (!/^off\b/i.test(rxps)) throw Error('Turn RX power saving off before starting');
        policy(await command('ota config'));
        if (!this.tempRadio) this.tempRadio = radio;
        else if (radio.some((v, i) => Math.abs(v - this.tempRadio[i]) > 0.001)) throw Error('Temporary radio settings do not match');
      }
      await this.requireInstallHash();
      const state = await this.readDownload();
      if (state.state !== 'idle' && (!options.allowExisting || state.id !== checks.hex(this.info.merkleRoot) || state.state === 'failed')) throw Error('Target already has another or failed OTA download; resolve it first');
      if (state.state !== 'idle') {
        const record = this.readJournal();
        if (!record || record.target?.toLowerCase() !== this.key.toLowerCase() || record.source?.toLowerCase() !== this.link.sourceKey?.toLowerCase() || record.id !== state.id || record.packageHash !== this.packageHash || !['pull attempted', 'ready'].includes(record.phase))
          throw Error('The existing download has no matching browser recovery record; resolve it before proceeding');
      }
      if (!/\bfetch idle\b/i.test(await this.local('ota stats'))) throw Error('The source already has an OTA download');
      if (!/^folder=off\b/i.test(await this.local('ota folder'))) throw Error('The source already has an attached OTA folder');
      return {...dest, download:state.state};
    }
    async start(options = {}) {
      this.cancelled = false; this.tempRadio = null; const before = await this.inspect({allowExisting:!!options.resume});
      const id = checks.hex(this.info.merkleRoot);
      if (before.download === 'installed') return;
      if (options.resume && before.download === 'idle') throw Error('There is no matching download to resume');
      if (!options.resume && before.download !== 'idle') throw Error('Target already has a download');
      this.journal({phase: options.resume ? 'pull attempted' : 'prepared', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, before, bodyHash: this.bodyHash});
      if (before.download === 'ready') { this.journal({phase: 'ready', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, before, bodyHash: this.bodyHash}); return; }
      try {
        await this.link.serve(this.info, this.info.blob, this.compress); this.serving = true;
        if (!options.resume) {
          let found = false;
          for (let n = 0; n < 12 && !this.cancelled; n++) {
            if ((await this.remote('ota ls')).toLowerCase().includes(id)) { found = true; break; }
            await sleep(this.pollMs);
          }
          if (!found) throw Error('Target did not discover the selected package');
          if ((await this.readDownload()).state !== 'idle') throw Error('Target download state changed');
          this.journal({phase: 'pull attempted', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, before, bodyHash: this.bodyHash});
          try { await this.remote('ota pull ' + id.toUpperCase() + ' flash'); }
          catch (error) {
            // Never repeat a pull after an uncertain response.
            if (!/timeout|timed out|disconnect|result is unknown/i.test(String(error.message))) throw error;
            const current = await this.readDownload();
            if (current.id !== id || current.state === 'failed') throw error;
          }
        }
        const deadline = Date.now() + this.transferTimeoutMs;
        while (!this.cancelled && Date.now() < deadline) {
          let current;
          for (let attempt = 0; attempt < 3; attempt++) {
            try { current = await this.readDownload(); break; }
            catch (error) { if (attempt === 2 || !/timeout|timed out|disconnect|result is unknown/i.test(String(error.message))) throw error; await sleep(2000); }
          }
          this.progress(current.status);
          if (current.id !== id) throw Error('Target download does not match the selected package');
          if (current.state === 'failed') throw Error('Target reported a failed download');
          if (current.state === 'ready') { this.journal({phase: 'ready', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, before, bodyHash: this.bodyHash}); return; }
          await sleep(this.pollMs);
        }
        throw Error(this.cancelled ? 'Stopped; target download was not cancelled' : 'Transfer timed out; target download was not cancelled');
      } finally {
        if (this.serving) { try { await this.link.stopServing(); this.serving = false; } catch (error) { this.progress('Could not detach the package: ' + error.message); } }
      }
    }
    async install() {
      const id = checks.hex(this.info.merkleRoot), before = await this.inspect({allowExisting:true}), current = await this.readDownload();
      if (before.download !== 'ready' || current.id !== id || current.state !== 'ready') throw Error('The selected package is not ready to install');
      this.journal({phase: 'install attempted', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, bodyHash: this.bodyHash});
      try { await this.remote('ota installhash ' + this.installHash); } catch (error) {
        if (!/timeout|timed out|disconnect|result is unknown/i.test(String(error.message))) throw error;
        this.progress('Install reply was lost; checking the running firmware');
      }
      const deadline = Date.now() + 300000;
      while (Date.now() < deadline && !this.cancelled) {
        await sleep(this.pollMs);
        try {
          // The target reboot loses its learned return path. Reset and prove
          // once, rather than using a cached session or sending two logins.
          if (typeof this.link.resetRemoteSession === 'function') await this.link.resetRemoteSession(this.key, this.password);
          else await this.link.verifyPeer(this.key, this.password);
          const d = checks.destination(await this.remote('ota status'), await this.remote('ota self'), await this.remote('ver'));
          if (d.targetId === this.info.targetId && d.hwId === this.info.hwId && d.version === this.info.fwVersion && d.baseHash === this.bodyHash) {
            this.journal({phase: 'installed', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, bodyHash: this.bodyHash}); return d;
          }
        } catch (_) { /* Reboot and reconnect can take time. */ }
      }
      throw Error('Could not confirm the installed firmware; inspect the target before retrying');
    }
    stop() { this.cancelled = true; }
  }
  return { DirectSession, temp, download, policy, expectedBody, installHash };
});
