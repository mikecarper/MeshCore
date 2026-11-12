'use strict';
const assert = require('assert');
const checks = require('../docs/_javascript/lora_ota_checks.js');
assert.equal(checks.explicitMaxBlock('OTA | maxblk:2048 |'),2048);
assert.equal(checks.explicitMaxBlock('OTA | maxblk:1024 |'),1024);
assert.equal(checks.explicitMaxBlock('ERR unsupported; maxblk:2048'),null);
for (const missing of ['', 'Unknown command', 'maxblk:3000', 'maxblk:0', 'maxblk:2048.5', 'maxblk:2048,5', 'maxblk:2048x', 'maxblk:2048 maxblk:', 'maxblk:2048 maxblk:2048.5', 'maxblk:2048 maxblk:1024'])
  assert.equal(checks.explicitMaxBlock(missing),null);
const source = 'aa'.repeat(32), relay = 'bb'.repeat(32), target = 'cc'.repeat(32);
assert.deepStrictEqual(checks.route(target, [relay], source), ['BBBBBB']);
assert.throws(() => checks.route(target, [target], source), /prefixes/);
assert.throws(() => checks.route(target, ['bad'], source), /key/);
const d = checks.destination('OTA hw=ikoka target:E5331F02 | maxblk:2048 | bl:QSPI', 'base_hash=0123456789ABCDEF | bootloader: QSPI apply OK (abi=3 codecs=0x5)', 'v1.17.1.9');
checks.destination('OTA hw=ikoka target:E5331F02 | maxblk:2048 | bl:QSPI | no download', 'base_hash=0123456789ABCDEF | bootloader: QSPI apply OK (abi=3 codecs=0x5)', 'v1.17.1.9');
for (const bad of ['NO SD apply', 'NO QSPI apply', 'NO delta apply', 'NO QSPI mota-apply support', 'NO SD mota-apply support', 'NO mota-apply support'])
  assert.throws(() => checks.destination('hw=x target:12345678 bl:apply no download', 'base_hash=0123456789abcdef bootloader: '+bad, '1.2.3'), /not ready/);
const info = { targetId: 0xe5331f02, hwId: 'ikoka', baseHash: Uint8Array.from(Buffer.from('0123456789abcdef', 'hex')), fwVersion: checks.version('1.18.0.1'), blockSize: 1024, isFull: false, isBootloader: false, codecId: 2 };
assert(checks.validate(info, d, false));
assert.throws(() => checks.validate({ ...info, hwId: 'wrong' }, d), /match/);
assert.throws(() => checks.validate({ ...info, baseHash: new Uint8Array(8) }, d), /exact/);
assert.throws(() => checks.validate({ ...info, codecId: 1 }, d), /codec/);
assert.throws(() => checks.validate({ ...info, blockSize: 4096 }, d), /block/);
assert.throws(() => checks.validate({ ...info, fwVersion: d.version }, d), /newer/);
assert.throws(() => checks.destination('hw=x target:12345678 maxblk:1024 maxblk:2048', 'base_hash=0123456789ABCDEF', '1.2.3'), /conflicting/);
assert.throws(() => checks.validate({ ...info, isBootloader: true }, d), /Bootloader/);
checks.confirmSequentialTarget('hw=heltec', 'base_hash=0123456789abcdef', 'Error: unsupported');
assert.throws(() => checks.confirmSequentialTarget('hw=unknown', 'base_hash=0123456789abcdef', '> unknown'), /confirm/);
assert.throws(() => checks.confirmSequentialTarget('bl:QSPI', 'base_hash=0123456789abcdef', 'Error: unsupported'), /confirm/);
const internal = {...d, external:false, nrf:true, codecs:5};
assert.equal(checks.internalStage('bl:internal', 'bootloader: apply OK', internal), 0xD4000);
assert.equal(checks.internalStage('bl:apply', 'bootloader: apply OK (abi=3 codecs=0x5 stage=ED000)', internal), 0xED000);
assert.throws(() => checks.internalStage('bl:QSPI', 'bootloader: QSPI apply OK', {...internal, external:true}), /internal/);
assert.throws(() => checks.internalStage('bl:QSPI', 'internal store:256K bootloader: apply OK', internal), /internal/);
assert.throws(() => checks.validateInternalPlan({storage:'internal', app:0x26000, layoutCeiling:0xED000, layoutFlags:16, memorySize:8192}, {blob:new Uint8Array(4097)}, 'bl:internal', 'bootloader: apply OK', internal), /RAM capability/);
const hybridPlan={storage:'internal', app:0x26000, layoutCeiling:0xED000, layoutFlags:16, memorySize:8192};
const hybridInfo={blob:new Uint8Array(4097)}, proof='OTA RAM: hybrid=1 abi=1 record=72 arena=65536';
for (const valid of [proof, '> '+proof, 'ota ram: HYBRID=1 ABI=1 RECORD=72 ARENA=65536'])
  checks.validateInternalPlan(hybridPlan,hybridInfo,'bl:internal','bootloader: apply OK (stage=ED000)',internal,null,valid);
for (const invalid of ['', undefined, 'Unknown OTA command. Type `ota help`.', 'OTA RAM: hybrid=0',
  'OTA RAM: hybrid=1 abi=1 record=71 arena=65536', 'OTA RAM: hybrid=1 abi=1 record=72 arena=65535',
  proof+' hybrid=1', proof+'\n'+proof, 'prefix '+proof, proof+' trailing'])
  assert.throws(()=>checks.validateInternalPlan(hybridPlan,hybridInfo,'bl:internal','bootloader: apply OK (stage=ED000)',internal,null,invalid),/RAM capability/);
checks.validateInternalPlan(hybridPlan,{blob:new Uint8Array(4096)},'bl:internal','bootloader: apply OK (stage=ED000)',internal);
assert.throws(() => checks.validateInternalPlan({app:0x26000, layoutCeiling:0xED000, memorySize:0xAE000}, {blob:new Uint8Array(4096)}, 'bl:internal', 'bootloader: apply OK', internal), /space/);
const recovery = [
  ['storage error', /get storage\.layout and ota self/],
  ['manifest timeout', /LoRa path.*update channel/],
  ['leaves timeout', /LoRa path.*update channel/],
  ['radio session changed', /temporary-radio settings/],
  ['unsupported codec', /compatible update package/],
  ['unsupported hash', /compatible update package/],
  ['invalid geometry', /compatible update package/],
  ['not a newer version', /newer compatible package/],
  ['image too large', /smaller compatible package/],
  ['invalid manifest', /package and source connection/],
  ['integrity check', /package and source connection/],
];
for (const [reason, expected] of recovery) {
  assert.match(checks.failureAdvice(`failed (${reason}) 4/32 id=12345678`), expected);
  assert.match(checks.failureAdvice(`failed:${reason} 4/32 id=12345678`), expected);
}
for (const detail of ['', 'failed 0/0 id=12345678', 'failed (none) 0/0 id=12345678',
  'failed (storage errorish) 0/0 id=12345678', 'not failed (storage error)',
  'failed:unknown 0/0 id=12345678']) assert.equal(checks.failureAdvice(detail), '');
console.log('Browser OTA safety checks passed');
