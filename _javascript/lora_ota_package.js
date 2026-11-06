/* Validate application mOTA files before serving them over a radio. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MeshCoreMotaPackage = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const MAX_SIZE = 64 * 1024 * 1024;
  const equal = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
  const text = b => {
    const end = b.indexOf(0);
    const part = end < 0 ? b : b.subarray(0, end);
    if (part.some(v => v > 127)) throw new Error('mOTA hardware ID is not ASCII');
    return String.fromCharCode(...part);
  };
  function cryptoApi() {
    if (globalThis.crypto && globalThis.crypto.subtle) return globalThis.crypto.subtle;
    if (typeof require === 'function') return require('node:crypto').webcrypto.subtle;
    throw new Error('Web Crypto is unavailable; use a secure browser connection');
  }
  async function sha256(bytes) { return new Uint8Array(await cryptoApi().digest('SHA-256', bytes)); }
  async function parseEndF(input) {
    const image = new Uint8Array(input instanceof ArrayBuffer ? input : input.buffer.slice(input.byteOffset, input.byteOffset + input.byteLength)).slice();
    if (image.length < 56 || image.length > MAX_SIZE) throw new Error('Invalid firmware image size');
    const end = image.length - 56, ev = new DataView(image.buffer, end, 56);
    if (!equal(image.slice(end, end + 4), Uint8Array.of(69, 110, 100, 70)) || ev.getUint32(4, true) !== end ||
        !equal((await sha256(image.subarray(0, end))).slice(0, 8), image.slice(end + 8, end + 16)))
      throw new Error('Firmware EndF trailer or body hash is invalid');
    return {image, imageSize: image.length, bodyHash: image.slice(end + 8, end + 16), imageHash: await sha256(image),
      fwVersion: ev.getUint32(16, true), targetId: ev.getUint32(20, true), hwId: text(image.slice(end + 24, end + 56))};
  }
  async function merkleRoot(leaves) {
    if (!leaves.length) throw new Error('mOTA payload is empty');
    let level = leaves;
    while (level.length > 1) {
      const next = [];
      for (let i = 0; i < level.length; i += 2) {
        if (i + 1 === level.length) next.push(level[i]);
        else {
          const pair = new Uint8Array(8);
          pair.set(level[i]); pair.set(level[i + 1], 4);
          next.push((await sha256(pair)).slice(0, 4));
        }
      }
      level = next;
    }
    return level[0];
  }
  async function parseMota(input, options = {}) {
    // Copy to avoid changes to the caller's input buffer during validation.
    const blob = new Uint8Array(input instanceof ArrayBuffer ? input : input.buffer.slice(input.byteOffset, input.byteOffset + input.byteLength)).slice();
    if (blob.length < 210 || blob.length > MAX_SIZE) throw new Error('mOTA file size is invalid');
    const view = new DataView(blob.buffer);
    const slice = (start, end) => blob.slice(start, end);
    const ascii = (offset, value) => equal(slice(offset, offset + value.length), Uint8Array.from(value, c => c.charCodeAt(0)));
    if (!ascii(0, 'mOTA') || !ascii(blob.length - 5, 'vk496')) throw new Error('Not a MeshCore mOTA container');
    if (view.getUint32(4, true) !== blob.length) throw new Error('mOTA declared size does not match file');
    if (blob[8] !== 2) throw new Error('Only version 2 application mOTA packages are supported');
    const flags = blob[9];
    if (flags & ~3) throw new Error('Unsupported application mOTA flags');
    if (blob[10] !== 0x12) throw new Error('Unsupported mOTA hash algorithm');
    const targetId = view.getUint32(11, true), fwVersion = view.getUint32(15, true);
    const imageSize = view.getUint32(19, true), payloadSize = view.getUint32(23, true);
    const blockLog = blob[27];
    if (blockLog < 1 || blockLog > 11 || !payloadSize || !imageSize || imageSize > MAX_SIZE)
      throw new Error('Invalid mOTA payload, image, or block size');
    const blockSize = 1 << blockLog, merkle = slice(28, 32), imageHash = slice(32, 64);
    const codecId = blob[64], hwId = text(slice(65, 97)), baseHash = slice(97, 105);
    const signer = slice(105, 137), signature = slice(137, 201);
    const full = !!(flags & 1), signed = !!(flags & 2);
    if (full ? codecId !== 0 : (codecId !== 1 && codecId !== 2)) throw new Error('Unsupported mOTA codec');
    if (!equal(slice(201, 205), Uint8Array.of(255, 255, 255, 255))) throw new Error('mOTA approval marker is not erased');
    const blockCount = Math.ceil(payloadSize / blockSize), payloadOffset = 205 + blockCount * 4;
    if (blockCount > 65535) throw new Error('mOTA has more blocks than the radio protocol supports');
    if (payloadOffset + payloadSize !== blob.length - 5) throw new Error('mOTA manifest geometry does not match file');
    const payload = slice(payloadOffset, payloadOffset + payloadSize), leaves = [];
    for (let i = 0; i < blockCount; i++) {
      const hash = (await sha256(payload.subarray(i * blockSize, Math.min((i + 1) * blockSize, payloadSize)))).slice(0, 4);
      if (!equal(hash, slice(205 + i * 4, 209 + i * 4))) throw new Error('mOTA block hash does not match payload');
      leaves.push(hash);
    }
    if (!equal(await merkleRoot(leaves), merkle)) throw new Error('mOTA Merkle root does not match payload');
    if (signed) {
      let key;
      try { key = await cryptoApi().importKey('raw', signer, {name: 'Ed25519'}, false, ['verify']); }
      catch (_) { throw new Error('This browser cannot verify Ed25519 mOTA signatures'); }
      if (!(await cryptoApi().verify('Ed25519', key, signature, blob.subarray(8, 137))))
        throw new Error('mOTA Ed25519 signature is invalid');
    }
    if (options.expectedSigner) {
      const expected = new Uint8Array(options.expectedSigner);
      if (expected.length !== 32 || !signed || !equal(expected, signer)) throw new Error('mOTA signer is not the expected key');
    }
    if (full) {
      if (payloadSize !== imageSize || !equal(await sha256(payload), imageHash)) throw new Error('Full image size or hash is invalid');
      const endf = await parseEndF(payload);
      if ((endf.targetId && endf.targetId !== targetId) || (endf.fwVersion && endf.fwVersion !== fwVersion) || (endf.hwId && hwId && endf.hwId !== hwId))
        throw new Error('Firmware EndF identity differs from mOTA manifest');
    }
    return {blob, payload, flags, full, isFull: full, isBootloader: false, signed, targetId, fwVersion, imageSize, payloadSize, payloadOffset, blockSize, blockCount,
      merkleRoot: merkle, imageHash, codecId, hwId, baseHash, signer, signature,
      // A delta can only be reconstructed and checked against its exact base on the destination.
      reconstructedImageVerified: full};
  }
  async function buildFullMota(input, options = {}) {
    const info = await parseEndF(input), image = info.image, end = image.length - 56;
    if (image.length > MAX_SIZE - 210) throw new Error('Invalid firmware image size');
    const hwId = image.slice(end + 24, end + 56);
    const blockSize = options.blockSize === undefined ? 1024 : options.blockSize;
    if (!Number.isInteger(blockSize) || blockSize < 2 || blockSize > 2048 || (blockSize & (blockSize - 1)))
      throw new Error('Invalid mOTA block size');
    if (Math.ceil(image.length / blockSize) > 65535) throw new Error('Firmware needs too many mOTA blocks');
    const leaves = [];
    for (let i = 0; i < image.length; i += blockSize) leaves.push((await sha256(image.subarray(i, i + blockSize))).slice(0, 4));
    const payloadOffset = 205 + leaves.length * 4, total = payloadOffset + image.length + 5;
    if (total > MAX_SIZE) throw new Error('mOTA package is too large');
    const blob = new Uint8Array(total), view = new DataView(blob.buffer);
    blob.set([109, 79, 84, 65]); view.setUint32(4, total, true);
    blob[8] = 2; blob[9] = 1; blob[10] = 0x12;
    view.setUint32(11, info.targetId, true); view.setUint32(15, info.fwVersion, true);
    view.setUint32(19, image.length, true); view.setUint32(23, image.length, true);
    blob[27] = Math.log2(blockSize); blob.set(await merkleRoot(leaves), 28);
    blob.set(await sha256(image), 32); blob.set(hwId, 65); blob.fill(255, 201, 205);
    leaves.forEach((leaf, i) => blob.set(leaf, 205 + i * 4));
    blob.set(image, payloadOffset); blob.set([118, 107, 52, 57, 54], total - 5);
    return blob;
  }
  return {parseMota, parseEndF, buildFullMota};
});
