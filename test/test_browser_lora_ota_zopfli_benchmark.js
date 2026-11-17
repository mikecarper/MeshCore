'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const benchmark=require('../docs/_javascript/lora_ota_zopfli_benchmark.js');
function storage(){const data=new Map();return {data,getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v)};}
class Page extends EventTarget{constructor(){super();this.visibilityState='visible';}set(state,notify=true){this.visibilityState=state;if(notify)this.dispatchEvent(new Event('visibilitychange'));}}
function engine(factory){let disposed=0,created=0;return {create(){created++;return {compress:factory,dispose(){disposed++;}};},get disposed(){return disposed;},get created(){return created;}};}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
test('passes a representative 2 KiB block once and reuses the saved result',async()=>{
  const memory=storage(),doc=new Page(),calls=[];
  const worker=engine(async(raw)=>{calls.push(raw);return Uint8Array.of(1);});
  assert.equal(await benchmark.create({engine:worker,storage:memory,doc,limit:100}).run(),'passed');
  assert.equal(calls.length,1);assert.equal(calls[0].length,2048);assert.equal(worker.disposed,1);
  const again=benchmark.create({engine:worker,storage:memory,doc});assert.equal(again.prior,'passed');assert.equal(await again.run(),'passed');assert.equal(calls.length,1);
});
test('slow or failing engines choose and persist fast mode',async()=>{
  for(const factory of [()=>new Promise(()=>{}),()=>Promise.reject(Error('no wasm'))]){
    const memory=storage(),doc=new Page(),worker=engine(factory);
    assert.equal(await benchmark.create({engine:worker,storage:memory,doc,limit:15}).run(),'failed');
    assert.equal(benchmark.read(memory),'failed');assert.equal(worker.disposed,1);
  }
});
test('manual choice wins and cancels an in-progress test',async()=>{
  const memory=storage(),doc=new Page(),worker=engine(()=>new Promise(()=>{})),check=benchmark.create({engine:worker,storage:memory,doc,limit:100});
  const result=check.run();await pause(2);check.cancel();check.cancel();
  assert.equal(await result,'manual');assert.equal(benchmark.read(memory),'manual');assert.equal(worker.disposed,1);
});
test('hidden and suspended attempts do not persist a false slow result',async()=>{
  const memory=storage(),doc=new Page();let count=0;
  const worker=engine(()=>++count===1?new Promise(()=>{}):Promise.resolve(Uint8Array.of(1)));
  const check=benchmark.create({engine:worker,storage:memory,doc,limit:15}),result=check.run();
  await pause(2);doc.set('hidden',false);await pause(25);assert.equal(benchmark.read(memory),null);
  doc.set('visible');assert.equal(await result,'passed');assert.equal(worker.created,2);assert.equal(worker.disposed,2);
});
test('storage unavailable does not prevent a safe failed decision',async()=>{
  const unavailable={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
  const worker=engine(()=>Promise.reject(Error('no wasm')));
  assert.equal(await benchmark.create({engine:worker,storage:unavailable,doc:new Page()}).run(),'failed');
});
