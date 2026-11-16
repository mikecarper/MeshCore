/* Serial-like byte streams over the locally authorized Wi-Fi bridge. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaWifiPort = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const MAX_BUFFER = 1024 * 1024;
  const MAX_MESSAGE = 65536;
  const OPEN_TIMEOUT = 10000;
  const DRAIN_TIMEOUT = 15000;

  // Only the local bridge is trusted to receive privileged Companion traffic.
  // The unguessable path is a capability; do not log or include it in errors.
  function validateUrl(value) {
    let url;
    try { url = new URL(value); } catch (_) { throw new Error('Enter the complete local Wi-Fi bridge address.'); }
    if (url.protocol !== 'ws:' || url.hostname !== '127.0.0.1' || url.port !== '8765' ||
        url.username || url.password || url.search || url.hash ||
        !/^\/[A-Za-z0-9_-]{16,256}$/.test(url.pathname))
      throw new Error('Enter the complete local Wi-Fi bridge address.');
    return url.href;
  }

  class WifiPort {
    constructor(url, {WebSocket: Socket = globalThis.WebSocket, openTimeout = OPEN_TIMEOUT,
                      drainTimeout = DRAIN_TIMEOUT} = {}) {
      this.url = validateUrl(url);
      if (typeof Socket !== 'function') throw new Error('This browser does not support WebSocket connections.');
      if (!Number.isFinite(openTimeout) || openTimeout <= 0 ||
          !Number.isFinite(drainTimeout) || drainTimeout <= 0) throw new RangeError('Invalid bridge timeout.');
      this.Socket = Socket;
      this.openTimeout = openTimeout;
      this.drainTimeout = drainTimeout;
      this.socket = null;
      this.closeSocket = null;
      this.readable = null;
      this.writable = null;
      this.connected = false;
      this.opening = false;
    }

    async open() {
      if (this.socket || this.opening) throw new Error('Wi-Fi bridge is already open.');
      this.opening = true;
      let socket;
      try { socket = new this.Socket(this.url); }
      catch (_) { this.opening = false; throw new Error('Wi-Fi bridge connection failed.'); }
      this.socket = socket;
      socket.binaryType = 'arraybuffer';
      let readController, writeController, expectedClose = false, terminated = false;
      const fail = message => {
        if (terminated) return;
        terminated = true;
        if (this.socket === socket) this.connected = false;
        const error = new Error(message);
        if (!expectedClose) {
          try { readController?.error(error); } catch (_) {}
          try { writeController?.error(error); } catch (_) {}
        } else {
          try { readController?.close(); } catch (_) {}
        }
        try { socket.close(); } catch (_) {}
      };
      const closeSocket = () => { expectedClose = true; fail('Wi-Fi bridge closed.'); };
      this.closeSocket = closeSocket;
      this.readable = new ReadableStream({
        start(controller) { readController = controller; },
        cancel() { closeSocket(); }
      }, {highWaterMark: MAX_BUFFER, size: chunk => chunk.byteLength});
      this.writable = new WritableStream({
        start(controller) { writeController = controller; },
        async write(chunk) {
          if (!(chunk instanceof Uint8Array)) throw new TypeError('Wi-Fi bridge accepts byte arrays only.');
          if (chunk.byteLength > MAX_MESSAGE) throw new RangeError('Wi-Fi bridge write is too large.');
          const deadline = Date.now() + thisDrainTimeout;
          while (socket.bufferedAmount + chunk.byteLength > MAX_BUFFER) {
            if (terminated || socket.readyState !== 1) throw new Error('Wi-Fi bridge disconnected.');
            if (Date.now() >= deadline) { fail('Wi-Fi bridge write timed out.'); throw new Error('Wi-Fi bridge write timed out.'); }
            await new Promise(resolve => setTimeout(resolve, 20));
          }
          if (terminated || socket.readyState !== 1) throw new Error('Wi-Fi bridge disconnected.');
          try { socket.send(chunk); } catch (_) { fail('Wi-Fi bridge write failed.'); throw new Error('Wi-Fi bridge write failed.'); }
        },
        close() { closeSocket(); },
        abort() { closeSocket(); }
      });
      const thisDrainTimeout = this.drainTimeout;
      socket.onmessage = event => {
        if (terminated) return;
        const data = event.data instanceof ArrayBuffer ? new Uint8Array(event.data) : null;
        if (!data || data.byteLength > MAX_BUFFER || readController.desiredSize < data.byteLength) {
          fail('Wi-Fi bridge sent invalid or excessive data.'); return;
        }
        try { readController.enqueue(data); } catch (_) { fail('Wi-Fi bridge receive failed.'); }
      };
      socket.onclose = () => fail('Wi-Fi bridge disconnected.');
      socket.onerror = () => fail('Wi-Fi bridge connection failed.');
      try {
        await new Promise((resolve, reject) => {
          let done = false;
          const finish = error => {
            if (done) return;
            done = true; clearTimeout(timer);
            if (error) reject(error); else resolve();
          };
          const timer = setTimeout(() => { finish(new Error('Wi-Fi bridge connection timed out.')); fail('Wi-Fi bridge connection timed out.'); }, this.openTimeout);
          socket.onopen = () => { if (terminated) finish(new Error('Wi-Fi bridge disconnected.')); else finish(); };
          socket.onerror = () => { finish(new Error('Wi-Fi bridge connection failed.')); fail('Wi-Fi bridge connection failed.'); };
          socket.onclose = () => { fail('Wi-Fi bridge disconnected.'); finish(new Error('Wi-Fi bridge disconnected.')); };
        });
        if (terminated) throw new Error('Wi-Fi bridge disconnected.');
        this.connected = true;
      } catch (error) {
        // Opening failure is a fresh socket, not a persistent USB owner.
        expectedClose = true; fail('Wi-Fi bridge disconnected.');
        this.socket = this.closeSocket = this.readable = this.writable = null;
        throw error;
      } finally {
        this.opening = false;
      }
    }

    async close() {
      const socket = this.socket;
      if (this.closeSocket) this.closeSocket();
      this.socket = null;
      this.closeSocket = null;
      this.connected = false;
      if (socket) try { socket.close(); } catch (_) {}
      this.readable = this.writable = null;
    }
  }

  return {create: (url, options) => new WifiPort(url, options), validateUrl};
});
