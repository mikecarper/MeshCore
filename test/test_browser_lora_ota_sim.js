const test = require('node:test');
const assert = require('node:assert/strict');
const sim = require('../docs/_javascript/lora_ota_sim.js');
const inventory = require('../docs/_data/otafix_rak_inventory.json');
const fs = require('node:fs');
const path = require('node:path');
const status = 'BL board=239A0029 target=2D0DF000 name=4631_DFU crc=FF374B1B abi=3 caps=0A | staged:none';
test('requires matching version, board, target, checksum and capabilities', () => {
  assert.equal(sim.identify('v0.11.0-OTAFIX2.4.12', status), '2.4.12-rak4631');
  for (const bad of [
    status.replace('FF374B1B','FF374B1C'), status.replace('2D0DF000','23818A80'),
    status.replace('caps=0A','caps=0E'), status.replace('abi=3','abi=2')
  ]) assert.throws(() => sim.identify('OTAFIX2.4.12', bad));
  assert.throws(() => sim.identify('OTAFIX2.4.11', status));
});
test('every allowed model matches its published bootloader inventory', () => {
  for (const [key, model] of Object.entries(sim.profiles)) {
    const [version,name,crc] = key.split(':');
    const rows = inventory.profiles.filter(x => x.tag.endsWith('OTAFIX'+version) &&
      x.device_name === name && x.manifest_crc32 === crc);
    assert.equal(rows.length,1,key);
    const p=rows[0], response='board='+p.board_id+' target='+p.target_id+' name='+name+' crc='+crc+' abi='+p.capability_abi+' caps='+p.storage_caps.toString(16).padStart(2,'0');
    assert.equal(sim.identify('OTAFIX'+version,response),model);
  }
});
test('published simulator assets are identical to the reviewed research builds', () => {
  const source=path.resolve(__dirname,'../tools/lora_ota/otafix_wasm');
  const published=path.resolve(__dirname,'../docs/_javascript/otafix_sim');
  assert.deepEqual(fs.readFileSync(path.join(published,'worker.js')),fs.readFileSync(path.join(source,'worker.js')));
  for(const model of Object.values(sim.profiles)) {
    const file='otafix-'+model+'-internal.wasm';
    assert.deepEqual(fs.readFileSync(path.join(published,'build',file)),fs.readFileSync(path.join(source,'build',file)),model);
  }
});
test('simulates every delta and rejects unmodeled full steps', async () => {
  const sent = [];
  class Worker {
    postMessage(v) { sent.push(v); queueMicrotask(() => this.onmessage({data:{ok:true}})); }
    terminate() {}
  }
  const step = {sourceImage:Uint8Array.of(1),targetImage:Uint8Array.of(2),
    package:{blob:Uint8Array.of(3),info:{codecId:2}}};
  assert.equal(await sim.simulate('2.4.12-rak4631',{steps:[step,step]},0xD4000,{Worker}),true);
  assert.equal(sent.length,2);
  await assert.rejects(sim.simulate('2.4.12-rak4631',{steps:[{...step,package:{...step.package,info:{codecId:0}}}]},0xD4000,{Worker}), /Only in-place/);
});
