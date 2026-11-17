'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const rows=new Map();let failWrite=false;
function request(tx,fn){
  const req={};tx.pending++;
  queueMicrotask(()=>{
    if(tx.failed)return;
    try{req.result=fn();req.onsuccess?.();}
    catch(error){tx.failed=true;tx.onabort?.({target:{error}});return;}
    finally{tx.pending--;}
    setTimeout(()=>{
      if(tx.pending||tx.failed||tx.done)return;
      tx.done=true;if(tx.mode==='readwrite'){rows.clear();for(const [k,v] of tx.copy)rows.set(k,v);}
      tx.oncomplete?.();
    },0);
  });return req;
}
const db={
  createObjectStore(){},close(){},
  transaction(_name,mode){
    const tx={mode,pending:0,failed:false,done:false,copy:new Map(structuredClone(Array.from(rows.entries())))};
    tx.objectStore=()=>({
      get:k=>request(tx,()=>structuredClone(tx.copy.get(k))),
      put:(v,k)=>request(tx,()=>{if(failWrite)throw Error('quota');tx.copy.set(k,structuredClone(v));}),
      delete:k=>request(tx,()=>tx.copy.delete(k))
    });return tx;
  }
};
global.indexedDB={open(){const req={result:db};queueMicrotask(()=>req.onsuccess?.());return req;}};
const store=require('../docs/_javascript/lora_ota_resume_store.js');
const source='a'.repeat(64),target='b'.repeat(64),other='c'.repeat(64),pkg='d'.repeat(64);
const file=(name,content)=>new File([content],name,{type:'application/octet-stream'});
test('saves and checks exact files, public identities and signing key',async()=>{
  rows.clear();const expected=await store.save({source,target,file:file('goal.bin','abc'),base:file('base.bin','def'),image:file('out.bin','ghi'),signer:pkg,packageHash:pkg});
  const peek=await store.peek();assert.equal(peek.fileName,'goal.bin');assert.equal(peek.baseName,'base.bin');assert.equal(peek.recordId,expected.recordId);
  const loaded=await store.load({source,target,recordId:expected.recordId,packageHash:pkg});
  assert.equal(await loaded.file.text(),'abc');assert.equal(await loaded.base.text(),'def');assert.equal(await loaded.image.text(),'ghi');assert.equal(loaded.signer,pkg);
  assert.equal(await store.load({source:other}),null);assert.equal(await store.load({target:other}),null);assert.equal(await store.load({packageHash:other}),null);
  assert.equal('password' in rows.get('latest'),false);
});
test('detects corrupt or oversized saved bytes without clearing recovery data',async()=>{
  const corrupted=structuredClone(rows.get('latest'));new Uint8Array(corrupted.file.data)[0]^=1;rows.set('latest',corrupted);
  await assert.rejects(store.load(),/integrity/);assert.ok(rows.has('latest'));
  await assert.rejects(store.save({source,target,file:{size:store.MAX_SIZE+1,name:'huge.bin',type:'',arrayBuffer:async()=>new ArrayBuffer(0)}}),/too large/);
});
test('failed quota write retains previous session and metadata',async()=>{
  rows.clear();const old=await store.save({source,target,file:file('old.bin','old')});failWrite=true;
  try{await assert.rejects(store.save({source:other,target,file:file('new.bin','new')}),/Could not save/);}finally{failWrite=false;}
  assert.equal((await store.peek()).recordId,old.recordId);assert.equal(await (await store.load()).file.text(),'old');
});
test('identity scoped clear preserves a newer record and clears successful transfer',async()=>{
  const old=await store.peek(),fresh=await store.save({source,target,file:file('new.bin','new')});
  assert.equal(await store.clear({source,target,recordId:old.recordId}),false);assert.equal((await store.peek()).recordId,fresh.recordId);
  assert.equal(await store.clear({source,target,recordId:fresh.recordId}),true);assert.equal(await store.peek(),null);assert.equal(await store.load(),null);
});
test('validates automatic chain identity and rejects invalid public metadata',async()=>{
  const autoKey=source+':'+target+':'+pkg;
  await store.save({source,target,file:file('x.bin','x'),kind:'automatic',autoKey});assert.equal((await store.load()).autoKey,autoKey);
  await assert.rejects(store.save({source,target,file:file('x.bin','x'),kind:'automatic',autoKey:other+':'+target+':'+pkg}),/path/);
  await assert.rejects(store.save({source,target,file:file('x.bin','x'),signer:'wrong'}),/signing key/);
  await store.clear();
});

test('manual plan keys must match both identities',async()=>{
  const planKey='manual:'+source+':'+target+':'+pkg+':'+('e'.repeat(32));
  const rec=await store.save({source,target,file:file('goal.bin','abc'),kind:'manual',planKey});
  assert.equal((await store.peek()).planKey,planKey);assert.equal((await store.load(rec)).kind,'manual');
  await assert.rejects(store.save({source,target,file:file('goal.bin','abc'),kind:'manual',planKey:'manual:'+other+':'+target+':'+pkg+':'+('e'.repeat(32))}),/manual update plan/);
  await store.clear(rec);
});

test('preserves the exact compression choice and defaults old records to native',async()=>{
  const rec=await store.save({source,target,file:file('goal.bin','abc'),compression:'zopfli'});
  assert.equal((await store.peek()).compression,'zopfli');assert.equal((await store.load(rec)).compression,'zopfli');
  await assert.rejects(store.save({source,target,file:file('x.bin','x'),compression:'unknown'}),/compression/);
  const old=rows.get('latest');delete old.compression;rows.set('latest',old);
  assert.equal((await store.load(rec)).compression,'native');await store.clear(rec);
});
