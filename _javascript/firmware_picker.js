(function (global) {
  "use strict";

  const PURPOSES = Object.freeze({
    "usb-logging": Object.freeze({
      title: "Station G2 FULL USB logging repeater",
      summary: "Best for a G2 that normally remains connected to a computer collecting serial packet logs.",
      pattern: /^Station_G2_repeater-full-logging-ota-/,
      expected: "Station_G2_repeater-full-logging-ota-...",
      full: true,
      notes: [
        "Back up the node configuration before the first FULL install.",
        "Use the merged image once to install the expanded partition table.",
        "Reconnect at 115200 baud and leave the USB data connection attached for logging.",
      ],
    }),
    field: Object.freeze({
      title: "Station G2 standard repeater",
      summary: "The simplest sensor-enabled repeater for local USB installation and ordinary field use.",
      pattern: /^Station_G2_repeater-v/,
      expected: "Station_G2_repeater-v...",
      full: false,
      notes: [
        "Choose this when USB logging, MQTT, ESP-NOW, and remote LoRa updates are not required.",
        "This keeps the standard Station G2 partition profile.",
      ],
    }),
    "field-ota": Object.freeze({
      title: "Station G2 lean OTA repeater",
      summary: "A standalone repeater with LoRa/WiFi update support and no optional external environmental-sensor drivers.",
      pattern: /^Station_G2_repeater_lora_ota_no_external_sensors-ota-/,
      expected: "Station_G2_repeater_lora_ota_no_external_sensors-ota-...",
      full: false,
      notes: [
        "Board-native GPS support remains available; only optional external sensor drivers are removed.",
        "Use the exact matching target for every later LoRa package.",
      ],
    }),
    mqtt: Object.freeze({
      title: "Station G2 FULL MQTT observer/repeater",
      summary: "For direct radio-to-MQTT forwarding over WiFi with the complete CLI and expanded partitions.",
      pattern: /^Station_G2_repeater_observer_mqtt-full-ota-/,
      expected: "Station_G2_repeater_observer_mqtt-full-ota-...",
      full: true,
      notes: [
        "This is an MQTT observer role, not USB packet logging.",
        "Use its merged image once before any app-only WiFi or LoRa update.",
      ],
    }),
    espnow: Object.freeze({
      title: "Station G2 FULL ESP-NOW bridge repeater",
      summary: "For a repeater that bridges packets through ESP-NOW and needs the complete CLI.",
      pattern: /^Station_G2_repeater_bridge_espnow-full-ota-/,
      expected: "Station_G2_repeater_bridge_espnow-full-ota-...",
      full: true,
      notes: ["ESP-NOW bridge builds use the expanded FULL partition profile."],
    }),
    room: Object.freeze({
      title: "Station G2 room server",
      summary: "A standard room-server role without MQTT.",
      pattern: /^Station_G2_room_server-v/,
      expected: "Station_G2_room_server-v...",
      full: false,
      notes: ["Do not install this when the node is intended to remain a repeater."],
    }),
    "room-mqtt": Object.freeze({
      title: "Station G2 FULL MQTT room observer",
      summary: "A room server with direct MQTT/WiFi forwarding and expanded partitions.",
      pattern: /^Station_G2_room_server_observer_mqtt-full-ota-/,
      expected: "Station_G2_room_server_observer_mqtt-full-ota-...",
      full: true,
      notes: ["Use its merged image once before later same-profile OTA updates."],
    }),
    "companion-usb": Object.freeze({
      title: "Station G2 USB Companion",
      summary: "For a Station G2 controlled as a Companion through a USB serial connection.",
      pattern: /^Station_G2_companion_radio_usb-ota-/,
      expected: "Station_G2_companion_radio_usb-ota-...",
      full: false,
      notes: ["This is a Companion, not a standalone repeater or USB packet logger."],
    }),
    "companion-ble": Object.freeze({
      title: "Station G2 Bluetooth Companion",
      summary: "For a Station G2 controlled by a phone or computer through Bluetooth.",
      pattern: /^Station_G2_companion_radio_ble-/,
      expected: "Station_G2_companion_radio_ble-...",
      full: false,
      notes: ["Choose USB Companion instead when the data connection is normally wired."],
    }),
    "companion-wifi": Object.freeze({
      title: "Station G2 WiFi Companion",
      summary: "For Companion protocol access over WiFi.",
      pattern: /^Station_G2_companion_radio_wifi-ota-/,
      expected: "Station_G2_companion_radio_wifi-ota-...",
      full: false,
      notes: ["This is not the standalone MQTT observer role."],
    }),
    "companion-full": Object.freeze({
      title: "Station G2 Full Companion",
      summary: "A host-backed Companion that can serve LoRa OTA content and exposes the complete transport set.",
      pattern: /^Station_G2_companion_radio_full-v/,
      expected: "Station_G2_companion_radio_full-...",
      full: true,
      notes: [
        "Full Companion is an OTA source and host-backed controller, not a self-updating repeater.",
        "Install its merged image over USB.",
      ],
    }),
  });

  function isSafeGithubUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" &&
        (url.hostname === "github.com" || url.hostname === "objects.githubusercontent.com");
    } catch (_) {
      return false;
    }
  }

  function flattenReleases(releases, channel) {
    const allowPrerelease = channel === "development";
    const rows = [];
    (Array.isArray(releases) ? releases : []).forEach(function (release) {
      if (!release || release.draft || (!allowPrerelease && release.prerelease)) return;
      (Array.isArray(release.assets) ? release.assets : []).forEach(function (asset) {
        if (!asset || typeof asset.name !== "string") return;
        rows.push({
          name: asset.name,
          url: isSafeGithubUrl(asset.browser_download_url) ? asset.browser_download_url : "",
          size: Number(asset.size) || 0,
          releaseName: release.name || release.tag_name || "Release",
          releaseTag: release.tag_name || "",
          releaseUrl: isSafeGithubUrl(release.html_url) ? release.html_url : "",
          prerelease: Boolean(release.prerelease),
          publishedAt: release.published_at || release.created_at || "",
        });
      });
    });
    return rows.sort(function (a, b) {
      return String(b.publishedAt).localeCompare(String(a.publishedAt));
    });
  }

  function wantedSuffix(install) {
    return install === "first" ? "-merged.bin" : ".bin";
  }

  function selectAsset(rows, purposeKey, install) {
    const purpose = PURPOSES[purposeKey];
    if (!purpose) return null;
    const suffix = wantedSuffix(install);
    const matches = rows.filter(function (row) {
      if (!purpose.pattern.test(row.name) || !row.name.endsWith(suffix)) return false;
      if (install !== "first" && row.name.endsWith("-merged.bin")) return false;
      return true;
    });
    matches.sort(function (a, b) {
      const byDate = String(b.publishedAt).localeCompare(String(a.publishedAt));
      if (byDate) return byDate;
      const byLength = a.name.length - b.name.length;
      return byLength || a.name.localeCompare(b.name);
    });
    return matches[0] || null;
  }

  function formatBytes(value) {
    const size = Number(value) || 0;
    if (size >= 1024 * 1024) return (size / (1024 * 1024)).toFixed(2) + " MiB";
    if (size >= 1024) return (size / 1024).toFixed(1) + " KiB";
    return size + " bytes";
  }

  function installExplanation(install) {
    if (install === "first") {
      return {
        label: "USB merged-image install",
        warning: "This can replace the partition table. Back up configuration first.",
      };
    }
    if (install === "lora") {
      return {
        label: "LoRa OTA source image",
        warning: "Package the non-merged application for the exact target. The installed partition signature must already match.",
      };
    }
    return {
      label: "Browser/WiFi app-only update",
      warning: "Use only when the installed board, role, and partition layout already match.",
    };
  }

  function createElement(tag, text) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    return element;
  }

  function replaceFacts(container, facts) {
    container.replaceChildren();
    facts.forEach(function (fact) {
      container.appendChild(createElement("dt", fact[0]));
      const dd = createElement("dd", fact[1]);
      if (fact[2]) dd.title = fact[2];
      container.appendChild(dd);
    });
  }

  function renderSteps(container, purpose, install) {
    container.replaceChildren();
    container.appendChild(createElement("strong", "What to do"));
    const list = createElement("ol");
    const common = install === "first"
      ? [
          "Back up configuration, keys, and radio settings.",
          "Verify that the filename begins with Station_G2 before flashing.",
          "Flash the merged image over a data-capable USB cable.",
          "Wait for the board to reboot, reconnect, and verify its role and radio settings.",
        ]
      : install === "lora"
        ? [
            "Confirm the running target and partition signature already match this profile.",
            "Use the non-merged application as input to the LoRa OTA packaging workflow.",
            "Verify the target identity and completed update after reboot.",
          ]
        : [
            "Confirm the running target and partition signature already match this profile.",
            "Upload the non-merged application through the firmware's browser/WiFi updater.",
            "Verify the role and version after reboot.",
          ];
    common.concat(purpose.notes).forEach(function (text) {
      list.appendChild(createElement("li", text));
    });
    container.appendChild(list);
  }

  function initPicker(root) {
    const repo = root.getAttribute("data-release-repo") || "mikecarper/MeshCore";
    const form = root.querySelector('[data-role="form"]');
    const channelField = root.querySelector('[data-field="channel"]');
    const purposeField = root.querySelector('[data-field="purpose"]');
    const installField = root.querySelector('[data-field="install"]');
    const status = root.querySelector('[data-role="status"]');
    const result = root.querySelector('[data-role="result"]');
    const missing = root.querySelector('[data-role="missing"]');
    const search = root.querySelector('[data-field="asset-search"]');
    const assetResults = root.querySelector('[data-role="asset-results"]');
    let releases = [];

    function currentRows() {
      return flattenReleases(releases, channelField.value);
    }

    function render() {
      const purpose = PURPOSES[purposeField.value];
      const install = installField.value;
      const rows = currentRows();
      const asset = selectAsset(rows, purposeField.value, install);
      const installInfo = installExplanation(install);
      result.hidden = true;
      missing.hidden = true;

      if (!asset) {
        missing.hidden = false;
        const suffix = wantedSuffix(install);
        root.querySelector('[data-role="missing-text"]').textContent =
          "Expected a recent asset beginning with " + purpose.expected +
          " and ending in " + suffix + ". Try the other release channel or open the release collection.";
        return;
      }

      root.querySelector('[data-role="result-title"]').textContent = purpose.title;
      root.querySelector('[data-role="result-summary"]').textContent = purpose.summary;
      replaceFacts(root.querySelector('[data-role="result-facts"]'), [
        ["File", asset.name],
        ["Release", asset.releaseName],
        ["Install path", installInfo.label],
        ["Size", formatBytes(asset.size)],
        ["Important", installInfo.warning],
      ]);
      const download = root.querySelector('[data-role="download"]');
      download.href = asset.url || asset.releaseUrl;
      download.hidden = !(asset.url || asset.releaseUrl);
      const releaseLink = root.querySelector('[data-role="release"]');
      releaseLink.href = asset.releaseUrl || "https://github.com/" + repo + "/releases";
      renderSteps(root.querySelector('[data-role="steps"]'), purpose, install);
      result.hidden = false;
    }

    function renderSearch() {
      assetResults.replaceChildren();
      const query = search.value.trim().toLowerCase();
      if (query.length < 2) return;
      const matches = currentRows().filter(function (row) {
        return row.name.toLowerCase().includes(query);
      }).slice(0, 80);
      if (!matches.length) {
        assetResults.appendChild(createElement("p", "No recent release filename matched."));
        return;
      }
      const list = createElement("ul");
      list.className = "firmware-asset-list";
      matches.forEach(function (row) {
        const item = createElement("li");
        const link = createElement("a", row.name);
        link.href = row.url || row.releaseUrl;
        item.appendChild(link);
        const meta = createElement("span", row.releaseName + " - " + formatBytes(row.size));
        meta.className = "firmware-asset-meta";
        item.appendChild(meta);
        list.appendChild(item);
      });
      assetResults.appendChild(list);
    }

    form.addEventListener("change", function () {
      render();
      renderSearch();
    });
    search.addEventListener("input", renderSearch);

    const endpoint = "https://api.github.com/repos/" + encodeURIComponent(repo.split("/")[0]) +
      "/" + encodeURIComponent(repo.split("/")[1]) + "/releases?per_page=20";
    fetch(endpoint, { headers: { Accept: "application/vnd.github+json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("GitHub returned HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        releases = Array.isArray(data) ? data : [];
        const count = flattenReleases(releases, "development").length;
        status.textContent = "Loaded " + count + " firmware assets from the 20 most recent releases.";
        render();
      })
      .catch(function (error) {
        status.textContent = "Could not load the GitHub release catalog: " + error.message;
        missing.hidden = false;
        root.querySelector('[data-role="missing-text"]').textContent =
          "Open the release collection and look for the expected filename shown by this picker.";
      });
  }

  const api = Object.freeze({
    PURPOSES: PURPOSES,
    flattenReleases: flattenReleases,
    selectAsset: selectAsset,
    wantedSuffix: wantedSuffix,
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
