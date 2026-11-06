/* Research-only Worker. This does not authenticate a device or enable updates. */
'use strict';
const MODULES = Object.freeze({
  '2.4.3-rak3401': ['otafix-2.4.3-rak3401-internal.wasm', 'ec6d9f9d223930f7d344bfd09396ff1441383a0375174a8f10cb694f46f4646d'],
  '2.4.3-rak4631': ['otafix-2.4.3-rak4631-internal.wasm', '289f5a00e0a4996e55df86b2e8843a0ccebdbb99d614487f0640d63aed6293f2'],
  '2.4.6-rak3401': ['otafix-2.4.6-rak3401-internal.wasm', '587cf73bb6613d265eeadd50916f883415a1f7fdfcd32aebde58e4984e053f0c'],
  '2.4.6-rak4631': ['otafix-2.4.6-rak4631-internal.wasm', 'd7e56f9193c289ced604e06925d3d830e8061c03bcdb828adfd777bf27961f90'],
  '2.4.9-rak3401': ['otafix-2.4.9-rak3401-internal.wasm', '3ced43fc4fe6ffa1dbbd3e02d1a7000c8f2e34cd19d6fd63ccd9dbe1bf9a3b7c'],
  '2.4.9-rak4631': ['otafix-2.4.9-rak4631-internal.wasm', '5d1ae157c3fa280cde8f48f35bc32ea0cb9ecc25adb67404013680dc1bb524c4'],
  '2.4.11-rak3401': ['otafix-2.4.11-rak3401-internal.wasm', '04321557e86ed3a4dc46e8d32c3b72ca09fbdf9671b65e321255dd49ae41c03e'],
  '2.4.11-rak4631': ['otafix-2.4.11-rak4631-internal.wasm', '1c1622d69e40408fc5105a980bbd7705b80865fe4fc0382cdebebb00647cf505'],
  '2.4.12-rak3401': ['otafix-2.4.12-rak3401-internal.wasm', 'b140596f787f63f5dd96f27a4c76b94338b45931101247df395e159c1a7b9653'],
  '2.4.12-rak4631': ['otafix-2.4.12-rak4631-internal.wasm', '906e1165a92803d66b82db507d72b5f353afe021e08c01e003be9dc9fbc4057e'],
});
const hex = b => Array.from(b, n => n.toString(16).padStart(2, '0')).join('');
function asBytes(input) {
  if (!(input instanceof ArrayBuffer) || input.byteLength === 0 || input.byteLength > 0x100000)
    throw Error('Invalid or oversized simulation input');
  return new Uint8Array(input);
}
self.onmessage = async event => {
  try {
    const {profile, base:old, package:container, target:expected, ceiling} = event.data || {};
    const selected = MODULES[profile];
    if (!selected || ![0xD4000, 0xED000].includes(ceiling)) throw Error('Unsupported simulation profile');
    const base = asBytes(old), mota = asBytes(container), target = asBytes(expected);
    const response = await fetch('build/' + selected[0], {cache:'no-store'});
    if (!response.ok) throw Error('Simulator unavailable');
    const wasm = await response.arrayBuffer();
    if (wasm.byteLength > 128 * 1024 || hex(new Uint8Array(await crypto.subtle.digest('SHA-256', wasm))) !== selected[1])
      throw Error('Simulator integrity mismatch');
    const module = await WebAssembly.compile(wasm);
    if (WebAssembly.Module.imports(module).length) throw Error('Unexpected simulator imports');
    const {exports:e} = await WebAssembly.instantiate(module, {});
    const memory = new Uint8Array(e.memory.buffer);
    memory.set(base, e.sim_base()); memory.set(mota, e.sim_package());
    const result = e.sim_apply(base.length, mota.length, ceiling), status = e.sim_status();
    const output = memory.subarray(e.sim_result(), e.sim_result() + target.length);
    let mismatch = 0;
    for (let i=0; i<target.length; ++i) mismatch |= output[i] ^ target[i];
    if (result !== 1 || status !== 0xB8 || e.sim_commit_size() !== target.length || mismatch)
      throw Error('Simulated bootloader apply did not reproduce the expected image');
    self.postMessage({ok:true, status});
  } catch (error) { self.postMessage({ok:false, error:String(error && error.message || error)}); }
};
