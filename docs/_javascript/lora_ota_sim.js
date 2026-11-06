/* Bootloader identification and isolated nRF52 apply simulation. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaSim = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  // CRCs identify published bootloader builds, not a cryptographic attestation.
  // Profiles are deliberately limited to the modeled internal-flash handoff.
  const profiles = Object.freeze({
    '2.4.3:3401_DFU:B993684E':'2.4.3-rak3401',
    '2.4.3:4631_DFU:8CD63C00':'2.4.3-rak4631',
    '2.4.6:3401_DFU:A9C9F190':'2.4.6-rak3401',
    '2.4.6:4631_DFU:F0045404':'2.4.6-rak4631',
    '2.4.9:3401_DFU:FEEB920F':'2.4.9-rak3401',
    '2.4.9:4631_DFU:A957950E':'2.4.9-rak4631',
    '2.4.11:3401_DFU:3023B6AD':'2.4.11-rak3401',
    '2.4.11:4631_DFU:CD0863E0':'2.4.11-rak4631',
    '2.4.12:3401_DFU:11750A13':'2.4.12-rak3401',
    '2.4.12:4631_DFU:FF374B1B':'2.4.12-rak4631'
  });
  function identify(version, status) {
    const v = String(version).match(/OTAFIX(2\.4\.(?:3|6|9|11|12))(?!\d)/i);
    const fields = {};
    for (const m of String(status).matchAll(/\b(board|target|name|crc|abi|caps)=([^\s|;]+)/gi))
      fields[m[1].toLowerCase()] = m[2];
    if (!v || fields.board?.toUpperCase() !== '239A0029' ||
        fields.abi !== '3' || fields.caps?.toUpperCase() !== '0A')
      throw Error('This bootloader identity is not supported by the internal-flash simulator');
    const name = fields.name?.toUpperCase(), crc = fields.crc?.toUpperCase();
    const expectedTarget = name === '3401_DFU' ? '23818A80' : name === '4631_DFU' ? '2D0DF000' : '';
    if (!expectedTarget || fields.target?.toUpperCase() !== expectedTarget)
      throw Error('The bootloader board and target identity do not match a supported RAK profile');
    const result = profiles[v[1] + ':' + name + ':' + crc];
    if (!result) throw Error('The bootloader checksum does not match a supported published build');
    return result;
  }
  async function simulate(profile, prepared, ceiling, options = {}) {
    if (!profiles || !Object.values(profiles).includes(profile) || ![0xD4000,0xED000].includes(ceiling))
      throw Error('Unsupported bootloader simulation profile or staging ceiling');
    if (!prepared?.steps?.length || prepared.steps.some(s => s.package?.info?.codecId !== 2))
      throw Error('Only in-place differential steps can currently be simulated');
    const WorkerClass = options.Worker || globalThis.Worker;
    if (!WorkerClass) throw Error('Browser bootloader simulation is unavailable');
    const worker = new WorkerClass(options.url || '../_javascript/otafix_sim/worker.js');
    try {
      for (const step of prepared.steps) {
        if (!(step.sourceImage instanceof Uint8Array) || !(step.targetImage instanceof Uint8Array) ||
            !(step.package?.blob instanceof Uint8Array)) throw Error('A simulation image or package is missing');
        const send = b => b.slice().buffer;
        const result = await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(Error('Bootloader simulation timed out')), 30000);
          worker.onmessage = e => { clearTimeout(timeout); resolve(e.data); };
          worker.onerror = e => { clearTimeout(timeout); reject(Error(e.message || 'Bootloader simulation failed')); };
          worker.postMessage({profile, base:send(step.sourceImage), package:send(step.package.blob),
            target:send(step.targetImage), ceiling});
        });
        if (!result?.ok) throw Error(result?.error || 'Bootloader simulation failed');
      }
    } finally { worker.terminate(); }
    return true;
  }
  return {identify,simulate,profiles};
});
