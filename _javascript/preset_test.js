(function (global) {
  "use strict";

  const DEFAULTS = Object.freeze({
    start: "2026-09-21T17:00:00-07:00",
    end: "2026-09-23T17:00:00-07:00",
    freq: "910.1",
    bw: "500",
    sf: "8",
    cr: "7",
  });
  const VALID_BANDWIDTHS = Object.freeze([
    7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500,
  ]);
  const SCHEDULE_HORIZON_MS = 0x7fffffff;

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

  function configFromSearch(search) {
    const params = new URLSearchParams(search || "");
    const raw = {};
    Object.keys(DEFAULTS).forEach(function (key) {
      raw[key] = params.has(key) ? params.get(key) : DEFAULTS[key];
    });

    const startMs = parseTimestamp(raw.start, "start");
    const endMs = parseTimestamp(raw.end, "end");
    const freq = strictNumber(raw.freq, "freq");
    const bw = strictNumber(raw.bw, "bw");
    const sf = strictInteger(raw.sf, "sf");
    const cr = strictInteger(raw.cr, "cr");

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

    return Object.freeze({
      startMs: startMs,
      endMs: endMs,
      startEpoch: Math.floor(startMs / 1000),
      endEpoch: Math.floor(endMs / 1000),
      freq: freq,
      bw: bw,
      sf: sf,
      cr: cr,
      freqText: numberText(freq),
      bwText: numberText(bw),
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
      stockCancelDuring: "normalradio",
      companionCancelBefore:
        "get tempradioat2\ndel tempradioat2 all\nset radio2.cross auto",
      companionCancelDuring: "set tempradio2 off\nset radio2.cross auto",
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

  function formatPacific(milliseconds) {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Los_Angeles",
      weekday: "long",
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(new Date(milliseconds));
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

  function configuredUrl(config, baseUrl) {
    const url = new URL(baseUrl);
    url.search = "";
    url.searchParams.set("start", new Date(config.startMs).toISOString());
    url.searchParams.set("end", new Date(config.endMs).toISOString());
    url.searchParams.set("freq", config.freqText);
    url.searchParams.set("bw", config.bwText);
    url.searchParams.set("sf", String(config.sf));
    url.searchParams.set("cr", String(config.cr));
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

  function init(root) {
    let config;
    try {
      config = configFromSearch(global.location ? global.location.search : "");
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
    setText(root, '[data-role="start-pacific"]', formatPacific(config.startMs));
    setText(root, '[data-role="end-pacific"]', formatPacific(config.endMs));
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
    setCommand(root, "companion-cancel-before", staticCommands.companionCancelBefore);
    setCommand(root, "companion-cancel-during", staticCommands.companionCancelDuring);
    setCommand(
      root,
      "share-url",
      configuredUrl(config, global.location ? global.location.href : "https://example.invalid/")
    );

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
      const status = root.querySelector('[data-role="status"]');
      const schedule = scheduleAvailability(config, nowMs);
      const commands = commandsFor(config, nowMs);

      status.dataset.state = phase;
      if (phase === "before") {
        status.textContent = "Scheduled";
        setText(root, '[data-role="countdown-label"]', "Starts in");
        setText(root, '[data-role="countdown"]', formatCountdown(config.startMs - nowMs));
        setText(root, '[data-role="countdown-detail"]', "Do not use immediate TempRadio yet.");
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

      setCommand(root, "stock-now", commands.stockNow);
      setCommand(root, "companion-now", commands.companionNow);
      setCommandEnabled(root, "stock-now", phase === "active");
      setCommandEnabled(root, "companion-now", phase === "active");
      setText(
        root,
        '[data-role="stock-now-note"]',
        phase === "before"
          ? "Available when the test starts; schedule it now with Option 2."
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
    global.setInterval(render, 1000);
  }

  const api = Object.freeze({
    DEFAULTS: DEFAULTS,
    VALID_BANDWIDTHS: VALID_BANDWIDTHS,
    SCHEDULE_HORIZON_MS: SCHEDULE_HORIZON_MS,
    PresetTestError: PresetTestError,
    configFromSearch: configFromSearch,
    phaseAt: phaseAt,
    remainingMinutes: remainingMinutes,
    scheduleAvailability: scheduleAvailability,
    commandsFor: commandsFor,
    formatCountdown: formatCountdown,
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
