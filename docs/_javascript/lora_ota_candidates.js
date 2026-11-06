/* Offline, bounded preparation of candidate firmware-update chains. */
(function (root, factory) {
  const node = typeof module === 'object' && module.exports;
  const api = factory(node ? require('./lora_ota_package.js') : root.MeshCoreMotaPackage,
    node ? require('./lora_ota_delta.js') : root.MeshCoreLoraDelta,
    node ? require('./lora_ota_checks.js') : root.LoraOtaChecks,
    node ? require('./lora_ota_plan.js') : root.LoraOtaPlan);
  if (node) module.exports = api; else root.LoraOtaCandidates = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (mota, delta, checks, planner) {
  'use strict';
  const MAX_IMAGES = 8, MAX_IMAGE_BYTES = 4 * 1024 * 1024, MAX_TOTAL_BYTES = 16 * 1024 * 1024;
  const MAX_PACKAGES = 32 * 1024 * 1024;
  function cryptoApi() {
    const api = globalThis.crypto?.subtle || (typeof require === 'function' ? require('node:crypto').webcrypto.subtle : null);
    if (!api) throw Error('Web Crypto is unavailable');
    return api;
  }
  async function sha(bytes) { return checks.hex(new Uint8Array(await cryptoApi().digest('SHA-256', bytes))); }
  function cancelled(signal) { if (signal?.aborted) throw Error('Update-path preparation cancelled'); }
  function validateCaps(c) {
    return c?.otaConfirmed === true && typeof c.nrf === 'boolean' && typeof c.external === 'boolean' &&
      typeof c.sd === 'boolean' && [1024, 2048].includes(c.maxBlock) && Array.isArray(c.codecs) &&
      c.codecs.every(x => [0,1,2].includes(x));
  }
  function nrfOptions(c) {
    return {runtimeStageCeiling:c.runtimeStageCeiling, runtimeStorage:c.runtimeStorage,
      runtimeQspiApply:c.runtimeQspiApply, runtimeStorageBytes:c.runtimeStorageBytes};
  }
  async function encodedSizes(info, caps, encode, signal) {
    if (caps.deflate !== true || !encode || caps.wireProfile === 'legacy') return undefined;
    const result = [];
    for (let off=0; off<info.payload.length; off+=info.blockSize) {
      cancelled(signal);
      const raw = info.payload.subarray(off, Math.min(off+info.blockSize, info.payload.length));
      let length = raw.length;
      try { const compressed = new Uint8Array(await encode(raw)); if (compressed.length && compressed.length < length) length=compressed.length; }
      catch (_) { return undefined; } // wire compression is optional
      result.push(length);
    }
    return result;
  }
  async function prepare(options, dependencies = {}) {
    const {images, current, goal, signal, progress = () => {}} = options || {};
    if (!Array.isArray(images) || images.length < 2 || images.length > MAX_IMAGES || !current)
      throw Error('Choose the installed image, a destination, and at most six intermediate images');
    const identifiers = new Set(); let total = 0;
    for (const entry of images) {
      if (!entry || typeof entry.id !== 'string' || !entry.id || identifiers.has(entry.id) || !(entry.bytes instanceof Uint8Array))
        throw Error('Every firmware image needs a unique ID and byte array');
      identifiers.add(entry.id); total += entry.bytes.byteLength;
      if (entry.bytes.byteLength > MAX_IMAGE_BYTES || total > MAX_TOTAL_BYTES)
        throw Error('Firmware candidates exceed the browser preparation memory limit');
    }
    if (!identifiers.has(current.node) || !identifiers.has(goal)) throw Error('Select a known installed image and destination');
    const parsed = [];
    for (const entry of images) {
      cancelled(signal); progress('Checking firmware ' + entry.id);
      const f = await mota.parseEndF(entry.bytes);
      if (!f.targetId || !f.hwId || !f.fwVersion) throw Error('Every image must identify its board, role and version');
      parsed.push({id:entry.id, firmware:f, capabilities:entry.capabilities || {}});
    }
    const initial = parsed.find(x => x.id === current.node);
    if (!initial || initial.firmware.targetId !== current.targetId || initial.firmware.hwId !== current.hwId ||
        initial.firmware.fwVersion !== current.version || checks.hex(initial.firmware.bodyHash) !== String(current.bodyHash || '').toLowerCase() ||
        (current.imageHash && checks.hex(initial.firmware.imageHash) !== String(current.imageHash).toLowerCase()))
      throw Error('The installed image does not match the live target identity and body hash');
    if (!validateCaps(initial.capabilities)) throw Error('Confirm the installed target OTA, codec and storage capabilities first');
    const nodes = parsed.map(x => ({id:x.id, version:x.firmware.fwVersion, targetId:x.firmware.targetId,
      hwId:x.firmware.hwId, bodyHash:checks.hex(x.firmware.bodyHash), imageHash:checks.hex(x.firmware.imageHash),
      validated:true, capabilities:{...x.capabilities,
        // Unknown negotiation is always estimated using the deployed legacy geometry.
        wireProfile:x.capabilities.wireConfirmed === true ? x.capabilities.wireProfile : 'legacy'}}));
    const warnings = [], packages = new Map(), edges = [], fullCache = new Map(); let retained = 0, complete = true;
    const buildDelta = dependencies.buildDelta || ((a,b,o) => delta.buildInWorker(a,b,o));
    const encode = dependencies.compressBlock;
    const relays = options.relays ?? 0;
    const kinds = options.kinds || ['full', 'delta'];
    if (!Array.isArray(kinds) || !kinds.length || kinds.some(k => !['full','delta'].includes(k)))
      throw Error('Invalid update candidate types');
    for (const from of parsed) {
      cancelled(signal);
      const c = from.capabilities;
      if (!validateCaps(c)) {
        if (from.id !== goal) { complete = false; warnings.push('Skipping ' + from.id + ' as an intermediate: its OTA/apply capabilities are not confirmed'); }
        continue;
      }
      const profile = c.wireConfirmed === true && ['legacy','v2','direct'].includes(c.wireProfile) &&
        (c.wireProfile !== 'direct' || !relays) ? c.wireProfile : 'legacy';
      if (profile === 'legacy' && !(c.wireConfirmed === true && c.wireProfile === 'legacy'))
        warnings.push('Estimating transfers from ' + from.id + ' with legacy framing; newer wire capabilities are not confirmed');
      const blockSize = profile !== 'legacy' && c.maxBlock >= 2048 ? 2048 : 1024;
      const sourceNode = nodes.find(x=>x.id===from.id);
      sourceNode.capabilities = {...sourceNode.capabilities, wireProfile:profile,
        deflate:profile !== 'legacy' && c.deflate === true};
      const dest = {targetId:from.firmware.targetId, hwId:from.firmware.hwId, version:from.firmware.fwVersion,
        baseHash:checks.hex(from.firmware.bodyHash), nrf:c.nrf, external:c.external, sd:c.sd,
        maxBlock:c.maxBlock, codecs:c.codecs.reduce((bits,v)=>bits | (1<<v),0)};
      for (const to of parsed) {
        if (to.firmware.fwVersion <= from.firmware.fwVersion || to.firmware.targetId !== from.firmware.targetId || to.firmware.hwId !== from.firmware.hwId) continue;
        for (const kind of kinds) {
          cancelled(signal);
          const codec = kind === 'full' ? 0 : c.nrf ? 2 : 1;
          if (!c.codecs.includes(codec)) continue;
          progress('Preparing ' + kind + ' candidate ' + from.id + ' → ' + to.id);
          try {
            let info, blob, memorySize, inplacePlan = null;
            if (kind === 'full') {
              const cacheKey = to.id + ':' + blockSize;
              if (!fullCache.has(cacheKey)) fullCache.set(cacheKey, await mota.buildFullMota(to.firmware.image,{blockSize}));
              blob = fullCache.get(cacheKey);
            } else {
              if (c.nrf && !['internal','qspi'].includes(c.runtimeStorage))
                throw Error('Confirm whether the target uses internal or QSPI staging');
              const built = await buildDelta(from.firmware.image, to.firmware.image, {
                mode:c.nrf?'in-place':'sequential', blockSize, signal, ...nrfOptions(c)
              });
              blob = built.blob; memorySize = built.memorySize;
              if (c.nrf) {
                const layout=delta.nrfLayout(from.firmware.image);
                if (!Number.isInteger(memorySize) || memorySize <= 0) throw Error('The in-place worker did not report its flash workspace');
                // The application chooses the hybrid split for every larger
                // internal package. A compact all-flash fit does not prove the
                // installed bootloader has the required MOTARAMA capability.
                if (c.runtimeStorage === 'internal' && (layout.flags & 0x10) && blob.length > 4096)
                  throw Error('Hybrid internal packages over 4096 bytes require bootloader MOTARAMA proof, which runtime diagnostics do not provide');
                inplacePlan = c.runtimeStorage === 'qspi'
                  ? {storage:'qspi',app:layout.app,layoutEnd:layout.end,memorySize}
                  : {storage:'internal',app:layout.app,layoutCeiling:layout.ceiling,layoutFlags:layout.flags,memorySize};
              }
            }
            cancelled(signal);
            info = await mota.parseMota(blob);
            if (info.codecId !== codec || info.isFull !== (kind === 'full') || info.blockSize !== blockSize ||
                info.targetId !== to.firmware.targetId || info.hwId !== to.firmware.hwId || info.fwVersion !== to.firmware.fwVersion ||
                checks.hex(info.imageHash) !== checks.hex(to.firmware.imageHash) ||
                (kind === 'delta' && checks.hex(info.baseHash) !== checks.hex(from.firmware.bodyHash)))
              throw Error('Prepared package identity does not match its source and destination');
            checks.validate(info,dest,false);
            const hash = await sha(info.blob);
            if (!packages.has(hash)) {
              if (retained + info.blob.length > MAX_PACKAGES) throw Error('Candidate package memory limit reached');
              retained += info.blob.length; packages.set(hash,{blob:info.blob,info});
            }
            const sizes = await encodedSizes(info,sourceNode.capabilities,encode,signal);
            edges.push({from:from.id,to:to.id,kind,codec,payloadSize:info.payloadSize,blockSize,
              baseHash:checks.hex(from.firmware.bodyHash),targetImageHash:checks.hex(to.firmware.imageHash),
              packageHash:hash,verified:true,compatible:true,encodedBlockSizes:sizes,
              inplacePlan,
              nrfGeometry:kind === 'delta' && c.nrf ? {...nrfOptions(c),memorySize} : null});
          } catch (error) {
            cancelled(signal);
            complete = false;
            warnings.push('Skipped ' + kind + ' ' + from.id + ' → ' + to.id + ': ' + String(error?.message || error));
          }
        }
      }
    }
    cancelled(signal);
    let route;
    try {
      route = planner.planRoute({nodes,edges,current,goal,objective:options.objective || 'packets',relays,
        radio:options.radio,restartSeconds:options.restartSeconds,overheadFrames:options.overheadFrames});
    } catch (error) {
      if (warnings.length) throw Error(String(error?.message || error) + ': ' + warnings.join('; '));
      throw error;
    }
    warnings.push('Intermediate firmware OTA and storage capabilities must be confirmed again after each reboot');
    warnings.push('Only supplied images and successfully prepared packages were compared; an omitted candidate could be faster');
    const selected = route.steps.map(step => {
      const edge = edges.find(e=>e.from===step.from && e.to===step.to && e.packageHash===step.packageHash);
      return {...step,edge,package:packages.get(step.packageHash),
        sourceImage:parsed.find(image=>image.id===step.from).firmware.image,
        targetImage:parsed.find(image=>image.id===step.to).firmware.image};
    });
    const used = new Set(selected.map(x=>x.packageHash));
    for (const hash of packages.keys()) if (!used.has(hash)) packages.delete(hash);
    return {route,steps:selected,nodes,warnings,packages,complete};
  }
  async function checkStep(prepared, index, live) {
    const step = prepared?.steps?.[index];
    if (!step || !live || !validateCaps(live)) throw Error('Confirm the live target OTA, codec and storage capabilities before this step');
    const from = prepared.nodes.find(n=>n.id===step.from), to = prepared.nodes.find(n=>n.id===step.to);
    if (!from || !to || live.targetId !== from.targetId || live.hwId !== from.hwId || live.version !== from.version ||
        String(live.bodyHash || '').toLowerCase() !== from.bodyHash.toLowerCase() ||
        (live.imageHash && String(live.imageHash).toLowerCase() !== from.imageHash.toLowerCase()))
      throw Error('The live firmware no longer matches this planned step');
    if (await sha(step.package.blob) !== step.packageHash) throw Error('The planned package changed after preparation');
    const info = await mota.parseMota(step.package.blob);
    if (!live.codecs.includes(info.codecId)) throw Error('The live target does not confirm this package codec');
    const dest = {targetId:live.targetId,hwId:live.hwId,version:live.version,baseHash:String(live.bodyHash).toLowerCase(),
      nrf:live.nrf,external:live.external,sd:live.sd,maxBlock:live.maxBlock,
      codecs:live.codecs.reduce((bits,v)=>bits | (1<<v),0)};
    checks.validate(info,dest,false);
    if (live.nrf && info.codecId === 2) {
      if (!step.edge.nrfGeometry || !step.edge.inplacePlan)
        throw Error('The prepared nRF52 storage geometry is missing');
      const expected=step.edge.nrfGeometry;
      for (const k of ['runtimeStageCeiling','runtimeStorage','runtimeQspiApply','runtimeStorageBytes'])
        if (live[k] !== expected[k]) throw Error('The live nRF52 storage geometry differs from the prepared patch');
      if (!(step.sourceImage instanceof Uint8Array) || await sha(step.sourceImage) !== from.imageHash)
        throw Error('The source image used for nRF52 layout verification changed');
      const layout=delta.nrfLayout(step.sourceImage);
      const plan=step.edge.inplacePlan;
      if (plan.storage !== live.runtimeStorage || plan.app !== layout.app || plan.memorySize !== expected.memorySize ||
          (plan.storage === 'internal' && (plan.layoutCeiling !== layout.ceiling || plan.layoutFlags !== layout.flags)) ||
          (plan.storage === 'qspi' && plan.layoutEnd !== layout.end))
        throw Error('The prepared nRF52 workspace no longer matches the source image');
      if (live.runtimeStorage === 'internal' && (layout.flags & 0x10) && info.blob.length > 4096)
        throw Error('Hybrid internal packages over 4096 bytes require verified MOTARAMA support');
    }
    return {info,source:from,target:to};
  }
  return {prepare,checkStep,MAX_IMAGES,MAX_IMAGE_BYTES,MAX_TOTAL_BYTES};
});
