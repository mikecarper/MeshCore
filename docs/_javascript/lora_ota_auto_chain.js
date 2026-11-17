/* Automatic, hash-pinned nRF52 application bridges. No partial app is booted. */
(function(root,factory){
  const node=typeof module==='object'&&module.exports;
  const api=factory(node?require('./lora_ota_package.js'):root.MeshCoreMotaPackage,
    node?require('./lora_ota_delta.js'):root.MeshCoreLoraDelta,
    node?require('./lora_ota_checks.js'):root.LoraOtaChecks,
    node?require('./lora_ota_chain_ui.js'):root.LoraOtaChainUi,
    node?require('./lora_ota_plan.js'):root.LoraOtaPlan);
  if(node)module.exports=api;else root.LoraOtaAutoChain=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(mota,delta,checks,chainUi,planner){
  'use strict';
  const ROOT='https://mikecarper.github.io/MeshCore/_data/lora_ota_bridges/';
  const SHA=/^[0-9a-f]{64}$/, MAX_IMAGE=1024*1024, MAX_STEPS=8;
  const hex=checks.hex;
  const sha=async bytes=>hex(new Uint8Array(await (globalThis.crypto?.subtle||require('node:crypto').webcrypto.subtle).digest('SHA-256',bytes)));
  const cancelled=signal=>{if(signal?.aborted)throw Error('Update preparation stopped.');};
  async function read(path,limit,options){
    if(path!=='index.json'&&!/^[0-9a-f]{64}\.json$/.test(path))throw Error('Invalid intermediate firmware path');
    cancelled(options.signal);
    const url=ROOT+path, response=await (options.fetchImpl||fetch)(url,{credentials:'omit',cache:'no-cache',
      signal:options.signal?AbortSignal.any([options.signal,AbortSignal.timeout(60000)]):AbortSignal.timeout(60000)});
    if(!response.ok || (response.url && response.url!==url))throw Error('Could not load intermediate firmware (HTTP '+response.status+').');
    if(!response.body?.getReader)throw Error('Intermediate firmware download is unreadable');
    const reader=response.body.getReader(),parts=[];let length=0;
    try{for(;;){cancelled(options.signal);const {value,done}=await reader.read();if(done)break;
      if(!(value instanceof Uint8Array)||(length+=value.length)>limit)throw Error('Intermediate firmware exceeds its size limit');parts.push(value);}}
    finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    const result=new Uint8Array(length);let off=0;for(const part of parts){result.set(part,off);off+=part.length;}return result;
  }
  function routeFor(index,base,goal){
    if(index?.schemaVersion!==1 || !Array.isArray(index.routes) || index.routes.length>1000)
      throw Error('Invalid automatic update-path catalog');
    const matching=index.routes.filter(r=>r?.from===hex(base.imageHash)&&r.to===hex(goal.imageHash)&&
      r.targetId===base.targetId&&r.hwId===base.hwId);
    if(matching.length>1)throw Error('More than one automatic update path matches these images');
    const route=matching[0];if(!route)return null;
    if(!Array.isArray(route.images)||!route.images.length||route.images.length>=MAX_STEPS||
        route.images.some(x=>!SHA.test(x?.sha256||'')||!Number.isInteger(x.size)||x.size<80||x.size>MAX_IMAGE)||
        new Set([route.from,route.to,...route.images.map(x=>x.sha256)]).size!==route.images.length+2)
      throw Error('Invalid intermediate firmware catalog entry');
    for(const image of route.images)checks.assertApplicationAllowed(image.sha256);
    if(route.disabled===true)throw Error(route.disabledReason||'This automatic intermediate update path is disabled. Choose a supported standalone release.');
    return route;
  }
  async function loadImage(entry,options){
    // Text-only, content-addressed resources keep these application bridges
    // portable through Pages. Bound compressed input and inflated output.
    const payload=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(await read(entry.sha256+'.json',2*MAX_IMAGE,options)));
    if(payload?.encoding!=='gzip-base64'||typeof payload.data!=='string'||payload.data.length>2*MAX_IMAGE||
        !/^[A-Za-z0-9+/]+={0,2}$/.test(payload.data))throw Error('Invalid compressed intermediate firmware');
    const compressed=Uint8Array.from(atob(payload.data),c=>c.charCodeAt(0));
    const reader=new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip')).getReader();
    const result=new Uint8Array(entry.size);let offset=0;
    try{for(;;){cancelled(options.signal);const {value,done}=await reader.read();if(done)break;
      if(value.length>entry.size-offset)throw Error('Intermediate firmware expands beyond its expected size');result.set(value,offset);offset+=value.length;}}
    finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    if(offset!==entry.size)throw Error('Intermediate firmware has the wrong uncompressed size');return result;
  }
  async function lookup(baseBytes,goalBytes,options={}){
    const [base,goal]=await Promise.all([mota.parseEndF(baseBytes),mota.parseEndF(goalBytes)]);
    const index=options.index||JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(await read('index.json',512*1024,options)));
    return routeFor(index,base,goal)?index:null;
  }
  function rankPaths(nodes,edges){
    const best=objective=>{
      const paths=new Map([[nodes[0].id,{steps:[],bytes:0}]]);
      for(const edge of edges){
        const prior=paths.get(edge.from);if(!prior)continue;
        const next={steps:[...prior.steps,edge],bytes:prior.bytes+edge.radioDataBytes},old=paths.get(edge.to);
        const score=x=>objective==='data'?[x.bytes,x.steps.length]:[x.steps.length,x.bytes];
        const a=score(next),b=old&&score(old);
        if(!b||a[0]<b[0]||a[0]===b[0]&&a[1]<b[1])paths.set(edge.to,next);
      }
      return paths.get(nodes[nodes.length-1].id);
    };
    return {fewer:best('steps'),smaller:best('data')};
  }
  async function prepare(baseBytes,goalBytes,caps,options={}){
    if(!caps?.nrf || caps.external || caps.sd || caps.runtimeStorage!=='internal' || !caps.otaConfirmed)
      throw Error('Automatic intermediate updates require confirmed nRF52 internal storage');
    const [base,goal]=await Promise.all([mota.parseEndF(baseBytes),mota.parseEndF(goalBytes)]);
    if(base.targetId!==caps.targetId||base.hwId!==caps.hwId||base.fwVersion!==caps.version||hex(base.bodyHash)!==caps.bodyHash||
        goal.targetId!==base.targetId||goal.hwId!==base.hwId||goal.fwVersion<=base.fwVersion)
      throw Error('The automatic update path does not match the installed and selected firmware');
    const index=options.index || JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(await read('index.json',512*1024,options)));
    const route=routeFor(index,base,goal);
    if(!route)return null;
    const progress=options.progress||(()=>{}), images=[base];
    for(let i=0;i<route.images.length;i++){
      cancelled(options.signal);const entry=route.images[i];progress('Loading intermediate '+(i+1)+' of '+route.images.length+'...');
      const bytes=await (options.loadImage||((e)=>loadImage(e,options)))(entry);
      if(!(bytes instanceof Uint8Array)||bytes.length!==entry.size||await sha(bytes)!==entry.sha256)
        throw Error('Intermediate firmware failed its SHA-256 or size check');
      const image=await mota.parseEndF(bytes), prior=images[images.length-1];
      if(image.targetId!==base.targetId||image.hwId!==base.hwId||image.fwVersion<prior.fwVersion||image.fwVersion>goal.fwVersion)
        throw Error('Intermediate firmware has the wrong hardware, role or version');
      // A bridge runs its original, compiled application. Its published bytes,
      // identity and version are never edited to manufacture a fitting path.
      chainUi.evidence(image.image,caps,base.image,false);
      images.push(image);
    }
    chainUi.evidence(goal.image,caps,base.image,false);images.push(goal);
    const nodes=images.map((f,i)=>({id:'image-'+i,version:f.fwVersion,targetId:f.targetId,hwId:f.hwId,
      bodyHash:hex(f.bodyHash),imageHash:hex(f.imageHash),validated:true,capabilities:{...caps}}));
    const edges=[],packages=new Map(),build=options.buildDelta||delta.buildInWorker;
    const pairCount=images.length*(images.length-1)/2;let checked=0;
    for(let i=0;i<images.length-1;i++)for(let j=i+1;j<images.length;j++){
      cancelled(options.signal);progress('Comparing update paths: '+(++checked)+' of '+pairCount+' patches...');
      const a=images[i],b=images[j], allowSameVersion=a.fwVersion===b.fwVersion;
      let built;
      try{built=await build(a.image,b.image,{mode:'in-place',runtimeStorage:'internal',runtimeStageCeiling:caps.runtimeStageCeiling,
        runtimeHybridRam:caps.runtimeHybridRam,allowSameVersion,searchWorkspaces:options.deep?'deep':'data',blockSize:1024,signal:options.signal,
        progress:state=>{if(state?.phase==='workspace')progress('Comparing patch '+checked+' of '+pairCount+', workspace '+state.index+' of '+state.total+'...');}});}
      catch(error){cancelled(options.signal);if(error?.code==='OTA_DELTA_SPACE')continue;throw error;}
      const info=await mota.parseMota(built.blob),layout=delta.nrfLayout(a.image);
      if(info.codecId!==2||info.isFull||info.targetId!==b.targetId||info.hwId!==b.hwId||info.fwVersion!==b.fwVersion||
          hex(info.imageHash)!==hex(b.imageHash)||hex(info.baseHash)!==hex(a.bodyHash)||
          !Number.isInteger(built.memorySize)||built.memorySize<=0)
        throw Error('An intermediate patch does not match its expected images');
      if((layout.flags&16)&&info.blob.length>4096)checks.confirmHybridRam(caps.runtimeHybridRam);
      checks.validate(info,{...caps,baseHash:hex(a.bodyHash),version:a.fwVersion,
        codecs:caps.codecs.reduce((bits,v)=>bits|(1<<v),0)},false,{allowSameVersion});
      const packageHash=await sha(info.blob),pkg={blob:info.blob,info};packages.set(packageHash,pkg);
      const geometry={runtimeStorage:'internal',runtimeStageCeiling:caps.runtimeStageCeiling,memorySize:built.memorySize};
      const edge={allowSameVersion,nrfGeometry:geometry,inplacePlan:{storage:'internal',app:layout.app,
        layoutCeiling:layout.ceiling,layoutFlags:layout.flags,memorySize:built.memorySize}};
      const estimate=planner.estimateEdge({payloadSize:info.payloadSize,blockSize:1024},
        {wireProfile:'legacy',proactiveProof:true},{relays:options.relays||0});
      edges.push({from:nodes[i].id,to:nodes[j].id,kind:'delta',packageHash,package:pkg,edge,
        sourceImage:a.image,targetImage:b.image,radioDataBytes:estimate.transmittedMeshBytes});
    }
    const {fewer,smaller}=rankPaths(nodes,edges);
    if(!fewer)throw Object.assign(Error('No fitting intermediate update path is available for these exact images. '+
      'No firmware was uploaded. Use USB/Bluetooth DFU, or choose another compatible release.'),
      {code:'OTA_DELTA_SPACE',otaEndpoint:'Browser',otaLocalCheck:true,otaStep:'Find a fitting update path'});
    const chosen=options.objective==='data'?smaller:fewer,steps=chosen.steps;
    const used=new Set(steps.map(step=>step.packageHash));for(const key of packages.keys())if(!used.has(key))packages.delete(key);
    return {steps,nodes,packages,complete:true,automatic:true,goalHash:hex(goal.imageHash),objective:options.objective==='data'?'data':'steps',
      alternatives:{steps:{installs:fewer.steps.length,bytes:fewer.bytes},data:{installs:smaller.steps.length,bytes:smaller.bytes}},
      route:{steps,totals:{installs:steps.length,radioDataBytes:chosen.bytes}},warnings:[]};
  }
  // Retain the exact images and prepared packages locally so a page reload or
  // USB reconnect needs no file picking and can resume the same hash-bound step.
  async function cache(action,key,value){
    if(typeof indexedDB==='undefined')throw Error('Browser update recovery storage is unavailable');
    const db=await new Promise((resolve,reject)=>{
      const request=indexedDB.open('meshcore-lora-update-paths',1);
      request.onupgradeneeded=()=>request.result.createObjectStore('plans');
      request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(Error('Could not open browser update recovery storage'));
    });
    try{return await new Promise((resolve,reject)=>{
      const transaction=db.transaction('plans',action==='get'?'readonly':'readwrite'),store=transaction.objectStore('plans');
      const request=action==='get'?store.get(key):action==='delete'?store.delete(key):store.put(value,key);let result;
      request.onsuccess=()=>{result=request.result;};
      transaction.oncomplete=()=>resolve(result);transaction.onerror=transaction.onabort=()=>reject(Error('Could not save browser update recovery state'));
    });}finally{db.close();}
  }
  return {prepare,lookup,routeFor,rankPaths,cache,MAX_STEPS};
});
