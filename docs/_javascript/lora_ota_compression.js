/* Prepare deterministic transfer compression before the source starts. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaCompression=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const subtle=()=>globalThis.crypto?.subtle||(typeof require==='function'?require('node:crypto').webcrypto.subtle:null);
  const hex=bytes=>Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
  const equal=(a,b)=>a.length===b.length&&a.every((v,i)=>v===b[i]);
  async function hash(raw){const api=subtle();if(!api)throw Error('Web Crypto is unavailable');return hex(new Uint8Array(await api.digest('SHA-256',raw)));}
  function createZopfli(engine){
    if(!engine||typeof engine.compress!=='function')throw Error('Zopfli compression is unavailable in this browser.');
    let cache=null;
    const compress=async raw=>{
      if(!cache)throw Error('Zopfli transfer compression was not prepared.');
      const entry=cache.get(await hash(raw));
      if(!entry||!equal(entry.raw,raw))throw Error('The firmware block changed after Zopfli preparation.');
      return entry.encoded;
    };
    compress.strict=true;
    compress.prepare=async(info,{signal,progress=()=>{}}={})=>{
      if(!info||!(info.blob instanceof Uint8Array)||!Number.isInteger(info.blockSize)||info.blockSize<1||
         !Number.isInteger(info.payloadOffset)||!Number.isInteger(info.payloadSize)||info.payloadOffset<0||info.payloadSize<0||
         info.payloadOffset+info.payloadSize>info.blob.length)throw Error('Invalid firmware package for Zopfli compression.');
      const total=Math.ceil(info.payloadSize/info.blockSize),next=new Map();
      for(let i=0;i<total;i++){
        if(signal?.aborted)throw new DOMException('Compression stopped','AbortError');
        const raw=info.blob.slice(info.payloadOffset+i*info.blockSize,Math.min(info.payloadOffset+(i+1)*info.blockSize,info.payloadOffset+info.payloadSize));
        const key=await hash(raw),existing=next.get(key);
        if(!existing||!equal(existing.raw,raw)){
          const encoded=new Uint8Array(await engine.compress(raw,{signal}));
          if(!encoded.length)throw Error('Zopfli returned an empty compressed block.');
          next.set(key,{raw,encoded});
        }
        progress(i+1,total);
      }
      if(signal?.aborted)throw new DOMException('Compression stopped','AbortError');
      cache=next;
    };
    return compress;
  }
  return {createZopfli};
});
