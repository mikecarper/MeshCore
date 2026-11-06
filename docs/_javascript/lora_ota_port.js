/* Bounded Web Serial open retries; this helper never retries an open port. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaPort = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
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
  return {openSerialPort};
});
