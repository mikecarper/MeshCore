"use strict";

const assert = require("assert");
const fs = require("fs");
const tool = require("../docs/_javascript/preset_test.js");

const pageSource = fs.readFileSync("docs/preset_test.md", "utf8");
assert.match(pageSource, /data-role="preset-test-page-title"/);
assert.match(pageSource, /data-role="preset-test-page-summary"/);
assert.match(pageSource, /data-role="preset-test-eyebrow"/);
assert.match(pageSource, /data-role="timezone-map"/);
assert.match(pageSource, /type="hidden" name="tz"/);
assert.doesNotMatch(pageSource, /<select name="tz"/);
assert.match(pageSource, /data-role="test-content" hidden/);
assert.match(pageSource, /data-role="test-content-footer" hidden/);
assert.match(pageSource, /data-role="url-builder-disclosure" open/);
assert.match(pageSource, /class="preset-test-quick-command"/);
assert.match(pageSource, /class="preset-test-generator-disclosure preset-test-stock-leave"/);
assert.match(pageSource, /data-role="keymind-disclosure"/);
assert.match(pageSource, /<details class="preset-test-generator-disclosure preset-test-keymind-disclosure"\s+data-role="keymind-disclosure">/);
const keymindStart = pageSource.indexOf('data-role="keymind-disclosure"');
const keymindEnd = pageSource.indexOf("</details>", keymindStart);
const keymindContent = pageSource.slice(keymindStart, keymindEnd);
assert(pageSource.indexOf('id="stock-test-title"') < keymindStart);
assert(pageSource.indexOf('data-command="stock-now"') <
  pageSource.indexOf('data-role="countdown"'));
assert(pageSource.indexOf('data-command="stock-now"') <
  pageSource.indexOf('data-role="url-builder-disclosure"'));
assert(pageSource.indexOf('data-role="test-content-footer"') < keymindStart);
assert(keymindStart < pageSource.indexOf('data-role="url-builder-disclosure"'));
assert.strictEqual((pageSource.match(/data-command="stock-now"/g) || []).length, 1);
assert.strictEqual((pageSource.match(/data-command="stock-cancel-during"/g) || []).length, 1);
assert.doesNotMatch(keymindContent, /data-command="stock-now"/);
for (const name of ["set-clock", "reset-clock", "companion-now", "primary-scheduled",
  "companion-scheduled", "primary-cancel", "companion-cancel-before",
  "companion-cancel-during"]) {
  assert.match(keymindContent, new RegExp('data-command="' + name + '"'));
}
const openTags = [];
for (const match of pageSource.matchAll(/<\/?(div|details|section|article|summary)\b[^>]*>/g)) {
  if (match[0][1] === "/") {
    assert.strictEqual(openTags.pop(), match[1], "nested page HTML must stay balanced");
  } else {
    openTags.push(match[1]);
  }
}
assert.deepStrictEqual(openTags, []);
assert.match(pageSource, /data-role="node-clock-input"/);
assert.match(pageSource, /placeholder="02:42 22\/9\/2026 UTC"/);
assert.match(pageSource, /data-action="apply-node-clock"/);
assert.match(pageSource, /data-role="node-clock-status"/);
assert.match(pageSource, /data-command="primary-scheduled"/);
assert.match(pageSource, /data-command="primary-cancel"/);
assert.match(pageSource, /name="schedule-command-mode" value="relative" checked/);
assert.match(pageSource, /KeyMind Cascade · <code>\+minutes<\/code>/);
assert.match(pageSource, /name="schedule-command-mode" value="absolute"/);
assert.match(pageSource, /data-role="absolute-clock-controls" hidden/);
assert.match(pageSource, /class="preset-test-time-fields"/);
assert.match(pageSource, /class="preset-test-radio-layout"/);
assert.match(pageSource, /class="preset-test-radio-fields"/);
assert.match(pageSource, /Simple Repeater primary radio/);
assert.doesNotMatch(pageSource, /Stock firmware has no <code>tempradioat<\/code> command/);
assert.doesNotMatch(pageSource, /Normal return profile/);

