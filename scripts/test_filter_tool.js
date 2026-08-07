"use strict";

const assert = require("assert");
const tool = require("../docs/_javascript/filter_tool.js");

let passed = 0;

function test(name, callback) {
  callback();
  passed += 1;
  process.stdout.write(`ok ${passed} - ${name}\n`);
}

function assertToolError(callback, pattern) {
  assert.throws(callback, (error) => error instanceof tool.FilterToolError && pattern.test(error.message));
}

function packet(overrides) {
  return {
    route: "unscoped_flood",
    type: "grp_data",
    hops: 4,
    channel: "#rgdata",
    path: "860C,12A4",
    scopeStatus: "none",
    scopeName: "",
    regionName: "",
    sender: "",
    tempRadio: false,
    blacklist: false,
    buckets: [],
    loopLevel: 0,
    ...overrides,
  };
}

const RAW_PACKET_SAMPLE = "014e912ceebb98918b86772df5dacf1bcba9e4127ffffaa8665596aa3e2903a3b1901fdc53497dfca6b5d7df2d771bea68de";

test("decodes the supplied raw MeshCore packet into simulator facts", () => {
  const decoded = tool.decodeRawPacketHex(RAW_PACKET_SAMPLE);
  assert.strictEqual(decoded.rawLength, 50);
  assert.strictEqual(decoded.headerHex, "01");
  assert.strictEqual(decoded.routeName, "flood");
  assert.strictEqual(decoded.simulatorRoute, "unscoped_flood");
  assert.strictEqual(decoded.filterEligible, true);
  assert.strictEqual(decoded.type, "req");
  assert.strictEqual(decoded.payloadVersion, 1);
  assert.strictEqual(decoded.pathLengthHex, "4E");
  assert.strictEqual(decoded.pathHashBytes, 2);
  assert.strictEqual(decoded.hops, 14);
  assert.deepStrictEqual(decoded.pathIds, [
    "912C", "EEBB", "9891", "8B86", "772D", "F5DA", "CF1B",
    "CBA9", "E412", "7FFF", "FAA8", "6655", "96AA", "3E29",
  ]);
  assert.strictEqual(decoded.payloadLength, 20);
  assert.deepStrictEqual(decoded.payloadFields, [
    { label: "Destination hash", value: "03" },
    { label: "Source hash", value: "A3" },
    { label: "Cipher MAC", value: "B190" },
    { label: "Encrypted body", value: "1FDC53497DFCA6B5D7DF2D771BEA68DE" },
  ]);
});

test("decodes transport codes and keeps direct packets out of flood simulation", () => {
  const scoped = tool.decodeRawPacketHex("00 34 12 00 00 02 AA BB");
  assert.strictEqual(scoped.routeName, "transport_flood");
  assert.strictEqual(scoped.simulatorRoute, "scoped_flood");
  assert.deepStrictEqual(scoped.transportCodes, [0x1234, 0]);
  assert.deepStrictEqual(scoped.pathIds, ["AA", "BB"]);

  const direct = tool.decodeRawPacketHex("0200");
  assert.strictEqual(direct.routeName, "direct");
  assert.strictEqual(direct.filterEligible, false);
  assert.strictEqual(direct.simulatorRoute, "");
  assert.ok(direct.notes.some((note) => /outside the flood-policy simulator/.test(note)));
});

test("rejects raw packets current firmware would reject", () => {
  assertToolError(() => tool.decodeRawPacketHex("01"), /at least a header and path-length byte/);
  assertToolError(() => tool.decodeRawPacketHex("01Z0"), /only hexadecimal bytes/);
  assertToolError(() => tool.decodeRawPacketHex("014"), /complete two-character bytes/);
  assertToolError(() => tool.decodeRawPacketHex("01C0"), /mode 3 is reserved/);
  assertToolError(() => tool.decodeRawPacketHex("0142AA"), /encoded path is complete/);
  assertToolError(() => tool.decodeRawPacketHex("41 00"), /Payload version 2 is not supported/);
});

