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
  assert.deepStrictEqual(tool.parseDefinition(tool.buildDefinition(rule)), rule);
});

test("omits defaults from a catch-all rule without narrowing its match", () => {
  const full = "policy set catch-all phase=forward owner=filter priority=0 mode=active when route=flood type=any hops=all rx.scope=any via=radio tempradio=any do drop";
  const compact = "policy set catch-all when do drop";
  const rule = tool.parseDefinition(full);
  assert.strictEqual(tool.buildDefinition(rule), compact);
  assert.deepStrictEqual(tool.parseDefinition(compact), rule);
  assert.strictEqual(rule.priority, 0);
  assert.strictEqual(tool.matchRule(rule, packet({ type: "ota", channel: "", hops: 0 })).matched, true);
  assert.strictEqual(tool.matchRule(rule, packet({ route: "scoped_flood", type: "req", channel: "", hops: 63 })).matched, true);
});

test("infers processing phase and owner only when they can be omitted safely", () => {
  const definitions = [
    ["policy set scope when do scope=#local", "rewrite", "scope"],
    ["policy set region when do region=sea", "rewrite", "scope"],
    ["policy set gate when do scope-gate=require", "scope_gate", "scope"],
    ["policy set retry when do retry=bucket:2", "schedule", "filter"],
    ['policy set sender when type=grp_txt sender="Noisy User" do drop', "content", "filter"],
    ["policy set tagged when do tag=seen", "forward", "filter"],
    ["policy set timed when do timing=fast", "forward", "filter"],
    ["policy set content phase=content when do drop", "content", "filter"],
    ["policy set delegated owner=filter when do scope=#local", "rewrite", "filter"],
    ["policy set admin owner=admin when do drop stop=policy", "forward", "admin"],
    ["policy set system owner=system when do drop", "forward", "system"],
    ["policy set scheduled phase=schedule when do queue=high", "schedule", "filter"],
  ];
  for (const [definition, phase, owner] of definitions) {
    const rule = tool.parseDefinition(definition);
    assert.strictEqual(rule.phase, phase, definition);
    assert.strictEqual(rule.owner, owner, definition);
    assert.strictEqual(tool.buildDefinition(rule), definition);
    assert.deepStrictEqual(tool.parseDefinition(tool.buildDefinition(rule)), rule);
  }
  assertToolError(() => tool.parseDefinition(
    'policy set wrong phase=forward when sender="Noisy User" do drop'
  ), /only in the content phase/);
  assertToolError(() => tool.parseDefinition(
    "policy set wrong owner=scope when do drop"
  ), /ACL 4 rules may run only/);
});

test("preserves explicit priorities and all nondefault match and action fields", () => {
  const full = "policy set nondefaults phase=schedule owner=admin priority=100 mode=shadow when route=scoped_flood type=grp_txt hops=2-6 channel=public rx.scope=allowed hashbytes=2+ path=prefix:ABCD via=bridge,cross tempradio=inactive do drop rate=12/min burst=3 timing=slow queue=high retry=bucket:4/3 tag=matched stop=policy";
  const rule = tool.parseDefinition(full);
  const compact = tool.buildDefinition(rule);
  assert.strictEqual(compact,
    "policy set nondefaults owner=admin priority=100 mode=shadow when route=scoped_flood type=grp_txt hops=2-6 channel=public rx.scope=allowed hashbytes=2+ path=prefix:ABCD via=bridge,cross tempradio=inactive do drop rate=12/min burst=3 timing=slow queue=high retry=bucket:4/3 tag=matched stop=policy");
  assert.deepStrictEqual(tool.parseDefinition(compact), rule);
  for (const priority of [0, 1, 100, 255]) {
    const variant = tool.normalizeRule({ ...rule, priority });
    const exported = tool.buildDefinition(variant);
    assert.strictEqual(exported.includes("priority="), priority !== 0);
    assert.deepStrictEqual(tool.parseDefinition(exported), variant);
    const imported = tool.parsePolicyInput(JSON.stringify(tool.policyDocument([variant])))[0];
    assert.strictEqual(imported.priority, priority);
    assert.strictEqual(tool.decodeBundle(tool.encodeBundle([variant]))[0].priority, priority);
  }
});

