/* Shared, fail-closed checks for a browser LoRa updater. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaChecks = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const keyPattern = /^[0-9a-f]{64}$/i;
  function hex(bytes) { return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join(''); }
  function failureIdentityText(value,limit=160) {
    if(typeof value!=='string')return '';
    const text=value.replace(/(?:https?:\/\/|file:\/\/)[^\s<>]+/gi,'<address hidden>')
      .replace(/\b[a-z]:[\\/][^\s<>]+/gi,'<address hidden>')
      .replace(/\b(?:(?:10|127)\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|169\.254\.\d{1,3}\.\d{1,3})\b/g,'<address hidden>')
      .replace(/(?:\b(?:f[cd][a-f0-9]{2}|fe80):[a-f0-9:]+|::1\b)/gi,'<address hidden>')
      .replace(/\b[a-f0-9]{16,}\b/gi,'<key hidden>')
      .replace(/\b[A-Za-z0-9+/_=-]{32,}\b/g,'<value hidden>')
      .replace(/[!-~]{2}\|(?=[!-~])/g,'<request tag hidden>')
      .replace(/[^\x20-\x7e]/g,' ').replace(/\s+/g,' ').trim();
    return text.length>limit?text.slice(0,limit)+'...':text;
  }
  function failureIdentityValue(value) {
    if(typeof value!=='string'||value.length>2048||/[^\x20-\x7e\r\n\t]/.test(value))return '';
    const text=value.trim().replace(/^>\s*/, '').trim();
    if(!text||/^(?:err(?:or)?\b|unknown\b|unsupported\b|unrecognized\b|not supported\b|permission denied\b|access denied\b)/i.test(text))return '';
    return failureIdentityText(text);
  }
  // Reuse identity already read from this target in the selected session.
  // Best-effort diagnostics send one bounded request only for a missing field.
  // The supplied read owns its timeout and must not authenticate/reset/retry.
  // Await it directly so no abandoned request can outlive this collector.
  async function collectFailureIdentity({source={},target={},read,canContinue=()=>true,timeoutMs=5000}={}) {
    const sourceInfo={version:failureIdentityValue(source?.firmwareVersion),board:failureIdentityValue(source?.firmwareBoardName),origin:'cached USB identity'};
    const cachedTarget={version:failureIdentityValue(target?.version),board:failureIdentityValue(target?.board)};
    const result={source:sourceInfo,target:{}};
    const budget=Number.isFinite(timeoutMs)&&timeoutMs>0?Math.min(5000,Math.max(1,Math.floor(timeoutMs))):5000;
    const active=()=>{try{return typeof canContinue==='function'&&canContinue()===true;}catch(_){return false;}};
    let stopped=false;
    for(const [field,command] of [['version','ver'],['board','board']]){
      if(cachedTarget[field]){
        result.target[field]={command,origin:'cached target reply',attempted:false,value:cachedTarget[field]};continue;
      }
      if(stopped||!active()){
        stopped=true;result.target[field]={command,origin:'not available',attempted:false,error:'Connection changed or diagnostic stopped; no identity result retained'};continue;
      }
      if(typeof read!=='function'){
        result.target[field]={command,origin:'not available',attempted:false,error:'No safe read callback is available'};continue;
      }
      try{
        const response=await read(command,{timeoutMs:budget});
        if(!active()){
          stopped=true;result.target[field]={command,origin:'not available',attempted:true,error:'Connection changed or diagnostic stopped; reply discarded'};continue;
        }
        const value=failureIdentityValue(response);
        result.target[field]=value?{command,origin:'fresh read',attempted:true,value}
          :{command,origin:'not available',attempted:true,error:'The '+command+' reply was empty, rejected or not a usable identity value'};
      }catch(error){
        if(!active())stopped=true;
        const reason=stopped?'Connection changed or diagnostic stopped; no identity result retained'
          :failureIdentityText(typeof error==='string'?error:error?.message,240)||'Diagnostic read failed without a reason';
        result.target[field]={command,origin:'not available',attempted:true,error:reason};
      }
    }
    return result;
  }
  // Error context is for display only. Never rewrite the original message or
  // transport flags: timeout/retry and uncertain-install decisions use them.
  function errorDetails(error, {step='', secrets=[]} = {}) {
    const hidden='<hidden>', argumentsHidden='<arguments hidden>';
    const privateValues=Array.isArray(secrets) ? [...new Set(secrets.filter(value=>typeof value==='string'&&value.length))].sort((a,b)=>b.length-a.length) : [];
    const redact=value=>{
      let result=typeof value==='string'?value:'';
      for(const secret of privateValues)result=result.split(secret).join(hidden);
      return result;
    };
    const safeText=(value,limit=160,multiline=false)=>{
      // Remove secrets before truncating; cutting first could expose a prefix.
      let text=redact(value).replace(/(?:https?:\/\/|file:\/\/)[^\s<>]+/gi,'<address hidden>')
        .replace(/\b[a-z]:[\\/][^\s<>]+/gi,'<address hidden>')
        .replace(/\b(?:(?:10|127)\.(?:\d{1,3}\.){2}\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|169\.254\.\d{1,3}\.\d{1,3})\b/g,'<address hidden>')
        .replace(/(?:\b(?:f[cd][a-f0-9]{2}|fe80):[a-f0-9:]+|::1\b)/gi,'<address hidden>')
        .replace(/\b[a-f0-9]{16,}\b/gi,'<key hidden>')
        .replace(/\b[A-Za-z0-9+/_=-]{32,}\b/g,'<value hidden>')
        .replace(/[!-~]{2}\|(?=[!-~])/g,'<request tag hidden>');
      text=text.replace(multiline?/[^\x20-\x7e\n]/g:/[^\x20-\x7e]/g,' ');
      text=multiline?text.replace(/ +/g,' ').trim():text.replace(/\s+/g,' ').trim();
      return text.length>limit?text.slice(0,limit)+'...':text;
    };
    const safeCommand=value=>{
      const text=redact(value).trim();
      if(!text||text.length>1024||/[^\x20-\x7e]/.test(text))return '';
      // These fixed binary-operation names come from the USB transport, not
      // device text. Preserve them without accepting arbitrary prose as CLI.
      const usbOperations=new Set(['reading the Companion identity','sending an admin command',
        'reading the Companion clock','reading a queued message','reading device information',
        'sending an admin login','setting up the OTA session','sending a raw OTA packet',
        'waiting for a USB command reply','running a local CLI command']);
      if(usbOperations.has(text))return text;
      if(/^USB (?:binary )?command (?:[0-9]|[1-9][0-9]{1,2})$/.test(text))return text;
      if(/^(?:ver|version|board|clock|login|password|tempradio|tempradio2)$/.test(text))
        return /^(?:login|password)$/.test(text)?text+' '+argumentsHidden:text;
      if(/^get [a-z][a-z0-9_.]{0,47}$/.test(text))return text;
      if(/^get ota\.direct\.reply (?:[0-9]|[1-9][0-9]{1,4})$/.test(text))return text;
      if(/^ota (?:status|self|config|stats|installhash|ls|qspi|ram|folder|install|abort|cancel|stop)$/.test(text)
          ||/^ota bootloader status$/.test(text)||/^ota key list$/.test(text))return text;
      if(/^ota ls (?:[1-9]|[1-9][0-9]|1[01][0-9]|12[0-8])$/.test(text))return text;
      if(/^ota\.peer (?:status|path|timeout|radio|close)$/.test(text))return text;
      const setting=/^(get|set) ([a-z][a-z0-9_.]{0,47})(?: |$)/.exec(text);
      if(setting)return setting[1]+' '+setting[2]+' '+argumentsHidden;
      const ota=/^(ota(?:\.peer)?) ([a-z][a-z0-9_.]{0,31})(?: |$)/.exec(text);
      if(ota)return ota[1]+' '+ota[2]+' '+argumentsHidden;
      if(text==='CLI command '+argumentsHidden)return text;
      const verb=/^([a-z][a-z0-9_.-]{0,23})(?: |$)/i.exec(text);
      return verb?verb[1]+' '+argumentsHidden:'';
    };
    const detail=error&&(typeof error==='object'||typeof error==='function')?error:{};
    const action=safeText(step), stage=safeText(detail.otaStep);
    const lines=['Step: '+(action||stage||'not identified')];
    if(stage&&action&&stage!==action)lines.push('Stage: '+stage);
    const endpoint=safeText(detail.otaEndpoint,64);
    if(endpoint)lines.push('Endpoint: '+endpoint);
    const path=safeText(detail.otaPath,160);
    if(path)lines.push('Path: '+path);
    const radio=safeText(detail.otaRadio,160);
    if(radio)lines.push('Radio: '+radio);
    const command=safeCommand(detail.otaCommand);
    lines.push('Command: '+(detail.otaLocalCheck===true
      ?detail.otaLocalStateCheck===true?'none (local saved-state check)':'none (local browser calculation)'
      :command||'not identified for this failure'));
    const requestStep=safeText(detail.otaRequestStep),requestCommand=safeCommand(detail.otaRequestCommand);
    if(requestStep&&requestStep!==stage)lines.push('Request step: '+requestStep);
    if(requestCommand&&requestCommand!==command)lines.push('Request command: '+requestCommand);
    const requestRadio=safeText(detail.otaRequestRadio,160);
    if(requestRadio&&requestRadio!==radio)lines.push('Request radio: '+requestRadio);
    if(Number.isFinite(detail.otaTimeoutMs)&&detail.otaTimeoutMs>0&&detail.otaTimeoutMs<=86400000)
      lines.push('Timeout: '+Number((detail.otaTimeoutMs/1000).toFixed(3))+' s');
    if(Number.isInteger(detail.otaAttempt)&&detail.otaAttempt>0&&detail.otaAttempt<=1000){
      const maximum=Number.isInteger(detail.otaMaxAttempts)&&detail.otaMaxAttempts>=detail.otaAttempt&&detail.otaMaxAttempts<=1000?detail.otaMaxAttempts:null;
      lines.push('Attempt: '+detail.otaAttempt+(maximum?' of '+maximum:''));
    }
    const identity=detail.otaIdentity;
    if(identity&&typeof identity==='object'){
      const source=identity.source&&typeof identity.source==='object'?identity.source:{};
      for(const [field,label] of [['version','Source version'],['board','Source board']])
        lines.push(label+' (cached USB identity): '+(safeText(source[field])||'not available (not reported by USB identity)'));
      const target=identity.target&&typeof identity.target==='object'?identity.target:{};
      for(const [field,label] of [['version','Target version'],['board','Target board']]){
        const record=target[field]&&typeof target[field]==='object'?target[field]:{},value=safeText(record.value);
        const origin=record.origin==='fresh read'?'fresh read':record.origin==='cached target reply'?'cached earlier in this session; may predate update':'diagnostic value';
        lines.push(label+(value?' ('+origin+'): '+value:': not available ('+(safeText(record.error,240)||'not read')+')'));
      }
    }
    const preparation=detail.otaPreparation;
    if(preparation&&typeof preparation==='object'&&!Array.isArray(preparation)){
      // These dedicated image fields are calculated from firmware files, not
      // device keys. Validate each shape before retaining long release names
      // and complete hashes; generic free-text redaction would erase them.
      const object=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
      const addressText=value=>redact(value)
        .replace(/(?:https?:\/\/|file:\/\/)[^\s<>]+/gi,'<address hidden>')
        .replace(/(^|[^A-Za-z0-9])(?:\d{1,3}\.){3}\d{1,3}(?=$|[^A-Za-z0-9])/g,'$1<address hidden>')
        .replace(/(?:\b(?:f[cd][a-f0-9]{2}|fe80):[a-f0-9:]+|::1\b)/gi,'<address hidden>');
      const filename=value=>{
        if(typeof value!=='string'||value.length>2048||/[^\x20-\x7e]/.test(value))return 'not available';
        if(/(?:https?:\/\/|file:\/\/)/i.test(value))return '<address hidden>';
        // Strip local directories before displaying a file selected in the
        // browser or supplied by a desktop wrapper. Never retain a home path.
        const name=value.replace(/\\/g,'/').split('/').at(-1);
        if(!/^[A-Za-z0-9][A-Za-z0-9._() +@-]{0,511}\.(?:bin|zip|mota|uf2)$/i.test(name))return 'not available';
        return addressText(redact(value).replace(/\\/g,'/').split('/').at(-1));
      };
      const firmwareHash=(value,length)=>typeof value==='string'&&new RegExp('^[0-9a-f]{'+length+'}$','i').test(value)
        ?redact(value).toLowerCase():'not available';
      const packedVersion=value=>Number.isInteger(value)&&value>=0&&value<=0xffffffff
        ?redact([24,16,8,0].map(shift=>(value>>>shift)&255).join('.')):'not available';
      lines.push('Update preparation details:');
      for(const [field,label] of [['installed','Installed'],['selected','Selected']]){
        const image=object(preparation[field]);
        lines.push(label+' firmware file: '+filename(image.name));
        lines.push(label+' firmware hardware: '+(typeof image.hwId==='string'&&/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/.test(image.hwId)?addressText(safeText(image.hwId,64)):'not available'));
        lines.push(label+' firmware version: '+packedVersion(image.version));
        lines.push(label+' firmware image SHA-256: '+firmwareHash(image.imageHash,64));
        lines.push(label+' firmware body hash: '+firmwareHash(image.bodyHash,16));
      }
      const search=object(preparation.search),searchState=search.enabled===true?'enabled':search.enabled===false?'disabled':'not available';
      const depth=['standard','deep'].includes(search.depth)?search.depth:'not available';
      const objective=search.objective==='steps'?'fewest steps':search.objective==='data'?'least radio data':'not available';
      const candidates=Number.isInteger(search.candidates)&&search.candidates>=0&&search.candidates<=1000000?search.candidates:'not available';
      lines.push('Automatic search: '+redact(searchState+'; depth: '+depth+'; objective: '+objective+'; candidates: '+candidates));
      lines.push('Search result: '+(safeText(search.result,240)||'not available'));
      const ram=object(preparation.ram),ramState=ram.confirmed===true?'confirmed':ram.confirmed===false?'not confirmed':'not available';
      lines.push('Retained RAM support: '+ramState+(safeText(ram.reason,240)?' ('+safeText(ram.reason,240)+')':''));
      lines.push('Reported bootloader version: '+(safeText(ram.bootloaderVersion,160)||'not available'));
      lines.push('Reported bootloader identity: '+(safeText(ram.bootloaderStatus||ram.status,320)||'not available'));
      const space=object(preparation.space),bytes=value=>Number.isInteger(value)&&value>=0&&value<=0xffffffff;
      for(const [field,label] of [['workspace','Rewrite workspace'],['staged','Staged package in flash'],['required','Total flash required'],['available','Flash available']])
        lines.push(label+': '+(bytes(space[field])?redact(Math.ceil(space[field]/1024)+' KiB ('+space[field]+' bytes)'):'not available'));
    }
    const reason=safeText(typeof error==='string'?error:detail.message,2048,true)||'Unknown failure';
    lines.push('Reason: '+reason);
    return lines.join('\n');
  }
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
  function bare(reply) { return String(reply || '').trim().replace(/^>\s*/, ''); }
  function coldRakStatus(status) {
    const text = bare(status), hw = [...text.matchAll(/\bhw=([^ |]+)/gi)],
      target = [...text.matchAll(/\btarget:([0-9a-f]{8})\b/gi)],
      limits = [...text.matchAll(/\bmaxblk:(\d+)\b/gi)],
      stores = [...text.matchAll(/\bbl:([^ |]+)/gi)];
    if (!/^OTA\s*\|/.test(text) || hw.length !== 1 ||
        !['RAK_3401','RAK4631'].includes(hw[0][1]) || target.length !== 1 ||
        target[0][1] === '00000000' || limits.length !== 1 ||
        !['1024','2048'].includes(limits[0][1]) || stores.length !== 1 ||
        !['NONE','storage-ERR'].includes(stores[0][1]) ||
        text.split(' | ').filter(part => part === 'no download').length !== 1 ||
        /\bdownload:/i.test(text)) return null;
    return {hw:hw[0][1], target:target[0][1].toLowerCase(), limit:Number(limits[0][1])};
  }
  // The adaptive RAK store deliberately does not probe removable media for
  // idle status calls. Its explicit bootloader-status query prepares storage
  // without starting an update; bind that one query to the same live image.
  async function prepareColdRakStorage(remote, status, self) {
    status = bare(status); self = bare(self);
    const identity = coldRakStatus(status);
    const prefix = /^self body=([1-9]\d*) image=([1-9]\d*) base_hash=([0-9a-f]{16}) \| maxblk:(1024|2048)(?: abi=(\d+) codecs=0x([0-9a-f]+))? \| (.+)$/i;
    const before = prefix.exec(self);
    if (!identity || !before || before[7] !== 'storage unsafe: not probed')
      return {status,self,prepared:false};
    if (Number(before[4]) !== identity.limit) throw Error('Target reports conflicting block limits');
    const bootloader = bare(await remote('ota bootloader status'));
    const boot = /^BL board=239A0029 target=([0-9a-f]{8}) name=(3401(?:_AUTO)?_DFU|4631(?:_AUTO)?_DFU) crc=([0-9a-f]{8}) abi=(\d+) caps=([0-9a-f]{2}) \| staged:none mid=- hash=-$/i.exec(bootloader);
    const profiles = {
      RAK_3401: {'3401_DFU':['23818A80','0A'], '3401_AUTO_DFU':['D04AB3AB','1E']},
      RAK4631: {'4631_DFU':['2D0DF000','0A'], '4631_AUTO_DFU':['FEEAFD1B','1E']}
    };
    const expected = boot && profiles[identity.hw][boot[2].toUpperCase()];
    if (!boot || !expected || boot[1].toUpperCase() !== expected[0] ||
        boot[5].toUpperCase() !== expected[1] || /^(?:00000000|ffffffff)$/i.test(boot[3]) ||
        Number(boot[4]) < 3 || Number(boot[4]) >= 65535 ||
        (before[5] && (Number(before[5]) !== Number(boot[4]) || (parseInt(before[6],16) & 5) !== 5)))
      throw Error('Target bootloader identity or staging state could not be verified');
    const nextStatus = bare(await remote('ota status'));
    const nextSelf = bare(await remote('ota self'));
    const after = prefix.exec(nextSelf);
    const hw = [...nextStatus.matchAll(/\bhw=([^ |]+)/gi)], targets = [...nextStatus.matchAll(/\btarget:([0-9a-f]{8})\b/gi)],
      limits = [...nextStatus.matchAll(/\bmaxblk:(\d+)\b/gi)], stores = [...nextStatus.matchAll(/\bbl:([^ |]+)/gi)];
    if (!after || before.slice(1,7).some((v,i) => String(v || '').toLowerCase() !== String(after[i+1] || '').toLowerCase()) ||
        !/^OTA\s*\|/.test(nextStatus) || hw.length !== 1 || hw[0][1] !== identity.hw ||
        targets.length !== 1 || targets[0][1].toLowerCase() !== identity.target ||
        limits.length !== 1 || Number(limits[0][1]) !== identity.limit || stores.length !== 1 ||
        nextStatus.split(' | ').filter(part => part === 'no download').length !== 1 || /\bdownload:/i.test(nextStatus))
      throw Error('Target firmware or update state changed while checking storage');
    let backend;
    if (/^internal store:[1-9]\d*K \| bootloader: delta apply OK$/.test(after[7])) backend = 'internal';
    else if (/^QSPI store:[1-9]\d*K \| bootloader: QSPI apply OK$/.test(after[7])) backend = 'QSPI';
    else if (identity.hw === 'RAK4631' && after[7] === 'SD store | bootloader: authenticated SD apply OK') backend = 'SD';
    if (!backend || stores[0][1] !== backend) throw Error('Target update storage is not ready');
    return {status:nextStatus,self:nextSelf,prepared:true,bootloader};
  }
  // Used by manual package checks too, so skipping investigation does not
  // defer an explicit receiver rejection until after unrelated target reads.
  async function readReceiver(remote) {
    let status = bare(await remote('ota status'));
    const first = availability(status);
    const reject = result => { const error=Error(result.message);error.otaAvailability=result;throw error; };
    if (first.state==='unsupported' || (first.state==='unavailable' && !coldRakStatus(status)))reject(first);
    let self = bare(await remote('ota self'));
    ({status,self} = await prepareColdRakStorage(remote, status, self));
    const result = availability(status, self);
    if(result.state!=='supported')reject(result);
    return {status,self};
  }
  function validate(info, dest, verifiedSigner, options = {}) {
    if (!info || !dest || !(info.baseHash instanceof Uint8Array)) throw Error('Invalid package metadata');
    if (info.isBootloader) throw Error('Bootloader updates are not supported by this updater');
    if (info.signed && !verifiedSigner) throw Error('A signed package requires its full trusted signing key');
    if (info.targetId !== dest.targetId || info.hwId !== dest.hwId) throw Error('Package hardware or target does not match');
    if (info.fwVersion < dest.version || (info.fwVersion === dest.version &&
        !(options.allowSameVersion === true && !info.isFull && info.codecId === 2)))
      throw Error('Package is not newer than the installed firmware');
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
  function legacyHybridRam(status, version) {
    // Published GAT562 OTAFIX 2.4.12 image: manifest/CRC verified on-device.
    // Its SHA-256-pinned release ZIP is 86490cbca323a8b81fc8490f9a43fd06
    // db4c776bac6aa0bda91ee807cf801c60. The sole aligned MOTARAMA record
    // at bootloader offset 37648 is ABI 1 / record 72 / arena 65536.
    // Older applications cannot expose that record through `ota ram` yet.
    const proof='OTA RAM: hybrid=1 abi=1 record=72 arena=65536',text=typeof status==='string'?status.trim():'';
    if(/^(?:>[ \t]*)?BL board=239A0029 target=D50D2D44 name=GAT562_DFU crc=03F81FDD abi=3 caps=0A \| staged:none mid=-+ hash=-+$/i.test(text))return proof;
    // Official stable INTERNAL OTAFIX 2.4.6 introduced the 64 KiB arena;
    // verified releases through 2.4.12 retain that ABI/record geometry.
    // https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX/releases/tag/0.11.0-OTAFIX2.4.6
    // Introduction: f969518b3abe9fe38a787f0adba0fe1699a6b98a.
    // Do not extrapolate to preview/custom builds or the separate 2.5 line.
    const release=/^(?:>[ \t]*)?(?:v?0\.11\.0-)?OTAFIX2\.4\.(6|7|8|9|10|11|12)$/i.exec(typeof version==='string'?version.trim():'');
    const identity=/^(?:>[ \t]*)?BL board=([0-9a-f]{8}) target=([0-9a-f]{8}) name=([A-Za-z0-9_][A-Za-z0-9_.-]{0,62}) crc=([0-9a-f]{8}) abi=3 caps=0A \| staged:none mid=-+ hash=-+$/i.exec(text);
    if(release&&identity&&!identity.slice(1,3).concat(identity[4]).some(value=>/^(?:00000000|ffffffff)$/i.test(value))&&
        !/(?:^|[_-])(?:QSPI|SD)(?:[_-]|$)/i.test(identity[3]))return proof;
    throw Error('This older target cannot confirm retained RAM support; its bootloader does not match a verified published image or stable internal OTAFIX 2.4.6 through 2.4.12');
  }
  function ramCommandUnsupported(reply){
    return /^(?:>\s*)?(?:(?:err(?:or)?\s*:?\s*)?(?:unknown(?: OTA)? command|unsupported)\b)/i.test(String(reply).trim());
  }
  async function probeHybridRam(read) {
    const legacy=async()=>{
      const status=await read('ota bootloader status');
      try{return legacyHybridRam(status);}catch(_){/* exact CRC needs no version query */}
      return legacyHybridRam(status,await read('get bootloader.ver'));
    };
    let reply;
    try{reply=await read('ota ram');}
    catch(error){
      if(ramCommandUnsupported(error?.message))return legacy();
      throw error;
    }
    return ramCommandUnsupported(reply)?legacy():confirmHybridRam(reply);
  }
  async function readHybridRam(plan, info, getReply, getBootloader, getVersion) {
    if (!needsHybridRam(plan, info)) return null;
    return probeHybridRam(command=>{
      if(command==='ota ram')return getReply();
      if(command==='ota bootloader status'&&getBootloader)return getBootloader();
      if(command==='get bootloader.ver'&&getVersion)return getVersion();
      throw Error('This hybrid nRF52 package requires bootloader RAM capability that cannot be confirmed here');
    });
  }
  function stagedFlashBytes(size, flags, ceiling, hybridRamReply) {
    if(!Number.isSafeInteger(size)||size<=0)throw Error('Invalid staged package size');
    if((flags&16)&&size>4096&&hybridRamReply!==undefined&&hybridRamReply!==null){
      confirmHybridRam(hybridRamReply);
      if(ceiling!==0xED000)throw Error('Hybrid staging requires the expanded nRF52 flash layout');
      // Match mota_nrf52_hybrid_stage_plan exactly: one flash page minimum,
      // page-rounded prefix, and at most 64 KiB in the retained SRAM arena.
      return Math.max(4096,Math.ceil((size-65536)/4096)*4096);
    }
    return Math.ceil(size/4096)*4096;
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
        plan.memorySize + stagedFlashBytes(info.blob.length,plan.layoutFlags,ceiling,hybridRamReply) > ceiling - plan.app)
      throw Error('The current nRF52 staging space no longer fits this differential');
  }
  return { route, destination, availability, readReceiver, coldRakStatus, prepareColdRakStorage, validate, confirmSequentialTarget, isKnownEsp32, ensureSequentialTarget, internalStage, qspiCapacity, needsHybridRam, confirmHybridRam, legacyHybridRam, probeHybridRam, readHybridRam, stagedFlashBytes, validateInternalPlan, version, hex, failureAdvice, explicitMaxBlock, errorDetails, collectFailureIdentity };
});