test("builds and parses the BlackHole86 policy definition", () => {
  const definition = "policy set blackhole-rewrite phase=rewrite owner=scope priority=160 when route=flood type=grp_data hops=4+ channel=#rgdata rx.scope=none path=prefix:860C do scope=#BlackHole86 timing=fast stop=phase";
  const rule = tool.parseDefinition(definition);
  assert.strictEqual(rule.targetKind, "scope");
  assert.strictEqual(rule.target, "BlackHole86");
  assert.strictEqual(tool.buildDefinition(rule), definition);
});

test("keeps a payload class and path bucket in one rule", () => {
  const definition = "policy set other-bucket phase=rewrite owner=scope priority=130 when route=flood type=class:other hops=all path=bucket:2 do scope=#BlackHole86 timing=slow";
  const rule = tool.parseDefinition(definition);
  assert.strictEqual(rule.type, "class:other");
  assert.strictEqual(rule.pathKind, "bucket:2");
  assert.strictEqual(tool.buildDefinition(rule), definition);
  assert.match(tool.explainRule(rule), /catch-all non-group, non-login class/);
});

test("enforces ACL 4 scope-manager boundaries", () => {
  assertToolError(
    () => tool.parseDefinition("policy set bad-drop phase=rewrite owner=scope priority=1 when route=flood type=any hops=all do drop"),
    /ACL 4 cannot create general drop/
  );
  assertToolError(
    () => tool.parseDefinition("policy set bad-stop phase=rewrite owner=scope priority=1 when route=flood type=any hops=all do scope=#x stop=policy"),
    /ACL 4 may stop only its current phase/
  );
});

test("enforces ACL 5 filter-manager boundaries", () => {
  assertToolError(
    () => tool.parseDefinition("policy set bad-region phase=rewrite owner=filter priority=1 when route=flood type=any hops=all do region=usa"),
    /ACL 5 cannot select a configured region target/
  );
  const rule = tool.parseDefinition(
    "policy set public-scope phase=rewrite owner=filter priority=1 when route=flood type=any hops=all do scope=#BlackHole86"
  );
  assert.strictEqual(rule.targetKind, "scope");
});

test("limits decrypted sender matching to the content phase", () => {
  assertToolError(
    () => tool.parseDefinition('policy set early-sender phase=forward owner=filter priority=1 when route=flood type=grp_txt hops=all sender="Noisy User" do drop'),
    /only in the content phase/
  );
  const definition = 'policy set noisy-user phase=content owner=filter priority=150 when route=flood type=grp_txt hops=all channel=public sender="Noisy User" do rate=5/min burst=5 tag=public-rate';
  assert.strictEqual(tool.buildDefinition(tool.parseDefinition(definition)), definition);
  const quoted = 'policy set quoted-user phase=content owner=filter priority=1 when route=flood type=grp_txt hops=all sender="Noisy \\"User\\"" do drop';
  assert.strictEqual(tool.buildDefinition(tool.parseDefinition(quoted)), quoted);
});

test("round trips token rate and burst actions", () => {
  const rule = tool.parseDefinition(
    "policy set rate-limit phase=forward owner=filter priority=90 when route=flood type=grp_data hops=3+ channel=#rgdata do rate=10/min burst=3"
  );
  assert.strictEqual(rule.rate, 10);
  assert.strictEqual(rule.burst, 3);
  assert.strictEqual(tool.buildDefinition(rule).endsWith("rate=10/min burst=3"), true);
  assertToolError(
    () => tool.parseDefinition("policy set zero-rate phase=forward owner=filter priority=1 when route=flood type=any hops=all do rate=0/min"),
    /Token rate must be 1-65534/
  );
});

test("encodes and decodes a complete playground bundle", () => {
  const rules = tool.EXAMPLES.mixed.map(tool.normalizeRule);
  const bundle = tool.encodeBundle(rules);
  assert.ok(bundle.startsWith(tool.BUNDLE_PREFIX));
  assert.deepStrictEqual(tool.decodeBundle(bundle), tool.sortedRules(rules));
  assert.deepStrictEqual(tool.parsePolicyInput(bundle), tool.sortedRules(rules));
});

