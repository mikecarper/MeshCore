"use strict";

const assert = require("assert");
const picker = require("../docs/_javascript/firmware_picker.js");

const releases = [
  {
    name: "Stable FULL profiles",
    tag_name: "full-profiles-v1",
    html_url: "https://github.com/mikecarper/MeshCore/releases/tag/full-profiles-v1",
    published_at: "2026-08-20T12:00:00Z",
    prerelease: false,
    draft: false,
    assets: [
      {
        name: "Station_G2_repeater-full-logging-ota-v1-deadbee-merged.bin",
        browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/full-profiles-v1/Station_G2_repeater-full-logging-ota-v1-deadbee-merged.bin",
        size: 2000000,
      },
      {
        name: "Station_G2_repeater-full-logging-ota-v1-merged.bin",
        browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/full-profiles-v1/Station_G2_repeater-full-logging-ota-v1-merged.bin",
        size: 2000000,
      },
      {
        name: "Station_G2_repeater-full-logging-ota-v1.bin",
        browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/full-profiles-v1/Station_G2_repeater-full-logging-ota-v1.bin",
        size: 1500000,
      },
      {
        name: "Station_G2_companion_radio_full-logging-v1-merged.bin",
        browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/full-profiles-v1/Station_G2_companion_radio_full-logging-v1-merged.bin",
        size: 1800000,
      },
      {
        name: "Station_G2_companion_radio_full-v1-merged.bin",
        browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/full-profiles-v1/Station_G2_companion_radio_full-v1-merged.bin",
        size: 1700000,
      },
    ],
  },
  {
    name: "Development FULL profiles",
    tag_name: "full-profiles-v2-dev",
    html_url: "https://github.com/mikecarper/MeshCore/releases/tag/full-profiles-v2-dev",
    published_at: "2026-08-21T12:00:00Z",
    prerelease: true,
    draft: false,
    assets: [
      {
        name: "Station_G2_repeater-full-logging-ota-v2-merged.bin",
        browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/full-profiles-v2-dev/Station_G2_repeater-full-logging-ota-v2-merged.bin",
        size: 2100000,
      },
    ],
  },
];

const stable = picker.flattenReleases(releases, "stable");
const development = picker.flattenReleases(releases, "development");

assert.strictEqual(stable.length, 5);
assert.strictEqual(development.length, 6);
assert.strictEqual(picker.wantedSuffix("first"), "-merged.bin");
assert.strictEqual(picker.wantedSuffix("wifi"), ".bin");
assert.strictEqual(
  picker.selectAsset(stable, "usb-logging", "first").name,
  "Station_G2_repeater-full-logging-ota-v1-merged.bin"
);
assert.strictEqual(
  picker.selectAsset(stable, "usb-logging", "wifi").name,
  "Station_G2_repeater-full-logging-ota-v1.bin"
);
assert.strictEqual(
  picker.selectAsset(development, "usb-logging", "first").name,
  "Station_G2_repeater-full-logging-ota-v2-merged.bin"
);
assert.strictEqual(picker.selectAsset(stable, "field", "first"), null);
assert.strictEqual(
  picker.selectAsset(stable, "companion-full", "first").name,
  "Station_G2_companion_radio_full-v1-merged.bin"
);
assert.strictEqual(picker.formatBytes(2097152), "2.00 MiB");

console.log("firmware picker tests passed");
