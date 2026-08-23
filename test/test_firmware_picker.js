"use strict";

const assert = require("assert");
const picker = require("../docs/_javascript/firmware_picker.js");

const family = "v2.0.0-dev-abcd1234";

function asset(name, size) {
  return {
    name: name,
    browser_download_url:
      "https://github.com/mikecarper/MeshCore/releases/download/test/" + name,
    size: size || 1000000,
  };
}

function release(tag, publishedAt, assets, extra) {
  return Object.assign({
    name: tag,
    tag_name: tag,
    html_url: "https://github.com/mikecarper/MeshCore/releases/tag/" + tag,
    published_at: publishedAt,
    prerelease: true,
    draft: false,
    assets: assets,
  }, extra || {});
}

const releases = [
  release(family, "2026-08-23T12:00:06Z", [
    asset("Station_G2_companion_radio_full-" + family + "-merged.bin"),
    asset("Station_G2_companion_radio_full-" + family + ".bin"),
    asset("Heltec_t096_companion_radio_ble_ps_femon-" + family + ".uf2"),
    asset("Heltec_t096_companion_radio_ble_ps_femon-" + family + ".zip"),
  ]),
  release("repeater-room-" + family, "2026-08-23T12:00:05Z", [
    asset("Station_G2_repeater-" + family + "-deadbee-merged.bin"),
    asset("Station_G2_repeater-" + family + "-deadbee.bin"),
    asset("Station_G2_repeater-" + family + "-merged.bin"),
    asset("Station_G2_repeater-" + family + ".bin"),
    asset("wio-e5-repeater_bridge_rs232-" + family + ".hex"),
  ]),
  release("utility-" + family, "2026-08-23T12:00:04Z", [
    asset("RAK_4631_sensor-" + family + ".uf2"),
    asset("RAK_4631_sensor-" + family + ".zip"),
  ]),
  release("logging-" + family, "2026-08-23T12:00:03Z", [
    asset("ProMicro_terminal_chat-logging-" + family + ".uf2"),
    asset("ProMicro_terminal_chat-logging-" + family + ".zip"),
  ]),
  release("lora-ota-" + family, "2026-08-23T12:00:02Z", [
    asset(
      "Station_G2_repeater_lora_ota_no_external_sensors-ota-" +
        family + "-merged.bin"
    ),
    asset(
      "Station_G2_repeater_lora_ota_no_external_sensors-ota-" +
        family + ".bin"
    ),
  ]),
  release("full-profiles-" + family, "2026-08-23T12:00:01Z", [
    asset(
      "Station_G2_repeater-full-logging-ota-" + family + "-merged.bin"
    ),
    asset("Station_G2_repeater-full-logging-ota-" + family + ".bin"),
    asset(
      "Station_G2_repeater_observer_mqtt-full-ota-" +
        family + "-merged.bin"
    ),
    asset(
      "Station_G2_repeater_observer_mqtt-full-ota-" + family + ".bin"
    ),
  ]),
  release(
    "nrf52-mota-v1.0.0-to-" + family,
    "2026-08-23T12:00:07Z",
    [asset("Unrelated_repeater-" + family + ".uf2")]
  ),
  release("v1.9.9", "2026-08-20T12:00:00Z", [], {
    prerelease: false,
  }),
];

const releaseSet = picker.selectReleaseSet(releases);
assert.strictEqual(releaseSet.familyTag, family);
assert.strictEqual(releaseSet.releases.length, 6);
assert(!releaseSet.releases.some(function (item) {
  return item.tag_name.startsWith("nrf52-mota-");
}));

const catalog = picker.buildCatalog(releases);
assert.strictEqual(catalog.releaseSet.familyTag, family);
assert.strictEqual(catalog.profiles.length, 9);
assert.strictEqual(catalog.rows.length, 19);

function profile(target) {
  const found = catalog.profiles.find(function (item) {
    return item.target === target;
  });
  assert(found, "missing profile " + target);
  return found;
}