const query =
  "?start=2026-09-21T17:00:00-07:00" +
  "&end=2026-09-23T17:00:00-07:00" +
  "&tz=America%2FLos_Angeles" +
  "&freq=910.1&bw=500&sf=8&cr=7";
const config = tool.configFromSearch(query);
const defaultNow = Date.parse("2026-09-22T08:00:00.000Z");
const defaults = tool.configFromSearch("", "America/Los_Angeles", defaultNow);

assert.strictEqual(tool.hasPresetParameters(""), false);
assert.strictEqual(tool.hasPresetParameters("?"), false);
assert.strictEqual(tool.hasPresetParameters("?utm_source=example"), false);
assert.strictEqual(
  tool.hasPresetParameters("?node_clock=1790044920&node_clock_at=1790034120"),
  false
);
assert.strictEqual(tool.hasPresetParameters("?freq=911.3"), true);
assert.strictEqual(tool.hasPresetParameters("?tz=UTC"), true);
assert.strictEqual(tool.hasPresetParameters("?start=bad"), true);

const observedAt = Date.parse("2026-09-21T23:42:37.000Z");
const parsedNodeClock = tool.parseNodeClock("02:42 22/9/2026 UTC");
assert.strictEqual(parsedNodeClock, 1790044920);
assert.strictEqual(
  tool.parseNodeClock("02:42 - 22/9/2026 UTC"),
  parsedNodeClock
);
assert.strictEqual(tool.parseNodeClock(""), null);
assert.strictEqual(tool.nodeClockOffsetSeconds(parsedNodeClock, observedAt), 10800);
assert.strictEqual(tool.nodeClockOffsetSeconds(null, observedAt), 0);
assert.strictEqual(tool.formatClockOffset(10800), "3h");
assert.strictEqual(tool.formatClockOffset(-90), "1m");
assert.throws(
  () => tool.parseNodeClock("02:42 22/9/2026"),
  /node clock must be HH:mm DD\/M\/YYYY UTC/
);
assert.throws(
  () => tool.parseNodeClock("25:42 22/9/2026 UTC"),
  /real UTC date and time/
);
assert.throws(
  () => tool.parseNodeClock("02:42 29/2/2025 UTC"),
  /real UTC date and time/
);

assert.strictEqual(config.startEpoch, 1790035200);
assert.strictEqual(config.endEpoch, 1790208000);
assert.strictEqual(config.endEpoch - config.startEpoch, 48 * 60 * 60);
assert.strictEqual(config.freq, 910.1);
assert.strictEqual(config.sf, 8);
assert.strictEqual(config.cr, 7);
assert.strictEqual(config.tz, "America/Los_Angeles");
assert.strictEqual(config.tx, 22);
assert.strictEqual(
  defaults.startMs,
  Date.parse("2026-09-23T17:00:00-07:00")
);
assert.strictEqual(
  defaults.endMs,
  Date.parse("2026-09-25T17:00:00-07:00")
);
assert.strictEqual(defaults.endEpoch - defaults.startEpoch, 48 * 60 * 60);
assert.strictEqual(defaults.freq, config.freq);
assert.strictEqual(defaults.bw, 500);
assert.strictEqual(defaults.sf, 8);
assert.strictEqual(defaults.cr, 7);
assert.strictEqual(defaults.tx, 22);
assert.strictEqual(defaults.tz, config.tz);
assert.strictEqual(tool.isDefaultPreset(defaults), true);
assert.strictEqual(tool.isDefaultPreset(config), true);
assert.strictEqual(tool.presetPageTitle(defaults), "Default temporary radio test · 910.1 MHz");
assert.match(tool.presetPageSummary(defaults), /910\.1 MHz, 500 kHz, SF8, CR7, 22 dBm/);
assert.strictEqual(tool.presetEyebrow(defaults), "MeshCore · default 48-hour temporary preset test");

