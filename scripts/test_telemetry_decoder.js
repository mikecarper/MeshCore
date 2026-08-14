"use strict";

const assert = require("assert");
const decoder = require("../docs/_javascript/telemetry_decoder.js");

let passed = 0;

function test(name, callback) {
  callback();
  passed += 1;
  process.stdout.write(`ok ${passed} - ${name}\n`);
}

function assertDecoderError(callback, pattern) {
  assert.throws(
    callback,
    (error) => error instanceof decoder.TelemetryDecodeError && pattern.test(error.message)
  );
}

const TEMPERATURE_PACKET =
  "3E00545442311122334455667788800092651E0008000102354A4E5082";
const VOLTAGE_PACKET =
  "3E00545642311122334455667788800092651E000800010264C8FEFFDC";

test("decodes complete analyzer temperature packet hex", () => {
  const decoded = decoder.decodeRawTelemetryHex(TEMPERATURE_PACKET);
  assert.strictEqual(decoded.kind, "temperature");
  assert.strictEqual(decoded.typeCode, "TTB1");
  assert.strictEqual(decoded.sourceId, "1122334455667788");
  assert.strictEqual(decoded.firstEpoch, 1704067200);
  assert.strictEqual(decoded.intervalMinutes, 30);
  assert.strictEqual(decoded.count, 8);
  assert.strictEqual(decoded.packet.routeName, "direct");
  assert.strictEqual(decoded.packet.payloadType, 0x0f);
  assert.strictEqual(decoded.packet.hopCount, 0);
  assert.deepStrictEqual(
    decoded.rows.map((row) => [row.status, row.valueC]),
    [
      ["Missing", null],
      ["Below range", null],
      ["Above range", null],
      ["Value", 0],
      ["Value", 21],
      ["Value", 25],
      ["Value", 27],
      ["Value", 77],
    ]
  );
});

test("decodes analyzer voltage packet and exact voltage codes", () => {
  const decoded = decoder.decodeTelemetry(VOLTAGE_PACKET.toLowerCase());
  assert.strictEqual(decoded.kind, "voltage");
  assert.strictEqual(decoded.typeCode, "TVB1");
  assert.deepStrictEqual(
    decoded.rows.map((row) => [row.status, row.millivolts]),
    [
      ["Missing", null],
      ["Below range", null],
      ["Value", 1880],
      ["Value", 2860],
      ["Value", 3860],
      ["Value", 4400],
      ["Above range", null],
      ["Value", 4060],
    ]
  );
});

test("accepts payload-only spaced hex", () => {
  const payload = TEMPERATURE_PACKET.slice(4).match(/.{2}/g).join(" ");
  const decoded = decoder.decodeRawTelemetryHex(payload);
  assert.strictEqual(decoded.payloadOffset, 0);
  assert.strictEqual(decoded.packet, null);
  assert.strictEqual(decoded.sourceId, "1122334455667788");
});

test("extracts raw hex from a quoted analyzer-style field", () => {
  const decoded = decoder.decodeTelemetry(`{\n  "raw": "${VOLTAGE_PACKET}"\n}`);
  assert.strictEqual(decoded.kind, "voltage");
  assert.strictEqual(decoded.packet.isRawCustom, true);
});

test("recognizes a routed packet and its path", () => {
  const routed = `3E4212345678${TEMPERATURE_PACKET.slice(4)}`;
  const decoded = decoder.decodeRawTelemetryHex(routed);
  assert.strictEqual(decoded.packet.routeName, "direct");
  assert.strictEqual(decoded.packet.hopCount, 2);
  assert.strictEqual(decoded.packet.pathHashBytes, 2);
  assert.strictEqual(decoded.payloadOffset, 6);
});

test("retains legacy CLI Base64 decoding", () => {
  const decoded = decoder.decodeTelemetry(decoder.EXAMPLES.voltage.reply);
  assert.strictEqual(decoded.kind, "voltage");
  assert.strictEqual(decoded.count, 48);
  assert.strictEqual(decoded.format, undefined);
});

test("rejects unrelated, truncated, and ambiguous hex", () => {
  assertDecoderError(
    () => decoder.decodeRawTelemetryHex("00000000000000000000000000000000000000"),
    /does not contain TTB1 temperature or TVB1 voltage/
  );
  assertDecoderError(
    () => decoder.decodeRawTelemetryHex(TEMPERATURE_PACKET.slice(0, -2)),
    /payload bytes are missing/
  );
  assertDecoderError(
    () => decoder.decodeRawTelemetryHex(`${TEMPERATURE_PACKET}${VOLTAGE_PACKET}`),
    /More than one telemetry snapshot/
  );
});

test("exports raw telemetry rows as CSV", () => {
  const decoded = decoder.decodeRawTelemetryHex(VOLTAGE_PACKET);
  const csv = decoder.modelToCsv(decoder.tableModel(decoded, false));
  assert.match(csv, /Timestamp \(UTC\)/);
  assert.match(csv, /2\.86 V/);
  assert.strictEqual(csv.split("\r\n").length, 9);
});

process.stdout.write(`1..${passed}\n`);