test("imports JSON and multiple readable definitions", () => {
  const rules = tool.EXAMPLES.blackhole.map(tool.normalizeRule);
  const json = JSON.stringify(tool.policyDocument(rules));
  assert.deepStrictEqual(tool.parsePolicyInput(json), rules);
  const readable = [
    "# a local note",
    tool.buildDefinition(tool.EXAMPLES.blackhole[0]),
    tool.buildDefinition(tool.EXAMPLES.system[0]),
  ].join("\n");
  assert.strictEqual(tool.parsePolicyInput(readable).length, 2);
});

test("loads proposed replacements for the documented commands", () => {
  Object.values(tool.EXAMPLES).forEach((rules) => rules.forEach(tool.normalizeRule));
  assert.strictEqual(
    tool.buildDefinition(tool.EXAMPLES.channel_scope[0]),
    "policy set rgdata-scope phase=rewrite owner=scope priority=100 when route=flood type=class:group hops=all channel=#rgdata do scope=#BlackHole86 timing=fast"
  );
  assert.strictEqual(
    tool.buildDefinition(tool.EXAMPLES.blackhole[0]),
    "policy set blackhole-after-hop-3 phase=rewrite owner=scope priority=100 when route=flood type=grp_data hops=4+ channel=#rgdata rx.scope=none do scope=#BlackHole86 timing=fast"
  );
  assert.strictEqual(
    tool.buildDefinition(tool.EXAMPLES.prefix_rate[0]),
    "policy set prefix-860c-rate phase=forward owner=filter priority=100 when route=flood type=any hops=all path=prefix:860C do rate=10/min burst=10"
  );
  assert.deepStrictEqual(
    tool.EXAMPLES.high_traffic.map((rule) => `${rule.type}:${rule.hops}`),
    ["req:3+", "response:9+", "grp_data:3+", "anon_req:9+", "path:9+", "control:1+"]
  );
});

test("orders by phase, descending priority, and stable ASCII ID", () => {
  const definitions = [
    "policy set z-last phase=forward owner=filter priority=20 when route=flood type=any hops=all do tag=z",
    "policy set rewrite-first phase=rewrite owner=scope priority=1 when route=flood type=any hops=all do scope=#x",
    "policy set b-middle phase=forward owner=filter priority=30 when route=flood type=any hops=all do tag=b",
    "policy set A-first phase=forward owner=filter priority=20 when route=flood type=any hops=all do tag=a",
  ];
  assert.deepStrictEqual(
    tool.sortedRules(definitions.map(tool.parseDefinition)).map((rule) => rule.id),
    ["rewrite-first", "b-middle", "A-first", "z-last"]
  );
});

test("matches every rule against immutable receive-time scope", () => {
  const rules = [
    tool.parseDefinition("policy set rewrite phase=rewrite owner=scope priority=200 when route=flood type=grp_data hops=all rx.scope=none do scope=#BlackHole86"),
    tool.parseDefinition("policy set original-none phase=forward owner=filter priority=100 when route=flood type=grp_data hops=all rx.scope=none do drop"),
  ];
  const result = tool.simulatePolicy(rules, packet());
  assert.deepStrictEqual(result.decision.scopeTarget, { kind: "scope", name: "BlackHole86", rule: "rewrite" });
  assert.strictEqual(result.decision.drop, true);
  assert.deepStrictEqual(result.trace.map((entry) => entry.status), ["match", "match"]);
  assert.strictEqual(result.packet.scopeStatus, "none");
});