test("omits default rate bursts and retry attempts without changing their values", () => {
  for (const [definition, compact] of [
    ["policy set rate when do rate=10/min burst=10", "policy set rate when do rate=10/min"],
    ["policy set burst when do rate=10/min burst=2", "policy set burst when do rate=10/min burst=2"],
    ["policy set retry when do retry=bucket:3/1", "policy set retry when do retry=bucket:3"],
    ["policy set attempts when do retry=bucket:3/4", "policy set attempts when do retry=bucket:3/4"],
  ]) {
    const rule = tool.parseDefinition(definition);
    assert.strictEqual(tool.buildDefinition(rule), compact);
    assert.deepStrictEqual(tool.parseDefinition(compact), rule);
  }
  assert.strictEqual(tool.parseDefinition("policy set rate when do rate=10/min").burst, 10);
  assert.strictEqual(tool.parseDefinition("policy set retry when do retry=bucket:3").retryAttempts, 1);
});

test("compact exports preserve every normalized example field and full bundle schema", () => {
  for (const rules of Object.values(tool.EXAMPLES)) {
    for (const input of rules) {
      const rule = tool.normalizeRule(input);
      assert.deepStrictEqual(tool.parseDefinition(tool.buildDefinition(rule)), rule);
      const document = tool.policyDocument([rule]);
      assert.deepStrictEqual(document.rules[0], rule);
      for (const key of ["phase", "owner", "priority", "route", "type", "hops", "burst", "retryAttempts"]) {
        assert.ok(Object.prototype.hasOwnProperty.call(document.rules[0], key), key);
      }
    }
  }
  for (const example of ["hash_width", "hash_width_two_plus", "channel_scope", "blackhole", "scope_rewrite", "prefix_rate", "high_traffic", "moderation", "blacklist"]) {
    for (const rule of tool.EXAMPLES[example]) {
      assert.strictEqual(rule.priority, 0, example);
      assert.doesNotMatch(tool.buildDefinition(rule), /priority=/);
    }
  }
});

test("treats channel star as no channel condition", () => {
  const rule = tool.parseDefinition(
    "policy set everything phase=forward owner=filter priority=1 when route=flood type=any hops=all channel=* do drop"
  );
  assert.strictEqual(rule.channel, "");
  assert.strictEqual(tool.matchRule(rule, packet({ type: "grp_data" })).matched, true);
  assert.strictEqual(tool.matchRule(
    rule, packet({ type: "ota", channel: "" })
  ).matched, true);
  assert.doesNotMatch(tool.buildDefinition(rule), /channel=/);
});

test("canonicalizes and matches one-byte channel hash selectors", () => {
  const definition = "policy set short-channel phase=forward owner=filter priority=1 when route=flood type=grp_data hops=all channel=short:a7 do tag=seen";
  const rule = tool.parseDefinition(definition);
  assert.strictEqual(rule.channel, "hash:A7");
  assert.match(tool.buildDefinition(rule), /channel=hash:A7/);
  assert.strictEqual(tool.matchRule(rule, packet({ channel: "A7" })).matched, true);
  assert.strictEqual(tool.matchRule(rule, packet({ channel: "hash:A8" })).matched, false);
  assert.ok(tool.ruleWarnings(rule).some((warning) => /unauthenticated/.test(warning)));
});

test("extracts a visible group channel hash from raw packets", () => {
  const decoded = tool.decodeRawPacketHex("15 00 A7 00 00");
  assert.strictEqual(decoded.type, "grp_txt");
  assert.strictEqual(decoded.channelHash, "hash:A7");
  assert.ok(decoded.notes.some((note) => /hash:XX rule/.test(note)));
});

