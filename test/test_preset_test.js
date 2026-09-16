"use strict";

const assert = require("assert");
const tool = require("../docs/_javascript/preset_test.js");

const query =
  "?start=2026-09-21T17:00:00-07:00" +
  "&end=2026-09-23T17:00:00-07:00" +
  "&tz=America%2FLos_Angeles" +
  "&freq=910.1&bw=500&sf=8&cr=7";
const config = tool.configFromSearch(query);
const defaults = tool.configFromSearch("", "America/Los_Angeles");

assert.strictEqual(config.startEpoch, 1790035200);
assert.strictEqual(config.endEpoch, 1790208000);
assert.strictEqual(config.endEpoch - config.startEpoch, 48 * 60 * 60);
assert.strictEqual(config.freq, 910.1);
assert.strictEqual(config.sf, 8);
assert.strictEqual(config.cr, 7);
assert.strictEqual(config.tz, "America/Los_Angeles");
assert.strictEqual(config.tx, 22);
assert.strictEqual(config.normalFreq, 910.525);
assert.strictEqual(config.normalBw, 62.5);
assert.strictEqual(config.normalSf, 7);
assert.strictEqual(config.normalCr, 5);
assert.strictEqual(defaults.startEpoch, config.startEpoch);
assert.strictEqual(defaults.endEpoch, config.endEpoch);
assert.strictEqual(defaults.freq, config.freq);
assert.strictEqual(defaults.tz, config.tz);

const browserZoneFallback = tool.configFromSearch("", "America/New_York");
assert.strictEqual(browserZoneFallback.tz, "America/New_York");
assert.strictEqual(
  tool.configFromSearch("?tz=UTC", "America/New_York").tz,
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
  commands.stockScheduled,
  "set tempradioat 910.1,500,8,7,1790035200,1790208000\nget tempradioat"
);
assert.strictEqual(
  commands.companionNow,
  "set radio2.cross on\nset tempradio2 910.1,500,8,7,rxtx,2880"
);
assert.strictEqual(
  commands.companionScheduled,
  "set radio2.cross on\n" +
    "set tempradioat2 910.1,500,8,7,rxtx,1790035200,1790208000\n" +
    "get tempradioat2"
);
assert.strictEqual(commands.stockLeaveIn30, "tempradio 910.1,500,8,7,30");
assert.strictEqual(
  commands.stockCancelDuring,
  "tempradio 910.525,62.5,7,5,1"
);
assert.strictEqual(
  commands.companionLeaveIn30,
  "set radio2.cross on\n" +
    "del tempradioat2 all\n" +
    "set tempradio2 910.1,500,8,7,rxtx,30"
);

assert.strictEqual(tool.scheduleAvailability(config, before).available, true);
assert.strictEqual(tool.scheduleAvailability(config, active).available, false);
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
  normalfreq: "910.525",
  normalbw: "62.5",
  normalsf: "7",
  normalcr: "5",
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
assert.throws(
  () => tool.configFromSearch("?normalbw=100"),
  /normalbw must be one of/
);

const alternateReturn = tool.configFromSearch(
  "?normalfreq=915.5&normalbw=125&normalsf=9&normalcr=6"
);
assert.strictEqual(
  tool.commandsFor(alternateReturn, alternateReturn.startMs).stockCancelDuring,
  "tempradio 915.5,125,9,6,1"
);

const changed = tool.configFromSearch(
  "?start=1790035200&end=1790208000&tz=America%2FLos_Angeles" +
    "&freq=915.25&bw=125&sf=10&cr=5"
);
assert.strictEqual(
  tool.commandsFor(changed, changed.startMs).stockNow,
  "tempradio 915.25,125,10,5,2880"
);

const shared = new URL(tool.configuredUrl(
  changed,
  "https://example.test/preset-test/?stale=yes#commands"
));
assert.strictEqual(shared.searchParams.get("start"), "2026-09-22T00:00:00.000Z");
assert.strictEqual(shared.searchParams.get("end"), "2026-09-24T00:00:00.000Z");
assert.strictEqual(shared.searchParams.get("freq"), "915.25");
assert.strictEqual(shared.searchParams.get("tz"), "America/Los_Angeles");
assert.strictEqual(shared.searchParams.get("tx"), "22");
assert.strictEqual(shared.searchParams.get("normalfreq"), "910.525");
assert.strictEqual(shared.searchParams.get("normalbw"), "62.5");
assert.strictEqual(shared.searchParams.get("normalsf"), "7");
assert.strictEqual(shared.searchParams.get("normalcr"), "5");
assert.strictEqual(shared.searchParams.has("stale"), false);
assert.strictEqual(shared.hash, "#commands");

process.stdout.write("preset test command generator checks passed\n");
