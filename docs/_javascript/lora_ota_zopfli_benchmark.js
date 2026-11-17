/* One-time, bounded device capability check; no firmware or radio is used. */
(function(root,factory){
  const api=factory(root);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaZopfliBenchmark=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(root){
  'use strict';
  const KEY='meshcore-browser-lora-ota-zopfli-test-v1',LIMIT=2000;
  function read(storage){
    try{const x=JSON.parse(storage.getItem(KEY)||'null');return x?.version===1&&['passed','failed','manual'].includes(x.result)?x.result:null;}
    catch(_){return null;}
  }
  function save(storage,result){try{storage.setItem(KEY,JSON.stringify({version:1,result}));return true;}catch(_){return false;}}
  // A repeatable mix of executable-like patterns, zeros and varied bytes.
  function sample(){
    const code=Uint8Array.of(0x2d,0xe9,0xf0,0x41,0x04,0x46,0x0d,0x46,0x16,0x46,0x1f,0x46,0x20,0x68,0x00,0x28);
    const out=new Uint8Array(2048);let state=0x152a41c3;
    for(let i=0;i<out.length;i++){
      state^=state<<13;state^=state>>>17;state^=state<<5;
      out[i]=i%16<8?code[i%code.length]:(i%16<12?0:state&255);
    }
    return out;
  }
  function create({engine=root.LoraOtaZopfli,storage,doc=root.document,now=()=>performance.now(),limit=LIMIT}={}){
    if(!storage)try{storage=root.localStorage;}catch(_){storage=null;}
    let canceled=false,active=null,promise=null;const prior=read(storage);
    function cancel(){canceled=true;save(storage,'manual');active?.();}
    async function visible(){
      if(!doc||doc.visibilityState!=='hidden')return;
      await new Promise(resolve=>{
        const change=()=>{if(canceled||doc.visibilityState!=='hidden'){doc.removeEventListener('visibilitychange',change);active=null;resolve();}};
        active=change;doc.addEventListener('visibilitychange',change);change();
      });
    }
    async function run(){
      if(prior)return prior;
      while(!canceled){
        await visible();if(canceled)break;
        let worker,timeout,hidden=false,start;const controller=new AbortController();
        try{
          start=now();
          worker=engine.create();
          const interrupted=new Promise(resolve=>{
            const change=()=>{if(doc?.visibilityState==='hidden'){hidden=true;controller.abort();resolve('hidden');}};
            active=()=>{controller.abort();resolve(canceled?'manual':'hidden');};
            if(doc)doc.addEventListener('visibilitychange',change);
            interruptedCleanup=()=>doc?.removeEventListener('visibilitychange',change);
          });
          const timed=new Promise(resolve=>{timeout=setTimeout(()=>{controller.abort();resolve('failed');},limit);});
          const work=Promise.resolve().then(()=>worker.compress(sample(),{signal:controller.signal})).then(value=>value?.length?'passed':'failed',()=> 'failed');
          let result=await Promise.race([work,timed,interrupted]);
          if(result==='passed'&&now()-start>=limit)result='failed';
          if(canceled)break;
          if(hidden||result==='hidden'||doc?.visibilityState==='hidden')continue;
          save(storage,result);return result;
        }catch(_){if(canceled)break;if(doc?.visibilityState==='hidden')continue;save(storage,'failed');return 'failed';}
        finally{clearTimeout(timeout);interruptedCleanup?.();interruptedCleanup=null;active=null;controller.abort();try{worker?.dispose();}catch(_){}}
      }
      return 'manual';
    }
    let interruptedCleanup=null;
    return {prior,cancel,run:()=>promise||(promise=run().catch(()=>{if(canceled)return 'manual';save(storage,'failed');return 'failed';}))};
  }
  return {create,read,KEY,LIMIT,sample};
});
