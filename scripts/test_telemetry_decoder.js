"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
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
const EXTERNAL_VOLTAGE_PACKET =
  "3E00495642311122334455667788800092651E00020800000004019026927109C427107FFF";

function pack15(codes) {
  const output = Buffer.alloc(Math.ceil((codes.length * 15) / 8));
  let bitOffset = 0;
  codes.forEach((code) => {
    for (let bit = 14; bit >= 0; bit -= 1, bitOffset += 1) {
      if (code & (1 << bit)) {
        output[Math.floor(bitOffset / 8)] |= 1 << (7 - (bitOffset % 8));
      }
    }
  });
  return output;
}

function externalPage(epoch, channel, codes) {
  const header = Buffer.alloc(8);
  header[0] = 0x14;
  header.writeUInt32LE(epoch, 1);
  header[5] = 30;
  header[6] = codes.length;
  header[7] = channel;
  return `> ${Buffer.concat([header, pack15(codes)]).toString("base64")}`;
}

function externalPacket(epoch, channel, codes) {
  const header = Buffer.alloc(20);
  header.write("IVB1", 0, "ascii");
  Buffer.from("1122334455667788", "hex").copy(header, 4);
  header.writeUInt32LE(epoch, 12);
  header.writeUInt16LE(30, 16);
  header[18] = channel;
  header[19] = codes.length;
  return Buffer.concat([
    Buffer.from("3e00", "hex"), header, pack15(codes),
  ]).toString("hex").toUpperCase();
}

function pack5(codes) {
  const output = Buffer.alloc(Math.ceil(codes.length * 5 / 8));
  codes.forEach((code, index) => {
    for (let bit = 0; bit < 5; bit += 1) {
      const offset = index * 5 + bit;
      output[offset >> 3] |= ((code >> (4 - bit)) & 1) << (7 - (offset % 8));
    }
  });
  return output;
}

function noisePageBytes(epoch, profile, codes, interval = 15) {
  const header = Buffer.alloc(7);
  header[0] = profile === 1 ? 0x15 : 0x16;
  header.writeUInt32LE(epoch, 1);
  header[5] = interval;
  header[6] = codes.length;
  return Buffer.concat([header, pack5(codes)]);
}

function noisePage(epoch, profile, codes, interval = 15) {
  return `> ${noisePageBytes(epoch, profile, codes, interval).toString("base64")}`;
}

function noisePacketBytes(epoch, profile, codes, interval = 15,
                          source = "1122334455667788") {
  const header = Buffer.alloc(19);
  header.write(profile === 1 ? "N1B1" : "N2B1", 0, "ascii");
  Buffer.from(source, "hex").copy(header, 4);
  header.writeUInt32LE(epoch, 12);
  header.writeUInt16LE(interval, 16);
  header[18] = codes.length;
  return Buffer.concat([header, pack5(codes)]);
}

// Run the real browser entry point. The DOM boundary starts with the empty host
// shipped in Markdown and exposes only controls inserted by the actual script.
function browserFixture(readyState = "complete") {
  const fields = new Map();
  const examples = new Map();
  const downloads = [];
  const blobs = new Map();
  const events = new Map();
  function element(tagName = "div", attributes = {}) {
    const handlers = new Map();
    return {
      tagName, value: "", checked: false, hidden: false, textContent: "", children: [],
      classList: { add() {} },
      getAttribute(name) { return attributes[name] ?? null; },
      setAttribute(name, value) { attributes[name] = value; },
      addEventListener(name, callback) { handlers.set(name, callback); },
      emit(name, event = {}) {
        assert.ok(handlers.has(name), `Missing ${name} handler on ${tagName}`);
        return handlers.get(name)(event);
      },
      append(...children) { this.children.push(...children); },
      appendChild(child) { this.children.push(child); },
      replaceChildren(...children) { this.children = children; },
      focus() { this.focused = true; },
      click() {
        downloads.push({ filename: this.download, blob: blobs.get(this.href) });
      },
      remove() {},
    };
  }
  const root = element();
  let markup = "<p>Enable JavaScript on the documentation website to use the decoder.</p>";
  Object.defineProperty(root, "innerHTML", {
    get() { return markup; },
    set(value) {
      markup = value;
      fields.clear(); examples.clear();
      for (const match of value.matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/gi)) {
        const attributes = Object.fromEntries(
          Array.from(match[2].matchAll(/([\w-]+)="([^"]*)"/g), (item) => [item[1], item[2]])
        );
        const node = element(match[1], attributes);
        node.hidden = /(?:^|\s)hidden(?:\s|$)/.test(match[2]);
        if (attributes["data-role"]) fields.set(attributes["data-role"], node);
        if (attributes["data-telemetry-example"]) {
          examples.set(attributes["data-telemetry-example"], node);
        }
      }
    },
  });
  root.querySelector = (selector) => {
    const match = selector.match(/^\[data-role='([^']+)'\]$/);
    return match ? fields.get(match[1]) ?? null : null;
  };
  root.querySelectorAll = (selector) => selector === "[data-telemetry-example]"
    ? Array.from(examples.values()) : [];
  const context = {
    atob: globalThis.atob, URLSearchParams, location: { search: "" },
    Blob: class { constructor(parts, options) { this.parts = parts; this.options = options; } },
    URL: {
      createObjectURL(blob) { const url = `blob:${blobs.size}`; blobs.set(url, blob); return url; },
      revokeObjectURL(url) { blobs.delete(url); },
    },
    document: {
      readyState, body: element("body"), createElement: element,
      createElementNS: (_namespace, tagName) => element(tagName),
      querySelector: (selector) => selector === "[data-telemetry-decoder]" ? root : null,
      addEventListener: (name, callback) => events.set(name, callback),
    },
  };
  vm.runInNewContext(fs.readFileSync(
    path.join(__dirname, "../docs/_javascript/telemetry_decoder.js"), "utf8"
  ), context);
  return { root, fields, examples, downloads, boot() { events.get("DOMContentLoaded")(); } };
}

