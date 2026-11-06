/* A dedicated worker prevents differential preparation from blocking the UI. */
'use strict';
importScripts('vendor/detools-js/bsdiff.js', 'vendor/detools-js/pack-size.js',
  'vendor/detools-js/crle.js', 'lora_ota_package.js', 'lora_ota_delta.js');
self.onmessage = async event => {
  const {id, base, target, blockSize, mode = 'sequential', runtimeStageCeiling, runtimeStorage, runtimeQspiApply, runtimeStorageBytes} = event.data || {};
  try {
    if (!(base instanceof ArrayBuffer) || !(target instanceof ArrayBuffer)) throw new Error('Two firmware images are required');
    self.postMessage({id, phase:'building'});
    if (!['sequential','in-place'].includes(mode)) throw new Error('Unsupported delta type');
    const build = mode === 'in-place' ? self.MeshCoreLoraDelta.buildInPlaceDelta : self.MeshCoreLoraDelta.buildSequentialDelta;
    const result = await build(new Uint8Array(base), new Uint8Array(target), {blockSize, runtimeStageCeiling, runtimeStorage, runtimeQspiApply, runtimeStorageBytes});
    self.postMessage({id, phase:'complete', package:result.blob.buffer, payloadBytes:result.payload.length,
      baseHash:result.baseHash, targetHash:result.targetHash, memorySize:result.memorySize}, [result.blob.buffer]);
  } catch (error) { self.postMessage({id, phase:'error', error:String(error?.message || error)}); }
};
