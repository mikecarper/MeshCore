/* Offline version-chain planning. Outputs ideal estimates, never an install authorization. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.LoraOtaPlan = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SHA = /^[0-9a-f]{64}$/i, BODY = /^[0-9a-f]{16}$/i;
  const ceil = (n, d) => Math.ceil(n / d);
  function siblingCount(count, index) {
    if (!Number.isInteger(count) || count < 1 || !Number.isInteger(index) || index < 0 || index >= count) throw Error('Invalid proof geometry');
    let n = 0;
    while (count > 1) {
      if (!(count % 2 && index === count - 1)) n++;
      index = Math.floor(index / 2); count = ceil(count, 2);
    }
    return n;
  }
  function airtime(length, radio) {
    const {sf, bandwidth, cr, preamble} = radio;
    const symbol = 2 ** sf / (bandwidth * 1000), de = symbol >= 0.016 ? 1 : 0;
    const payload = 8 + Math.max(Math.ceil((8 * length - 4 * sf + 28 + 16) / (4 * (sf - 2 * de))) * cr, 0);
    return (preamble + 4.25 + payload) * symbol;
  }
  function radioValid(r) {
    return r && Number.isInteger(r.sf) && r.sf >= 5 && r.sf <= 12 && Number.isFinite(r.bandwidth) && r.bandwidth > 0 &&
      Number.isInteger(r.cr) && r.cr >= 4 && r.cr <= 8 && Number.isInteger(r.preamble) && r.preamble >= 1 && r.preamble <= 65535;
  }
  function estimateEdge(edge, caps, options = {}) {
    if (!edge || !Number.isSafeInteger(edge.payloadSize) || edge.payloadSize <= 0 || edge.payloadSize > 64 * 1024 * 1024 ||
        ![1024, 2048].includes(edge.blockSize)) throw Error('Invalid payload or block geometry');
    const relays = options.relays ?? 0;
    if (!Number.isInteger(relays) || relays < 0 || relays > 8) throw Error('Invalid relay count');
    const radio = options.radio;
    if (radio && !radioValid(radio)) throw Error('Invalid LoRa radio parameters');
    const warnings = [], profile = caps.wireProfile;
    if (!['legacy', 'v2', 'direct'].includes(profile)) throw Error('Unknown wire profile');
    if (profile === 'direct' && relays) throw Error('Direct 240-byte framing cannot be relayed');
    if (profile === 'legacy' && edge.blockSize !== 1024) throw Error('Legacy geometry requires 1024-byte blocks');
    const blockCount = ceil(edge.payloadSize, edge.blockSize);
    if (blockCount > 65535) throw Error('Too many package blocks');
    let pipeline = profile === 'legacy' ? 1 : caps.pipeline;
    if (pipeline === undefined || pipeline === null) { pipeline = 1; warnings.push('Unknown fetch pipeline; using one block per request'); }
    if (!Number.isInteger(pipeline) || pipeline < 1 || pipeline > 4) throw Error('Invalid fetch pipeline');
    let proactive = profile !== 'legacy' || caps.proactiveProof === true;
    if (profile === 'legacy' && typeof caps.proactiveProof !== 'boolean') warnings.push('Unknown legacy proof behavior; including proof requests');
    const fragmentSize = profile === 'legacy' ? 160 : profile === 'v2' ? 171 : 240;
    let encoded = edge.encodedBlockSizes;
    if (encoded !== undefined && (!Array.isArray(encoded) || encoded.length !== blockCount)) throw Error('Encoded block count does not match payload');
    if (caps.deflate !== true || profile === 'legacy') {
      if (encoded && encoded.some((length, i) => length !== Math.min(edge.blockSize, edge.payloadSize - i * edge.blockSize)))
        throw Error('Compressed block estimates require confirmed v2 DEFLATE capability');
      encoded = undefined;
      if (profile !== 'legacy' && caps.deflate == null) warnings.push('Unknown DEFLATE capability; estimating uncompressed DATA');
    } else if (!encoded) warnings.push('Per-block compression sizes unavailable; estimating uncompressed DATA');
    if (encoded) encoded.forEach((length, i) => {
      const raw = Math.min(edge.blockSize, edge.payloadSize - i * edge.blockSize);
      if (!Number.isInteger(length) || length < 1 || length > raw) throw Error('Invalid encoded block length');
    });
    const counts = {manifest:3, data:0, requests:0, proofRequests:0, proofs:blockCount, overhead:0};
    let originBytes = 0, transmittedBytes = 0, rfSeconds = radio ? 0 : null;
    const emit = (length, amount = 1) => {
      originBytes += length * amount;
      for (let hop = 0; hop <= relays; hop++) {
        const wireLength = length + hop; // normal unscoped flood adds one one-byte path hash per relay
        transmittedBytes += wireLength * amount;
        if (radio) rfSeconds += airtime(wireLength, radio) * amount;
      }
    };
    // GET_MANIFEST + 197-byte manifest in 176-byte fragments; both include two Mesh bytes.
    emit(9); emit(185); emit(30);
    for (let i = 0; i < blockCount; i++) {
      const raw = Math.min(edge.blockSize, edge.payloadSize - i * edge.blockSize);
      const length = encoded ? encoded[i] : raw, whole = Math.floor(length / fragmentSize), tail = length % fragmentSize;
      const header = profile === 'legacy' ? 11 : 15;
      counts.data += whole + (tail ? 1 : 0);
      if (whole) emit(header + fragmentSize, whole);
      if (tail) emit(header + tail);
      emit(10 + 4 * siblingCount(blockCount, i));
      if (!proactive) { counts.proofRequests++; emit(9); }
    }
    let left = blockCount, width = 1;
    while (left) {
      const n = Math.min(width, left); counts.requests++;
      emit(profile === 'legacy' ? 11 : 7 + 4 * n); left -= n;
      width = Math.min(width + 1, pipeline);
    }
    // Optional caller-supplied, measured/assumed setup/control frames per step.
    if (options.overheadFrames !== undefined) {
      if (!Array.isArray(options.overheadFrames) || options.overheadFrames.some(n => !Number.isInteger(n) || n < 2 || n > 184))
        throw Error('Invalid additional Mesh frame lengths');
      counts.overhead = options.overheadFrames.length;
      for (const n of options.overheadFrames) emit(n);
    } else warnings.push('Discovery, administration, retries and status-poll frames are not included');
    const packets = Object.values(counts).reduce((a, b) => a + b, 0);
    return {profile, blockCount, fragmentSize, counts, logicalPackets:packets,
      rfTransmissions:packets * (relays + 1), originMeshBytes:originBytes, transmittedMeshBytes:transmittedBytes,
      rfAirtimeSeconds:rfSeconds, assumptions:warnings};
  }
  function planRoute(options) {
    const {nodes, edges, current, goal, objective = 'packets', radio, allowUnverified = false} = options || {};
    const relays = options?.relays ?? 0;
    if (!Array.isArray(nodes) || !nodes.length || !Array.isArray(edges) || !current || !['packets','airtime'].includes(objective))
      throw Error('Provide image nodes, candidate edges, the current identity and a valid objective');
    if (!Number.isInteger(relays) || relays < 0 || relays > 8) throw Error('Invalid relay count');
    if (objective === 'airtime' && (!radioValid(radio) || !Number.isFinite(options.restartSeconds) || options.restartSeconds < 0))
      throw Error('Airtime planning requires radio parameters and an explicit per-install restart estimate');
    const byId = new Map();
    for (const n of nodes) {
      if (!n || typeof n.id !== 'string' || !n.id || byId.has(n.id) || !Number.isInteger(n.version) || n.version < 0 || n.version > 0xffffffff ||
          !Number.isInteger(n.targetId) || n.targetId < 0 || n.targetId > 0xffffffff || typeof n.hwId !== 'string' || !n.hwId ||
          !BODY.test(n.bodyHash || '') || !SHA.test(n.imageHash || '') || n.validated !== true)
        throw Error('Each image needs unique ID, validated target, hardware, version and full image/body hashes');
      byId.set(n.id, n);
    }
    if (!BODY.test(current.bodyHash || '') || !Number.isInteger(current.version) || !Number.isInteger(current.targetId) || typeof current.hwId !== 'string')
      throw Error('Current target identity and installed body hash are required');
    const start = byId.get(current.node), end = byId.get(goal);
    if (!start || !end || start.version !== current.version || start.targetId !== current.targetId || start.hwId !== current.hwId ||
        start.bodyHash.toLowerCase() !== current.bodyHash.toLowerCase() ||
        (current.imageHash && (!SHA.test(current.imageHash) || current.imageHash.toLowerCase() !== start.imageHash.toLowerCase())))
      throw Error('The selected base image does not match the current target identity');
    if (start.id === end.id) return {steps:[], totals:{installs:0,logicalPackets:0,rfTransmissions:0,rfAirtimeSeconds:radio?0:null,estimatedSeconds:0}, assumptions:[], excluded:[], exact:false};
    const excluded = [], usable = [];
    for (const e of edges) {
      try {
        const a = byId.get(e.from), b = byId.get(e.to), assumptions = [];
        if (!a || !b || b.version <= a.version || a.targetId !== b.targetId || a.hwId !== b.hwId ||
            !['full','delta'].includes(e.kind) || !Number.isInteger(e.codec) || (e.kind === 'full' ? e.codec !== 0 : ![1,2].includes(e.codec)) ||
            !SHA.test(e.packageHash || '') || !SHA.test(e.targetImageHash || '') || e.targetImageHash.toLowerCase() !== b.imageHash.toLowerCase() ||
            (e.kind === 'delta' && (!BODY.test(e.baseHash || '') || e.baseHash.toLowerCase() !== a.bodyHash.toLowerCase())))
          throw Error('Invalid identity, upgrade order, codec or package hash');
        if (e.verified !== true || e.compatible !== true) {
          if (!allowUnverified || e.verified === false || e.compatible === false) throw Error('Package or compatibility not verified');
          assumptions.push('Package or apply compatibility still needs verification');
        }
        const caps = {...(a.capabilities || {})};
        if (!Array.isArray(caps.codecs) || !Number.isInteger(caps.maxBlock) || !['legacy','v2','direct'].includes(caps.wireProfile)) {
          if (!allowUnverified) throw Error('Current firmware OTA capabilities are unknown');
          assumptions.push('Some running-firmware capabilities are unknown and must be checked');
          if (!Array.isArray(caps.codecs)) caps.codecs = [e.codec];
          if (!Number.isInteger(caps.maxBlock)) caps.maxBlock = 1024;
          if (!['legacy','v2','direct'].includes(caps.wireProfile)) caps.wireProfile = 'legacy';
        }
        if (!caps.codecs.includes(e.codec) || e.blockSize > caps.maxBlock) throw Error('Installed firmware does not support the codec or block size');
        const estimate = estimateEdge(e, caps, {radio, relays, overheadFrames:options.overheadFrames});
        assumptions.push(...estimate.assumptions);
        usable.push({edge:e, from:a, to:b, estimate, assumptions});
      } catch (error) { excluded.push({from:e?.from, to:e?.to, reason:error.message}); }
    }
    const records = new Map([[start.id, {score:0, installs:0, steps:[]}]])
    const ordered = [...usable].sort((x,y) => x.from.version - y.from.version || x.to.version - y.to.version);
    for (const candidate of ordered) {
      const previous = records.get(candidate.from.id); if (!previous) continue;
      const step = objective === 'packets' ? candidate.estimate.rfTransmissions : candidate.estimate.rfAirtimeSeconds + options.restartSeconds;
      const score = previous.score + step, installs = previous.installs + 1, existing = records.get(candidate.to.id);
      if (!existing || score < existing.score || (score === existing.score && installs < existing.installs))
        records.set(candidate.to.id, {score, installs, steps:[...previous.steps, candidate]});
    }
    const result = records.get(end.id);
    if (!result) throw Error('No compatible forward update path is known');
    const totals = {installs:result.installs, logicalPackets:0, rfTransmissions:0, rfAirtimeSeconds:radio?0:null, estimatedSeconds:null};
    const assumptions = new Set(['Clean-link estimate: loss, contention, duty pacing and additional flood relays are unknown']);
    if (!current.imageHash) assumptions.add('The live node reports only an eight-byte body hash; its full installed image hash is not proven');
    for (const s of result.steps) {
      totals.logicalPackets += s.estimate.logicalPackets; totals.rfTransmissions += s.estimate.rfTransmissions;
      if (radio) totals.rfAirtimeSeconds += s.estimate.rfAirtimeSeconds;
      s.assumptions.forEach(a => assumptions.add(a));
    }
    if (objective === 'airtime') { totals.estimatedSeconds = totals.rfAirtimeSeconds + result.installs * options.restartSeconds; assumptions.add('Restart duration is a caller-supplied assumption per install'); }
    return {steps:result.steps.map(s => ({from:s.from.id,to:s.to.id,kind:s.edge.kind,packageHash:s.edge.packageHash.toLowerCase(),estimate:s.estimate,assumptions:s.assumptions})),
      totals, assumptions:[...assumptions], excluded, objective, exact:false};
  }
  return {planRoute, estimateEdge, siblingCount, airtime};
});
