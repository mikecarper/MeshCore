(function (global) {
  "use strict";

  const RELEASE_PAGE_PREFIXES = Object.freeze([
    "",
    "repeater-room-",
    "utility-",
    "logging-",
    "lora-ota-",
    "full-profiles-",
  ]);

  const FILTER_FIELDS = Object.freeze([
    "chipFamily",
    "hardwareFamily",
    "hardware",
    "role",
    "logging",
    "ota",
    "mode",
    "feature",
    "variant",
  ]);

  const FACET_FIELDS = Object.freeze(FILTER_FIELDS.concat(["install"]));

  const CHIP_FAMILY_LABELS = Object.freeze({
    esp32: "ESP32",
    nrf52: "nRF52",
    rp2040: "RP2040",
    stm32: "STM32",
    unknown: "Other / unspecified",
  });

  const ROLE_LABELS = Object.freeze({
    companion: "Companion",
    repeater: "Repeater",
    room: "Room Server",
    sensor: "Sensor / telemetry",
    terminal: "Terminal Chat",
    kiss: "KISS modem",
    other: "Other",
  });

  const LOGGING_LABELS = Object.freeze({
    none: "None / normal operation",
    usb: "USB logging / USB-connected MQTT",
    wifi: "Wi-Fi MQTT",
    both: "USB logging + Wi-Fi MQTT",
    runtime: "Runtime selectable: off / USB / Wi-Fi / both",
    "usb-runtime": "Runtime selectable: off / USB",
  });

  const OTA_LABELS = Object.freeze({
    none: "No LoRa OTA",
    "lora-receiver": "LoRa OTA repeater",
    "lora-source": "LoRa OTA source only (Full Companion)",
  });

  const MODE_LABELS = Object.freeze({
    standard: "Standard / no separate bridge",
    full: "Full Companion transports",
    ble: "Bluetooth LE",
    usb: "USB",
    "usb-ble": "USB + Bluetooth LE",
    wifi: "Wi-Fi",
    serial: "Serial / UART",
    ethernet: "Ethernet",
    espnow: "ESP-NOW bridge",
    rs232: "RS-232 bridge",
  });

  const FEATURE_LABELS = Object.freeze({
    standard: "Standard profile",
    full: "FULL / complete profile",
  });

  const INSTALL_LABELS = Object.freeze({
    "merged-bin": "Full install / layout migration (merged .bin)",
    bin: "Update existing install (.bin)",
    zip: "nRF52 update / Serial DFU (.zip)",
    uf2: "UF2 install / update (.uf2)",
    hex: "Erase / recovery flash (.hex)",
  });

  const INSTALL_ORDER = Object.freeze([
    "merged-bin",
    "bin",
    "zip",
    "uf2",
    "hex",
  ]);

  const CANDIDATE_RESULT_LIMIT = 5;

  const HARDWARE_ALIASES = Object.freeze({
    // Generated Full Companion artifact name. It uses the standard Heltec V4
    // board definition and auto-detects the V4.2 and V4.3 FEM hardware.
    "heltec_v4_2_v4_3": "heltec_v4",
    // These Station target prefixes select a firmware/radio profile on the
    // same physical hardware.
    "Station_G2_logging": "Station_G2",
    "Station_G3_ESP32_logging": "Station_G3_ESP32",
  });

  const HIDDEN_PROFILE_HARDWARE = Object.freeze([
    // These legacy Station environments select a runtime radio-gain default
    // or only change ADVERT_NAME. Their ordinary targets provide the same
    // behavior, while all legacy files stay available through exact search.
    "Station_G2_logging",
    "Station_G3_ESP32_logging",
  ]);

  const HIDDEN_PROFILE_TARGETS = Object.freeze([
    // SolarXiao repeaters already stage full-sensor LoRa OTA images in their
    // matched external QSPI flash. Older releases contain redundant lean
    // siblings; keep those files searchable without recommending them.
    "solarxiao_30S_repeater_lora_ota_no_external_sensors",
    "solarxiao_33S_repeater_lora_ota_no_external_sensors",
    // Exact aliases of the unsuffixed Heltec V4 USB/BLE recipes.
    "heltec_v4_companion_radio_usb_femon",
    "heltec_v4_companion_radio_ble_femon",
    "ikoka_handheld_nrf_e22_30dbm_096_rotated_companion_radio_full",
  ]);

  let pickerInstanceCount = 0;

  function isSafeGithubUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" &&
        (url.hostname === "github.com" ||
          url.hostname === "objects.githubusercontent.com");
    } catch (_) {
      return false;
    }
  }

  function escapeRegExp(value) {
    return String(value).replace(/[.*+?^$()|[\]\\{}]/g, "\\$&");
  }

  function releaseDate(release) {
    return release.published_at || release.created_at || "";
  }

  function selectReleaseSet(releases) {
    const publicReleases = (Array.isArray(releases) ? releases : []).filter(
      function (release) {
        return release && !release.draft && typeof release.tag_name === "string";
      }
    );
    const mainReleases = publicReleases.filter(function (release) {
      return /^v\d/i.test(release.tag_name);
    }).sort(function (a, b) {
      return releaseDate(b).localeCompare(releaseDate(a));
    });
    const main = mainReleases[0];
    if (!main) return null;

    const familyTag = main.tag_name;
    const acceptedTags = new Set(RELEASE_PAGE_PREFIXES.map(function (prefix) {
      return prefix + familyTag;
    }));
    const familyReleases = publicReleases.filter(function (release) {
      return acceptedTags.has(release.tag_name);
    }).sort(function (a, b) {
      return releaseDate(b).localeCompare(releaseDate(a));
    });

    return {
      familyTag: familyTag,
      name: main.name || familyTag,
      url: isSafeGithubUrl(main.html_url) ? main.html_url : "",
      prerelease: Boolean(main.prerelease),
      releases: familyReleases,
    };
  }

  function flattenReleaseAssets(releaseSet) {
    const rows = [];
    if (!releaseSet) return rows;
    releaseSet.releases.forEach(function (release) {
      (Array.isArray(release.assets) ? release.assets : []).forEach(
        function (asset) {
          if (!asset || typeof asset.name !== "string") return;
          rows.push({
            name: asset.name,
            url: isSafeGithubUrl(asset.browser_download_url)
              ? asset.browser_download_url
              : "",
            size: Number(asset.size) || 0,
            releaseName: release.name || release.tag_name || "Release",
            releaseTag: release.tag_name || "",
            releaseUrl: isSafeGithubUrl(release.html_url)
              ? release.html_url
              : "",
            publishedAt: releaseDate(release),
          });
        }
      );
    });
    return rows;
  }

  function installKindForAsset(extension, merged) {
    if (extension === "bin") return merged ? "merged-bin" : "bin";
    if (extension === "zip") return "zip";
    if (extension === "uf2") return "uf2";
    if (extension === "hex") return "hex";
    return "";
  }

  function parseFirmwareAsset(row, familyTag) {
    if (!row || typeof row.name !== "string" || !familyTag) return null;
    const extensionMatch = row.name.match(/\.([^.]+)$/);
    if (!extensionMatch) return null;
    const extension = extensionMatch[1].toLowerCase();
    if (!["bin", "zip", "uf2", "hex"].includes(extension)) return null;

    let stem = row.name.slice(0, -extensionMatch[0].length);
    const merged = stem.endsWith("-merged");
    if (merged) stem = stem.slice(0, -"-merged".length);

    const versionPattern = new RegExp(
      "^(.*?)-(ota-)?" + escapeRegExp(familyTag) +
        "(?:-[0-9a-f]{7,8})?$",
      "i"
    );
    const versionMatch = stem.match(versionPattern);
    if (!versionMatch) return null;
    const kind = installKindForAsset(extension, merged);
    if (!kind) return null;

    return Object.assign({}, row, {
      target: versionMatch[1],
      otaPackaged: Boolean(versionMatch[2]),
      extension: extension,
      merged: merged,
      installKind: kind,
    });
  }

  function roleParts(target) {
    const definitions = [
      {
        role: "companion",
        pattern: /_(?:companion_radio_|companion_|comp_radio_)/i,
      },
      {
        role: "room",
        pattern: /_(?:room_server|room_svr)(?=$|[_-])/i,
      },
      {
        role: "repeater",
        pattern: /(?:_|-)(?:repeater|repeatr)(?=$|[_-])/i,
      },
      {
        role: "sensor",
        pattern: /_sensor(?=$|[_-])/i,
      },
      {
        role: "terminal",
        pattern: /_terminal_chat(?=$|[_-])/i,
      },
      {
        role: "kiss",
        pattern: /_kiss_modem(?=$|[_-])/i,
      },
    ];

    for (const definition of definitions) {
      const match = definition.pattern.exec(target);
      if (!match) continue;
      return {
        role: definition.role,
        hardware: target.slice(0, match.index),
        tail: target.slice(match.index + match[0].length),
      };
    }
    return { role: "other", hardware: target, tail: "" };
  }

  function modeForRole(role, tail) {
    const value = String(tail).toLowerCase().replace(/^[_-]+/, "");
    if (role === "companion") {
      if (/^full(?:$|[_-])/.test(value)) return "full";
      if (/^usb_ble(?:$|[_-])/.test(value)) return "usb-ble";
      if (/^ble(?:$|[_-])/.test(value)) return "ble";
      if (/^usb(?:$|[_-])/.test(value)) return "usb";
      if (/^wifi(?:$|[_-])/.test(value)) return "wifi";
      if (/^serial(?:$|[_-])/.test(value)) return "serial";
      if (/^ethernet(?:$|[_-])/.test(value)) return "ethernet";
      return "standard";
    }
    // MQTT is an output capability selected under Logging / MQTT. Keeping it
    // out of the connection facet avoids asking for the same choice twice.
    // ESP-NOW, RS-232, and Ethernet remain here because they select distinct
    // bridge firmware or hardware paths.
    if (value.includes("observer_mqtt")) return "standard";
    if (value.includes("bridge_espnow")) return "espnow";
    if (value.includes("bridge_rs232")) return "rs232";
    if (value.includes("ethernet")) return "ethernet";
    return "standard";
  }

  function variantForProfile(role, tail) {
    let value = String(tail);
    if (role === "companion") {
      value = value.replace(
        /^(?:full|wifi_mqtt|usb_ble|usb_wifi|ble|usb|wifi|serial|ethernet)(?=$|[_-])/i,
        " "
      );
      // Power saving and controllable FEM gain are persisted settings. Legacy
      // filenames retain these tokens for exact search, but they are not
      // distinct recommended firmware variants.
      value = value.replace(/(?:^|[_-])ps(?=$|[_-])/gi, " ");
      value = value.replace(/(?:^|[_-])fem(?:on|off)(?=$|[_-])/gi, " ");
    }
    value = value.replace(/-full-usb-wifi/gi, "");
    value = value.replace(/-logging/gi, "");
    value = value.replace(/(?:^|[_-])full(?=$|[_-])/gi, " ");
    value = value.replace(/observer_mqtt/gi, " ");
    value = value.replace(/bridge_espnow/gi, " ");
    value = value.replace(/bridge_rs232/gi, " ");
    value = value.replace(/lora_ota/gi, " ");
    value = value.replace(/(?:^|[_-])ethernet(?=$|[_-])/gi, " ");
    value = value.replace(/^[_\-\s]+|[_\-\s]+$/g, "");
    if (!value) return "default";
    return value.toLowerCase().replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "default";
  }

  function parseTargetProfile(target) {
    const parts = roleParts(target);
    const sourceHardware = parts.hardware || target;
    const lowerTarget = target.toLowerCase();
    const lowerTail = parts.tail.toLowerCase();
    const mode = modeForRole(parts.role, parts.tail);
    const unifiedLogging = lowerTarget.includes("-full-usb-wifi");
    const fullLoggingFallback = parts.role !== "companion" &&
      lowerTarget.includes("-full-logging");
    const logging = unifiedLogging
      ? "runtime"
      : fullLoggingFallback
        ? "usb-runtime"
        : lowerTarget.includes("observer_mqtt") ||
            (parts.role === "companion" &&
              /(?:^|[_-])wifi_mqtt(?=$|[_-])/.test(lowerTail))
          ? "wifi"
          : lowerTarget.includes("-logging")
            ? "usb"
            : "none";
    const loggingModes = unifiedLogging
      ? ["none", "usb", "wifi", "both"]
      : fullLoggingFallback
        ? ["none", "usb"]
        : [logging];
    const feature = lowerTail.includes("-full") ||
      (parts.role === "companion" && mode === "full")
      ? "full"
      : "standard";
    const explicitOta = lowerTarget.includes("lora_ota")
      ? "lora-receiver"
      : parts.role === "companion" && mode === "full"
        ? "lora-source"
        : "";
    let variant = variantForProfile(parts.role, parts.tail);
    if (sourceHardware === "Station_G2_logging") {
      variant = variant === "default"
        ? "rx-boosted"
        : variant + "-rx-boosted";
    }

    return {
      target: target,
      sourceHardware: sourceHardware,
      hardware: canonicalHardware(sourceHardware),
      role: parts.role,
      logging: logging,
      loggingModes: loggingModes,
      mode: mode,
      feature: feature,
      variant: variant,
      explicitOta: explicitOta,
    };
  }

  function canonicalHardware(hardware) {
    const value = String(hardware || "");
    return HARDWARE_ALIASES[value] || value;
  }

  function isHiddenLegacyProfile(profile) {
    const target = String(profile && profile.target || "");
    const lowerTarget = target.toLowerCase();
    return HIDDEN_PROFILE_HARDWARE.includes(profile.sourceHardware) ||
      HIDDEN_PROFILE_TARGETS.includes(target) ||
      (lowerTarget.includes("companion_radio_") &&
        /(?:^|[_-])(?:ps|femoff)(?=$|[_-])/.test(lowerTarget));
  }

  function isDualCdcFullCompanion(profile) {
    if (!profile || profile.role !== "companion" || profile.mode !== "full") {
      return false;
    }
    const installKinds = Array.isArray(profile.installKinds)
      ? profile.installKinds
      : [];
    // Native nRF52 releases normally publish both Serial DFU ZIP and UF2.
    // Treat a UF2-only Full profile as nRF52 too so an incomplete release page
    // does not mislabel it as an ESP32 single-TTY image. ESP32 Full profiles
    // always publish BIN assets.
    return installKinds.includes("zip") ||
      (installKinds.includes("uf2") &&
        !installKinds.includes("bin") &&
        !installKinds.includes("merged-bin"));
  }

  function isFullCompanion(profile) {
    return Boolean(profile && profile.role === "companion" &&
      profile.mode === "full");
  }

  function replacementKey(profile) {
    let hardware = String(profile && profile.hardware || "").toLowerCase();
    // The generated base V4 Full image auto-detects both V4.2 and V4.3 FEMs.
    // Older V4.3 transport artifacts therefore have the same physical
    // replacement even though their filename hardware prefix differs.
    if (hardware === "heltec_v4_3") hardware = "heltec_v4";
    return hardware + "\n" + String(profile && profile.variant || "default");
  }

  function omitTransportsReplacedByFull(profiles) {
    const fullKeys = new Set((profiles || []).filter(function (profile) {
      return isFullCompanion(profile);
    }).map(replacementKey));
    const combinedUsbBleKeys = new Set((profiles || []).filter(
      function (profile) {
        return profile.role === "companion" && profile.mode === "usb-ble";
      }
    ).map(replacementKey));
    const terminalReplacementKeys = new Set((profiles || []).filter(
      function (profile) {
        return profile.role === "companion" &&
          (profile.mode === "full" || profile.mode === "usb" ||
            profile.mode === "usb-ble");
      }
    ).map(replacementKey));
    const targets = new Set((profiles || []).map(function (profile) {
      return String(profile.target || "").toLowerCase();
    }));
    const mergedRs232Targets = {
      heltec_t096_repeater_bridge_rs232: "heltec_t096_repeater",
      heltec_t096_repeater_bridge_rs232_lora_ota_no_external_sensors:
        "heltec_t096_repeater_lora_ota_no_external_sensors",
      rak_4631_repeater_bridge_rs232_serial1_lora_ota_no_external_sensors:
        "rak_4631_repeater_lora_ota_no_external_sensors",
      rak_4631_repeater_bridge_rs232_serial2_lora_ota_no_external_sensors:
        "rak_4631_repeater_lora_ota_no_external_sensors",
      rak_4631_repeater_bridge_rs232_serial1: "rak_4631_repeater",
      rak_4631_repeater_bridge_rs232_serial2: "rak_4631_repeater",
      promicro_repeater_bridge_rs232_serial1: "promicro_repeater",
      heltec_t114_without_display_repeater_bridge_rs232:
        "heltec_t114_without_display_repeater",
      heltec_t114_repeater_bridge_rs232: "heltec_t114_repeater",
      rak_3112_repeater_bridge_rs232: "rak_3112_repeater",
      rak_11310_repeater_bridge_rs232: "rak_11310_repeater",
      waveshare_rp2040_lora_repeater_bridge_rs232:
        "waveshare_rp2040_lora_repeater",
      solarxiao_30s_repeater_bridge_rs232: "solarxiao_30s_repeater",
      solarxiao_33s_repeater_bridge_rs232: "solarxiao_33s_repeater",
      heltec_v3_repeater_bridge_rs232: "heltec_v3_repeater",
      heltec_wsl3_repeater_bridge_rs232: "heltec_wsl3_repeater",
      lilygo_tlora_v2_1_1_6_repeater_bridge_rs232:
        "lilygo_tlora_v2_1_1_6_repeater",
    };

    return (profiles || []).filter(function (profile) {
      const key = replacementKey(profile);
      const replacedAttachedTransport = profile.role === "companion" &&
        (profile.mode === "usb" || profile.mode === "ble" ||
          profile.mode === "wifi" || profile.mode === "serial" ||
          profile.mode === "ethernet" || profile.mode === "usb-ble");
      const replacedByUsbBle = profile.role === "companion" &&
        (profile.mode === "usb" || profile.mode === "ble") &&
        combinedUsbBleKeys.has(key);
      const replacedTerminal = profile.role === "terminal" &&
        terminalReplacementKeys.has(key);
      const rs232Replacement = mergedRs232Targets[
        String(profile.target || "").toLowerCase()
      ];
      const replacedRs232 = Boolean(rs232Replacement &&
        targets.has(rs232Replacement));
      return !(replacedAttachedTransport && fullKeys.has(key)) &&
        !replacedByUsbBle && !replacedTerminal && !replacedRs232;
    });
  }

  function applyFullCompanionCapabilities(profiles) {
    return (profiles || []).map(function (profile) {
      if (!isFullCompanion(profile)) return profile;

      // Every Full Companion controls diagnostics with usb.logging. Some
      // qualified boards also contain a direct MQTT bridge, but the Companion
      // command parser does not implement the observer-only logging.output
      // selector, so never advertise its off/USB/WiFi/both modes here.
      profile.logging = "usb-runtime";
      profile.loggingModes = ["none", "usb"];
      profile.dedicatedUsbLogging = false;
      if (isDualCdcFullCompanion(profile)) {
        profile.dedicatedUsbLogging = true;
      }
      return profile;
    });
  }

  function applyMergedRak4631RepeaterCapabilities(profiles) {
    return (profiles || []).map(function (profile) {
      const target = String(profile && profile.target || "").toLowerCase();
      if (
        target !== "rak_4631_repeater" &&
        target !== "rak_4631_repeater_lora_ota_no_external_sensors"
      ) {
        return profile;
      }

      // The canonical RAK4631 repeater contains both the ordinary interface
      // and the runtime-selectable RS232 bridge. Legacy Serial1/Serial2 target
      // rows remain exact OTA compatibility assets; do not attach their files
      // to this profile merely to expose the merged image's second mode.
      profile.mode = "standard";
      profile.connectionModes = ["standard", "rs232"];
      return profile;
    });
  }

  function applyMergedStandardUsbLoggingCapabilities(profiles) {
    return (profiles || []).map(function (profile) {
      const safeRole = [
        "companion", "repeater", "room", "sensor", "terminal",
      ].includes(profile.role);
      const unsafeCompanionMode = profile.role === "companion" &&
        (profile.mode === "ble" || profile.mode === "full");
      if (profile.logging !== "none" || profile.ota !== "none" ||
          !safeRole || unsafeCompanionMode) {
        return profile;
      }

      profile.logging = "usb-runtime";
      profile.loggingModes = ["none", "usb"];
      return profile;
    });
  }

  function hardwareFamilyFor(hardware, hardwareNames) {
    const value = String(hardware || "");
    const lowerValue = value.toLowerCase();
    const parents = (hardwareNames || []).filter(function (candidate) {
      const parent = String(candidate || "");
      if (!parent || parent === value) return false;
      const lowerParent = parent.toLowerCase();
      return lowerValue.startsWith(lowerParent + "_") ||
        lowerValue.startsWith(lowerParent + "-");
    }).sort(function (a, b) {
      return a.length - b.length || a.localeCompare(b);
    });
    return parents[0] || value;
  }

  function humanizeHardwareVariant(hardware, family) {
    const value = String(hardware || "");
    const parent = String(family || "");
    if (!parent || value === parent) {
      if (value === "heltec_v4") {
        return "Base / standard (V4.2 / V4.3 auto-detect)";
      }
      return "Base / standard";
    }

    const knownLabels = {
      "heltec_v4_3": "V4.3",
      "heltec_v4_3_expansionkit_tft": "V4.3 + expansion kit + TFT",
      "heltec_v4_3_tft": "V4.3 + TFT",
      "heltec_v4_expansionkit": "Expansion kit",
      "heltec_v4_expansionkit_tft": "Expansion kit + TFT",
      "heltec_v4_r8": "R8",
      "heltec_v4_r8_tft": "R8 + TFT",
      "heltec_v4_tft": "TFT",
      "wio-e5-mini": "Mini",
    };
    if (knownLabels[value]) return knownLabels[value];

    let suffix = value.slice(parent.length).replace(/^[_-]+/, "");
    const suffixLabels = {
      logging: "Logging hardware profile",
      without_display: "Without display",
      sdcard: "SD card",
      "096": "0.96-inch display",
      "096_rotated": "0.96-inch rotated display",
      Lite_non_shell: "Lite (no shell)",
    };
    if (suffixLabels[suffix]) return suffixLabels[suffix];
    suffix = humanizeHardware(suffix).replace(/\bTft\b/gi, "TFT")
      .replace(/\bWio\b/gi, "WIO");
    return suffix || humanizeHardware(value);
  }

  function canonicalAsset(files, installKind) {
    return (Array.isArray(files) ? files : []).filter(function (file) {
      return file.installKind === installKind;
    }).sort(function (a, b) {
      const byLength = a.name.length - b.name.length;
      return byLength || a.name.localeCompare(b.name);
    })[0] || null;
  }

  function resolveProfileAssets(profiles, installKind) {
    const resolved = [];
    (Array.isArray(profiles) ? profiles : []).forEach(function (profile) {
      const installKinds = installKind
        ? [installKind]
        : profile.installKinds || [];
      installKinds.forEach(function (kind) {
        const asset = canonicalAsset(profile.files, kind);
        if (asset) {
          resolved.push({
            profile: profile,
            asset: asset,
            installKind: kind,
          });
        }
      });
    });
    return resolved;
  }

  function shouldShowCandidateResults(profiles) {
    return Array.isArray(profiles) && profiles.length > 0 &&
      profiles.length <= CANDIDATE_RESULT_LIMIT;
  }

  function buildCatalog(releases, controlData) {
    const releaseSet = selectReleaseSet(releases);
    if (!releaseSet) {
      return { releaseSet: null, rows: [], profiles: [] };
    }
    const rows = flattenReleaseAssets(releaseSet);
    const firmwareRows = rows.map(function (row) {
      return parseFirmwareAsset(row, releaseSet.familyTag);
    }).filter(Boolean);
    const grouped = new Map();

    firmwareRows.forEach(function (file) {
      let profile = grouped.get(file.target);
      if (!profile) {
        profile = Object.assign(parseTargetProfile(file.target), {
          files: [],
          otaPackaged: false,
        });
        grouped.set(file.target, profile);
      }
      profile.files.push(file);
      profile.otaPackaged = profile.otaPackaged || file.otaPackaged;
    });

    const visibleProfiles = Array.from(grouped.values()).map(function (profile) {
      profile.ota = profile.explicitOta ||
        (profile.otaPackaged ? "lora-receiver" : "none");
      profile.installKinds = INSTALL_ORDER.filter(function (kind) {
        return Boolean(canonicalAsset(profile.files, kind));
      });
      profile.files.sort(function (a, b) {
        return a.name.localeCompare(b.name);
      });
      return profile;
    }).filter(function (profile) {
      return !isHiddenLegacyProfile(profile);
    });
    const profiles = applyMergedStandardUsbLoggingCapabilities(
      applyMergedRak4631RepeaterCapabilities(
        omitTransportsReplacedByFull(
          applyFullCompanionCapabilities(visibleProfiles)
        )
      )
    ).sort(function (a, b) {
      return a.target.localeCompare(b.target, undefined, {
        numeric: true,
        sensitivity: "base",
      });
    });

    const hardwareNames = Array.from(new Set(profiles.map(function (profile) {
      return profile.hardware;
    })));
    profiles.forEach(function (profile) {
      if (controlData && controlData.familyTag === releaseSet.familyTag &&
          controlData.profiles && controlData.profiles[profile.target]) {
        profile.controls = controlData.profiles[profile.target];
        if (isFullCompanion(profile) && profile.controls.mqtt) {
          profile.loggingModes = ["none", "usb", "wifi", "both"];
        }
        if (profile.controls.rs232 && profile.role === "repeater" && profile.mode === "standard") {
          profile.connectionModes = ["standard", "rs232"];
        }
      }
      profile.hardwareFamily = hardwareFamilyFor(
        profile.hardware,
        hardwareNames
      );
    });

    // Use release-bound platform metadata, never a filename extension (UF2
    // is used by both nRF52 and RP2040). Legacy images may inherit the chip
    // family, but no runtime capabilities, from the same exact hardware.
    profiles.forEach(function (profile) {
      const platform = profile.controls && profile.controls.platform;
      profile.chipFamily = platform
        ? platform.replace(/_PLATFORM$/, "").toLowerCase() : "";
    });
    const knownChips = new Map();
    profiles.forEach(function (profile) {
      if (!profile.chipFamily) return;
      if (!knownChips.has(profile.hardware)) knownChips.set(profile.hardware, new Set());
      knownChips.get(profile.hardware).add(profile.chipFamily);
    });
    profiles.forEach(function (profile) {
      const chips = knownChips.get(profile.hardware);
      if (!profile.chipFamily) {
        profile.chipFamily = chips && chips.size === 1 ? Array.from(chips)[0] : "unknown";
      }
    });

    return {
      releaseSet: releaseSet,
      rows: firmwareRows,
      profiles: profiles,
    };
  }

  function profileMatches(profile, filters, fields) {
    const wantedFields = fields || FILTER_FIELDS;
    return wantedFields.every(function (field) {
      const value = filters && filters[field];
      return !value || profileFieldValues(profile, field).includes(value);
    });
  }

  function profileFieldValues(profile, field) {
    if (field === "mode" && Array.isArray(profile.connectionModes)) {
      return profile.connectionModes;
    }
    if (field === "logging" && Array.isArray(profile.loggingModes)) {
      return profile.loggingModes;
    }
    return profile[field] ? [profile[field]] : [];
  }

  function profileMatchesFacets(profile, filters, ignoredField) {
    const ignoredFields = new Set(
      Array.isArray(ignoredField) ? ignoredField : [ignoredField]
    );
    return FACET_FIELDS.every(function (field) {
      if (ignoredFields.has(field)) return true;
      const value = filters && filters[field];
      if (!value) return true;
      if (field === "install") {
        return profile.installKinds.includes(value);
      }
      return profileFieldValues(profile, field).includes(value);
    });
  }

  function facetValues(profiles, filters, field, additionallyIgnoredFields) {
    const ignoredFields = [field].concat(additionallyIgnoredFields || []);
    const compatible = (profiles || []).filter(function (profile) {
      return profileMatchesFacets(profile, filters, ignoredFields);
    });
    const values = field === "install"
      ? compatible.flatMap(function (profile) {
        return profile.installKinds;
      })
      : compatible.flatMap(function (profile) {
        return profileFieldValues(profile, field);
      });
    return Array.from(new Set(values.filter(Boolean)));
  }

  function uniqueValues(profiles, field) {
    return Array.from(new Set((profiles || []).flatMap(function (profile) {
      return profileFieldValues(profile, field);
    }).filter(Boolean)));
  }

  function selectionUrl(url, filters, automaticChipFamily) {
    const result = new URL(url);
    FACET_FIELDS.forEach(function (field) {
      result.searchParams.delete(field);
      if (filters[field]) result.searchParams.set(field, filters[field]);
    });
    result.searchParams.delete("chipAuto");
    if (filters.hardwareFamily || filters.hardware) {
      result.searchParams.set("chipAuto", automaticChipFamily ? "1" : "0");
    }
    return result.href;
  }

  function selectionFromUrl(url, profiles) {
    const params = new URL(url).searchParams;
    const requested = {};
    const filters = {};
    const unavailable = [];
    FACET_FIELDS.forEach(function (field) {
      requested[field] = params.get(field) || "";
    });
    // Handwritten links may specify just the exact hardware variant.
    if (requested.hardware && !requested.hardwareFamily) {
      const profile = profiles.find(function (item) {
        return item.hardware === requested.hardware;
      });
      if (profile) requested.hardwareFamily = profile.hardwareFamily;
    }
    const automaticChipFamily = params.get("chipAuto") === "1" ||
      (params.get("chipAuto") !== "0" && !requested.chipFamily &&
        Boolean(requested.hardwareFamily || requested.hardware));
    if (automaticChipFamily) requested.chipFamily = "";
    // Validate in picker order so a stale variant cannot discard valid hardware.
    FACET_FIELDS.forEach(function (field) {
      const value = requested[field];
      if (!value) return;
      if ((field !== "hardware" || filters.hardwareFamily) &&
          facetValues(profiles, filters, field).includes(value)) {
        filters[field] = value;
      } else {
        unavailable.push(field + "=" + value);
      }
    });
    return { filters: filters, automaticChipFamily: automaticChipFamily, unavailable: unavailable };
  }

  function humanizeHardware(value) {
    return String(value || "").replace(/_/g, " ").replace(/\s+/g, " ").trim();
  }

  function humanizeVariant(value, hardware) {
    if (!value || value === "default") return "Default";
    const tokenLabels = {
      ps: "Power save",
      rx: "RX",
      femon: "FEM on",
      femoff: "FEM off",
      serial1: "Serial 1",
      serial2: "Serial 2",
      sim: "SIM",
      rak15001: "RAK15001",
      rak13302: value.includes("w25q16") ? "RAK13302 +" : "RAK13302",
      w25q16: "External storage board (W25Q16)",
      qspi: "QSPI",
      lora: "LoRa",
      ota: "OTA",
    };
    const tokens = value.split("-");
    const labels = [];
    for (let index = 0; index < tokens.length; index += 1) {
      if (
        tokens[index] === "no" &&
        tokens[index + 1] === "external" &&
        tokens[index + 2] === "sensors"
      ) {
        // The legacy target token is an OTA identity, not a literal statement
        // that the I2C bus and every external peripheral have been removed.
        labels.push(/^RAK_(3401|4631)$/i.test(hardware || "")
          ? "Internal storage (no external storage board)"
          : "Reduced optional environmental/ranging drivers");
        index += 2;
        continue;
      }
      const token = tokens[index];
      labels.push(
        tokenLabels[token] ||
        token.charAt(0).toUpperCase() + token.slice(1)
      );
    }
    return labels.join(" ");
  }

  function labelFor(field, value, hardware) {
    if (field === "chipFamily") return CHIP_FAMILY_LABELS[value] || value;
    if (field === "hardwareFamily") return humanizeHardware(value);
    if (field === "hardware") return humanizeHardware(value);
    if (field === "role") return ROLE_LABELS[value] || value;
    if (field === "logging") return LOGGING_LABELS[value] || value;
    if (field === "ota") return OTA_LABELS[value] || value;
    if (field === "mode") return MODE_LABELS[value] || value;
    if (field === "feature") return FEATURE_LABELS[value] || value;
    if (field === "variant") return humanizeVariant(value, hardware);
    if (field === "install") return INSTALL_LABELS[value] || value;
    return value;
  }

  function formatBytes(value) {
    const size = Number(value) || 0;
    if (size >= 1024 * 1024) {
      return (size / (1024 * 1024)).toFixed(2) + " MiB";
    }
    if (size >= 1024) return (size / 1024).toFixed(1) + " KiB";
    return size + " bytes";
  }

  function createElement(tag, text) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    return element;
  }

  function optionSort(field, a, b) {
    const roleOrder = ["companion", "repeater", "room", "sensor", "terminal", "kiss", "other"];
    const loggingOrder = ["none", "usb", "wifi", "both"];
    const otaOrder = ["none", "lora-receiver", "lora-source"];
    const featureOrder = ["standard", "full"];
    const modeOrder = ["standard", "full", "ble", "usb", "wifi", "serial", "ethernet", "espnow", "rs232"];
    const orders = {
      role: roleOrder,
      logging: loggingOrder,
      ota: otaOrder,
      feature: featureOrder,
      mode: modeOrder,
      install: INSTALL_ORDER,
    };
    const order = orders[field];
    if (order) {
      const left = order.indexOf(a);
      const right = order.indexOf(b);
      if (left !== right) return left - right;
    }
    return labelFor(field, a).localeCompare(labelFor(field, b), undefined, {
      numeric: true,
      sensitivity: "base",
    });
  }

  function installSteps(profile, kind) {
    const common = [
      "Verify that the hardware name and every displayed variant match the physical board.",
      "Back up configuration, keys, and radio settings before changing roles or profiles.",
    ];
    const byKind = {
      "merged-bin": [
        "Flash the merged image over a data-capable USB connection.",
        "Use this path for first installation, recovery, or a partition migration. Erase flash only when you intend to reset saved data.",
      ],
      bin: [
        "Use this app-only image for the exact board and role, with compatible installed application slots large enough for the image.",
        "Do not use an app-only image to migrate partition layouts.",
      ],
      zip: [
        "Use the ZIP as the native nRF52 Serial DFU package; it is not an extra archive.",
        "For a LoRa OTA repeater, install the exact-board OTAFIX bootloader first.",
      ],
      uf2: [
        "Place the device in its UF2 bootloader mode and copy the UF2 to the mounted drive.",
        "Confirm the role and radio settings after reboot.",
      ],
      hex: [
        "Flash the HEX with the board's supported wired programmer or recovery method.",
        "Confirm the bootloader and application versions after reboot.",
      ],
    };
    const extra = [];
    if (profile.variant.includes("w25q16")) {
      extra.push(
        "Requires the external W25Q16 storage board, its exact documented wiring, and the matching storage-aware OTAFIX bootloader."
      );
    }
    if (profile.variant.includes("no-external-sensors")) {
      if (/^RAK_(3401|4631)$/i.test(profile.hardware)) {
        extra.push("This profile uses internal storage for LoRa OTA; no external storage board is required.");
      }
      extra.push(
        "This compact profile omits selected optional environmental/ranging sensor drivers. Generic I2C and supported board peripherals remain available; see the hardware and variant notes for retained sensors."
      );
    }
    if (profile.ota === "lora-receiver") {
      extra.push(
        "This installs the LoRa OTA repeater profile. The later update package must still match the exact target and partition signature."
      );
    } else if (profile.ota === "lora-source") {
      extra.push(
        "Full Companion can serve a host-supplied update to another node; it does not self-install that LoRa update."
      );
    }
    if (profile.logging === "runtime") {
      extra.push(
        "Use get logging.output and set logging.output off|usb|wifi|both to choose the saved output mode. Avoid both when two consumers publish the same packets to one broker."
      );
      extra.push(
        "With no saved SSID, the setup AP stays available for 30 minutes after each boot, then Wi-Fi powers off automatically until reboot or an explicit start webconfig command. A configured Wi-Fi mode keeps reconnecting instead."
      );
    } else if (profile.logging === "usb-runtime") {
      if (!isFullCompanion(profile)) {
        extra.push(
          "This ordinary image includes USB logging. Use get usb.logging and set usb.logging off|on to select and save normal or logging operation."
        );
      } else if (profile.dedicatedUsbLogging) {
        extra.push(
          "Full Companion starts with USB logging off and only interface 00. Use get usb.logging, or set usb.logging on reboot to add interface 02; set usb.logging off reboot removes it again."
        );
        extra.push(
          "Interface 00 always carries Companion/terminal/mOTA traffic. After logging is enabled and the node reboots, interface 02 carries plaintext logs. Match services by USB interface number instead of assuming tty or COM numbering."
        );
      } else {
        extra.push(
          "Full Companion starts in its ASCII terminal with USB logging off. Use set usb.logging on for plaintext logs; set usb.logging off stops those logs but stays in normal ASCII mode. Then send +++MESHCORE-TERM-STOP or let a Companion app send a valid framed probe to switch the TTY to Binary Companion."
        );
      }
    }
    return common.concat(byKind[kind] || [], extra);
  }

  // These are instructions only: changing a picker choice never changes a node.
  function runtimeDirections(profile, selection) {
    const chosen = selection || {};
    const info = profile.controls;
    const full = isFullCompanion(profile);
    const companion = profile.role === "companion";
    const infrastructure = ["repeater", "room", "sensor"].includes(profile.role);
    const sections = [];
    function section(title, actions, note) {
      sections.push({ title: title, actions: actions, note: note || "" });
    }
    function toggle(title, command, read, note) {
      section(title, [
        { label: "On", commands: [command + " on"] },
        { label: "Off", commands: [command + " off"] },
        { label: "Check", commands: [read] },
      ], note);
    }
    if (profile.logging === "runtime" || profile.logging === "usb-runtime") {
      const modes = chosen.logging ? [chosen.logging] : profile.loggingModes;
      const actions = modes.filter(function (mode) {
        return profile.loggingModes.includes(mode);
      }).map(function (mode) {
        const enabled = mode === "usb" || mode === "both";
        const commands = profile.logging === "runtime"
          ? ["set logging.output " + (mode === "none" ? "off" : mode), "get logging.output"]
          : ["set usb.logging " + (enabled ? "on" : "off") +
              (profile.dedicatedUsbLogging ? " reboot" : "")];
        return {
          label: LOGGING_LABELS[mode], commands: commands,
          text: full && info && info.mqtt
            ? (mode === "wifi" || mode === "both"
              ? "In WebConfig, enable the desired MQTT broker presets/settings and save."
              : "In WebConfig, choose none for every MQTT broker slot and save to stop MQTT connections.")
            : "",
        };
      });
      section("Restore the selected logging mode", actions,
        full ? (profile.dedicatedUsbLogging
          ? "Reboot adds/removes the second logging port. Keep Companion/MOTA on primary interface 00."
          : "USB logs and binary Companion share one port. Turn logging off, send +++MESHCORE-TERM-STOP, and close the console before connecting the app or MOTA host.")
          : "Saved settings survive updates. These commands restore your selected output mode; downloading alone does not change it.");
    }
    if (!info) return sections;
    if (full || infrastructure) {
      toggle("Device power saving", full ? "powersaving" : "set powersaving", "powersaving",
        full ? "Controls device/GPS power saving separately from LoRa RXPS and WiFi modem sleep."
          : "The set form saves/applies without the bare powersaving on command's USB/bridge guards. Actual sleep depends on the board and can interrupt WiFi. Bare powersaving on rejects local/USB requests on nRF52 and standalone ESP32, and is unavailable on ESP32 bridge builds.");
      if (info.rxps && !info.primaryEspnow) toggle("LoRa RX power saving", "set radio.rxps", "get radio.rxps");
      if (info.rxgain && !info.primaryEspnow) toggle("Radio RX boost", "set radio.rxgain", "get radio.rxgain");
      if (info.femRx) toggle("External FEM receive gain", "set radio.fem.rxgain", "get radio.fem.rxgain", "Requires the controllable FEM on the installed board revision; check the reply for hardware support.");
      if (info.femTx) toggle("External FEM transmit gain", "set radio.fem.txgain", "get radio.fem.txgain", "Requires the controllable PA on the installed board revision.");
    }
    if (info.gps && (companion || infrastructure)) {
      if (companion) section("GPS", [
        { label: "On", text: "In the Companion app's custom sensor settings, set gps=1." },
        { label: "Off", text: "In the Companion app's custom sensor settings, set gps=0." },
      ], "These are app settings, not USB terminal commands. Connect the board's supported GPS hardware; sharing location is a separate setting.");
      else toggle("GPS", "gps", "gps", "Requires the supported GPS hardware to be installed.");
    }
    if (info.webconfig) {
      toggle("WiFi settings website (WebConfig)", "set webui", "get webui");
      if (infrastructure) toggle("WebConfig command terminal", "set wifi.cli", "get wifi.cli", "This is the node's WiFi browser terminal. The USB web console is independent and needs no WiFi.");
    }
    if (info.platform === "ESP32_PLATFORM" && (info.webconfig || info.mqtt)) {
      section("WiFi modem power saving", [
        { label: "On (minimum sleep)", commands: ["set wifi.powersave min"] },
        { label: "Off", commands: ["set wifi.powersave none"] },
        { label: "Check", commands: ["get wifi.powersave"] },
      ], "Separate from device sleep and LoRa RXPS. Bluetooth coexistence can constrain the effective mode.");
    }
    if (info.mqtt) {
      if (companion) section("MQTT broker connections", [
        { label: "On", commands: ["set webui on", "get webui"], text: "Open the reported WebConfig URL, select/configure a broker preset, and save. Repeat for each desired slot." },
        { label: "Off", text: "Open WebConfig, set every broker slot's preset to none, and save. Turning status publication off does not disconnect a broker." },
      ], "Companion configures MQTT through WebConfig; infrastructure MQTT text commands are not available in its terminal.");
      else {
        toggle("MQTT bridge", "set bridge.enabled", "get bridge.running", "Configure WiFi and broker slots first. get mqtt.status shows connections. This switch does not disable LoRa repeating.");
        ["status", "packets", "raw", "rx"].forEach(function (name) {
          toggle("MQTT " + (name === "rx" ? "receive capture" : name + " publication"), "set mqtt." + name, "get mqtt." + name,
            name === "status" ? "Only controls status publishing; connections remain enabled. The check command reports connection status." : "");
        });
        section("MQTT transmit capture", ["off", "advert", "on"].map(function (mode) {
          return { label: mode, commands: ["set mqtt.tx " + mode] };
        }));
      }
    }
    if (info.snmp && infrastructure) section("SNMP", [
      { label: "On", commands: ["set snmp on", "reboot"] },
      { label: "Off", commands: ["set snmp off", "reboot"] },
      { label: "Check", commands: ["get snmp"] },
    ]);
    if (profile.role === "repeater") toggle("Repeat mesh traffic", "set repeat", "get repeat");
    if (info.rs232 && infrastructure) {
      const mode = chosen.mode;
      section("RS232 bridge" + (mode === "rs232" ? " — selected" : ""), [
        { label: "On", commands: ["set bridge.enabled on", "get bridge.running"] },
        { label: "Off", commands: ["set bridge.enabled off"] },
        { label: "Set baud", commands: ["set bridge.enabled off", "set bridge.baud 115200", "set bridge.enabled on"] },
      ].sort(function (a, b) {
        return mode === "standard" ? Number(b.label === "Off") - Number(a.label === "Off") : 0;
      }), "Use the UART and pin map for this exact board. Canonical GPS-enabled RAK4631 uses UART2; select it with set bridge.uart 2 while the bridge is stopped. UART1 needs a compatible GPS-free image.");
    }
    if (info.espnowBridge && infrastructure) {
      toggle("ESP-NOW bridge", "set bridge.enabled", "get bridge.running");
      section("ESP-NOW bridge framing", ["wrapped", "raw"].map(function (mode) {
        return { label: mode, commands: ["set bridge.format " + mode] };
      }), "Set bridge.channel to the bridge channel. Primary ESP-NOW mesh uses set espnow.channel instead; see the board guide before changing channels.");
    }
    if (full && /sensecapindicator/i.test(profile.target)) section("Indicator wireless transport", [
      { label: "WiFi", commands: ["set companion.transport wifi", "reboot"] },
      { label: "Bluetooth", commands: ["set companion.transport ble", "reboot"] },
      { label: "Check", commands: ["get companion.transport"] },
    ], "USB stays available. This changes the secondary wireless transport, not the image's primary LoRa/ESP-NOW mesh.");
    if ((info.updateMethods || []).includes("wifi")) section("WiFi firmware uploader", [
      { label: "On (existing WiFi)", commands: ["start ota"] },
      { label: "On (setup access point)", commands: ["start ota ap"] },
      { label: "Off", commands: ["stop ota"] },
    ], full ? "Upload the exact application .bin to the returned URL on port 8080. Requires a compatible two-slot layout; never upload a merged image here."
      : "Stop WebConfig first if it is running. The uploader uses port 80; upload the exact application .bin, not a merged image.");
    if (infrastructure && (info.updateMethods || []).includes("bluetooth")) section("Bluetooth firmware update", [
      { label: "Start DFU", commands: ["start ota"], text: "Use the exact board's application DFU ZIP with the matching Bluetooth-capable bootloader." },
    ]);
    if (full && info.display) section("Display rotation", [0, 90, 180, 270].map(function (angle) {
      return { label: angle === 0 ? "Board default" : angle + " degrees", commands: ["set display.rotation " + angle] };
    }), "Use on supported displays; 0 restores the board default. Check with get display.rotation.");
    return sections;
  }

  function renderRuntimeDirections(card, profile, selection) {
    const panel = createElement("section");
    panel.className = "firmware-picker-runtime";
    panel.appendChild(createElement("h4", "Restore your settings after flashing"));
    panel.appendChild(createElement("p", "These directions change with the selected firmware and logging mode. Choose On, Off, or Check below to see the exact steps. Nothing is sent to your device by this page."));
    const links = createElement("p");
    const consoleLink = createElement("a", "Open USB web console");
    consoleLink.href = "https://flasher.meshcore.io/console";
    links.appendChild(consoleLink);
    links.appendChild(document.createTextNode(" · "));
    const guide = createElement("a", "Complete role commands and board exceptions");
    guide.href = "https://github.com/mikecarper/MeshCore/blob/keymindCascade/docs/role_feature_switches.md";
    links.appendChild(guide);
    panel.appendChild(links);
    if (isFullCompanion(profile) || ["repeater", "room", "sensor"].includes(profile.role)) {
      panel.appendChild(createElement("p", "Use a data-capable USB cable and 115200 baud. Full Companion and infrastructure start in ASCII mode. " + (profile.role === "companion"
        ? "If already in binary mode, send +++MESHCORE-TERM-START. Use board and version to identify the node."
        : "Use board and ver to identify the node.")));
    }
    if (!profile.controls) panel.appendChild(createElement("p", "Additional hardware controls have not been verified for this exact release image. Use the role guide for those settings."));
    runtimeDirections(profile, selection).forEach(function (item, index) {
      const details = createElement("details");
      details.open = index === 0;
      details.appendChild(createElement("summary", item.title));
      if (item.note) details.appendChild(createElement("p", item.note));
      const label = createElement("label", "Show steps for ");
      const select = createElement("select");
      select.setAttribute("aria-label", item.title);
      item.actions.forEach(function (action, i) {
        const option = createElement("option", action.label);
        option.value = String(i);
        select.appendChild(option);
      });
      label.appendChild(select);
      details.appendChild(label);
      const output = createElement("div");
      function show() {
        output.replaceChildren();
        const action = item.actions[Number(select.value) || 0];
        if (action.text) output.appendChild(createElement("p", action.text));
        if (action.commands) {
          const pre = createElement("pre");
          pre.appendChild(createElement("code", action.commands.join("\n")));
          output.appendChild(pre);
          const button = createElement("button", "Copy commands");
          button.type = "button";
          button.addEventListener("click", function () {
            if (!global.navigator || !global.navigator.clipboard) {
              button.textContent = "Select and copy the commands above";
              return;
            }
            global.navigator.clipboard.writeText(action.commands.join("\n")).then(function () {
              button.textContent = "Copied";
            }).catch(function () { button.textContent = "Select and copy the commands above"; });
          });
          output.appendChild(button);
        }
      }
      select.addEventListener("change", show);
      show();
      details.appendChild(output);
      panel.appendChild(details);
    });
    card.appendChild(panel);
  }

  function replaceFacts(container, facts) {
    container.replaceChildren();
    facts.forEach(function (fact) {
      container.appendChild(createElement("dt", fact[0]));
      container.appendChild(createElement("dd", fact[1]));
    });
  }

  function renderProfileCard(container, profile, asset, installKind, selection) {
    const card = createElement("article");
    card.className = "firmware-picker-card";
    card.appendChild(createElement(
      "h3",
      humanizeHardware(profile.hardware) + " — " +
        (ROLE_LABELS[profile.role] || profile.role)
    ));

    const facts = createElement("dl");
    facts.className = "firmware-picker-facts";
    const factRows = [
      ["Target", profile.target],
      [
        "Connection / mode",
        profileFieldValues(profile, "mode").map(function (mode) {
          return labelFor("mode", mode);
        }).join(" / "),
      ],
      ["Logging", labelFor("logging", selection && selection.logging || profile.logging)],
      ["OTA", labelFor("ota", profile.ota)],
      ["Feature profile", labelFor("feature", profile.feature)],
      ["Variant", labelFor("variant", profile.variant, profile.hardware)],
      ["Install operation", labelFor("install", installKind)],
      ["File", asset.name],
      ["Size", formatBytes(asset.size)],
      ["Release", asset.releaseName],
    ];
    if (profile.dedicatedUsbLogging) {
      factRows.splice(3, 0, [
        "USB port split",
        "Interface 00 always; interface 02 after logging on + reboot",
      ]);
    }
    replaceFacts(facts, factRows);
    card.appendChild(facts);

    const actions = createElement("div");
    actions.className = "firmware-picker-actions";
    const download = createElement("a", "Download exact firmware");
    download.className = "firmware-picker-primary";
    download.href = asset.url || asset.releaseUrl;
    actions.appendChild(download);
    const release = createElement("a", "Open release notes");
    release.href = asset.releaseUrl;
    actions.appendChild(release);
    card.appendChild(actions);

    const steps = createElement("div");
    steps.className = "firmware-picker-steps";
    steps.appendChild(createElement("strong", "What to do"));
    const list = createElement("ol");
    installSteps(profile, installKind).forEach(function (text) {
      list.appendChild(createElement("li", text));
    });
    steps.appendChild(list);
    card.appendChild(steps);
    renderRuntimeDirections(card, profile, selection);
    container.appendChild(card);
  }

  function initPicker(root) {
    const repo = root.getAttribute("data-release-repo") || "mikecarper/MeshCore";
    const form = root.querySelector('[data-role="form"]');
    const status = root.querySelector('[data-role="status"]');
    const releaseSetStatus = root.querySelector('[data-role="release-set"]');
    const result = root.querySelector('[data-role="result"]');
    const resultEyebrow = root.querySelector('[data-role="result-eyebrow"]');
    const resultTitle = root.querySelector('[data-role="result-title"]');
    const resultNote = root.querySelector('[data-role="result-note"]');
    const resultList = root.querySelector('[data-role="result-list"]');
    const missing = root.querySelector('[data-role="missing"]');
    const search = root.querySelector('[data-field="asset-search"]');
    const assetResults = root.querySelector('[data-role="asset-results"]');
    const clearButton = form.querySelector('[data-action="clear"]');
    const shareLink = root.querySelector('[data-role="share-link"]');
    const copyLinkButton = root.querySelector('[data-action="copy-link"]');
    const linkStatus = root.querySelector('[data-role="link-status"]');
    const controls = {};
    FACET_FIELDS.forEach(function (field) {
      controls[field] = form.querySelector('[data-field="' + field + '"]');
    });
    let catalog = { releaseSet: null, rows: [], profiles: [] };
    const filters = {};
    let automaticChipFamily = false;
    const groupPrefix = "firmware-picker-" + (++pickerInstanceCount);

    function updateSelectionUrl() {
      const url = selectionUrl(global.location.href, filters, automaticChipFamily);
      if (url !== global.location.href) {
        try {
          global.history.replaceState(global.history.state, "", url);
        } catch (error) {
          // Some local-file viewers block History API writes. The share link
          // still carries the complete selection on the public web picker.
        }
      }
      if (shareLink) {
        const base = global.location.protocol === "file:"
          ? root.getAttribute("data-share-url") : url;
        shareLink.href = selectionUrl(base || url, filters, automaticChipFamily);
        shareLink.hidden = false;
      }
      if (copyLinkButton) copyLinkButton.disabled = false;
    }

    function restoreSelectionUrl() {
      if (!catalog.profiles.length) return;
      const restored = selectionFromUrl(global.location.href, catalog.profiles);
      FACET_FIELDS.forEach(function (field) {
        filters[field] = restored.filters[field] || "";
      });
      automaticChipFamily = restored.automaticChipFamily;
      if (linkStatus) linkStatus.textContent = restored.unavailable.length
        ? "Some linked choices are unavailable or incompatible in the current release: " +
          restored.unavailable.join(", ") + ". Review the remaining choices before downloading."
        : "";
      refreshFacets();
    }

    if (copyLinkButton && shareLink) copyLinkButton.addEventListener("click", function () {
      const copy = global.navigator.clipboard && global.navigator.clipboard.writeText;
      if (!copy) {
        linkStatus.textContent = "Copy the address bar, or copy the ‘Link to these settings’ link.";
        return;
      }
      global.navigator.clipboard.writeText(shareLink.href).then(function () {
        linkStatus.textContent = "Link copied.";
      }).catch(function () {
        linkStatus.textContent = "Copy the address bar, or copy the ‘Link to these settings’ link.";
      });
    });
    global.addEventListener("popstate", restoreSelectionUrl);

    function matchingProfiles(ignoredField) {
      return catalog.profiles.filter(function (profile) {
        return profileMatchesFacets(profile, filters, ignoredField);
      });
    }

    function setSelectOptions(select, field, values, selected) {
      select.replaceChildren();
      const placeholders = {
        chipFamily: "Any chip family — skip this filter",
        hardwareFamily: "Any hardware",
        hardware: "Choose a hardware variant",
        mode: "Any connection / mode",
        variant: "Any hardware/profile variant",
      };
      const blank = createElement("option", placeholders[field] || "Any");
      blank.value = "";
      select.appendChild(blank);
      values.slice().sort(function (a, b) {
        return optionSort(field, a, b);
      }).forEach(function (value) {
        const label = field === "hardware"
          ? humanizeHardwareVariant(value, filters.hardwareFamily)
          : labelFor(field, value, filters.hardware || filters.hardwareFamily);
        const option = createElement("option", label);
        option.value = value;
        select.appendChild(option);
      });
      select.disabled = values.length === 0;
      select.value = selected && values.includes(selected) ? selected : "";
    }

    function setRadioOptions(container, field, values, selected) {
      container.replaceChildren();
      const options = [{ value: "", label: "Any" }].concat(
        values.slice().sort(function (a, b) {
          return optionSort(field, a, b);
        }).map(function (value) {
          return { value: value, label: labelFor(field, value) };
        })
      );
      options.forEach(function (option) {
        const label = createElement("label");
        label.className = "firmware-picker-radio-option";
        const input = createElement("input");
        input.type = "radio";
        input.name = groupPrefix + "-" + field;
        input.value = option.value;
        input.dataset.choiceField = field;
        input.checked = selected === option.value;
        label.appendChild(input);
        label.appendChild(createElement("span", option.label));
        container.appendChild(label);
      });
      const fieldset = container.closest("fieldset");
      if (fieldset) fieldset.disabled = values.length === 0;
    }

    function setControlOptions(field, values) {
      const control = controls[field];
      if (!control) return;
      if (control.tagName === "SELECT") {
        setSelectOptions(control, field, values, filters[field] || "");
      } else {
        setRadioOptions(control, field, values, filters[field] || "");
      }
    }

    function valuesForField(field) {
      const ignored = field === "chipFamily" ? ["hardwareFamily", "hardware"]
        : field === "hardwareFamily" ? ["hardware"] : [];
      if (automaticChipFamily && (field === "hardwareFamily" || field === "hardware")) {
        ignored.push("chipFamily");
      }
      return facetValues(
        catalog.profiles,
        filters,
        field,
        ignored
      );
    }

    function stabilizeFilters() {
      let changed = true;
      let passes = 0;
      while (changed && passes <= FACET_FIELDS.length) {
        changed = false;
        passes += 1;
        FACET_FIELDS.forEach(function (field) {
          if (field === "hardware" && !filters.hardwareFamily) {
            if (filters.hardware) {
              filters.hardware = "";
              changed = true;
            }
            return;
          }
          const values = valuesForField(field);
          if (filters[field] && !values.includes(filters[field])) {
            filters[field] = "";
            changed = true;
          } else if (field === "hardware" && filters.hardwareFamily &&
            !filters[field] && values.length === 1) {
            filters[field] = values[0];
            changed = true;
          }
        });
      }
    }

    function refreshFacets() {
      if (automaticChipFamily) filters.chipFamily = "";
      stabilizeFilters();
      if (automaticChipFamily && filters.hardwareFamily) {
        const chips = uniqueValues(matchingProfiles("chipFamily"), "chipFamily");
        filters.chipFamily = chips.length === 1 ? chips[0] : "";
      }
      FACET_FIELDS.forEach(function (field) {
        setControlOptions(field, valuesForField(field));
      });
      const hardwareVariants = valuesForField("hardware");
      const hardwareVariantControl = root.querySelector(
        '[data-role="hardware-variant-control"]'
      );
      hardwareVariantControl.hidden = !filters.hardwareFamily ||
        hardwareVariants.length <= 1;
      const chipSummary = root.querySelector('[data-role="chip-family-summary"]');
      if (chipSummary) chipSummary.textContent = "Optional: chip family" +
        (filters.chipFamily ? " — " + labelFor("chipFamily", filters.chipFamily) : "");
      render();
      updateSelectionUrl();
    }

    function render() {
      result.hidden = true;
      missing.hidden = true;
      resultList.replaceChildren();
      const missingFields = FACET_FIELDS.filter(function (field) {
        return field !== "chipFamily" && !filters[field];
      });
      const matches = matchingProfiles();

      if (missingFields.length) {
        if (missingFields.length === FACET_FIELDS.length - 1 && !filters.chipFamily) {
          status.textContent = "Pick options in any order. " +
            matches.length + " compatible configurations are available.";
        } else {
          status.textContent = matches.length + " compatible configuration" +
            (matches.length === 1 ? " remains. " : "s remain. ") +
            "Choose " + missingFields.length + " more option" +
            (missingFields.length === 1 ? "" : "s") + " in any order.";
        }
        if (shouldShowCandidateResults(matches)) {
          const candidates = resolveProfileAssets(matches, filters.install);
          if (candidates.length) {
            status.textContent += " Possible files are shown below.";
            resultEyebrow.textContent = "Narrowed firmware candidates";
            resultTitle.textContent = "Possible firmware files";
            resultNote.textContent =
              "Confirm the remaining choices and the exact hardware before downloading.";
            resultNote.hidden = false;
            candidates.forEach(function (entry) {
              renderProfileCard(
                resultList,
                entry.profile,
                entry.asset,
                entry.installKind,
          filters
              );
            });
            result.hidden = false;
          }
        }
        return;
      }

      const installKind = filters.install;
      const resolved = resolveProfileAssets(matches, installKind);
      if (!resolved.length) {
        missing.hidden = false;
        root.querySelector('[data-role="missing-text"]').textContent =
          "No exact file provides that installation method. Change the install method or one of the firmware options.";
        status.textContent = "No exact firmware file matched.";
        return;
      }

      status.textContent = resolved.length + " exact firmware configuration" +
        (resolved.length === 1 ? " matched." : "s matched.");
      resultEyebrow.textContent = "Exact firmware match";
      resultTitle.textContent = "Recommended download";
      resultNote.textContent = "";
      resultNote.hidden = true;
      resolved.forEach(function (entry) {
        renderProfileCard(
          resultList,
          entry.profile,
          entry.asset,
          entry.installKind,
          filters
        );
      });
      result.hidden = false;
    }

    function renderSearch() {
      assetResults.replaceChildren();
      const query = search.value.trim().toLowerCase();
      if (query.length < 2) return;
      const matches = catalog.rows.filter(function (row) {
        return row.name.toLowerCase().includes(query);
      }).sort(function (a, b) {
        const byLength = a.name.length - b.name.length;
        return byLength || a.name.localeCompare(b.name);
      }).slice(0, 80);
      if (!matches.length) {
        assetResults.appendChild(
          createElement("p", "No current release filename matched.")
        );
        return;
      }
      const list = createElement("ul");
      list.className = "firmware-asset-list";
      matches.forEach(function (row) {
        const item = createElement("li");
        const link = createElement("a", row.name);
        link.href = row.url || row.releaseUrl;
        item.appendChild(link);
        const meta = createElement(
          "span",
          row.releaseName + " — " + formatBytes(row.size)
        );
        meta.className = "firmware-asset-meta";
        item.appendChild(meta);
        list.appendChild(item);
      });
      assetResults.appendChild(list);
    }

    form.addEventListener("change", function (event) {
      const target = event.target;
      const field = target.dataset.choiceField || target.dataset.field;
      if (!FACET_FIELDS.includes(field)) return;
      if (linkStatus) linkStatus.textContent = "";
      if (field === "chipFamily") {
        automaticChipFamily = false;
        // A deliberate family switch takes precedence over an old board.
        if (target.value && !matchingProfiles("chipFamily").some(function (profile) {
          return profile.chipFamily === target.value;
        })) {
          filters.hardwareFamily = "";
          filters.hardware = "";
        }
      }
      if (field === "hardwareFamily" || field === "hardware") {
        automaticChipFamily = true;
      }
      if (field === "hardwareFamily") filters.hardware = "";
      filters[field] = target.value;
      refreshFacets();
    });
    form.addEventListener("reset", function (event) {
      event.preventDefault();
      if (!catalog.profiles.length) return;
      automaticChipFamily = false;
      if (linkStatus) linkStatus.textContent = "";
      FACET_FIELDS.forEach(function (field) {
        filters[field] = "";
      });
      refreshFacets();
    });
    search.addEventListener("input", renderSearch);

    const endpoint = "https://api.github.com/repos/" +
      encodeURIComponent(repo.split("/")[0]) + "/" +
      encodeURIComponent(repo.split("/")[1]) +
      "/releases?per_page=20";
    const embeddedElement = document.getElementById("firmware-picker-data");
    const embedded = embeddedElement ? JSON.parse(embeddedElement.textContent) : null;
    const controlUrl = root.getAttribute("data-controls-url");
    const controlRequest = embedded ? Promise.resolve(embedded.controls) : controlUrl ? fetch(controlUrl).then(function (response) {
      return response.ok ? response.json() : null;
    }).catch(function () { return null; }) : Promise.resolve(null);
    const releaseRequest = embedded ? Promise.resolve(embedded.releases) :
      fetch(endpoint, { headers: { Accept: "application/vnd.github+json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("GitHub returned HTTP " + response.status);
        }
        return response.json();
      });
    releaseRequest.then(function (data) {
        return controlRequest.then(function (controls) { return { releases: data, controls: controls }; });
      })
      .then(function (data) {
        catalog = buildCatalog(data.releases, data.controls);
        if (!catalog.releaseSet || !catalog.profiles.length) {
          throw new Error("no complete release family was found");
        }
        const set = catalog.releaseSet;
        releaseSetStatus.replaceChildren();
        releaseSetStatus.appendChild(
          createElement(
            "strong",
            set.name + (set.prerelease ? " — prerelease" : "")
          )
        );
        releaseSetStatus.appendChild(document.createTextNode(
          " · " + catalog.profiles.length + " configurations · " +
          catalog.rows.length + " firmware files · " +
          set.releases.length + " release pages"
        ));
        if (set.url) {
          releaseSetStatus.appendChild(document.createTextNode(" · "));
          const link = createElement("a", "open primary release");
          link.href = set.url;
          releaseSetStatus.appendChild(link);
        }
        clearButton.disabled = false;
        restoreSelectionUrl();
      })
      .catch(function (error) {
        status.textContent =
          "Could not load the GitHub release catalog: " + error.message;
        missing.hidden = false;
        root.querySelector('[data-role="missing-text"]').textContent =
          "Open the release collection and search for the exact hardware name.";
      });
  }

  const api = Object.freeze({
    FILTER_FIELDS: FILTER_FIELDS,
    FACET_FIELDS: FACET_FIELDS,
    ROLE_LABELS: ROLE_LABELS,
    LOGGING_LABELS: LOGGING_LABELS,
    OTA_LABELS: OTA_LABELS,
    MODE_LABELS: MODE_LABELS,
    FEATURE_LABELS: FEATURE_LABELS,
    INSTALL_LABELS: INSTALL_LABELS,
    CANDIDATE_RESULT_LIMIT: CANDIDATE_RESULT_LIMIT,
    selectReleaseSet: selectReleaseSet,
    flattenReleaseAssets: flattenReleaseAssets,
    parseFirmwareAsset: parseFirmwareAsset,
    parseTargetProfile: parseTargetProfile,
    applyFullCompanionCapabilities: applyFullCompanionCapabilities,
    applyMergedRak4631RepeaterCapabilities:
      applyMergedRak4631RepeaterCapabilities,
    applyMergedStandardUsbLoggingCapabilities:
      applyMergedStandardUsbLoggingCapabilities,
    applyDualCdcFullCompanionCapabilities: applyFullCompanionCapabilities,
    applyNrf52FullCompanionCapabilities:
      applyFullCompanionCapabilities,
    canonicalHardware: canonicalHardware,
    omitTransportsReplacedByFull: omitTransportsReplacedByFull,
    omitTransportsReplacedByDualCdcFull:
      omitTransportsReplacedByFull,
    omitNrf52TransportsReplacedByFull:
      omitTransportsReplacedByFull,
    hardwareFamilyFor: hardwareFamilyFor,
    humanizeHardwareVariant: humanizeHardwareVariant,
    buildCatalog: buildCatalog,
    profileMatches: profileMatches,
    profileFieldValues: profileFieldValues,
    profileMatchesFacets: profileMatchesFacets,
    facetValues: facetValues,
    uniqueValues: uniqueValues,
    selectionUrl: selectionUrl,
    selectionFromUrl: selectionFromUrl,
    canonicalAsset: canonicalAsset,
    resolveProfileAssets: resolveProfileAssets,
    shouldShowCandidateResults: shouldShowCandidateResults,
    runtimeDirections: runtimeDirections,
    renderRuntimeDirections: renderRuntimeDirections,
    installSteps: installSteps,
    humanizeHardware: humanizeHardware,
    humanizeVariant: humanizeVariant,
    labelFor: labelFor,
    formatBytes: formatBytes,
  });

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.MeshCoreFirmwarePicker = api;

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      document.querySelectorAll("[data-firmware-picker]").forEach(initPicker);
    });
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
