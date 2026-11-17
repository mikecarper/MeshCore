const test=require('node:test');
const assert=require('node:assert/strict');
const zlib=require('node:zlib');
const compression=require('../docs/_javascript/lora_ota_compression.js');
const {DirectSession}=require('../docs/_javascript/lora_ota_session.js');
const {Source}=require('../docs/_javascript/lora_ota_companion_source.js');
const {SeederData}=require('../docs/_javascript/lora_ota_serial.js');

test('prepares every unique package block before serving and returns compatible data',async()=>{
  const raw=Uint8Array.from([...Array(8).fill(7),...Array(8).fill(7),...Array(8).fill(3)]), calls=[];
  const engine={async compress(bytes){calls.push([...bytes]);return zlib.deflateRawSync(bytes);}};
  const encoder=compression.createZopfli(engine),progress=[];
  await encoder.prepare({blob:raw,payloadOffset:0,payloadSize:24,blockSize:8},{progress:(done,total)=>progress.push([done,total])});
  assert.equal(calls.length,2);assert.deepEqual(progress,[[1,3],[2,3],[3,3]]);
  assert.deepEqual(zlib.inflateRawSync(await encoder(raw.subarray(0,8))),Buffer.from(raw.subarray(0,8)));
  await assert.rejects(encoder(Uint8Array.of(4)),/changed/);
});

test('a failing or canceled encoder never produces a partially prepared cache',async()=>{
  const controller=new AbortController();
  const encoder=compression.createZopfli({async compress(raw){controller.abort();return raw;}});
  await assert.rejects(encoder.prepare({blob:Uint8Array.of(1,2),payloadOffset:0,payloadSize:2,blockSize:1},{signal:controller.signal}),{name:'AbortError'});
  await assert.rejects(encoder(Uint8Array.of(1)),/not prepared/);
});

test('stop cancels preparation before any radio commands',async()=>{
  let signal,called=0;
  const encoder=async()=>{};
  encoder.prepare=(info,options)=>new Promise((resolve,reject)=>{
    signal=options.signal;signal.addEventListener('abort',()=>reject(Object.assign(Error('stopped'),{name:'AbortError'})));
  });
  const session=new DirectSession({remoteCommand:async()=>{called++;},stopServing:async()=>{}},'ab'.repeat(32),'',{}, {compress:encoder});
  const task=session.start();await new Promise(resolve=>setTimeout(resolve,0));assert(signal);
  await session.stop();await assert.rejects(task,{name:'AbortError'});assert.equal(called,0);
});

test('resume rejects a different compression mode before restarting transfer',()=>{
  const session=new DirectSession({sourceKey:'aa'.repeat(32)},'bb'.repeat(32),'',
    {merkleRoot:Uint8Array.of(1,2,3,4)}, {compressionMode:'zopfli',readJournal:()=>({compression:'native'})});
  assert.throws(()=>session.validateRecovery({state:'active',id:'01020304'},{allowExisting:true}),/original transfer compression/);
});

test('selected Zopfli failures stop both source types instead of silently using raw blocks',async()=>{
  const fail=async()=>{throw Error('cached block missing');};fail.strict=true;
  const payload=new Uint8Array(1024).fill(1),merkleRoot=Uint8Array.of(1,2,3,4);
  const info={blob:payload,payload,merkleRoot,blockCount:1,blockSize:1024,payloadOffset:0,payloadSize:1024};
  const source=new Source(info,null,null,[],fail);
  const req=Uint8Array.of(0x32,0,6,...merkleRoot,0,0,1,0xc0);
  await assert.rejects(source.replies(req),/Zopfli transfer compression failed/);
  const folder=new SeederData(info,payload,fail);
  await assert.rejects(folder.request(9,Uint8Array.of(0,0,0,0,0,0,0)),/Zopfli transfer compression failed/);
});