const dstDefault = tool.configFromSearch(
  "?tz=America%2FLos_Angeles",
  "UTC",
  Date.parse("2026-03-06T08:00:00.000Z")
);
assert.strictEqual(
  tool.zonedInputValue(dstDefault.startMs, dstDefault.tz),
  "2026-03-07T17:00"
);
assert.strictEqual(
  tool.zonedInputValue(dstDefault.endMs, dstDefault.tz),
  "2026-03-09T17:00"
);
assert.strictEqual(dstDefault.endMs - dstDefault.startMs, 47 * 60 * 60 * 1000);
assert.strictEqual(tool.isDefaultPreset(dstDefault), true);

const requestedLink = tool.configFromSearch(
  "?start=2026-09-22T00:00:00.000Z&end=2026-09-24T00:00:00.000Z" +
    "&tz=America%2FLos_Angeles&freq=911.3&bw=500&sf=8&cr=7&tx=22"
);
assert.strictEqual(tool.isDefaultPreset(requestedLink), false);
assert.strictEqual(tool.presetPageTitle(requestedLink), "Temporary radio test · 911.3 MHz");
assert.match(tool.presetPageSummary(requestedLink), /911\.3 MHz, 500 kHz, SF8, CR7, 22 dBm/);
assert.doesNotMatch(tool.presetPageSummary(requestedLink), /910\.1 MHz/);
assert.strictEqual(tool.presetEyebrow(requestedLink), "MeshCore · 48-hour temporary preset test");
assert.strictEqual(
  tool.commandsFor(requestedLink, requestedLink.startMs).stockNow,
  "tempradio 911.3,500,8,7,2880"
);
assert.strictEqual(
  tool.commandsFor(requestedLink, requestedLink.startMs).primaryScheduled,
  "set tempradioat 911.3,500,8,7,+1,+2880\nget tempradioat"
);
assert.strictEqual(
  tool.commandsFor(
    requestedLink,
    requestedLink.startMs,
    0,
    tool.SCHEDULE_MODE_ABSOLUTE
  ).primaryScheduled,
  "set tempradioat 911.3,500,8,7,1790035200,1790208000\nget tempradioat"
);

const clockAdjustedLink = tool.configFromSearch(
  "?start=2026-09-22T00:00:00.000Z&end=2026-09-24T00:00:00.000Z" +
    "&tz=America%2FLos_Angeles&freq=910.3&bw=500&sf=8&cr=7&tx=22"
);
const clockOffset = tool.nodeClockOffsetSeconds(parsedNodeClock, observedAt);
const adjustedEpochs = tool.schedulerEpochs(clockAdjustedLink, clockOffset);
assert.deepStrictEqual(adjustedEpochs, {
  startEpoch: 1790046000,
  endEpoch: 1790218800,
});
const adjustedCommands = tool.commandsFor(
  clockAdjustedLink,
  clockAdjustedLink.startMs,
  clockOffset,
  tool.SCHEDULE_MODE_ABSOLUTE
);
assert.strictEqual(
  adjustedCommands.primaryScheduled,
  "set tempradioat 910.3,500,8,7,1790046000,1790218800\nget tempradioat"
);
assert.strictEqual(
  adjustedCommands.companionScheduled,
  "set radio2.cross on\n" +
    "set tempradioat2 910.3,500,8,7,rxtx,1790046000,1790218800\n" +
    "get tempradioat2"
);
assert.strictEqual(
  adjustedCommands.stockNow,
  tool.commandsFor(clockAdjustedLink, clockAdjustedLink.startMs).stockNow
);
const pageOnlyClockUrl = new URL(tool.configuredUrl(
  clockAdjustedLink,
  "https://example.test/preset-test/"
));
assert.strictEqual(pageOnlyClockUrl.searchParams.has("node_clock"), false);
assert.strictEqual(pageOnlyClockUrl.searchParams.has("node_clock_at"), false);
assert.throws(
  () => tool.schedulerEpochs(clockAdjustedLink, tool.SCHEDULER_EPOCH_MAX),
  /outside the firmware range/
);