test("keeps a payload class and path bucket in one rule", () => {
  const definition = "policy set other-bucket phase=rewrite owner=scope priority=130 when route=flood type=class:other hops=all path=bucket:2 do scope=#BlackHole86 timing=slow";
  const rule = tool.parseDefinition(definition);
  assert.strictEqual(rule.type, "class:other");
  assert.strictEqual(rule.pathKind, "bucket:2");
  assert.deepStrictEqual(tool.parseDefinition(tool.buildDefinition(rule)), rule);
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
  assert.strictEqual(tool.buildDefinition(tool.parseDefinition(definition)),
    'policy set noisy-user priority=150 when type=grp_txt channel=public sender="Noisy User" do rate=5/min tag=public-rate');
  const quoted = 'policy set quoted-user phase=content owner=filter priority=1 when route=flood type=grp_txt hops=all sender="Noisy \\"User\\"" do drop';
  assert.deepStrictEqual(tool.parseDefinition(tool.buildDefinition(tool.parseDefinition(quoted))),
    tool.parseDefinition(quoted));
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

test("loads the playground policy examples", () => {
  Object.values(tool.EXAMPLES).forEach((rules) => rules.forEach(tool.normalizeRule));
  assert.strictEqual(
    tool.buildDefinition(tool.EXAMPLES.channel_scope[0]),
    "policy set rgdata-scope when type=class:group channel=#rgdata do scope=#BlackHole86 timing=fast"
  );
  assert.match(tool.explainRule(tool.EXAMPLES.channel_scope[0]), /the #rgdata channel/);
  assert.doesNotMatch(tool.explainRule(tool.EXAMPLES.channel_scope[0]), /authenticated/i);
  assert.strictEqual(
    tool.buildDefinition(tool.EXAMPLES.blackhole[0]),
    "policy set blackhole-unscoped-rgdata when type=grp_data channel=#rgdata rx.scope=none do scope=#BlackHole86 timing=fast"
  );
  assert.strictEqual(tool.EXAMPLES.channel_stop, undefined);
  assert.strictEqual(
    tool.buildDefinition(tool.EXAMPLES.prefix_rate[0]),
    "policy set prefix-860c-rate when path=prefix:860C do rate=10/min"
  );
  assert.deepStrictEqual(
    tool.EXAMPLES.high_traffic.map((rule) => `${rule.type}:${rule.hops}`),
    ["req:3+", "response:9+", "grp_data:3+", "anon_req:9+", "path:9+", "control:1+"]
  );
});

test("orders by phase, priority, channel specificity, and stable ASCII ID", () => {
  const definitions = [
    "policy set z-last phase=forward owner=filter priority=20 when route=flood type=any hops=all do tag=z",
    "policy set rewrite-first phase=rewrite owner=scope priority=1 when route=flood type=any hops=all do scope=#x",
    "policy set b-middle phase=forward owner=filter priority=30 when route=flood type=any hops=all do tag=b",
    "policy set A-first phase=forward owner=filter priority=20 when route=flood type=any hops=all do tag=a",
    "policy set hash-middle phase=forward owner=filter priority=20 when route=flood "
      + "type=grp_txt hops=all channel=hash:11 do tag=hash",
    "policy set exact-first phase=forward owner=filter priority=20 when route=flood "
      + "type=grp_txt hops=all channel=public do tag=exact",
    "policy set hash-override phase=forward owner=filter priority=21 when route=flood "
      + "type=grp_txt hops=all channel=hash:11 do tag=override",
  ];
  assert.deepStrictEqual(
    tool.sortedRules(definitions.map(tool.parseDefinition)).map((rule) => rule.id),
    ["rewrite-first", "b-middle", "hash-override", "exact-first", "hash-middle", "A-first", "z-last"]
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
  assertToolError(() => tool.normalizePacket(packet({ type: "req" })), /include a channel/);
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

test("factory has three rules with independent radio and transport decisions", () => {
  const rules = tool.EXAMPLES.factory;
  assert.strictEqual(rules.length, 3);
  for (const via of ["radio", "bridge", "cross"]) {
    for (const hops of [0, 4, 5, 63]) {
      const facts = packet({ channel: "#wardriving", via, hops });
      assert.strictEqual(tool.simulatePolicy(rules, facts).decision.drop,
        via !== "radio" || hops >= 5);
      assert.strictEqual(tool.simulatePolicy(rules,
        { ...facts, channel: "#other", tempRadio: true }).decision.drop, false);
    }
  }
});

test("combined transport selector survives readable and bundle round trips", () => {
  const rule = tool.EXAMPLES.factory[2];
  const definition = tool.buildDefinition(rule);
  assert.ok(definition.includes("via=bridge,cross"));
  assert.strictEqual(tool.parseDefinition(definition).via, "bridge,cross");
  assert.strictEqual(tool.decodeBundle(tool.encodeBundle([rule]))[0].via, "bridge,cross");
  assert.strictEqual(tool.normalizeRule({ ...rule, via: "cross,bridge" }).via, "bridge,cross");
});

test("path hash width survives definitions and bundle round trips", () => {
  assert.strictEqual(tool.buildDefinition(tool.EXAMPLES.hash_width[0]),
    "policy set drop-one-byte-paths when hashbytes=1 do drop");
  for (const hashBytes of [1, 2, 3]) {
    const rule = tool.normalizeRule({ ...tool.EXAMPLES.factory[2], hashBytes });
    const definition = tool.buildDefinition(rule);
    assert.ok(definition.includes(`hashbytes=${hashBytes}`));
    assert.strictEqual(tool.parseDefinition(definition).hashBytes, hashBytes);
    assert.strictEqual(tool.decodeBundle(tool.encodeBundle([rule]))[0].hashBytes, hashBytes);
    assert.match(tool.explainRule(rule), new RegExp(`${hashBytes}-byte encoded path hashes`));
  }
});

test("width-only rules match all IDs and zero hops without widening other conditions", () => {
  for (const wanted of [1, 2, 3]) {
    const rule = { ...tool.EXAMPLES.factory[2], hashBytes: wanted };
    for (const actual of [1, 2, 3]) {
      for (const path of ["", "12".repeat(actual), "AB".repeat(actual)]) {
        const facts = packet({ via: "bridge", channel: "#wardriving", path,
          hops: path ? 1 : 0, hashBytes: actual });
        assert.strictEqual(tool.matchRule(rule, facts).matched, wanted === actual);
        assert.strictEqual(tool.matchRule(rule, { ...facts, channel: "#other" }).matched, false);
      }
    }
  }
});

test("wildcard width preserves existing behavior and explicit conflicting widths fail", () => {
  const base = tool.EXAMPLES.factory[2];
  for (const hashBytes of [undefined, 0, "any", "*"])
    assert.strictEqual(tool.normalizeRule({ ...base, hashBytes }).hashBytes, 0);
  for (const hashBytes of [4, -1, "1x"])
    assertToolError(() => tool.normalizeRule({ ...base, hashBytes }), /Path hash bytes/);
  assertToolError(() => tool.normalizeRule({ ...base, hashBytes: 1,
    pathKind: "prefix", pathPrefix: "1234" }), /conflicts with prefix width/);
  assertToolError(() => tool.normalizePacket(packet({ hashBytes: 1 })), /conflicts with received path width/);
  const decoded = tool.decodeRawPacketHex("15 80 A7 00 00");
  assert.strictEqual(decoded.hops, 0);
  assert.strictEqual(tool.normalizePacket(packet({ path: "", hops: 0,
    hashBytes: decoded.pathHashBytes })).hashBytes, 3);
});

test("2+ matches both supported larger widths, including zero hops, and round trips", () => {
  const rule = tool.EXAMPLES.hash_width_two_plus[0];
  const definition = tool.buildDefinition(rule);
  assert.ok(definition.includes("hashbytes=2+"));
  assert.strictEqual(tool.parseDefinition(definition).hashBytes, "2+");
  assert.strictEqual(tool.decodeBundle(tool.encodeBundle([rule]))[0].hashBytes, "2+");
  assert.match(tool.explainRule(rule), /2- or 3-byte/);
  for (const actual of [1, 2, 3]) {
    for (const path of ["", "12".repeat(actual)]) {
      const facts = packet({ path, hops: path ? 1 : 0, hashBytes: actual });
      assert.strictEqual(tool.matchRule(rule, facts).matched, actual >= 2);
    }
  }
});

test("2+ works with 2- and 3-byte prefixes but rejects incompatible or invalid ranges", () => {
  const base = tool.EXAMPLES.hash_width_two_plus[0];
  for (const prefix of ["1234", "123456"]) {
    const rule = tool.normalizeRule({ ...base, pathKind: "prefix", pathPrefix: prefix });
    assert.strictEqual(tool.parseDefinition(tool.buildDefinition(rule)).hashBytes, "2+");
    assert.strictEqual(tool.matchRule(rule, packet({ path: prefix })).matched, true);
    assert.strictEqual(tool.matchRule(rule, packet({ path: "", hops: 0, hashBytes: 2 })).matched, false);
  }
  assertToolError(() => tool.normalizeRule({ ...base, pathKind: "prefix", pathPrefix: "12" }), /conflicts with prefix width/);
  for (const hashBytes of ["1+", "3+", "4+", "02+", "2++"])
    assertToolError(() => tool.normalizeRule({ ...base, hashBytes }), /Path hash bytes/);
  assertToolError(() => tool.normalizePacket(packet({ path: "", hashBytes: "2+" })), /Packet path hash bytes/);
});

process.stdout.write(`# ${passed} filter-policy playground tests passed\n`);
