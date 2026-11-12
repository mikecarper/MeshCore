const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {test} = require('node:test');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const ota = require('../docs/_javascript/lora_ota_package.js');

const hash = data => crypto.createHash('sha256').update(data).digest();
function firmware(size = 2301) {
  const body = Buffer.alloc(size);
  for (let i = 0; i < body.length; i++) body[i] = (i * 13) & 255;
  const end = Buffer.alloc(56); end.write('EndF'); end.writeUInt32LE(body.length, 4);
  hash(body).copy(end, 8, 0, 8); end.writeUInt32LE(0x01120002, 16); end.writeUInt32LE(0x12345678, 20); end.write('test_board', 24);
  return Buffer.concat([body, end]);
}

test('large native-folder full images use larger blocks only with both explicit capabilities', async () => {
  const image = firmware(2001 * 1024), nativeFolder = true;
  for (const [sourceMaxBlock, targetMaxBlock, expected] of [
    [2048,2048,2048], [2048,1024,1024], [1024,2048,1024],
    [undefined,2048,1024], [2048,undefined,1024], ['2048',2048,1024]
  ]) {
    const result = await ota.buildFullForSource(image,{nativeFolder,sourceMaxBlock,targetMaxBlock});
    const parsed = await ota.parseMota(result.blob);
    assert.equal(parsed.blockSize,expected);
    assert.equal(parsed.blockCount,Math.ceil(image.length/expected));
    assert.deepEqual(Buffer.from(parsed.payload),image);
  }
  const large = await ota.buildFullForSource(image,{nativeFolder,sourceMaxBlock:2048,targetMaxBlock:2048});
  assert.ok((await ota.parseMota(large.blob)).blockCount <= 1024);
  const other = await ota.buildFullForSource(image,{nativeFolder:false,sourceMaxBlock:2048,targetMaxBlock:2048});
  assert.equal(other.blockSize,1024);
  const small = await ota.buildFullForSource(firmware(),{nativeFolder,sourceMaxBlock:2048,targetMaxBlock:2048});
  assert.equal(small.blockSize,1024);
  const huge = await ota.buildFullForSource(firmware(2*1024*1024),{nativeFolder,sourceMaxBlock:2048,targetMaxBlock:2048});
  assert.ok((await ota.parseMota(huge.blob)).blockCount > 1024);
});

test('full package recovery preserves exact bytes for the original owner and geometry', async () => {
  const image=firmware(1024*1024+5), sourceKey='a'.repeat(64), targetKey='b'.repeat(64);
  for(const blockSize of [1024,2048]) {
    const original=await ota.buildFullMota(image,{blockSize});
    const id=Buffer.from(original.subarray(28,32)).toString('hex');
    const packageHash=hash(original).toString('hex');
    const recovery={source:sourceKey,target:targetKey,id,packageHash,phase:'pull attempted'};
    const restored=await ota.buildFullForSource(image,{nativeFolder:true,sourceMaxBlock:2048,targetMaxBlock:2048,sourceKey,targetKey,recovery});
    assert.equal(restored.recovered,true);assert.equal(restored.blockSize,blockSize);
    assert.deepEqual(Buffer.from(restored.blob),Buffer.from(original));
    for (const wrong of [
      {...recovery,source:'c'.repeat(64)}, {...recovery,id:'12345678'},
      {...recovery,packageHash:'0'.repeat(64)}, {...recovery,phase:'other'}
    ]) assert.equal(await ota.matchesRecovery(original,{sourceKey,targetKey,recovery:wrong}),false);
    const foreign=await ota.buildFullForSource(image,{nativeFolder:true,sourceMaxBlock:2048,targetMaxBlock:2048,sourceKey,targetKey,recovery:{...recovery,target:'c'.repeat(64)}});
    assert.equal(foreign.recovered,false);assert.equal(foreign.blockSize,2048);
  }
});
async function deltaFixture() {
  const full = Buffer.from(await ota.buildFullMota(firmware(), {blockSize: 1024}));
  // Preserve valid block geometry to test the delta parser independently of detools generation.
  full[9] = 0; full[64] = 2; full.fill(0x23, 97, 105);
  return full;
}

