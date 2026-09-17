"use strict";

const assert = require("assert");
if (!globalThis.crypto) globalThis.crypto = require("crypto").webcrypto;
const decoder = require("../docs/_javascript/management_decoder.js");

function hex(text) {
  return Uint8Array.from(Buffer.from(text, "hex"));
}

(async function run() {
  // RFC 5297 Appendix A.1: independent known-answer coverage for the exact
  // AES-SIV-CMAC-256 algorithm used to protect an MGR1 ACL page.
  const key = hex("fffefdfcfbfaf9f8f7f6f5f4f3f2f1f0f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff");
  const aad = hex("101112131415161718191a1b1c1d1e1f2021222324252627");
  const ciphertext = hex("40c02b9690c4dc04daef7f6afe5c");
  const tag = hex("85632d07c6e8f37f950acd320a2ecc93");
  assert.strictEqual(
    Buffer.from(await decoder.openSiv(key, aad, ciphertext, tag)).toString("hex"),
    "112233445566778899aabbccddee"
  );

  const publicOnly = await decoder.decodeManagement(decoder.EXAMPLE_PAGE, "");
  assert.strictEqual(publicOnly.authenticated, false);
  assert.strictEqual(publicOnly.public.radioId, "000102030405060708090A0B0C0D0E0F");
  assert.strictEqual(publicOnly.public.firmware, "1.17.1.5");
  assert.strictEqual(publicOnly.acl.length, 0);

  const decoded = await decoder.decodeManagement(
    decoder.EXAMPLE_PAGE,
    decoder.EXAMPLE_PASSWORD
  );
  assert.strictEqual(decoded.authenticated, true);
  assert.strictEqual(decoded.public.bootloader, "0.2.4.6");
  assert.strictEqual(decoded.public.target, "1234ABCD");
  assert.strictEqual(decoded.acl.length, 1);
  assert.strictEqual(decoded.acl[0].fingerprint, "AABBCCDDEEFF001122334455");
  assert.strictEqual(decoded.acl[0].administrator, true);
  assert.strictEqual(decoded.acl[0].otaSigner, true);

  const groupData = "1A00" + decoder.EXAMPLE_PAGE;
  const packet = await decoder.decodeManagement(groupData, decoder.EXAMPLE_PASSWORD);
  assert.strictEqual(packet.public.envelope.route, "direct");
  assert.strictEqual(packet.public.envelope.pathHops, 0);
  const paddedRouted = "1A0177" + decoder.EXAMPLE_PAGE + "000000";
  const routed = await decoder.decodeManagement(paddedRouted, decoder.EXAMPLE_PASSWORD);
  assert.strictEqual(routed.public.envelope.pathHops, 1);
  assert.strictEqual(routed.acl[0].administrator, true);

  // An empty ACL is a valid, authenticated page. AES-CTR has no padding, so
  // it must also work when the SIV ciphertext has zero bytes.
  const emptyAclPage =
    "4D475231000102030405060708090A0B0C0D0E0F2A00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000A815011F051F4F000001000000DAA550D7FEFAEB13412CF9E457A45F2A";
  const emptyAcl = await decoder.decodeManagement(emptyAclPage, decoder.EXAMPLE_PASSWORD);
  assert.strictEqual(emptyAcl.authenticated, true);
  assert.strictEqual(emptyAcl.acl.length, 0);

  await assert.rejects(
    decoder.decodeManagement(decoder.EXAMPLE_PAGE, "not the right management password"),
    /Password is wrong/
  );
  const altered = decoder.EXAMPLE_PAGE.slice(0, -2) + "00";
  await assert.rejects(
    decoder.decodeManagement(altered, decoder.EXAMPLE_PASSWORD),
    /Password is wrong/
  );
  await assert.rejects(
    decoder.decodeManagement(decoder.EXAMPLE_PAGE, "short"),
    /12 through 96/
  );
  console.log("management browser decoder checks passed");
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
