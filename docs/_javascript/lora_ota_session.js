/* A deliberately conservative direct LoRa OTA session. */
(function (root, factory) {
  const checks = typeof module === 'object' && module.exports ? require('./lora_ota_checks.js') : root.LoraOtaChecks;
  const api = factory(checks);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaSession = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (checks) {
  'use strict';
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const readBudgetExhausted = error => error?.otaBudgetExhausted===true||
    Number.isInteger(error?.otaAttempt)&&Number.isInteger(error?.otaMaxAttempts)&&
      error.otaMaxAttempts>0&&error.otaAttempt>=error.otaMaxAttempts||
    [7,11].includes(error?.attempts);
  const radioSetup = () => typeof module === 'object' && module.exports ? require('./lora_ota_radio_setup.js') : globalThis.LoraOtaRadioSetup;
  const formatRadio = tuple => [Number(tuple[0]).toFixed(3), ...tuple.slice(1)].join(',');
  // Installer support belongs to the authenticated running firmware, not the
  // selected update or its journal. Keep credentials and connection ownership
  // only in this module's RAM; closing a guard for a channel handoff is not a
  // physical USB reconnect and need not discard unchanged firmware evidence.
  const installCapabilities = new WeakMap();
  const object = value => value !== null && (typeof value === 'object' || typeof value === 'function');
  function installCapabilityReply(reply) {
    if (typeof reply !== 'string') return null;
    reply = reply.trim().replace(/^>\s*/, '');
    if (reply === 'ERR usage: ota installhash <64-hex-container-sha256>') return {mode:'hash',reply};
    if (/^(?:(?:err(?:or)?\s*:?\s*)?(?:unknown|unsupported)\b)/i.test(reply)) return {mode:'legacy',reply};
    return null;
  }
  function installCapabilityBinding(link, key, password, dest) {
    if (!object(link) || typeof link.sourceKey !== 'string' || !/^[a-f0-9]{64}$/i.test(link.sourceKey) ||
        typeof key !== 'string' || !/^[a-f0-9]{64}$/i.test(key) || typeof password !== 'string' ||
        !dest || !Number.isInteger(dest.targetId) || dest.targetId < 0 || dest.targetId > 0xffffffff ||
        typeof dest.hwId !== 'string' || !/^[a-z0-9_.-]{1,64}$/i.test(dest.hwId) ||
        !Number.isInteger(dest.version) || dest.version < 0 || dest.version > 0xffffffff ||
        typeof dest.baseHash !== 'string' || !/^[a-f0-9]{16}$/i.test(dest.baseHash)) return null;
    if ([link.knownRepeater,link.target].some(selected => typeof selected === 'string' &&
        /^[a-f0-9]{64}$/i.test(selected) && selected.toLowerCase() !== key.toLowerCase())) return null;
    const connection = [], seen = new Set();
    for (let current = link; object(current); current = current.link) {
      if (seen.has(current) || connection.length >= 8 || current.failure ||
          current.running === false || current.opened === false || current.portOpened === false) return null;
      seen.add(current);
      connection.push({owner:current,port:current.port,reader:current.reader,writer:current.writer,
        running:current.running,opened:current.opened,portOpened:current.portOpened,sourceKey:current.sourceKey,
        knownRepeater:current.knownRepeater,target:current.target});
    }
    if (!connection.some(snapshot => object(snapshot.port) && object(snapshot.reader) && object(snapshot.writer))) return null;
    return {source:link.sourceKey.toLowerCase(),target:key.toLowerCase(),password,
      targetId:dest.targetId,hwId:dest.hwId,version:dest.version,baseHash:dest.baseHash.toLowerCase(),connection};
  }
  function sameInstallCapabilityBinding(a, b) {
    return !!a && !!b && ['source','target','password','targetId','hwId','version','baseHash'].every(field => a[field] === b[field]) &&
      a.connection.length === b.connection.length && a.connection.every((snapshot, index) =>
        Object.keys(snapshot).every(field => snapshot[field] === b.connection[index][field]));
  }
  function clearInstallCapability(link) { if (object(link)) installCapabilities.delete(link); }
  function rememberInstallCapability(link, key, password, dest, reply) {
    const binding = installCapabilityBinding(link, key, password, dest), capability = installCapabilityReply(reply);
    if (!binding || !capability) { clearInstallCapability(link); return false; }
    installCapabilities.set(link, {...binding,...capability});
    return true;
  }
  function cachedInstallCapability(link, key, password, dest) {
    if (!object(link)) return null;
    const cached = installCapabilities.get(link);
    if (!cached) return null;
    if (!sameInstallCapabilityBinding(cached, installCapabilityBinding(link, key, password, dest))) {
      clearInstallCapability(link); return null;
    }
    return cached;
  }
  function setupRequired(message) { const error = Error(message); error.radioSetupRequired = true; return error; }
  function value(text) { const s = String(text).trim().replace(/^>\s*/, ''); if (!s || /^(error|err\b|unknown\b|unsupported\b)/i.test(s)) throw Error(s || 'Empty response'); return s; }
  function diagnostic(text, limit = 160) {
    const clean = String(text).replace(/[^\x20-\x7e]/g, ' ').replace(/\s+/g, ' ').trim();
    return clean.length > limit ? clean.slice(0, limit) + '...' : clean;
  }
  function leaseExpiry(reply, now) {
    const text=String(reply), seconds=/\b(\d+)s left\b/i.exec(text), minutes=/\((\d+) min\) left\b/i.exec(text);
    // Radio2 rounds its display up to minutes. Subtract one displayed unit
    // rather than claiming a lease remains valid longer than its readback.
    const remaining=seconds?Math.max(0,Number(seconds[1])-1):minutes?Math.max(0,Number(minutes[1])-1)*60:0;
    return Number.isSafeInteger(remaining)&&remaining>0&&remaining<=10080*60?now+remaining*1000:null;
  }
  function temp(text) {
    let s;
    try { s = value(text); }
    catch (_) { throw Error('Cannot read active temporary radio settings: ' + diagnostic(text)); }
    if (/^TempRadio (?:pending\b|inactive\b)/i.test(s)) throw Error('An active temporary radio window is required');
    s = s.replace(/^TempRadio active:\s*/i, '');
    if (/^(?:off|inactive|pending)\b/i.test(s)) throw Error('An active temporary radio window is required');
    const m = s.match(/^(\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+),(\d+)/);
    if (!m) throw Error('Cannot read active temporary radio settings: ' + diagnostic(text));
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
    if (!/\bhops=0\b/i.test(config)) throw setupRequired('OTA hops must be 0 on both radios for a direct update');
    if (!/mode=seeder-only/i.test(config) && (!/\bautofetch=off\b/i.test(config) || !/\bautoinstall=off\b/i.test(config))) throw setupRequired('Automatic OTA fetch and install must be off before starting');
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
  function restorationRecordError(message) {
    return Object.assign(Error(message), {otaStep:'Validate saved target radio2 restoration record',
      otaEndpoint:'Browser',otaLocalCheck:true,otaRestorationVerification:true});
  }
  function pausedSecondary(config) {
    if (!config || config.state !== 'configured' || config.autoOwned !== false ||
        !['rx','rxtx'].includes(config.mode) || !Array.isArray(config.tuple) || config.tuple.length !== 4 ||
        typeof config.automatic !== 'boolean') throw restorationRecordError('The saved target radio2 restoration record is invalid.');
    const [frequency,bandwidth,sf,cr] = config.tuple;
    try { radioSetup().settings({frequency,bandwidth,sf,cr,minutes:1,preamble:config.preamble}); }
    catch (_) { throw restorationRecordError('The saved target radio2 restoration record is invalid.'); }
    return {state:'configured',tuple:config.tuple.slice(),mode:config.mode,preamble:config.preamble,
      automatic:config.automatic,autoOwned:false};
  }
  function cloneRadio(radio) {
    return {...radio,tuple:radio.tuple.slice(),
      ...(radio.explicitProfiles ? {explicitProfiles:radio.explicitProfiles.slice()} : {}),
      ...(radio.pausedTargetSecondary ? {pausedTargetSecondary:pausedSecondary(radio.pausedTargetSecondary)} : {})};
  }
  class DirectSession {
    constructor(link, key, password, info, options = {}) {
      this.link = link; this.key = key; this.password = password; this.info = info;
      this.signerVerified = !!options.signerVerified; this.image = options.image; this.compress = options.compress || null; this.inplacePlan = options.inplacePlan || null;
      this.onStage = options.onStage || (() => {});
      // Only an explicit, hash-bound application chain can request an equal-
      // version bridge. Discovery and ordinary single updates remain forward-only.
      this.allowSameVersion = options.allowSameVersion === true;
      this.deferSecondaryRestoration = options.deferSecondaryRestoration === true;
      this.secondaryRestorationPending = false;
      this.secondaryRestorationBinding = null;
      this.pollMs = options.pollMs || 20000; this.transferTimeoutMs = options.transferTimeoutMs || 4 * 60 * 60 * 1000; this.progress = options.progress || (() => {}); this.readJournal = options.readJournal || (() => null);
      this.verifiedRadio = null;
      this.journal = options.journal || (() => {});
      this.cancelled = false; this.serving = false; this.serveTask = null; this.stopServingTask = null; this.startTask = null;
      this.leaseMaintenanceUnavailable = false;
      this.pollWake = null;
    }
    recordJournal(state) {
      this.journal(this.verifiedRadio ? {...state, radio:cloneRadio(this.verifiedRadio)} : state);
    }
    restorePausedSecondaryRecord(record) {
      // A journal cannot supply restoration expectations for another radio or
      // package. The image body is independently validated before this runs.
      if (!record?.radio?.pausedTargetSecondary || !['replacement cancel attempted','prepared','pull attempted','ready','install attempted','installed'].includes(record.phase) ||
          typeof record.target !== 'string' || record.target.toLowerCase() !== this.key.toLowerCase() ||
          typeof record.source !== 'string' || record.source.toLowerCase() !== this.link.sourceKey?.toLowerCase() ||
          record.id !== checks.hex(this.info.merkleRoot) || record.packageHash !== this.packageHash ||
          record.bodyHash !== this.bodyHash) return;
      const radio = record.radio;
      if (radio.targetProfile !== 1 || ![0,1].includes(radio.sourceProfile) || !Array.isArray(radio.tuple) || radio.tuple.length !== 4)
        throw restorationRecordError('The saved target radio2 restoration record is invalid.');
      if (radio.explicitProfiles !== undefined && (!Array.isArray(radio.explicitProfiles) || radio.explicitProfiles.length !== 2 ||
          radio.explicitProfiles.some(value => typeof value !== 'boolean')))
        throw restorationRecordError('The saved target radio2 restoration record is invalid.');
      try { radioSetup().settings({frequency:radio.tuple[0],bandwidth:radio.tuple[1],sf:radio.tuple[2],cr:radio.tuple[3],
        minutes:1,preamble:radio.preamble}); }
      catch (_) { throw restorationRecordError('The saved target radio2 restoration record is invalid.'); }
      if (this.verifiedRadio && (!radioSetup().same(radio.tuple,this.verifiedRadio.tuple) ||
          radio.preamble !== this.verifiedRadio.preamble || radio.sourceProfile !== this.verifiedRadio.sourceProfile ||
          radio.targetProfile !== this.verifiedRadio.targetProfile)) return;
      const expected = pausedSecondary(radio.pausedTargetSecondary), current = this.verifiedRadio?.pausedTargetSecondary;
      if (current && radioSetup().secondarySignature(current) !== radioSetup().secondarySignature(expected))
        throw restorationRecordError('The target radio2 restoration record differs from this update setup.');
      if (!this.verifiedRadio) this.verifiedRadio = cloneRadio(radio);
      this.verifiedRadio.pausedTargetSecondary = expected;
    }
    installedSecondaryRecord(record) {
      if (!record || record.phase !== 'installed' || record.secondaryRestorationPending !== true ||
          typeof record.source !== 'string' || record.source.toLowerCase() !== this.link.sourceKey?.toLowerCase() ||
          typeof record.target !== 'string' || record.target.toLowerCase() !== this.key.toLowerCase() ||
          record.id !== checks.hex(this.info.merkleRoot) || record.packageHash !== this.packageHash ||
          record.bodyHash !== this.bodyHash || !record.radio?.pausedTargetSecondary || !this.verifiedRadio?.pausedTargetSecondary)
        return false;
      return JSON.stringify(cloneRadio(record.radio)) === JSON.stringify(cloneRadio(this.verifiedRadio));
    }
    secondaryConnectionBinding() {
      return installCapabilityBinding(this.link, this.key, this.password,
        {targetId:this.info.targetId,hwId:this.info.hwId,version:this.info.fwVersion,baseHash:this.bodyHash});
    }
    async recordVerifiedInstall(id, prior=null) {
      const expected=this.verifiedRadio?.pausedTargetSecondary;
      const pending=this.deferSecondaryRestoration && !!expected &&
        !(prior?.phase==='installed' &&
          (prior.secondaryRestorationPending===undefined||prior.secondaryRestorationPending===false));
      if (!this.deferSecondaryRestoration) await this.verifyRestoredTargetSecondary();
      this.recordJournal({phase:'installed',target:this.key,source:this.link.sourceKey,id,
        packageHash:this.packageHash,bodyHash:this.bodyHash,...(pending?{secondaryRestorationPending:true}:{})});
      this.secondaryRestorationPending=pending;
      this.secondaryRestorationBinding=pending?this.secondaryConnectionBinding():null;
    }
    async verifyRestoredTargetSecondary({maxDurationMs}={}) {
      const expected = this.verifiedRadio?.pausedTargetSecondary;
      if (!expected) return {state:'none'};
      const setup = radioSetup(); let command = 'get tempradio2';
      try {
        const bounded=this.deferSecondaryRestoration || maxDurationMs!==undefined;
        let snapshot,record,binding,deadline,previous;
        if(bounded){
          record=this.readJournal();
          if(!record?.secondaryRestorationPending){
            if(this.secondaryRestorationPending)
              throw Error('The pending installed restoration record is missing or changed; its verification was not cleared.');
            return {state:'none'};
          }
          if(!Number.isFinite(maxDurationMs??5000)||(maxDurationMs??5000)<=0||(maxDurationMs??5000)>5000)
            throw Error('Radio2 restoration verification needs a positive time budget of at most 5 seconds.');
          if(this.link.browserRawSource!==true||this.link.otaSupported!==true)
            throw Error('This connection cannot perform a bounded radio2 restoration check.');
          if(!this.installedSecondaryRecord(record)||!this.secondaryRestorationBinding)
            throw Error('Verify the exact installed firmware before checking its pending radio2 restoration.');
          snapshot=JSON.stringify(record);binding=this.secondaryRestorationBinding;
          previous=Date.now();deadline=previous+(maxDurationMs??5000);
        }
        const current=()=>{
          this.requireActive();
          if(!bounded)return Infinity;
          const now=Date.now();
          if(!Number.isFinite(now)||now<previous||!Number.isFinite(deadline)||now>=deadline)
            throw Error('The optional radio2 restoration check exceeded its time budget.');
          previous=now;
          const currentRecord=this.readJournal();
          if(!sameInstallCapabilityBinding(binding,this.secondaryConnectionBinding())||
              !this.installedSecondaryRecord(currentRecord)||JSON.stringify(currentRecord)!==snapshot)
            throw Error('The installed firmware, connection or pending restoration record changed; its verification was not cleared.');
          return deadline-now;
        };
        const read=async text=>{
          const remaining=current();
          const reply=bounded?await this.link.remoteCommand(this.key,this.password,text,
            {maxDurationMs:remaining,allowReacquire:false}):await this.remote(text);
          current();return value(reply);
        };
        if (setup.secondary(await read(command), command).state !== 'off')
          throw Error('The target temporary radio2 window is still active or pending.');
        command = 'get radio2';
        const actual = setup.secondary(await read(command));
        if (setup.secondarySignature(actual) !== setup.secondarySignature(expected))
          throw Error('The target saved radio2 settings differ from the settings paused for this update.');
        if(bounded){
          current();const updated={...record};delete updated.secondaryRestorationPending;
          this.journal(updated);this.secondaryRestorationPending=false;this.secondaryRestorationBinding=null;
        }
        this.progress('Target normal radio2 restored; its saved settings are verified unchanged.');
        return {state:'verified'};
      } catch (cause) {
        const error = cause instanceof Error ? cause : Error(String(cause));
        error.message = 'Firmware installed and verified, but normal target radio2 restoration could not be verified: ' + diagnostic(error.message);
        const step = 'Verify restored target radio2 after reboot';
        if (error.otaStep || error.otaCommand || error.otaEndpoint) {
          error.otaRequestStep = step; error.otaRequestCommand = command;
        }
        if (!error.otaStep) error.otaStep = step;
        if (!error.otaEndpoint) error.otaEndpoint = 'remote target';
        if (!error.otaCommand) error.otaCommand = command;
        error.otaRestorationVerification = true;
        throw error;
      }
    }
    requireActive() {
      if (this.cancelled) throw Error('Stopped; no further update commands were sent.');
      if (this.link.failure) throw this.link.failure;
    }
    async remote(command) { this.requireActive(); return value(await this.link.remoteCommand(this.key, this.password, command)); }
    async local(command) { this.requireActive(); return value(await this.link.localCommand(command)); }
    waitForPoll(milliseconds) {
      if (this.cancelled) return Promise.resolve();
      return new Promise(resolve => {
        const finish = () => {
          clearTimeout(timer);
          if (this.pollWake === finish) this.pollWake = null;
          resolve();
        };
        const timer = setTimeout(finish, milliseconds);
        this.pollWake = finish;
        if (this.cancelled) finish();
      });
    }
    async maintainTemporaryLease(current) {
      this.requireActive();
      const record=this.readJournal();
      if(!this.serving||this.link.relays?.length||this.leaseMaintenanceUnavailable||!record?.radio||record.radio.expiresAt>Date.now()+20*60000)return;
      const leases=typeof module==='object'&&module.exports?require('./lora_ota_lease.js'):globalThis.LoraOtaLease;
      if(!leases||current?.state!=='active'||!leases.dataReady(current.status,record.id))return;
      const result=await leases.maintain(this.link,this.key,this.password,record,{progress:this.progress,
        journal:updated=>this.journal(updated)});
      if(result.state==='unavailable')this.leaseMaintenanceUnavailable=true;
      if(result.record?.radio)this.verifiedRadio=cloneRadio(result.record.radio);
      this.requireActive();
    }
    async requireInstallHash(dest) {
      this.requireActive();
      this.installMode = null;
      let capability = cachedInstallCapability(this.link, this.key, this.password, dest);
      if (!capability) {
        const before = installCapabilityBinding(this.link, this.key, this.password, dest);
        const reply = await this.link.remoteCommand(this.key, this.password, 'ota installhash');
        this.requireActive();
        if (before && !sameInstallCapabilityBinding(before, installCapabilityBinding(this.link, this.key, this.password, dest))) {
          clearInstallCapability(this.link);
          throw Error('The target identity or USB connection changed while checking installation capability');
        }
        capability = installCapabilityReply(reply);
        if (!capability) {
          clearInstallCapability(this.link);
          throw Error('Cannot determine whether the target supports verified OTA installation');
        }
        rememberInstallCapability(this.link, this.key, this.password, dest, reply);
      }
      // Only an explicit unsupported-command response establishes that the
      // older install verb is needed. A timeout or unexpected answer leaves
      // the target's capabilities unknown and must never select a fallback.
      this.installMode = capability.mode;
      if (this.installMode === 'legacy') {
        this.progress('Older target: ota install validates the staged package, but cannot bind the full container hash. Post-install verification is limited to the target-reported firmware hash prefix.');
      }
    }
    async readDownload() {
      const status = await this.remote('ota status');
      let parsed, stats = '', statsError = '';
      try { parsed = download(status); } catch (_) { stats = await this.remote('ota stats'); parsed = download(status, stats); }
      // Preserve the target's reason, including on older firmware whose
      // download identity and state must be read from ota stats instead.
      const failureIn = text => {
        const section = /\b(?:download:|fetch)\s*(failed\b[^|]*)/i.exec(text)?.[1];
        const id = section && /\bid=([0-9a-f]{8})\b/i.exec(section)?.[1];
        return id?.toLowerCase() === parsed.id ? section : null;
      };
      const hasReason = section => /^failed\s*(?:\((?!none\))[^)]+\)|:(?!none\b)[^\s|]+)/i.test(section || '');
      const statusFailure = parsed.state === 'failed' ? failureIn(status) : null;
      const optionalProbeMayRetry = this.link.browserRawSource && this.link.otaSupported !== true;
      if (parsed.state === 'failed' && !hasReason(statusFailure) && !stats && !optionalProbeMayRetry && !this.cancelled && !this.link.failure) {
        // A reason-less status can be supplemented by stats; failure to read
        // it must not hide the already confirmed failed state. Legacy
        // Companion reads retry internally, so do not start that optional
        // probe and delay the failure report by several minutes.
        try { stats = await this.remote('ota stats'); }
        catch (error) { statsError = diagnostic(error?.message || error); }
      }
      const statsFailure = parsed.state === 'failed' ? failureIn(stats) : null;
      const failure = hasReason(statusFailure) ? statusFailure : hasReason(statsFailure) ? statsFailure : statusFailure || statsFailure;
      return { ...parsed, status, ...(stats ? {stats} : {}), ...(statsError ? {statsError} : {}),
        ...(failure ? {failureDetail:diagnostic(failure), failureHasReason:hasReason(failure)} : {}) };
    }
    reportFailedDownload(current) {
      this.progress('Target OTA status: ' + diagnostic(current.status, 512));
      if (current.stats) this.progress('Target OTA stats: ' + diagnostic(current.stats, 512));
      if (current.statsError) this.progress('Target OTA stats unavailable: ' + current.statsError);
      const advice = current.failureHasReason && checks.failureAdvice(current.failureDetail);
      if (advice) this.progress('Next step: ' + advice);
      throw Error('Target reported a failed download' +
        (current.failureDetail ? ': ' + current.failureDetail : '') +
        (current.failureHasReason ? '' : '. Run ota stats on the target for details.'));
    }
    validateRecovery(state, options = {}) {
      if (state.state === 'idle') return;
      if (!options.allowExisting || state.id !== checks.hex(this.info.merkleRoot))
        throw Error('Target already has another OTA download; keep the original firmware selected to recover it');
      const record = this.readJournal();
      if (!record || typeof record.target !== 'string' || typeof record.source !== 'string' ||
          record.target.toLowerCase() !== this.key.toLowerCase() ||
          record.source.toLowerCase() !== this.link.sourceKey?.toLowerCase() || record.id !== state.id ||
          record.packageHash !== this.packageHash || !['pull attempted', 'ready'].includes(record.phase))
        throw Error('The existing download has no matching browser recovery record; resolve it before proceeding');
    }
    downloadDisposition(state, options = {}) {
      try { this.validateRecovery(state, options); }
      catch (error) {
        if (!options.allowReplacement || state.state === 'idle') throw error;
        return {download:'replace', existingDownload:{id:state.id, state:state.state}};
      }
      return {download:state.state};
    }
    async inspectRadios() {
      // Package-independent GET-only preflight. The caller admits the selected
      // peer first; inspect() also retains its full identity/install checks.
      const checkSourceOta = !this.link.browserRawSource || this.link.otaSupported === true || this.link.usesUsbFolderFallback === true;
      const guardedCompanion = this.link.browserRawSource && this.link.otaSupported === true;
      if (!checkSourceOta) this.progress('The Companion firmware OTA seeder and folder state cannot be checked.');
      let sourceRadio, targetRadio, savedSourceRadio, sourcePreamble, sourceReadback = 'active';
      const leaseReadbacks=[];
      this.tempRadio = null;
      this.verifiedRadio = null;
      for (const source of [true, false]) {
        const label = source ? (this.link.browserRawSource ? 'USB Companion' : 'source') : 'target';
        const profile = (source ? this.link.otaRadioProfile : this.link.otaTargetProfile) ?? 0;
        if (profile !== 0 && profile !== 1) throw Error(label + ': Invalid selected OTA radio profile');
        if (source && profile === 1 && this.link.supportsOtaRadio2 !== true)
          throw Error(label + ': This source cannot verify OTA transmission on radio2');
        const rawCommand = async c => {
          try { return await (source ? this.link.localCommand(c) : this.link.remoteCommand(this.key, this.password, c)); }
          catch (error) { throw Error(label + ': ' + diagnostic(error.message)); }
        };
        const command = async c => {
          try {
            const reply = await rawCommand(c);
            if (c === 'get tempradio') radioSetup().requiredReply(reply, c, source && (this.link.browserRawSource ? 'companion' : 'repeater'));
            return value(reply);
          }
          catch (error) { if (!error.message.startsWith(label + ': ')) error.message = label + ': ' + diagnostic(error.message); throw error; }
        };
        if (source && this.link.browserRawSource && this.link.localCliSupported === false) {
          if (profile !== 0) throw Error('The USB Companion cannot verify an active temporary radio2 without local CLI');
          const radio = await this.link.savedSourceRadio();
          if (!Array.isArray(radio) || radio.length !== 4 || radio.some(v => !Number.isFinite(v) || v <= 0))
            throw Error('Cannot read the USB Companion saved radio settings');
          sourceRadio = savedSourceRadio = radio.slice(); sourceReadback = 'saved-only';
          this.progress('The Companion reported these saved radio settings on connection. Active temporary settings and preamble cannot be verified on this firmware.');
          continue;
        }
        let radio;
        try {
          if (profile === 1) {
            // Resolve at call time: the page loads setup after this module.
            const setup = typeof module === 'object' && module.exports ? require('./lora_ota_radio_setup.js') : globalThis.LoraOtaRadioSetup;
            if (typeof setup?.readTemporary !== 'function') throw Error('Temporary radio2 verification is unavailable');
            const selected = await setup.readTemporary(rawCommand, profile);
            if (selected.state !== 'active') throw Error('An active temporary radio window is required');
            radio = selected.tuple;
            leaseReadbacks.push({reply:selected.raw,at:Date.now(),source});
            if (source) sourcePreamble = selected.preamble;
            // Native locally generated OTA selects radio2 only while primary
            // is not temporary. Never silently route requests to another lease.
            const primary = String(await command('get tempradio')).trim().replace(/^>\s*/, '');
            if (!/^(?:off|inactive|TempRadio inactive)$/i.test(primary))
              throw Error('The primary temporary radio must be off when OTA uses radio2');
          } else {
            const reply = await command('get tempradio');
            radio = temp(reply);
            leaseReadbacks.push({reply,at:Date.now(),source});
            // Some older readbacks omit the preamble. They can still use the
            // existing updater, but cannot authorize automatic saved recovery.
            if (source) {
              try { sourcePreamble = radioSetup().temporary(reply).preamble; }
              catch (_) { sourcePreamble = undefined; }
            }
          }
        }
        catch (error) {
          if (/active temporary radio window is required/i.test(String(error.message))) {
            throw setupRequired('An active temporary radio window is required on the '+label+(profile ? ' radio2' : '')+' for LoRa OTA.');
          }
          if (String(error.message).startsWith(label + ': ')) throw error;
          throw Error(label + ': ' + diagnostic(error.message));
        }
        if (source) {
          try { savedSourceRadio = temp(await command('get radio')); }
          catch (error) { throw Error(label + ' saved primary: ' + diagnostic(error.message)); }
          // Native source-selection APIs pin the selected profile generation,
          // including a repeater's primary fallback. Unprobed older sources
          // retain the original saved-primary equality requirement.
          const verifiedSecondary = profile === 1 && this.link.supportsOtaRadio2 === true;
          const nativeRepeater = !this.link.browserRawSource && this.link.supportsOtaRadio2 === true;
          if (!guardedCompanion && !verifiedSecondary && !nativeRepeater &&
              this.link.usesUsbFolderFallback !== true && radio.some((v, i) => Math.abs(v - savedSourceRadio[i]) > 0.001)) {
            const command='tempradio '+formatRadio(savedSourceRadio)+',120,128';
            const error=setupRequired('The '+label+' temporary radio must match its saved primary channel in compatibility mode. '+
              'Saved: '+formatRadio(savedSourceRadio)+'; temporary: '+formatRadio(radio)+'. Automatic setup will align both radios.');
            error.manualRadioCommand=command;throw error;
          }
        }
        if (!source || checkSourceOta) {
          try { policy(await command('ota config')); }
          catch (error) { if (!error.message.startsWith(label + ': ')) error.message = label + ': ' + diagnostic(error.message); throw error; }
        }
        if (source) sourceRadio = radio;
        else targetRadio = radio;
      }
      if (targetRadio.some((v, i) => Math.abs(v - sourceRadio[i]) > 0.001))
        throw setupRequired('Temporary radio settings do not match between '+(this.link.browserRawSource ? 'USB Companion' : 'source')+' and target');
      this.tempRadio = sourceRadio.slice();
      if (sourceReadback === 'active' && Number.isInteger(sourcePreamble)) {
        const verifiedAt=Date.now(), expiries=leaseReadbacks.map(row=>leaseExpiry(row.reply,row.at)).filter(value=>value!==null);
        this.verifiedRadio = {tuple:sourceRadio.slice(), preamble:sourcePreamble,
          sourceProfile:this.link.otaRadioProfile ?? 0, targetProfile:this.link.otaTargetProfile ?? 0,
          verifiedAt,expiresAt:expiries.length?Math.min(...expiries):null};
        for(const row of leaseReadbacks)this.verifiedRadio[row.source?'sourceExpiresAt':'targetExpiresAt']=leaseExpiry(row.reply,row.at);
        const proof=this.link.otaLeaseProof,setup=radioSetup();
        if(proof&&proof.source===this.link.sourceKey&&proof.target===this.key&&setup.same(proof.tuple||[],sourceRadio)&&
          proof.preamble===sourcePreamble&&proof.sourceProfile===this.verifiedRadio.sourceProfile&&
          proof.targetProfile===this.verifiedRadio.targetProfile&&Array.isArray(proof.explicitProfiles)) {
          this.verifiedRadio.explicitProfiles=proof.explicitProfiles.map(value=>value===true);
          if (proof.pausedTargetSecondary && proof.targetProfile === 1)
            this.verifiedRadio.pausedTargetSecondary=pausedSecondary(proof.pausedTargetSecondary);
        }
      }
      return {source:sourceRadio, target:targetRadio, savedSource:savedSourceRadio, sourceReadback};
    }
    async inspect(options = {}) {
      this.requireActive();
      this.link.requireOtaPacketCapacity?.();
      await this.link.verifyPeer(this.key, this.password);
      this.requireActive();
      const {status,self} = await checks.readReceiver(command => this.link.remoteCommand(this.key, this.password, command));
      const ver = await this.remote('ver');
      const dest = checks.destination(status, self, ver);
      const cryptoApi = globalThis.crypto?.subtle || (typeof require === 'function' ? require('node:crypto').webcrypto.subtle : null);
      if (!cryptoApi) throw Error('Web Crypto is unavailable');
      this.packageHash = checks.hex(new Uint8Array(await cryptoApi.digest('SHA-256', this.info.blob)));
      this.installHash = await installHash(this.info.blob);
      const prior = this.readJournal();
      if (options.allowExisting && ['install attempted', 'installed'].includes(prior?.phase) && typeof prior.target === 'string' && prior.target.toLowerCase() === this.key.toLowerCase() && typeof prior.source === 'string' && prior.source.toLowerCase() === this.link.sourceKey?.toLowerCase() && prior.id === checks.hex(this.info.merkleRoot) && prior.packageHash === this.packageHash && dest.targetId === this.info.targetId && dest.hwId === this.info.hwId && dest.version === this.info.fwVersion) {
        this.bodyHash = await expectedBody(this.info, this.image);
        if (dest.baseHash === this.bodyHash && prior.bodyHash === this.bodyHash) {
          this.verifiedRadio = null;
          this.restorePausedSecondaryRecord(prior);
          await this.recordVerifiedInstall(prior.id,prior);
          return {...dest, download:'installed'};
        }
      }
      checks.validate(this.info, dest, this.signerVerified, {allowSameVersion:this.allowSameVersion});
      const hybridRamReply = await checks.readHybridRam(this.inplacePlan, this.info, () => this.remote('ota ram'),
        ()=>this.remote('ota bootloader status'),()=>this.remote('get bootloader.ver'));
      checks.validateInternalPlan(this.inplacePlan, this.info, status, self, dest,
        this.inplacePlan?.storage === 'qspi' ? await this.remote('ota qspi') : null, hybridRamReply);
      if (!this.info.isFull && this.info.codecId === 1)
        await checks.ensureSequentialTarget(status, self, () => this.link.remoteCommand(this.key, this.password, 'get bootloader.ver'));
      if (this.info.signed) {
        const trusted = await this.remote('ota key list');
        if (!trusted.toLowerCase().split(/\s+/).includes(checks.hex(this.info.signer.subarray(0, 8)))) throw Error('The target does not list the supplied trusted signer; add it on the target first');
      }
      this.bodyHash = await expectedBody(this.info, this.image);
      const checkSourceOta = !this.link.browserRawSource || this.link.otaSupported === true || this.link.usesUsbFolderFallback === true;
      await this.requireInstallHash(dest);
      await this.inspectRadios();
      this.restorePausedSecondaryRecord(prior);
      const state = await this.readDownload();
      const disposition = this.downloadDisposition(state, options);
      if (checkSourceOta) {
        if (!/\bfetch idle\b/i.test(await this.local('ota stats'))) throw Error('The source already has an OTA download');
        if (!/^folder=off\b/i.test(await this.local('ota folder'))) throw Error('The source already has an attached OTA folder');
      }
      return {...dest, ...disposition};
    }
    async start(options = {}) {
      const previous = this.startTask;
      if (previous && !this.cancelled) throw Error('This update is already running');
      // A stopped read can still be unwinding its finally block. Let both it
      // and its local detach finish before a new package takes USB ownership.
      if (previous) await previous.catch(() => {});
      if (this.stopServingTask) await this.stopServingTask;
      if (this.startTask) throw Error('This update is already running');
      this.stopServingTask = null;
      const task = this.startTransfer(options);
      this.startTask = task;
      try { return await task; }
      finally {
        if (this.startTask === task) {
          this.startTask = null;
          if (!this.serving) this.stopServingTask = null;
        }
      }
    }
    async catalogContains(id) {
      // Firmware catalogs contain at most 255 entries, rendered two per page.
      // Every page query also refreshes the catalog, so the advertised page
      // count may change while scanning; each reply must identify its page.
      for (let page = 1, pages = 1; page <= pages && page <= 128; page++) {
        this.requireActive();
        let reply;
        try { reply = String(await this.remote(page === 1 ? 'ota ls' : 'ota ls ' + page)).trim(); }
        catch (error) {
          // Catalog rows can expire while the previous page is in flight.
          // Restart on the next bounded discovery round only for the precise
          // out-of-range reply for the page just requested.
          const shrink = /^ERR update page (\d+) out of range \(1-(\d+)\)$/i.exec(String(error?.message || ''));
          if (page > 1 && shrink && Number(shrink[1]) === page && Number(shrink[2]) >= 1 && Number(shrink[2]) < page)
            return false;
          throw error;
        }
        if (page === 1 && /^No updates seen yet\b/i.test(reply)) return false;
        // A few older peers return one bare ID instead of a paged listing.
        if (page === 1 && /^[a-f0-9]{8}$/i.test(reply)) return reply.toLowerCase() === id;
        const header = /^Updates\s+(\d+)\/(\d+)(?=\s|\(|:)/i.exec(reply);
        if (!header || Number(header[1]) !== page || Number(header[2]) < page || Number(header[2]) > 128)
          throw Error('Cannot read update catalog pagination');
        pages = Number(header[2]);
        const rows = [...reply.matchAll(/(?:^|[\r\n])\s*(\d+)\)\s+([a-f0-9]{8})(?=\s|$)/gi)];
        const seen = new Set(); let found = false;
        if (!rows.length) throw Error('Cannot read update catalog entries');
        for (const row of rows) {
          const index = Number(row[1]);
          if (index < (page - 1) * 2 + 1 || index > page * 2 || index > 255 || seen.has(index))
            throw Error('Cannot read update catalog entries');
          seen.add(index);
          if (row[2].toLowerCase() === id) found = true;
        }
        if (found) return true;
      }
      return false;
    }
    async startTransfer(options = {}) {
      const replacement = options.replace;
      if (replacement && (options.resume || !/^[0-9a-f]{8}$/.test(replacement.id || '') ||
          !['active','failed','ready'].includes(replacement.state))) throw Error('Invalid download replacement request');
      this.cancelled = false; this.tempRadio = null; this.leaseMaintenanceUnavailable = false;
      this.onStage('checking');
      let before = await this.inspect({allowExisting:!!options.resume, allowReplacement:!!replacement});
      this.requireActive();
      const id = checks.hex(this.info.merkleRoot);
      if (before.download === 'installed') return;
      if (replacement) {
        const selected = before.existingDownload;
        if (!selected || selected.id !== replacement.id || selected.state !== replacement.state)
          throw Error('Target download state changed; prepare the replacement again');
        const current = await this.readDownload();
        if (current.id !== replacement.id || current.state !== replacement.state)
          throw Error('Target download state changed; prepare the replacement again');
        // The explicit replacement action authorizes discarding this exact
        // observed session. Require its acknowledgement: becoming idle alone
        // cannot prove that persistent staged data was invalidated successfully.
        this.recordJournal({phase:'replacement cancel attempted', target:this.key, source:this.link.sourceKey,
          id, packageHash:this.packageHash, replaced:replacement, bodyHash:this.bodyHash});
        const reply = await this.remote('ota cancel');
        if (!/^OK(?:\s|$)/i.test(reply)) throw Error('Target did not acknowledge download cancellation: ' + diagnostic(reply));
        if ((await this.readDownload()).state !== 'idle') throw Error('Target download was not cancelled; prepare the replacement again');
        before = {...before, download:'idle'};
        this.progress('The previous download was cancelled. Starting the selected firmware.');
      }
      if (options.resume && before.download === 'idle') throw Error('There is no matching download to resume');
      if (!options.resume && before.download !== 'idle') throw Error('Target already has a download');
      this.recordJournal({phase: options.resume ? 'pull attempted' : 'prepared', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, before, bodyHash: this.bodyHash});
      if (before.download === 'ready') { this.recordJournal({phase: 'ready', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, before, bodyHash: this.bodyHash}); return; }
      try {
        this.onStage('preparing-source');
        this.serveTask = Promise.resolve().then(() => {
          this.requireActive();
          return this.link.serve(this.info, this.info.blob, this.compress,
            {resume:!!options.resume&&before.download==='active'});
        }).then(() => { this.serving = true; });
        await this.serveTask;
        this.requireActive();
        const restartFailed = options.resume && before.download === 'failed';
        if (!options.resume || restartFailed) {
          this.onStage('discovering');
          let found = false;
          for (let n = 0; n < 12 && !this.cancelled; n++) {
            if (await this.catalogContains(id)) { found = true; break; }
            await this.waitForPoll(this.pollMs);
          }
          this.requireActive();
          if (!found) throw Error('Target did not discover the selected package');
          const current = await this.readDownload();
          if (restartFailed) {
            // A confirmed failed fetch can be explicitly pulled again. The
            // target resumes its verified staged blocks; cancelling would
            // discard them. Never replay a pull merely because its reply was
            // lost, or replace a session that changed while discovery ran.
            this.validateRecovery(current, {allowExisting:true});
            if (current.state !== 'failed' || current.id !== id) throw Error('Target download state changed before recovery');
            this.progress('Recovering the same failed download; the target will reuse any verified staged blocks.');
          } else if (current.state !== 'idle') throw Error('Target download state changed');
          this.requireActive();
          this.recordJournal({phase: 'pull attempted', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, before, bodyHash: this.bodyHash});
          this.onStage('requesting');
          try { await this.remote('ota pull ' + id.toUpperCase() + ' flash'); }
          catch (error) {
            // Never repeat a pull after an uncertain response.
            if (/^(?:err(?:or)?\b|target rejected ota pull:)/i.test(String(error.message))) throw error;
            if (!/timeout|timed out|disconnect|result is unknown/i.test(String(error.message))) throw error;
            // The locally queued command can still be in flight. Allow a
            // bounded number of read-only checks; never send the pull again.
            let confirmed = false, maxChecks = 3, addedAckCheck = false;
            for (let attempt = 0; attempt < maxChecks; attempt++) {
              // A tagged ACK can arrive while the status call is in flight;
              // that status might still be the pre-ACK failure snapshot.
              const acknowledgedBeforeRead = this.link.pullTargetAcknowledged === true;
              const current = await this.readDownload();
              if (current.id === id) {
                if (current.state === 'failed') {
                  // A failed download existed before a recovery pull. Until
                  // this request is confirmed, the target may still be
                  // reporting that earlier failure rather than a new one.
                  if (!restartFailed || acknowledgedBeforeRead)
                    this.reportFailedDownload(current);
                } else { confirmed = true; break; }
              } else if (current.state !== 'idle') {
                throw error;
              }
              // An ACK observed during the last bounded read requires one
              // additional read taken after the acknowledgement.
              if (restartFailed && !acknowledgedBeforeRead && this.link.pullTargetAcknowledged === true &&
                  attempt === maxChecks - 1 && !addedAckCheck) {
                maxChecks++; addedAckCheck = true;
              }
              if (attempt < maxChecks - 1) { await this.waitForPoll(this.pollMs); this.requireActive(); }
            }
            if (!confirmed) throw Error('Target has not confirmed the update; result is unknown. No second pull was sent.');
          }
        }
        const deadline = Date.now() + this.transferTimeoutMs;
        while (!this.cancelled && Date.now() < deadline) {
          let current;
          for (let attempt = 0; attempt < 3; attempt++) {
            try { current = await this.readDownload(); break; }
            catch (error) { this.requireActive(); if (readBudgetExhausted(error)||attempt === 2 || !/timeout|timed out|disconnect|result is unknown/i.test(String(error.message))) throw error; await this.waitForPoll(2000); }
          }
          if (this.cancelled) this.requireActive();
          if (current.id !== id) throw Error('Target download does not match the selected package');
          if (current.state === 'failed') this.reportFailedDownload(current);
          this.requireActive();
          if (current.state === 'ready') { this.progress(current.status); this.recordJournal({phase: 'ready', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, before, bodyHash: this.bodyHash}); return; }
          this.onStage('transferring');
          this.progress(current.status);
          await this.maintainTemporaryLease(current);
          await this.waitForPoll(this.pollMs);
        }
        throw Error(this.cancelled ? 'Stopped; target download was not cancelled' : 'Transfer timed out; target download was not cancelled');
      } finally {
        const serving = this.serveTask;
        try { await this.stopOwnedSource(); }
        finally { if (this.serveTask === serving) this.serveTask = null; }
      }
    }
    async planRebootChannel() {
      // Temporary profiles do not survive a target reboot. Read its saved
      // primary before installation, while the update channel still works.
      const setup = radioSetup(), savedTarget = setup.primary(await this.remote('get radio'));
      const selected = this.link.otaRadioProfile ?? 0;
      if (setup.same(savedTarget, this.tempRadio)) return {savedTarget, change: false};
      if ((this.link.browserRawSource && this.link.localCliSupported === false) ||
          typeof this.link.closeRadioSession !== 'function' || typeof this.link.selectOtaRadio !== 'function' ||
          (this.link.browserRawSource && typeof this.link.applyTemporaryRadio !== 'function')) {
        const error = Error('This USB source cannot automatically reconnect on the target saved channel after installation. Update the source firmware first.');
        error.sourceUpdateRequired = this.link.browserRawSource ? 'companion' : 'repeater';
        throw error;
      }
      const savedSource = setup.primary(await this.local('get radio'));
      const primary = await setup.readTemporary(c => this.link.localCommand(c), 0);
      if (!['off', 'active'].includes(primary.state)) throw Error('Cannot verify the source primary channel before installation.');
      const tune = !setup.same(primary.state === 'active' ? primary.tuple : savedSource, savedTarget);
      const clearSecondary = selected === 1 && tune && Math.abs(this.tempRadio[0] - savedTarget[0]) <= (this.tempRadio[1] + savedTarget[1]) / 2000 + 0.001;
      const command = setup.commands({frequency:savedTarget[0], bandwidth:savedTarget[1], sf:savedTarget[2], cr:savedTarget[3], minutes:10, preamble:128}).source;
      return {savedTarget, savedSource, change:true, tune, clearSecondary, command};
    }
    async reconnectChannel(plan) {
      this.requireActive();
      const setup = radioSetup();
      this.progress('Reconnecting on the target saved channel: ' + formatRadio(plan.savedTarget) + '.');
      await this.link.closeRadioSession();
      this.requireActive();
      await this.link.selectOtaRadio(0);
      this.requireActive();
      this.link.otaTargetProfile = 0;
      if (plan.clearSecondary) {
        // End only the selected temporary update lease when it would overlap
        // the reconnect channel. Never disable the saved secondary profile.
        try { await this.local('set tempradio2 off'); }
        catch (error) { if (!/timeout|timed out|disconnect|result is unknown/i.test(String(error.message))) throw error; }
        if ((await setup.readTemporary(c => this.link.localCommand(c), 1)).state !== 'off')
          throw Error('Source temporary radio2 did not stop before reconnecting.');
      }
      this.requireActive();
      if (plan.tune) {
        // One local, temporary write only. A lost acknowledgement is resolved
        // by readback, never by repeating the write or changing saved settings.
        try {
          const reply = await (this.link.browserRawSource ? this.link.applyTemporaryRadio(plan.command) : this.link.localCommand(plan.command));
          if (!/^(?:>\s*)?OK(?:\s|$)/i.test(String(reply).trim())) throw Error('Source did not accept the post-install channel: ' + diagnostic(reply));
        } catch (error) {
          if (!/timeout|timed out|disconnect|result is unknown/i.test(String(error.message))) throw error;
        }
      }
      const deadline = Date.now() + 35000;
      do {
        this.requireActive();
        const current = await setup.readTemporary(c => this.link.localCommand(c), 0);
        const saved = setup.primary(await this.local('get radio'));
        if (!setup.same(saved, plan.savedSource)) throw Error('Source saved channel changed during post-install reconnect.');
        if ((current.state === 'active' && setup.same(current.tuple, plan.savedTarget)) ||
            (!plan.tune && current.state === 'off' && setup.same(saved, plan.savedTarget))) return;
        await sleep(500);
      } while (Date.now() < deadline && !this.cancelled);
      throw Error('Source did not switch to the target saved channel for post-install verification.');
    }
    async install() {
      this.requireActive();
      const id = checks.hex(this.info.merkleRoot), before = await this.inspect({allowExisting:true}), current = await this.readDownload();
      if (before.download !== 'ready' || current.id !== id || current.state !== 'ready') throw Error('The selected package is not ready to install');
      const rebootChannel = await this.planRebootChannel();
      this.requireActive();
      this.recordJournal({phase: 'install attempted', target: this.key, source: this.link.sourceKey, id, packageHash:this.packageHash, bodyHash: this.bodyHash, savedTargetRadio:rebootChannel.savedTarget});
      const command = this.installMode === 'hash' ? 'ota installhash ' + this.installHash : this.installMode === 'legacy' ? 'ota install' : null;
      if (!command) throw Error('Target installation capability is unknown');
      try { await this.remote(command); } catch (error) {
        if (!/timeout|timed out|disconnect|result is unknown/i.test(String(error.message))) throw error;
        this.progress('Install reply was lost; checking the running firmware');
      }
      this.requireActive();
      let freshSession = false;
      if (rebootChannel.change) {
        await this.reconnectChannel(rebootChannel);
        freshSession = true;
      }
      const deadline = Date.now() + 300000;
      while (Date.now() < deadline && !this.cancelled) {
        await sleep(this.pollMs);
        this.requireActive();
        try {
          // The target reboot loses its learned return path. Reset and prove
          // once, rather than using a cached session or sending two logins.
          if (freshSession && !this.link.browserRawSource) await this.link.verifyPeer(this.key, this.password);
          else if (typeof this.link.resetRemoteSession === 'function') await this.link.resetRemoteSession(this.key, this.password);
          else await this.link.verifyPeer(this.key, this.password);
          freshSession = false;
          const d = checks.destination(await this.remote('ota status'), await this.remote('ota self'), await this.remote('ver'));
          if (d.targetId === this.info.targetId && d.hwId === this.info.hwId && d.version === this.info.fwVersion && d.baseHash === this.bodyHash) {
            await this.recordVerifiedInstall(id); return d;
          }
        } catch (error) {
          if (error?.otaRestorationVerification) throw error;
          // Target reboot may take time; a failed USB stream needs reconnect.
          this.requireActive();
        }
      }
      this.requireActive();
      throw Error('Could not confirm the installed firmware; inspect the target before retrying');
    }
    stop() {
      this.cancelled = true;
      this.pollWake?.();
      return this.stopOwnedSource();
    }
    stopOwnedSource() {
      if (this.stopServingTask) return this.stopServingTask;
      // Only this session's package may be detached. In particular, stopping
      // investigation or preparation must not change somebody else's source.
      const starting = this.serveTask;
      if (!this.serving && !starting) return Promise.resolve();
      // A successful serve owns this package, even if Stop arrived during its
      // bounded USB handoff. Rejected foreign/busy serves grant no ownership.
      this.stopServingTask = Promise.resolve(starting).catch(() => {}).then(() => {
        if (this.serving) return this.link.stopServing();
      }).then(
        () => { this.serving = false; },
        error => {
          this.serving = false;
          // UI callers intentionally need not await Stop. Report an uncertain
          // local detach without replaying it or cancelling the target fetch.
          try { this.progress('Could not detach the package: ' + error.message); } catch (_) {}
        });
      return this.stopServingTask;
    }
  }
  return { DirectSession, temp, download, policy, expectedBody, installHash, rememberInstallCapability, clearInstallCapability };
});
