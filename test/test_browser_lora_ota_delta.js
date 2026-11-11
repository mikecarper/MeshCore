'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const delta = require('../docs/_javascript/lora_ota_delta.js');

function nrfImage(version, flags=0, app=0x26000) {
  const body=Buffer.alloc(16000,0xA4), record=Buffer.alloc(24), end=Buffer.alloc(56);
  record.write('mOTALay1');record[8]=1;record[9]=flags;record.writeUInt16LE(24,10);record.writeUInt32LE(app,12);
  record.writeUInt32LE(flags===2?0xD4000:flags===12?0xE0000:0xED000,16);record.writeUInt32LE(flags===2?0xD4000:0xED000,20);
  const b=Buffer.concat([body,record]);end.write('EndF');end.writeUInt32LE(b.length,4);
  crypto.createHash('sha256').update(b).digest().copy(end,8,0,8);end.writeUInt32LE(version,16);end.writeUInt32LE(0x12345678,20);end.write('board',24);
  return Buffer.concat([b,end]);
}

test('reconstructs every encoded and CRLE-compressed patch before use', () => {
  for (let n = 1; n <= 100; n++) {
    const base = crypto.createHash('sha256').update(String(n)).digest();
    const target = Buffer.concat([base.subarray(0, n % 31), Buffer.from([n]), base.subarray(n % 31)]);
    const patch = delta.sequential(base, target);
    assert.deepEqual(Buffer.from(delta.applySequential(base, patch)), target);
    assert.throws(() => delta.applySequential(base, patch.subarray(0, patch.length - 1)));
  }
  assert.throws(() => delta.applySequential(new Uint8Array([1]), Uint8Array.of(0,1)), /Invalid/);
  assert.throws(() => delta.sequential(new Uint8Array(), new Uint8Array([1])), /size/);
});

test('nRF52 generation requires exact layouts, confirmed stage, and space', async () => {
  const base=nrfImage(0x01120001),target=nrfImage(0x01120002);
  await assert.rejects(delta.buildInPlaceDelta(base,target),/staging ceiling/);
  await assert.rejects(delta.buildInPlaceDelta(base,target,{runtimeStageCeiling:0x28000}),/workspace/);
  await assert.rejects(delta.buildInPlaceDelta(base,nrfImage(0x01120002,2),{runtimeStageCeiling:0xED000}),/layouts differ/);
  await assert.rejects(delta.buildInPlaceDelta(base,nrfImage(0x01120002,4),{runtimeStageCeiling:0xED000}),/layouts differ/);
  const autoBase=nrfImage(0x01120001,32),autoTarget=nrfImage(0x01120002,32);
  await assert.rejects(delta.buildInPlaceDelta(autoBase,autoTarget,{runtimeStageCeiling:0xED000}),/confirmed internal/);
  assert.equal((await delta.buildInPlaceDelta(autoBase,autoTarget,{runtimeStageCeiling:0xED000,runtimeStorage:'internal'})).blob[64],2);
  const result=await delta.buildInPlaceDelta(base,target,{runtimeStageCeiling:0xED000});
  assert.equal(result.blob[64],2);
  assert.deepEqual(Buffer.from(delta.applyInPlace(base,result.payload)),target);
  assert.throws(()=>delta.applyInPlace(base,result.payload.subarray(0,result.payload.length-1)));
});

test('QSPI requires confirmed apply and capacity and never emits unsigned SD packages', async () => {
  for (const flags of [4,12,32,48]) {
    const base=nrfImage(0x01120001,flags),target=nrfImage(0x01120002,flags);
    await assert.rejects(delta.buildInPlaceDelta(base,target,{runtimeStorage:'qspi'}),/Confirm QSPI/);
    await assert.rejects(delta.buildInPlaceDelta(base,target,{runtimeStorage:'qspi',runtimeQspiApply:true,runtimeStorageBytes:1}),/capacity/);
    const result=await delta.buildInPlaceDelta(base,target,{runtimeStorage:'qspi',runtimeQspiApply:true,runtimeStorageBytes:1024*1024});
    assert.equal(result.blob[64],2);assert.deepEqual(Buffer.from(delta.applyInPlace(base,result.payload)),target);
  }
  const base=nrfImage(0x01120001,1),target=nrfImage(0x01120002,1);
  await assert.rejects(delta.buildInPlaceDelta(base,target,{runtimeStorage:'sd',runtimeQspiApply:true,runtimeStorageBytes:1024*1024}),/Unsupported/);
});
