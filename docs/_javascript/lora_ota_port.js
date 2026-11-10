/* Bounded Web Serial open retries; this helper never retries an open port. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaPort = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const detectedSources = new WeakMap();
  const encoder = new TextEncoder(), decoder = new TextDecoder();
  function abortError() {
    if (typeof DOMException === 'function') return new DOMException('Connection canceled', 'AbortError');
    const error = new Error('Connection canceled'); error.name = 'AbortError'; return error;
  }
  function wait(ms, signal) {
    if (signal?.aborted) return Promise.reject(abortError());
    return new Promise((resolve, reject) => {
      const done = () => { signal?.removeEventListener('abort', cancel); resolve(); };
      const timer = setTimeout(done, ms);
      const cancel = () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); reject(abortError()); };
      signal?.addEventListener('abort', cancel, {once:true});
      if (signal?.aborted) cancel();
    });
  }
  function openError(cause, attempts) {
    const error = new Error(cause?.message || 'Failed to open serial port');
    error.name = cause?.name || 'Error'; error.cause = cause;
    error.usbOpenFailed = true; error.openAttempts = attempts;
    return error;
  }
  async function requestSerialPort(serial, {signal} = {}) {
    if (signal?.aborted) throw abortError();
    if (!serial || typeof serial.requestPort !== 'function') throw new TypeError('Web Serial is unavailable');
    // Reuse the sole connected radio the user has already authorized for
    // this site. Multiple authorized radios still require an explicit choice.
    const ports = typeof serial.getPorts === 'function' ? await serial.getPorts() : [];
    if (signal?.aborted) throw abortError();
    return ports.length === 1 ? ports[0] : serial.requestPort();
  }
  async function openSerialPort(port, {signal, onAttempt, onSuccess, onAcquired, onReleased, delayMs = 1000} = {}) {
    if (!port || typeof port.open !== 'function') throw new TypeError('A serial port is required');
    if (!Number.isFinite(delayMs) || delayMs < 0 || delayMs > 10000) throw new RangeError('Invalid serial retry delay');
    const maxAttempts = 3;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      if (signal?.aborted) throw abortError();
      try { onAttempt?.(attempt, maxAttempts); } catch (_) { /* Status reporting is optional. */ }
      if (signal?.aborted) throw abortError();
      try {
        await port.open({baudRate:115200, bufferSize:4096});
      } catch (cause) {
        if (signal?.aborted) throw abortError();
        if (cause?.name !== 'NetworkError' || port.connected === false || attempt === maxAttempts)
          throw openError(cause, attempt);
        await wait(delayMs, signal);
        continue;
      }
      // Tell the owner before any cancellation cleanup can fail. In that
      // case its normal close path still knows it owns this handle.
      onAcquired?.();
      if (signal?.aborted) {
        // open() has no abort argument. If it succeeded after cancellation,
        // release only the handle acquired by this attempt before returning.
        try { await port.close(); onReleased?.(); } catch (_) {}
        throw abortError();
      }
      try { onSuccess?.(); } catch (_) { /* Status reporting is optional. */ }
      return;
    }
  }
  // Probe only local identity, without APP_START, radio changes, or taking a
  // firmware folder/peer session. Keep the same open handle for the selected
  // transport: closing/reopening some USB radios would reset the board.
  async function detectSource(port, options = {}) {
    const {openSettle = 750, binaryTimeout = 2000, cliTimeout = 3000} = options;
    const signal = options.signal || options.openSignal;
    for (const value of [openSettle, binaryTimeout, cliTimeout])
      if (!Number.isFinite(value) || value < 0 || value > 15000) throw new RangeError('Invalid source probe timeout');
    let opened = false, reader, writer, pendingRead;
    let bytes = [], phase = 'passive', probeStart = 0;
    const read = () => pendingRead ||= reader.read();
    async function cleanup() {
      if (reader) { try { await reader.cancel(); } catch (_) {} try { reader.releaseLock(); } catch (_) {} reader = null; }
      if (writer) { try { writer.abort?.().catch(() => {}); } catch (_) {} try { writer.releaseLock(); } catch (_) {} writer = null; }
      if (opened) { try { await port.close(); opened = false; } catch (_) {} }
    }
    function inspect() {
      let companion = null;
      for (let i = 0; i < bytes.length; i++) {
        if (bytes[i] === 62 && i + 3 <= bytes.length) {
          const n = bytes[i + 1] | (bytes[i + 2] << 8), end = i + 3 + n;
          if (n > 0 && n <= 300) {
            if (end > bytes.length) break; // Do not parse a partial binary body as another protocol.
            if (phase !== 'passive' && i >= probeStart && bytes[i + 3] === 13 && n >= 60)
              companion = {kind:'companion', initialBytes:Uint8Array.from([...bytes.slice(0, i), ...bytes.slice(end)])};
            i = end - 1;
            continue;
          }
        }
        // Native host-folder requests are a different USB owner. Do not send
        // its off command, answer its requests, or let a transport adopt it.
        if (i + 2 < bytes.length && bytes[i] === 77 && bytes[i + 1] === 83 && bytes[i + 2] >= 1 && bytes[i + 2] <= 9)
          throw new Error('This USB radio already has an active firmware source; finish it before connecting.');
      }
      if (companion) return companion; // Accept a late DEVICE_QUERY response during the CLI fallback too.
      if (phase === 'cli') {
        const text = decoder.decode(Uint8Array.from(bytes));
        const identity = /(?:^|[\r\n])get public\.key\r?\n\s*->\s*([a-f0-9]{64})\r?\n/i.exec(text);
        if (identity) return {kind:'repeater', sourceKey:identity[1].toLowerCase(), initialBytes:new Uint8Array()};
      }
      return null;
    }
    async function receiveUntil(deadline) {
      for (;;) {
        if (signal?.aborted) throw abortError();
        const found = inspect(); if (found) return found;
        const remaining = deadline - Date.now(); if (remaining <= 0) return null;
        let timer, abort;
        const timeout = new Promise((resolve, reject) => {
          timer = setTimeout(() => resolve(null), remaining);
          abort = () => reject(abortError());
          signal?.addEventListener('abort', abort, {once:true});
        });
        let result;
        try { result = await Promise.race([read(), timeout]); }
        finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
        if (result === null) return null; // Retain the pending read for the next phase.
        pendingRead = null;
        if (result.done) throw new Error('The USB radio disconnected during identification.');
        bytes.push(...result.value);
        if (bytes.length > 16384) throw new Error('USB identification received too much data; stop logging before connecting.');
      }
    }
    async function probeWrite(value, timeoutMs) {
      if (signal?.aborted) throw abortError();
      let timer, abort;
      const timeout = new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error('USB identification write timed out. Reconnect the radio.')), timeoutMs);
        abort = () => reject(abortError());
        signal?.addEventListener('abort', abort, {once:true});
      });
      try { await Promise.race([writer.write(value), timeout]); }
      finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
    }
    try {
      await openSerialPort(port, {...options, signal, onAttempt:options.onAttempt || options.onOpenAttempt,
        onSuccess:options.onSuccess || options.onOpenSuccess, delayMs:options.delayMs ?? options.openRetryDelayMs,
        onAcquired:() => { opened = true; options.onAcquired?.(); },
        onReleased:() => { opened = false; options.onReleased?.(); }});
      reader = port.readable.getReader(); writer = port.writable.getWriter();
      await receiveUntil(Date.now() + openSettle);
      if (signal?.aborted) throw abortError();
      phase = 'binary';
      probeStart = bytes.length;
      await probeWrite(Uint8Array.of(60, 2, 0, 22, 4), binaryTimeout || 1); // DEVICE_QUERY, local identity only.
      let found = await receiveUntil(Date.now() + binaryTimeout);
      if (!found) {
        phase = 'cli';
        // Finish the harmless binary probe's partial ASCII line before the
        // read-only CLI request. It can only produce an unknown-command reply.
        await probeWrite(encoder.encode('\r\nget public.key\r\n'), cliTimeout || 1);
        found = await receiveUntil(Date.now() + cliTimeout);
      }
      if (!found) throw new Error('Cannot identify this USB radio. Choose Companion or Repeater in connection options.');
      if (signal?.aborted) throw abortError();
      const token = {kind:found.kind, close:async () => {
        if (!detectedSources.has(token)) return;
        detectedSources.delete(token); await cleanup();
      }};
      detectedSources.set(token, {port, reader, writer, ...found});
      return token;
    } catch (error) { await cleanup(); throw error; }
  }
  function adoptDetectedSource(owner, kind) {
    const token = owner.options?.detectedSource;
    if (!token) return false;
    const found = detectedSources.get(token);
    if (!found || found.port !== owner.port || found.kind !== kind)
      throw new Error('USB source identification is no longer valid. Connect the radio again.');
    detectedSources.delete(token);
    owner.portOpened = true; owner.reader = found.reader; owner.writer = found.writer;
    owner.sourceProbeBytes = found.initialBytes;
    owner.detectedSourceKey = found.sourceKey || null;
    return true;
  }
  return {openSerialPort, requestSerialPort, detectSource, adoptDetectedSource};
});
