'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const zlib = require('node:zlib');
const crypto = require('node:crypto');
const source = path.resolve(__dirname, '../docs/_javascript');
const api = require(path.join(source, 'lora_ota_zopfli.js'));

class SimulatedWorker {
  constructor() {
    this.terminated = false;
    const scope = {console, URL, Uint8Array, ArrayBuffer, WebAssembly, TextDecoder,
      setTimeout, clearTimeout, performance, location:{href:'http://example.test/_javascript/lora_ota_zopfli_worker.js'}};
    scope.self = scope;
    scope.fetch = async () => ({ok:true, arrayBuffer:async() => {
      const bytes=fs.readFileSync(path.join(source,'vendor/zopfli/zopfli.wasm'));
      return bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
    }});
    scope.postMessage = value => { if (!this.terminated) this.onmessage?.({data:value}); };
    scope.importScripts = name => vm.runInContext(fs.readFileSync(path.join(source,name),'utf8'), scope);
    vm.createContext(scope);
    vm.runInContext(fs.readFileSync(path.join(source,'lora_ota_zopfli_worker.js'),'utf8'),scope);
    this.scope = scope;
  }
  postMessage(data) { this.scope.onmessage({data}); }
  terminate() { this.terminated = true; }
}

test('real Zopfli worker produces raw DEFLATE which round trips varied OTA blocks', async () => {
  let worker;
  const encoder=api.create({workerFactory:() => worker=new SimulatedWorker()});
  const blocks=[Uint8Array.from({length:1024},(_,i)=>(i*i+i)%251),
    Uint8Array.from({length:2048},(_,i)=>i%29),Uint8Array.of(0),
    Uint8Array.from({length:513},(_,i)=>(Math.imul(i,33)^(i>>>2))&255)];
  try {
    let settings;
    for(const [index,raw] of blocks.entries()) {
      const encoded=await encoder.compress(raw);
      assert.deepEqual(new Uint8Array(zlib.inflateRawSync(encoded)),raw);
      if(index===0) {
        // Reference output from the pinned native Zopfli 0.4.3 at 1000
        // iterations; confirms encoder options, beyond just decoding.
        assert.equal(crypto.createHash('sha256').update(encoded).digest('hex'),
          '0d368599c9a618195546268d097fa1592693a4c8b068a88dfd3b70b95d2cca50');
        const original=worker.scope.Module._compress;
        worker.scope.Module._compress=(...args)=>{settings=args.slice(3);return original(...args);};
      }
    }
    assert.deepEqual(Array.from(settings),[2,0,0,1000,1,15]);
  } finally { encoder.dispose(); }
});

test('cancelling a compression stops its worker and permits a fresh request', async () => {
  const workers=[];
  const encoder=api.create({workerFactory:() => {
    const instance={postMessage(){},terminate(){this.terminated=true;}};workers.push(instance);return instance;
  }});
  const controller=new AbortController();
  const pending=encoder.compress(Uint8Array.of(1),{signal:controller.signal});
  controller.abort();
  await assert.rejects(pending,{name:'AbortError'});
  assert.equal(workers[0].terminated,true);
  const next=encoder.compress(Uint8Array.of(2));
  encoder.dispose();await assert.rejects(next,{name:'AbortError'});
  assert.equal(workers.length,2);
});

test('invalid inputs and unsupported workers fail explicitly', async () => {
  const encoder=api.create();
  await assert.rejects(encoder.compress(new Uint8Array()),/Invalid Zopfli block/);
  await assert.rejects(encoder.compress(new Uint8Array(65537)),/Invalid Zopfli block/);
  await assert.rejects(encoder.compress(Uint8Array.of(1)),/unavailable/);
});
