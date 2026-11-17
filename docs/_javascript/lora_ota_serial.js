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
  // Preserve transport errors/messages used by retry logic; add only bounded,
  // non-secret diagnostic labels rather than arbitrary CLI text or arguments.
  function errorContext(error,fields){
    if(error&&(typeof error==='object'||typeof error==='function'))for(const [name,value] of Object.entries(fields)){
      if(value!==undefined&&error[name]===undefined)try{error[name]=value;}catch(_){}
    }
    return error;
  }
  const SAFE_READS=new Set(['get public.key','clock','ver','board','ota status','ota self','ota ram','ota config','ota key list','ota stats','ota ls','ota qspi','ota installhash','ota bootloader status',
    'get bootloader.ver','get radio','get tempradio','get radio.rxps','get radio2','get tempradio2','get radio2.cross','get radio2.status','get radioat','get tempradioat','get radioat2','get tempradioat2',
    'get repeat','get radio.flood','get radio2.flood',
    'ota.peer status','ota.peer capabilities','ota.peer path','ota.peer timeout','ota.peer radio','ota folder']);
  function safeCommand(text){
    if(typeof text!=='string')return 'CLI command <arguments hidden>';
    if(SAFE_READS.has(text)||/^ota ls (?:[1-9]|[1-9][0-9]|1[01][0-9]|12[0-8])$/.test(text))return text;
    if(/^ota\.peer (?:result|peers|login(?:\.repeater)?|cmd(?:\.acl)?|acl|path|timeout|radio)\b/.test(text))return text.split(' ').slice(0,2).join(' ')+' <arguments hidden>';
    if(/^ota (?:pull|installhash|config|key)\b/.test(text))return text.split(' ').slice(0,2).join(' ')+' <arguments hidden>';
    const setting=/^set (tempradio2|radio2(?:\.cross)?|radio\.rxps)(?: |$)/.exec(text);
    if(setting)return 'set '+setting[1]+' <arguments hidden>';
    if(/^(?:set|login|tempradio)\b/.test(text))return text.split(' ')[0]+' <arguments hidden>';
    return 'CLI command <arguments hidden>';
  }
  function peerContext(command,timeoutMs){
    const match=/^ota\.peer cmd(?:\.acl)? [a-f0-9]{64} (.+)$/i.exec(command);
    const text=match?.[1],login=/^ota\.peer (?:login(?:\.repeater)?|acl) /.test(command);
    return {otaStep:text==='get public.key'?'Verifying target identity':login?'Logging in to target':'Waiting for remote command reply',
      otaCommand:text?safeCommand(text):login?'login <arguments hidden>':command==='ota.peer discover'?'discovery':'OTA peer request',otaEndpoint:'remote target',otaTimeoutMs:timeoutMs};
  }
  function remoteError(error,fields){
    if(error?.otaStep!==undefined&&(error.otaCommand!==fields.otaCommand||error.otaEndpoint!==fields.otaEndpoint||error.otaStep!==fields.otaStep))
      errorContext(error,{otaRequestCommand:fields.otaCommand,otaRequestStep:fields.otaStep,otaRequestRadio:fields.otaRadio});
    return errorContext(error,fields);
  }
  function mailboxError(error,timeoutMs){return errorContext(error,{otaStep:'Reading source reply mailbox',otaCommand:'ota.peer result',otaEndpoint:'USB Repeater',otaTimeoutMs:timeoutMs,otaRadio:'USB/local command (no LoRa transmission)'});}
  function requestRadio(link){
    const source=link.supportsOtaRadio2&&[0,1].includes(link.otaRadioProfile)?(link.otaRadioProfile===1?'Radio 2':'primary radio')+' (selected OTA profile)':'LoRa profile not confirmed';
    return 'Source: '+source+'; target: profile not confirmed';
  }
  function reportedPath(reply,request){
    const match=/^OTApeer path=(off|[a-f0-9,]+)$/i.exec(reply);
    if(!match)return null;
    const value=match[1];
    if(request!=='ota.peer path'&&value.toLowerCase()!==request.slice('ota.peer path '.length).toLowerCase())return null;
    if(value.toLowerCase()==='off')return 'direct (0 hops; configured)';
    const prefixes=value.split(',');
    if(prefixes.length>8||prefixes.some(prefix=>!/^(?:[a-f0-9]{2}){1,3}$/i.test(prefix))||new Set(prefixes.map(prefix=>prefix.length)).size!==1||new Set(prefixes.map(prefix=>prefix.toUpperCase())).size!==prefixes.length)return null;
    return 'routed ('+prefixes.length+' hop'+(prefixes.length===1?'':'s')+'; configured)';
  }
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
          } catch (error) {
            if(this.compress.strict)throw Error('Zopfli transfer compression failed: '+String(error?.message||error));
            this.compressed.set(block, null);
          }
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
  // Older Full Companions already expose the host-folder byte stream. It is
  // independent of their 176-byte app commands: the firmware reads bounded
  // file slices here and builds the unchanged OTA radio packets itself.
  class CompanionFolderSource {
    constructor(write, info, blob, options = {}) {
      this.write = write; this.options = options;
      this.seeder = new SeederData(info, blob, options.compress);
      this.decoder = new StreamDecoder(); this.mode = 'binary';
      this.responses = Promise.resolve(); this.transition = null;
      this.failure = null; this.startedAt = 0; this.lastReadAt = 0; this.firstStartedAt = null; this.bootstrapNotified = false;
      this.waitVersion = 0; this.wakeWait = null;
      this.handoffDrain = null; this.binaryBarrierPending = false;
    }
    ownsStream() { return this.mode !== 'binary'; }
    blocksAppCommands() { return this.ownsStream() || this.binaryBarrierPending; }
    fail(error) {
      this.failure = error;
      this.interruptWait();
      if (this.transition) this.transition.finish(error);
      this.options.onFailure?.(error);
    }
    feed(bytes) {
      for (const event of this.decoder.feed(bytes)) {
        if (event.type === 'request') {
          // USB is ordered: requests preceding the off barrier still have a
          // retained response boundary in firmware. Finish those replies even
          // while stopping; never accept a new request after that barrier.
          if (this.mode === 'binary') continue;
          const run = this.responses.then(async () => {
            if (this.failure) throw this.failure;
            const [status, payload] = await this.seeder.request(event.op, event.args);
            await this.write(responseFrame(event.op, status, payload));
            if(status===0&&(event.op===3||event.op===9))this.lastReadAt=Date.now();
            this.options.onRead?.(event.op, event.args, status);
          });
          this.responses = run.catch(error => this.fail(error));
        } else if (this.transition) this.transition.line(event.line);
      }
    }
    async drainResponses() {
      for (;;) {
        const pending = this.responses;
        await pending;
        if (this.failure) throw this.failure;
        if (pending === this.responses) return;
      }
    }
    async change(start) {
      if (this.handoffDrain) await this.handoffDrain;
      const expected = start ? 'binary' : 'folder';
      if (this.failure) throw this.failure;
      if (this.mode === (start ? 'folder' : 'binary')) return;
      if (this.mode !== expected || this.transition)
        throw new Error('USB folder handoff is already in progress');
      // An MCU can receive a USB reply and issue its next read before the
      // host's write promise settles. Drain the current generation AND every
      // request chained to it before writing the protocol-switch control.
      await this.drainResponses();
      this.mode = start ? 'starting' : 'stopping';
      this.decoder = new StreamDecoder();
      return new Promise((resolve, reject) => {
        let settled = false, acknowledged = false;
        const complete = value => {
          if (settled) return;
          settled = true; clearTimeout(timer); this.transition = null; resolve(value);
        };
        const finish = (error, value) => {
          if (settled) return;
          if (error) { settled = true; clearTimeout(timer); this.transition = null; reject(error); return; }
          if (acknowledged) return;
          acknowledged = true; this.transition = null;
          // A protocol barrier is recognized synchronously by feed(), before
          // the next byte in this same USB read reaches the binary decoder.
          this.mode = start ? 'folder' : 'binary';
          if (start) {
            this.startedAt = this.lastReadAt = Date.now();
            if (this.firstStartedAt === null) this.firstStartedAt = this.startedAt;
            complete(value); return;
          }
          // Firmware quarantines an outstanding native reply across this
          // barrier. Do not let the next app command overtake its queued bytes.
          this.binaryBarrierPending = true;
          const drain = this.handoffDrain = this.drainResponses();
          drain.then(() => {
            if (settled) return;
            this.handoffDrain = null; this.binaryBarrierPending = false; complete(value);
          }, error => finish(error));
        };
        const timer = setTimeout(() => {
          const error = new Error('USB folder '+(start ? 'start' : 'stop')+' was not confirmed; reconnect the USB source');
          this.fail(error); finish(error);
        }, this.options.transitionTimeout ?? 15000);
        this.transition = {finish, line: line => {
          if (/^ERR\b|^Error\b/i.test(line)) {
            this.fail(new Error('USB folder handoff failed: '+line)); return;
          }
          if (start && /^OK folder (?:attached|refreshed)\b/.test(line)) finish(null, line);
          else if (!start && /^OK - Binary mode$/.test(line)) finish(null, line);
        }};
        this.write(encoder.encode('ota folder '+(start ? 'on' : 'off')+'\r\n'))
          .catch(error => this.fail(error));
      });
    }
    start() { return this.change(true); }
    stop() { return this.change(false); }
    interruptWait() { ++this.waitVersion; this.wakeWait?.(); }
    async waitServeWindow() {
      const version=this.waitVersion, deadline=Date.now()+(this.options.maxWaitMs??120000);
      const bootstrapEnd=(this.firstStartedAt??this.startedAt)+(this.options.bootstrapMs??0);
      if(this.mode==='folder' && Date.now()<bootstrapEnd && !this.bootstrapNotified){
        this.bootstrapNotified=true;
        try { this.options.onBootstrap?.(); } catch (_) {}
      }
      while(this.mode==='folder'&&version===this.waitVersion){
        // Legacy receivers may delay the first flooded manifest request for
        // up to 32 seconds. Do not repeatedly detach the native source before
        // that first request has had a chance to arrive. This initial window
        // is bounded by the same maximum and remains interruptible by Stop.
        const remaining=Math.min(deadline,Math.max(bootstrapEnd,Math.max(this.startedAt,this.lastReadAt)+(this.options.pollDelayMs??10000)))-Date.now();
        if(remaining<=0)return;
        await new Promise(resolve=>{
          const timer=setTimeout(()=>{this.wakeWait=null;resolve();},Math.min(remaining,250));
          this.wakeWait=()=>{clearTimeout(timer);this.wakeWait=null;resolve();};
        });
      }
      if(this.failure)throw this.failure;
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
      this.diagnosticPassword = null; this.diagnosticDeadline = null; this.diagnosticCanContinue = null; this.diagnosticReadUncertain = false;
      this.failureIdentityCache = null;
      this.knownRepeater = options.knownRepeater ? key(options.knownRepeater) : null;
      this.otaPath = null;
      this.otaPathValue = null;
      this.knownTargetSupported = false;
      if (this.aclOnly && !this.knownRepeater) throw new Error('ACL access requires a complete known repeater key');
      this.supportsRoutedPath = false; this.clockChecked = false;
      this.otaRadioProfile=0;this.otaTargetProfile=0;this.supportsOtaRadio2=false;
    }
    async open() {
      try {
        const adopted=portHelper.adoptDetectedSource(this,'repeater');
        if(!adopted)await portHelper.openSerialPort(this.port, {signal:this.options.openSignal, onAttempt:this.options.onOpenAttempt, onSuccess:this.options.onOpenSuccess, onAcquired:()=>{this.portOpened=true;}, onReleased:()=>{this.portOpened=false;}, delayMs:this.options.openRetryDelayMs});
        this.portOpened = true;
        if (this.options.openSignal?.aborted) throw new DOMException('Connection canceled', 'AbortError');
        if(!adopted){this.reader = this.port.readable.getReader(); this.writer = this.port.writable.getWriter();} this.running = true;
        this.readTask = this.readLoop();
        await sleep(this.options.openSettle ?? 750);
        const identity = await this.localCommand('get public.key');
        const match = identity.match(/\b[0-9a-f]{64}\b/i);
        if (!match) throw new Error('The repeater did not report its complete public key');
        this.sourceKey = match[0].toLowerCase();
        if(this.detectedSourceKey && this.sourceKey!==this.detectedSourceKey)
          throw new Error('The USB source identity changed during connection. Connect the intended radio again.');
        const peerStatus = await this.localCommand('ota.peer status');
        if (!/^OTApeer v=1 session=[01] busy=[01] peers=\d+$/.test(peerStatus)) {
          const unsupported = /^(?:(?:err(?:or)?\s*[:\-]?\s*)?(?:unknown|unsupported)\b|err\s+use\s+ota\.peer\b)/i.test(peerStatus);
          const error = new Error(unsupported ? 'This repeater needs firmware with ota.peer support' : 'Could not verify the repeater OTA peer status');
          if (unsupported) error.sourceUpdateRequired = 'repeater';
          throw error;
        }
        if (!/^OTApeer v=1 session=0 busy=0 peers=\d+$/.test(peerStatus))
          throw new Error('The source already has an OTA peer session or job; finish it before connecting');
        if (this.knownRepeater || this.options.deferredTarget) {
          const caps = await this.localCommand('ota.peer capabilities');
          if (!/^OTApeer known_repeater=1(?: routed_path=1)?$/.test(caps)) {
            const unsupported = /^(?:(?:err(?:or)?\s*[:\-]?\s*)?(?:unknown|unsupported)\b|err\s+use\s+ota\.peer\b|otapeer\s+known_repeater=0(?:\s|$))/i.test(caps);
            const error = new Error(unsupported ? 'The repeater needs known-repeater OTA support' : 'Could not verify the repeater OTA capabilities');
            if (unsupported) error.sourceUpdateRequired = 'repeater';
            throw error;
          }
          this.supportsRoutedPath = caps.endsWith(' routed_path=1');
          this.knownTargetSupported = true;
          if (this.supportsRoutedPath) {
            if(await this.localCommand('ota.peer path') !== 'OTApeer path=off')throw new Error('The repeater already has an OTA route; clear it before starting');
            this.otaPath='direct (0 hops; configured)';
          }
        } else {
          // Older firmware may not recognize the query. A recognized existing
          // route must never silently turn a direct session into a routed one.
          const path = await this.localCommand('ota.peer path');
          if (path.startsWith('OTApeer path=') && path !== 'OTApeer path=off')
            throw new Error('The repeater already has an OTA route; clear it before starting');
          if(path==='OTApeer path=off')this.otaPath='direct (0 hops; configured)';
        }
        return this;
      } catch (error) { await this.close(); throw error; }
    }
    selectTarget(publicKey) {
      const selected=key(publicKey);
      if(this.knownRepeater===selected)return;
      if(!this.knownTargetSupported || !this.running || this.failure || this.serving || this.ownsPeer || this.authenticatedKey || this.knownRepeater)
        throw new Error('Disconnect before choosing another target.');
      this.knownRepeater=selected;
      this.failureIdentityCache=null;
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
          const initial=this.sourceProbeBytes;this.sourceProbeBytes=null;
          const {value, done} = initial?.length ? {value:initial,done:false} : await this.reader.read();
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
      const pathRequest=/^ota\.peer path(?: |$)/.test(text);
      // The routed wrapper configures this through localCommand directly.
      // Never keep an old direct/routed claim across an unconfirmed request.
      if(pathRequest){this.otaPath=null;this.otaPathValue=null;}
      const localRead=SAFE_READS.has(text)||/^ota\.peer (?:result|peers)(?: |$)/.test(text);
      const radio=/^ota\.peer (?:cmd(?:\.acl)?|login(?:\.repeater)?|acl|discover)(?: |$)/.test(text)?requestRadio(this):localRead?'USB/local command (no LoRa transmission)':'USB/local command (LoRa transmission not confirmed)';
      const operation = this.commandQueue.then(() => new Promise((resolve, reject) => {
        if (!this.running || this.failure) return reject(this.failure || new Error('The repeater is disconnected'));
        if(this.diagnosticCanContinue&&!this.diagnosticCanContinue())return reject(new Error('Failure diagnostics stopped: connection or request changed'));
        const timeout=this.diagnosticDeadline===null?this.commandTimeout:Math.min(this.commandTimeout,this.diagnosticDeadline-Date.now());
        if(timeout<=0)return reject(new Error('Diagnostic identity read timed out before another USB command was sent'));
        let echoed = false, settled = false;
        const timer = setTimeout(() => {
          // CLI replies have no request ID. A late result cannot be allowed
          // to satisfy the next command on this stream.
          this.failure = new Error('Repeater command timed out; reconnect before retrying; its result is unknown');
          finish(this.failure);
        }, timeout);
        const finish = (error, value) => {
          if (settled) return;
          settled = true; clearTimeout(timer); this.waiter = null;
          if(!error&&pathRequest){
            this.otaPath=reportedPath(value,text);
            this.otaPathValue=this.otaPath?value.slice('OTApeer path='.length).toUpperCase():null;
          }
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
      })).catch(error=>{throw errorContext(error,{otaStep:/^ota\.peer result(?: |$)/.test(text)?'Reading source reply mailbox':'Waiting for USB command reply',otaCommand:safeCommand(text),otaEndpoint:'USB Repeater',otaTimeoutMs:this.commandTimeout,otaRadio:radio});});
      this.commandQueue = operation.catch(() => {}); return operation;
    }
    async job(command) {
      const radio=requestRadio(this),path=this.otaPath||'path unknown';
      try{
      // The command may reach the repeater even if its acknowledgement is
      // lost. Only jobs initiated after the empty-state check belong to us.
      this.ownsPeer = true;
      const queued = await this.localCommand(command), match = queued.match(/^OTApeer job=([0-9a-f]{8}) state=pending$/i);
      if (!match) throw new Error(queued || 'Could not start OTA request');
      const job = match[1].toUpperCase(), deadline = Math.min(Date.now() + this.remoteTimeout,this.diagnosticDeadline??Infinity);
      let offset = 0, total = null, state = null, result = [];
      while (Date.now() < deadline) {
        const reply = await this.localCommand('ota.peer result' + (offset ? ' ' + offset : ''));
        const p = reply.match(/^OTApeer job=([0-9a-f]{8}) state=(pending|ok|error) bytes=(\d+) off=(\d+) data=([0-9a-f]+|-)$/i);
        if (!p || p[1].toUpperCase() !== job) throw mailboxError(new Error('OTA request changed; its result is unknown'),this.commandTimeout);
        if (p[2] === 'pending') { await sleep(this.diagnosticDeadline===null?this.pollMs:Math.min(this.pollMs,Math.max(0,deadline-Date.now()))); continue; }
        const size = Number(p[3]), start = Number(p[4]), encoded = p[5] === '-' ? '' : p[5];
        if (size > 160 || start !== offset || encoded.length % 2 || (total !== null && (total !== size || state !== p[2]))) throw mailboxError(new Error('Invalid OTA reply pages'),this.commandTimeout);
        const chunk = encoded.match(/../g)?.map(c => parseInt(c, 16)) || [];
        if (chunk.length > 48 || offset + chunk.length > size || (!chunk.length && offset < size)) throw mailboxError(new Error('Invalid OTA reply length'),this.commandTimeout);
        total = size; state = p[2]; result.push(...chunk); offset += chunk.length;
        if (offset === total) {
          const text = decoder.decode(Uint8Array.from(result)); if (state === 'error') throw new Error(text);
          const identity=/^ota\.peer cmd(?:\.acl)? ([a-f0-9]{64}) (ver|board|ota status)$/i.exec(command);
          if(identity)this.rememberFailureIdentity(identity[1],identity[2],text);
          return text;
        }
      }
      throw new Error('OTA request timed out; its result is unknown');
      }catch(error){throw remoteError(error,{...peerContext(command,this.remoteTimeout),otaPath:path,otaRadio:radio});}
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
      for (let attempt = 0; attempt <= 5; attempt++) {
        const start = parse(await this.localCommand('clock')), now = Math.floor(Date.now()/1000);
        if (start <= now + 600 && start + 59 >= now) {
          this.clockChecked = true;
          return;
        }
        // A minute-resolution clock can still show the previous minute at
        // a boundary even with only a second or two of skew. Re-read it for
        // at most five seconds; never accept the old reading or widen the
        // clock bounds. A materially stale or future clock still fails now.
        if (start + 59 < now && now - (start + 59) <= 5 && attempt < 5) {
          await sleep(1000);
          continue;
        }
        throw new Error('Source clock is out of sync with this computer. Check both clocks and correct the repeater clock before retrying.');
      }
    }
    validatePeer(publicKey, password) {
      const k = key(publicKey);
      if (this.knownRepeater && k !== this.knownRepeater) throw new Error('Target differs from the selected repeater');
      if (typeof password !== 'string' || encoder.encode(password).length > 63 || /[\x00-\x1f\x7f]/.test(password) || (this.aclOnly && password)) throw new Error('Invalid admin password');
      return k;
    }
    async login(k, password) {
      const radio=requestRadio(this),path=this.otaPath||'path unknown';
      try{
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
      this.diagnosticPassword=password;
      }catch(error){throw errorContext(error,{otaStep:'Logging in to target',otaCommand:'login <arguments hidden>',otaEndpoint:'remote target',otaTimeoutMs:this.remoteTimeout,otaPath:path,otaRadio:radio});}
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
      const radio=requestRadio(this),path=this.otaPath||'path unknown';
      return this.withAdmin(async () => {
        await this.checkClock();
        if (this.aclOnly || password === '') {
          // cmd.acl replaces the source's current peer session.
          this.authenticatedKey = null; this.lastPeerUse = 0;
          const reply=await this.job('ota.peer cmd.acl ' + k + ' ' + text);this.rememberFailureIdentity(k,text,reply);return reply;
        }
        if (this.authenticatedKey !== k || Date.now() - this.lastPeerUse > 240000) await this.login(k, password);
        const reply = await this.job('ota.peer cmd ' + k + ' ' + text); this.lastPeerUse = Date.now();this.rememberFailureIdentity(k,text,reply);return reply;
      }).catch(error=>{throw remoteError(error,{otaStep:text==='get public.key'?'Verifying target identity':'Waiting for remote command reply',otaCommand:safeCommand(text),otaEndpoint:'remote target',otaTimeoutMs:this.remoteTimeout,otaPath:path,otaRadio:radio});});
    }
    rememberFailureIdentity(publicKey,command,reply) {
      if(!/^[a-f0-9]{64}$/i.test(publicKey||'')||!/^[a-f0-9]{64}$/i.test(this.sourceKey||'')||!['ver','board','ota status'].includes(command)||typeof reply!=='string')return;
      const value=reply.replace(/^>\s*/,'').trim();
      if(!value||value.length>160||/[^\x20-\x7e]/.test(value)||/^(?:err(?:or)?\b|unknown\b|unsupported\b)/i.test(value))return;
      if(command==='ver'&&!/\bv?\d+\.\d+(?:\.\d+)*/i.test(value))return;
      const targetKey=publicKey.toLowerCase(),previous=this.cachedFailureIdentity(targetKey);
      let field;
      if(command==='ota status'){
        const matches=[...value.matchAll(/\bhw=([A-Za-z0-9][A-Za-z0-9_.-]{0,63})(?=\s|\||$)/g)];
        if(matches.length!==1||/^(?:unknown|unset|none|na)$/i.test(matches[0][1])||previous?.boardOrigin==='board command')return;
        field={board:'hardware ID '+matches[0][1]+' (reported by ota status)',boardOrigin:'ota status hardware ID'};
      }else field=command==='ver'?{version:value}:{board:value,boardOrigin:'board command'};
      this.failureIdentityCache={sourceKey:this.sourceKey.toLowerCase(),targetKey,version:previous?.version||'',board:previous?.board||'',boardOrigin:previous?.boardOrigin||'',origin:'cached from earlier in this USB session',...field};
    }
    cachedFailureIdentity(publicKey) {
      const cached=this.failureIdentityCache;
      if(typeof publicKey!=='string'||!cached||cached.targetKey!==publicKey.toLowerCase()||typeof this.sourceKey!=='string'||cached.sourceKey!==this.sourceKey.toLowerCase())return null;
      return {...cached};
    }
    async diagnosticRead(publicKey,password,text,{timeoutMs=5000,canContinue}={}) {
      const k=this.validatePeer(publicKey,password);
      if(!['ver','board'].includes(text))throw new Error('Failure diagnostics allow only ver and board');
      if(!Number.isFinite(timeoutMs)||timeoutMs<=0)throw new Error('Invalid failure diagnostic timeout');
      if(canContinue!==undefined&&typeof canContinue!=='function')throw new Error('Invalid failure diagnostic guard');
      const timeout=Math.min(timeoutMs,5000),radio=requestRadio(this),path=this.otaPath||'path unknown';
      const admitted=()=>{
        if(canContinue&&canContinue()!==true)throw new Error('Failure diagnostics stopped: connection or request changed');
        if(!this.running||!this.writer||this.failure)throw this.failure||new Error('Failure diagnostics unavailable: USB source disconnected');
        if(this.serving||this.seeder||this.folderUnknown||!this.ownsPeer||this.diagnosticReadUncertain)
          throw new Error('Failure diagnostics unavailable: the source session is busy or its state is uncertain');
        if(this.authenticatedKey!==k||this.diagnosticPassword!==password||!this.lastPeerUse||Date.now()-this.lastPeerUse<0||Date.now()-this.lastPeerUse>=240000)
          throw new Error('Failure diagnostics unavailable: no current authenticated target session');
      };
      return this.withAdmin(async()=>{
        if(canContinue&&canContinue()!==true)throw new Error('Failure diagnostics stopped: connection or request changed');
        const cached=this.cachedFailureIdentity(k),field=text==='ver'?'version':'board';
        if(cached?.[field])return cached[field];
        admitted();
        const previous={commandTimeout:this.commandTimeout,remoteTimeout:this.remoteTimeout,deadline:this.diagnosticDeadline,canContinue:this.diagnosticCanContinue};
        this.commandTimeout=Math.min(this.commandTimeout,timeout);this.remoteTimeout=Math.min(this.remoteTimeout,timeout);this.diagnosticDeadline=Date.now()+timeout;this.diagnosticCanContinue=canContinue||null;
        try{
          // Use only the existing peer session. ACL admission/login or path
          // selection here would alter the failed operation's context.
          const reply=await this.job('ota.peer cmd '+k+' '+text);
          admitted();this.lastPeerUse=Date.now();this.rememberFailureIdentity(k,text,reply);return reply;
        }catch(error){
          if(/timed out/i.test(error?.message||''))this.diagnosticReadUncertain=true;
          throw remoteError(error,{otaStep:'Reading target identity after failure',otaCommand:text,otaEndpoint:'remote target',otaTimeoutMs:timeout,otaPath:path,otaRadio:radio});
        }finally{
          this.commandTimeout=previous.commandTimeout;this.remoteTimeout=previous.remoteTimeout;this.diagnosticDeadline=previous.deadline;this.diagnosticCanContinue=previous.canContinue;
        }
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
      this.otaPath=prefixes.length?'routed ('+prefixes.length+' hops; configured)':'direct (0 hops; configured)';
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
      this.seeder = null; this.serving = false; this.sourceKey = null; this.authenticatedKey = null; this.diagnosticPassword=null;this.failureIdentityCache=null;
      this.diagnosticDeadline=null;this.diagnosticCanContinue=null;this.diagnosticReadUncertain=false;
      this.ownsPeer = false;
    }
  }
  return {StreamDecoder, SeederData, CompanionFolderSource, RepeaterTransport, responseFrame, rawDeflate, hex};
});