const browserZoneFallback = tool.configFromSearch(
  "", "America/New_York", defaultNow
);
assert.strictEqual(browserZoneFallback.tz, "America/New_York");
assert.strictEqual(
  tool.configFromSearch("?tz=UTC", "America/New_York", defaultNow).tz,
  "UTC"
);
assert.doesNotThrow(() => tool.validateTimeZone(tool.browserTimeZone()));
const availableZones = tool.supportedTimeZones("America/Los_Angeles");
assert.strictEqual(availableZones[0], "UTC");
assert.ok(availableZones.includes("America/Los_Angeles"));
assert.ok(availableZones.includes("America/New_York"));
if (typeof Intl.supportedValuesOf === "function") {
  assert.strictEqual(
    availableZones.length,
    new Set(["UTC", ...Intl.supportedValuesOf("timeZone")]).size
  );
}

const before = Date.parse("2026-09-21T16:59:00-07:00");
const beforeSetup = Date.parse("2026-09-21T15:59:59-07:00");
const setupOpens = Date.parse("2026-09-21T16:00:00-07:00");
const active = Date.parse("2026-09-21T17:00:30-07:00");
const ended = Date.parse("2026-09-23T17:00:00-07:00");
assert.strictEqual(tool.phaseAt(config, before), "before");
assert.strictEqual(tool.phaseAt(config, active), "active");
assert.strictEqual(tool.phaseAt(config, ended), "ended");
assert.strictEqual(tool.EARLY_JOIN_MS, 60 * 60 * 1000);
assert.strictEqual(tool.CLOCK_RESET_COMMAND, "clkreboot");
assert.strictEqual(tool.immediateAvailable(config, beforeSetup), false);
assert.strictEqual(tool.immediateAvailable(config, setupOpens), true);
assert.strictEqual(tool.immediateAvailable(config, before), true);
assert.strictEqual(tool.immediateAvailable(config, active), true);
assert.strictEqual(tool.immediateAvailable(config, ended), false);
assert.strictEqual(tool.remainingMinutes(config, active), 2880);
assert.strictEqual(tool.remainingMinutes(config, setupOpens), 2940);

const commands = tool.commandsFor(config, active);
assert.strictEqual(commands.stockNow, "tempradio 910.1,500,8,7,2880");
assert.strictEqual(
  commands.primaryScheduled,
  "set tempradioat 910.1,500,8,7,+1,+2880\nget tempradioat"
);
assert.strictEqual(commands.primaryCancel, "get tempradioat\ndel tempradioat all");
assert.strictEqual(
  commands.companionNow,
  "set radio2.cross on\nset tempradio2 910.1,500,8,7,rxtx,2880"
);
assert.strictEqual(
  commands.companionScheduled,
  "set radio2.cross on\n" +
    "set tempradioat2 910.1,500,8,7,rxtx,+1,+2880\n" +
    "get tempradioat2"
);
assert.strictEqual(commands.stockLeaveIn30, "tempradio 910.1,500,8,7,30");
assert.strictEqual(
  commands.stockCancelDuring,
  "tempradio 910.1,500,8,7,1"
);
assert.strictEqual(
  commands.companionLeaveIn30,
  "set radio2.cross on\n" +
    "del tempradioat2 all\n" +
    "set tempradio2 910.1,500,8,7,rxtx,30"
);

const absoluteCommands = tool.commandsFor(
  config,
  active,
  0,
  tool.SCHEDULE_MODE_ABSOLUTE
);
assert.strictEqual(
  absoluteCommands.primaryScheduled,
  "set tempradioat 910.1,500,8,7,1790035200,1790208000\nget tempradioat"
);
assert.strictEqual(
  absoluteCommands.companionScheduled,
  "set radio2.cross on\n" +
    "set tempradioat2 910.1,500,8,7,rxtx,1790035200,1790208000\n" +
    "get tempradioat2"
);
assert.doesNotThrow(() => tool.commandsFor(
  config,
  active,
  tool.SCHEDULER_EPOCH_MAX,
  tool.SCHEDULE_MODE_RELATIVE
));
assert.throws(
  () => tool.commandsFor(config, active, 0, "unsupported"),
  /schedule mode must be relative or absolute/
);

