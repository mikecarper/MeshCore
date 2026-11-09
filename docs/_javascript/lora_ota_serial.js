/* Browser-side USB repeater transport. One reader handles CLI and folder frames. */
(function (root, factory) {
  const node = typeof module === 'object' && module.exports;
  const api = factory(node ? require('./lora_ota_port.js') : root.LoraOtaPort);
  if (node) module.exports = api;
  if (root) root.LoraOtaSerial = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (portHelper) {
  'use strict';
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('').toUpperCase();
  const equal = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
  const u16 = (v, n) => v.getUint16(n, true);
  const u32 = (v, n) => v.getUint32(n, true);
  const view = b => new DataView(b.buffer, b.byteOffset, b.byteLength);
  const key = value => {
    if (!/^[0-9a-f]{64}$/i.test(value || '')) throw new Error('A complete 64-character public key is required');
    return value.toLowerCase();
  };
  function commandBytes(value, limit) {
    if (typeof value !== 'string' || !value || /[\x00-\x1f\x7f]/.test(value)) throw new Error('Command must be one nonempty line');
    const bytes = encoder.encode(value);
    if (bytes.length > limit) throw new Error('Command is too long');
    return bytes;
  }
  function responseFrame(op, status, payload = new Uint8Array()) {
    const result = new Uint8Array(5 + payload.length);
    result.set([0x6d, 0x73, op, status]); result.set(payload, 4);
    result[result.length - 1] = result.subarray(0, -1).reduce((a, b) => a ^ b, 0);
    return result;
  }
  async function rawDeflate(bytes) {
    if (typeof CompressionStream !== 'function' || typeof Response !== 'function')
      throw new Error('Raw compression is unavailable');
    const stream = new CompressionStream('deflate-raw');
    const output = new Response(stream.readable).arrayBuffer();
    const writer = stream.writable.getWriter();
    await writer.write(bytes); await writer.close();
    return new Uint8Array(await output);
  }
  class StreamDecoder {
    constructor(timeout = 2000) { this.timeout = timeout; this.buffer = []; this.line = []; this.pending = null; }
    feed(data, now = Date.now()) {
      const events = [];
      if (this.pending !== null && now - this.pending > this.timeout) {
        this.buffer = []; this.line = []; this.pending = null;
      }
      this.buffer.push(...data);
      const headers = {1:0, 2:1, 3:7, 4:4, 5:8, 6:10, 7:10, 8:4, 9:7};
      while (this.buffer.length) {
        if (this.buffer[0] === 77 && this.buffer.length < 3) { this.pending ??= now; break; }
        if (this.buffer[0] === 77 && this.buffer[1] === 83 && this.buffer[2] in headers) {
          const op = this.buffer[2]; let needed = 4 + headers[op];
          if (this.buffer.length < needed) { this.pending ??= now; break; }
          if (op === 6) {
            const length = this.buffer[11] | (this.buffer[12] << 8);
            if (length > 512) { this.buffer.splice(0, 3); this.line = []; this.pending = null; continue; }
            needed += length;
            if (this.buffer.length < needed) { this.pending ??= now; break; }
          }
          const frame = this.buffer.splice(0, needed);
          this.line = []; this.pending = null;
          if (frame.slice(2).reduce((a, b) => a ^ b, 0) === 0)
            events.push({type:'request', op, args:Uint8Array.from(frame.slice(3, -1))});
          continue;
        }
        const byte = this.buffer.shift(); this.pending = null;
        if (byte === 10) {
          const line = decoder.decode(Uint8Array.from(this.line)).replace(/[\r\n]+$/, ''); this.line = [];
          if (line) events.push({type:'line', line});
        } else { this.line.push(byte); if (this.line.length > 512) this.line = []; }
      }
      return events;
    }
  }
  class SeederData {
    constructor(info, blob, compress = null) {
      if (!(blob instanceof Uint8Array)) blob = new Uint8Array(blob);
      if (!info || !(info.merkleRoot instanceof Uint8Array) || info.merkleRoot.length !== 4 ||
          !Number.isInteger(info.blockSize) || info.blockSize < 1 || info.blockSize > 2048 || (info.blockSize & (info.blockSize - 1)) ||
          !Number.isInteger(info.payloadOffset) || !Number.isInteger(info.payloadSize) || info.payloadOffset < 0 ||
          info.payloadSize < 0 || info.payloadOffset + info.payloadSize > blob.length)
        throw new Error('Invalid verified mOTA package');
      this.info = info; this.blob = blob; this.compress = compress; this.compressed = new Map();
    }
    async request(op, args) {
      const i = this.info, v = view(args), blocks = Math.ceil(i.payloadSize / i.blockSize);
      if (op === 1 && !args.length) return [0, Uint8Array.of(1)];
      if (op === 2 && equal(args, Uint8Array.of(0))) {
        const bytes = new Uint8Array(38), d = view(bytes); bytes.set(i.merkleRoot);
        d.setUint32(4, i.targetId, true); d.setUint32(8, i.fwVersion, true);
        bytes[12] = i.codecId; bytes[13] = i.flags;
        [this.blob.length, 205, blocks, i.payloadOffset, i.payloadSize].forEach((x, j) => d.setUint32(14 + j * 4, x, true));
        bytes[34] = Math.log2(i.blockSize); bytes[35] = 1;
        return [0, bytes];
      }
      if (op === 3 && args.length === 7) {
        const index = args[0], offset = u32(v, 1), length = u16(v, 5);
        if (!index && length > 0 && length <= 192 && offset + length <= this.blob.length)
          return [0, this.blob.slice(offset, offset + length)];
      }
      if (op === 9 && args.length === 7 && this.compress) {
        const index = args[0], block = u16(v, 1), offset = u16(v, 3), length = u16(v, 5);
        if (index || block >= blocks || length > 190 || (!length && offset)) return [1, new Uint8Array()];
        if (!this.compressed.has(block)) {
          const start = i.payloadOffset + block * i.blockSize;
          const raw = this.blob.slice(start, Math.min(start + i.blockSize, i.payloadOffset + i.payloadSize));
          try {
            const encoded = new Uint8Array(await this.compress(raw));
            this.compressed.set(block, encoded.length < raw.length ? encoded : null);
          } catch (_) { this.compressed.set(block, null); }
        }
        const encoded = this.compressed.get(block);
        if (encoded && offset + length <= encoded.length) {
          const result = new Uint8Array(2 + length); view(result).setUint16(0, encoded.length, true);
          result.set(encoded.subarray(offset, offset + length), 2); return [0, result];
        }
      }
      return [1, new Uint8Array()];
    }
  }
  class RepeaterTransport {
    constructor(port, options = {}) {
      this.port = port; this.options = options; this.portOpened = false; this.commandTimeout = options.commandTimeout ?? 12000;
      this.remoteTimeout = options.remoteTimeout ?? 40000; this.pollMs = options.pollMs ?? 250;
      this.decoder = new StreamDecoder(); this.reader = null; this.writer = null; this.running = false;
      this.failure = null; this.waiter = null; this.writeQueue = Promise.resolve(); this.commandQueue = Promise.resolve();
      this.adminQueue = Promise.resolve(); this.sourceKey = null; this.seeder = null; this.serving = false;
      this.folderUnknown = false;
      this.ownsPeer = false;
      this.authenticatedKey = null; this.lastPeerUse = 0; this.aclOnly = !!options.aclOnly;
      this.knownRepeater = options.knownRepeater ? key(options.knownRepeater) : null;
      if (this.aclOnly && !this.knownRepeater) throw new Error('ACL access requires a complete known repeater key');
      this.supportsRoutedPath = false; this.clockChecked = false;
      this.otaRadioProfile=0;this.otaTargetProfile=0;this.supportsOtaRadio2=false;
    }
    async open() {
      try {
        await portHelper.openSerialPort(this.port, {signal:this.options.openSignal, onAttempt:this.options.onOpenAttempt, onSuccess:this.options.onOpenSuccess, onAcquired:()=>{this.portOpened=true;}, onReleased:()=>{this.portOpened=false;}, delayMs:this.options.openRetryDelayMs});
        this.portOpened = true;
        if (this.options.openSignal?.aborted) throw new DOMException('Connection canceled', 'AbortError');
        this.reader = this.port.readable.getReader(); this.writer = this.port.writable.getWriter(); this.running = true;
        this.readTask = this.readLoop();
        await sleep(this.options.openSettle ?? 750);
        const identity = await this.localCommand('get public.key');
        const match = identity.match(/\b[0-9a-f]{64}\b/i);
        if (!match) throw new Error('The repeater did not report its complete public key');
        this.sourceKey = match[0].toLowerCase();
        const peerStatus = await this.localCommand('ota.peer status');
        if (!/^OTApeer v=1 session=[01] busy=[01] peers=\d+$/.test(peerStatus)) {
          const unsupported = /^(?:(?:err(?:or)?\s*[:\-]?\s*)?(?:unknown|unsupported)\b|err\s+use\s+ota\.peer\b)/i.test(peerStatus);
          const error = new Error(unsupported ? 'This repeater needs firmware with ota.peer support' : 'Could not verify the repeater OTA peer status');
          if (unsupported) error.sourceUpdateRequired = 'repeater';
          throw error;
        }
        if (!/^OTApeer v=1 session=0 busy=0 peers=\d+$/.test(peerStatus))
          throw new Error('The source already has an OTA peer session or job; finish it before connecting');
        if (this.knownRepeater) {
          const caps = await this.localCommand('ota.peer capabilities');
          if (!/^OTApeer known_repeater=1(?: routed_path=1)?$/.test(caps)) {
            const unsupported = /^(?:(?:err(?:or)?\s*[:\-]?\s*)?(?:unknown|unsupported)\b|err\s+use\s+ota\.peer\b|otapeer\s+known_repeater=0(?:\s|$))/i.test(caps);
            const error = new Error(unsupported ? 'The repeater needs known-repeater OTA support' : 'Could not verify the repeater OTA capabilities');
            if (unsupported) error.sourceUpdateRequired = 'repeater';
            throw error;
          }
          this.supportsRoutedPath = caps.endsWith(' routed_path=1');
          if (this.supportsRoutedPath && await this.localCommand('ota.peer path') !== 'OTApeer path=off')
            throw new Error('The repeater already has an OTA route; clear it before starting');
        } else {
          // Older firmware may not recognize the query. A recognized existing
          // route must never silently turn a direct session into a routed one.
          const path = await this.localCommand('ota.peer path');
          if (path.startsWith('OTApeer path=') && path !== 'OTApeer path=off')
            throw new Error('The repeater already has an OTA route; clear it before starting');
        }
        return this;
      } catch (error) { await this.close(); throw error; }
    }
    async write(bytes) {
      const operation = this.writeQueue.then(async () => {
        if (!this.running || !this.writer || this.failure) throw this.failure || new Error('The repeater is disconnected');
        await this.writer.write(bytes);
      });
      this.writeQueue = operation.catch(() => {}); return operation;
    }
    async readLoop() {
      try {
        while (this.running) {
          const {value, done} = await this.reader.read();
          if (done) { if (this.running) throw new Error('The repeater disconnected'); break; }
          for (const event of this.decoder.feed(value)) {
            if (event.type === 'request') {
              let answer = this.seeder ? await this.seeder.request(event.op, event.args) :
                event.op === 1 ? [0, Uint8Array.of(0)] : [1, new Uint8Array()];
              await this.write(responseFrame(event.op, answer[0], answer[1]));
            } else if (this.waiter) this.waiter(event.line);
          }
        }
      } catch (error) {
        if (this.running) { this.failure = error; if (this.waiter) this.waiter(null, error); }
      }
    }
    async localCommand(text) {
      const bytes = commandBytes(text, 240);
      const operation = this.commandQueue.then(() => new Promise((resolve, reject) => {
        if (!this.running || this.failure) return reject(this.failure || new Error('The repeater is disconnected'));
        let echoed = false, settled = false;
        const timer = setTimeout(() => {
          // CLI replies have no request ID. A late result cannot be allowed
          // to satisfy the next command on this stream.
          this.failure = new Error('Repeater command timed out; reconnect before retrying; its result is unknown');
          finish(this.failure);
        }, this.commandTimeout);
        const finish = (error, value) => {
          if (settled) return;
          settled = true; clearTimeout(timer); this.waiter = null;
          error ? reject(error) : resolve(value);
        };
        this.waiter = (line, error) => {
          if (error) return finish(error);
          const isLogin = /^ota\.peer login(?:\.repeater)? /.test(text);
          const prefix = isLogin ? text.split(' ').slice(0, 3).join(' ') + ' ' : '';
          if (line.trim() === text.trim() || (prefix && line.startsWith(prefix) && /^\*+$/.test(line.slice(prefix.length)))) { echoed = true; return; }
          const match = echoed && line.match(/^\s*->\s?(.*)$/);
          if (match) {
            const reply = match[1].trim();
            // These are source-local OTA operations. Only an explicit missing
            // command or folder feature indicates that a firmware update is
            // needed; busy, permission, transport and parse failures do not.
            if (/^ota (?:stats|config|folder(?: on)?)$/.test(text) &&
                (/^(?:(?:err(?:or)?\s*[:\-]?\s*)?(?:unknown|unsupported)\b)/i.test(reply) ||
                 /^err\s+not\s+built\s+with\s+ota_folder_serial\b/i.test(reply))) {
              const unsupported = new Error(reply); unsupported.sourceUpdateRequired = 'repeater';
              finish(unsupported); return;
            }
            finish(null, reply);
          }
        };
        const payload = new Uint8Array(bytes.length + 2); payload.set(bytes); payload.set([13, 10], bytes.length);
        this.write(payload).catch(error => finish(error));
      }));
      this.commandQueue = operation.catch(() => {}); return operation;
    }
    async job(command) {
      // The command may reach the repeater even if its acknowledgement is
      // lost. Only jobs initiated after the empty-state check belong to us.
      this.ownsPeer = true;
      const queued = await this.localCommand(command), match = queued.match(/^OTApeer job=([0-9a-f]{8}) state=pending$/i);
      if (!match) throw new Error(queued || 'Could not start OTA request');
      const job = match[1].toUpperCase(), deadline = Date.now() + this.remoteTimeout;
      let offset = 0, total = null, state = null, result = [];
      while (Date.now() < deadline) {
        const reply = await this.localCommand('ota.peer result' + (offset ? ' ' + offset : ''));
        const p = reply.match(/^OTApeer job=([0-9a-f]{8}) state=(pending|ok|error) bytes=(\d+) off=(\d+) data=([0-9a-f]+|-)$/i);
        if (!p || p[1].toUpperCase() !== job) throw new Error('OTA request changed; its result is unknown');
        if (p[2] === 'pending') { await sleep(this.pollMs); continue; }
        const size = Number(p[3]), start = Number(p[4]), encoded = p[5] === '-' ? '' : p[5];
        if (size > 160 || start !== offset || encoded.length % 2 || (total !== null && (total !== size || state !== p[2]))) throw new Error('Invalid OTA reply pages');
        const chunk = encoded.match(/../g)?.map(c => parseInt(c, 16)) || [];
        if (chunk.length > 48 || offset + chunk.length > size || (!chunk.length && offset < size)) throw new Error('Invalid OTA reply length');
        total = size; state = p[2]; result.push(...chunk); offset += chunk.length;
        if (offset === total) { const text = decoder.decode(Uint8Array.from(result)); if (state === 'error') throw new Error(text); return text; }
      }
      throw new Error('OTA request timed out; its result is unknown');
    }
    async discover() {
      return this.withAdmin(async () => {
        this.authenticatedKey = null; await this.job('ota.peer discover');
        const status = await this.localCommand('ota.peer status'), m = status.match(/\bpeers=(\d+)\b/);
        if (!m || Number(m[1]) > 8) throw new Error('Invalid OTA discovery result');
        const peers = [];
        for (let j = 0; j < Number(m[1]); j++) {
          const p = (await this.localCommand('ota.peer peers ' + j)).match(/^OTApeer peer=(\d+) key=([0-9a-f]{64}) type=([23]) rssi=(-?\d+) snr=(-?\d+)$/i);
          if (!p || Number(p[1]) !== j) throw new Error('Invalid discovered peer');
          peers.push({publicKey:p[2].toLowerCase(), type:Number(p[3]), rssi:Number(p[4]), snr:Number(p[5])});
        }
        return peers.sort((a,b) => b.rssi - a.rssi);
      });
    }
    withAdmin(fn) { const p = this.adminQueue.then(fn); this.adminQueue = p.catch(() => {}); return p; }
    async checkClock() {
      if (this.clockChecked) return;
      const parse = reply => {
        // Reject ambiguous or normalized dates before setting the clock.
        const matches = [...reply.matchAll(/\b(\d{1,2}):(\d{2})\s*-\s*(\d{1,2})\/(\d{1,2})\/(\d{4})\s+UTC\b/gi)];
        if (matches.length !== 1) throw new Error('Cannot verify source clock before authenticated OTA');
        const m = matches[0], [hour, minute, day, month, year] = m.slice(1).map(Number);
        const date = new Date(Date.UTC(year, month - 1, day, hour, minute));
        if (date.getUTCFullYear() !== year || date.getUTCMonth() + 1 !== month || date.getUTCDate() !== day ||
            date.getUTCHours() !== hour || date.getUTCMinutes() !== minute)
          throw new Error('Source clock returned an invalid UTC date');
        return Math.floor(date.getTime() / 1000);
      };
      const start = parse(await this.localCommand('clock')), now = Math.floor(Date.now()/1000);
      if (start > now + 600 || start + 59 < now)
        throw new Error('Source clock is out of sync with this computer. Check both clocks and correct the repeater clock before retrying.');
      this.clockChecked = true;
    }
    validatePeer(publicKey, password) {
      const k = key(publicKey);
      if (this.knownRepeater && k !== this.knownRepeater) throw new Error('Target differs from the selected repeater');
      if (typeof password !== 'string' || encoder.encode(password).length > 63 || /[\x00-\x1f\x7f]/.test(password) || (this.aclOnly && password)) throw new Error('Invalid admin password');
      return k;
    }
    async login(k, password) {
      if (this.aclOnly || password === '') {
        await this.job('ota.peer acl ' + k);
      } else if (!this.knownRepeater) {
        await this.job('ota.peer discover');
        const status = await this.localCommand('ota.peer status'), m = status.match(/\bpeers=(\d+)\b/);
        if (!m || Number(m[1]) > 8) throw new Error('Invalid direct discovery');
        let found = false;
        for (let j = 0; j < Number(m[1]); j++) if ((await this.localCommand('ota.peer peers ' + j)).toLowerCase().includes('key=' + k)) found = true;
        if (!found) throw new Error('Target did not answer direct discovery');
        await this.job('ota.peer login ' + k + ' ' + password);
      } else await this.job('ota.peer login.repeater ' + k + ' ' + password);
      this.authenticatedKey = k; this.lastPeerUse = Date.now();
    }
    async verifyPeer(publicKey, password = '') {
      const k = this.validatePeer(publicKey, password);
      return this.withAdmin(async () => { await this.checkClock(); await this.login(k, password); return k; });
    }
    async resetRemoteSession(publicKey, password = '') {
      const k = this.validatePeer(publicKey, password);
      return this.withAdmin(async () => {
        if (!this.ownsPeer) throw new Error('This updater does not own the OTA peer session');
        // A reboot invalidates the target's return path and the old session.
        // Clear local credentials even if the close reply is lost.
        this.authenticatedKey = null; this.lastPeerUse = 0;
        const reply = await this.localCommand('ota.peer close');
        if (reply !== 'OTApeer closed') throw new Error(reply || 'Could not reset the OTA session');
        await this.checkClock(); await this.login(k, password); return k;
      });
    }
    async closeRadioSession() {
      if(this.serving)throw Error('Stop the transfer before changing the OTA channel');
      return this.withAdmin(async()=>{
        const reply=await this.localCommand('ota.peer close');
        if(reply!=='OTApeer closed')throw Error('Could not close the source OTA session');
        this.authenticatedKey=null;this.lastPeerUse=0;this.ownsPeer=false;
      });
    }
    async probeOtaRadio() {
      const reply=await this.localCommand('ota.peer radio');
      const match=/^OTApeer radio=(radio|radio2)$/.exec(reply);
      if(match){this.otaRadioProfile=match[1]==='radio2'?1:0;this.supportsOtaRadio2=true;return true;}
      if(/^(?:unknown\b|unsupported\b|err(?:or)?\s*:?\s*(?:unknown|unsupported|use ota\.peer)\b)/i.test(reply)){this.supportsOtaRadio2=false;this.otaRadioProfile=0;return false;}
      throw Error('Cannot confirm the source OTA radio selection');
    }
    async selectOtaRadio(profile) {
      if(profile!==0&&profile!==1)throw Error('Invalid OTA radio selection');
      if(!this.supportsOtaRadio2){if(profile===0){this.otaRadioProfile=0;return;}throw Error('This source cannot select OTA radio2');}
      if(this.serving||this.authenticatedKey||this.ownsPeer)throw Error('Close the OTA session before selecting its radio');
      const name=profile?'radio2':'radio';
      if(await this.localCommand('ota.peer radio '+name)!=='OTApeer radio='+name ||
         await this.localCommand('ota.peer radio')!=='OTApeer radio='+name)
        throw Error('Source OTA radio selection could not be verified');
      this.otaRadioProfile=profile;
    }
    async remoteCommand(publicKey, password, text) {
      const k = this.validatePeer(publicKey, password); commandBytes(text, 154);
      return this.withAdmin(async () => {
        await this.checkClock();
        if (this.aclOnly || password === '') {
          // cmd.acl replaces the source's current peer session.
          this.authenticatedKey = null; this.lastPeerUse = 0;
          return this.job('ota.peer cmd.acl ' + k + ' ' + text);
        }
        if (this.authenticatedKey !== k || Date.now() - this.lastPeerUse > 240000) await this.login(k, password);
        const reply = await this.job('ota.peer cmd ' + k + ' ' + text); this.lastPeerUse = Date.now(); return reply;
      });
    }
    async setPath(prefixes) {
      if (!this.supportsRoutedPath) throw new Error('Source firmware has no routed-path support');
      if (!Array.isArray(prefixes) || prefixes.length > 8 || prefixes.some(p => !/^(?:[0-9a-f]{2}){1,3}$/i.test(p))) throw new Error('Invalid route');
      if (new Set(prefixes.map(p => p.length)).size > 1 || new Set(prefixes.map(p => p.toUpperCase())).size !== prefixes.length)
        throw new Error('Route prefixes must have equal widths and identify different repeaters');
      const value = prefixes.length ? prefixes.map(p => p.toUpperCase()).join(',') : 'off';
      const reply = await this.localCommand('ota.peer path ' + value);
      if (reply !== 'OTApeer path=' + value) throw new Error('Route change was not confirmed');
    }
    async serve(info, blob, compress = rawDeflate) {
      if (this.serving || this.seeder || this.folderUnknown) throw new Error('A package is already being served or folder ownership is unresolved');
      if (!/^folder=off\b/.test(await this.localCommand('ota folder')))
        throw new Error('The source already has an attached OTA folder; it was not changed');
      this.seeder = new SeederData(info, blob, compress);
      try {
        const reply = await this.localCommand('ota folder on');
        if (!reply.startsWith('OK folder attached')) throw new Error(reply || 'Could not attach OTA folder');
        this.serving = true;
      } catch (error) {
        // There is no server-side owner token. An unacknowledged command can
        // have succeeded, but an on state could also belong to another admin.
        // Only clear locally when readback proves it is still off.
        let state = '';
        try { state = await this.localCommand('ota folder'); } catch (_) {}
        if (/^folder=off\b/.test(state)) this.seeder = null;
        else {
          this.folderUnknown = true;
          throw new Error('OTA folder attachment is unconfirmed; inspect the source and detach it manually if appropriate');
        }
        throw error;
      }
    }
    async stopServing() {
      if (!this.serving) return;
      try {
        const reply = await this.localCommand('ota folder off');
        if (!/^OK/.test(reply)) throw new Error(reply || 'Could not detach OTA folder');
        this.serving = false; this.seeder = null;
      } catch (error) {
        let state = '';
        try { state = await this.localCommand('ota folder'); } catch (_) {}
        if (/^folder=off\b/.test(state)) { this.serving = false; this.seeder = null; return; }
        // Never automatically retry a mutation after losing its acknowledgement.
        this.serving = false; this.folderUnknown = true;
        throw new Error('OTA folder detach is unconfirmed; inspect the source before changing its folder');
      }
    }
    async close() {
      if (this.running && !this.failure) {
        if (this.serving) { try { await this.stopServing(); } catch (_) {} }
        if (this.ownsPeer || this.supportsOtaRadio2 && this.otaRadioProfile!==0) {
          try {
            await this.closeRadioSession();
            if(this.supportsOtaRadio2 && this.otaRadioProfile!==0)await this.selectOtaRadio(0);
          } catch (_) {}
        }
      }
      this.running = false;
      if (this.waiter) this.waiter(null, new Error('Connection closed'));
      if (this.reader) { try { await this.reader.cancel(); } catch (_) {} }
      if (this.readTask) { try { await this.readTask; } catch (_) {} }
      if (this.reader) { try { this.reader.releaseLock(); } catch (_) {} }
      if (this.writer) { try { await this.writer.close(); } catch (_) {} try { this.writer.releaseLock(); } catch (_) {} }
      this.reader = this.writer = null;
      if (this.portOpened) { try { await this.port.close(); this.portOpened = false; } catch (_) {} }
      this.seeder = null; this.serving = false; this.sourceKey = null; this.authenticatedKey = null;
      this.ownsPeer = false;
    }
  }
  return {StreamDecoder, SeederData, RepeaterTransport, responseFrame, rawDeflate, hex};
});
