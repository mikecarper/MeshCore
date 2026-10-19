"use strict";
const assert = require("assert");
const picker = require("../docs/_javascript/firmware_picker.js");
const source = "a".repeat(40);
for (const [target, controller] of [
  ["heltec_rc32_companion_radio_full", "nv3001b"],
  ["Heltec_Wireless_Tracker_repeater", "st7735"],
  ["heltec_tracker_v1_1_repeater_observer_mqtt", "st7735"],
  ["heltec_v4_r8_tft_portrait_repeater_observer_mqtt", "st7789"]
]) {
  const profile = {...picker.parseTargetProfile(target), target,
    controls: {platform: "ESP32_PLATFORM", display: true, displayAuto: true,
      displayController: controller, displaySource: source},
    files: [".bin", "-merged.bin"].map(suffix => ({name: target + "-v1.18.0.1-dev-aaaaaaaa" + suffix}))};
  assert(picker.optionalAutoDisplayProfile(profile), target);
  assert(picker.boardNotes(profile).some(note => note.includes("optional screen")), target);
  assert(picker.hardwareChoiceLabel("hardware", profile.hardware, [profile]).includes("automatic screen detection"));
  for (const change of [{display: false}, {displayAuto: false}, {displayController: "unknown"},
    {displaySource: source.slice(0, 8)}, {displaySource: "b".repeat(40)}, {platform: "NRF52_PLATFORM"}]) {
    const old = {...profile, controls: {...profile.controls, ...change}};
    assert(!picker.optionalAutoDisplayProfile(old));
    assert(!picker.boardNotes(old).some(note => note.includes("optional screen")));
  }
  const mixed = {...profile, files: [...profile.files, {name: target + "-v1.18.0.1-dev-bbbbbbbb.bin"}]};
  assert(!picker.optionalAutoDisplayProfile(mixed), "every download must match the detection source");
  assert(!picker.optionalAutoDisplayProfile({...profile, controlsSource: "b".repeat(40)}),
    "the detection source must match attached metadata too");
}
for (const target of ["heltec_rc32_without_display_repeater", "heltec_tracker_v2_repeater",
  "Heltec_t096_repeater", "Heltec_T190_repeater_", "LilyGo_T-Echo_Card_repeater", "heltec_v4_tft_repeater"]) {
  assert(!picker.optionalAutoDisplayProfile({target, controls: {platform: "ESP32_PLATFORM",
    display: true, displayAuto: true, displayController: "st7789", displaySource: source},
    files: [{name: target + "-v1.18.0.1-dev-aaaaaaaa.bin"}]}));
}
console.log("Optional display picker directions require the exact supported board, controller and application source");