test("stop=phase skips lower rules only in that phase", () => {
  const rules = [
    tool.parseDefinition("policy set first phase=forward owner=filter priority=200 when route=flood type=any hops=all do tag=first stop=phase"),
    tool.parseDefinition("policy set skipped phase=forward owner=filter priority=100 when route=flood type=any hops=all do drop"),
    tool.parseDefinition("policy set later phase=content owner=filter priority=100 when route=flood type=any hops=all do tag=later"),
  ];
  const result = tool.simulatePolicy(rules, packet());
  assert.deepStrictEqual(result.trace.map((entry) => entry.status), ["match", "stopped", "match"]);
  assert.deepStrictEqual(result.decision.tags, ["first", "later"]);
  assert.strictEqual(result.decision.drop, false);
});

test("stop=policy skips every later configurable phase", () => {
  const rules = [
    tool.parseDefinition("policy set first phase=forward owner=admin priority=200 when route=flood type=any hops=all do tag=first stop=policy"),
    tool.parseDefinition("policy set skipped phase=content owner=filter priority=100 when route=flood type=any hops=all do drop"),
  ];
  const result = tool.simulatePolicy(rules, packet());
  assert.deepStrictEqual(result.trace.map((entry) => entry.status), ["match", "stopped"]);
  assert.strictEqual(result.decision.drop, false);
});

test("shadow rules neither act nor stop later rules", () => {
  const rules = [
    tool.parseDefinition("policy set shadow-drop phase=forward owner=admin priority=200 mode=shadow when route=flood type=any hops=all do drop stop=policy"),
    tool.parseDefinition("policy set active-drop phase=forward owner=filter priority=100 when route=flood type=any hops=all do drop"),
  ];
  const result = tool.simulatePolicy(rules, packet());
  assert.deepStrictEqual(result.trace.map((entry) => entry.status), ["shadow", "match"]);
  assert.strictEqual(result.decision.drop, true);
});

test("drop is sticky while later non-verdict actions still accumulate", () => {
  const rules = [
    tool.parseDefinition("policy set drop-first phase=forward owner=filter priority=200 when route=flood type=any hops=all do drop"),
    tool.parseDefinition("policy set tag-later phase=forward owner=filter priority=100 when route=flood type=any hops=all do tag=observed"),
  ];
  const result = tool.simulatePolicy(rules, packet());
  assert.strictEqual(result.decision.drop, true);
  assert.deepStrictEqual(result.decision.tags, ["observed"]);
});

test("scope-gate require drops an originally unscoped packet", () => {
  const rule = tool.parseDefinition(
    "policy set require-region phase=scope_gate owner=scope priority=200 when route=flood type=class:group hops=all do scope-gate=require stop=phase"
  );
  const result = tool.simulatePolicy([rule], packet());
  assert.strictEqual(result.decision.scopeGate, "require_allowed");
  assert.strictEqual(result.decision.drop, true);
  assert.match(result.trace[0].detail, /scope requirement failed/);
});

test("matches login and future-safe other payload classes", () => {
  const login = tool.parseDefinition("policy set login phase=forward owner=filter priority=1 when route=flood type=class:login hops=all do drop");
  const other = tool.parseDefinition("policy set other phase=forward owner=filter priority=1 when route=flood type=class:other hops=all do drop");
  assert.strictEqual(tool.matchRule(login, packet({ type: "req", channel: "" })).matched, true);
  assert.strictEqual(tool.matchRule(login, packet({ type: "ota", channel: "" })).matched, false);
  assert.strictEqual(tool.matchRule(other, packet({ type: "ota", channel: "" })).matched, true);
  assert.strictEqual(tool.matchRule(other, packet({ type: "grp_data" })).matched, false);
  assertToolError(() => tool.normalizePacket(packet({ type: "class:group", channel: "" })), /exact known type/);
  assertToolError(() => tool.normalizePacket(packet({ type: "req" })), /authenticated channel/);
});

test("keeps direct routes and SNR outside the flood policy schema", () => {
  assertToolError(
    () => tool.parseDefinition("policy set direct-drop phase=forward owner=filter priority=1 when route=direct type=any hops=all do drop"),
    /Route matcher is invalid/
  );
  assertToolError(() => tool.normalizePacket(packet({ route: "direct" })), /Packet route is invalid/);
  assertToolError(
    () => tool.parseDefinition("policy set signal-drop phase=forward owner=filter priority=1 when route=flood type=any hops=all snr=..-8 do drop"),
    /Unsupported receive-time matcher/
  );
});

