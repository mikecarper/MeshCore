/* Serial-like byte streams over the Companion's local Wi-Fi WebSocket. */
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

  // Restrict direct connections to literal private IPv4 addresses: a public
  // endpoint or a hostname that resolves elsewhere must not receive commands.
  function privateAddress(value) {
    const parts=String(value).split('.');
    if(parts.length!==4||parts.some(p=>!/^(?:0|[1-9]\d{0,2})$/.test(p)||Number(p)>255))return false;
    const [a,b,c,d]=parts.map(Number);
    return d>0&&d<255&&(a===10 || (a===172&&b>=16&&b<=31) || (a===192&&b===168));
  }

  // Existing localhost capability URLs remain supported for older installs.
  // The direct endpoint is fixed by the firmware; users enter only its IP.
  function validateUrl(value) {
    value=String(value||'').trim();
    if(privateAddress(value))return `ws://${value}:5005/ota`;
    let url;
    try { url = new URL(value); } catch (_) { throw new Error('Enter the Companion’s private IPv4 address.'); }
    const noExtras=!url.username&&!url.password&&!url.search&&!url.hash;
    const direct=url.protocol==='ws:'&&privateAddress(url.hostname)&&url.port==='5005'&&url.pathname==='/ota';
    const legacy=url.protocol==='ws:'&&url.hostname==='127.0.0.1'&&url.port==='8765'&&/^\/[A-Za-z0-9_-]{16,256}$/.test(url.pathname);
    if(!noExtras||(!direct&&!legacy))throw new Error('Enter the Companion’s private IPv4 address.');
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
      catch (_) { this.opening = false; throw new Error('Wi-Fi connection failed.'); }
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
      const closeSocket = () => { expectedClose = true; fail('Wi-Fi connection closed.'); };
      this.closeSocket = closeSocket;
      this.readable = new ReadableStream({
        start(controller) { readController = controller; },
        cancel() { closeSocket(); }
      }, {highWaterMark: MAX_BUFFER, size: chunk => chunk.byteLength});
      this.writable = new WritableStream({
        start(controller) { writeController = controller; },
        async write(chunk) {
          if (!(chunk instanceof Uint8Array)) throw new TypeError('Wi-Fi connection accepts byte arrays only.');
          if (chunk.byteLength > MAX_MESSAGE) throw new RangeError('Wi-Fi write is too large.');
          const deadline = Date.now() + thisDrainTimeout;
          while (socket.bufferedAmount + chunk.byteLength > MAX_BUFFER) {
            if (terminated || socket.readyState !== 1) throw new Error('Wi-Fi disconnected.');
            if (Date.now() >= deadline) { fail('Wi-Fi write timed out.'); throw new Error('Wi-Fi write timed out.'); }
            await new Promise(resolve => setTimeout(resolve, 20));
          }
          if (terminated || socket.readyState !== 1) throw new Error('Wi-Fi disconnected.');
          try { socket.send(chunk); } catch (_) { fail('Wi-Fi write failed.'); throw new Error('Wi-Fi write failed.'); }
        },
        close() { closeSocket(); },
        abort() { closeSocket(); }
      });
      const thisDrainTimeout = this.drainTimeout;
      socket.onmessage = event => {
        if (terminated) return;
        const data = event.data instanceof ArrayBuffer ? new Uint8Array(event.data) : null;
        if (!data || data.byteLength > MAX_BUFFER || readController.desiredSize < data.byteLength) {
          fail('Wi-Fi source sent invalid or excessive data.'); return;
        }
        try { readController.enqueue(data); } catch (_) { fail('Wi-Fi receive failed.'); }
      };
      socket.onclose = () => fail('Wi-Fi disconnected.');
      socket.onerror = () => fail('Wi-Fi connection failed.');
      try {
        await new Promise((resolve, reject) => {
          let done = false;
          const finish = error => {
            if (done) return;
            done = true; clearTimeout(timer);
            if (error) reject(error); else resolve();
          };
          const timer = setTimeout(() => { finish(new Error('Wi-Fi connection timed out.')); fail('Wi-Fi connection timed out.'); }, this.openTimeout);
          socket.onopen = () => { if (terminated) finish(new Error('Wi-Fi disconnected.')); else finish(); };
          socket.onerror = () => { finish(new Error('Wi-Fi connection failed.')); fail('Wi-Fi connection failed.'); };
          socket.onclose = () => { fail('Wi-Fi disconnected.'); finish(new Error('Wi-Fi disconnected.')); };
        });
        if (terminated) throw new Error('Wi-Fi disconnected.');
        this.connected = true;
      } catch (error) {
        // Opening failure is a fresh socket, not a persistent USB owner.
        expectedClose = true; fail('Wi-Fi disconnected.');
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

  return {create: (url, options) => new WifiPort(url, options), validateUrl, privateAddress};
});
