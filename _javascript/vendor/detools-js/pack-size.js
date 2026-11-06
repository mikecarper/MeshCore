/* detools-js 0.1.0; MIT; adapted for Uint8Array and browser. See LICENSE. */
(function(root){
'use strict';

// detools signed variable-length "size" integer.
// First byte: bit 6 = sign, bits 0-5 = low magnitude bits, bit 7 = continuation.
// Later bytes: bits 0-6 = 7 more magnitude bits, bit 7 = continuation.

/**
 * Encode a signed integer into a detools size buffer.
 * @param {number} value
 * @returns {Buffer}
 */
function packSize(value) {
  if (!Number.isInteger(value)) {
    throw new TypeError(`packSize expects an integer, got ${value}`);
  }

  let sign = 0;
  if (value < 0) {
    sign = 0x40;
    value = -value;
  }

  const bytes = [];

  // First byte carries 6 bits of magnitude plus the sign bit.
  let byte = sign | (value & 0x3f);
  value = Math.floor(value / 64); // value >> 6, safe for >32-bit values

  if (value > 0) {
    byte |= 0x80;
  }
  bytes.push(byte);

  // Remaining bytes carry 7 bits each.
  while (value > 0) {
    byte = value & 0x7f;
    value = Math.floor(value / 128); // value >> 7
    if (value > 0) {
      byte |= 0x80;
    }
    bytes.push(byte);
  }

  return Uint8Array.from(bytes);
}

/**
 * A tiny cursor over a Buffer for sequential reads.
 */
class ByteReader {
  constructor(buffer, position = 0) {
    this.buffer = buffer;
    this.position = position;
  }

  /** Read a single byte, throwing on EOF. */
  readByte() {
    if (this.position >= this.buffer.length) {
      throw new Error('Unexpected end of data while reading size byte.');
    }
    return this.buffer[this.position++];
  }

  get eof() {
    return this.position >= this.buffer.length;
  }
}

/**
 * Decode a detools size from a ByteReader, returning the signed value.
 * @param {ByteReader} reader
 * @returns {number}
 */
function unpackSize(reader) {
  let byte = reader.readByte();
  const isSigned = byte & 0x40;
  let value = byte & 0x3f;
  let mul = 64; // first continuation byte contributes bits at offset 6

  while (byte & 0x80) {
    byte = reader.readByte();
    value += (byte & 0x7f) * mul; // value |= (byte & 0x7f) << offset, without Math.pow
    mul *= 128;
  }

  return isSigned ? -value : value;
}

const exported = { packSize, unpackSize, ByteReader };

if (typeof module === "object" && module.exports) module.exports = exported; else root.MeshCoreDetoolsPack = exported;
})(typeof globalThis !== "undefined" ? globalThis : this);
