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
    asset("Station_G2_logging_repeater-" + family + "-merged.bin"),
    asset("Station_G2_logging_repeater-" + family + ".bin"),
    asset("Station_G3_ESP32_repeater-" + family + "-merged.bin"),
    asset("Station_G3_ESP32_repeater-" + family + ".bin"),
    asset("Station_G3_ESP32_logging_repeater-" + family + "-merged.bin"),
    asset("Station_G3_ESP32_logging_repeater-" + family + ".bin"),
    asset("Heltec_t096_companion_radio_ble_ps_femon-" + family + ".uf2"),
    asset("Heltec_t096_companion_radio_ble_ps_femon-" + family + ".zip"),
    asset("RAK_4631_companion_radio_full-" + family + ".uf2"),
    asset("RAK_4631_companion_radio_full-" + family + ".zip"),
    asset("RAK_4631_companion_radio_usb-logging-" + family + ".uf2"),
    asset("RAK_4631_companion_radio_usb-logging-" + family + ".zip"),
    asset(
      "heltec_v4_2_v4_3_companion_radio_full_femon-" + family +
        "-merged.bin"
    ),
    asset(
      "heltec_v4_2_v4_3_companion_radio_full_femon-" + family + ".bin"
    ),
    asset("heltec_v4_companion_radio_usb-" + family + "-merged.bin"),
    asset("heltec_v4_companion_radio_usb-" + family + ".bin"),
    asset("heltec_v4_companion_radio_ble-" + family + "-merged.bin"),
    asset("heltec_v4_companion_radio_ble-" + family + ".bin"),
    asset(
      "heltec_v4_companion_radio_wifi_femon-" + family + "-merged.bin"
    ),
    asset("heltec_v4_companion_radio_wifi_femon-" + family + ".bin"),
  ]),
  release("repeater-room-" + family, "2026-08-23T12:00:05Z", [
    asset("Station_G2_repeater-" + family + "-deadbee-merged.bin"),
    asset("Station_G2_repeater-" + family + "-deadbee.bin"),
    asset("Station_G2_repeater-" + family + "-merged.bin"),
    asset("Station_G2_repeater-" + family + ".bin"),
    asset("solarxiao_33S_repeater-ota-" + family + ".uf2"),
    asset("solarxiao_33S_repeater-ota-" + family + ".zip"),
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
    asset(
      "solarxiao_33S_repeater_lora_ota_no_external_sensors-ota-" +
        family + ".uf2"
    ),
    asset(
      "solarxiao_33S_repeater_lora_ota_no_external_sensors-ota-" +
        family + ".zip"
    ),
  ]),
  release("full-profiles-" + family, "2026-08-23T12:00:01Z", [
    asset(
      "Generic_ESPNOW_repeatr-full-logging-ota-" + family + "-merged.bin"
    ),
    asset("Generic_ESPNOW_repeatr-full-logging-ota-" + family + ".bin"),
    asset(
      "Station_G2_repeater_observer_mqtt-full-usb-wifi-ota-" +
        family + "-merged.bin"
    ),
    asset(
      "Station_G2_repeater_observer_mqtt-full-usb-wifi-ota-" + family + ".bin"
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
assert.strictEqual(catalog.profiles.length, 12);
assert.strictEqual(catalog.rows.length, 41);

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
assert.strictEqual(companionFull.logging, "usb-runtime");
assert.deepStrictEqual(companionFull.loggingModes, ["none", "usb"]);
assert.strictEqual(companionFull.dedicatedUsbLogging, true);
assert.strictEqual(companionFull.ota, "lora-source");
assert.strictEqual(companionFull.feature, "full");
assert.strictEqual(companionFull.variant, "default");

const rakFull = profile("RAK_4631_companion_radio_full");
assert.strictEqual(rakFull.logging, "usb-runtime");
assert.deepStrictEqual(rakFull.loggingModes, ["none", "usb"]);
assert.strictEqual(rakFull.dedicatedUsbLogging, true);
assert(!catalog.profiles.some(function (item) {
  return item.target === "RAK_4631_companion_radio_usb-logging";
}));
assert(catalog.rows.some(function (item) {
  return item.target === "RAK_4631_companion_radio_usb-logging";
}));

const v4Full = profile("heltec_v4_2_v4_3_companion_radio_full_femon");
assert.strictEqual(v4Full.logging, "usb-runtime");
assert.deepStrictEqual(v4Full.loggingModes, ["none", "usb"]);
assert.strictEqual(v4Full.dedicatedUsbLogging, true);
["usb", "ble", "wifi"].forEach(function (mode) {
  assert(!catalog.profiles.some(function (item) {
    return item.hardware === "heltec_v4" && item.role === "companion" &&
      item.mode === mode;
  }));
});

["RAK_3112", "heltec_rc32", "heltec_rc32_without_display"].forEach(
  function (hardware) {
    const candidates = ["full", "usb", "ble", "wifi"].map(function (mode) {
      return Object.assign(
        picker.parseTargetProfile(
          hardware + "_companion_radio_" + mode
        ),
        { installKinds: ["bin"] }
      );
    });
    const classified = picker.applyFullCompanionCapabilities(candidates);
    assert.strictEqual(classified[0].logging, "usb-runtime");
    assert.deepStrictEqual(classified[0].loggingModes, ["none", "usb"]);
    assert.strictEqual(classified[0].dedicatedUsbLogging, undefined);
    assert.deepStrictEqual(
      picker.omitTransportsReplacedByFull(classified).map(function (item) {
        return item.target;
      }),
      [hardware + "_companion_radio_full"]
    );
  }
);

const companionBle = picker.parseTargetProfile(
  "Heltec_t096_companion_radio_ble_ps_femon"
);
assert.strictEqual(companionBle.hardware, "Heltec_t096");
assert.strictEqual(companionBle.mode, "ble");
assert.strictEqual(companionBle.variant, "default");
assert(!catalog.profiles.some(function (item) {
  return item.target === "Heltec_t096_companion_radio_ble_ps_femon";
}));
assert(catalog.rows.some(function (item) {
  return item.target === "Heltec_t096_companion_radio_ble_ps_femon";
}));

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
assert.strictEqual(heltecV4Full.variant, "default");
assert.strictEqual(
  picker.canonicalHardware("heltec_v4_2_v4_3"),
  "heltec_v4"
);
assert.strictEqual(
  picker.canonicalHardware("heltec_v4_3_tft"),
  "heltec_v4_3_tft"
);
assert.strictEqual(
  picker.canonicalHardware("heltec_v4_3_expansionkit_tft"),
  "heltec_v4_3_expansionkit_tft"
);

const heltecV4FemOff = picker.parseTargetProfile(
  "heltec_v4_3_companion_radio_ble_femoff"
);
assert.strictEqual(heltecV4FemOff.hardware, "heltec_v4_3");
assert.strictEqual(heltecV4FemOff.variant, "default");

const heltecV4TftFemOff = picker.parseTargetProfile(
  "heltec_v4_3_tft_companion_radio_wifi_femoff"
);
assert.strictEqual(heltecV4TftFemOff.hardware, "heltec_v4_3_tft");
assert.strictEqual(heltecV4TftFemOff.variant, "default");

const g2RxBoosted = picker.parseTargetProfile(
  "Station_G2_logging_repeater"
);
assert.strictEqual(g2RxBoosted.sourceHardware, "Station_G2_logging");
assert.strictEqual(g2RxBoosted.hardware, "Station_G2");
assert.strictEqual(g2RxBoosted.variant, "rx-boosted");
assert.strictEqual(
  picker.humanizeVariant(g2RxBoosted.variant),
  "RX Boosted"
);
assert(!catalog.profiles.some(function (item) {
  return item.target === "Station_G2_logging_repeater";
}));
assert(catalog.rows.some(function (item) {
  return item.target === "Station_G2_logging_repeater";
}));

const g3Standard = profile("Station_G3_ESP32_repeater");
assert.strictEqual(g3Standard.hardware, "Station_G3_ESP32");
assert(!catalog.profiles.some(function (item) {
  return item.target === "Station_G3_ESP32_logging_repeater";
}));
assert(catalog.rows.some(function (item) {
  return item.target === "Station_G3_ESP32_logging_repeater";
}));

assert.deepStrictEqual(
  picker.omitNrf52TransportsReplacedByFull([
    {
      target: "RAK_4631_companion_radio_full",
      hardware: "RAK_4631",
      variant: "default",
      role: "companion",
      mode: "full",
      logging: "none",
      installKinds: ["zip", "uf2"],
    },
    {
      target: "RAK_4631_companion_radio_usb",
      hardware: "RAK_4631",
      variant: "default",
      role: "companion",
      mode: "usb",
      logging: "none",
      installKinds: ["zip", "uf2"],
    },
    {
      target: "RAK_4631_companion_radio_ble",
      hardware: "RAK_4631",
      variant: "default",
      role: "companion",
      mode: "ble",
      logging: "none",
      installKinds: ["zip", "uf2"],
    },
    {
      target: "RAK_4631_companion_radio_usb-logging",
      hardware: "RAK_4631",
      variant: "default",
      role: "companion",
      mode: "usb",
      logging: "usb",
      installKinds: ["zip", "uf2"],
    },
  ]).map(function (item) { return item.target; }),
  ["RAK_4631_companion_radio_full"]
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

const fullLogging = profile("Generic_ESPNOW_repeatr-full-logging");
assert.strictEqual(fullLogging.logging, "usb-runtime");
assert.deepStrictEqual(fullLogging.loggingModes, ["none", "usb"]);
assert.strictEqual(fullLogging.ota, "lora-receiver");
assert.strictEqual(fullLogging.feature, "full");
assert.strictEqual(fullLogging.variant, "default");

const mqtt = profile("Station_G2_repeater_observer_mqtt-full-usb-wifi");
assert.strictEqual(mqtt.logging, "runtime");
assert.deepStrictEqual(mqtt.loggingModes, ["none", "usb", "wifi", "both"]);
assert.strictEqual(mqtt.mode, "mqtt");
assert.strictEqual(mqtt.ota, "lora-receiver");
assert.strictEqual(mqtt.variant, "default");

const lora = profile(
  "Station_G2_repeater_lora_ota_no_external_sensors"
);
assert.strictEqual(lora.ota, "lora-receiver");
assert.strictEqual(lora.variant, "no-external-sensors");
assert.strictEqual(
  picker.OTA_LABELS["lora-receiver"],
  "LoRa OTA enabled / receiver"
);
assert(!Object.prototype.hasOwnProperty.call(
  picker.OTA_LABELS,
  "ota-enabled"
));
assert(!picker.uniqueValues(catalog.profiles, "ota").includes("ota-enabled"));

const solarXiao = profile("solarxiao_33S_repeater");
assert.strictEqual(solarXiao.ota, "lora-receiver");
assert.strictEqual(solarXiao.variant, "default");
assert(!catalog.profiles.some(function (item) {
  return item.target ===
    "solarxiao_33S_repeater_lora_ota_no_external_sensors";
}));
assert(catalog.rows.some(function (item) {
  return item.target ===
    "solarxiao_33S_repeater_lora_ota_no_external_sensors";
}));
assert.deepStrictEqual(
  picker.facetValues(
    catalog.profiles,
    {
      hardware: "solarxiao_33S",
      role: "repeater",
      logging: "none",
    },
    "ota"
  ),
  ["lora-receiver"]
);

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
assert.strictEqual(
  matches[0].target,
  "Station_G2_repeater_observer_mqtt-full-usb-wifi"
);

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
  picker.facetValues(catalog.profiles, { logging: "both" }, "hardware"),
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
  ["Generic_ESPNOW", "Station_G2"]
);

assert.deepStrictEqual(
  picker.uniqueValues(catalog.profiles, "hardware").sort(),
  [
    "Generic_ESPNOW",
    "ProMicro",
    "RAK_4631",
    "Station_G2",
    "Station_G3_ESP32",
    "heltec_v4",
    "solarxiao_33S",
    "wio-e5",
  ].sort()
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
