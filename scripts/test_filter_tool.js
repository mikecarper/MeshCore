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
    snr: 6,
    blacklist: false,
    buckets: [],
    loopLevel: 0,
    ...overrides,
  };
}

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

test("flags lockout, future-type, spoofing, and policy-stop risks", () => {
  const broad = tool.parseDefinition("policy set broad phase=forward owner=admin priority=255 when route=any type=any hops=all do drop stop=policy");
  const other = tool.parseDefinition("policy set future phase=forward owner=filter priority=1 when route=flood type=class:other hops=all do drop");
  const sender = tool.parseDefinition('policy set sender phase=content owner=filter priority=1 when route=flood type=grp_txt hops=all sender="Noisy User" do drop');
  assert.ok(tool.ruleWarnings(broad).some((warning) => /remote administration/.test(warning)));
  assert.ok(tool.ruleWarnings(broad).some((warning) => /mandatory protocol/.test(warning)));
  assert.ok(tool.ruleWarnings(other).some((warning) => /future types/.test(warning)));
  assert.ok(tool.ruleWarnings(sender).some((warning) => /spoofable/.test(warning)));
});

process.stdout.write(`# ${passed} filter-policy playground tests passed\n`);
