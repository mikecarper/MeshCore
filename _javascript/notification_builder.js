(function (global) {
  "use strict";
  const OUTPUTS = { vibration: 1, sound: 2, led: 4, screen: 8, gpio: 16 };
  const EXAMPLES = {
    find: { label: "Find my node", kind: "all", id: "", when: "disconnected", vibration: "200,200,200", led: "100,100,100,700", sound: "find:d=8,o=6,b=180:c,e,g,p", screen: "on", gpio: "off", repeat: "forever", gap: "1000", stop: "connected" },
    food: { label: "Your food truck order is ready", kind: "channel", id: "3", when: "any", vibration: "50,300,40,20,500", led: "100,100,100,500", sound: "order:d=8,o=5,b=180:c,e,g,4c6", screen: "on", gpio: "off", repeat: "forever", gap: "2000", stop: "button" },
    vip: { label: "VIP chat", kind: "contact", id: "", when: "disconnected", vibration: "100,100,300", led: "50,300,40,20,500", sound: "vip:d=8,o=5,b=180:c,e,g,4g", screen: "on", gpio: "off", repeat: "2", gap: "500", stop: "button" }
  };
  function pulse(text) {
    if (text === "off" || text === "inherit") return [];
    if (!/^\d+(,\d+){0,11}$/.test(text)) throw new Error("Use up to 12 millisecond durations separated by commas.");
    const values = text.split(",").map(Number);
    if (values.some(n => n < 1 || n > 60000)) throw new Error("Each pulse duration must be 1-60000 ms.");
    return values;
  }
  function melody(text) {
    if (text === "off" || text === "inherit") return [];
    if (text.length >= 64) throw new Error("The melody must be shorter than 64 ASCII characters.");
    const header = text.match(/^[A-Za-z0-9_]{1,12}:d=(1|2|4|8|16|32),o=([4-7]),b=(\d+):(.+)$/);
    if (!header) throw new Error("Use RTTTL: name:d=8,o=5,b=180:c,e,g,p (octaves 4-7).");
    const bpm = Number(header[3]);
    if (bpm < 25 || bpm > 900) throw new Error("Tempo must be 25-900 BPM.");
    const semitones = { c: 0, d: 2, e: 4, f: 5, g: 7, a: 9, b: 11 };
    let total = 0;
    return header[4].split(",").map(note => {
      const match = note.match(/^(1|2|4|8|16|32)?([a-gp])(#)?(\.)?([4-7])?(\.)?$/);
      if (!match || (match[3] && !"acdfg".includes(match[2])) || (match[4] && match[6])) throw new Error("Invalid note. Use c-g, a, b, p for rest, # for a sharp, and one dot.");
      let ms = Math.floor(Math.floor(60000 / bpm) * 4 / Number(match[1] || header[1]));
      if (match[4] || match[6]) ms += Math.floor(ms / 2);
      total += ms;
      if (total > 60000) throw new Error("A melody must finish within 60 seconds.");
      const midi = (Number(match[5] || header[2]) + 1) * 12 + (semitones[match[2]] || 0) + (match[3] ? 1 : 0);
      return { ms, hz: match[2] === "p" ? 0 : 440 * Math.pow(2, (midi - 69) / 12) };
    });
  }
  function selector(config) {
    let target = "all";
    if (config.kind === "contact" || config.kind === "room") {
      if (!/^[0-9a-fA-F]{64}$/.test(config.id)) throw new Error("Enter the contact or room server's full 64-digit public key.");
      target = (config.kind === "room" ? "room:" : "contact:") + config.id.toLowerCase();
    } else if (config.kind === "channel") {
      if (!/^(\d{1,3}|[0-9a-fA-F]{32})$/.test(config.id)) throw new Error("Enter a channel slot, or its 32-digit key.");
      target = "channel:" + config.id;
    } else if (config.kind !== "all") throw new Error("Unknown target.");
    if (!["any", "connected", "disconnected"].includes(config.when)) throw new Error("Unknown connection condition.");
    return target + (config.when === "any" ? "" : "@" + config.when);
  }
  function commands(config) {
    const target = selector(config), result = [];
    for (const name of ["vibration", "led"]) pulse(config[name]);
    melody(config.sound);
    if (!["inherit", "on", "off"].includes(config.screen)) throw new Error("Screen must be on, off, or inherit.");
    if (config.gpio !== "off" && config.gpio !== "inherit") {
      const match = config.gpio.match(/^(\d{1,2}):(.+)$/);
      if (!match || Number(match[1]) > 63) throw new Error("GPIO needs pin:pattern, for example 22:50,300,50.");
      pulse(match[2]);
    }
    if (config.repeat !== "forever" && (!/^\d+$/.test(config.repeat) || Number(config.repeat) < 1 || Number(config.repeat) > 65535)) throw new Error("Repeat must be 1-65535 or forever.");
    if (!/^\d+$/.test(config.gap) || Number(config.gap) < 1 || Number(config.gap) > 60000) throw new Error("Repeat gap must be 1-60000 ms.");
    if (!["button", "connected", "never"].includes(config.stop)) throw new Error("Unknown stop condition.");
    for (const name of Object.keys(OUTPUTS)) {
      if (!["off", "inherit"].includes(config[name])) result.push("set notify." + name + " on");
      result.push("set notify." + name + " " + target + " " + config[name]);
    }
    for (const name of ["repeat", "gap", "stop"]) result.push("set notify." + name + " " + target + " " + config[name]);
    if (config.remote && config.remote !== "inherit") {
      if (!["contact", "room"].includes(config.kind) || !["on", "off"].includes(config.remote)) throw new Error("Notification-string permission needs one contact or room's full key.");
      result.push("set notify.remote " + (config.kind === "room" ? "room:" : "contact:") + config.id.toLowerCase() + " " + config.remote);
    }
    for (const command of result) if (command.length + 4 > 175) throw new Error("This rule exceeds the device command size. Shorten the pattern or melody.");
    return result;
  }
  function notificationText(config) {
    const parts = [];
    for (const name of ["vibration", "sound", "led", "screen"]) if (config[name] !== "inherit") parts.push(name + "=" + config[name]);
    for (const name of ["repeat", "gap"]) parts.push(name + "=" + config[name]);
    const text = "!notify " + parts.join(" ");
    // Room forwarding reserves an author prefix and currently stores 150 text
    // characters. Reject before a truncated post could lose a field's value.
    const maximum = config.kind === "room" ? 150 : 159;
    if (text.length > maximum) throw new Error("Notification text exceeds " + maximum + " characters for " + (config.kind === "room" ? "a room post" : "a DM") + ". Shorten the patterns or melody.");
    return text;
  }
  function level(pattern, elapsed) {
    for (let i = 0; i < pattern.length; i++) { if (elapsed < pattern[i]) return i % 2 === 0; elapsed -= pattern[i]; }
    return false;
  }
  function encodeCommand(command, tag) {
    const text = new TextEncoder().encode(tag + "|" + command);
    if (!/^[A-Za-z0-9]{2}$/.test(tag) || text.length + 1 > 176) throw new Error("Command too long or invalid tag.");
    return Uint8Array.from([60, (text.length + 1) & 255, (text.length + 1) >> 8, 0x42, ...text]);
  }
  class FrameDecoder {
    constructor() { this.buffer = []; }
    push(chunk) {
      this.buffer.push(...chunk);
      if (this.buffer.length > 4096) this.buffer = this.buffer.slice(-4096);
      const frames = [];
      while (this.buffer.length) {
        const head = this.buffer.indexOf(62);
        if (head < 0) { this.buffer = []; break; }
        if (head) this.buffer.splice(0, head);
        if (this.buffer.length < 3) break;
        const size = this.buffer[1] + this.buffer[2] * 256;
        if (!size || size > 176) { this.buffer.shift(); continue; }
        if (this.buffer.length < size + 3) break;
        frames.push(Uint8Array.from(this.buffer.splice(0, size + 3).slice(3)));
      }
      return frames;
    }
  }
  class SerialClient {
    constructor(port) { this.port = port; this.decoder = new FrameDecoder(); this.sequence = 0; this.pending = null; }
    async open() {
      await this.port.open({ baudRate: 115200 });
      await this.port.setSignals({ dataTerminalReady: true, requestToSend: false });
      this.reader = this.port.readable.getReader(); this.writer = this.port.writable.getWriter();
      this.reading = this.readLoop();
    }
    async readLoop() {
      try {
        while (true) {
          const { value, done } = await this.reader.read(); if (done) break;
          for (const frame of this.decoder.push(value)) {
            if (frame[0] !== 0x1d || !this.pending) continue;
            const text = new TextDecoder().decode(frame.slice(1));
            if (text.startsWith(this.pending.tag + "|")) this.pending.resolve(text.slice(3));
          }
        }
      } catch (error) { if (this.pending) this.pending.reject(error); }
      finally { if (this.pending) this.pending.reject(new Error("USB connection closed.")); }
    }
    async command(text) {
      if (this.pending) throw new Error("Wait for the current command.");
      const tag = (this.sequence++ % 1296).toString(36).padStart(2, "0");
      let timeout;
      const response = new Promise((resolve, reject) => { this.pending = { tag, resolve, reject }; timeout = setTimeout(() => reject(new Error("Device did not reply. Use Binary Companion USB mode and updated firmware.")), 5000); });
      try { const results = await Promise.all([this.writer.write(encodeCommand(text, tag)), response]); return results[1]; }
      finally { clearTimeout(timeout); this.pending = null; }
    }
    async close() {
      if (this.reader) { await this.reader.cancel(); await this.reading; this.reader.releaseLock(); }
      if (this.writer) this.writer.releaseLock();
      await this.port.close();
    }
  }
  function init(root) {
    if (root.dataset.ready) return; root.dataset.ready = "true";
    const find = role => root.querySelector('[data-role="' + role + '"]');
    const form = find("form"), error = find("error"), output = find("commands"), log = find("device-log");
    let client = null, supported = null, previewTimer = null, audio = null, oscillators = [];
    const config = () => Object.fromEntries(new FormData(form));
    function render() {
      try {
        output.textContent = commands(config()).join("\n"); error.textContent = "";
        try { find("dm").textContent = notificationText(config()); }
        catch (e) { find("dm").textContent = e.message; }
      }
      catch (e) { output.textContent = ""; find("dm").textContent = ""; error.textContent = e.message; }
    }
    function preset(name) {
      const values = { remote: "inherit", ...EXAMPLES[name] };
      for (const [key, value] of Object.entries(values)) if (form.elements[key]) form.elements[key].value = value;
      render();
    }
    function stopPreview() {
      clearInterval(previewTimer); previewTimer = null;
      for (const oscillator of oscillators) { try { oscillator.stop(); } catch (_) {} } oscillators = [];
      for (const item of root.querySelectorAll("[data-indicator]")) item.dataset.on = "false";
    }
    function preview() {
      stopPreview(); const c = config(); commands(c);
      const pulses = { vibration: pulse(c.vibration), led: pulse(c.led), gpio: pulse(c.gpio.includes(":") ? c.gpio.split(":")[1] : c.gpio) };
      const notes = melody(c.sound), tuneMs = notes.reduce((n, item) => n + item.ms, 0);
      const duration = Math.max(tuneMs, ...Object.values(pulses).map(list => list.reduce((a, b) => a + b, 0))) || 1000;
      // Preview one cycle. Never creates an unbounded oscillator or vibration loop.
      if (notes.length && (global.AudioContext || global.webkitAudioContext)) {
        audio = audio || new (global.AudioContext || global.webkitAudioContext)(); audio.resume();
        let offset = 0;
        for (const note of notes) {
          if (note.hz) {
            const oscillator = audio.createOscillator(), gain = audio.createGain();
            oscillator.type = "square"; oscillator.frequency.value = note.hz; gain.gain.value = 0.03;
            oscillator.connect(gain); gain.connect(audio.destination);
            oscillator.start(audio.currentTime + offset / 1000); oscillator.stop(audio.currentTime + (offset + note.ms) / 1000);
            oscillators.push(oscillator);
          }
          offset += note.ms;
        }
      }
      const started = performance.now();
      previewTimer = setInterval(() => {
        const elapsed = performance.now() - started;
        for (const name of Object.keys(pulses)) root.querySelector('[data-indicator="' + name + '"]').dataset.on = String(level(pulses[name], elapsed));
        root.querySelector('[data-indicator="screen"]').dataset.on = String(c.screen === "on");
        find("preview-time").textContent = Math.min(Math.round(elapsed), duration) + " / " + duration + " ms";
        if (elapsed >= duration) stopPreview();
      }, 10);
    }
    const append = text => { log.textContent += text + "\n"; log.scrollTop = log.scrollHeight; };
    async function run(text) {
      if (!client) throw new Error("Connect a device over USB first.");
      append("> " + text); const reply = await client.command(text); append(reply);
      if (/^(Error|ERR|Unknown command|Not Supported)/.test(reply)) throw new Error(reply);
      return reply;
    }
    async function act(action) {
      error.textContent = "";
      const buttons = root.querySelectorAll("button[data-action]"); buttons.forEach(b => b.disabled = true);
      try {
        if (action === "copy") { await navigator.clipboard.writeText(commands(config()).join("\n"));find("copy-status").textContent = "Copied"; }
        if (action === "preview") preview();
        if (action === "preview-stop") stopPreview();
        if (action === "connect") {
          if (!navigator.serial) throw new Error("USB testing requires Chrome or Edge with Web Serial support.");
          if (client) { await client.close(); client = null; }
          const port = await navigator.serial.requestPort();client = new SerialClient(port);await client.open();
          const state = await run("get notify");
          const match = state.match(/supported=(\d+)/);if (!match) throw new Error("This firmware does not expose notification settings.");
          supported = Number(match[1]);
          find("device-status").textContent = "USB connected. Available alerts: " + Object.keys(OUTPUTS).filter(k => supported & OUTPUTS[k]).join(", ");
          await run("get notify.gpio.pins");
        }
        if (action === "disconnect" && client) { await client.close();client = null;supported = null;find("device-status").textContent = "Device disconnected"; }
        if (action === "apply" || action === "apply-test") {
          const c = config(), list = commands(c);
          if (!client || supported === null) throw new Error("Connect updated Companion firmware first.");
          for (const name of Object.keys(OUTPUTS)) if (!["off", "inherit"].includes(c[name]) && !(supported & OUTPUTS[name])) throw new Error(name + " is unavailable on this device. Choose off or inherit.");
          for (const text of list) await run(text);
          if (action === "apply-test") await run("notify.test " + selector(c));
        }
        if (action === "test") await run("notify.test " + selector(config()));
        if (action === "stop") await run("notify.stop");
        if (action === "delete") await run("notify.delete " + selector(config()));
      } catch (e) { error.textContent = e.message; }
      finally { buttons.forEach(b => b.disabled = false); }
    }
    form.addEventListener("input", render);
    root.addEventListener("click", event => {
      const example = event.target.closest("[data-example]"); if (example) preset(example.dataset.example);
      const button = event.target.closest("[data-action]"); if (button) act(button.dataset.action);
    });
    global.addEventListener("pagehide", stopPreview);preset("food");
  }
  const api = { pulse, melody, selector, commands, notificationText, level, encodeCommand, FrameDecoder, SerialClient, EXAMPLES, init };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.MeshCoreNotifications = api;
  if (typeof document !== "undefined") {
    const boot = () => document.querySelectorAll("[data-notification-builder]").forEach(init);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
    if (typeof document$ !== "undefined") document$.subscribe(boot);
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
