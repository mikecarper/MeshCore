"use strict";
const assert = require("assert");
const picker = require("../docs/_javascript/firmware_picker.js");
const source = "a".repeat(40), tag = "v1.18.0.2-dev-aaaaaaaa";
const cases = [
  ["heltec_v4_r8_tft_kiss_modem", "heltec_v4_r8_kiss_modem", "ESP32_PLATFORM", "headlessR8Kiss"],
  ["WioTrackerL1Eink_kiss_modem", "WioTrackerL1_kiss_modem", "NRF52_PLATFORM", "headlessWioL1Kiss"],
  ["WioTrackerL1Eink_companion_radio_ble", "WioTrackerL1_companion_radio_full", "NRF52_PLATFORM", "unifiedWioL1Display"],
];

function catalog(legacy, target, platform, key, changes = {}, names = [legacy, target]) {
  const profiles = {[target]: {platform, [key]: true, [key + "Source"]: source,
    ...(key === "unifiedWioL1Display" ? {display: true, displayAuto: true,
      displayController: "sh1106_ssd1680", displaySource: source} : {}), ...changes}};
  const release = {tag_name: tag, name: tag, draft: false, published_at: "2026-10-31T00:00:00Z",
    html_url: "https://github.com/mikecarper/MeshCore/releases/tag/" + tag,
    assets: names.map(name => {
      const file = name + "-" + tag + (platform === "NRF52_PLATFORM" ? ".uf2" : ".bin");
      return {name: file, size: 500000,
        browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/" + tag + "/" + file};
    })};
  const before = JSON.stringify(release);
  const result = picker.buildCatalog([release], {familyTag: tag, source, profiles});
  assert.strictEqual(JSON.stringify(release), before, "historical filenames and downloads stay exact");
  return result;
}

for (const args of cases) {
  const [legacy, target, platform, key] = args;
  const result = catalog(...args);
  assert.deepStrictEqual(result.profiles.map(row => row.target), [target]);
  assert.strictEqual(result.rows.length, 2, "the exact historical download remains available");
  const old = picker.parseTargetProfile(legacy), next = picker.parseTargetProfile(target);
  const selected = picker.selectionFromUrl("https://example.org/?hardware=" + old.hardware + "&role=" + old.role,
    result.profiles);
  assert.strictEqual(selected.filters.hardware, next.hardware);
  assert.deepStrictEqual(selected.unavailable, []);
  for (const fault of [{[key]: false}, {[key]: 1}, {[key + "Source"]: "b".repeat(40)},
    {[key + "Source"]: "aaaaaaaa"}, {platform: platform === "ESP32_PLATFORM" ? "NRF52_PLATFORM" : "ESP32_PLATFORM"}]) {
    assert.strictEqual(catalog(...args, fault).profiles.length, 2, "unqualified replacements keep old choices");
  }
  assert.strictEqual(catalog(...args, {}, [legacy]).profiles.length, 1, "missing successor keeps legacy");
  if (key === "unifiedWioL1Display") {
    for (const fault of [{display: false}, {displayAuto: false}, {displaySource: "b".repeat(40)},
      {displayController: "ssd1681"}]) {
      assert.strictEqual(catalog(...args, fault).profiles.length, 2, "Wio needs its exact optional display proof");
    }
    assert(picker.boardNotes(result.profiles[0]).some(note => note.includes("1W")));
  } else {
    assert(picker.boardNotes(result.profiles[0]).some(note => note.includes("display")));
  }
}
const wio = cases[2];
for (const other of ["WioTrackerL1_1W_companion_radio_ble", "WioTrackerL1Eink_repeater",
  "WioTrackerL1Eink_companion_radio_ble_lora_ota"]) {
  assert.strictEqual(catalog(other, wio[1], wio[2], wio[3]).profiles.length, 2,
    other + ": different physical hardware, roles and deployed OTA identities stay separate");
}
console.log("Combined Wio L1 / headless KISS choices require exact release proof and preserve historical downloads");
