"use strict";
const assert = require("assert");
const picker = require("../docs/_javascript/firmware_picker.js");
const source = "a".repeat(40);
const family = "v2.0.0-dev-aaaaaaaa";
const suffix = "-full-usb-wifi";
const canonical = "Station_G3_ESP32_repeater" + suffix;
const legacy = "Station_G3_ESP32_r2_repeater" + suffix;
function asset(target) {
  const name = target + "-" + family + ".bin";
  return {name, size: 1000000,
    browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/test/" + name};
}
function controls(extra = {}) {
  return {platform: "ESP32_PLATFORM", radioSlotAuto: true,
    radioSlotFamily: "sx1262", radioSlotSource: source, ...extra};
}
function profile(target, metadata) {
  return {target, files: [asset(target)], controls: metadata};
}
const r2 = profile(legacy);
const shared = profile(canonical, controls());
assert.deepStrictEqual(picker.omitStationG3SlotDuplicates([r2, shared]), [shared]);
for (const wrong of [undefined, controls({radioSlotAuto: false}),
  controls({radioSlotFamily: "lr1121"}), controls({platform: "NRF52_PLATFORM"}),
  controls({radioSlotSource: "b".repeat(40)}), controls({radioSlotSource: "a".repeat(8)})]) {
  const old = profile(canonical, wrong);
  assert.deepStrictEqual(picker.omitStationG3SlotDuplicates([r2, old]), [r2, old]);
}
for (const target of ["Station_G3_ESP32_room_server"+suffix,
  "Station_G3_ESP32_repeater_observer_mqtt"+suffix,
  "Station_G3_ESP32_repeater-full-logging-ota", "Station_G2_repeater"+suffix]) {
  const other = profile(target, controls());
  assert.deepStrictEqual(picker.omitStationG3SlotDuplicates([r2, other]), [r2, other]);
}
assert.deepStrictEqual(picker.omitStationG3SlotDuplicates([r2]), [r2]);
const releases = [{name: family, tag_name: family, draft: false, prerelease: true,
  published_at: "2026-10-15T00:00:00Z",
  html_url: "https://github.com/mikecarper/MeshCore/releases/tag/"+family,
  assets: [asset(canonical), asset(legacy)]}];
const before = JSON.stringify(releases);
const oldCatalog = picker.buildCatalog(releases);
assert.strictEqual(oldCatalog.profiles.length, 2, "old releases retain both physical-slot choices");
const catalog = picker.buildCatalog(releases, {familyTag: family, profiles: {[canonical]: controls()}});
assert.deepStrictEqual(catalog.profiles.map(p => p.target), [canonical]);
assert.strictEqual(catalog.rows.length, 2, "historical file stays in exact raw-asset search");
assert.strictEqual(JSON.stringify(releases), before, "no old release or asset mutation");
const wrongFamily = picker.buildCatalog(releases, {familyTag: "other", profiles: {[canonical]: controls()}});
assert.strictEqual(wrongFamily.profiles.length, 2, "other-release metadata cannot collapse slots");
const url = "https://mikecarper.github.io/MeshCore/firmware_picker/?hardwareFamily=" +
  "Station_G3_ESP32&hardware=Station_G3_ESP32_r2&role=repeater";
const oldSelection = picker.selectionFromUrl(url, oldCatalog.profiles);
assert.strictEqual(oldSelection.filters.hardware, "Station_G3_ESP32_r2");
const selection = picker.selectionFromUrl(url, catalog.profiles);
assert.strictEqual(selection.filters.hardware, "Station_G3_ESP32");
assert.strictEqual(selection.filters.role, "repeater");
assert.deepStrictEqual(selection.unavailable, []);
const roomTarget = "Station_G3_ESP32_r2_room_server"+suffix;
const mixed = picker.buildCatalog([{...releases[0], assets: [
  ...releases[0].assets, asset(roomTarget),
]}], {familyTag: family, profiles: {[canonical]: controls()}});
assert.deepStrictEqual(mixed.profiles.map(p => p.target), [canonical, roomTarget].sort());
const mixedSelection = picker.selectionFromUrl(url, mixed.profiles);
assert.strictEqual(mixedSelection.filters.hardware, "Station_G3_ESP32");
assert.strictEqual(mixedSelection.filters.role, "repeater");
assert.deepStrictEqual(mixedSelection.unavailable, []);
const oldRoom = picker.selectionFromUrl(url.replace("role=repeater", "role=room"), mixed.profiles);
assert.strictEqual(oldRoom.filters.hardware, "Station_G3_ESP32_r2");
assert.strictEqual(oldRoom.filters.role, "room");
const autoLabel = "Station G3 ESP32 (automatic slot detection)";
assert.strictEqual(picker.hardwareChoiceLabel("hardwareFamily", "Station_G3_ESP32", catalog.profiles), autoLabel);
assert.strictEqual(picker.hardwareChoiceLabel("hardware", "Station_G3_ESP32", catalog.profiles,
  "Station_G3_ESP32"), autoLabel);
assert.strictEqual(picker.hardwareChoiceLabel("hardware", "Station_G3_ESP32", catalog.profiles), autoLabel);
assert(!picker.hardwareChoiceLabel("hardwareFamily", "Station_G3_ESP32", oldCatalog.profiles).includes("automatic"));
assert(!picker.hardwareChoiceLabel("hardwareFamily", "Station_G3_ESP32", mixed.profiles).includes("automatic"),
  "a partially old family must not claim all its images are automatic");
assert.strictEqual(picker.hardwareChoiceLabel("hardwareFamily", "Station_G3_ESP32",
  mixed.profiles.filter(p => p.role === "repeater")), autoLabel);
assert(picker.boardNotes(catalog.profiles[0]).some(note => note.includes("Automatically selects physical radio slot")));
assert(!oldCatalog.profiles.flatMap(picker.boardNotes).some(note => note.includes("Automatically selects physical radio slot")));
console.log("Station G3 shared-slot picker qualification, old catalogs and scoped bookmarks passed");
