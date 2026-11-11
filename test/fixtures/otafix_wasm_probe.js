'use strict';
const fs = require('node:fs');
const [moduleFile, baseFile, packageFile, targetFile, mode] = process.argv.slice(2);
(async () => {
  const module = await WebAssembly.compile(fs.readFileSync(moduleFile));
  if (WebAssembly.Module.imports(module).length) throw Error('unexpected WASM imports');
  const instance = await WebAssembly.instantiate(module, {}), e = instance.exports;
  const base = fs.readFileSync(baseFile), data = fs.readFileSync(packageFile), target = fs.readFileSync(targetFile);
  if (mode === 'badbase') base[17] ^= 1;
  if (mode === 'badpatch') {
    const size=data.readUInt32LE(23), block=1<<data[27];
    data[205+Math.ceil(size/block)*4] = 0xff;
  }
  const memory = new Uint8Array(e.memory.buffer);
  memory.set(base, e.sim_base()); memory.set(data, e.sim_package());
  const result = e.sim_apply(base.length, data.length, mode === 'badstage' ? 0xDEAD0 : 0xD4000);
  const current = Buffer.from(memory.slice(e.sim_result(), e.sim_result() + target.length));
  console.log(JSON.stringify({result, status:e.sim_status(), size:e.sim_commit_size(),
    matches:current.equals(target), baseIntact:current.subarray(0,base.length).equals(base)}));
})().catch(e => { console.error(e); process.exitCode = 1; });
