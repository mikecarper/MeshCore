const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {test} = require('node:test');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const ota = require('../docs/_javascript/lora_ota_package.js');

const hash = data => crypto.createHash('sha256').update(data).digest();
function firmware() {
  const body = Buffer.alloc(2301);
  for (let i = 0; i < body.length; i++) body[i] = (i * 13) & 255;
  const end = Buffer.alloc(56); end.write('EndF'); end.writeUInt32LE(body.length, 4);
  hash(body).copy(end, 8, 0, 8); end.writeUInt32LE(0x01120002, 16); end.writeUInt32LE(0x12345678, 20); end.write('test_board', 24);
  return Buffer.concat([body, end]);
}
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
