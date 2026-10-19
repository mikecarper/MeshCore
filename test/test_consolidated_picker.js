"use strict";
const assert = require("assert");
const p = require("../docs/_javascript/firmware_picker.js");
const source = "a".repeat(40), family = "v1.18.0.1-dev-aaaaaaaa";
const targets = [
  "PicoW_companion_radio", "PicoW_companion_radio_usb", "PicoW_companion_radio_ble", "PicoW_companion_radio_wifi",
  "heltec_v4_repeater-full-usb-wifi", "heltec_v4_expansionkit_repeater-full-usb-wifi",
  "heltec_v4_r8_tft_room_server-full-usb-wifi", "heltec_v4_r8_tft_portrait_room_server_observer_mqtt-full-usb-wifi",
  "heltec_rc32_companion_radio_full", "heltec_rc32_without_display_companion_radio_full",
];
function asset(target) {
  const name = target + "-" + family + (target.startsWith("PicoW") ? ".uf2" : ".bin");
  return {name, size: 500000, browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/" + family + "/" + name};
}
function release(names) {
  return {tag_name: family, name: family, draft: false, published_at: "2026-10-20T00:00:00Z", assets: names.map(asset),
    html_url: "https://github.com/mikecarper/MeshCore/releases/tag/" + family};
}
const controls = Object.fromEntries(targets.map(t => [t, {platform: t.startsWith("PicoW") ? "RP2040_PLATFORM" : "ESP32_PLATFORM"}]));
const metadata = {familyTag: family, source, profiles: controls};
const oldCatalog = p.buildCatalog([release(targets)], metadata);
assert.strictEqual(oldCatalog.profiles.length, targets.length, "unqualified historical choices remain exact");
controls.PicoW_companion_radio = {platform: "RP2040_PLATFORM", combinedCompanion: true, combinedCompanionSource: source};
Object.assign(controls[targets[4]], {oledExpansionCompatible: true, oledExpansionCompatibleSource: source});
Object.assign(controls[targets[6]], {display: true, displayRotation: true, displayRotationSource: source});
Object.assign(controls[targets[8]], {display: true, displayAuto: true, displayController: "nv3001b", displaySource: source});
const releases = [release(targets)], snapshot = JSON.stringify(releases);
const catalog = p.buildCatalog(releases, metadata);
assert.deepStrictEqual(catalog.profiles.map(x => x.target).sort(), [targets[0], targets[4], targets[6], targets[8]].sort());
assert.strictEqual(catalog.rows.length, targets.length);
assert.strictEqual(JSON.stringify(releases), snapshot, "no release filename, OTA identity or download is rewritten");
const pico = catalog.profiles.find(x => x.target === targets[0]);
assert.strictEqual(pico.mode, "combined");
assert.strictEqual(pico.ota, "none", "combined transports do not claim Full mOTA support");
assert(["usb", "ble", "wifi"].every(mode => p.profileFieldValues(pico, "mode").includes(mode)));
assert(p.boardNotes(pico).some(note => note.includes("USB, Bluetooth LE and WiFi")));
for (const [oldHardware, newHardware] of [["heltec_v4_expansionkit", "heltec_v4"], ["heltec_v4_r8_tft_portrait", "heltec_v4_r8_tft"], ["heltec_rc32_without_display", "heltec_rc32"]]) {
  const role = oldHardware.includes("portrait") ? "room" : oldHardware.includes("rc32") ? "companion" : "repeater";
  const restored = p.selectionFromUrl("https://example.org/?hardware=" + oldHardware + "&role=" + role, catalog.profiles);
  assert.strictEqual(restored.filters.hardware, newHardware);
  assert.deepStrictEqual(restored.unavailable, []);
}
const stale = JSON.parse(JSON.stringify(metadata));
for (const entry of Object.values(stale.profiles)) for (const key of Object.keys(entry)) if (key.endsWith("Source")) entry[key] = "b".repeat(40);
assert.strictEqual(p.buildCatalog(releases, stale).profiles.length, targets.length, "stale proof never folds old hardware");

const pairNames = ["Test_repeater_lora_ota_no_external_sensors-full", "Test_repeater_lora_ota_no_external_sensors-reduced"];
const sensorMetadata = {familyTag: family, source, profiles: Object.fromEntries(pairNames.map((target, i) => [target, {
  platform: "NRF52_PLATFORM", sensorProfile: i ? "reduced" : "full", sensorProfileSource: source,
  otaRole: "lora-receiver", updateMethods: ["lora"],
}]))};
const pairs = p.buildCatalog([release(pairNames)], sensorMetadata).profiles;
assert.strictEqual(pairs.length, 2, "both qualified packages remain in the catalog");
assert.deepStrictEqual(p.recommendedProfiles(pairs, {}).map(x => x.sensorProfile), ["full"]);
assert.strictEqual(p.recommendedProfiles(pairs, {}, true).length, 2, "Advanced exposes Reduced");
const full = pairs.find(x => x.sensorProfile === "full"), reduced = pairs.find(x => x.sensorProfile === "reduced");
assert.strictEqual(p.recommendedProfiles([reduced], {}).length, 1, "unmatched Reduced compatibility route stays available");
const filters = {hardware: reduced.hardware, role: reduced.role, feature: reduced.feature, variant: reduced.variant, ota: reduced.ota};
const url = p.shortSelectionUrl("https://example.org/firmware_picker/", filters, true);
const restored = p.selectionFromUrl(url, pairs);
assert.deepStrictEqual(restored.unavailable, []);
assert(p.recommendedProfiles(pairs, restored.filters).includes(reduced), "explicit Reduced links never select Full");
assert.strictEqual(p.recommendedProfiles([full, {...reduced, controls: {...reduced.controls, sensorProfileSource: "b".repeat(40)}}], {}).length, 2, "unmatched source policies are retained");
console.log("Qualified hardware/transport consolidation, historical files and Full-default/Advanced-Reduced compatibility passed");

for (const [legacy, canonical, controller] of [
  ["muzi_base_uno_superIO_companion_radio_ble", "muzi_base_uno_companion_radio_full", "sh1107"],
  ["muzi_base_duo_superIO_companion_radio_ble", "muzi_base_duo_companion_radio_full", "sh1107"],
  ["LilyGo_T-Echo-Lite_non_shell_companion_radio_usb", "LilyGo_T-Echo-Lite_companion_radio_full", "ssd1681"]
]) {
  const proof = {platform: "NRF52_PLATFORM", display: true, displayAuto: true, displayController: controller, displaySource: source};
  const metadata = {familyTag: family, source, profiles: {[canonical]: proof}};
  const catalog = p.buildCatalog([release([legacy, canonical])], metadata);
  assert.deepStrictEqual(catalog.profiles.map(x => x.target), [canonical]);
  assert.strictEqual(catalog.rows.length, 2);
  assert(p.optionalAutoDisplayProfile(catalog.profiles[0]));
  const oldHardware = p.parseTargetProfile(legacy).hardware;
  const restored = p.selectionFromUrl("https://example.org/?hardware=" + oldHardware + "&role=companion", catalog.profiles);
  assert.strictEqual(restored.filters.hardware, p.parseTargetProfile(canonical).hardware);
  assert.deepStrictEqual(restored.unavailable, []);
  const unqualified = {familyTag: family, source, profiles: {[canonical]: {...proof, displaySource: "b".repeat(40)}}};
  assert.strictEqual(p.buildCatalog([release([legacy, canonical])], unqualified).profiles.length, 2);
  const other = canonical.includes("uno") ? canonical.replace("uno", "duo") : canonical.includes("duo") ? canonical.replace("duo", "uno") : canonical.replace("Lite", "Card");
  assert.strictEqual(p.buildCatalog([release([legacy, other])], {familyTag: family, source, profiles: {[other]: proof}}).profiles.length, 2, "physical radio/panel families cannot substitute");
}
console.log("Muzi per-radio accessories and T-Echo Lite shell/core folding require exact new display proof");

{
  const canonical = "heltec_v4_tft_companion_radio_full_femon";
  const legacy = "heltec_v4_expansionkit_tft_companion_radio_full_femon";
  const metadata = {familyTag: family, source, profiles: {[canonical]: {
    platform: "ESP32_PLATFORM", display: true, tftExpansionCompatible: true, tftExpansionCompatibleSource: source,
  }}};
  const catalog = p.buildCatalog([release([legacy, canonical])], metadata);
  assert.deepStrictEqual(catalog.profiles.map(x => x.target), [canonical]);
  assert.strictEqual(catalog.rows.length, 2);
  assert(p.boardNotes(catalog.profiles[0]).some(note => note.includes("TFT expansion kit")));
  const restored = p.selectionFromUrl("https://example.org/?hardware=heltec_v4_expansionkit_tft&role=companion", catalog.profiles);
  assert.strictEqual(restored.filters.hardware, "heltec_v4_tft");
  assert.deepStrictEqual(restored.unavailable, []);
  delete metadata.profiles[canonical].tftExpansionCompatible;
  assert.strictEqual(p.buildCatalog([release([legacy, canonical])], metadata).profiles.length, 2);
}
console.log("V4 TFT base/kit consolidation requires its own exact qualified Full image");