test("matches ordered pbyte prefixes, buckets, and loop thresholds", () => {
  const prefix = tool.parseDefinition("policy set path-prefix phase=forward owner=filter priority=1 when route=flood type=any hops=all path=prefix:86,0C do drop");
  const bucket = tool.parseDefinition("policy set path-bucket phase=forward owner=filter priority=1 when route=flood type=any hops=all path=bucket:2 do drop");
  const moderate = tool.parseDefinition("policy set loop phase=forward owner=filter priority=1 when route=flood type=any hops=all path=loop:moderate do drop");
  const facts = packet({ channel: "", path: "86,0C,AA", buckets: [2], loopLevel: 2 });
  assert.strictEqual(tool.matchRule(prefix, facts).matched, true);
  assert.strictEqual(tool.matchRule(bucket, facts).matched, true);
  assert.strictEqual(tool.matchRule(moderate, facts).matched, true);
  assertToolError(
    () => tool.parseDefinition("policy set mixed-width phase=forward owner=filter priority=1 when route=flood type=any hops=all path=prefix:86,1234 do drop"),
    /same 2, 4, or 6 hex-character width/
  );
});

test("reports duplicate IDs, rewrite conflicts, and target overflow", () => {
  const first = tool.parseDefinition("policy set duplicate phase=rewrite owner=scope priority=100 when route=flood type=any hops=all do scope=#one");
  const second = tool.parseDefinition("policy set duplicate phase=rewrite owner=scope priority=100 when route=flood type=any hops=all do scope=#two");
  const duplicates = tool.policyWarnings([first, second], "nrf52");
  assert.ok(duplicates.some((warning) => /duplicated/.test(warning)));
  assert.ok(duplicates.some((warning) => /overlapping core matches/.test(warning)));

  const large = Array.from({ length: 90 }, (_unused, index) => tool.parseDefinition(
    `policy set size-${index} phase=forward owner=filter priority=1 when route=flood type=any hops=all do tag=tag-${index}`
  ));
  assert.ok(tool.policyWarnings(large, "stm32").some((warning) => /exceeds/.test(warning)));
});

test("flags remote-login reach, future-type, spoofing, and policy-stop risks", () => {
  const broad = tool.parseDefinition("policy set broad phase=forward owner=admin priority=255 when route=flood type=any hops=all do drop stop=policy");
  const other = tool.parseDefinition("policy set future phase=forward owner=filter priority=1 when route=flood type=class:other hops=all do drop");
  const sender = tool.parseDefinition('policy set sender phase=content owner=filter priority=1 when route=flood type=grp_txt hops=all sender="Noisy User" do drop');
  const channelOnly = tool.parseDefinition("policy set channel-only phase=forward owner=filter priority=1 when route=flood type=any hops=all channel=#wardriving do drop");
  assert.ok(tool.ruleWarnings(broad).some((warning) => /Remote-management relay risk/.test(warning)));
  assert.ok(tool.policyWarnings([broad], "nrf52").some((warning) => /end-to-end flood login reach is not guaranteed/.test(warning)));
  assert.ok(tool.ruleWarnings(tool.EXAMPLES.high_traffic[0]).some((warning) => /Remote-management reach warning/.test(warning)));
  assert.ok(!tool.ruleWarnings(channelOnly).some((warning) => /Remote-management/.test(warning)));
  assert.ok(!tool.ruleWarnings(tool.EXAMPLES.blacklist[0]).some((warning) => /global drop/.test(warning)));
  assert.ok(tool.ruleWarnings(broad).some((warning) => /mandatory protocol/.test(warning)));
  assert.ok(tool.ruleWarnings(other).some((warning) => /future types/.test(warning)));
  assert.ok(tool.ruleWarnings(sender).some((warning) => /spoofable/.test(warning)));
});

process.stdout.write(`# ${passed} filter-policy playground tests passed\n`);