test("GitHub readers can open the hosted decoder from its packet section", () => {
  const documentation = fs.readFileSync(
    path.join(__dirname, "../docs/telemetry_decoder.md"), "utf8"
  );
  const hostedDecoder =
    "https://mikecarper.github.io/MeshCore/telemetry_decoder/#decode-a-packet";
  const introduction = documentation.split(/^## /m)[0];
  assert.ok(introduction.includes(
    'class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/telemetry_decoder/"'
  ), "The source-visible banner must link to the canonical hosted page.");
  assert.ok(!introduction.includes(hostedDecoder),
    "The introduction must use the banner that disappears on the official host.");
  const packetSection = documentation.split("## Decode a packet\n")[1]
    .split(/^## /m)[0];
  assert.ok(packetSection.includes(`href="${hostedDecoder}"`),
    "The GitHub #decode-a-packet anchor must lead readers to the working tool.");
  assert.match(documentation, /GitHub's Markdown preview[\s\S]*cannot run/);
  assert.doesNotMatch(documentation, /<(?:textarea|input|button)\b/i,
    "GitHub Markdown must not display a browser-only form.");
  assert.match(documentation, /<div class="telemetry-tool" data-telemetry-decoder>/);
  const configuration = fs.readFileSync(path.join(__dirname, "../mkdocs.yml"), "utf8");
  assert.match(configuration, /^\s+- _javascript\/telemetry_decoder\.js\?v=\S+$/m,
    "A versioned script prevents the old cached script from using the new empty host.");
});

test("decoder and CLI commands come before the detailed reference", () => {
  const documentation = fs.readFileSync(
    path.join(__dirname, "../docs/telemetry_decoder.md"), "utf8"
  );
  const headings = Array.from(documentation.matchAll(/^## (.+)$/gm), match => match[1].trim());
  assert.deepStrictEqual(headings.slice(0, 3), [
    "Decode a packet", "CLI history pages", "Send raw telemetry packets",
  ]);
});

test("browser initialization creates the form and wires every example", () => {
  const fixture = browserFixture();
  assert.match(fixture.root.innerHTML, /<textarea[\s\S]*data-role="input"/);
  for (const [example, count] of [
    ["packetTemperature", 8], ["packetVoltage", 8],
    ["packetExternalVoltage", 8], ["externalVoltage", 48],
    ["packetNoise", 8], ["noiseSecondary", 8],
  ]) {
    fixture.examples.get(example).emit("click");
    assert.strictEqual(fixture.fields.get("results").hidden, false,
      fixture.fields.get("error").textContent);
    assert.strictEqual(fixture.fields.get("error").hidden, true);
    assert.strictEqual(fixture.fields.get("table").children[1].children.length, count);
  }
});

test("browser form waits for DOM ready and preserves decode, clear and CSV", () => {
  const fixture = browserFixture("loading");
  assert.strictEqual(fixture.fields.size, 0);
  fixture.boot();
  fixture.fields.get("input").value = VOLTAGE_PACKET;
  let prevented = false;
  fixture.fields.get("input").emit("keydown", {
    key: "Enter", ctrlKey: true, preventDefault() { prevented = true; },
  });
  assert.strictEqual(prevented, true);
  fixture.fields.get("download").emit("click");
  assert.strictEqual(fixture.downloads.length, 1);
  assert.strictEqual(fixture.downloads[0].filename,
    "meshcore-telemetry-1122334455667788-voltage.csv");
  const csv = fixture.downloads[0].blob.parts.join("");
  assert.match(csv, /2\.86 V/);
  assert.strictEqual(csv.split("\r\n").length, 9);
  fixture.fields.get("clear").emit("click");
  assert.strictEqual(fixture.fields.get("input").value, "");
  assert.strictEqual(fixture.fields.get("results").hidden, true);
  fixture.fields.get("input").value = "not a telemetry packet";
  fixture.fields.get("decode").emit("click");
  assert.strictEqual(fixture.fields.get("error").hidden, false);
  fixture.examples.get("packetTemperature").emit("click");
  assert.strictEqual(fixture.fields.get("error").hidden, true);
  assert.strictEqual(fixture.fields.get("results").hidden, false);
});

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

test("decodes full-range I2C voltage packets", () => {
  const decoded = decoder.decodeTelemetry(EXTERNAL_VOLTAGE_PACKET);
  assert.strictEqual(decoded.kind, "external-voltage");
  assert.strictEqual(decoded.typeCode, "IVB1");
  assert.strictEqual(decoded.channel, 2);
  assert.strictEqual(decoded.count, 8);
  assert.deepStrictEqual(
    decoded.rows.map((row) => [row.status, row.millivolts]),
    [
      ["Missing", null],
      ["Value", 20],
      ["Value", 1000],
      ["Value", 12340],
      ["Value", 100000],
      ["Value", 200000],
      ["Value", 400000],
      ["Value", 655340],
    ]
  );
});

test("decodes and merges four I2C voltage CLI pages", () => {
  const firstEpoch = 1704067200;
  const pages = [];
  for (let page = 3; page >= 0; page -= 1) {
    const codes = Array.from(
      { length: 48 }, (_, index) => page * 48 + index + 1
    );
    pages.push(externalPage(firstEpoch + page * 48 * 1800, 7, codes));
  }
  const decoded = decoder.decodeTelemetry(pages.join("\n"));
  assert.strictEqual(decoded.kind, "external-voltage");
  assert.strictEqual(decoded.channel, 7);
  assert.strictEqual(decoded.count, 192);
  assert.strictEqual(decoded.pageCount, 4);
  assert.strictEqual(decoded.rows[0].millivolts, 20);
  assert.strictEqual(decoded.rows[191].millivolts, 3840);
  const csv = decoder.modelToCsv(decoder.tableModel(decoded, false));
  assert.strictEqual(csv.split("\r\n").length, 193);
});

test("merges sequential IVB1 chunks but rejects mixed channels", () => {
  const firstEpoch = 1704067200;
  const first = externalPacket(firstEpoch, 2, [1, 2, 3]);
  const second = externalPacket(firstEpoch + 3 * 1800, 2, [4, 5, 6]);
  const decoded = decoder.decodeTelemetry(`${second}\n${first}`);
  assert.strictEqual(decoded.count, 6);
  assert.strictEqual(decoded.pageCount, 2);
  assert.deepStrictEqual(
    decoded.rows.map((row) => row.millivolts),
    [20, 40, 60, 80, 100, 120]
  );
  assertDecoderError(
    () => decoder.decodeTelemetry(
      `${first}\n${externalPacket(firstEpoch + 3 * 1800, 3, [4, 5, 6])}`
    ),
    /same type, source, channel, and interval/
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
    /does not contain TTB1, TVB1, IVB1, N1B1, or N2B1/
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

test("all 32 noise codes agree between CLI and the existing RAW_CUSTOM header", () => {
  const codes = Array.from({ length: 32 }, (_, index) => index);
  for (const profile of [1, 2]) {
    const cli = decoder.decodeTelemetry(noisePage(1704067200, profile, codes));
    const rawBytes = noisePacketBytes(1704067200, profile, codes);
    const raw = decoder.decodeTelemetry(`3e4212345678${rawBytes.toString("hex")}`);
    assert.strictEqual(cli.profile, profile);
    assert.strictEqual(raw.profile, profile);
    assert.strictEqual(raw.sourceId, "1122334455667788");
    assert.strictEqual(raw.payloadOffset, 6);
    assert.strictEqual(raw.packet.hopCount, 2);
    assert.strictEqual(raw.intervalMinutes, 15);
    assert.strictEqual(raw.firstEpoch, 1704067200);
    assert.strictEqual(raw.byteLength, 19 + 20);
    assert.deepStrictEqual(raw.rows, cli.rows);
    assert.deepStrictEqual(cli.rows.slice(0, 3).map((row) => row.noiseDbm), [-120, -119, -118]);
    assert.deepStrictEqual(cli.rows.slice(3, 30).map((row) => row.noiseDbm),
      Array.from({ length: 27 }, (_, index) => -116 + index * 2));
    assert.deepStrictEqual(cli.rows.slice(30).map((row) => [row.status, row.noiseDbm]),
      [["MAX", null], ["Missing", null]]);
    assert.strictEqual(cli.rows[31].epoch, 1704067200 + 31 * 900);
  }
});

test("noise pages fit the current CLI buffer and merge all eight days", () => {
  const epoch = 1704067200;
  const pages = [];
  for (let first = 0; first < 768; first += 176) {
    const count = Math.min(176, 768 - first);
    const page = noisePage(epoch + first * 900, 1, Array(count).fill(12));
    assert.ok(page.length + 1 <= 160, "Reply and terminating NUL fit the existing buffer.");
    pages.unshift(page);
  }
  const decoded = decoder.decodeTelemetry(pages.join("\n"));
  assert.strictEqual(decoded.pageCount, 5);
  assert.strictEqual(decoded.count, 768);
  assert.strictEqual(decoded.rows[0].epoch, epoch);
  assert.strictEqual(decoded.rows[767].epoch, epoch + 767 * 900);
  assert.strictEqual(decoder.decodeTelemetry(pages[4]).byteLength, 117);
  const fullRaw = decoder.decodeTelemetry(
    noisePacketBytes(epoch, 1, Array(255).fill(31)).toString("hex")
  );
  assert.strictEqual(fullRaw.count, 255);
  assert.strictEqual(fullRaw.byteLength, 179);
  assert.ok(fullRaw.rows.every((row) => row.status === "Missing"));
});

test("correlation-prefixed noise replies preserve full pages and merge by timestamp", () => {
  const epoch = 1704067200;
  for (const profile of [1, 2]) {
    const page = noisePage(epoch, profile, Array(176).fill(1));
    const bare = page.slice(2);
    assert.strictEqual(`01|${bare}`.length + 1, 160);
    for (const input of [`01|${bare}`, `02|>${bare}`, `03|> ${bare}`]) {
      const decoded = decoder.decodeTelemetry(input);
      assert.strictEqual(decoded.count, 176);
      assert.strictEqual(decoded.profile, profile);
      assert.ok(decoded.rows.every((row) => row.noiseDbm === -119));
      assert.strictEqual(decoder.extractBase64(input), bare);
    }
    const older = noisePage(epoch - 176 * 900, profile, Array(176).fill(0)).slice(2);
    const merged = decoder.decodeTelemetry(`01|${bare}\n02|>${older}`);
    assert.strictEqual(merged.count, 352);
    assert.strictEqual(merged.pageCount, 2);
    assert.strictEqual(merged.profile, profile);
    assert.strictEqual(merged.rows[0].noiseDbm, -120);
    assert.strictEqual(merged.rows[351].noiseDbm, -119);
  }
});

test("noise decoder rejects wrong interval, count, size, padding and enclosing packet", () => {
  const epoch = 1704067200;
  for (const interval of [0, 14, 30]) {
    assertDecoderError(() => decoder.decodeTelemetry(noisePage(epoch, 1, [0], interval)),
      /interval/);
    assertDecoderError(() => decoder.decodeTelemetry(
      noisePacketBytes(epoch, 1, [0], interval).toString("hex")), /interval/);
  }
  assertDecoderError(() => decoder.decodeTelemetry(noisePage(epoch, 1, [])), /count/);
  assertDecoderError(() => decoder.decodeTelemetry(noisePage(epoch, 1, Array(177).fill(0))),
    /between 1 and 176/);
  const cli = noisePageBytes(epoch, 1, [0]);
  cli[7] = 1;
  assertDecoderError(() => decoder.decodeTelemetry(cli.toString("base64")), /padding bits/);
  const raw = noisePacketBytes(epoch, 1, [0]);
  raw[19] = 1;
  assertDecoderError(() => decoder.decodeTelemetry(raw.toString("hex")), /padding bits/);
  for (const bytes of [noisePageBytes(epoch, 1, [0]), noisePacketBytes(epoch, 1, [0])]) {
    const encode = (value) => bytes[0] === 0x15 ? value.toString("base64") : value.toString("hex");
    assertDecoderError(() => decoder.decodeTelemetry(encode(bytes.subarray(0, bytes.length - 1))),
      /bytes|shorter/);
    assertDecoderError(() => decoder.decodeTelemetry(encode(Buffer.concat([bytes, Buffer.from([0])]))),
      /should be/);
  }
  assertDecoderError(() => decoder.decodeTelemetry(`3a00${noisePacketBytes(epoch, 1, [0]).toString("hex")}`),
    /complete RAW_CUSTOM/);
  assertDecoderError(() => decoder.decodeTelemetry(`3e01${noisePacketBytes(epoch, 1, [0]).toString("hex")}`),
    /complete RAW_CUSTOM/);
});

test("noise merge preserves source, profile and interval boundaries", () => {
  const epoch = 1704067200;
  const first = noisePage(epoch, 1, [0, 1]);
  assertDecoderError(() => decoder.decodeTelemetry(`${first}\n${noisePage(epoch, 2, [0, 1])}`),
    /radio profile/);
  const raw = noisePacketBytes(epoch, 1, [0, 1]).toString("hex");
  assertDecoderError(() => decoder.decodeTelemetry(`${raw}\n${noisePacketBytes(epoch, 2, [0, 1]).toString("hex")}`),
    /radio profile/);
  assertDecoderError(() => decoder.decodeTelemetry(`${raw}\n${noisePacketBytes(epoch, 1, [0, 1], 15, "8877665544332211").toString("hex")}`),
    /same type, source/);
  const decoded = decoder.decodeTelemetry(first);
  assertDecoderError(() => decoder.mergeDecodedTelemetry([decoded, { ...decoded, intervalMinutes: 30 }]),
    /interval/);
});

test("noise table and CSV keep MAX and missing separate from numeric dBm", () => {
  const decoded = decoder.decodeTelemetry(noisePage(1704067200, 2, [0, 1, 29, 30, 31]));
  const model = decoder.tableModel(decoded, false);
  assert.strictEqual(model.rows[3].values[3], ">-64 dBm");
  assert.strictEqual(model.rows[4].values[2], "Missing");
  const csv = decoder.modelToCsv(model);
  assert.strictEqual(csv.split("\r\n")[0],
    "sample,timestamp_utc,radio_profile,status,noise_floor_dbm,raw_code");
  assert.match(csv, /,2,value,-120,0/);
  assert.match(csv, /,2,value,-119,1/);
  assert.match(csv, /,2,value,-64,29/);
  assert.match(csv, /,2,max,,30/);
  assert.match(csv, /,2,missing,,31/);
  const fixture = browserFixture();
  fixture.examples.get("noiseSecondary").emit("click");
  assert.strictEqual(fixture.fields.get("chart").hidden, false);
  fixture.fields.get("download").emit("click");
  assert.strictEqual(fixture.downloads[0].filename, "meshcore-telemetry-noise-radio2.csv");
  assert.match(fixture.downloads[0].blob.parts.join(""), /,2,max,,30/);
  fixture.examples.get("packetVoltage").emit("click");
  assert.strictEqual(fixture.fields.get("chart").hidden, true);
});

test("noise chart leaves MAX, missing and unrecorded time gaps", () => {
  const decoded = decoder.decodeTelemetry(noisePage(1704067200, 1, [0, 1, 30, 29, 31, 2]));
  decoded.rows.push({ ...decoded.rows[0], index: 6, epoch: 1704067200 + 8 * 900 });
  const chart = decoder.noiseChartModel(decoded);
  assert.deepStrictEqual(chart.segments.map((segment) => segment.length), [2, 1, 1, 1]);
  assert.strictEqual(chart.segments[0][0].y, 150);
  assert.strictEqual(chart.segments[1][0].y, 20);
  assert.match(chart.description, /MAX and missing samples leave gaps/);
  assert.strictEqual(decoder.noiseChartModel(decoder.decodeTelemetry(VOLTAGE_PACKET)), null);
});

process.stdout.write(`1..${passed}\n`);
