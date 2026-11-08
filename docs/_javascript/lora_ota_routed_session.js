/* Explicitly preconfigured, ACL-authenticated multi-hop LoRa OTA. */
(function (root, factory) {
  const direct = typeof module === 'object' && module.exports ? require('./lora_ota_session.js') : root.LoraOtaSession;
  const checks = typeof module === 'object' && module.exports ? require('./lora_ota_checks.js') : root.LoraOtaChecks;
  const api = factory(direct, checks);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.LoraOtaRoutedSession = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (direct, checks) {
  'use strict';
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  function value(reply) {
    const result = String(reply).trim().replace(/^>\s*/, '');
    if (!result || /^(?:err(?:or)?\b|unknown\b|unsupported\b)/i.test(result)) throw Error(result || 'Empty response');
    return result;
  }
  function noSecondary(reply) {
    return /^off(?:\s|;|$)/i.test(value(reply));
  }
  function routedPolicy(reply, requiredHops) {
    const config = value(reply), matches = [...config.matchAll(/\bhops=(\d+)\b/gi)];
    if (matches.length !== 1 || Number(matches[0][1]) < requiredHops || Number(matches[0][1]) > 8)
      throw Error('OTA hops must cover every relay and be at most eight');
    if (!/\bmode=seeder-only\b/i.test(config)) {
      for (const name of ['autofetch', 'autoinstall']) {
        const values = [...config.matchAll(new RegExp('\\b' + name + '=([a-z]+)\\b', 'gi'))];
        if (values.length !== 1 || values[0][1].toLowerCase() !== 'off')
          throw Error('Turn automatic OTA fetch and install off on every participant');
      }
    }
  }
  function sameRadio(first, second) { return first.every((v, i) => Math.abs(v - second[i]) <= 0.001); }

  class RoutedSession extends direct.DirectSession {
    constructor(link, key, password, info, options = {}) {
      if (!link || !link.aclOnly || password !== '' || !Array.isArray(link.relays) ||
          !link.relays.length || link.relays.length > 8 || typeof link.proveAll !== 'function' ||
          typeof link.resetRemoteSession !== 'function' || String(key).toLowerCase() !== link.target)
        throw Error('Multi-hop OTA requires an explicit ACL-authenticated route to the selected target');
      super(link, key, password, info, options);
      this.hops = link.relays.length;
    }
    async relay(key, command) { return value(await this.link.remoteCommand(key, '', command)); }
    async inspect(options = {}) {
      if (!this.link.opened || !this.link.supportsRoutedPath) throw Error('Open a source with routed OTA support first');
      await this.link.proveAll();
      if (await this.local('ota.peer radio') !== 'OTApeer radio=radio')
        throw Error('Select the source primary radio for routed OTA');

      const {status,self} = await checks.readReceiver(command => this.link.remoteCommand(this.key, this.password, command));
      const version = await this.remote('ver');
      const dest = checks.destination(status, self, version);
      const cryptoApi = globalThis.crypto?.subtle || (typeof require === 'function' ? require('node:crypto').webcrypto.subtle : null);
      if (!cryptoApi) throw Error('Web Crypto is unavailable');
      this.packageHash = checks.hex(new Uint8Array(await cryptoApi.digest('SHA-256', this.info.blob)));
      this.installHash = await direct.installHash(this.info.blob);
      const prior = this.readJournal();
      if (options.allowExisting && prior?.phase === 'install attempted' &&
          prior.target?.toLowerCase() === this.key.toLowerCase() &&
          prior.source?.toLowerCase() === this.link.sourceKey?.toLowerCase() &&
          prior.id === checks.hex(this.info.merkleRoot) && prior.packageHash === this.packageHash &&
          dest.targetId === this.info.targetId && dest.hwId === this.info.hwId && dest.version === this.info.fwVersion) {
        this.bodyHash = await direct.expectedBody(this.info, this.image);
        if (dest.baseHash === this.bodyHash && prior.bodyHash === this.bodyHash) {
          this.journal({phase:'installed', target:this.key, source:this.link.sourceKey,
            id:prior.id, packageHash:this.packageHash, bodyHash:this.bodyHash});
          return {...dest, download:'installed'};
        }
      }
      checks.validate(this.info, dest, this.signerVerified);
      checks.validateInternalPlan(this.inplacePlan, this.info, status, self, dest,
        this.inplacePlan?.storage === 'qspi' ? await this.remote('ota qspi') : null);
      if (!this.info.isFull && this.info.codecId === 1)
        await checks.ensureSequentialTarget(status, self, () => this.link.remoteCommand(this.key, this.password, 'get bootloader.ver'));
      if (this.info.signed) {
        const trusted = await this.remote('ota key list');
        if (!trusted.toLowerCase().split(/\s+/).includes(checks.hex(this.info.signer.subarray(0, 8))))
          throw Error('The target does not list the supplied trusted signer; add it on the target first');
      }
      this.bodyHash = await direct.expectedBody(this.info, this.image);

      const participants = [
        {label:'source', command:c => this.local(c)},
        ...this.link.relays.map((key, i) => ({label:'relay ' + (i + 1), relay:true, command:c => this.relay(key, c)})),
        {label:'target', command:c => this.remote(c)}
      ];
      let shared = null;
      for (const {label, relay, command} of participants) {
        const primary = direct.temp(await command('get radio'));
        const active = direct.temp(await command('get tempradio'));
        if (!sameRadio(primary, active)) throw Error(label + ' temporary radio must match its saved primary radio');
        if (shared && !sameRadio(primary, shared)) throw Error('Source, relays and target must use the same radio settings');
        shared = primary;
        if (!noSecondary(await command('get radio2')) || !noSecondary(await command('get tempradio2')) ||
            !noSecondary(await command('get radio2.cross')))
          throw Error(label + ' must have radio2, temporary radio2 and radio2.cross off');
        routedPolicy(await command('ota config'), this.hops);
        if (relay) {
          if (!/^on$/i.test(await command('get repeat'))) throw Error(label + ' must have repeating enabled');
          if (!/\bfetch idle\b/i.test(await command('ota stats'))) throw Error(label + ' already has an OTA download');
        }
      }
      this.tempRadio = shared;
      await this.requireInstallHash();
      const state = await this.readDownload();
      if (state.state !== 'idle' && (!options.allowExisting || state.id !== checks.hex(this.info.merkleRoot) || state.state === 'failed'))
        throw Error('Target already has another or failed OTA download; resolve it first');
      if (state.state !== 'idle') {
        const record = this.readJournal();
        if (!record || record.target?.toLowerCase() !== this.key.toLowerCase() ||
            record.source?.toLowerCase() !== this.link.sourceKey?.toLowerCase() || record.id !== state.id ||
            record.packageHash !== this.packageHash || !['pull attempted', 'ready'].includes(record.phase))
          throw Error('The existing download has no matching browser recovery record; resolve it before proceeding');
      }
      if (!/\bfetch idle\b/i.test(await this.local('ota stats'))) throw Error('The source already has an OTA download');
      if (!/^folder=off\b/i.test(await this.local('ota folder'))) throw Error('The source already has an attached OTA folder');
      return {...dest, download:state.state};
    }
    async install() {
      // Recheck the entire route and the exact ready package immediately before
      // arming the target; no radio or OTA settings are changed by this session.
      const selected = checks.hex(this.info.merkleRoot);
      const before = await this.inspect({allowExisting:true});
      const current = await this.readDownload();
      if (before.download !== 'ready' || current.id !== selected || current.state !== 'ready')
        throw Error('The selected package is not ready to install');
      this.journal({phase:'install attempted', target:this.key, source:this.link.sourceKey, id:selected, packageHash:this.packageHash, bodyHash:this.bodyHash});
      const command = this.installMode === 'hash' ? 'ota installhash ' + this.installHash : this.installMode === 'legacy' ? 'ota install' : null;
      if (!command) throw Error('Target installation capability is unknown');
      try { await this.remote(command); }
      catch (error) {
        if (!/timeout|timed out|disconnect|result is unknown/i.test(String(error.message))) throw error;
        this.progress('Install reply was lost; checking the running firmware');
      }
      const deadline = Date.now() + 300000;
      while (Date.now() < deadline && !this.cancelled) {
        await sleep(this.pollMs);
        try {
          // A reboot loses the target's learned return path. Cached ACL proof
          // alone cannot establish that the new firmware is responding.
          await this.link.resetRemoteSession(this.key);
          const after = checks.destination(await this.remote('ota status'), await this.remote('ota self'), await this.remote('ver'));
          if (after.targetId === this.info.targetId && after.hwId === this.info.hwId &&
              after.version === this.info.fwVersion && after.baseHash === this.bodyHash) {
            this.journal({phase:'installed', target:this.key, source:this.link.sourceKey, id:selected, packageHash:this.packageHash, bodyHash:this.bodyHash}); return after;
          }
        } catch (_) { /* Reboot and route reacquisition can take time. */ }
      }
      throw Error('Could not confirm the installed firmware; inspect the target before retrying');
    }
  }
  return {RoutedSession, routedPolicy, sameRadio, noSecondary};
});
