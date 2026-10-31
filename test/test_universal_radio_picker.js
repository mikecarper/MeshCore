"use strict";
const assert = require("assert");
const picker = require("../docs/_javascript/firmware_picker.js");
const source = "a".repeat(40), tag = "v1.18.0.2-dev-aaaaaaaa";
const families = [
  ["Tbeam", ["SX1262", "SX1276"], ["repeater-full-usb-wifi", "room_server-full-usb-wifi", "kiss_modem", "companion_radio_full"]],
  ["LilyGo_T3S3", ["sx1262", "sx1276"], ["repeater-full-usb-wifi", "room_server-full-usb-wifi", "kiss_modem", "companion_radio_full"]],
  ["Generic_E22", ["sx1262", "sx1268"], ["repeater-full-usb-wifi"]],
  ["Meshadventurer", ["sx1262", "sx1268"], ["repeater-full-usb-wifi", "room_server-full-usb-wifi", "kiss_modem", "companion_radio_full"]],
  ["Tenstar_C3", ["sx1262", "sx1268"], ["repeater-full-usb-wifi", "kiss_modem"]],
  ["muzi_base", ["uno", "duo"], ["repeater", "room_server", "companion_radio_full"]],
];
function catalog(targets, canonical, changed = {}) {
  const assets = targets.map(target => {
    const name = target + "-" + tag + (target.startsWith("muzi_base_") ? ".uf2" : ".bin");
    return {name, size: 500000, browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/" + tag + "/" + name};
  });
  const releases = [{tag_name: tag, name: tag, draft: false, published_at: "2026-10-31T00:00:00Z", assets}];
  const before = JSON.stringify(releases);
  const muzi = canonical.startsWith("muzi_base_");
  const controls = {platform: muzi ? "NRF52_PLATFORM" : "ESP32_PLATFORM", autoLoRaRadio: true,
    autoLoRaRadioSource: source, ...(muzi ? {display: true, displayAuto: true,
      displayController: "sh1107", displaySource: source} : {}), ...changed};
  const result = picker.buildCatalog(releases, {familyTag: tag, source, profiles: {[canonical]: controls}});
  assert.strictEqual(JSON.stringify(releases), before, "never rewrite filenames or OTA identities");
  return result;
}
let checked = 0;
for (const [board, chips, roles] of families) for (const role of roles) {
  const canonical = board + "_" + role;
  const old = chips.map(chip => board + "_" + chip + "_" + role);
  const result = catalog([...old, canonical], canonical);
  assert.deepStrictEqual(result.profiles.map(profile => profile.target), [canonical]);
  assert.strictEqual(result.rows.length, 3, "exact historical downloads remain available");
  assert(picker.boardNotes(result.profiles[0]).some(note => note.includes("detects the fitted radio")));
  for (const legacy of old) {
    const identity = picker.parseTargetProfile(legacy);
    const restored = picker.selectionFromUrl("https://example.org/?hardware=" + identity.hardware + "&role=" + identity.role, result.profiles);
    assert.strictEqual(restored.filters.hardware, board);
    assert.deepStrictEqual(restored.unavailable, []);
  }
  for (const changed of [{autoLoRaRadio: false}, {autoLoRaRadio: 1}, {autoLoRaRadioSource: "b".repeat(40)},
                         {platform: board === "muzi_base" ? "ESP32_PLATFORM" : "NRF52_PLATFORM"},
                         {autoLoRaRadioSource: "aaaaaaaa"}]) {
    const unqualified = catalog([...old, canonical], canonical, changed);
    assert.strictEqual(unqualified.profiles.length, 3, "unqualified image cannot hide existing variants");
  }
  assert.strictEqual(catalog(old, canonical).profiles.length, 2, "missing successor cannot remove either chip");
  ++checked;
}
for (const [legacy, other] of [
  ["Tbeam_SX1262_repeater-full-usb-wifi", "LilyGo_T3S3_repeater-full-usb-wifi"],
  ["Generic_E22_sx1268_repeater-full-usb-wifi", "Tenstar_C3_repeater-full-usb-wifi"],
  ["Meshadventurer_sx1262_repeater-full-usb-wifi", "Meshadventurer_room_server-full-usb-wifi"],
  ["Tenstar_C3_sx1268_repeater", "Tenstar_C3_repeater-full-usb-wifi"],
]) assert.strictEqual(catalog([legacy, other], other).profiles.length, 2, "different board, role or policy stays separate");
const muziFull = "muzi_base_companion_radio_full";
for (const chip of ["uno", "duo"]) {
  const legacy = "muzi_base_" + chip + "_superIO_companion_radio_full";
  const qualified = catalog([legacy, muziFull], muziFull);
  assert.deepStrictEqual(qualified.profiles.map(profile => profile.target), [muziFull]);
  for (const changed of [{displayAuto: false}, {display: false}, {displaySource: "b".repeat(40)},
                         {displayController: "ssd1681"}, {autoLoRaRadio: false}]) {
    assert.strictEqual(catalog([legacy, muziFull], muziFull, changed).profiles.length, 2,
      "superIO needs both the real radio and accessory detection proofs");
  }
}
for (const legacy of ["muzi_base_uno_repeater_lora_ota", "muzi_base_duo_room_server_lora_ota"]) {
  assert.strictEqual(catalog([legacy, "muzi_base_repeater"], "muzi_base_repeater").profiles.length, 2,
    "shared non-OTA firmware must never replace a historical OTA receiver");
}
console.log("Automatic radio selection: " + checked + " board/role combinations preserve exact historical downloads and reject unqualified replacements");
