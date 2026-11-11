/* Explicit ACL-authenticated routes for browser LoRa OTA control. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.LoraOtaRoute = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const validKey = value => {
    if (typeof value !== 'string' || !/^[0-9a-f]{64}$/i.test(value)) throw Error('Full 64-character public keys are required');
    return value.toLowerCase();
  };
  function relayRoute(target, relays, prefixBytes = 3) {
    target = validKey(target);
    if (!Array.isArray(relays) || relays.length < 1 || relays.length > 8) throw Error('Choose one to eight ordered relay public keys');
    if (![1, 2, 3].includes(prefixBytes)) throw Error('Route prefixes must be one, two or three bytes');
    relays = relays.map(validKey);
    const keys = [...relays, target], prefixes = keys.map(k => k.slice(0, prefixBytes * 2).toUpperCase());
    if (new Set(keys).size !== keys.length || new Set(prefixes).size !== prefixes.length)
      throw Error('Relays and destination need distinct public keys and route prefixes');
    const paths = new Map(keys.map((k, n) => [k, prefixes.slice(0, n)]));
    return {target, relays, prefixBytes, paths};
  }
  class RoutedRepeaterTransport {
    constructor(link, target, relays, prefixBytes = 3) {
      if (!link || !link.aclOnly) throw Error('Routed updates require existing ACL access on the USB repeater');
      const route = relayRoute(target, relays, prefixBytes);
      if (link.knownRepeater !== route.target) throw Error('Source must be bound to the selected target key');
      Object.assign(this, route); this.link = link; this.proved = new Set();
      this.selected = []; this.pathOwned = false; this.opened = false; this.closing = false;
      this.aclOnly = true; this.supportsRadio2 = true;
    }
    get sourceKey() { return this.link.sourceKey; }
    get supportsRoutedPath() { return this.link.supportsRoutedPath; }
    async open() {
      if (this.closing || this.closeTask) throw Error('Create a new routed connection after closing');
      if (this.opened) return this;
      try {
        if (!this.link.running) await this.link.open();
        if (!this.link.supportsRoutedPath) throw Error('Source firmware needs routed-path OTA support');
        if (!this.link.sourceKey || [...this.paths.keys()].some(k => k.slice(0, this.prefixBytes * 2) === this.link.sourceKey.slice(0, this.prefixBytes * 2)))
          throw Error('Source and route keys need distinct public-key prefixes');
        if (await this.link.localCommand('ota.peer path') !== 'OTApeer path=off')
          throw Error('Clear the existing OTA path before starting a routed update');
        this.opened = true; this.closing = false; return this;
      } catch (error) { await this.link.close(); throw error; }
    }
    allowed(publicKey, password) {
      const k = validKey(publicKey);
      if (!this.paths.has(k)) throw Error('Destination is outside the selected relay route');
      if (password !== '') throw Error('Routed OTA control requires existing ACL access');
      return k;
    }
    expected(path) { return 'OTApeer path=' + (path.length ? path.join(',') : 'off'); }
    assertOpen() {
      if (!this.opened || this.closing) throw Error('The routed connection is closing or disconnected');
    }
    async assertCurrent() {
      if (await this.link.localCommand('ota.peer path') !== this.expected(this.selected))
        throw Error('The OTA path changed; it was not overwritten');
    }
    async select(k) {
      const path = this.paths.get(k);
      // Never act on our cached path until the device confirms it. Another
      // administrator may have changed it since the previous transaction.
      await this.assertCurrent();
      if (path.join(',') === this.selected.join(',')) return;
      // Record ownership before issuing a command: a lost reply can still mean
      // the device changed state. Read back before doing anything else.
      this.selected = path.slice(); this.pathOwned = true;
      let reply;
      try { reply = await this.link.localCommand('ota.peer path ' + (path.length ? path.join(',') : 'off')); }
      catch (_) { reply = await this.link.localCommand('ota.peer path'); }
      if (reply !== this.expected(path)) throw Error('Could not confirm the selected relay path');
    }
    async prove(k) {
      if (!this.proved.has(k)) { await this.link.job('ota.peer acl ' + k); this.proved.add(k); }
    }
    async verifyPeer(publicKey, password = '') {
      const k = this.allowed(publicKey, password);
      this.assertOpen();
      return this.link.withAdmin(async () => {
        this.assertOpen(); await this.link.checkClock(); await this.select(k); await this.prove(k); return k;
      });
    }
    async proveAll() {
      for (const k of [...this.relays, this.target]) await this.verifyPeer(k, '');
    }
    async remoteCommand(publicKey, password, command) {
      const k = this.allowed(publicKey, password);
      if (typeof command !== 'string' || !command || /[\x00-\x1f\x7f]/.test(command) || new TextEncoder().encode(command).length > 154)
        throw Error('Command must be one nonempty line of at most 154 bytes');
      this.assertOpen();
      return this.link.withAdmin(async () => {
        this.assertOpen();
        await this.link.checkClock(); await this.select(k); await this.prove(k);
        await this.assertCurrent();
        return this.link.job('ota.peer cmd.acl ' + k + ' ' + command);
      });
    }
    localCommand(command) { return this.link.localCommand(command); }
    serve(...args) { return this.link.serve(...args); }
    stopServing() { return this.link.stopServing(); }
    async resetRemoteSession(publicKey) {
      const k = this.allowed(publicKey, '');
      this.assertOpen();
      return this.link.withAdmin(async () => {
        this.assertOpen();
        if (!this.link.ownsPeer) throw Error('This updater does not own the routed OTA peer session');
        await this.select(k);
        if (await this.link.localCommand('ota.peer close') !== 'OTApeer closed') throw Error('Could not reset the routed session');
        this.proved.delete(k); await this.link.checkClock(); await this.prove(k);
      });
    }
    async close() {
      if (this.closeTask) return this.closeTask;
      this.closing = true;
      // The native path is shared by all admin jobs. Finish the in-flight
      // exchange before changing it or closing the underlying transport.
      this.closeTask = (async () => {
        let failure = null;
        try {
          await this.link.withAdmin(async () => {
            if (this.pathOwned && this.link.running && !this.link.failure) {
              const current = await this.link.localCommand('ota.peer path');
              if (current !== this.expected(this.selected)) throw Error('The OTA path changed; it was not overwritten');
              if (current !== 'OTApeer path=off' && await this.link.localCommand('ota.peer path off') !== 'OTApeer path=off')
                throw Error('Could not restore the source OTA path');
            } else if (this.pathOwned && (!this.link.running || this.link.failure)) {
              throw Error('The source disconnected; its OTA path may need to be cleared manually');
            }
          });
        } catch (error) { failure = error; }
        // USB cleanup acquires this same queue to close the peer session and
        // reset its OTA radio. Release our path transaction first; otherwise
        // close waits forever for the transaction that is waiting for close.
        try { await this.link.close(); } catch (error) { failure ||= error; }
        this.opened = false; this.proved.clear();
        if (failure) throw failure;
      })();
      return this.closeTask;
    }
  }
  return {relayRoute, RoutedRepeaterTransport};
});
