/* Browser-compatible detools application deltas for ESP32 and nRF52. */
(function(root, factory) {
  const node = typeof module === 'object' && module.exports;
  const scriptUrl=!node&&root.document?.currentScript?.src?new URL(root.document.currentScript.src):null;
  const workerUrl=scriptUrl?new URL('lora_ota_delta_worker.js'+scriptUrl.search,scriptUrl).href:null;
  const api = factory(node ? require('./vendor/detools-js/bsdiff.js') : root.MeshCoreDetoolsBsdiff,
    node ? require('./vendor/detools-js/pack-size.js') : root.MeshCoreDetoolsPack,
    node ? require('./vendor/detools-js/crle.js') : root.MeshCoreDetoolsCrle,
    node ? require('./lora_ota_package.js') : root.MeshCoreMotaPackage, workerUrl,
    node ? require('./lora_ota_checks.js') : root.LoraOtaChecks);
  if (node) module.exports = api; else root.MeshCoreLoraDelta = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function(bsdiff, pack, crle, mota, workerUrl, checks) {
  'use strict';
  const MAX_IMAGE = 4 * 1024 * 1024;
  function join(parts) {
    const size = parts.reduce((sum, p) => sum + p.length, 0);
    const out = new Uint8Array(size); let n = 0;
    for (const part of parts) { out.set(part, n); n += part.length; }
    return out;
  }
  const same = (a,b) => a.length === b.length && a.every((v,i) => v === b[i]);
  async function hash(data) {
    const subtle = globalThis.crypto?.subtle || (typeof require === 'function' ? require('node:crypto').webcrypto.subtle : null);
    if (!subtle) throw new Error('Web Crypto unavailable');
    return new Uint8Array(await subtle.digest('SHA-256', data));
  }
  async function tree(leaves) {
    if (!leaves.length) throw new Error('Empty delta');
    while (leaves.length > 1) {
      const next = [];
      for (let i = 0; i < leaves.length; i += 2)
        next.push(i + 1 === leaves.length ? leaves[i] : (await hash(join([leaves[i], leaves[i + 1]]))).slice(0, 4));
      leaves = next;
    }
    return leaves[0];
  }
  function controlData(base, target, index) {
    const parts = [pack.packSize(0)];
    for (const c of bsdiff.createControl(base, target, index))
      parts.push(pack.packSize(c.diff.length), c.diff, pack.packSize(c.extra.length), c.extra, pack.packSize(c.adjustment));
    return join(parts);
  }
  function sequential(base, target) {
    if (!(base instanceof Uint8Array) || !(target instanceof Uint8Array) || !base.length || !target.length ||
        base.length > MAX_IMAGE || target.length > MAX_IMAGE) throw new Error('Firmware image size is unsupported');
    // Header 0x02 is sequential + CRLE. The new size remains uncompressed.
    return join([Uint8Array.of(2), pack.packSize(target.length), crle.compress(controlData(base, target))]);
  }
  function applySequential(base, patch) {
    if (!(base instanceof Uint8Array) || !(patch instanceof Uint8Array) || !base.length || base.length > MAX_IMAGE || patch[0] !== 2)
      throw new Error('Invalid sequential CRLE delta');
    const prefix = new pack.ByteReader(patch, 1), outputSize = pack.unpackSize(prefix);
    if (!Number.isSafeInteger(outputSize) || outputSize <= 0 || outputSize > MAX_IMAGE) throw new Error('Invalid delta result size');
    const raw = crle.decompress(patch.subarray(prefix.position)), r = new pack.ByteReader(raw);
    if (pack.unpackSize(r) !== 0) throw new Error('Delta contains an unsupported data transform');
    const output = new Uint8Array(outputSize); let out = 0, from = 0;
    const size = () => {
      const n = pack.unpackSize(r);
      if (!Number.isSafeInteger(n)) throw new Error('Invalid delta integer');
      return n;
    };
    while (out < outputSize) {
      const n = size();
      if (n < 0 || from < 0 || from + n > base.length || out + n > outputSize || r.position + n > raw.length)
        throw new Error('Invalid delta diff range');
      for (let j = 0; j < n; j++) output[out + j] = (base[from + j] + raw[r.position + j]) & 255;
      r.position += n; out += n; from += n;
      const extra = size();
      if (extra < 0 || out + extra > outputSize || r.position + extra > raw.length) throw new Error('Invalid delta extra range');
      output.set(raw.subarray(r.position, r.position + extra), out); r.position += extra; out += extra;
      from += size();
    }
    if (r.position !== raw.length) throw new Error('Trailing delta data');
    return output;
  }
  async function verifiedPair(baseBytes, targetBytes, options = {}) {
    const [base, target] = await Promise.all([mota.parseEndF(baseBytes), mota.parseEndF(targetBytes)]);
    if (!base.targetId || !target.targetId || !base.hwId || !target.hwId || base.targetId !== target.targetId || base.hwId !== target.hwId)
      throw new Error('Base and target must have matching, nonempty board and role identities');
    if (target.fwVersion < base.fwVersion || (target.fwVersion === base.fwVersion && options.allowSameVersion !== true))
      throw new Error('Target firmware must be newer than the base');
    if (same(base.imageHash, target.imageHash)) throw new Error('The target image is already installed');
    return [base, target];
  }
  async function assemble(base, target, payload, codecId, options) {
    const blockSize = options.blockSize === undefined ? 1024 : options.blockSize;
    if (!Number.isInteger(blockSize) || blockSize < 2 || blockSize > 2048 || (blockSize & (blockSize - 1))) throw new Error('Invalid mOTA block size');
    const count = Math.ceil(payload.length / blockSize);
    if (!count || count > 65535) throw new Error('Delta needs an unsupported number of blocks');
    const leaves = [];
    for (let i = 0; i < payload.length; i += blockSize) leaves.push((await hash(payload.subarray(i, i + blockSize))).slice(0,4));
    const start = 205 + count * 4, blob = new Uint8Array(start + payload.length + 5), view = new DataView(blob.buffer);
    blob.set([109,79,84,65]); view.setUint32(4, blob.length, true);
    blob[8] = 2; blob[9] = 0; blob[10] = 0x12;
    view.setUint32(11, target.targetId, true); view.setUint32(15, target.fwVersion, true);
    view.setUint32(19, target.imageSize, true); view.setUint32(23, payload.length, true);
    blob[27] = Math.log2(blockSize); blob.set(await tree(leaves), 28); blob.set(target.imageHash, 32); blob[64] = codecId;
    const hw = new TextEncoder().encode(target.hwId);
    if (hw.length > 32) throw new Error('Hardware ID too long');
    blob.set(hw, 65); blob.set(base.bodyHash, 97); blob.fill(255, 201, 205);
    leaves.forEach((leaf, i) => blob.set(leaf, 205 + i * 4)); blob.set(payload, start); blob.set([118,107,52,57,54], blob.length - 5);
    const verified = await mota.parseMota(blob);
    if (!same(verified.baseHash, base.bodyHash) || !same(verified.imageHash, target.imageHash)) throw new Error('Delta manifest validation failed');
    return {blob, payload, baseHash:base.bodyHash, targetHash:target.imageHash, target, blockSize};
  }
  async function buildSequentialDelta(baseBytes, targetBytes, options = {}) {
    const [base, target] = await verifiedPair(baseBytes, targetBytes, options);
    const payload = sequential(base.image, target.image);
    if (!same(applySequential(base.image, payload), target.image)) throw new Error('Generated delta did not reconstruct the target');
    return assemble(base, target, payload, 1, options);
  }
  const PAGE = 4096;
  function nrfLayout(image) {
    const start = image.length - 56 - 24;
    if (start < 0 || String.fromCharCode(...image.subarray(start,start+8)) !== 'mOTALay1') throw new Error('Both nRF52 images need a verified mOTALay1 record');
    const v = new DataView(image.buffer, image.byteOffset + start, 24), flags = v.getUint8(9);
    const app = v.getUint32(12,true), end = v.getUint32(16,true), ceiling = v.getUint32(20,true);
    // Limit to the fixed internal-flash layout. A hybrid-capable build is
    // handled conservatively as entirely flash-staged, not assumed to retain RAM.
    const validInternal = ([0,32].includes(flags) && [0xD4000,0xED000].includes(end) && ceiling === 0xED000) ||
      (flags === 2 && [0xD4000,0xED000].includes(end) && ceiling === 0xD4000) ||
      ([16,48].includes(flags) && end === 0xED000 && ceiling === 0xED000);
    const validQspi = (flags === 4 && [0xD4000,0xED000].includes(end) && ceiling === 0xED000) ||
      (flags === 12 && end === 0xE0000 && ceiling === 0xED000);
    if (v.getUint8(8) !== 1 || v.getUint16(10,true) !== 24 || !(validInternal || validQspi) ||
        ![0x26000,0x27000].includes(app) || image.length > Math.min(end,ceiling)-app)
      throw new Error('Unsupported nRF52 flash layout');
    return {app, end, ceiling, flags};
  }
  function inPlace(base, target, memorySize, sourceIndex) {
    const shift = Math.max(memorySize - Math.ceil(base.length / PAGE) * PAGE, 2 * PAGE);
    if (base.length > memorySize - shift || target.length > memorySize) throw new Error('Firmware does not fit the in-place workspace');
    const shifted = base.subarray(0, memorySize - shift), parts = [];
    const index=sourceIndex||bsdiff.qsufsort(shifted,shifted.length),suffix=new Int32Array(index.length);
    for (let offset = 0; offset < target.length; offset += PAGE) {
      const from = Math.max(offset + PAGE - shift, 0);
      const start=Math.min(from,shifted.length);let count=0;
      for(const position of index)if(position>=start)suffix[count++]=position-start;
      parts.push(controlData(shifted.subarray(start), target.subarray(offset, offset + PAGE),suffix.subarray(0,count)));
    }
    return join([Uint8Array.of(0x12), pack.packSize(memorySize), pack.packSize(PAGE), pack.packSize(shift),
      pack.packSize(base.length), pack.packSize(target.length), crle.compress(join(parts))]);
  }
  function applyInPlace(base, patch) {
    if (!(base instanceof Uint8Array) || !(patch instanceof Uint8Array) || patch[0] !== 0x12) throw new Error('Invalid in-place delta');
    const h = new pack.ByteReader(patch,1), memorySize=pack.unpackSize(h), segment=pack.unpackSize(h), shift=pack.unpackSize(h),
      fromSize=pack.unpackSize(h), toSize=pack.unpackSize(h);
    if (![memorySize,segment,shift,fromSize,toSize].every(Number.isSafeInteger) || segment !== PAGE || memorySize <= 0 ||
        memorySize > 0xC7000 || memorySize % PAGE || shift < 2*PAGE || shift % PAGE || fromSize !== base.length ||
        fromSize > memorySize-shift || toSize <= 0 || toSize > memorySize) throw new Error('Invalid in-place geometry');
    const mem = new Uint8Array(memorySize); mem.fill(255); mem.set(base); mem.set(base.subarray(0, memorySize-shift),shift);
    const raw = crle.decompress(patch.subarray(h.position)), r = new pack.ByteReader(raw);
    const size = () => { const n=pack.unpackSize(r); if(!Number.isSafeInteger(n)) throw new Error('Invalid delta integer'); return n; };
    for (let off=0;off<toSize;off+=PAGE) {
      const end=Math.min(off+PAGE,toSize); let out=off, from=Math.max(off+PAGE,shift);
      if (size() !== 0) throw new Error('Delta contains an unsupported data transform');
      while (out<end) {
        const n=size();
        if(n<0||from<0||from+n>memorySize||out+n>end||r.position+n>raw.length) throw new Error('Invalid in-place diff range');
        for(let j=0;j<n;j++) mem[out+j]=(mem[from+j]+raw[r.position+j])&255;
        from+=n;out+=n;r.position+=n;
        const extra=size();
        if(extra<0||out+extra>end||r.position+extra>raw.length) throw new Error('Invalid in-place extra range');
        mem.set(raw.subarray(r.position,r.position+extra),out);out+=extra;r.position+=extra;from+=size();
      }
    }
    if(r.position!==raw.length) throw new Error('Trailing delta data');
    return mem.slice(0,toSize);
  }
  async function buildInPlaceDelta(baseBytes, targetBytes, options={}) {
    const [base,target]=await verifiedPair(baseBytes,targetBytes,options), a=nrfLayout(base.image), b=nrfLayout(target.image);
    if(a.app!==b.app||a.end!==b.end||a.ceiling!==b.ceiling||a.flags!==b.flags) throw new Error('nRF52 flash layouts differ');
    const qspi=options.runtimeStorage==='qspi';
    if((a.flags & 32) && !['internal','qspi'].includes(options.runtimeStorage))
      throw new Error('Adaptive nRF52 storage must be confirmed internal or QSPI before preparing this delta');
    if((a.flags & 4) && !qspi) throw new Error('This nRF52 layout requires confirmed QSPI storage');
    if(qspi && !(a.flags & (4|32))) throw new Error('This nRF52 layout does not permit QSPI staging');
    let usableCeiling;
    if(qspi) {
      if(options.runtimeQspiApply!==true || !Number.isInteger(options.runtimeStorageBytes) || options.runtimeStorageBytes<=0)
        throw new Error('Confirm QSPI apply support and usable storage capacity before preparing this delta');
      // The linked application end never exceeds this layout's apply ceiling,
      // and also preserves QSPI bootloader scratch where present.
      usableCeiling=a.end;
    } else {
      const runtimeCeiling=options.runtimeStageCeiling;
      if(!Number.isInteger(runtimeCeiling)||runtimeCeiling%PAGE||runtimeCeiling<=a.app||runtimeCeiling>0xED000)
        throw new Error('Confirm the installed bootloader staging ceiling before preparing an in-place update');
      usableCeiling=Math.min(a.ceiling,runtimeCeiling);
    }
    const align=n=>Math.ceil(n/PAGE)*PAGE;
    const minimum=Math.max(align(base.imageSize)+2*PAGE,align(target.imageSize));
    const spaceError = (workspace, staged) => {
      const available=usableCeiling-a.app, required=workspace+staged;
      const kb=n=>(n/1024).toFixed(1).replace(/\.0$/,'')+' KiB';
      return Object.assign(new Error('This update needs '+kb(required)+' of flash, but '+kb(available)+
        ' is available ('+kb(workspace)+' rewrite workspace + '+kb(staged)+' staged package in flash). '+
        'No firmware was uploaded. An intermediate update is needed.'),
      {code:'OTA_DELTA_SPACE',otaEndpoint:'Browser',otaStep:'Build differential package in this browser',
        otaLocalCheck:true,space:{workspace,staged,required,available}});
    };
    if(minimum>usableCeiling-a.app || target.imageSize>usableCeiling-a.app) throw spaceError(minimum,0);
    if(options.memorySize!==undefined&&(!Number.isInteger(options.memorySize)||options.memorySize%PAGE||options.memorySize<minimum||options.memorySize>usableCeiling-a.app))
      throw Error('Invalid requested in-place workspace');
    const maximum=usableCeiling-a.app-(qspi?0:PAGE);
    const pages=options.searchWorkspaces==='deep'?Array.from({length:Math.max(0,Math.floor((maximum-minimum)/PAGE)+1)},(_,i)=>i):[0,1,2,4,8,16,32];
    const index=bsdiff.qsufsort(base.image,base.image.length),choices=options.memorySize!==undefined?[options.memorySize]:
      options.searchWorkspaces?pages.map(p=>minimum+p*PAGE).filter(n=>n<=maximum):[minimum];
    let best=null,failure=null;
    for(const memorySize of choices){
      options.progress?.({phase:'workspace',index:choices.indexOf(memorySize)+1,total:choices.length});
      const payload=inPlace(base.image,target.image,memorySize,index);
      if(!same(applyInPlace(base.image,payload),target.image)) throw new Error('Generated in-place delta did not reconstruct target');
      const result=await assemble(base,target,payload,2,options);
      if(205+Math.ceil(payload.length/result.blockSize)*4>PAGE) throw new Error('Delta metadata exceeds the first staging page');
      const stagedFlash=options.runtimeHybridRam==null ? align(result.blob.length) :
        checks.stagedFlashBytes(result.blob.length,a.flags,usableCeiling,options.runtimeHybridRam);
      if(qspi){
        if(result.blob.length>options.runtimeStorageBytes){failure=Error('Delta exceeds the confirmed QSPI storage capacity');continue;}
      }else if(memorySize+stagedFlash>usableCeiling-a.app){
        const error=spaceError(memorySize,stagedFlash);if(!failure||error.space.required<failure.space.required)failure=error;continue;
      }
      if(!best||result.blob.length<best.blob.length)best={...result,memorySize,layout:a,stagedFlashBytes:stagedFlash};
      if(!['data','deep'].includes(options.searchWorkspaces))break;
    }
    if(!best)throw failure||spaceError(minimum,PAGE);
    return best;
  }
  function buildInWorker(base, target, options = {}) {
    if (typeof Worker !== 'function') return Promise.reject(new Error('Browser workers are unavailable'));
    const url = options.workerUrl || workerUrl;
    if (!url) return Promise.reject(new Error('Worker URL is unavailable'));
    if (!(base instanceof Uint8Array) || !(target instanceof Uint8Array)) return Promise.reject(new Error('Two firmware byte arrays are required'));
    return new Promise((resolve, reject) => {
      const worker = new Worker(url), id = 1, signal = options.signal;
      let done = false;
      const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); worker.terminate(); };
      const fail = error => { if (!done) { done = true; cleanup(); reject(error); } };
      const abort = () => fail(Object.assign(new Error('Differential preparation cancelled. No firmware was uploaded.'),
        {otaLocalCheck:true,otaEndpoint:'Browser',otaStep:'Prepare differential package'}));
      const timer = setTimeout(() => fail(new Error('Differential preparation timed out')), options.timeoutMs || 120000);
      if (signal?.aborted) return abort();
      signal?.addEventListener('abort', abort, {once:true});
      worker.onerror = event => fail(new Error(event.message || 'Differential worker failed'));
      worker.onmessage = event => {
        const result = event.data;
        if (!result || result.id !== id) return;
        if (result.phase === 'building') { options.progress?.('building'); return; }
        if(result.phase==='workspace'&&Number.isInteger(result.index)&&Number.isInteger(result.total)&&result.index>=1&&result.index<=result.total&&result.total<=256){
          options.progress?.({phase:result.phase,index:result.index,total:result.total});return;
        }
        if (result.phase !== 'complete' || !(result.package instanceof ArrayBuffer)) {
          const error=new Error(result.error || 'Invalid differential worker response');
          if(result.code==='OTA_DELTA_SPACE'){
            Object.assign(error,{code:result.code,otaEndpoint:'Browser',otaStep:'Build differential package in this browser',otaLocalCheck:true});
            const s=result.space;
            if(s&&['workspace','staged','required','available'].every(k=>Number.isSafeInteger(s[k])&&s[k]>=0&&s[k]<=64*1024*1024)&&
                s.required===s.workspace+s.staged)error.space={workspace:s.workspace,staged:s.staged,required:s.required,available:s.available};
          }
          return fail(error);
        }
        if (!done) { done = true; cleanup(); resolve({blob:new Uint8Array(result.package), payloadBytes:result.payloadBytes,
          baseHash:new Uint8Array(result.baseHash), targetHash:new Uint8Array(result.targetHash), memorySize:result.memorySize}); }
      };
      const a = Uint8Array.from(base).buffer, b = Uint8Array.from(target).buffer;
      try { worker.postMessage({id, base:a, target:b, blockSize:options.blockSize, mode:options.mode || 'sequential',allowSameVersion:options.allowSameVersion===true,
        searchWorkspaces:options.searchWorkspaces,memorySize:options.memorySize,
        runtimeStageCeiling:options.runtimeStageCeiling, runtimeStorage:options.runtimeStorage,
        runtimeQspiApply:options.runtimeQspiApply, runtimeStorageBytes:options.runtimeStorageBytes,runtimeHybridRam:options.runtimeHybridRam}, [a,b]); }
      catch (error) { fail(error); }
    });
  }
  return {sequential, applySequential, buildSequentialDelta, nrfLayout, inPlace, applyInPlace, buildInPlaceDelta, buildInWorker};
});