const companionFull = profile("Station_G2_companion_radio_full");
assert.strictEqual(companionFull.hardware, "Station_G2");
assert.strictEqual(companionFull.role, "companion");
assert.strictEqual(companionFull.mode, "full");
assert.strictEqual(companionFull.logging, "none");
assert.strictEqual(companionFull.ota, "lora-source");
assert.strictEqual(companionFull.feature, "full");
assert.strictEqual(companionFull.variant, "default");

const companionBle = profile("Heltec_t096_companion_radio_ble_ps_femon");
assert.strictEqual(companionBle.hardware, "Heltec_t096");
assert.strictEqual(companionBle.mode, "ble");
assert.strictEqual(companionBle.variant, "ps-femon");
assert.strictEqual(
  picker.humanizeVariant(companionBle.variant),
  "Power save FEM on"
);

const fullWifiLogging = picker.parseTargetProfile(
  "Heltec_v2_companion_radio_wifi-full-logging"
);
assert.strictEqual(fullWifiLogging.mode, "wifi");
assert.strictEqual(fullWifiLogging.logging, "usb");
assert.strictEqual(fullWifiLogging.feature, "full");
assert.strictEqual(fullWifiLogging.variant, "default");

const fullUsbLogging = picker.parseTargetProfile(
  "Meshadventurer_sx1262_companion_radio_usb-full-logging"
);
assert.strictEqual(fullUsbLogging.mode, "usb");
assert.strictEqual(fullUsbLogging.variant, "default");

const heltecV4Full = picker.parseTargetProfile(
  "heltec_v4_2_v4_3_companion_radio_full_femon"
);
assert.strictEqual(heltecV4Full.target,
  "heltec_v4_2_v4_3_companion_radio_full_femon");
assert.strictEqual(heltecV4Full.hardware, "heltec_v4");
assert.strictEqual(heltecV4Full.mode, "full");
assert.strictEqual(heltecV4Full.variant, "femon");
assert.strictEqual(
  picker.canonicalHardware("heltec_v4_2_v4_3"),
  "heltec_v4"
);

const standardRepeater = profile("Station_G2_repeater");
assert.strictEqual(standardRepeater.hardwareFamily, "Station_G2");
assert.strictEqual(standardRepeater.role, "repeater");
assert.strictEqual(standardRepeater.logging, "none");
assert.strictEqual(standardRepeater.ota, "none");
assert.strictEqual(standardRepeater.mode, "standard");
assert.strictEqual(standardRepeater.feature, "standard");
assert.strictEqual(
  picker.canonicalAsset(standardRepeater.files, "merged-bin").name,
  "Station_G2_repeater-" + family + "-merged.bin"
);

const fullLogging = profile("Station_G2_repeater-full-logging");
assert.strictEqual(fullLogging.logging, "usb");
assert.strictEqual(fullLogging.ota, "ota-enabled");
assert.strictEqual(fullLogging.feature, "full");
assert.strictEqual(fullLogging.variant, "default");

const mqtt = profile("Station_G2_repeater_observer_mqtt-full");
assert.strictEqual(mqtt.logging, "wifi");
assert.strictEqual(mqtt.mode, "mqtt");
assert.strictEqual(mqtt.ota, "ota-enabled");

const lora = profile(
  "Station_G2_repeater_lora_ota_no_external_sensors"
);
assert.strictEqual(lora.ota, "lora-receiver");
assert.strictEqual(lora.variant, "no-external-sensors");

const wio = profile("wio-e5-repeater_bridge_rs232");
assert.strictEqual(wio.hardware, "wio-e5");
assert.strictEqual(wio.role, "repeater");
assert.strictEqual(wio.mode, "rs232");

const matches = catalog.profiles.filter(function (item) {
  return picker.profileMatches(item, {
    hardware: "Station_G2",
    role: "repeater",
    logging: "usb",
  });
});
assert.strictEqual(matches.length, 1);
assert.strictEqual(matches[0].target, "Station_G2_repeater-full-logging");

