/* Bounded, identity-scoped recovery files for an interrupted LoRa update. */
(function (root, factory) {
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaResumeStore=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const DB='meshcore-lora-update-resume', STORE='sessions', KEY='latest', META='meta', MAX_SIZE=64*1024*1024;
  const keyPattern=/^[a-f0-9]{64}$/i, idPattern=/^[a-f0-9]{32}$/i;
  function webCrypto(){
    if(globalThis.crypto?.subtle && globalThis.crypto?.getRandomValues)return globalThis.crypto;
    if(typeof require==='function')return require('node:crypto').webcrypto;
    throw Error('Secure browser storage verification is unavailable.');
  }
  const hex=bytes=>Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
  const key=value=>typeof value==='string'&&keyPattern.test(value)?value.toLowerCase():null;
  function metadata(value,forSave=false){
    const source=key(value?.source),target=key(value?.target),kind=value?.kind??'single';
    if(!source||!target||!['single','automatic','manual'].includes(kind))throw Error('Saved update identities are invalid.');
    const signer=value.signer==null||value.signer===''?'':key(value.signer);
    if(signer===null)throw Error('Saved signing key is invalid.');
    let autoKey=value.autoKey??null;
    if(autoKey!==null){
      if(typeof autoKey!=='string')throw Error('Saved update path is invalid.');
      autoKey=autoKey.toLowerCase();
      if(!new RegExp('^'+source+':'+target+':[a-f0-9]{64}$').test(autoKey))throw Error('Saved update path is invalid.');
    }
    if(kind==='automatic'&&!autoKey)throw Error('Saved update path is invalid.');
    let planKey=value.planKey??null;
    if(planKey!==null){
      if(typeof planKey!=='string'||!new RegExp('^manual:'+source+':'+target+':[a-f0-9]{64}:[a-f0-9]{32}$').test(planKey.toLowerCase()))throw Error('Saved manual update plan is invalid.');
      planKey=planKey.toLowerCase();
    }
    if(kind==='manual'&&!planKey)throw Error('Saved manual update plan is invalid.');
    const packageHash=value.packageHash==null||value.packageHash===''?'':key(value.packageHash);
    if(packageHash===null)throw Error('Saved package hash is invalid.');
    const recordId=forSave?hex(webCrypto().getRandomValues(new Uint8Array(16))):value.recordId;
    if(typeof recordId!=='string'||!idPattern.test(recordId))throw Error('Saved update record is invalid.');
    return {source,target,kind,autoKey,planKey,signer,packageHash,recordId:recordId.toLowerCase()};
  }
  async function digest(data){return hex(new Uint8Array(await webCrypto().subtle.digest('SHA-256',data)));}
  async function pack(file,required){
    if(file==null){if(required)throw Error('Select the firmware file before saving the update.');return null;}
    if(typeof file.arrayBuffer!=='function'||!Number.isSafeInteger(file.size)||file.size<1||file.size>MAX_SIZE||
       typeof file.name!=='string'||!file.name||file.name.length>255||/[\x00-\x1f\x7f]/.test(file.name)||
       typeof file.type!=='string'||file.type.length>128)throw Error('The selected recovery file is invalid or too large.');
    const data=await file.arrayBuffer();
    if(!(data instanceof ArrayBuffer)||data.byteLength!==file.size)throw Error('Could not read the selected recovery file.');
    const hash=await digest(data);
    return {name:file.name,type:file.type,data,hash};
  }
  async function unpack(value,required){
    if(value==null){if(required)throw Error('Saved firmware data is missing.');return null;}
    if(!value||typeof value.name!=='string'||!value.name||value.name.length>255||/[\x00-\x1f\x7f]/.test(value.name)||
       typeof value.type!=='string'||value.type.length>128||!(value.data instanceof ArrayBuffer)||
       value.data.byteLength<1||value.data.byteLength>MAX_SIZE||typeof value.hash!=='string'||!keyPattern.test(value.hash))
      throw Error('Saved firmware data is invalid.');
    if(await digest(value.data)!==value.hash.toLowerCase())throw Error('Saved firmware data failed its integrity check.');
    return new File([value.data],value.name,{type:value.type});
  }
  function open(){
    if(typeof indexedDB==='undefined')return Promise.reject(Error('Browser update recovery storage is unavailable.'));
    return new Promise((resolve,reject)=>{
      const req=indexedDB.open(DB,1);
      req.onupgradeneeded=()=>req.result.createObjectStore(STORE);
      req.onsuccess=()=>resolve(req.result);
      req.onerror=()=>reject(Error('Could not open browser update recovery storage.'));
      req.onblocked=()=>reject(Error('Browser update recovery storage is in use by another tab.'));
    });
  }
  async function save(value){
    const meta=metadata(value,true);
    const [file,base,image]=await Promise.all([pack(value.file,true),pack(value.base,false),pack(value.image,false)]);
    const db=await open();
    try {await new Promise((resolve,reject)=>{
      const tx=db.transaction(STORE,'readwrite');
      const store=tx.objectStore(STORE);
      store.put({version:1,...meta,file,base,image},KEY);
      store.put({version:1,...meta,fileName:file.name,baseName:base?.name??null,imageName:image?.name??null},META);
      tx.oncomplete=()=>resolve();tx.onerror=tx.onabort=()=>reject(Error('Could not save update recovery files in this browser.'));
    });}finally{db.close();}
    return meta;
  }
  function matches(record,expected){
    if(!expected)return true;
    for(const name of ['source','target','recordId','packageHash','planKey'])if(expected[name]!=null &&
      (typeof expected[name]!=='string'||record[name]!==expected[name].toLowerCase()))return false;
    return true;
  }
  async function load(expected){
    const db=await open();let value;
    try {value=await new Promise((resolve,reject)=>{
      const tx=db.transaction(STORE,'readonly'),req=tx.objectStore(STORE).get(KEY);let result;
      req.onsuccess=()=>{result=req.result;};tx.oncomplete=()=>resolve(result);
      tx.onerror=tx.onabort=()=>reject(Error('Could not read browser update recovery files.'));
    });}finally{db.close();}
    if(value==null)return null;
    if(value.version!==1)throw Error('Saved update record is invalid.');
    const meta=metadata(value);
    if(!matches(meta,expected))return null;
    const [file,base,image]=await Promise.all([unpack(value.file,true),unpack(value.base,false),unpack(value.image,false)]);
    return {...meta,file,base,image};
  }
  async function peek(){
    const db=await open();let value;
    try {value=await new Promise((resolve,reject)=>{
      const tx=db.transaction(STORE,'readonly'),req=tx.objectStore(STORE).get(META);let result;
      req.onsuccess=()=>{result=req.result;};tx.oncomplete=()=>resolve(result);
      tx.onerror=tx.onabort=()=>reject(Error('Could not read browser update recovery state.'));
    });}finally{db.close();}
    if(value==null)return null;
    if(value.version!==1 || typeof value.fileName!=='string' || !value.fileName || value.fileName.length>255 ||
       [value.baseName,value.imageName].some(name=>name!==null&&(typeof name!=='string'||!name||name.length>255)))
      throw Error('Saved update record is invalid.');
    return {...metadata(value),fileName:value.fileName,baseName:value.baseName,imageName:value.imageName};
  }
  async function clear(expected){
    const db=await open();
    try {return await new Promise((resolve,reject)=>{
      const tx=db.transaction(STORE,'readwrite'),store=tx.objectStore(STORE),req=store.get(KEY);let deleted=false;
      req.onsuccess=()=>{
        const value=req.result;
        if(value!=null&&(!expected||matches(value,expected))){store.delete(KEY);store.delete(META);deleted=true;}
      };
      tx.oncomplete=()=>resolve(deleted);tx.onerror=tx.onabort=()=>reject(Error('Could not clear browser update recovery files.'));
    });}finally{db.close();}
  }
  return {save,load,peek,clear,MAX_SIZE};
});
