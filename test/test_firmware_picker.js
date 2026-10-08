"use strict";

const assert = require("assert");
const picker = require("../docs/_javascript/firmware_picker.js");

const bootloaderManifest = require("../docs/_data/bootloader_manifest.json");
const bootloaderCatalog = picker.buildBootloaderCatalog(bootloaderManifest);
assert.strictEqual(bootloaderCatalog.profiles.length, 23);
for (const profile of bootloaderCatalog.profiles) {
  for (const hardware of profile.meshcoreHardware) {
    assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, hardware, "nrf52").id, profile.id);
    assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, hardware, "esp32"), null);
    assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, hardware, "rp2040"), null);
  }
}
assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, "Heltec_t096", "nrf52").id, "heltec_t096");
assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, "Heltec_t114", "nrf52").id, "heltec_t114");
assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, "Heltec_tower_v2_sdcard", "nrf52").storage, "sd");
assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, "Heltec_tower_v2", "nrf52"), null);
assert(picker.unavailableBootloaderForHardware(bootloaderCatalog, "Heltec_tower_v2", "nrf52").reason.includes("one-time local installation"));
assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, "RAK_3401", "nrf52").storage, "adaptive");
assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, "RAK_4631", "nrf52").id, "wiscore_rak4631_auto");
assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, "ThinkNode_M8", "nrf52"), null);
const nrf52Hardware = require("./fixtures/firmware_picker_nrf52_1_17_1_7.json").hardware;
assert.strictEqual(nrf52Hardware.length, 53);
let mappedNrf52 = 0;
for (const hardware of nrf52Hardware) {
  const download = picker.bootloaderForHardware(bootloaderCatalog, hardware, "nrf52");
  const unavailable = picker.unavailableBootloaderForHardware(bootloaderCatalog, hardware, "nrf52");
  assert.strictEqual(Number(!!download) + Number(!!unavailable), 1, hardware + " needs an explicit coverage decision");
  if (download) mappedNrf52++;
  else {
    assert(unavailable.reason.length > 20);
    assert.strictEqual(unavailable.files, undefined);
  }
  assert.strictEqual(picker.unavailableBootloaderForHardware(bootloaderCatalog, hardware, "esp32"), null);
}
assert.strictEqual(mappedNrf52, 38);
for (const hardware of nrf52Hardware.filter(h => /^(ikoka_|solarxiao_)/.test(h))) {
  assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, hardware, "nrf52").id, "xiao_nrf52840_ble");
}
for (const hardware of ["WioTrackerL1-1W", "WioTrackerL1Eink"]) {
  assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, hardware, "nrf52").id, "wio_tracker_l1");
}
assert(picker.unavailableBootloaderForHardware(bootloaderCatalog, "GAT562_Mesh_Watch13", "nrf52").reason.includes("vibration motor"));
assert(picker.unavailableBootloaderForHardware(bootloaderCatalog, "wio_wm1110", "nrf52").reason.includes("identity and installation route still need qualification"));
const nextBootloaderTag = "v0.11.0-OTAFIX2.4.12";
const nextBootloaderRelease = {
  tag_name: nextBootloaderTag, draft: false, prerelease: false,
  html_url: "https://github.com/" + bootloaderManifest.repository + "/releases/tag/" + nextBootloaderTag,
  assets: bootloaderManifest.profiles.flatMap(function (profile) {
    return Object.values(profile.files).map(function (file) {
      return { name: file.name.replace(bootloaderManifest.tag, nextBootloaderTag),
        browser_download_url: file.url.replaceAll(bootloaderManifest.tag, nextBootloaderTag),
        size: file.size, digest: "sha256:" + file.sha256 };
    });
  }),
};
const nextBootloaderCatalog = picker.buildBootloaderCatalog(bootloaderManifest, nextBootloaderRelease);
assert.strictEqual(nextBootloaderCatalog.version, "2.4.12");
assert(picker.bootloaderForHardware(nextBootloaderCatalog, "t1000e", "nrf52").files.uf2.url.includes(nextBootloaderTag));
const ambiguousBootloader = structuredClone(nextBootloaderRelease);
ambiguousBootloader.assets.push(ambiguousBootloader.assets.find(a => a.name.startsWith("update-heltec_t096_")));
assert.strictEqual(picker.bootloaderForHardware(picker.buildBootloaderCatalog(bootloaderManifest, ambiguousBootloader), "Heltec_t096", "nrf52"), null);
const badBootloader = structuredClone(nextBootloaderRelease);
badBootloader.assets.find(a => a.name.startsWith("update-heltec_t096_")).browser_download_url = "https://example.com/wrong.uf2";
assert.strictEqual(picker.bootloaderForHardware(picker.buildBootloaderCatalog(bootloaderManifest, badBootloader), "Heltec_t096", "nrf52"), null);
const ambiguousMapping = structuredClone(bootloaderManifest);
ambiguousMapping.profiles[1].meshcoreHardware.push(ambiguousMapping.profiles[0].meshcoreHardware[0]);
assert.throws(() => picker.buildBootloaderCatalog(ambiguousMapping), /Ambiguous bootloader hardware/);
const shadowedMapping = structuredClone(bootloaderManifest);
shadowedMapping.unavailableProfiles[0].meshcoreHardware.push("ikoka_stick_nrf_33dbm");
assert.throws(() => picker.buildBootloaderCatalog(shadowedMapping), /Ambiguous bootloader hardware/);
assert.throws(() => picker.buildBootloaderCatalog(bootloaderManifest, Object.assign({}, nextBootloaderRelease, {prerelease: true})), /stable OTAFIX/);
console.log("exact bootloader downloads, storage variants, chip gating, latest version and unsafe catalog rejection passed");

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

// Numbered pages are emitted when a category exceeds the asset limit.
// Include them only for the exact family and known category names.
const chunkTags = ["companion-2-", "lora-ota-2-", "full-profiles-10-"];
const chunkedReleases = releases.concat(chunkTags.map(function (prefix, index) {
  return release(prefix + family, "2026-08-23T12:00:00Z", [
    asset("Extra" + index + "_repeater-" + family + ".uf2")
  ]);
}));
for (const prefix of ["companion-1-", "lora-ota-0-", "lora-ota-02-", "unknown-2-"]) {
  chunkedReleases.push(release(prefix + family, "2026-08-23T12:00:00Z", []));
}
chunkedReleases.push(release("lora-ota-2-" + family + "-other", "2026-08-23T12:00:00Z", []));
chunkedReleases.push(release("lora-ota-3-" + family, "2026-08-23T12:00:00Z", [], { draft: true }));
const chunkedSet = picker.selectReleaseSet(chunkedReleases);
assert.strictEqual(chunkedSet.releases.length, releaseSet.releases.length + chunkTags.length);
for (const prefix of chunkTags) assert(chunkedSet.releases.some(r => r.tag_name === prefix + family));
const chunkedCatalog = picker.buildCatalog(chunkedReleases);
for (let index = 0; index < chunkTags.length; ++index) {
  assert(chunkedCatalog.profiles.some(p => p.target === "Extra" + index + "_repeater"));
}

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
assert.strictEqual(companionFull.dedicatedUsbLogging, false);
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

const rakSensor = profile("RAK_4631_sensor");
assert.strictEqual(rakSensor.logging, "usb-runtime");
assert.deepStrictEqual(rakSensor.loggingModes, ["none", "usb"]);

const standardBesideFull = picker.applyMergedStandardUsbLoggingCapabilities([
  Object.assign(picker.parseTargetProfile("Example_repeater"), {
    hardware: "Example",
    role: "repeater",
    variant: "default",
    feature: "standard",
    logging: "none",
    ota: "none",
  }),
  Object.assign(picker.parseTargetProfile("Example_repeater-full-logging-ota"), {
    hardware: "Example",
    role: "repeater",
    variant: "default",
    feature: "full",
    logging: "usb",
    ota: "lora-receiver",
  }),
]);
assert.strictEqual(standardBesideFull[0].logging, "usb-runtime");
assert.deepStrictEqual(standardBesideFull[0].loggingModes, ["none", "usb"]);

const v4Full = profile("heltec_v4_2_v4_3_companion_radio_full_femon");
assert.strictEqual(v4Full.logging, "usb-runtime");
assert.deepStrictEqual(v4Full.loggingModes, ["none", "usb"]);
assert.strictEqual(v4Full.dedicatedUsbLogging, false);
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
    assert.strictEqual(classified[0].dedicatedUsbLogging, false);
    assert.deepStrictEqual(
      picker.omitTransportsReplacedByFull(classified).map(function (item) {
        return item.target;
      }),
      [hardware + "_companion_radio_full"]
    );
  }
);

const combinedUsbBle = [
  picker.parseTargetProfile("Heltec_E290_companion_usb"),
  picker.parseTargetProfile("Heltec_E290_companion_ble"),
  picker.parseTargetProfile("Heltec_E290_companion_usb_ble"),
];
combinedUsbBle.forEach(function (item) {
  item.installKinds = ["bin"];
});
assert.strictEqual(combinedUsbBle[2].mode, "usb-ble");
assert.strictEqual(combinedUsbBle[2].variant, "default");
assert.deepStrictEqual(
  picker.omitTransportsReplacedByFull(combinedUsbBle).map(function (item) {
    return item.target;
  }),
  ["Heltec_E290_companion_usb_ble"]
);

const fullWithWiredTransports = [
  "ThinkNode_M7_companion_radio_full",
  "ThinkNode_M7_companion_radio_usb",
  "ThinkNode_M7_companion_radio_ble",
  "ThinkNode_M7_companion_radio_wifi",
  "ThinkNode_M7_companion_radio_serial",
  "ThinkNode_M7_companion_radio_ethernet",
  "ThinkNode_M7_terminal_chat",
].map(function (target) {
  return Object.assign(picker.parseTargetProfile(target), {
    installKinds: ["bin"],
  });
});
assert.deepStrictEqual(
  picker.omitTransportsReplacedByFull(fullWithWiredTransports).map(
    function (item) { return item.target; }
  ),
  ["ThinkNode_M7_companion_radio_full"]
);

