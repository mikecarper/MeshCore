/* Run the local Zopfli encoder outside the updater's main browser thread. */
'use strict';

let encoderReady;
async function encoder() {
  if (!encoderReady) encoderReady = (async () => {
    const response = await fetch(new URL('vendor/zopfli/zopfli.wasm', self.location.href));
    if (!response.ok) throw new Error('Could not load the local Zopfli encoder.');
    self.__meshcoreZopfliWasm = new Uint8Array(await response.arrayBuffer());
    importScripts('vendor/zopfli/libzopfli.js');
    delete self.__meshcoreZopfliWasm;
    if (!self.Module) throw new Error('Could not initialize the local Zopfli encoder.');
    await new Promise((resolve, reject) => {
      if (self.Module.calledRun) return resolve();
      self.Module.onRuntimeInitialized = resolve;
      self.Module.onAbort = reason => reject(new Error('Zopfli initialization failed: ' + reason));
    });
    return self.Module;
  })();
  return encoderReady;
}

function deflate(z, input) {
  let inputPtr = 0, output = 0, outputPtr = 0;
  try {
    inputPtr = z._malloc(input.byteLength);
    if (!inputPtr) throw new Error('Not enough memory for Zopfli.');
    z.HEAPU8.set(input, inputPtr);
    output = z._createZopfliJsOutput();
    if (!output) throw new Error('Not enough memory for Zopfli output.');
    // Format 2 is raw DEFLATE; match the native host seeder's exact Zopfli
    // settings: 1000 iterations, block splitting enabled, max 15 splits.
    z._compress(inputPtr, input.byteLength, output, 2, 0, 0, 1000, 1, 15);
    outputPtr = z._getBuffer(output);
    const size = z._getBufferSize(output);
    if (!outputPtr || !size || outputPtr + size > z.HEAPU8.length)
      throw new Error('Zopfli returned an invalid compressed block.');
    return z.HEAPU8.slice(outputPtr, outputPtr + size);
  } finally {
    if (outputPtr) z._deallocate(outputPtr);
    if (output) z._deallocate(output);
    if (inputPtr) z._deallocate(inputPtr);
  }
}

self.onmessage = async event => {
  const {id, data} = event.data || {};
  try {
    if (!Number.isSafeInteger(id) || !(data instanceof ArrayBuffer) || !data.byteLength || data.byteLength > 65536)
      throw new Error('Invalid Zopfli block.');
    const output = deflate(await encoder(), new Uint8Array(data));
    self.postMessage({id, data:output.buffer}, [output.buffer]);
  } catch (error) {
    self.postMessage({id, error:String(error?.message || error)});
  }
};
