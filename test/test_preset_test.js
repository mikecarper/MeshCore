"use strict";

const assert = require("assert");
const tool = require("../docs/_javascript/preset_test.js");

const query =
  "?start=2026-09-21T17:00:00-07:00" +
  "&end=2026-09-23T17:00:00-07:00" +
  "&freq=910.1&bw=500&sf=8&cr=7";
const config = tool.configFromSearch(query);
const defaults = tool.configFromSearch("");

assert.strictEqual(config.startEpoch, 1790035200);
assert.strictEqual(config.endEpoch, 1790208000);
assert.strictEqual(config.endEpoch - config.startEpoch, 48 * 60 * 60);
assert.strictEqual(config.freq, 910.1);
assert.strictEqual(config.sf, 8);
assert.strictEqual(config.cr, 7);
assert.strictEqual(defaults.startEpoch, config.startEpoch);
assert.strictEqual(defaults.endEpoch, config.endEpoch);
assert.strictEqual(defaults.freq, config.freq);

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

assert.strictEqual(tool.scheduleAvailability(config, before).available, true);
assert.strictEqual(tool.scheduleAvailability(config, active).available, false);
assert.strictEqual(
  tool.scheduleAvailability(config, config.endMs - tool.SCHEDULE_HORIZON_MS - 1).available,
  false
);

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

const changed = tool.configFromSearch(
  "?start=1790035200&end=1790208000&freq=915.25&bw=125&sf=10&cr=5"
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
assert.strictEqual(shared.searchParams.has("stale"), false);
assert.strictEqual(shared.hash, "#commands");

process.stdout.write("preset test command generator checks passed\n");
