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
    "hardware",
    "role",
    "logging",
    "ota",
    "mode",
    "feature",
    "variant",
  ]);

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
    wifi: "Wi-Fi MQTT observer",
  });

  const OTA_LABELS = Object.freeze({
    none: "No explicit OTA profile",
    "ota-enabled": "OTA-enabled profile",
    "lora-receiver": "LoRa OTA receiver",
    "lora-source": "LoRa OTA source (Full Companion)",
  });

  const MODE_LABELS = Object.freeze({
    standard: "Standard",
    full: "Full Companion transports",
    ble: "Bluetooth LE",
    usb: "USB",
    wifi: "Wi-Fi",
    serial: "Serial / UART",
    ethernet: "Ethernet",
    mqtt: "MQTT observer",
    espnow: "ESP-NOW bridge",
    rs232: "RS-232 bridge",
  });

  const FEATURE_LABELS = Object.freeze({
    standard: "Standard profile",
    full: "FULL / complete profile",
  });

  const INSTALL_LABELS = Object.freeze({
    "merged-bin": "USB first install / recovery (.bin)",
    bin: "Same-profile application update (.bin)",
    zip: "nRF52 Serial DFU (.zip)",
    uf2: "UF2 drag-and-drop (.uf2)",
    hex: "Full wired flash (.hex)",
  });

  const INSTALL_ORDER = Object.freeze([
    "merged-bin",
    "bin",
    "zip",
    "uf2",
    "hex",
  ]);

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
      if (/^ble(?:$|[_-])/.test(value)) return "ble";
      if (/^usb(?:$|[_-])/.test(value)) return "usb";
      if (/^wifi(?:$|[_-])/.test(value)) return "wifi";
      if (/^serial(?:$|[_-])/.test(value)) return "serial";
      if (/^ethernet(?:$|[_-])/.test(value)) return "ethernet";
      return "standard";
    }
    if (value.includes("observer_mqtt")) return "mqtt";
    if (value.includes("bridge_espnow")) return "espnow";
    if (value.includes("bridge_rs232")) return "rs232";
    if (value.includes("ethernet")) return "ethernet";
    return "standard";
  }

  function variantForProfile(role, tail) {
    let value = String(tail);
    if (role === "companion") {
      value = value.replace(
        /^(?:full|ble|usb|wifi|serial|ethernet)(?=$|[_-])/i,
        " "
      );
    }
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
    const lowerTarget = target.toLowerCase();
    const lowerTail = parts.tail.toLowerCase();
    const mode = modeForRole(parts.role, parts.tail);
    const logging = lowerTarget.includes("observer_mqtt")
      ? "wifi"
      : lowerTarget.includes("-logging")
        ? "usb"
        : "none";
    const feature = lowerTail.includes("-full") ||
      (parts.role === "companion" && mode === "full")
      ? "full"
      : "standard";
    const explicitOta = lowerTarget.includes("lora_ota")
      ? "lora-receiver"
      : parts.role === "companion" && mode === "full"
        ? "lora-source"
        : "";

    return {
      target: target,
      hardware: parts.hardware || target,
      role: parts.role,
      logging: logging,
      mode: mode,
      feature: feature,
      variant: variantForProfile(parts.role, parts.tail),
      explicitOta: explicitOta,
    };
  }

  function canonicalAsset(files, installKind) {
    return (Array.isArray(files) ? files : []).filter(function (file) {
      return file.installKind === installKind;
    }).sort(function (a, b) {
      const byLength = a.name.length - b.name.length;
      return byLength || a.name.localeCompare(b.name);
    })[0] || null;
  }

  function buildCatalog(releases) {
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

    const profiles = Array.from(grouped.values()).map(function (profile) {
      profile.ota = profile.explicitOta ||
        (profile.otaPackaged ? "ota-enabled" : "none");
      profile.installKinds = INSTALL_ORDER.filter(function (kind) {
        return Boolean(canonicalAsset(profile.files, kind));
      });
      profile.files.sort(function (a, b) {
        return a.name.localeCompare(b.name);
      });
      return profile;
    }).sort(function (a, b) {
      return a.target.localeCompare(b.target, undefined, {
        numeric: true,
        sensitivity: "base",
      });
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
      return !value || profile[field] === value;
    });
  }

  function uniqueValues(profiles, field) {
    return Array.from(new Set((profiles || []).map(function (profile) {
      return profile[field];
    }).filter(Boolean)));
  }

  function humanizeHardware(value) {
    return String(value || "").replace(/_/g, " ").replace(/\s+/g, " ").trim();
  }

  function humanizeVariant(value) {
    if (!value || value === "default") return "Default";
    const tokenLabels = {
      ps: "Power save",
      femon: "FEM on",
      femoff: "FEM off",
      serial1: "Serial 1",
      serial2: "Serial 2",
      sim: "SIM",
      rak15001: "RAK15001",
      qspi: "QSPI",
    };
    return value.split("-").map(function (token) {
      return tokenLabels[token] ||
        token.charAt(0).toUpperCase() + token.slice(1);
    }).join(" ");
  }

  function labelFor(field, value) {
    if (field === "hardware") return humanizeHardware(value);
    if (field === "role") return ROLE_LABELS[value] || value;
    if (field === "logging") return LOGGING_LABELS[value] || value;
    if (field === "ota") return OTA_LABELS[value] || value;
    if (field === "mode") return MODE_LABELS[value] || value;
    if (field === "feature") return FEATURE_LABELS[value] || value;
    if (field === "variant") return humanizeVariant(value);
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
    const loggingOrder = ["none", "usb", "wifi"];
    const otaOrder = ["none", "ota-enabled", "lora-receiver", "lora-source"];
    const featureOrder = ["standard", "full"];
    const modeOrder = ["standard", "full", "ble", "usb", "wifi", "serial", "ethernet", "mqtt", "espnow", "rs232"];
    const orders = {
      role: roleOrder,
      logging: loggingOrder,
      ota: otaOrder,
      feature: featureOrder,
      mode: modeOrder,
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
        "Use this path for a first install, recovery, or partition/profile migration.",
      ],
      bin: [
        "Use this app-only image only when the installed board, role, and partition layout already match.",
        "Do not use an app-only image to migrate partition layouts.",
      ],
      zip: [
        "Use the ZIP as the native nRF52 Serial DFU package; it is not an extra archive.",
        "For LoRa OTA receiving, install the exact-board OTAFIX bootloader first.",
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
    if (profile.ota === "lora-receiver") {
      extra.push(
        "This installs the LoRa OTA receiver profile. The later update package must still match the exact target and partition signature."
      );
    } else if (profile.ota === "lora-source") {
      extra.push(
        "Full Companion can serve a host-supplied update to another node; it does not self-install that LoRa update."
      );
    }
    return common.concat(byKind[kind] || [], extra);
  }

  function replaceFacts(container, facts) {
    container.replaceChildren();
    facts.forEach(function (fact) {
      container.appendChild(createElement("dt", fact[0]));
      container.appendChild(createElement("dd", fact[1]));
    });
  }

  function renderProfileCard(container, profile, asset, installKind) {
    const card = createElement("article");
    card.className = "firmware-picker-card";
    card.appendChild(createElement(
      "h3",
      humanizeHardware(profile.hardware) + " — " +
        (ROLE_LABELS[profile.role] || profile.role)
    ));

    const facts = createElement("dl");
    facts.className = "firmware-picker-facts";
    replaceFacts(facts, [
      ["Target", profile.target],
      ["Connection / mode", labelFor("mode", profile.mode)],
      ["Logging", labelFor("logging", profile.logging)],
      ["OTA", labelFor("ota", profile.ota)],
      ["Feature profile", labelFor("feature", profile.feature)],
      ["Variant", labelFor("variant", profile.variant)],
      ["Install path", labelFor("install", installKind)],
      ["File", asset.name],
      ["Size", formatBytes(asset.size)],
      ["Release", asset.releaseName],
    ]);
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
    container.appendChild(card);
  }

  function initPicker(root) {
    const repo = root.getAttribute("data-release-repo") || "mikecarper/MeshCore";
    const status = root.querySelector('[data-role="status"]');
    const releaseSetStatus = root.querySelector('[data-role="release-set"]');
    const result = root.querySelector('[data-role="result"]');
    const resultList = root.querySelector('[data-role="result-list"]');
    const missing = root.querySelector('[data-role="missing"]');
    const installSelect = root.querySelector('[data-field="install"]');
    const search = root.querySelector('[data-field="asset-search"]');
    const assetResults = root.querySelector('[data-role="asset-results"]');
    const selects = {};
    FILTER_FIELDS.forEach(function (field) {
      selects[field] = root.querySelector('[data-field="' + field + '"]');
    });
    let catalog = { releaseSet: null, rows: [], profiles: [] };
    const filters = {};

    function matchingProfiles(fields) {
      return catalog.profiles.filter(function (profile) {
        return profileMatches(profile, filters, fields);
      });
    }

    function setOptions(select, field, values, placeholder, selected) {
      select.replaceChildren();
      const blank = createElement("option", placeholder);
      blank.value = "";
      select.appendChild(blank);
      values.slice().sort(function (a, b) {
        return optionSort(field, a, b);
      }).forEach(function (value) {
        const option = createElement("option", labelFor(field, value));
        option.value = value;
        select.appendChild(option);
      });
      select.disabled = values.length === 0;
      if (selected && values.includes(selected)) select.value = selected;
      if (values.length === 1) select.value = values[0];
    }

    function rebuildCascade(changedIndex) {
      if (changedIndex != null && changedIndex >= 0) {
        FILTER_FIELDS.slice(changedIndex + 1).forEach(function (field) {
          filters[field] = "";
          selects[field].value = "";
        });
        installSelect.value = "";
      }

      let priorComplete = true;
      FILTER_FIELDS.forEach(function (field, index) {
        const select = selects[field];
        if (!priorComplete) {
          setOptions(select, field, [], "Choose earlier options first", "");
          filters[field] = "";
          return;
        }
        const priorFields = FILTER_FIELDS.slice(0, index);
        const candidates = matchingProfiles(priorFields);
        const values = uniqueValues(candidates, field);
        const previous = filters[field] || select.value;
        const placeholder = field === "hardware"
          ? "Choose hardware"
          : "Choose " + field;
        setOptions(select, field, values, placeholder, previous);
        filters[field] = select.value;
        if (!filters[field]) priorComplete = false;
      });
      rebuildInstall(priorComplete);
      render();
    }

    function rebuildInstall(profileComplete) {
      if (!profileComplete) {
        setOptions(
          installSelect,
          "install",
          [],
          "Choose firmware options first",
          ""
        );
        return;
      }
      const matches = matchingProfiles(FILTER_FIELDS);
      const kinds = Array.from(new Set(matches.flatMap(function (profile) {
        return profile.installKinds;
      }))).sort(function (a, b) {
        return INSTALL_ORDER.indexOf(a) - INSTALL_ORDER.indexOf(b);
      });
      const previous = installSelect.value;
      setOptions(
        installSelect,
        "install",
        kinds,
        "Choose installation method",
        previous
      );
    }

    function nextMissingField() {
      return FILTER_FIELDS.find(function (field) {
        return !filters[field];
      }) || (!installSelect.value ? "installation method" : "");
    }

    function render() {
      result.hidden = true;
      missing.hidden = true;
      resultList.replaceChildren();
      const missingField = nextMissingField();
      const activeFields = FILTER_FIELDS.filter(function (field) {
        return Boolean(filters[field]);
      });
      const matches = matchingProfiles(activeFields);

      if (missingField) {
        status.textContent = "Choose " + missingField + ". " +
          matches.length + " compatible configuration" +
          (matches.length === 1 ? " remains." : "s remain.");
        return;
      }

      const installKind = installSelect.value;
      const resolved = matches.map(function (profile) {
        return {
          profile: profile,
          asset: canonicalAsset(profile.files, installKind),
        };
      }).filter(function (entry) {
        return Boolean(entry.asset);
      });
      if (!resolved.length) {
        missing.hidden = false;
        root.querySelector('[data-role="missing-text"]').textContent =
          "No exact file provides that installation method. Change the install method or one of the firmware options.";
        status.textContent = "No exact firmware file matched.";
        return;
      }

      status.textContent = resolved.length + " exact firmware configuration" +
        (resolved.length === 1 ? " matched." : "s matched.");
      resolved.forEach(function (entry) {
        renderProfileCard(
          resultList,
          entry.profile,
          entry.asset,
          installKind
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

    FILTER_FIELDS.forEach(function (field, index) {
      selects[field].addEventListener("change", function () {
        filters[field] = selects[field].value;
        rebuildCascade(index);
      });
    });
    installSelect.addEventListener("change", render);
    search.addEventListener("input", renderSearch);

    const endpoint = "https://api.github.com/repos/" +
      encodeURIComponent(repo.split("/")[0]) + "/" +
      encodeURIComponent(repo.split("/")[1]) +
      "/releases?per_page=20";
    fetch(endpoint, { headers: { Accept: "application/vnd.github+json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("GitHub returned HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (data) {
        catalog = buildCatalog(data);
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
        status.textContent =
          "Catalog loaded. Start by choosing the exact hardware.";
        rebuildCascade(-1);
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
    ROLE_LABELS: ROLE_LABELS,
    LOGGING_LABELS: LOGGING_LABELS,
    OTA_LABELS: OTA_LABELS,
    MODE_LABELS: MODE_LABELS,
    FEATURE_LABELS: FEATURE_LABELS,
    INSTALL_LABELS: INSTALL_LABELS,
    selectReleaseSet: selectReleaseSet,
    flattenReleaseAssets: flattenReleaseAssets,
    parseFirmwareAsset: parseFirmwareAsset,
    parseTargetProfile: parseTargetProfile,
    buildCatalog: buildCatalog,
    profileMatches: profileMatches,
    uniqueValues: uniqueValues,
    canonicalAsset: canonicalAsset,
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
