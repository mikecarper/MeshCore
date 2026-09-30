"use strict";

const assert = require("assert");
const picker = require("../docs/_javascript/firmware_picker.js");

const bootloaderManifest = require("../docs/_data/bootloader_manifest.json");
const bootloaderCatalog = picker.buildBootloaderCatalog(bootloaderManifest);
assert.strictEqual(bootloaderCatalog.profiles.length, 24);
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
assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, "Heltec_tower_v2", "nrf52").storage, "internal");
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
assert.strictEqual(mappedNrf52, 39);
for (const hardware of nrf52Hardware.filter(h => /^(ikoka_|solarxiao_)/.test(h))) {
  assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, hardware, "nrf52").id, "xiao_nrf52840_ble");
}
for (const hardware of ["WioTrackerL1-1W", "WioTrackerL1Eink"]) {
  assert.strictEqual(picker.bootloaderForHardware(bootloaderCatalog, hardware, "nrf52").id, "wio_tracker_l1");
}
assert(picker.unavailableBootloaderForHardware(bootloaderCatalog, "GAT562_Mesh_Watch13", "nrf52").reason.includes("vibration motor"));
assert(picker.unavailableBootloaderForHardware(bootloaderCatalog, "wio_wm1110", "nrf52").reason.includes("does not make it XIAO"));
const nextBootloaderTag = "v0.11.0-OTAFIX2.4.11";
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
assert.strictEqual(nextBootloaderCatalog.version, "2.4.11");
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
const expandedEsp32 = currentCatalog.profiles.find(profile =>
  profile.target === 'Ebyte_EoRa-S3_Repeater-full-logging');
assert(expandedEsp32);
assert(picker.installSteps(expandedEsp32, 'bin').some(step =>
  step.includes('exact board/role migration ZIP')));
assert(picker.installSteps(expandedEsp32, 'merged-bin').some(step =>
  step.includes('get storage.layout')));
assert.strictEqual(
  picker.migrationReleaseUrl(expandedEsp32, expandedEsp32.files[0]),
  'https://github.com/mikecarper/MeshCore/releases/tag/utility-' +
    currentControls.familyTag);
const ordinaryEsp32 = currentCatalog.profiles.find(profile =>
  profile.target === 'Ebyte_EoRa-S3_Repeater');
assert(ordinaryEsp32);
assert.strictEqual(
  picker.migrationReleaseUrl(ordinaryEsp32, ordinaryEsp32.files[0]), '');
const ikokaNormal = currentCatalog.profiles.find(profile =>
  profile.target === 'ikoka_stick_nrf_30dbm_repeater');
assert(!currentCatalog.profiles.some(profile =>
  profile.target === 'ikoka_stick_nrf_30dbm_repeater_lora_ota_no_external_sensors'));
assert.strictEqual(ikokaNormal.ota, 'lora-receiver');
assert.strictEqual(ikokaNormal.logging, 'usb-runtime');
assert.deepStrictEqual(ikokaNormal.loggingModes, ['none', 'usb']);
assert.deepStrictEqual(picker.runtimeDirections(ikokaNormal, {logging: 'usb'})[0].actions[0].commands,
  ['set usb.logging on']);
const ikokaUrl = 'https://example.com/firmware_picker/?chipFamily=nrf52&hardwareFamily=ikoka_stick_nrf_30dbm&hardware=ikoka_stick_nrf_30dbm&role=repeater&variant=default&install=zip&chipAuto=1';
const ikokaRelease = release(currentControls.familyTag, '2026-09-13T00:00:00Z',
  [ikokaNormal].map(profile => {
    const source = currentControls.profiles[profile.target].loggingSource;
    const tag = currentControls.familyTag.replace(/-[0-9a-f]{8}$/, '-' + source.slice(0, 8));
    return asset(profile.target + '-ota-' + tag + '.zip');
  }));
const ikokaCatalog = picker.buildCatalog([ikokaRelease], currentControls);
const ikokaSelection = picker.selectionFromUrl(ikokaUrl, ikokaCatalog.profiles);
assert.deepStrictEqual(ikokaSelection.unavailable, []);
assert.deepStrictEqual(ikokaCatalog.profiles.filter(profile =>
  picker.profileMatchesFacets(profile, ikokaSelection.filters)).map(profile => profile.target),
  ['ikoka_stick_nrf_30dbm_repeater']);
const mismatchedIkoka = picker.buildCatalog([
  release(currentControls.familyTag, '2026-09-13T00:00:00Z', [
    asset('ikoka_stick_nrf_30dbm_repeater-ota-' +
      currentControls.familyTag.replace(/-[0-9a-f]{8}$/, '-306feebe') + '.zip'),
  ]),
], currentControls).profiles[0];
assert.strictEqual(mismatchedIkoka.logging, 'none', 'Logging metadata must match the published build source');
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
assert.strictEqual(commands(combinedDirections).filter(command => command === 'set bridge.enabled on').length, 1);
console.log('current release metadata and capacity directions tests passed');

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
assert.strictEqual(picker.migrationLink(ordinaryEsp32, ordinaryEsp32.files[0]), null);
assert.strictEqual(picker.migrationLink(ikokaNormal, ikokaNormal.files[0]), null);
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