const terminalWithUsb = [
  "PicoW_terminal_chat",
  "PicoW_companion_radio_usb",
].map(function (target) {
  return Object.assign(picker.parseTargetProfile(target), {
    installKinds: ["uf2"],
  });
});
assert.deepStrictEqual(
  picker.omitTransportsReplacedByFull(terminalWithUsb).map(
    function (item) { return item.target; }
  ),
  ["PicoW_companion_radio_usb"]
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

const partitionExpander = picker.parseTargetProfile(
  "Xiao_S3_WIO_partition_expander"
);
assert.strictEqual(partitionExpander.role, "partition-expander");
assert.strictEqual(partitionExpander.hardware, "Xiao_S3_WIO");
assert.strictEqual(partitionExpander.explicitOta, "lora-receiver");
assert.strictEqual(picker.ROLE_LABELS[partitionExpander.role],
  "Partition Expander");
assert(picker.installSteps(partitionExpander, "zip").some(function (step) {
  return step.includes("not an nRF52 Serial DFU package");
}));
assert(picker.installSteps(partitionExpander, "bin").some(function (step) {
  return step.includes("temporary bridge");
}));

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

const directWifiMqtt = picker.parseTargetProfile(
  "Heltec_v3_companion_radio_wifi_mqtt"
);
assert.strictEqual(directWifiMqtt.mode, "wifi");
assert.strictEqual(directWifiMqtt.logging, "wifi");
assert.deepStrictEqual(directWifiMqtt.loggingModes, ["wifi"]);
assert.strictEqual(directWifiMqtt.variant, "default");

const consolidatedV3 = [
  "Heltec_v3_companion_radio_full",
  "Heltec_v3_companion_radio_wifi_mqtt",
].map(function (target) {
  return Object.assign(picker.parseTargetProfile(target), {
    installKinds: ["bin", "merged-bin"],
  });
});
picker.applyFullCompanionCapabilities(consolidatedV3);
assert.deepStrictEqual(
  consolidatedV3[0].loggingModes,
  ["none", "usb"]
);
assert(!picker.profileMatches(consolidatedV3[0], { logging: "wifi" }));
assert.deepStrictEqual(
  picker.omitTransportsReplacedByFull(consolidatedV3).map(function (item) {
    return item.target;
  }),
  ["Heltec_v3_companion_radio_full"]
);

const consolidatedV4 = [
  "heltec_v4_2_v4_3_companion_radio_full_femon",
  "heltec_v4_companion_radio_wifi_mqtt_femon",
  "heltec_v4_3_companion_radio_wifi_mqtt_femoff",
].map(function (target) {
  return Object.assign(picker.parseTargetProfile(target), {
    installKinds: ["bin", "merged-bin"],
  });
});
picker.applyFullCompanionCapabilities(consolidatedV4);
assert.deepStrictEqual(
  picker.omitTransportsReplacedByFull(consolidatedV4).map(function (item) {
    return item.target;
  }),
  ["heltec_v4_2_v4_3_companion_radio_full_femon"]
);

const consolidatedSenseCap = [
  "SenseCapIndicator-LoRa_companion_radio_full",
  "SenseCapIndicator-LoRa_comp_radio_usb_wifi",
].map(function (target) {
  return Object.assign(picker.parseTargetProfile(target), {
    installKinds: ["bin", "merged-bin"],
  });
});
assert.strictEqual(consolidatedSenseCap[1].variant, "default");
picker.applyFullCompanionCapabilities(consolidatedSenseCap);
assert.deepStrictEqual(
  picker.omitTransportsReplacedByFull(consolidatedSenseCap).map(
    function (item) { return item.target; }
  ),
  ["SenseCapIndicator-LoRa_companion_radio_full"]
);

const nrfUf2OnlyFull = Object.assign(
  picker.parseTargetProfile("RAK_4631_companion_radio_full"),
  { installKinds: ["uf2"] }
);
picker.applyFullCompanionCapabilities([nrfUf2OnlyFull]);
assert.strictEqual(nrfUf2OnlyFull.dedicatedUsbLogging, true);
assert.strictEqual(nrfUf2OnlyFull.logging, "usb-runtime");

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
assert.strictEqual(standardRepeater.logging, "usb-runtime");
assert.deepStrictEqual(standardRepeater.loggingModes, ["none", "usb"]);
assert.strictEqual(standardRepeater.ota, "none");
assert.strictEqual(standardRepeater.mode, "standard");
assert.strictEqual(standardRepeater.feature, "standard");

const mergedLoggingProfiles = picker.applyMergedStandardUsbLoggingCapabilities([
  Object.assign(picker.parseTargetProfile("PicoW_room_server"), {
    ota: "none",
  }),
  Object.assign(picker.parseTargetProfile("PicoW_kiss_modem"), {
    ota: "none",
  }),
]);
assert.strictEqual(mergedLoggingProfiles[0].logging, "usb-runtime");
assert.deepStrictEqual(mergedLoggingProfiles[0].loggingModes, ["none", "usb"]);
assert.strictEqual(mergedLoggingProfiles[1].logging, "none");
assert.strictEqual(
  picker.canonicalAsset(standardRepeater.files, "merged-bin").name,
  "Station_G2_repeater-" + family + "-merged.bin"
);
assert.strictEqual(picker.CANDIDATE_RESULT_LIMIT, 5);
assert(picker.shouldShowCandidateResults(new Array(5).fill(standardRepeater)));
assert(!picker.shouldShowCandidateResults(new Array(6).fill(standardRepeater)));
assert.deepStrictEqual(
  picker.resolveProfileAssets([standardRepeater], "merged-bin").map(
    function (entry) {
      return [entry.profile.target, entry.asset.name, entry.installKind];
    }
  ),
  [[
    "Station_G2_repeater",
    "Station_G2_repeater-" + family + "-merged.bin",
    "merged-bin",
  ]]
);
assert.deepStrictEqual(
  picker.resolveProfileAssets([standardRepeater], "").map(function (entry) {
    return entry.installKind;
  }),
  ["merged-bin", "bin"]
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
assert.strictEqual(mqtt.mode, "standard");
assert.strictEqual(mqtt.ota, "lora-receiver");
assert.strictEqual(mqtt.variant, "default");
assert.strictEqual(
  picker.parseTargetProfile("Station_G2_repeater_observer_mqtt").mode,
  "standard"
);
assert.strictEqual(
  picker.MODE_LABELS.standard,
  "Standard / no separate bridge"
);
assert(!Object.prototype.hasOwnProperty.call(picker.MODE_LABELS, "mqtt"));
assert(!picker.uniqueValues(catalog.profiles, "mode").includes("mqtt"));
assert(picker.profileMatchesFacets(mqtt, {
  role: "repeater",
  logging: "wifi",
  ota: "lora-receiver",
  mode: "standard",
  feature: "full",
}));

// Phase one of the same-partition ESP32 migration keeps the legacy observer
// artifact downloadable for its distinct deployed mOTA identity, but offers
// the ordinary target's exact-identity Full successor in the picker. An
// unmatched observer profile remains visible.
const observerMigrationCatalog = picker.buildCatalog([
  release(family, "2026-09-18T12:00:00Z", [
    asset("Heltec_v3_repeater-full-usb-wifi-ota-" + family + ".bin"),
    asset(
      "Heltec_v3_repeater_observer_mqtt-full-usb-wifi-ota-" +
        family + ".bin"
    ),
    asset(
      "Heltec_v3_room_server_observer_mqtt-full-usb-wifi-ota-" +
        family + ".bin"
    ),
  ]),
]);
assert.strictEqual(observerMigrationCatalog.rows.length, 3);
assert.deepStrictEqual(
  observerMigrationCatalog.profiles.map(function (item) { return item.target; }),
  [
    "Heltec_v3_repeater-full-usb-wifi",
    "Heltec_v3_room_server_observer_mqtt-full-usb-wifi",
  ]
);

const fullCompanionSteps = picker.installSteps(v4Full, "merged-bin");
assert(fullCompanionSteps.some(function (step) {
  return step.includes("usb.logging");
}));
assert(!fullCompanionSteps.some(function (step) {
  return step.includes("logging.output");
}));
const observerSteps = picker.installSteps(mqtt, "merged-bin");
assert(observerSteps.some(function (step) {
  return step.includes("logging.output");
}));

const lora = profile(
  "Station_G2_repeater_lora_ota_no_external_sensors"
);
assert.strictEqual(lora.ota, "lora-receiver");
assert.strictEqual(lora.variant, "no-external-sensors");
assert.strictEqual(
  picker.OTA_LABELS["lora-receiver"],
  "Receives LoRa OTA"
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

const mergedRs232 = [
  "RAK_4631_repeater",
  "RAK_4631_repeater_bridge_rs232_serial1",
  "RAK_4631_repeater_bridge_rs232_serial2",
].map(function (target) {
  return Object.assign(picker.parseTargetProfile(target), {
    installKinds: ["uf2"],
  });
});
const mergedRs232Visible = picker.applyMergedRak4631RepeaterCapabilities(
  picker.omitTransportsReplacedByFull(mergedRs232)
);
assert.deepStrictEqual(
  mergedRs232Visible.map(function (item) {
    return item.target;
  }),
  ["RAK_4631_repeater"]
);
assert.deepStrictEqual(
  picker.profileFieldValues(mergedRs232Visible[0], "mode"),
  ["standard", "rs232"]
);
assert(picker.profileMatches(
  mergedRs232Visible[0],
  { mode: "standard" },
  ["mode"]
));
assert(picker.profileMatches(
  mergedRs232Visible[0],
  { mode: "rs232" },
  ["mode"]
));

// Exact legacy target rows must stay attached to their own compatibility
// downloads. They disappear only from recommendations when the canonical
// merged image is present; advertising RS232 on that image must not rewrite
// or coalesce the legacy files into the canonical profile.
const rakCompatibilityCatalog = picker.buildCatalog([
  release(family, "2026-08-23T13:00:02Z", []),
  release("repeater-room-" + family, "2026-08-23T13:00:01Z", [
    asset("RAK_4631_repeater-" + family + ".uf2"),
    asset(
      "RAK_4631_repeater_bridge_rs232_serial1-" + family + ".uf2"
    ),
    asset(
      "RAK_4631_repeater_bridge_rs232_serial2-" + family + ".uf2"
    ),
  ]),
  release("lora-ota-" + family, "2026-08-23T13:00:00Z", [
    asset(
      "RAK_4631_repeater_lora_ota_no_external_sensors-ota-" +
        family + ".zip"
    ),
    asset(
      "RAK_4631_repeater_bridge_rs232_serial1_" +
        "lora_ota_no_external_sensors-ota-" + family + ".zip"
    ),
    asset(
      "RAK_4631_repeater_bridge_rs232_serial2_" +
        "lora_ota_no_external_sensors-ota-" + family + ".zip"
    ),
  ]),
]);
const expectedCompatibilityTargets = [
  "RAK_4631_repeater_bridge_rs232_serial1",
  "RAK_4631_repeater_bridge_rs232_serial2",
  "RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors",
  "RAK_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors",
];
expectedCompatibilityTargets.forEach(function (target) {
  assert(rakCompatibilityCatalog.rows.some(function (row) {
    return row.target === target;
  }), "missing exact compatibility row " + target);
  assert(!rakCompatibilityCatalog.profiles.some(function (item) {
    return item.target === target;
  }), "legacy compatibility target was recommended " + target);
});
[
  "RAK_4631_repeater",
  "RAK_4631_repeater_lora_ota_no_external_sensors",
].forEach(function (target) {
  const canonical = rakCompatibilityCatalog.profiles.find(function (item) {
    return item.target === target;
  });
  assert(canonical, "missing canonical RAK4631 profile " + target);
  assert.deepStrictEqual(
    picker.profileFieldValues(canonical, "mode"),
    ["standard", "rs232"]
  );
  assert(canonical.files.every(function (file) {
    return file.target === target;
  }), "legacy compatibility file was mapped to " + target);
});

const constrainedRs232 = [
  "wio-e5_repeater",
  "wio-e5-repeater_bridge_rs232",
].map(function (target) {
  return Object.assign(picker.parseTargetProfile(target), {
    installKinds: ["hex"],
  });
});
assert.strictEqual(
  picker.omitTransportsReplacedByFull(constrainedRs232).length,
  2
);

const matches = catalog.profiles.filter(function (item) {
  return picker.profileMatches(item, {
    hardware: "Station_G2",
    role: "repeater",
    logging: "usb",
  });
});
assert.deepStrictEqual(
  matches.map(function (item) { return item.target; }).sort(),
  [
    "Station_G2_repeater",
    "Station_G2_repeater_observer_mqtt-full-usb-wifi",
  ]
);

assert.deepStrictEqual(
  picker.FACET_FIELDS,
  [
    "chipFamily",
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
  "Full install / layout migration (merged .bin)"
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
assert.strictEqual(
  picker.humanizeVariant("rak13302-w25q16-lora-ota"),
  "RAK13302 + External storage board (W25Q16) LoRa OTA"
);
const rakStorageTargets = [
  "RAK_4631_repeater_unified_lora_ota",
  "RAK_4631_repeater_lora_ota_no_external_sensors",
  "RAK_4631_repeater_rak15001_slot_c_lora_ota",
  "RAK_4631_repeater_w25q16_lora_ota",
  "RAK_3401_repeater_unified_lora_ota",
  "RAK_3401_repeater_lora_ota_no_external_sensors",
  "RAK_3401_repeater_rak13302_w25q16_lora_ota",
];
const rakStorageAssets = rakStorageTargets.map(function (target) {
  return asset(target + "-ota-" + family + ".uf2");
});
const rakUnifiedCatalog = picker.buildCatalog([
  release(family, "2026-09-25T00:00:00Z", rakStorageAssets),
]);
assert.deepStrictEqual(
  rakUnifiedCatalog.profiles.map(function (item) { return item.target; }).sort(),
  ["RAK_3401_repeater_unified_lora_ota", "RAK_4631_repeater_unified_lora_ota"].sort()
);
assert(rakUnifiedCatalog.profiles.every(function (item) {
  return item.variant === "default" && item.ota === "lora-receiver";
}));
assert.strictEqual(rakUnifiedCatalog.rows.length, rakStorageTargets.length);
const rakLegacyCatalog = picker.buildCatalog([
  release(family, "2026-09-25T00:00:00Z", rakStorageAssets.slice(1, 4).concat(rakStorageAssets.slice(5))),
]);
assert.strictEqual(rakLegacyCatalog.profiles.length, 5);
// Sensor-policy suffixes must not resurrect the storage-specific compatibility
// images. A Full successor cannot replace a missing Reduced successor.
const rakPolicyTargets = rakStorageTargets.flatMap(target =>
  ['full', 'reduced'].map(policy => target + '-' + policy));
const rakPolicyAssets = rakPolicyTargets.map(target =>
  asset(target + '-ota-' + family + '.uf2'));
const rakPolicyCatalog = picker.buildCatalog([
  release(family, '2026-10-08T00:00:00Z', rakPolicyAssets),
]);
assert.deepStrictEqual(rakPolicyCatalog.profiles.map(item => item.target).sort(),
  ['RAK_3401', 'RAK_4631'].flatMap(board =>
    ['full', 'reduced'].map(policy => board + '_repeater_unified_lora_ota-' + policy)).sort());
assert.deepStrictEqual(rakPolicyCatalog.rows.map(item => item.target).sort(), rakPolicyTargets.sort(),
  'All exact legacy OTA identities must remain in the raw catalog');
const rakMissingReducedCatalog = picker.buildCatalog([
  release(family, '2026-10-08T00:00:00Z', rakPolicyAssets.filter(file =>
    !file.name.includes('_unified_lora_ota-reduced-'))),
]);
assert.deepStrictEqual(rakMissingReducedCatalog.profiles.map(item => item.target).sort(),
  rakPolicyTargets.filter(target => target.endsWith('_unified_lora_ota-full') ||
    (target.endsWith('-reduced') && !target.includes('_unified_lora_ota-'))).sort());
assert.strictEqual(picker.formatBytes(2097152), "2.00 MiB");
assert.strictEqual(
  picker.parseFirmwareAsset(
    { name: "wrong-version.bin", url: "", size: 1 },
    family
  ),
  null
);

console.log("generalized firmware picker tests passed");

// Shared features must produce the same commands for every role.
// Keep the old release's workarounds and replacement-image checks independent
// of whichever release the live site currently serves.
const controls = require('./fixtures/firmware_picker_1_17_1_5_controls.json');
const liveFamily = controls.familyTag;
const controlledReleases = [release(liveFamily, '2026-09-01T00:00:00Z', [
  asset('heltec_v4_2_v4_3_companion_radio_full_femon-' + liveFamily + '.bin'),
  asset('RAK_4631_companion_radio_full-' + liveFamily + '.uf2'),
  asset('Heltec_v3_companion_radio_full-' + liveFamily + '.bin'),
  asset('SenseCapIndicator-LoRa_companion_radio_full-' + liveFamily + '.bin'),
]), release('full-profiles-' + liveFamily, '2026-09-01T00:00:00Z', [
  asset('heltec_v4_repeater_observer_mqtt-full-usb-wifi-ota-' + liveFamily + '.bin'),
]), release('repeater-room-' + liveFamily, '2026-09-01T00:00:00Z', [
  asset('RAK_4631_repeater-' + liveFamily + '.uf2'),
  asset('Station_G3_ESP32_repeater-' + liveFamily + '.bin'),
])];
const controlled = picker.buildCatalog(controlledReleases, controls);
const findControlled = name => controlled.profiles.find(p => p.target === name);
const commands = sections => sections.flatMap(s => s.actions.flatMap(a => a.commands || []));
const observer = findControlled('heltec_v4_repeater_observer_mqtt-full-usb-wifi');
for (const mode of ['none', 'usb', 'wifi', 'both']) {
  const directions = picker.runtimeDirections(observer, {logging: mode});
  assert.deepStrictEqual(directions[0].actions[0].commands,
    (mode === 'usb' || mode === 'both' ? ['set powersaving off'] : []).concat(
      ['set logging.output ' + (mode === 'none' ? 'off' : mode), 'get logging.output']));
}
const mqttCompanion = findControlled('heltec_v4_2_v4_3_companion_radio_full_femon');
assert.deepStrictEqual(mqttCompanion.loggingModes, ['none', 'usb', 'wifi', 'both']);
for (const mode of ['usb', 'both']) {
  assert.deepStrictEqual(picker.runtimeDirections(mqttCompanion, {logging: mode})[0].actions[0].commands,
    ['set powersaving off', 'set logging.output ' + mode, 'get logging.output']);
}
assert(picker.installSteps(mqttCompanion, 'bin').some(step => step.includes('set powersaving off')));
// The workaround belongs to the affected release, not every future ESP32 build.
for (const releaseFamily of ['v1.17.1.4-old', 'v1.17.1.5-halo-keymind-cascade-dev-newfix',
                             'v1.17.1.6-next', 'v1.17.1.50-next', '']) {
  const otherRelease = {...mqttCompanion, releaseFamily};
  assert.deepStrictEqual(picker.runtimeDirections(otherRelease, {logging: 'usb'})[0].actions[0].commands,
    ['set logging.output usb', 'get logging.output']);
  assert(!picker.installSteps(otherRelease, 'bin').some(step => step.includes('set powersaving off')));
}
const companionWifi = picker.runtimeDirections(mqttCompanion, {logging: 'wifi'});
assert.deepStrictEqual(companionWifi[0].actions[0].commands, ['set logging.output wifi', 'get logging.output']);
assert(companionWifi[0].actions[0].text.includes('enable the desired MQTT'));
assert(commands(companionWifi).includes('set mqtt.enabled on'));
assert(commands(companionWifi).includes('get wifi.cli'));
assert(commands(companionWifi).includes('set usb.logging on') === false);
assert(commands(companionWifi).includes('set gps on'));
const companionOff = picker.runtimeDirections(mqttCompanion, {logging: 'none'});
assert(companionOff[0].actions[0].text.includes('saved broker settings'));
for (const title of ['Device power saving', 'GPS', 'MQTT broker connections', 'WebConfig command terminal']) {
  assert.deepStrictEqual(companionWifi.find(s => s.title === title).actions,
    picker.runtimeDirections(observer, {}).find(s => s.title === title).actions);
}
const nrf = findControlled('RAK_4631_companion_radio_full');
assert.deepStrictEqual(picker.runtimeDirections(nrf, {logging: 'usb'})[0].actions[0].commands,
  ['set usb.logging on reboot']);
const rakRepeater = findControlled('RAK_4631_repeater');
const g3Repeater = findControlled('Station_G3_ESP32_repeater');
assert.deepStrictEqual(picker.runtimeDirections(g3Repeater, {logging: 'usb'})[0].actions[0].commands,
  ['set powersaving off', 'set usb.logging on']);
assert.deepStrictEqual(picker.runtimeDirections(g3Repeater, {logging: 'none'})[0].actions[0].commands,
  ['set usb.logging off']);
const rakCommands = picker.runtimeDirections(rakRepeater, {logging: 'usb', mode: 'standard'});
assert.deepStrictEqual(rakCommands[0].actions[0].commands, ['set usb.logging on']);
assert.strictEqual(rakCommands.find(s => s.title.startsWith('RS232')).actions[0].label, 'Off');
assert(commands(rakCommands).includes('set gps on'));
assert(!rakCommands.some(s => s.title === 'MQTT broker connections'));
const indicator = picker.runtimeDirections(findControlled('SenseCapIndicator-LoRa_companion_radio_full'), {});
assert(commands(indicator).includes('set companion.transport ble'));
assert(!indicator.some(s => s.title.startsWith('MQTT')));
// Never apply old hardware capabilities to another release, or to an unknown
// exact target. Fall back to role documentation rather than invented switches.
// Capacity directions belong to the corrected image, even when the release
// tag is shared with older assets or a user opens an older offline picker.
const memoryControl = controls.profiles.Heltec_v3_companion_radio_full;
assert(memoryControl.memoryNote.includes('150 contacts'));
const replacementFilename = 'Heltec_v3_companion_radio_full-v1.17.1.5-halo-keymind-cascade-dev-aa20e927.bin';
const replacementCatalog = picker.buildCatalog([release(liveFamily, '2026-09-01T00:00:00Z', [asset(replacementFilename)])], controls);
assert.strictEqual(replacementCatalog.profiles.length, 1, 'A replacement source hash must remain visible on the original release page');
const correctedMemoryProfile = replacementCatalog.profiles[0];
assert.strictEqual(picker.parseFirmwareAsset(asset(replacementFilename.replace('1.17.1.5-', '1.17.1.50-')), liveFamily), null);
const memorySteps = picker.installSteps(correctedMemoryProfile, 'bin');
assert(memorySteps.includes(memoryControl.memoryNote));
assert(memorySteps.indexOf(memoryControl.memoryNote) < memorySteps.findIndex(step => step.startsWith('Use this app-only image')));
assert(!picker.installSteps({...correctedMemoryProfile,
  files: [{name: 'Heltec_v3_companion_radio_full-v1.17.1.5-halo-keymind-cascade-dev-26303793.bin'}]}, 'bin').includes(memoryControl.memoryNote));
// During publication, both source revisions can briefly coexist. Directions
// must describe the selected download, even if another image is corrected.
const mixedMemoryCatalog = picker.buildCatalog([release(liveFamily, '2026-09-01T00:00:00Z', [
  asset(replacementFilename),
  asset(replacementFilename.replace('aa20e927', '26303793')),
  asset(replacementFilename.replace('.bin', '-merged.bin')),
])], controls);
const mixedMemoryProfile = mixedMemoryCatalog.profiles[0];
assert(!picker.installSteps(mixedMemoryProfile, 'bin').includes(memoryControl.memoryNote));
assert(picker.installSteps(mixedMemoryProfile, 'merged-bin').includes(memoryControl.memoryNote));

const paperControl = controls.profiles.Heltec_Wireless_Paper_companion_radio_full;
assert(paperControl.memoryNote.includes('350 contacts and 40 channels'));
assert(paperControl.memoryNote.includes('128 while mOTA'));
const paperFilename = 'Heltec_Wireless_Paper_companion_radio_full-' +
  liveFamily.replace('26303793', paperControl.memorySource.slice(0, 8)) + '.bin';
const paperCatalog = picker.buildCatalog([
  release(liveFamily, '2026-09-08T00:00:00Z', [asset(paperFilename)])
], controls);
assert(picker.installSteps(paperCatalog.profiles[0], 'bin').includes(paperControl.memoryNote));
const oldPaperCatalog = picker.buildCatalog([
  release(liveFamily, '2026-09-08T00:00:00Z', [asset(paperFilename.replace('1e4d1e16', 'aa20e927'))])
], controls);
assert(!picker.installSteps(oldPaperCatalog.profiles[0], 'bin').includes(paperControl.memoryNote));

const stale = picker.buildCatalog(controlledReleases, {...controls, familyTag: 'v0.0.0'});
assert(stale.profiles.every(p => !p.controls));
assert(!picker.runtimeDirections({...mqttCompanion, controls: undefined}, {}).some(s => s.title === 'GPS'));
console.log('role-specific runtime directions tests passed');

// Exercise every profile from the current generated catalog while the tests
// above keep the older release's USB and capacity workarounds intact.
const currentControls = require('../docs/_data/firmware_controls.json');
const currentAssets = Object.entries(currentControls.profiles).map(([target, info]) => {
  const source = info.loggingSource || info.memorySource || currentControls.source;
  const tag = currentControls.familyTag.replace(/-[0-9a-f]{8}$/, '-' + source.slice(0, 8));
  return asset(target + '-' + tag + (info.platform === 'NRF52_PLATFORM' ? '.uf2' : '.bin'));
});
const currentCatalog = picker.buildCatalog([
  release(currentControls.familyTag, '2026-09-13T00:00:00Z', currentAssets),
], currentControls);
assert.strictEqual(currentCatalog.rows.length, currentAssets.length);
assert(currentCatalog.profiles.every(profile => profile.controls && profile.chipFamily !== 'unknown'));
for (const [hardware, bases] of [
  ['RAK_4631', ['RAK_4631_repeater_unified_lora_ota', 'RAK_4631_repeater_ethernet']],
  ['RAK_3401', ['RAK_3401_repeater_unified_lora_ota']],
]) {
  const context = {hardware, role: 'repeater'};
  const expected = bases.flatMap(base => ['full', 'reduced'].map(policy => base + '-' + policy));
  const visible = currentCatalog.profiles.filter(profile => picker.profileMatchesFacets(profile, context));
  assert.deepStrictEqual(visible.map(profile => profile.target).sort(), expected.sort(),
    hardware + ' must recommend only combined storage/bridge images and genuine Ethernet variants');
  const choices = picker.firmwareProfileChoices(currentCatalog.profiles, context);
  assert.strictEqual(choices.length, expected.length, hardware + ' must have one choice per real image');
  assert.strictEqual(new Set(choices.map(choice => choice.label)).size, choices.length,
    hardware + ' must not show duplicate profile labels');
  for (const profile of visible.filter(item => item.target.includes('_unified_lora_ota-'))) {
    assert.deepStrictEqual(picker.profileFieldValues(profile, 'mode'),
      hardware === 'RAK_4631' ? ['standard', 'rs232'] : ['standard']);
    if (hardware === 'RAK_4631') {
      const bridge = picker.runtimeDirections(profile, {mode: 'rs232'})
        .find(section => section.title.startsWith('RS232 bridge'));
      assert(bridge && bridge.note.includes('set bridge.uart 1') && bridge.note.includes('pauses UART GPS'),
        'Both combined sensor policies must explain runtime UART selection and GPS sharing');
      assert(!bridge.note.includes('GPS-free image'));
    }
  }
}
const rakBridgeBase = 'RAK_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors';
const rakPartialAssets = currentAssets.filter(file =>
  file.name.startsWith('RAK_4631_repeater_unified_lora_ota-full-') ||
  file.name.startsWith(rakBridgeBase + '-reduced-'));
const rakPartialCatalog = picker.buildCatalog([
  release(currentControls.familyTag, '2026-10-08T00:00:00Z', rakPartialAssets),
], currentControls);
assert(rakPartialCatalog.profiles.some(profile => profile.target === rakBridgeBase + '-reduced'),
  'A Full combined image must not hide the only available Reduced bridge image');
const expandedEsp32 = currentCatalog.profiles.find(profile =>
  profile.target === 'Ebyte_EoRa-S3_Repeater-full-usb-wifi');
assert(expandedEsp32);
assert.strictEqual(expandedEsp32.ota, 'lora-receiver');
assert.deepStrictEqual(expandedEsp32.loggingModes, ['none', 'usb', 'wifi', 'both']);
assert(picker.installSteps(expandedEsp32, 'bin').some(step =>
  step.includes('exact board/role migration ZIP')));
assert(picker.installSteps(expandedEsp32, 'merged-bin').some(step =>
  step.includes('get storage.layout')));
assert.strictEqual(
  picker.migrationReleaseUrl(expandedEsp32, expandedEsp32.files[0]),
  'https://github.com/mikecarper/MeshCore/releases/tag/utility-' +
    currentControls.familyTag);
// Infrastructure now ships as the combined Full profile. A Companion remains
// a real, non-expanded negative case; do not invent a removed ordinary image.
const companionEsp32 = currentCatalog.profiles.find(profile =>
  profile.target === 'Ebyte_EoRa-S3_companion_radio_full');
assert(companionEsp32);
assert.strictEqual(
  picker.migrationReleaseUrl(companionEsp32, companionEsp32.files[0]), '');
const ikokaBase = 'ikoka_stick_nrf_30dbm_repeater';
const ikokaProfiles = ['full', 'reduced'].map(policy => {
  const profile = currentCatalog.profiles.find(item => item.target === ikokaBase + '-' + policy);
  assert(profile, 'The qualified current release must retain the ' + policy + ' nRF52 profile');
  assert.strictEqual(profile.sensorProfile, policy);
  assert.strictEqual(profile.feature, policy === 'full' ? 'full' : 'standard');
  assert.strictEqual(profile.ota, 'lora-receiver');
  assert.strictEqual(profile.logging, 'usb-runtime');
  assert.deepStrictEqual(profile.loggingModes, ['none', 'usb']);
  assert.deepStrictEqual(picker.runtimeDirections(profile, {logging: 'usb'})[0].actions[0].commands,
    ['set usb.logging on']);
  return profile;
});
assert(!currentCatalog.profiles.some(profile =>
  [ikokaBase, ikokaBase + '_lora_ota_no_external_sensors'].includes(profile.target)));
const ikokaUrl = 'https://example.com/firmware_picker/?chipFamily=nrf52&hardwareFamily=ikoka_stick_nrf_30dbm&hardware=ikoka_stick_nrf_30dbm&role=repeater&variant=default&install=zip&chipAuto=1';
const ikokaRelease = release(currentControls.familyTag, '2026-09-13T00:00:00Z',
  ikokaProfiles.flatMap(profile => {
    const source = currentControls.profiles[profile.target].loggingSource;
    const tag = currentControls.familyTag.replace(/-[0-9a-f]{8}$/, '-' + source.slice(0, 8));
    return ['zip', 'uf2'].map(extension => asset(profile.target + '-ota-' + tag + '.' + extension));
  }));
const ikokaCatalog = picker.buildCatalog([ikokaRelease], currentControls);
const ikokaSelection = picker.selectionFromUrl(ikokaUrl, ikokaCatalog.profiles);
assert.deepStrictEqual(ikokaSelection.unavailable, []);
assert.deepStrictEqual(ikokaCatalog.profiles.filter(profile =>
  picker.profileMatchesFacets(profile, ikokaSelection.filters)).map(profile => profile.target),
  ikokaProfiles.map(profile => profile.target));
for (const profile of ikokaProfiles) {
  const policyUrl = ikokaUrl + '&feature=' + profile.feature + '&ota=lora-receiver';
  const selection = picker.selectionFromUrl(policyUrl, ikokaCatalog.profiles);
  assert.deepStrictEqual(selection.unavailable, []);
  assert.deepStrictEqual(ikokaCatalog.profiles.filter(item =>
    picker.profileMatchesFacets(item, selection.filters)).map(item => item.target), [profile.target]);
  const current = ikokaCatalog.profiles.find(item => item.target === profile.target);
  assert.deepStrictEqual(current.installKinds, ['zip', 'uf2']);
  const choices = picker.firmwareProfileChoices(ikokaCatalog.profiles, {
    hardware: current.hardware, role: current.role, install: 'zip',
  });
  const choice = choices.find(item => item.value === picker.firmwareProfileValue(current));
  assert(choice, 'Both current Full/Reduced profiles must have distinct picker choices');
  assert.deepStrictEqual(ikokaCatalog.profiles.filter(item => picker.profileMatchesFacets(item,
    {...choice.filters, hardware: current.hardware, role: current.role, install: 'zip'}))
    .map(item => item.target), [current.target]);
  const mismatched = picker.buildCatalog([
    release(currentControls.familyTag, '2026-09-13T00:00:00Z', [
      asset(profile.target + '-ota-' +
        currentControls.familyTag.replace(/-[0-9a-f]{8}$/, '-306feebe') + '.zip'),
    ]),
  ], currentControls).profiles[0];
  assert.strictEqual(mismatched.logging, 'none', 'Logging metadata must match the published build source');
  assert.strictEqual(mismatched.sensorProfile, undefined, 'Sensor policy must match the published build source');
}
const capacityProfiles = currentCatalog.profiles.filter(profile => profile.controls.memoryNote);
assert(capacityProfiles.length > 0, 'Regeneration must preserve capacity directions');
for (const profile of capacityProfiles) {
  assert(picker.installSteps(profile, profile.installKinds[0]).includes(profile.controls.memoryNote), profile.target);
}
const currentObserver = currentCatalog.profiles.find(profile =>
  profile.controls && profile.controls.mqtt &&
  profile.controls.loggingModes && profile.controls.loggingModes.includes('both'));
assert(currentObserver);
assert.deepStrictEqual(picker.runtimeDirections(currentObserver, {logging: 'usb'})[0].actions[0].commands,
  ['set logging.output usb', 'get logging.output']);
const combinedControlData = JSON.parse(JSON.stringify(currentControls));
combinedControlData.profiles[currentObserver.target].espnowBridge = true;
const combinedCatalog = picker.buildCatalog([
  release(currentControls.familyTag, '2026-09-13T00:00:00Z', currentAssets),
], combinedControlData);
const combinedObserver = combinedCatalog.profiles.find(profile => profile.target === currentObserver.target);
assert.deepStrictEqual(picker.profileFieldValues(combinedObserver, 'mode'), ['standard', 'espnow']);
assert(picker.profileMatches(combinedObserver, {mode: 'espnow'}, ['mode']));
const combinedDirections = picker.runtimeDirections(combinedObserver, {logging: 'usb'});
assert(combinedDirections.some(section => section.title === 'MQTT broker connections'));
assert(combinedDirections.some(section => section.title === 'ESP-NOW bridge'));
assert.strictEqual(commands(combinedDirections).filter(command => command === 'set mqtt.enabled on').length, 1);
assert.strictEqual(commands(combinedDirections).filter(command => command === 'set espnow.enabled on').length, 1);
assert.strictEqual(commands(combinedDirections).filter(command => command === 'set bridge.enabled on').length, 0);
console.log('current release metadata and capacity directions tests passed');

// The normal Full repeater can carry both independent bridges. Neither its
// mode choices nor its ESP-NOW instructions may overwrite the UART transport.
const bridgeFamily = 'v1.17.1.9-halo-keymind-cascade-dev-5f10e7d6';
const bridgeTargets = ['MKE_s3_repeater-full-logging', 'heltec_v4_tft_repeater-full-logging',
  'Heltec_v2_repeater_bridge_espnow'];
const bridgeProfiles = {
  [bridgeTargets[0]]: {platform: 'ESP32_PLATFORM', rs232: true, espnowBridge: true,
    mqtt: false, updateMethods: ['wifi', 'lora']},
  [bridgeTargets[1]]: {platform: 'ESP32_PLATFORM', rs232: false, espnowBridge: true,
    mqtt: false, updateMethods: ['wifi', 'lora']},
  [bridgeTargets[2]]: {platform: 'ESP32_PLATFORM', rs232: false, espnowBridge: true,
    mqtt: false, updateMethods: []},
};
const bridgeRelease = release(bridgeFamily, '2026-10-05T00:00:00Z',
  bridgeTargets.map(target => asset(target + '-ota-' + bridgeFamily + '.bin')));
const bridgeCatalog = picker.buildCatalog([bridgeRelease], {
  familyTag: bridgeFamily, profiles: bridgeProfiles, partitionMigrations: {},
});
const findBridge = target => bridgeCatalog.profiles.find(profile => profile.target === target);
const mkeBridge = findBridge(bridgeTargets[0]);
assert.deepStrictEqual(picker.profileFieldValues(mkeBridge, 'mode'), ['standard', 'rs232', 'espnow']);
for (const mode of ['standard', 'rs232', 'espnow']) {
  assert(picker.profileMatches(mkeBridge, {mode}, ['mode']));
  const directions = picker.runtimeDirections(mkeBridge, {mode});
  const uart = directions.find(section => /^RS232 bridge/.test(section.title));
  const wireless = directions.find(section => section.title === 'ESP-NOW bridge');
  assert(uart && wireless);
  assert(commands([uart]).includes('set bridge.enabled on'));
  assert(!commands([uart]).some(command => command.includes('espnow.enabled')));
  assert.deepStrictEqual(wireless.actions.map(action => action.commands), [
    ['set espnow.enabled on'], ['set espnow.enabled off'], ['get espnow.running'],
  ]);
  assert(!commands([wireless]).some(command => command.includes('bridge.enabled')));
  assert(wireless.note.includes('Independent of RS-232'));
}
const plainBridge = findBridge(bridgeTargets[1]);
assert.deepStrictEqual(picker.profileFieldValues(plainBridge, 'mode'), ['standard', 'espnow']);
assert(picker.profileMatches(plainBridge, {mode: 'espnow'}, ['mode']));
const plainDirections = picker.runtimeDirections(plainBridge, {mode: 'espnow'});
assert(!plainDirections.some(section => /^RS232 bridge/.test(section.title)));
assert(commands(plainDirections.filter(section => section.title === 'ESP-NOW bridge'))
  .includes('set bridge.enabled on'));
// Historical dedicated images did not expose espnow.enabled. Keep their
// directions compatible without guessing support from the release version.
const legacyDirections = picker.runtimeDirections(findBridge(bridgeTargets[2]), {mode: 'espnow'});
assert.deepStrictEqual(legacyDirections.find(section => section.title === 'ESP-NOW bridge')
  .actions.map(action => action.commands), [
    ['set bridge.enabled on'], ['set bridge.enabled off'], ['get bridge.running'],
  ]);
const staleBridgeMetadata = picker.buildCatalog([bridgeRelease], {
  familyTag: 'v1.17.1.8', profiles: bridgeProfiles,
});
assert(staleBridgeMetadata.profiles.every(profile => !profile.controls));
assert(!picker.runtimeDirections(staleBridgeMetadata.profiles.find(profile =>
  profile.target === bridgeTargets[0]), {mode: 'espnow'}).some(section => section.title === 'ESP-NOW bridge'));
console.log('combined UART/ESP-NOW picker modes, independent commands and historical compatibility passed');

// MQTT, ESP-NOW and UART coexist in the same qualified Full artifact. UART
// instructions must never use MQTT's historical ESP-NOW bridge alias.
const tripleTargets = ['Heltec_v3_repeater', 'Heltec_WSL3_repeater',
  'RAK_3112_repeater']
  .map(target => target + '-full-usb-wifi');
const tripleCatalog = picker.buildCatalog([
  release(bridgeFamily, '2026-10-05T00:00:00Z', tripleTargets.map(target =>
    asset(target + '-ota-' + bridgeFamily + '.bin'))),
], {
  familyTag: bridgeFamily,
  profiles: Object.fromEntries(tripleTargets.map(target => [target, {
    platform: 'ESP32_PLATFORM', rs232: true, mqtt: true, espnowBridge: true,
    updateMethods: ['wifi', 'lora'],
  }])),
});
for (const profile of tripleCatalog.profiles) {
  assert.deepStrictEqual(picker.profileFieldValues(profile, 'mode'),
    ['standard', 'rs232', 'espnow']);
  const directions = picker.runtimeDirections(profile, {mode: 'rs232'});
  const uart = directions.find(section => /^RS232 bridge/.test(section.title));
  const wireless = directions.find(section => section.title === 'ESP-NOW bridge');
  const mqtt = directions.find(section => section.title === 'MQTT broker connections');
  assert(uart && wireless && mqtt);
  assert.deepStrictEqual(uart.actions.map(action => action.commands), [
    ['set rs232.enabled on', 'get rs232.running'], ['set rs232.enabled off'],
    ['set rs232.enabled off', 'set bridge.baud 115200', 'set rs232.enabled on'],
  ]);
  assert(commands([wireless]).includes('set espnow.enabled on'));
  assert(commands([mqtt]).includes('set mqtt.enabled on'));
  assert(!commands([uart, wireless, mqtt]).some(command => command.includes('bridge.enabled')));
}
assert.strictEqual(tripleCatalog.profiles.length, tripleTargets.length);
console.log('three-transport Full picker uses independent UART, MQTT and ESP-NOW controls');

// T-LoRa cannot fit all three transports in its runtime RAM budget. Both
// capacity-safe Full images remain selectable with their actual capabilities.
const tloraNormalTarget = 'LilyGo_TLora_V2_1_1_6_repeater-full-usb-wifi';
const tloraObserverTarget = 'LilyGo_TLora_V2_1_1_6_repeater_observer_mqtt_-full-usb-wifi';
const tloraCatalog = picker.buildCatalog([
  release(bridgeFamily, '2026-10-05T00:00:00Z', [tloraNormalTarget, tloraObserverTarget]
    .map(target => asset(target + '-ota-' + bridgeFamily + '.bin'))),
], {
  familyTag: bridgeFamily,
  profiles: {
    [tloraNormalTarget]: {
      platform: 'ESP32_PLATFORM', rs232: true, mqtt: false, espnowBridge: true,
      updateMethods: ['wifi', 'lora'],
      loggingModes: ['none', 'usb'], loggingControl: 'usb.logging',
    },
    [tloraObserverTarget]: {
      platform: 'ESP32_PLATFORM', rs232: false, mqtt: true, espnowBridge: true,
      updateMethods: ['wifi', 'lora'],
      loggingModes: ['none', 'usb', 'wifi', 'both'], loggingControl: 'logging.output',
    },
  },
});
assert.strictEqual(tloraCatalog.profiles.length, 2);
const tloraNormalProfile = tloraCatalog.profiles.find(profile => profile.target === tloraNormalTarget);
const tloraObserverProfile = tloraCatalog.profiles.find(profile => profile.target === tloraObserverTarget);
assert(tloraNormalProfile && tloraObserverProfile);
assert.deepStrictEqual(picker.profileFieldValues(tloraNormalProfile, 'mode'),
  ['standard', 'rs232', 'espnow']);
assert.deepStrictEqual(picker.profileFieldValues(tloraObserverProfile, 'mode'),
  ['standard', 'espnow']);
assert.deepStrictEqual(picker.profileFieldValues(tloraNormalProfile, 'logging'), ['none', 'usb']);
assert.deepStrictEqual(picker.profileFieldValues(tloraObserverProfile, 'logging'),
  ['none', 'usb', 'wifi', 'both']);
assert(!picker.profileMatches(tloraNormalProfile, {logging: 'wifi'}, ['logging']));
assert(picker.profileMatches(tloraObserverProfile, {logging: 'wifi'}, ['logging']));
const tloraNormalDirections = picker.runtimeDirections(tloraNormalProfile, {mode: 'rs232'});
const tloraObserverDirections = picker.runtimeDirections(tloraObserverProfile, {mode: 'espnow'});
assert(tloraNormalDirections.some(section => /^RS232 bridge/.test(section.title)));
assert(tloraNormalDirections.some(section => section.title === 'ESP-NOW bridge'));
assert(!tloraNormalDirections.some(section => section.title === 'MQTT broker connections'));
assert(tloraObserverDirections.some(section => section.title === 'MQTT broker connections'));
assert(tloraObserverDirections.some(section => section.title === 'ESP-NOW bridge'));
assert(!tloraObserverDirections.some(section => /^RS232 bridge/.test(section.title)));
assert(!commands(tloraObserverDirections).some(command => command.startsWith('set rs232.')));
console.log('T-LoRa RAM exception preserves separate UART and MQTT Full picker choices');

// Partition expansion uses the packager's lookup and published utility assets,
// not hardware labels or guessed slugs (several historical names differ).
const migrationFamily = currentControls.familyTag;
const utilityTag = 'utility-' + migrationFamily;
const utilityUrl = 'https://github.com/mikecarper/MeshCore/releases/tag/' + utilityTag;
function migrationZip(packageName, tag = migrationFamily) {
  const name = packageName + '-' + tag + '-migration.zip';
  return {...asset(name), browser_download_url:
    'https://github.com/mikecarper/MeshCore/releases/download/utility-' + tag + '/' + name};
}
const migrationAssets = Object.values(currentControls.partitionMigrations).map(name => migrationZip(name));
function migrationCatalog(assets = migrationAssets, metadata = currentControls, utilityOverrides = {}) {
  return picker.buildCatalog([
    release(migrationFamily, '2026-09-13T00:00:00Z', currentAssets),
    release(utilityTag, '2026-09-13T00:00:01Z', assets, utilityOverrides),
  ], metadata);
}
const withMigrations = migrationCatalog();
assert.strictEqual(withMigrations.rows.length, currentCatalog.rows.length,
  'Migration ZIPs must not become firmware/Serial DFU choices');
const expandedWithMigration = withMigrations.profiles.find(profile =>
  profile.target === expandedEsp32.target);
assert.strictEqual(expandedWithMigration.migrationPackage, 'ebyte-eora-s3-repeater');
assert.deepStrictEqual(picker.migrationLink(expandedWithMigration, expandedWithMigration.files[0]), {
  url: migrationZip('ebyte-eora-s3-repeater').browser_download_url,
  label: 'Download exact partition-expansion ZIP',
});
const g2Target = 'Station_G2_repeater_observer_mqtt-full-usb-wifi';
function g2MigrationLink(catalog) {
  const profile = catalog.profiles.find(item => item.target === g2Target);
  return picker.migrationLink(profile, profile.files[0]);
}
const directG2Link = {
  url: migrationZip('station-g2-repeater').browser_download_url,
  label: 'Download exact partition-expansion ZIP',
};
const fallbackMigrationLink = {
  url: utilityUrl, label: 'Check partition-expansion availability',
};
assert.deepStrictEqual(g2MigrationLink(withMigrations), directG2Link);
const g2PickerUrl = 'https://mikecarper.github.io/MeshCore/firmware_picker/' +
  '?chipFamily=esp32&hardwareFamily=Station_G2&hardware=Station_G2&role=repeater&mode=espnow&install=bin&chipAuto=1';
const g2Selection = picker.selectionFromUrl(g2PickerUrl, withMigrations.profiles);
assert.deepStrictEqual(g2Selection.unavailable, []);
const g2Selected = withMigrations.profiles.filter(profile =>
  picker.profileMatchesFacets(profile, g2Selection.filters));
assert.deepStrictEqual(g2Selected.map(profile => profile.target), [g2Target]);
assert.deepStrictEqual(picker.migrationLink(g2Selected[0], g2Selected[0].files[0]), directG2Link);

// Exercise every row in the packager lookup, even recipes whose canonical
// image lives only inside its ZIP rather than the ordinary firmware matrix.
for (const [target, packageName] of Object.entries(currentControls.partitionMigrations)) {
  for (const suffix of ['-full-logging', '-full-usb-wifi']) {
    const fullTarget = target + suffix;
    const profile = Object.assign(picker.parseTargetProfile(fullTarget), {
      releaseFamily: migrationFamily, controls: {platform: 'ESP32_PLATFORM'},
      files: [{releaseUrl: 'https://github.com/mikecarper/MeshCore/releases/tag/' + migrationFamily}],
    });
    profile.migrationPackage = picker.migrationPackageForProfile(profile, currentControls.partitionMigrations);
    profile.migrationAsset = picker.migrationAssetForProfile(profile, migrationAssets.map(item => ({
      name: item.name, url: item.browser_download_url, releaseTag: utilityTag, releaseUrl: utilityUrl,
    })));
    assert.strictEqual(profile.migrationPackage, packageName, fullTarget);
    assert.strictEqual(picker.migrationLink(profile, profile.files[0]).url,
      migrationZip(packageName).browser_download_url, fullTarget);
  }
}
const currentMigrationProfiles = withMigrations.profiles.filter(profile => profile.migrationPackage);
assert(currentMigrationProfiles.length > 0);
for (const profile of currentMigrationProfiles) {
  assert(profile.migrationAsset, profile.target);
  assert.strictEqual(picker.migrationLink(profile, profile.files[0]).url,
    migrationZip(profile.migrationPackage).browser_download_url, profile.target);
}
assert.strictEqual(picker.migrationLink(companionEsp32, companionEsp32.files[0]), null);
for (const profile of ikokaProfiles) {
  assert.strictEqual(picker.migrationLink(profile, profile.files[0]), null);
}
const unmappedVariant = withMigrations.profiles.find(profile =>
  profile.target === 'Station_G3_ESP32_r2_repeater_observer_mqtt-full-usb-wifi');
assert(unmappedVariant);
assert.deepStrictEqual(picker.migrationLink(unmappedVariant, unmappedVariant.files[0]), fallbackMigrationLink,
  'An unlisted board revision must not borrow another board recipe');

const noG2Assets = migrationAssets.filter(item => item.name !== migrationZip('station-g2-repeater').name);
assert.deepStrictEqual(g2MigrationLink(migrationCatalog(noG2Assets)), fallbackMigrationLink,
  'Room-server and logging-hardware packages must not stand in for G2 repeater');
assert.deepStrictEqual(g2MigrationLink(migrationCatalog([
  ...noG2Assets, migrationZip('station-g2-repeater', family),
])), fallbackMigrationLink, 'A ZIP for another release is not a match');
assert.deepStrictEqual(g2MigrationLink(migrationCatalog(migrationAssets, currentControls,
  {tag_name: 'utility-' + family})), fallbackMigrationLink, 'Ignore other release families');
assert.deepStrictEqual(g2MigrationLink(migrationCatalog(migrationAssets, currentControls,
  {tag_name: 'logging-' + migrationFamily})), fallbackMigrationLink, 'Require the utility page');
assert.deepStrictEqual(g2MigrationLink(migrationCatalog([
  ...migrationAssets, migrationZip('station-g2-repeater'),
])), fallbackMigrationLink, 'Duplicate matches must fail closed');
for (const badUrl of ['javascript:alert(1)', 'https://example.com/download.zip',
  migrationZip('station-g2-repeater').browser_download_url.replace('/mikecarper/', '/someone-else/'),
  migrationZip('station-g2-room-server').browser_download_url]) {
  assert.deepStrictEqual(g2MigrationLink(migrationCatalog([
    ...noG2Assets, {...migrationZip('station-g2-repeater'), browser_download_url: badUrl},
  ])), fallbackMigrationLink, badUrl);
}
assert.deepStrictEqual(g2MigrationLink(migrationCatalog(migrationAssets, currentControls,
  {html_url: utilityUrl.replace('/mikecarper/', '/someone-else/')})), fallbackMigrationLink);
assert.deepStrictEqual(g2MigrationLink(migrationCatalog(migrationAssets,
  {...currentControls, partitionMigrations: undefined})), fallbackMigrationLink,
  'Older controls without a lookup retain a clearly labeled fallback');
assert.strictEqual(g2MigrationLink(migrationCatalog(migrationAssets,
  {...currentControls, familyTag: family})), null, 'Stale controls must not supply a migration');
console.log('exact partition-expansion links passed for all ' + migrationAssets.length + ' lookup recipes');

assert.strictEqual(nrf.chipFamily, 'nrf52');
assert.strictEqual(mqttCompanion.chipFamily, 'esp32');
assert.deepStrictEqual(picker.facetValues(controlled.profiles, {chipFamily: 'nrf52'}, 'hardwareFamily'), ['RAK_4631']);
assert(stale.profiles.every(p => p.chipFamily === 'unknown'));

const chipReleases = [release(family, '2026-09-01T00:00:00Z', [
  asset('pico_repeater-' + family + '.uf2'),
  asset('pico_room_server-' + family + '.uf2'),
  asset('wio-e5-repeater-' + family + '.hex'),
  asset('unlisted_repeater-' + family + '.uf2'),
])];
const chipCatalog = picker.buildCatalog(chipReleases, {familyTag: family, profiles: {
  pico_repeater: {platform: 'RP2040_PLATFORM'},
  'wio-e5-repeater': {platform: 'STM32_PLATFORM'},
}});
assert.deepStrictEqual(picker.uniqueValues(chipCatalog.profiles, 'chipFamily').sort(), ['rp2040', 'stm32', 'unknown']);
const sibling = chipCatalog.profiles.find(p => p.target === 'pico_room_server');
assert.strictEqual(sibling.chipFamily, 'rp2040');
assert.strictEqual(sibling.controls, undefined, 'Chip inheritance must not invent runtime controls');
assert.strictEqual(chipCatalog.profiles.find(p => p.target === 'unlisted_repeater').chipFamily, 'unknown', 'UF2 does not identify a chip family');
console.log('chip-family filtering tests passed');

const shareBase = 'https://example.com/firmware_picker/?h=storage#installation-methods';
const shareFilters = {hardwareFamily: 'RAK_4631', hardware: 'RAK_4631',
  chipFamily: 'nrf52', role: 'companion', mode: 'full', feature: 'full',
  logging: 'usb', ota: 'lora-source', variant: 'default', install: 'uf2'};
const shareUrl = picker.selectionUrl(shareBase, shareFilters, true);
const restoredShare = picker.selectionFromUrl(shareUrl, controlled.profiles);
assert.deepStrictEqual(restoredShare.unavailable, []);
assert.strictEqual(restoredShare.automaticChipFamily, true);
for (const field of picker.FACET_FIELDS.filter(f => f !== 'chipFamily')) {
  assert.strictEqual(restoredShare.filters[field], shareFilters[field], field);
}
assert.strictEqual(new URL(shareUrl).searchParams.get('h'), 'storage');
assert.strictEqual(new URL(shareUrl).hash, '#installation-methods');
assert.strictEqual(picker.selectionUrl(shareUrl, {}, false), shareBase);
const anyChip = picker.selectionFromUrl(picker.selectionUrl(shareBase,
  {...shareFilters, chipFamily: ''}, false), controlled.profiles);
assert.strictEqual(anyChip.automaticChipFamily, false);
assert(!anyChip.filters.chipFamily);
const partialShare = picker.selectionFromUrl(shareBase + '&unused=value', controlled.profiles);
assert.deepStrictEqual(partialShare.filters, {});
const hardwareOnly = picker.selectionFromUrl('https://example.com/?hardware=RAK_4631', controlled.profiles);
assert.strictEqual(hardwareOnly.filters.hardwareFamily, 'RAK_4631');
assert.strictEqual(hardwareOnly.automaticChipFamily, true);
const staleShare = picker.selectionFromUrl('https://example.com/?hardware=RAK_4631&role=companion&variant=removed&install=bin', controlled.profiles);
assert.strictEqual(staleShare.filters.hardware, 'RAK_4631');
assert.strictEqual(staleShare.filters.role, 'companion');
assert.deepStrictEqual(staleShare.unavailable, ['variant=removed', 'install=bin']);
const escapedShare = picker.selectionFromUrl('https://example.com/?role=%3Cscript%3E', controlled.profiles);
assert.deepStrictEqual(escapedShare.filters, {});
assert.deepStrictEqual(escapedShare.unavailable, ['role=<script>']);
console.log('shareable picker URL tests passed');

// One choice carries OTA, feature set and sensor/storage tradeoffs together.
// Every existing profile remains reachable and resolves to its original files.
for (const candidateCatalog of [catalog, controlled, currentCatalog, chipCatalog, withMigrations]) {
  for (const profile of candidateCatalog.profiles) {
    const context = {
      hardwareFamily: profile.hardwareFamily, hardware: profile.hardware,
      role: profile.role, mode: profile.mode, install: profile.installKinds[0],
    };
    const choices = picker.firmwareProfileChoices(candidateCatalog.profiles, context);
    const value = picker.firmwareProfileValue(profile);
    const choice = choices.find(item => item.value === value);
    assert(choice, profile.target);
    assert.deepStrictEqual(choice.filters, {
      ota: profile.ota, feature: profile.feature, variant: profile.variant,
    });
    assert.deepStrictEqual(picker.firmwareProfileFilters(value), choice.filters);
    const chosen = Object.assign({}, context, choice.filters);
    const matches = candidateCatalog.profiles.filter(item => picker.profileMatchesFacets(item, chosen));
    assert(matches.includes(profile), profile.target);
    assert(picker.resolveProfileAssets(matches, context.install)
      .some(item => item.profile === profile), profile.target);
    const linked = picker.selectionFromUrl(picker.selectionUrl(shareBase, chosen, true), candidateCatalog.profiles);
    assert.deepStrictEqual(linked.unavailable, [], profile.target);
    assert.strictEqual(picker.firmwareProfileValue(linked.filters), value, profile.target);
    assert.strictEqual(new Set(choices.map(item => item.value)).size, choices.length);
  }
}

const compactChoice = picker.firmwareProfileChoices(catalog.profiles, {
  hardware: 'Station_G2', role: 'repeater',
}).find(item => item.filters.variant === 'no-external-sensors');
assert(compactChoice);
assert.strictEqual(compactChoice.label, 'Receives LoRa OTA - Reduced optional sensors');
assert.strictEqual(compactChoice.filters.ota, 'lora-receiver');
assert(!compactChoice.label.includes('No I2C'));
const fullChoice = picker.firmwareProfileChoices(catalog.profiles, {
  hardware: 'Station_G2', role: 'companion',
})[0];
assert.strictEqual(fullChoice.label, 'Full - LoRa OTA source only');
assert.strictEqual(fullChoice.filters.ota, 'lora-source');
assert.strictEqual(fullChoice.filters.feature, 'full');
assert.strictEqual(picker.firmwareProfileLabel({ota: 'lora-receiver', feature: 'full', variant: 'default'}),
  'Full - Receives LoRa OTA');
assert(picker.firmwareProfileLabel({ota: 'lora-receiver', feature: 'standard', variant: 'w25q16'})
  .includes('External storage board (W25Q16)'));

// New nRF52 releases keep both sensor policies, using verified release-bound
// controls rather than a publication suffix as evidence of compiled drivers.
const sensorSource = 'deadbeef' + '0'.repeat(32);
const sensorFamily = 'v1.17.1.7-halo-keymind-cascade-dev-deadbeef';
const sensorAssets = [];
const sensorControls = {familyTag: sensorFamily, profiles: {}};
for (const role of ['repeater', 'room_server', 'sensor']) {
  for (const storage of ['', '_w25q16']) {
    const logical = 'RAK_3401_' + role + '_lora_ota_no_external_sensors' + storage;
    for (const policy of ['full', 'reduced']) {
      const publication = logical + '-' + policy;
      for (const extension of ['zip', 'uf2']) {
        sensorAssets.push(asset(publication + '-ota-' + sensorFamily + '.' + extension));
      }
      sensorControls.profiles[publication] = {
        platform: 'NRF52_PLATFORM', otaRole: 'lora-receiver', updateMethods: ['lora'],
        sensorProfile: policy, sensorProfileSource: sensorSource,
      };
    }
  }
}
const sensorReleases = [release(sensorFamily, '2026-10-02T00:00:00Z', sensorAssets)];
const sensorCatalog = picker.buildCatalog(sensorReleases, sensorControls);
assert.strictEqual(sensorCatalog.profiles.length, 12);
for (const item of sensorCatalog.profiles) {
  const policy = item.target.endsWith('-full') ? 'full' : 'reduced';
  assert.strictEqual(item.sensorProfile, policy);
  assert.strictEqual(item.chipFamily, 'nrf52');
  assert.strictEqual(item.ota, 'lora-receiver');
  assert.strictEqual(item.feature, policy === 'full' ? 'full' : 'standard');
  assert(!item.variant.includes('-reduced'));
  if (policy === 'full') assert(!item.variant.includes('no-external-sensors'));
  const label = picker.firmwareProfileLabel(item);
  assert(label.startsWith(policy === 'full' ? 'Full supported sensors + LoRa OTA' : 'Reduced sensors + LoRa OTA'));
  if (item.target.includes('w25q16')) assert(label.includes('W25Q16'));
  const choices = picker.firmwareProfileChoices(sensorCatalog.profiles, {
    hardware: item.hardware, role: item.role, mode: item.mode, install: 'zip',
  });
  const choice = choices.find(entry => entry.value === picker.firmwareProfileValue(item));
  assert(choice, item.target);
  const selected = sensorCatalog.profiles.filter(profile => picker.profileMatchesFacets(profile,
    {...choice.filters, hardware: item.hardware, role: item.role, mode: item.mode}));
  assert.deepStrictEqual(selected.map(profile => profile.target), [item.target]);
  assert.deepStrictEqual(item.installKinds, ['zip', 'uf2']);
  const steps = picker.installSteps(item, 'zip');
  assert(steps.some(step => step.includes(policy === 'full' ? 'complete sensor drivers' : 'Omits selected optional')));
  if (policy === 'full') assert(!steps.some(step => step.includes('compact profile omits')));
}
for (const change of [{sensorProfileSource: 'bad'}, {sensorProfileSource: 'f'.repeat(40)},
                      {platform: 'ESP32_PLATFORM'}, {updateMethods: []}, {otaRole: 'none'}]) {
  const changedControls = structuredClone(sensorControls);
  Object.values(changedControls.profiles).forEach(info => Object.assign(info, change));
  assert(picker.buildCatalog(sensorReleases, changedControls).profiles.every(item => !item.sensorProfile));
}
assert(picker.buildCatalog(sensorReleases).profiles.every(item => !item.sensorProfile));
assert(picker.buildCatalog(sensorReleases, {...sensorControls, familyTag: 'v0.0.0'}).profiles
  .every(item => !item.sensorProfile));
for (const length of [7, 9, 40]) {
  const hash = sensorSource.slice(0, length);
  const tag = sensorFamily.replace(/-deadbeef$/, '-' + hash);
  const files = sensorAssets.map(file => asset(file.name.replace(/-deadbeef\.(zip|uf2)$/, '-' + hash + '.$1')));
  const metadata = {...sensorControls, familyTag: tag};
  const profiles = picker.buildCatalog([release(tag, '2026-10-02T00:00:00Z', files)], metadata).profiles;
  assert.strictEqual(profiles.length, 12, 'Git abbreviation length ' + length);
  assert(profiles.every(item => item.sensorProfile), 'Git abbreviation length ' + length);
  const wrong = hash.slice(0, -1) + (hash.endsWith('f') ? '1' : 'f');
  const badFiles = files.map(file => asset(file.name.replace('-' + hash + '.', '-' + wrong + '.')));
  const badProfiles = picker.buildCatalog([release(tag, '2026-10-02T00:00:00Z', badFiles)], metadata).profiles;
  assert.strictEqual(badProfiles.length, 12);
  assert(badProfiles.every(item => !item.sensorProfile), 'Mismatched source of length ' + length);
}
const mixedSourceFiles = sensorAssets.map(file => asset(file.name.endsWith('.uf2')
  ? file.name.replace('-deadbeef.uf2', '-deadbeef1.uf2') : file.name));
assert(picker.buildCatalog([release(sensorFamily, '2026-10-02T00:00:00Z', mixedSourceFiles)], sensorControls)
  .profiles.every(item => !item.sensorProfile), 'Every file must bind to the exact source');
console.log('nRF52 full and reduced sensor OTA profile pairs and qualification gating passed');

// A combined profile can be replaced in one click even when its former
// OTA/feature/variant facets have narrowed the catalog to a different image.
const replacementChoices = picker.firmwareProfileChoices(catalog.profiles, {
  hardware: 'Station_G2', role: 'repeater',
  ota: 'none', feature: 'standard', variant: 'default',
});
assert(replacementChoices.some(item => item.value === compactChoice.value));
assert.deepStrictEqual(picker.firmwareProfileFilters(''), {ota: '', feature: '', variant: ''});
assert.strictEqual(picker.firmwareProfileValue({}), '');
for (const invalid of ['invalid', '{}', '["none"]', '["none",false,"default"]']) {
  assert.strictEqual(picker.firmwareProfileFilters(invalid), null);
}

// Legacy partial links keep a visible selection without guessing a sensor
// recipe, Full layout or OTA capability that the link did not request.
for (const partial of [{ota: 'lora-receiver'}, {feature: 'standard'}, {variant: 'no-external-sensors'}]) {
  const context = Object.assign({hardware: 'Station_G2', role: 'repeater'}, partial);
  const value = picker.firmwareProfileValue(context);
  const selected = picker.firmwareProfileChoices(catalog.profiles, context).find(item => item.value === value);
  assert(selected);
  assert(selected.label.includes('other profile choices open'));
  for (const field of picker.PROFILE_FIELDS) assert.strictEqual(selected.filters[field], partial[field] || '');
}
assert(!picker.firmwareProfileChoices(catalog.profiles, {
  hardware: 'Station_G2', role: 'repeater', variant: 'missing',
}).some(item => item.filters.variant === 'missing'));
console.log('combined firmware profile choices and legacy links passed');

// Mobile progress counts builds, not the installation files for each build.
const feedbackFilters = {hardware: 'Heltec_v3', role: 'companion'};
const v3Feedback = controlled.profiles.find(p => p.target === 'Heltec_v3_companion_radio_full');
const feedbackCatalog = [Object.assign({}, v3Feedback, {
  installKinds: ['merged-bin', 'bin'],
})];
const feedbackSnapshot = JSON.stringify(feedbackCatalog);
assert.deepStrictEqual(picker.selectionProgress(feedbackCatalog, feedbackFilters), {
  count: 1, state: 'single', title: '1 firmware build left',
  note: 'Choose an install operation.',
});
for (const install of ['merged-bin', 'bin']) {
  const progress = picker.selectionProgress(feedbackCatalog, {...feedbackFilters, install});
  assert.strictEqual(progress.count, 1);
  assert(progress.note.includes('Confirm your board and install choice'));
}
assert.strictEqual(picker.selectionProgress(controlled.profiles, {}).state, 'multiple');
assert.strictEqual(picker.selectionProgress(controlled.profiles, {}).count, controlled.profiles.length);
assert.strictEqual(picker.selectionProgress(feedbackCatalog, {hardware: 'Missing'}).state, 'empty');
assert.strictEqual(picker.selectionProgress([], {}).count, 0);
assert.strictEqual(picker.selectionProgress([], {}).title, 'No matching firmware builds');
assert.strictEqual(picker.selectionProgress(feedbackCatalog).count, 1);

function feedbackOptions(values) {
  return [{value: ''}].concat(values.map(value => ({value})));
}
assert(picker.choicesUseSameFirmware(feedbackCatalog, feedbackFilters, 'logging',
  feedbackOptions(v3Feedback.loggingModes)));
assert(picker.choicesUseSameFirmware(feedbackCatalog, {...feedbackFilters, logging: 'usb'}, 'logging',
  feedbackOptions(v3Feedback.loggingModes)));
assert(picker.choicesUseSameFirmware(feedbackCatalog, feedbackFilters, 'mode',
  feedbackOptions(picker.profileFieldValues(v3Feedback, 'mode'))));
assert(picker.choicesUseSameFirmware(feedbackCatalog, feedbackFilters, 'firmwareProfile',
  picker.firmwareProfileChoices(feedbackCatalog, feedbackFilters)));
// A sole available option can be marked without disabling it or selecting it.
assert(picker.choicesUseSameFirmware(feedbackCatalog, feedbackFilters, 'role',
  feedbackOptions(['companion'])));
assert(!picker.choicesUseSameFirmware(feedbackCatalog, feedbackFilters, 'install',
  feedbackOptions(['merged-bin', 'bin'])));

// One currently selected build is not enough: alternatives may change builds.
const feedbackAlternatives = [
  {...feedbackCatalog[0], loggingModes: ['none', 'usb'], mode: 'usb', connectionModes: ['usb']},
  {...feedbackCatalog[0], target: 'second-build', loggingModes: ['wifi'],
    mode: 'wifi', connectionModes: ['wifi'], feature: 'standard', ota: 'none'},
];
const selectedFeedback = {...feedbackFilters, logging: 'usb'};
assert.strictEqual(picker.selectionProgress(feedbackAlternatives, selectedFeedback).count, 1);
assert(!picker.choicesUseSameFirmware(feedbackAlternatives, selectedFeedback, 'logging',
  feedbackOptions(['none', 'usb', 'wifi'])));
assert(!picker.choicesUseSameFirmware(feedbackAlternatives, {...feedbackFilters, mode: 'usb'}, 'mode',
  feedbackOptions(['usb', 'wifi'])));
const selectedProfileFeedback = {...feedbackFilters, feature: 'full', ota: 'lora-source', variant: 'default'};
assert(!picker.choicesUseSameFirmware(feedbackAlternatives, selectedProfileFeedback, 'firmwareProfile',
  picker.firmwareProfileChoices(feedbackAlternatives, selectedProfileFeedback)));
// Multiple builds can all share a runtime choice; report that accurately too.
assert(picker.choicesUseSameFirmware(feedbackAlternatives.map(p => ({...p, loggingModes: ['none', 'usb']})),
  feedbackFilters, 'logging', feedbackOptions(['none', 'usb'])));
for (const partial of [{feature: 'full'}, {ota: 'lora-source'}]) {
  const selection = {...feedbackFilters, ...partial};
  assert(picker.choicesUseSameFirmware(feedbackCatalog, selection, 'firmwareProfile',
    picker.firmwareProfileChoices(feedbackCatalog, selection)));
}
assert(!picker.choicesUseSameFirmware(feedbackCatalog, feedbackFilters, 'firmwareProfile',
  [{value: 'invalid'}]));
assert(!picker.choicesUseSameFirmware(feedbackCatalog, feedbackFilters, 'role', [{value: ''}]));
assert(!picker.choicesUseSameFirmware([], {}, 'role', feedbackOptions(['companion'])));
assert.strictEqual(JSON.stringify(feedbackCatalog), feedbackSnapshot);
assert.deepStrictEqual(feedbackFilters, {hardware: 'Heltec_v3', role: 'companion'});
console.log('build counts, non-refining choices, install safety and partial-link feedback passed');

// Exact board and role are deliberate intent. Runtime settings and a sole
// available install format must not keep an already identified download locked.
const simpleFilters = {...feedbackFilters, hardwareFamily: 'Heltec_v3'};
const simpleRequirements = picker.selectionRequirements(feedbackCatalog, simpleFilters);
assert.deepStrictEqual(simpleRequirements.hiddenFields, ['logging', 'firmwareProfile', 'mode']);
assert.deepStrictEqual(simpleRequirements.missingFields, ['install']);
for (const install of ['bin', 'merged-bin']) {
  const requirements = picker.selectionRequirements(feedbackCatalog, {...simpleFilters, install});
  assert.deepStrictEqual(requirements.missingFields, []);
  assert.strictEqual(requirements.installKind, install);
}
const soleInstallCatalog = [{...feedbackCatalog[0], installKinds: ['bin']}];
const soleInstall = picker.selectionRequirements(soleInstallCatalog, simpleFilters);
assert.deepStrictEqual(soleInstall.hiddenFields, ['logging', 'firmwareProfile', 'mode', 'install']);
assert.deepStrictEqual(soleInstall.missingFields, []);
assert.strictEqual(soleInstall.installKind, 'bin');
assert.deepStrictEqual(picker.resolveProfileAssets(soleInstallCatalog, soleInstall.installKind)
  .map(entry => entry.asset.name), [picker.canonicalAsset(soleInstallCatalog[0].files, 'bin').name]);
const soleUf2 = picker.selectionRequirements([{...nrf, installKinds: ['uf2']}], {
  hardwareFamily: nrf.hardwareFamily, hardware: nrf.hardware, role: nrf.role,
});
assert.strictEqual(soleUf2.installKind, 'uf2');
assert(soleUf2.hiddenFields.includes('install'));
assert(picker.installSteps(nrf, soleUf2.installKind).some(step => step.includes('UF2')));
for (const filters of [{}, {hardware: 'Heltec_v3'}, {role: 'companion'}]) {
  const requirements = picker.selectionRequirements(soleInstallCatalog, filters);
  assert.deepStrictEqual(requirements.hiddenFields, []);
  if (!filters.hardware) assert(requirements.missingFields.includes('hardware'));
  if (!filters.role) assert(requirements.missingFields.includes('role'));
}
assert(picker.selectionRequirements([], simpleFilters).missingFields.includes('install'));

// Unsupported choices in an old/manual URL must remain visible to clear.
// Inferring the sole BIN for an explicitly requested ZIP would conceal the
// invalid constraint while actual matching still returned no files.
const usbOnlyCatalog = [{...soleInstallCatalog[0],
  loggingModes: ['none', 'usb'], connectionModes: ['usb']}];
for (const [field, value] of [['install', 'zip'], ['logging', 'wifi'], ['mode', 'wifi'],
                             ['feature', 'missing'], ['ota', 'missing'], ['variant', 'missing']]) {
  const selection = {...simpleFilters, [field]: value};
  const before = JSON.stringify(selection);
  const requirements = picker.selectionRequirements(usbOnlyCatalog, selection);
  const control = picker.PROFILE_FIELDS.includes(field) ? 'firmwareProfile' : field;
  assert(!requirements.hiddenFields.includes(control), control + ' must stay clearable');
  assert.strictEqual(JSON.stringify(selection), before);
  assert.strictEqual(new URL(picker.selectionUrl('https://example.test/picker/', selection, false))
    .searchParams.get(field), value);
  if (field === 'install') assert.strictEqual(requirements.installKind, 'zip');
}

// A selected setting that currently narrows to one image must remain visible
// if another choice would select a different image.
const distinctRequirements = picker.selectionRequirements(feedbackAlternatives, {
  ...simpleFilters, logging: 'usb', mode: 'usb',
  feature: 'full', ota: 'lora-source', variant: 'default',
});
for (const field of ['logging', 'mode', 'firmwareProfile']) {
  assert(!distinctRequirements.hiddenFields.includes(field),
    field + ' must remain clearable even when other selected facets narrow to one image');
}
// Test each facet independently of other already narrowing choices.
assert(!picker.selectionRequirements(feedbackAlternatives, {...simpleFilters, logging: 'usb'})
  .hiddenFields.includes('logging'));
assert(!picker.selectionRequirements(feedbackAlternatives, {...simpleFilters, mode: 'usb'})
  .hiddenFields.includes('mode'));
assert(!picker.selectionRequirements(feedbackAlternatives, {...simpleFilters,
  feature: 'full', ota: 'lora-source', variant: 'default'})
  .hiddenFields.includes('firmwareProfile'));
assert.strictEqual(distinctRequirements.installKind, '');
const tloraRequirements = picker.selectionRequirements(tloraCatalog.profiles, {
  hardwareFamily: tloraNormalProfile.hardwareFamily,
  hardware: tloraNormalProfile.hardware, role: 'repeater',
});
assert(!tloraRequirements.hiddenFields.includes('logging'));
assert(!tloraRequirements.hiddenFields.includes('mode'));
// Both capacity-safe images share the Full/OTA tuple. Logging and transport
// still distinguish their different files; the identical tuple adds no choice.
assert(tloraRequirements.hiddenFields.includes('firmwareProfile'));
assert(tloraRequirements.missingFields.includes('logging'));
assert(tloraRequirements.missingFields.includes('mode'));
const selectedTlora = picker.selectionRequirements(tloraCatalog.profiles, {
  hardwareFamily: tloraNormalProfile.hardwareFamily,
  hardware: tloraNormalProfile.hardware, role: 'repeater',
  logging: 'wifi', mode: 'espnow', feature: 'full', ota: tloraObserverProfile.ota,
  variant: tloraObserverProfile.variant,
});
assert(!selectedTlora.hiddenFields.includes('logging'));
assert(!selectedTlora.hiddenFields.includes('mode'));
for (const item of sensorCatalog.profiles) {
  const requirements = picker.selectionRequirements(sensorCatalog.profiles, {
    hardwareFamily: item.hardwareFamily, hardware: item.hardware, role: item.role,
  });
  assert(!requirements.hiddenFields.includes('firmwareProfile'), item.target);
  assert(!requirements.hiddenFields.includes('install'), 'ZIP and UF2 stay distinct');
  const selected = picker.selectionRequirements(sensorCatalog.profiles, {
    hardwareFamily: item.hardwareFamily, hardware: item.hardware, role: item.role,
    logging: item.loggingModes[0], mode: item.mode, install: 'zip',
    ota: item.ota, feature: item.feature, variant: item.variant,
  });
  assert(!selected.hiddenFields.includes('firmwareProfile'),
    'Selected sensor profile must remain clearable: ' + item.target);
}
for (const partial of [{feature: 'full'}, {ota: 'lora-source'}]) {
  const selection = {...simpleFilters, ...partial};
  const before = JSON.stringify(selection);
  const requirements = picker.selectionRequirements(soleInstallCatalog, selection);
  assert.deepStrictEqual(requirements.missingFields, []);
  assert.strictEqual(JSON.stringify(selection), before);
  const link = picker.selectionUrl('https://example.test/picker/', selection, false);
  assert(!new URL(link).searchParams.has('install'));
  for (const field of picker.PROFILE_FIELDS) {
    if (!partial[field]) assert(!new URL(link).searchParams.has(field));
  }
}
assert.strictEqual(JSON.stringify(feedbackCatalog), feedbackSnapshot);
assert.deepStrictEqual(simpleFilters, {...feedbackFilters, hardwareFamily: 'Heltec_v3'});
console.log('same-image selectors skip redundant steps while distinct profiles and install routes stay explicit');

// Logging choices remain available beneath the download. Linked choices
// preselect their exact commands; an unselected logging mode shows off steps.
for (const logging of ['none', 'usb', 'wifi', 'both']) {
  const sections = picker.runtimePanelDirections(observer, {logging}, ['logging', 'mode']);
  const section = sections.find(item => item.title === 'Logging / MQTT output');
  assert.deepStrictEqual(section.actions.map(action => action.selection.logging),
    ['none', 'usb', 'wifi', 'both']);
  assert.strictEqual(section.actions[section.selectedIndex].selection.logging, logging);
  assert.deepStrictEqual(section.actions[section.selectedIndex].commands,
    picker.runtimeDirections(observer, {logging})[0].actions[0].commands);
}
const defaultLogging = picker.runtimePanelDirections(observer, {}, ['logging'])[0];
assert.strictEqual(defaultLogging.actions[defaultLogging.selectedIndex].selection.logging, 'none');
const nrfLogging = picker.runtimePanelDirections(nrf, {logging: 'usb'}, ['logging'])[0];
assert.deepStrictEqual(nrfLogging.actions[nrfLogging.selectedIndex].commands, ['set usb.logging on reboot']);
assert.strictEqual(picker.runtimePanelDirections(observer, {logging: 'usb'}, [])[0].actions.length, 1);
for (const mode of ['', 'standard', 'rs232', 'espnow']) {
  const profile = tripleCatalog.profiles[0];
  const sections = picker.runtimePanelDirections(profile, {mode}, ['mode']);
  const uart = sections.find(section => /^RS232 bridge/.test(section.title));
  const wireless = sections.find(section => section.title === 'ESP-NOW bridge');
  assert.strictEqual(uart.actions[uart.selectedIndex].label, mode === 'rs232' ? 'On' : 'Off');
  assert.strictEqual(wireless.actions[wireless.selectedIndex].label, mode === 'espnow' ? 'On' : 'Off');
}

// Exercise the actual command renderer, including the preselected radio and
// later logging change, without sending anything to a device or the network.
class RuntimeElement {
  constructor(tag, text = '') {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this.ownText = text;
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; this.ownText = ''; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  get textContent() { return this.ownText + this.children.map(child => child.textContent).join(''); }
  set textContent(text) { this.ownText = text; this.children = []; }
}
function descendants(element, predicate) {
  return element.children.flatMap(child =>
    (predicate(child) ? [child] : []).concat(descendants(child, predicate)));
}
const previousDocument = global.document;
global.document = {
  createElement: tag => new RuntimeElement(tag),
  createTextNode: text => new RuntimeElement('#text', text),
};
try {
  const card = new RuntimeElement('article');
  const changes = [];
  picker.renderRuntimeDirections(card, observer, {logging: 'wifi'}, {
    runtimeFields: ['logging'], onSelectionChange: selection => changes.push(selection),
  });
  const loggingDetails = descendants(card, node => node.tagName === 'DETAILS')
    .find(node => node.children[0].textContent === 'Logging / MQTT output');
  const inputs = descendants(loggingDetails, node => node.tagName === 'INPUT');
  assert.strictEqual(inputs.length, 4);
  assert.deepStrictEqual(inputs.map(input => !!input.checked), [false, false, true, false]);
  assert.strictEqual(descendants(loggingDetails, node => node.tagName === 'CODE')[0].textContent,
    'set logging.output wifi\nget logging.output');
  const choices = descendants(loggingDetails, node => node.className === 'firmware-picker-radio-options')[0];
  choices.listeners.change({target: inputs[1]});
  assert.deepStrictEqual(changes, [{logging: 'usb'}]);
  assert.strictEqual(descendants(loggingDetails, node => node.tagName === 'CODE')[0].textContent,
    'set powersaving off\nset logging.output usb\nget logging.output');
  const url = picker.selectionUrl('https://example.test/picker/', {...simpleFilters, ...changes[0]}, false);
  assert.strictEqual(new URL(url).searchParams.get('logging'), 'usb');
  const resolveLogging = logging => picker.resolveProfileAssets([observer].filter(profile =>
    picker.profileMatchesFacets(profile, {logging})), 'bin').map(entry => entry.asset.name);
  assert.deepStrictEqual(resolveLogging(changes[0].logging), resolveLogging('wifi'));
  assert.strictEqual(resolveLogging(changes[0].logging).length, 1);

  // When logging still distinguishes images, candidate command controls are
  // directions only. Their preview cannot silently change filters or URLs.
  const refiningCard = new RuntimeElement('article');
  const refiningChanges = [];
  picker.renderRuntimeDirections(refiningCard, observer, {}, {
    runtimeFields: [], onSelectionChange: selection => refiningChanges.push(selection),
  });
  const refiningDetails = descendants(refiningCard, node => node.tagName === 'DETAILS')
    .find(node => node.children[0].textContent === 'Restore the selected logging mode');
  const refiningInputs = descendants(refiningDetails, node => node.tagName === 'INPUT');
  const refiningChoices = descendants(refiningDetails,
    node => node.className === 'firmware-picker-radio-options')[0];
  refiningChoices.listeners.change({target: refiningInputs[2]});
  assert.strictEqual(descendants(refiningDetails, node => node.tagName === 'CODE')[0].textContent,
    'set logging.output wifi\nget logging.output');
  assert.deepStrictEqual(refiningChanges, []);
} finally {
  if (previousDocument === undefined) delete global.document;
  else global.document = previousDocument;
}
console.log('result logging and bridge controls preserve linked modes and show deliberate default commands');
