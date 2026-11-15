/* A dedicated worker prevents differential preparation from blocking the UI. */
'use strict';
const revision=self.location?.search||'';
importScripts(...['vendor/detools-js/bsdiff.js', 'vendor/detools-js/pack-size.js',
  'vendor/detools-js/crle.js', 'lora_ota_package.js', 'lora_ota_checks.js', 'lora_ota_delta.js'].map(path=>path+revision));
self.onmessage = async event => {
  const {id, base, target, blockSize, mode = 'sequential', allowSameVersion, runtimeStageCeiling, runtimeStorage, runtimeQspiApply, runtimeStorageBytes, runtimeHybridRam,searchWorkspaces,memorySize} = event.data || {};
  try {
    if (!(base instanceof ArrayBuffer) || !(target instanceof ArrayBuffer)) throw new Error('Two firmware images are required');
    self.postMessage({id, phase:'building'});
    if (!['sequential','in-place'].includes(mode)) throw new Error('Unsupported delta type');
    const build = mode === 'in-place' ? self.MeshCoreLoraDelta.buildInPlaceDelta : self.MeshCoreLoraDelta.buildSequentialDelta;
    const result = await build(new Uint8Array(base), new Uint8Array(target), {blockSize, allowSameVersion, runtimeStageCeiling, runtimeStorage, runtimeQspiApply, runtimeStorageBytes,runtimeHybridRam,searchWorkspaces,memorySize,
      progress:state=>self.postMessage({id,...state})});
    self.postMessage({id, phase:'complete', package:result.blob.buffer, payloadBytes:result.payload.length,
      baseHash:result.baseHash, targetHash:result.targetHash, memorySize:result.memorySize}, [result.blob.buffer]);
  } catch (error) { self.postMessage({id, phase:'error', error:String(error?.message || error),
    ...(error?.code==='OTA_DELTA_SPACE'?{code:error.code}:{})}); }
};
