"use strict";
const assert = require("assert");
const picker = require("../docs/_javascript/firmware_picker.js");
const source = "a".repeat(40);
const family = "v1.18.0.1.preview1-aaaaaaaa";
const tag = "heltec-t114-v1.18.0.1.preview1";
const mainFamily = "v1.18.0.1-dev-bbbbbbbb";
const roles = ["repeater", "room_server", "sensor", "companion_radio_full"];
const canonical = role => "Heltec_t114_" + role;
const legacy = role => "Heltec_t114_without_display_" + role;
function asset(target, version = family, releaseTag = tag) {
  const name = target + "-" + version + ".uf2";
  return {name, size: 500000,
    browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/" + releaseTag + "/" + name};
}
function release(releaseTag, assets, date) {
  return {name: releaseTag, tag_name: releaseTag, draft: false, prerelease: true,
    published_at: date, html_url: "https://github.com/mikecarper/MeshCore/releases/tag/" + releaseTag, assets};
}
function controls(extra = {}) {
  return {platform: "NRF52_PLATFORM", display: true, displayAuto: true,
    displayController: "st7789", displaySource: source, ...extra};
}
const oldTargets = roles.flatMap(role => [canonical(role), legacy(role)]);
const main = release(mainFamily, oldTargets.concat("Other_repeater").map(target =>
  asset(target, mainFamily, mainFamily)), "2026-10-18T00:00:00Z");
const ordinary = picker.buildCatalog([main]);
assert.strictEqual(ordinary.profiles.length, oldTargets.length + 1);
assert(!ordinary.profiles.some(picker.t114AutoDisplayProfile));
assert(!ordinary.profiles.flatMap(picker.boardNotes).some(note => note.includes("detects")));
const singleton = picker.buildCatalog([{...main, assets: [asset(legacy("room_server"), mainFamily, mainFamily)]}]);
assert.strictEqual(singleton.profiles[0].hardware, "Heltec_t114_without_display", "an unmatched historical fallback stays accurate");

const previewTargets = roles.map(canonical).concat(canonical("kiss_modem"));
const preview = release(tag, previewTargets.concat("Other_repeater").map(target => asset(target)), "2026-10-19T00:00:00Z");
const supplemental = {releaseTag: tag, familyTag: family, source, profiles: Object.fromEntries(
  previewTargets.map(target => [target, target.endsWith("kiss_modem") ?
    {platform: "NRF52_PLATFORM", display: false} : controls()]))};
const metadata = {familyTag: mainFamily, profiles: {}, supplementalReleases: [supplemental]};
const releases = [main, preview];
const before = JSON.stringify(releases);
const catalog = picker.buildCatalog(releases, metadata);
assert.strictEqual(catalog.releaseSet.familyTag, mainFamily, "the latest main family is unchanged");
assert.deepStrictEqual(catalog.profiles.map(p => p.target).sort(), previewTargets.concat("Other_repeater").sort());
assert.strictEqual(catalog.profiles.find(p => p.target === "Other_repeater").files[0].releaseTag, mainFamily);
assert(catalog.rows.some(row => row.target === legacy("repeater")), "legacy exact downloads remain available");
assert(!catalog.rows.some(row => row.target === "Other_repeater" && row.releaseTag === tag), "a board preview cannot import another board's assets");
assert.strictEqual(JSON.stringify(releases), before, "published releases/assets are immutable");
for (const role of roles) {
  const profile = catalog.profiles.find(p => p.target === canonical(role));
  assert(picker.t114AutoDisplayProfile(profile));
  assert(profile.files.every(file => file.releaseTag === tag && file.name.includes(family)));
  assert(picker.boardNotes(profile).some(note => note.includes("without a screen")));
  assert(picker.boardNotes(profile).some(note => note.includes("local USB") && note.includes("LoRa OTA")));
}
assert(!picker.t114AutoDisplayProfile(catalog.profiles.find(p => p.role === "kiss")));
const repeaters = catalog.profiles.filter(p => p.hardware === "Heltec_t114" && p.role === "repeater");
assert.strictEqual(picker.hardwareChoiceLabel("hardware", "Heltec_t114", repeaters), "Heltec T114 (automatic screen detection)");
assert(!picker.hardwareChoiceLabel("hardware", "Heltec_t114", ordinary.profiles).includes("automatic"));

const standalone = picker.buildCatalog([preview], supplemental);
assert.strictEqual(standalone.releaseSet.familyTag, family);
assert.strictEqual(standalone.profiles.length, previewTargets.length);
const newerSource = "c".repeat(40);
const newerFamily = "v1.19.0-dev-cccccccc";
const newer = release(newerFamily, previewTargets.map(target => asset(target, newerFamily, newerFamily)), "2026-11-01T00:00:00Z");
const newerMetadata = {...metadata, source: newerSource, familyTag: newerFamily, profiles: Object.fromEntries(
  previewTargets.map(target => [target, target.endsWith("kiss_modem") ?
    {platform: "NRF52_PLATFORM", display: false} : controls({displaySource: newerSource})]))};
const superseding = picker.buildCatalog([main, preview, newer], newerMetadata);
assert.strictEqual(superseding.profiles.length, previewTargets.length);
assert(superseding.profiles.every(profile => profile.files.every(file => file.releaseTag === newerFamily)),
  "a later qualified main release must replace the earlier T114 preview, including KISS");
for (const changes of [undefined, {...metadata, supplementalReleases: []},
  {...metadata, supplementalReleases: [{...supplemental, source: "b".repeat(40)}]},
  {...metadata, supplementalReleases: [{...supplemental, familyTag: "v9.9.9-preview1-aaaaaaaa"}]},
  {...metadata, supplementalReleases: [{...supplemental, releaseTag: "ikoka-v1.18.0.1.preview1"}]}]) {
  const fallback = picker.buildCatalog(releases, changes);
  assert.deepStrictEqual(fallback.profiles.map(p => p.target), ordinary.profiles.map(p => p.target), "wrong or absent supplemental proof must retain the main release");
}
for (const wrong of [{displayAuto: false}, {displayController: "ssd1306"}, {display: false},
  {platform: "ESP32_PLATFORM"}, {displaySource: "b".repeat(40)}, {displaySource: "a".repeat(8)}]) {
  const changed = {...supplemental, profiles: {...supplemental.profiles, [canonical("room_server")]: controls(wrong)}};
  const partial = picker.buildCatalog(releases, {...metadata, supplementalReleases: [changed]});
  assert.strictEqual(partial.profiles.filter(p => p.role === "room").length, 2, "a rejected new room cannot hide either historical choice");
  assert(partial.profiles.some(p => p.target === legacy("room_server")));
  assert.strictEqual(partial.profiles.filter(p => p.role === "repeater" && p.hardware === "Heltec_t114").length, 1);
}
const bookmark = "https://mikecarper.github.io/MeshCore/firmware_picker/?hardwareFamily=Heltec_t114&hardware=Heltec_t114_without_display&role=repeater";
assert.strictEqual(picker.selectionFromUrl(bookmark, ordinary.profiles).filters.hardware, "Heltec_t114_without_display");
const selected = picker.selectionFromUrl(bookmark, catalog.profiles);
assert.strictEqual(selected.filters.hardware, "Heltec_t114");
assert.strictEqual(selected.filters.role, "repeater");
assert.deepStrictEqual(selected.unavailable, []);
const wrongRoom = {...supplemental, profiles: {...supplemental.profiles, [canonical("room_server")]: controls({displayAuto: false})}};
const mixed = picker.buildCatalog(releases, {...metadata, supplementalReleases: [wrongRoom]});
assert.strictEqual(picker.selectionFromUrl(bookmark.replace("role=repeater", "role=room"), mixed.profiles).filters.hardware,
  "Heltec_t114_without_display", "bookmark normalization is scoped to an available successor for that role");
for (const policy of ["full", "reduced"]) {
  const target = canonical("repeater") + "-" + policy;
  const oldTarget = legacy("repeater") + "-" + policy;
  const profiles = [{target: oldTarget}, {target, controls: controls(), files: [asset(target)]}];
  assert.deepStrictEqual(picker.omitT114DisplayDuplicates(profiles), [profiles[1]]);
  assert.deepStrictEqual(picker.omitT114DisplayDuplicates([profiles[0]]), [profiles[0]]);
  assert.strictEqual(picker.omitT114DisplayDuplicates([{...profiles[0], target: oldTarget + "-ota"}, profiles[1]]).length, 1,
    "a migrated role must not retain an old headless recommendation, even when its old OTA policy is unavailable");
}
for (const role of ["repeater", "room_server"]) for (const policy of ["full", "reduced"]) {
  const suffix = "_lora_ota_no_external_sensors-" + policy;
  const old = {target: legacy(role) + suffix};
  const current = {target: canonical(role) + suffix, controls: controls(), files: [asset(canonical(role) + suffix)]};
  assert.deepStrictEqual(picker.omitT114DisplayDuplicates([old, current]), [current]);
  const otherPolicy = {...current, target: canonical(role) + suffix.replace(policy, policy === "full" ? "reduced" : "full")};
  assert.strictEqual(picker.omitT114DisplayDuplicates([old, otherPolicy]).length, 1);
}
const bridge = {target: legacy("repeater_bridge_rs232")};
const merged = {target: canonical("repeater"), controls: controls({rs232: true, rs232Merged: true, rs232Source: source}), files: [asset(canonical("repeater"))]};
assert.deepStrictEqual(picker.omitT114DisplayDuplicates([bridge, merged]), [merged]);
assert.strictEqual(picker.omitT114DisplayDuplicates([bridge, {...merged, controls: controls({rs232: true})}]).length, 1);
const historicalTargets = ["repeater", "room_server"].flatMap(role =>
  ["full", "reduced"].flatMap(policy => [canonical(role), legacy(role)].map(target =>
    target + "_lora_ota_no_external_sensors-" + policy)));
const historicalMain = {...main, assets: main.assets.concat(historicalTargets.map(target => asset(target, mainFamily, mainFamily)))};
const historicalMetadata = {...metadata, profiles: Object.fromEntries(historicalTargets.map(target => [target,
  {platform: "NRF52_PLATFORM", display: !target.includes("without_display"), otaRole: "lora-receiver"}]))};
const migrated = picker.buildCatalog([historicalMain, preview], historicalMetadata);
assert(!migrated.profiles.some(profile => historicalTargets.includes(profile.target)),
  "a migrated role must recommend no old canonical screen-assumed or headless policy");
assert(historicalTargets.every(target => migrated.rows.some(file => file.target === target)),
  "all exact old full/reduced OTA files remain raw compatibility downloads");
const otaBookmark = bookmark + "&ota=lora-receiver";
const stale = picker.selectionFromUrl(otaBookmark, migrated.profiles);
assert.strictEqual(stale.filters.hardware, "Heltec_t114");
assert.strictEqual(stale.filters.role, "repeater");
assert(stale.unavailable.some(value => value.includes("ota")), "an unavailable old OTA policy must be reported rather than silently remapped");
const newLeanTarget = canonical("repeater_lora_ota_no_external_sensors") + "-reduced";
const bothPreview = {...preview, assets: preview.assets.concat(asset(newLeanTarget))};
const bothMetadata = {...metadata, supplementalReleases: [{...supplemental,
  profiles: {...supplemental.profiles, [newLeanTarget]: controls({otaRole: "lora-receiver"})}}]};
const both = picker.buildCatalog([historicalMain, bothPreview], bothMetadata);
assert.deepStrictEqual(both.profiles.filter(profile => profile.hardware === "Heltec_t114" && profile.role === "repeater")
  .map(profile => profile.target).sort(), [canonical("repeater"), newLeanTarget].sort(),
  "real standard and lean-OTA automatic images in the new role family can coexist");
console.log("T114 canonical display detection, scoped preview proof, legacy fallback and bookmark tests passed");
