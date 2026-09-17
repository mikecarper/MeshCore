(function (global) {
  "use strict";

  const DEFAULTS = Object.freeze({
    start: "2026-09-21T17:00:00-07:00",
    end: "2026-09-23T17:00:00-07:00",
    tz: "",
    freq: "910.1",
    bw: "500",
    sf: "8",
    cr: "7",
    tx: "22",
  });
  const VALID_BANDWIDTHS = Object.freeze([
    7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500,
  ]);
  const SCHEDULE_HORIZON_MS = 0x7fffffff;
  const EARLY_JOIN_MS = 60 * 60 * 1000;
  const CLOCK_RESET_COMMAND = "clkreboot";
  const TIMEZONE_BOUNDARY_PATH = "../_data/timezones-2025b-simplified.json";
  const TIMEZONE_MAP_STYLE = Object.freeze({
    default: Object.freeze({
      color: "#ffffff",
      weight: 1,
      opacity: 0.7,
      dashArray: "3",
      fillColor: "#087f8c",
      fillOpacity: 0.14,
    }),
    hover: Object.freeze({
      color: "#67204f",
      weight: 2,
      opacity: 1,
      dashArray: "",
      fillColor: "#b83280",
      fillOpacity: 0.35,
    }),
    selected: Object.freeze({
      color: "#67204f",
      weight: 3,
      opacity: 1,
      dashArray: "",
      fillColor: "#b83280",
      fillOpacity: 0.5,
    }),
  });

  class PresetTestError extends Error {}

  function strictNumber(value, name) {
    const text = String(value).trim();
    if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) {
      throw new PresetTestError(name + " must be a decimal number");
    }
    const parsed = Number(text);
    if (!Number.isFinite(parsed)) {
      throw new PresetTestError(name + " is outside the supported range");
    }
    return parsed;
  }

  function strictSignedNumber(value, name) {
    const text = String(value).trim();
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) {
      throw new PresetTestError(name + " must be a decimal number");
    }
    const parsed = Number(text);
    if (!Number.isFinite(parsed)) {
      throw new PresetTestError(name + " is outside the supported range");
    }
    return parsed;
  }

  function strictInteger(value, name) {
    const text = String(value).trim();
    if (!/^\d+$/.test(text)) {
      throw new PresetTestError(name + " must be an integer");
    }
    return Number(text);
  }

  function parseTimestamp(value, name) {
    const text = String(value).trim();
    let milliseconds;
    if (/^\d{10}$/.test(text)) {
      milliseconds = Number(text) * 1000;
    } else if (/^\d{13}$/.test(text)) {
      milliseconds = Number(text);
    } else {
      if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(text)) {
        throw new PresetTestError(
          name + " must include an explicit UTC offset such as Z or -07:00"
        );
      }
      milliseconds = Date.parse(text);
    }
    if (!Number.isFinite(milliseconds)) {
      throw new PresetTestError(
        name + " must be ISO-8601 with an explicit offset, or a Unix timestamp"
      );
    }
    return milliseconds;
  }

  function numberText(value) {
    return String(Number(value));
  }

  function validateTimeZone(value) {
    const text = String(value || "").trim();
    if (!text) {
      throw new PresetTestError("tz must be an IANA time zone such as America/Los_Angeles");
    }
    try {
      return new Intl.DateTimeFormat("en-US", { timeZone: text })
        .resolvedOptions().timeZone;
    } catch (error) {
      throw new PresetTestError(
        "tz must be a valid IANA time zone such as America/Los_Angeles or UTC"
      );
    }
  }

  function browserTimeZone() {
    try {
      return validateTimeZone(
        new Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
      );
    } catch (error) {
      return "UTC";
    }
  }

  function supportedTimeZones(selectedTimeZone) {
    const zones = new Set(["UTC"]);
    if (typeof Intl.supportedValuesOf === "function") {
      Intl.supportedValuesOf("timeZone").forEach(function (zone) {
        zones.add(zone);
      });
    } else {
      [
        "Africa/Johannesburg", "America/Chicago", "America/Denver",
        "America/Los_Angeles", "America/New_York", "America/Phoenix",
        "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney", "Europe/Berlin",
        "Europe/London", "Pacific/Auckland", "Pacific/Honolulu",
      ].forEach(function (zone) { zones.add(zone); });
    }
    if (selectedTimeZone) zones.add(validateTimeZone(selectedTimeZone));
    return Array.from(zones).sort(function (left, right) {
      if (left === "UTC") return -1;
      if (right === "UTC") return 1;
      return left.localeCompare(right);
    });
  }

  function configFromSearch(search, fallbackTimeZone) {
    const params = new URLSearchParams(search || "");
    const raw = {};
    Object.keys(DEFAULTS).forEach(function (key) {
      raw[key] = params.has(key) ? params.get(key) : DEFAULTS[key];
    });
    if (!params.has("tz")) {
      raw.tz = fallbackTimeZone || browserTimeZone();
    }

    const startMs = parseTimestamp(raw.start, "start");
    const endMs = parseTimestamp(raw.end, "end");
    const tz = validateTimeZone(raw.tz);
    const freq = strictNumber(raw.freq, "freq");
    const bw = strictNumber(raw.bw, "bw");
    const sf = strictInteger(raw.sf, "sf");
    const cr = strictInteger(raw.cr, "cr");
    const tx = strictSignedNumber(raw.tx, "tx");

    if (endMs <= startMs) {
      throw new PresetTestError("end must be later than start");
    }
    if (freq < 150 || freq > 2500) {
      throw new PresetTestError("freq must be between 150 and 2500 MHz");
    }
    if (!VALID_BANDWIDTHS.some(function (allowed) {
      return Math.abs(allowed - bw) < 0.01;
    })) {
      throw new PresetTestError(
        "bw must be one of " + VALID_BANDWIDTHS.join(", ") + " kHz"
      );
    }
    if (sf < 5 || sf > 12) {
      throw new PresetTestError("sf must be between 5 and 12");
    }
    if (cr < 5 || cr > 8) {
      throw new PresetTestError("cr must be between 5 and 8");
    }
    if (tx < -30 || tx > 60) {
      throw new PresetTestError("tx must be between -30 and 60 dBm");
    }

    return Object.freeze({
      startMs: startMs,
      endMs: endMs,
      startEpoch: Math.floor(startMs / 1000),
      endEpoch: Math.floor(endMs / 1000),
      tz: tz,
      freq: freq,
      bw: bw,
      sf: sf,
      cr: cr,
      tx: tx,
      freqText: numberText(freq),
      bwText: numberText(bw),
      txText: numberText(tx),
    });
  }

  function phaseAt(config, nowMs) {
    if (nowMs < config.startMs) return "before";
    if (nowMs < config.endMs) return "active";
    return "ended";
  }

  function remainingMinutes(config, nowMs) {
    return Math.max(0, Math.ceil((config.endMs - nowMs) / 60000));
  }

  function immediateAvailable(config, nowMs) {
    return nowMs >= config.startMs - EARLY_JOIN_MS && nowMs < config.endMs;
  }

  function scheduleAvailability(config, nowMs) {
    if (nowMs >= config.startMs) {
      return { available: false, reason: "The start time has passed; use the immediate option." };
    }
    if (config.endMs - nowMs > SCHEDULE_HORIZON_MS) {
      return {
        available: false,
        reason: "The end is outside the firmware's roughly 24-day horizon; return closer to the test.",
      };
    }
    return { available: true, reason: "Ready to queue after the node clock is verified." };
  }

  function commandsFor(config, nowMs) {
    const tuple = [config.freqText, config.bwText, config.sf, config.cr].join(",");
    const minutes = remainingMinutes(config, nowMs);
    return Object.freeze({
      stockNow: "tempradio " + tuple + "," + minutes,
      companionNow:
        "set radio2.cross on\nset tempradio2 " + tuple + ",rxtx," + minutes,
      stockScheduled:
        "set tempradioat " + tuple + "," + config.startEpoch + "," + config.endEpoch +
        "\nget tempradioat",
      companionScheduled:
        "set radio2.cross on\nset tempradioat2 " + tuple + ",rxtx," +
        config.startEpoch + "," + config.endEpoch + "\nget tempradioat2",
      stockCancelBefore: "get tempradioat\ndel tempradioat all",
      stockCancelDuring: "reboot",
      stockLeaveIn30: "tempradio " + tuple + ",30",
      companionCancelBefore:
        "get tempradioat2\ndel tempradioat2 all\nset radio2.cross auto",
      companionCancelDuring: "set tempradio2 off\nset radio2.cross auto",
      companionLeaveIn30:
        "set radio2.cross on\ndel tempradioat2 all\nset tempradio2 " +
        tuple + ",rxtx,30",
    });
  }

  function radioEstimates(config) {
    const requiredSnr = {
      5: -2.5,
      6: -5,
      7: -7.5,
      8: -10,
      9: -12.5,
      10: -15,
      11: -17.5,
      12: -20,
    }[config.sf];
    const bandwidthHz = config.bw * 1000;
    const bitrateKbps = (
      config.sf * (4 / config.cr) * bandwidthHz / Math.pow(2, config.sf)
    ) / 1000;
    const sensitivityDbm = -174 + 10 * Math.log10(bandwidthHz) + 6 + requiredSnr;
    return Object.freeze({
      bitrateKbps: bitrateKbps,
      sensitivityDbm: sensitivityDbm,
      linkBudgetDb: config.tx - sensitivityDbm,
    });
  }

  function formatCountdown(milliseconds) {
    let seconds = Math.max(0, Math.ceil(milliseconds / 1000));
    const days = Math.floor(seconds / 86400);
    seconds %= 86400;
    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;
    const minutes = Math.floor(seconds / 60);
    seconds %= 60;
    const parts = [];
    if (days) parts.push(days + "d");
    if (days || hours) parts.push(hours + "h");
    if (days || hours || minutes) parts.push(minutes + "m");
    parts.push(seconds + "s");
    return parts.join(" ");
  }

  function formatZoned(milliseconds, timeZone) {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: timeZone,
      weekday: "long",
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(new Date(milliseconds));
  }

  function zonedParts(milliseconds, timeZone) {
    const result = {};
    new Intl.DateTimeFormat("en-US", {
      timeZone: timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(milliseconds)).forEach(function (part) {
      if (part.type !== "literal") result[part.type] = Number(part.value);
    });
    return result;
  }

  function twoDigits(value) {
    return String(value).padStart(2, "0");
  }

  function zonedInputValue(milliseconds, timeZone) {
    const parts = zonedParts(milliseconds, timeZone);
    return String(parts.year).padStart(4, "0") + "-" +
      twoDigits(parts.month) + "-" + twoDigits(parts.day) + "T" +
      twoDigits(parts.hour) + ":" + twoDigits(parts.minute);
  }

  function parseLocalDateTime(value, name) {
    const text = String(value || "").trim();
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text);
    if (!match) {
      throw new PresetTestError(name + " must include a date and time");
    }
    const parts = {
      year: Number(match[1]),
      month: Number(match[2]),
      day: Number(match[3]),
      hour: Number(match[4]),
      minute: Number(match[5]),
      second: Number(match[6] || 0),
    };
    const checked = new Date(Date.UTC(
      parts.year, parts.month - 1, parts.day,
      parts.hour, parts.minute, parts.second
    ));
    if (checked.getUTCFullYear() !== parts.year ||
        checked.getUTCMonth() + 1 !== parts.month ||
        checked.getUTCDate() !== parts.day ||
        checked.getUTCHours() !== parts.hour ||
        checked.getUTCMinutes() !== parts.minute ||
        checked.getUTCSeconds() !== parts.second) {
      throw new PresetTestError(name + " is not a valid calendar date and time");
    }
    return parts;
  }

  function sameDateTime(left, right) {
    return left.year === right.year && left.month === right.month &&
      left.day === right.day && left.hour === right.hour &&
      left.minute === right.minute && left.second === right.second;
  }

  function offsetAt(milliseconds, timeZone) {
    const parts = zonedParts(milliseconds, timeZone);
    const rounded = Math.floor(milliseconds / 1000) * 1000;
    return Date.UTC(
      parts.year, parts.month - 1, parts.day,
      parts.hour, parts.minute, parts.second
    ) - rounded;
  }

  function localDateTimeToMs(value, timeZone, name) {
    const label = name || "date and time";
    const zone = validateTimeZone(timeZone);
    const desired = parseLocalDateTime(value, label);
    const wallMilliseconds = Date.UTC(
      desired.year, desired.month - 1, desired.day,
      desired.hour, desired.minute, desired.second
    );
    const offsets = new Set();
    for (let hours = -48; hours <= 48; hours += 6) {
      offsets.add(offsetAt(wallMilliseconds + hours * 60 * 60 * 1000, zone));
    }
    const matches = Array.from(offsets).map(function (offset) {
      return wallMilliseconds - offset;
    }).filter(function (candidate) {
      return sameDateTime(zonedParts(candidate, zone), desired);
    }).sort(function (left, right) { return left - right; });

    if (matches.length === 0) {
      throw new PresetTestError(
        label + " does not exist in " + zone + " because of a clock change"
      );
    }
    if (matches.length > 1) {
      throw new PresetTestError(
        label + " is ambiguous in " + zone + " because of a clock change"
      );
    }
    return matches[0];
  }

  function configFromGenerator(values) {
    const tz = validateTimeZone(values.tz);
    const startMs = localDateTimeToMs(values.start, tz, "start");
    const endMs = localDateTimeToMs(values.end, tz, "end");
    const params = new URLSearchParams();
    params.set("start", new Date(startMs).toISOString());
    params.set("end", new Date(endMs).toISOString());
    params.set("tz", tz);
    params.set("freq", values.freq);
    params.set("bw", values.bw);
    params.set("sf", values.sf);
    params.set("cr", values.cr);
    params.set("tx", values.tx);
    return configFromSearch("?" + params.toString());
  }

  function formatUtc(milliseconds) {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "UTC",
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZoneName: "short",
    }).format(new Date(milliseconds));
  }

  function readableQueryValue(value) {
    return encodeURIComponent(String(value))
      .replace(/%3A/gi, ":")
      .replace(/%2F/gi, "/");
  }

  function configuredUrl(config, baseUrl) {
    const url = new URL(baseUrl);
    const values = [
      ["start", new Date(config.startMs).toISOString()],
      ["end", new Date(config.endMs).toISOString()],
      ["tz", config.tz],
      ["freq", config.freqText],
      ["bw", config.bwText],
      ["sf", String(config.sf)],
      ["cr", String(config.cr)],
      ["tx", config.txText],
    ];
    url.search = "?" + values.map(function (entry) {
      return entry[0] + "=" + readableQueryValue(entry[1]);
    }).join("&");
    return url.toString();
  }

  function setText(root, selector, value) {
    root.querySelectorAll(selector).forEach(function (element) {
      element.textContent = value;
    });
  }

  function setCommand(root, name, value) {
    setText(root, '[data-command="' + name + '"]', value);
    root.querySelectorAll('[data-copy-command="' + name + '"]').forEach(function (button) {
      button.dataset.copyValue = value;
    });
  }

  function setCommandEnabled(root, name, enabled) {
    root.querySelectorAll('[data-copy-command="' + name + '"]').forEach(function (button) {
      button.disabled = !enabled;
    });
  }

  function setMaterialCommandCopyEnabled(root, name, enabled) {
    root.querySelectorAll('[data-command="' + name + '"]').forEach(function (code) {
      const container = code.closest(".highlight") || code.closest("pre") || code.parentElement;
      if (!container) return;
      container.querySelectorAll(
        'button.md-code__button[data-md-type="copy"], button.md-clipboard'
      ).forEach(function (button) {
        button.disabled = !enabled;
        if (enabled) button.removeAttribute("aria-disabled");
        else button.setAttribute("aria-disabled", "true");
      });
    });
  }

  function fallbackCopy(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () {
        fallbackCopy(text);
      });
    }
    fallbackCopy(text);
    return Promise.resolve();
  }

  function timeZoneBoundaryUrl() {
    let baseUrl = global.location
      ? global.location.href
      : "https://example.invalid/preset_test/";
    if (typeof document !== "undefined") {
      const script = document.querySelector('script[src*="_javascript/preset_test.js"]');
      if (script && script.src) baseUrl = script.src;
    }
    return new URL(TIMEZONE_BOUNDARY_PATH, baseUrl).toString();
  }

  function initTimeZoneMap(root, initialTimeZone, onChange) {
    const container = root.querySelector('[data-role="timezone-map"]');
    const status = root.querySelector('[data-role="timezone-map-status"]');
    const selectedLabel = root.querySelector('[data-role="selected-time-zone"]');
    const browserButton = root.querySelector('[data-action="use-browser-time-zone"]');
    const disclosure = container && container.closest("details");
    const generator = root.querySelector('[data-role="url-generator"]');
    const zoneInput = generator && generator.elements.tz;
    let selectedZone = validateTimeZone(initialTimeZone);
    let selectedLayer = null;
    const layerByZone = new Map();
    let map = null;

    function showStatus(message, state) {
      if (!status) return;
      status.textContent = message;
      status.dataset.state = state || "ready";
    }

    function setSelection(zone, options) {
      const settings = options || {};
      selectedZone = validateTimeZone(zone);
      if (zoneInput) zoneInput.value = selectedZone;
      if (selectedLabel) selectedLabel.textContent = selectedZone;

      if (selectedLayer) {
        selectedLayer.setStyle(TIMEZONE_MAP_STYLE.default);
        selectedLayer.bringToBack();
        selectedLayer = null;
      }

      const nextLayer = layerByZone.get(selectedZone);
      if (nextLayer) {
        selectedLayer = nextLayer;
        selectedLayer.setStyle(TIMEZONE_MAP_STYLE.selected);
        selectedLayer.bringToFront();
        if (map && settings.focus) {
          map.fitBounds(selectedLayer.getBounds(), { padding: [18, 18], maxZoom: 5 });
        }
        showStatus("Selected " + selectedZone + ".", "ready");
      } else if (layerByZone.size) {
        showStatus(
          selectedZone + " is selected, but this zone has no visible land boundary on the map.",
          "ready"
        );
      }

      if (settings.notify && typeof onChange === "function") onChange(selectedZone);
    }

    if (zoneInput) zoneInput.value = selectedZone;
    if (selectedLabel) selectedLabel.textContent = selectedZone;

    if (browserButton) {
      browserButton.addEventListener("click", function () {
        setSelection(browserTimeZone(), { focus: true, notify: true });
      });
    }

    if (!container) return;
    if (!global.L || typeof global.L.map !== "function") {
      showStatus(
        "The map library did not load. The browser time zone is still selected.",
        "error"
      );
      return;
    }

    const L = global.L;
    map = L.map(container, {
      minZoom: 1,
      maxZoom: 8,
      worldCopyJump: true,
    }).setView([25, 10], 2);

    if (disclosure) {
      disclosure.addEventListener("toggle", function () {
        if (!disclosure.open || !map) return;
        global.setTimeout(function () {
          map.invalidateSize();
          if (selectedLayer) {
            map.fitBounds(selectedLayer.getBounds(), { padding: [18, 18], maxZoom: 5 });
          }
        }, 0);
      });
    }

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      minZoom: 1,
      maxZoom: 8,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    global.fetch(timeZoneBoundaryUrl()).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    }).then(function (geoJson) {
      L.geoJSON(geoJson, {
        style: TIMEZONE_MAP_STYLE.default,
        onEachFeature: function (feature, layer) {
          const zone = feature && feature.properties && feature.properties.tzid;
          if (!zone) return;
          layerByZone.set(zone, layer);
          layer.bindTooltip(zone, { sticky: true });
          layer.on("mouseover", function () {
            if (layer !== selectedLayer) layer.setStyle(TIMEZONE_MAP_STYLE.hover);
          });
          layer.on("mouseout", function () {
            if (layer !== selectedLayer) layer.setStyle(TIMEZONE_MAP_STYLE.default);
          });
          layer.on("click", function () {
            setSelection(zone, { focus: true, notify: true });
          });
        },
      }).addTo(map);
      setSelection(selectedZone, { focus: true, notify: false });
      global.setTimeout(function () { map.invalidateSize(); }, 0);
    }).catch(function () {
      showStatus(
        "The time zone boundaries could not be loaded. The current selection is still usable.",
        "error"
      );
    });
  }

  function init(root) {
    let config;
    try {
      config = configFromSearch(
        global.location ? global.location.search : "",
        browserTimeZone()
      );
    } catch (error) {
      const box = root.querySelector('[data-role="config-error"]');
      box.hidden = false;
      box.textContent = "Invalid test URL: " + error.message;
      root.querySelector('[data-role="content"]').hidden = true;
      return;
    }

    setText(root, '[data-field="freq-display"]', config.freq.toFixed(3));
    setText(root, '[data-field="bw-display"]', config.bwText);
    setText(root, '[data-field="sf"]', String(config.sf));
    setText(root, '[data-field="cr"]', String(config.cr));
    setText(root, '[data-role="start-zoned"]', formatZoned(config.startMs, config.tz));
    setText(root, '[data-role="end-zoned"]', formatZoned(config.endMs, config.tz));
    setText(root, '[data-role="display-zone"]', config.tz);
    setText(
      root,
      '[data-role="epoch-range"]',
      config.startEpoch + " → " + config.endEpoch
    );
    setText(
      root,
      '[data-role="window-summary"]',
      "A " + formatCountdown(config.endMs - config.startMs) +
        " window. Saved primary settings return automatically at the end."
    );

    const staticCommands = commandsFor(config, config.startMs);
    setCommand(root, "stock-scheduled", staticCommands.stockScheduled);
    setCommand(root, "companion-scheduled", staticCommands.companionScheduled);
    setCommand(root, "stock-cancel-before", staticCommands.stockCancelBefore);
    setCommand(root, "stock-cancel-during", staticCommands.stockCancelDuring);
    setCommand(root, "stock-leave-30", staticCommands.stockLeaveIn30);
    setCommand(root, "companion-cancel-before", staticCommands.companionCancelBefore);
    setCommand(root, "companion-cancel-during", staticCommands.companionCancelDuring);
    setCommand(root, "companion-leave-30", staticCommands.companionLeaveIn30);
    setCommand(root, "reset-clock", CLOCK_RESET_COMMAND);

    const generator = root.querySelector('[data-role="url-generator"]');
    if (generator) {
      generator.elements.start.value = zonedInputValue(config.startMs, config.tz);
      generator.elements.end.value = zonedInputValue(config.endMs, config.tz);
      generator.elements.tz.value = config.tz;
      setText(root, '[data-role="selected-time-zone"]', config.tz);
      generator.elements.freq.value = config.freqText;
      generator.elements.bw.value = config.bwText;
      generator.elements.sf.value = String(config.sf);
      generator.elements.cr.value = String(config.cr);
      generator.elements.tx.value = config.txText;

      function generateUrl() {
        const errorBox = root.querySelector('[data-role="generator-error"]');
        try {
          const generated = configFromGenerator({
            start: generator.elements.start.value,
            end: generator.elements.end.value,
            tz: generator.elements.tz.value,
            freq: generator.elements.freq.value,
            bw: generator.elements.bw.value,
            sf: generator.elements.sf.value,
            cr: generator.elements.cr.value,
            tx: generator.elements.tx.value,
          });
          const url = configuredUrl(
            generated,
            global.location ? global.location.href : "https://example.invalid/"
          );
          setCommand(root, "generated-url", url);
          setCommandEnabled(root, "generated-url", true);
          const estimates = radioEstimates(generated);
          setText(root, '[data-role="estimate-rate"]', estimates.bitrateKbps.toFixed(2) + " kbps");
          setText(root, '[data-role="estimate-sensitivity"]', estimates.sensitivityDbm.toFixed(1) + " dBm");
          setText(root, '[data-role="estimate-budget"]', estimates.linkBudgetDb.toFixed(1) + " dB");
          setText(root, '[data-role="estimate-tx"]', generated.txText + " dBm");
          const openLink = root.querySelector('[data-role="open-generated-url"]');
          if (openLink) {
            openLink.href = url;
            openLink.hidden = false;
          }
          if (errorBox) {
            errorBox.textContent = "";
            errorBox.hidden = true;
          }
        } catch (error) {
          setCommand(root, "generated-url", "Fix the highlighted configuration error first.");
          setCommandEnabled(root, "generated-url", false);
          const openLink = root.querySelector('[data-role="open-generated-url"]');
          if (openLink) openLink.hidden = true;
          if (errorBox) {
            errorBox.textContent = error.message;
            errorBox.hidden = false;
          }
        }
      }

      generator.addEventListener("submit", function (event) {
        event.preventDefault();
        generateUrl();
      });
      generator.addEventListener("change", generateUrl);
      generateUrl();
      initTimeZoneMap(root, config.tz, generateUrl);
    }

    root.querySelectorAll("[data-copy-command]").forEach(function (button) {
      button.addEventListener("click", function () {
        const original = button.textContent;
        copyText(button.dataset.copyValue || "").then(function () {
          button.textContent = "Copied";
          global.setTimeout(function () { button.textContent = original; }, 1600);
        });
      });
    });

    function render() {
      const nowMs = Date.now();
      const phase = phaseAt(config, nowMs);
      const immediateOpen = immediateAvailable(config, nowMs);
      const status = root.querySelector('[data-role="status"]');
      const schedule = scheduleAvailability(config, nowMs);
      const commands = commandsFor(config, nowMs);

      status.dataset.state = phase;
      if (phase === "before") {
        if (immediateOpen) {
          status.textContent = "Setup window open";
          setText(root, '[data-role="countdown-label"]', "Test starts in");
          setText(root, '[data-role="countdown"]', formatCountdown(config.startMs - nowMs));
          setText(root, '[data-role="countdown-detail"]', "Immediate TempRadio commands are enabled.");
        } else {
          status.textContent = "Scheduled";
          setText(root, '[data-role="countdown-label"]', "Commands open in");
          setText(
            root,
            '[data-role="countdown"]',
            formatCountdown(config.startMs - EARLY_JOIN_MS - nowMs)
          );
          setText(root, '[data-role="countdown-detail"]', "The setup window opens one hour before the test.");
        }
      } else if (phase === "active") {
        status.textContent = "Test live";
        setText(root, '[data-role="countdown-label"]', "Ends in");
        setText(root, '[data-role="countdown"]', formatCountdown(config.endMs - nowMs));
        setText(root, '[data-role="countdown-detail"]', "Temporary radios revert at zero.");
      } else {
        status.textContent = "Test finished";
        setText(root, '[data-role="countdown-label"]', "Window closed");
        setText(root, '[data-role="countdown"]', "0s");
        setText(root, '[data-role="countdown-detail"]', "Temporary radios should be back on saved settings.");
      }

      if (immediateOpen) {
        setCommand(root, "stock-now", commands.stockNow);
        setCommand(root, "companion-now", commands.companionNow);
      } else {
        const unavailable = phase === "before"
          ? "Available one hour before the test — use Option 2 to schedule now."
          : "Test window ended — do not start TempRadio.";
        setCommand(root, "stock-now", unavailable);
        setCommand(root, "companion-now", unavailable);
      }
      setCommandEnabled(root, "stock-now", immediateOpen);
      setCommandEnabled(root, "companion-now", immediateOpen);
      setMaterialCommandCopyEnabled(root, "stock-now", immediateOpen);
      setMaterialCommandCopyEnabled(root, "companion-now", immediateOpen);
      setText(
        root,
        '[data-role="stock-now-note"]',
        phase === "before" && !immediateOpen
          ? "Available one hour before the test; schedule it now with Option 2."
          : phase === "before"
            ? "The setup window is open; the timeout includes the hour before the official start."
          : phase === "active"
            ? "The final argument is the live minutes remaining until the common end."
            : "The test window has ended."
      );

      setCommandEnabled(root, "stock-scheduled", schedule.available);
      setCommandEnabled(root, "companion-scheduled", schedule.available);
      setText(root, '[data-role="stock-schedule-note"]', schedule.reason);

      const nowEpoch = Math.floor(nowMs / 1000);
      setText(root, '[data-role="browser-utc"]', formatUtc(nowMs));
      setText(root, '[data-role="browser-epoch"]', String(nowEpoch));
      setCommand(root, "set-clock", "time " + nowEpoch + "\nclock");
    }

    render();
    if (typeof global.MutationObserver === "function") {
      const materialCopyObserver = new global.MutationObserver(function () {
        const enabled = immediateAvailable(config, Date.now());
        setMaterialCommandCopyEnabled(root, "stock-now", enabled);
        setMaterialCommandCopyEnabled(root, "companion-now", enabled);
      });
      materialCopyObserver.observe(root, { childList: true, subtree: true });
    }
    global.setInterval(render, 1000);
  }

  const api = Object.freeze({
    DEFAULTS: DEFAULTS,
    VALID_BANDWIDTHS: VALID_BANDWIDTHS,
    SCHEDULE_HORIZON_MS: SCHEDULE_HORIZON_MS,
    EARLY_JOIN_MS: EARLY_JOIN_MS,
    CLOCK_RESET_COMMAND: CLOCK_RESET_COMMAND,
    PresetTestError: PresetTestError,
    configFromSearch: configFromSearch,
    configFromGenerator: configFromGenerator,
    validateTimeZone: validateTimeZone,
    browserTimeZone: browserTimeZone,
    supportedTimeZones: supportedTimeZones,
    phaseAt: phaseAt,
    remainingMinutes: remainingMinutes,
    immediateAvailable: immediateAvailable,
    scheduleAvailability: scheduleAvailability,
    commandsFor: commandsFor,
    radioEstimates: radioEstimates,
    formatCountdown: formatCountdown,
    formatZoned: formatZoned,
    zonedInputValue: zonedInputValue,
    localDateTimeToMs: localDateTimeToMs,
    configuredUrl: configuredUrl,
    init: init,
  });

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.MeshCorePresetTest = api;

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      document.querySelectorAll("[data-preset-test]").forEach(init);
    });
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