assert.strictEqual(tool.scheduleAvailability(config, before).available, true);
assert.strictEqual(tool.scheduleAvailability(config, active).available, false);
assert.strictEqual(tool.primaryScheduleAvailability(config, before).available, true);
assert.strictEqual(tool.primaryScheduleAvailability(config, active).available, false);
assert.strictEqual(
  tool.scheduleAvailability(config, config.endMs - tool.SCHEDULE_HORIZON_MS - 1).available,
  false
);

assert.strictEqual(
  tool.zonedInputValue(config.startMs, "America/Los_Angeles"),
  "2026-09-21T17:00"
);
assert.strictEqual(
  tool.zonedInputValue(config.startMs, "America/New_York"),
  "2026-09-21T20:00"
);
assert.strictEqual(
  tool.localDateTimeToMs("2026-09-21T17:00", "America/Los_Angeles"),
  config.startMs
);
assert.throws(
  () => tool.localDateTimeToMs("2026-03-08T02:30", "America/Los_Angeles"),
  /does not exist/
);
assert.throws(
  () => tool.localDateTimeToMs("2026-11-01T01:30", "America/Los_Angeles"),
  /ambiguous/
);

const generated = tool.configFromGenerator({
  start: "2026-09-21T17:00",
  end: "2026-09-23T17:00",
  tz: "America/Los_Angeles",
  freq: "910.1",
  bw: "500",
  sf: "8",
  cr: "7",
  tx: "22",
});
assert.strictEqual(generated.startEpoch, config.startEpoch);
assert.strictEqual(generated.endEpoch, config.endEpoch);
assert.strictEqual(generated.tz, "America/Los_Angeles");

const estimates = tool.radioEstimates(config);
assert.ok(Math.abs(estimates.bitrateKbps - 8.9285714286) < 0.000001);
assert.ok(Math.abs(estimates.sensitivityDbm - (-121.0102999566)) < 0.000001);
assert.ok(Math.abs(estimates.linkBudgetDb - 143.0102999566) < 0.000001);

assert.throws(
  () => tool.configFromSearch("?start=bad"),
  /start must include an explicit UTC offset/
);
assert.throws(
  () => tool.configFromSearch("?start=2026-09-21T17:00:00"),
  /start must include an explicit UTC offset/
);
assert.throws(
  () => tool.configFromSearch("?bw=123"),
  /bw must be one of/
);
assert.throws(
  () => tool.configFromSearch("?start=1790208000&end=1790035200"),
  /end must be later/
);
assert.throws(
  () => tool.configFromSearch("?tz=Moon%2FTranquility"),
  /tz must be a valid IANA time zone/
);
assert.throws(
  () => tool.configFromSearch("?tx=61"),
  /tx must be between/
);
const changed = tool.configFromSearch(
  "?start=1790035200&end=1790208000&tz=America%2FLos_Angeles" +
    "&freq=915.25&bw=125&sf=10&cr=5"
);
assert.strictEqual(
  tool.commandsFor(changed, changed.startMs).stockNow,
  "tempradio 915.25,125,10,5,2880"
);
assert.strictEqual(
  tool.commandsFor(changed, changed.startMs).stockCancelDuring,
  "tempradio 915.25,125,10,5,1"
);

const shared = new URL(tool.configuredUrl(
  changed,
  "https://example.test/preset-test/?stale=yes#commands"
));
assert.strictEqual(
  shared.href,
  "https://example.test/preset-test/?start=2026-09-22T00:00:00.000Z" +
    "&end=2026-09-24T00:00:00.000Z&tz=America/Los_Angeles" +
    "&freq=915.25&bw=125&sf=10&cr=5&tx=22#commands"
);
assert.strictEqual(shared.searchParams.get("start"), "2026-09-22T00:00:00.000Z");
assert.strictEqual(shared.searchParams.get("end"), "2026-09-24T00:00:00.000Z");
assert.strictEqual(shared.searchParams.get("freq"), "915.25");
assert.strictEqual(shared.searchParams.get("tz"), "America/Los_Angeles");
assert.strictEqual(shared.searchParams.get("tx"), "22");
assert.strictEqual(shared.searchParams.has("stale"), false);
assert.strictEqual(shared.hash, "#commands");

process.stdout.write("preset test command generator checks passed\n");