assert.deepStrictEqual(
  picker.FACET_FIELDS,
  [
    "hardwareFamily",
    "hardware",
    "role",
    "logging",
    "ota",
    "mode",
    "feature",
    "variant",
    "install",
  ]
);
const hardwareNames = [
  "heltec_v4",
  "heltec_v4_3",
  "heltec_v4_3_tft",
  "heltec_v4_r8",
  "wio-e5",
  "wio-e5-mini",
];
assert.strictEqual(
  picker.hardwareFamilyFor("heltec_v4_3_tft", hardwareNames),
  "heltec_v4"
);
assert.strictEqual(
  picker.hardwareFamilyFor("wio-e5-mini", hardwareNames),
  "wio-e5"
);
assert.strictEqual(
  picker.humanizeHardwareVariant("heltec_v4", "heltec_v4"),
  "Base / standard (V4.2 / V4.3 auto-detect)"
);
assert.strictEqual(
  picker.humanizeHardwareVariant("heltec_v4_3_tft", "heltec_v4"),
  "V4.3 + TFT"
);
assert.strictEqual(
  picker.humanizeHardwareVariant("Station_G2_logging", "Station_G2"),
  "RX-boosted radio profile"
);
const groupedHardwareProfiles = [
  {
    hardwareFamily: "heltec_v4",
    hardware: "heltec_v4",
    installKinds: ["bin"],
  },
  {
    hardwareFamily: "heltec_v4",
    hardware: "heltec_v4_3_tft",
    installKinds: ["bin"],
  },
  {
    hardwareFamily: "wio-e5",
    hardware: "wio-e5",
    installKinds: ["hex"],
  },
];
assert.deepStrictEqual(
  picker.facetValues(
    groupedHardwareProfiles,
    { hardwareFamily: "heltec_v4", hardware: "heltec_v4_3_tft" },
    "hardwareFamily",
    ["hardware"]
  ).sort(),
  ["heltec_v4", "wio-e5"]
);
assert.deepStrictEqual(
  picker.facetValues(
    groupedHardwareProfiles,
    { hardwareFamily: "heltec_v4" },
    "hardware"
  ).sort(),
  ["heltec_v4", "heltec_v4_3_tft"]
);
assert.strictEqual(
  picker.INSTALL_LABELS["merged-bin"],
  "Erase & fresh install (merged .bin)"
);
assert.strictEqual(
  picker.INSTALL_LABELS.bin,
  "Update existing install (.bin)"
);
assert(picker.profileMatchesFacets(wio, { install: "hex" }));
assert(!picker.profileMatchesFacets(wio, { install: "merged-bin" }));
assert.deepStrictEqual(
  picker.facetValues(catalog.profiles, { install: "hex" }, "hardware"),
  ["wio-e5"]
);
assert.deepStrictEqual(
  picker.facetValues(catalog.profiles, { hardware: "wio-e5" }, "install"),
  ["hex"]
);
assert.deepStrictEqual(
  picker.facetValues(catalog.profiles, { logging: "wifi" }, "hardware"),
  ["Station_G2"]
);
assert.deepStrictEqual(
  picker.facetValues(
    catalog.profiles,
    {
      install: "merged-bin",
      feature: "full",
      logging: "usb",
      role: "repeater",
    },
    "hardware"
  ),
  ["Station_G2"]
);

assert.deepStrictEqual(
  picker.uniqueValues(catalog.profiles, "hardware").sort(),
  ["Heltec_t096", "ProMicro", "RAK_4631", "Station_G2", "wio-e5"].sort()
);
assert.strictEqual(picker.humanizeHardware("RAK_4631"), "RAK 4631");
assert.strictEqual(picker.formatBytes(2097152), "2.00 MiB");
assert.strictEqual(
  picker.parseFirmwareAsset(
    { name: "wrong-version.bin", url: "", size: 1 },
    family
  ),
  null
);

console.log("generalized firmware picker tests passed");