test('full build matches Python reader and exposes serving metadata', async () => {
  const image = firmware(), bytes = await ota.buildFullMota(image), info = await ota.parseMota(bytes);
  assert.equal(info.targetId, 0x12345678); assert.equal(info.fwVersion, 0x01120002);
  assert.equal(info.codecId, 0); assert.equal(info.blockSize, 1024); assert.equal(info.blockCount, 3);
  assert.equal(info.hwId, 'test_board'); assert.equal(info.merkleRoot.length, 4);
  assert.equal(info.payloadOffset, 205 + 12); assert.deepEqual(Buffer.from(info.payload), image);
  assert.equal(info.reconstructedImageVerified, true);
  assert.equal(info.isFull, true); assert.equal(info.isBootloader, false);
  const endf = await ota.parseEndF(image);
  assert.deepEqual(Buffer.from(endf.bodyHash), hash(image.subarray(0, -56)).subarray(0, 8));
  assert.deepEqual(Buffer.from(endf.imageHash), hash(image));
  assert.equal(endf.targetId, info.targetId); assert.equal(endf.hwId, info.hwId);
  const library = path.resolve(__dirname, '../tools/mota');
  const script = 'import sys; sys.path.insert(0,sys.argv[1]); import motalib; p=motalib.parse_container(bytes.fromhex(sys.stdin.read())); assert not motalib.verify(p); print(p.manifest.target_id)';
  const result = execFileSync(process.env.PYTHON || 'python', ['-c', script, library], {input: Buffer.from(bytes).toString('hex'), encoding: 'utf8'});
  assert.equal(Number(result.trim()), info.targetId);
});

test('delta accepts known codec, verifies transfer blocks, but does not claim reconstructed hash', async () => {
  const info = await ota.parseMota(await deltaFixture());
  assert.equal(info.full, false); assert.equal(info.codecId, 2);
  assert.equal(info.reconstructedImageVerified, false); assert.deepEqual(info.baseHash, new Uint8Array(8).fill(0x23));
});

test('rejects altered payload, stored leaves, root, geometry, approval, and unsupported codecs', async () => {
  const valid = await ota.buildFullMota(firmware());
  const payloadOffset = (await ota.parseMota(valid)).payloadOffset;
  for (const pos of [payloadOffset, 205, 28, 4, 201, 64, 8]) {
    const tampered = valid.slice(); tampered[pos] ^= 1;
    await assert.rejects(ota.parseMota(tampered), undefined, `tampering at ${pos}`);
  }
  const badDelta = await deltaFixture(); badDelta[64] = 3;
  await assert.rejects(ota.parseMota(badDelta), /codec/);
  const changedInput = firmware(); changedInput[0] ^= 1;
  await assert.rejects(ota.buildFullMota(changedInput), /hash/);
});

test('validates signed region and optionally pins trusted signer', async () => {
  const blob = Buffer.from(await ota.buildFullMota(firmware()));
  const {publicKey, privateKey} = crypto.generateKeyPairSync('ed25519');
  const der = publicKey.export({format: 'der', type: 'spki'}), key = der.subarray(der.length - 32);
  blob[9] |= 2; key.copy(blob, 105); crypto.sign(null, blob.subarray(8, 137), privateKey).copy(blob, 137);
  assert.equal((await ota.parseMota(blob, {expectedSigner: key})).signed, true);
  const wrong = Buffer.from(key); wrong[0] ^= 1;
  await assert.rejects(ota.parseMota(blob, {expectedSigner: wrong}), /expected key/);
  blob[137] ^= 1;
  await assert.rejects(ota.parseMota(blob), /signature/);
  await assert.rejects(ota.parseMota(await ota.buildFullMota(firmware()), {expectedSigner: key}), /expected key/);
});
