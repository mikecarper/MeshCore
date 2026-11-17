/* Cancellable, worker-backed Zopfli raw-DEFLATE encoder for LoRa OTA. */
(function(root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaZopfli = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function(root) {
  'use strict';
  const script = root.document?.currentScript?.src;
  const workerUrl = script ? new URL('lora_ota_zopfli_worker.js?v=1', script).href : null;
  const aborted = () => typeof DOMException === 'function'
    ? new DOMException('Zopfli compression canceled.', 'AbortError')
    : Object.assign(new Error('Zopfli compression canceled.'), {name:'AbortError'});
  function create(options = {}) {
    let worker = null, sequence = 0;
    const pending = new Map();
    function stop(error) {
      const old = worker; worker = null;
      if (old) old.terminate();
      for (const request of pending.values()) {
        request.signal?.removeEventListener('abort', request.abort);
        request.reject(error);
      }
      pending.clear();
    }
    function ensureWorker() {
      if (worker) return worker;
      const factory = options.workerFactory || (url => new Worker(url));
      if (!options.workerFactory && (!workerUrl || typeof root.Worker !== 'function'))
        throw new Error('Zopfli compression is unavailable in this browser.');
      const instance = factory(options.workerUrl || workerUrl);
      worker = instance;
      instance.onmessage = event => {
        if (worker !== instance) return;
        const {id, data, error} = event.data || {}, request = pending.get(id);
        if (!request) return stop(new Error('Zopfli worker returned an unexpected response.'));
        if (error) return stop(new Error('Zopfli compression failed: ' + String(error)));
        if (!(data instanceof ArrayBuffer) || !data.byteLength)
          return stop(new Error('Zopfli returned an invalid compressed block.'));
        pending.delete(id);
        request.signal?.removeEventListener('abort', request.abort);
        request.resolve(new Uint8Array(data));
      };
      instance.onerror = () => { if (worker === instance) stop(new Error('Zopfli worker failed.')); };
      instance.onmessageerror = () => { if (worker === instance) stop(new Error('Zopfli worker returned invalid data.')); };
      return instance;
    }
    function compress(raw, {signal} = {}) {
      if (!(raw instanceof Uint8Array) || !raw.byteLength || raw.byteLength > 65536)
        return Promise.reject(new Error('Invalid Zopfli block.'));
      if (signal?.aborted) return Promise.reject(aborted());
      let instance;
      try { instance = ensureWorker(); } catch (error) { return Promise.reject(error); }
      const bytes = raw.slice();
      return new Promise((resolve, reject) => {
        const id = ++sequence;
        const abort = () => stop(aborted());
        pending.set(id, {resolve, reject, signal, abort});
        signal?.addEventListener('abort', abort, {once:true});
        if (signal?.aborted) return abort();
        try { instance.postMessage({id, data:bytes.buffer}, [bytes.buffer]); }
        catch (error) { stop(error); }
      });
    }
    return {compress, dispose:() => stop(aborted())};
  }
  const shared = create();
  return {compress:shared.compress, dispose:shared.dispose, create};
});
