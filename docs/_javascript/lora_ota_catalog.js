/* Select verified, compatible application images from the Meshfirmware mirror. */
(function(root,factory){
  const node=typeof module==='object'&&module.exports;
  const api=factory(node?require('./lora_ota_package.js'):root.MeshCoreMotaPackage,
    node?require('./lora_ota_zip.js'):root.MeshCoreLoraOtaZip,
    node?require('./lora_ota_checks.js'):root.LoraOtaChecks);
  if(node)module.exports=api;else root.LoraOtaCatalog=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(mota,zip,checks){
  'use strict';
  const ROOT='https://mikecarper.github.io/meshfirmware/', CONTROLS='https://mikecarper.github.io/MeshCore/_data/firmware_controls.json', MAX=64*1024*1024;
  const hex=b=>Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
  const sha=async b=>hex(new Uint8Array(await (globalThis.crypto?.subtle||require('node:crypto').webcrypto.subtle).digest('SHA-256',b)));
  const digest=s=>typeof s==='string'&&/^[0-9a-f]{64}$/.test(s);
  const size=n=>Number.isSafeInteger(n)&&n>0&&n<=MAX;
  async function read(url,limit,fetchImpl,deadline){
    const expected=new URL(url,ROOT);
    const releaseApi=expected.origin==='https://api.github.com'&&/^\/repos\/mikecarper\/MeshCore\/releases\/tags\/[^/]+$/.test(expected.pathname)&&!expected.search&&!expected.hash;
    if(!releaseApi&&(expected.origin!==new URL(ROOT).origin||!(expected.href.startsWith(ROOT)||expected.href===CONTROLS)))throw Error('Unexpected firmware mirror URL');
    const remaining=deadline===undefined?120000:deadline-Date.now();if(remaining<=0)throw Error('Firmware search timed out');
    const response=await fetchImpl(expected.href,{credentials:'omit',cache:'no-cache',signal:AbortSignal.timeout(Math.min(120000,remaining))});
    if(!response.ok)throw Error('Firmware catalog returned HTTP '+response.status);
    if(response.url&&response.url!==expected.href)throw Error('Firmware mirror redirected to an unexpected URL');
    const length=response.headers?.get('content-length');
    if(length!==null&&length!==undefined&&(!/^\d+$/.test(length)||Number(length)>limit))throw Error('Firmware download exceeds its size limit');
    if(!response.body?.getReader)throw Error('Firmware download is not readable');
    const reader=response.body.getReader(),chunks=[];let total=0;
    try{for(;;){const {value,done}=await reader.read();if(done)break;if(!(value instanceof Uint8Array)||(total+=value.length)>limit)throw Error('Firmware download exceeds its size limit');chunks.push(value);}}
    finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    const bytes=new Uint8Array(total);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}return bytes;
  }
  async function json(url,limit,fetchImpl,deadline){try{return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(await read(url,limit,fetchImpl,deadline)));}catch(e){throw Error('Cannot load firmware catalog: '+(e.message||e));}}
  function validateDest(dest){if(!dest||!Number.isInteger(dest.targetId)||dest.targetId<=0||dest.targetId>0xffffffff||typeof dest.hwId!=='string'||!dest.hwId||!Number.isInteger(dest.version))throw Error('Target identity and firmware version must be confirmed first');}
  // Publication labels are stripped only to find possible build environments;
  // the downloaded application's actual EndF identity remains authoritative.
  function envNames(name,requireOta=true){
    if(typeof name!=='string'||name.length>300||/[\\/\x00-\x1f]/.test(name)||/-with-bootloader-|-(?:merged|factory)(?=\.|-)/i.test(name))return [];
    const m=/^(.+)-v\d+\.\d+\.\d+(?:\.\d+)?(?:[-+][a-z0-9._-]+)?\.(?:bin|zip)$/i.exec(name);
    if(!m||(requireOta&&!/-ota$/i.test(m[1])))return [];
    const raw=m[1].replace(/-ota$/i,''),out=new Set([raw]);
    out.add(raw.replace(/-(?:full|reduced)$/i,''));
    out.add(raw.replace(/-full-usb-wifi$/i,''));
    out.add(raw.replace(/-full-logging$/i,''));
    out.add(raw.replace(/-logging$/i,''));
    return [...out].filter(x=>/^[a-z0-9_-]+$/i.test(x));
  }
  async function idForEnv(value){const bytes=new TextEncoder().encode(value), hash=await (globalThis.crypto?.subtle||require('node:crypto').webcrypto.subtle).digest('SHA-256',bytes);return new DataView(hash).getUint32(0,true);}
  async function discover(dest,role,options={}){
    validateDest(dest);const roles={repeater:'repeater',room:'roomServer',sensor:'sensor'};
    if(!Object.hasOwn(roles,role))throw Error('Choose the target role before finding firmware');
    const fetchImpl=options.fetchImpl||fetch;
    const [catalog,controls]=await Promise.all([json('catalog.json',12*1024*1024,fetchImpl),json(CONTROLS,12*1024*1024,fetchImpl)]);
    if(!Array.isArray(catalog.items)||catalog.items.length>50000||!controls||typeof controls.profiles!=='object')throw Error('Invalid firmware catalog');
    const candidates=[],seen=new Set(),ids=new Map();
    for(const item of catalog.items){
      if(item?.project!=='MeshCore'||item.provider!=='Keymind Cascade'||item.role!==roles[role]||!['flash','flash-update'].includes(item.action))continue;
      if(typeof item.url!=='string'||!/^https:\/\/github\.com\/mikecarper\/MeshCore\/releases\/download\/[^/?#]+\/[^/?#]+$/i.test(item.url)||typeof item.version!=='string')continue;
      const asset=/^(.+)-ota-v\d+\.\d+\.\d+(?:\.\d+)?(?:[-+][a-z0-9._-]+)?\.(?:bin|zip)$/i.exec(item.name||'');
      const profile=asset&&controls.familyTag===item.version?controls.profiles[asset[1]]:null;
      if(!profile||profile.otaRole!=='lora-receiver'||!Array.isArray(profile.updateMethods)||!profile.updateMethods.includes('lora'))continue;
      let version;try{version=checks.version(item.version);}catch{continue;}if(version<=dest.version)continue;
      const names=envNames(item.name);let match=false;
      for(const name of names){if(!ids.has(name))ids.set(name,await idForEnv(name));if(ids.get(name)===dest.targetId){match=true;break;}}
      if(!match||seen.has(item.url))continue;seen.add(item.url);
      candidates.push(Object.freeze({name:item.name,version:item.version,url:item.url,hardware:String(item.hardware||''),role,arch:String(item.architecture||''),subtitle:String(item.subtitle||'')}));
    }
    return candidates.sort((a,b)=>checks.version(b.version)-checks.version(a.version)||a.name.localeCompare(b.name));
  }
  function recommend(candidates,dest){
    validateDest(dest);if(!Array.isArray(candidates)||!candidates.length)return null;
    let newest=-1,top=[];
    for(const c of candidates){let v;try{v=checks.version(c.version);}catch{return null;}if(v<=dest.version)continue;if(v>newest){newest=v;top=[c];}else if(v===newest)top.push(c);}
    // Equal packed versions do not prove ordering between releases or profiles.
    // Prereleases require an explicit choice, not an automatic recommendation.
    return top.length===1&&!/(?:^|[-.])(alpha|beta|dev|rc\d*|preview\d*)(?:[-.]|$)/i.test(top[0].version)?top[0]:null;
  }
  async function recommendVerified(candidates,dest,options={}){
    validateDest(dest);if(!Array.isArray(candidates)||!candidates.length)return null;
    let newest=-1,top=[];
    for(const c of candidates){let v;try{v=checks.version(c.version);}catch{return null;}if(v<=dest.version)continue;if(v>newest){newest=v;top=[c];}else if(v===newest)top.push(c);}
    if(top.length!==1)return null;const candidate=top[0];
    try{
      const url=new URL(candidate.url),m=/^\/mikecarper\/MeshCore\/releases\/download\/([^/]+)\/([^/]+)$/.exec(url.pathname);
      if(url.origin!=='https://github.com'||url.search||url.hash||!m||decodeURIComponent(m[2])!==candidate.name)return null;
      const tag=decodeURIComponent(m[1]),api='https://api.github.com/repos/mikecarper/MeshCore/releases/tags/'+encodeURIComponent(tag);
      const release=await json(api,12*1024*1024,options.fetchImpl||fetch);
      if(release.tag_name!==tag||release.draft!==false||release.prerelease!==false||!Array.isArray(release.assets)||!release.assets.some(a=>a.name===candidate.name&&a.browser_download_url===candidate.url))return null;
      return candidate;
    }catch(_){return null;}
  }
  async function unpack(raw,piece){
    if(raw.length===piece.size&&await sha(raw)===piece.sha256)return raw; // HTTP may decode a .gz response.
    if(raw.length!==piece.gzipSize||await sha(raw)!==piece.gzipSha256)throw Error('Compressed mirror hash or size does not match');
    if(typeof DecompressionStream!=='function')throw Error('This browser cannot decompress firmware');
    const stream=new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'));
    const reader=stream.getReader(),out=new Uint8Array(piece.size);let total=0;
    try{for(;;){const {value,done}=await reader.read();if(done)break;if((total+=value.length)>piece.size)throw Error('Firmware expands beyond its expected size');out.set(value,total-value.length);}}
    finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    if(total!==piece.size||await sha(out)!==piece.sha256)throw Error('Firmware mirror part failed its hash or size check');return out;
  }
  function part(value){
    if(!value||!digest(value.sha256)||!digest(value.gzipSha256)||!size(value.size)||!size(value.gzipSize)||value.path!==value.sha256+'.gz')throw Error('Invalid firmware mirror metadata');
    return value;
  }
  function mirrorEntry(candidate,index){
    if(index?.schemaVersion!==1||!Array.isArray(index.entries)||index.entries.length>10000)throw Error('Invalid firmware mirror index');
    const matches=index.entries.filter(e=>e?.url===candidate.url);if(matches.length!==1)throw Error('Firmware is not available in the verified mirror');
    const entry=matches[0];if(!digest(entry.sha256)||!size(entry.size))throw Error('Invalid firmware mirror metadata');
    const pieces=entry.parts===undefined?[part(entry)]:Array.isArray(entry.parts)&&entry.parts.length>=2&&entry.parts.length<=128?entry.parts.map(part):null;
    if(!pieces||pieces.reduce((n,p)=>n+p.size,0)!==entry.size)throw Error('Invalid firmware mirror parts');
    return {entry,pieces};
  }
  async function download(candidate,index,fetchImpl,deadline){
    const {entry,pieces}=mirrorEntry(candidate,index);
    const result=new Uint8Array(entry.size);let offset=0;
    for(const p of pieces){const data=await read('firmware/'+p.path,Math.max(p.gzipSize,p.size),fetchImpl,deadline);const bytes=await unpack(data,p);result.set(bytes,offset);offset+=bytes.length;}
    if(await sha(result)!==entry.sha256)throw Error('Firmware mirror failed its complete SHA-256 check');
    return /\.zip$/i.test(candidate.name)?await zip.loadApplication(result):await mota.parseEndF(result);
  }
  async function load(candidate,dest,options={}){
    validateDest(dest);if(!candidate||typeof candidate.url!=='string'||!envNames(candidate.name).length)throw Error('Choose a catalog firmware image');
    const fetchImpl=options.fetchImpl||fetch,index=await json('firmware/index.json',4*1024*1024,fetchImpl),image=await download(candidate,index,fetchImpl);
    if(image.targetId!==dest.targetId||image.hwId!==dest.hwId||image.fwVersion<=dest.version||image.fwVersion!==checks.version(candidate.version))throw Error('Downloaded firmware identity does not match this target');
    const body=image.image,has=text=>{const needle=new TextEncoder().encode(text);outer:for(let i=0;i<=body.length-needle.length;i++){for(let j=0;j<needle.length;j++)if(body[i+j]!==needle[j])continue outer;return true;}return false;};
    // Released receivers predate installhash. Keep checking the mirror digest,
    // EndF identity and receiver evidence; the live session selects the supported
    // installer and verifies the running firmware after reboot.
    if(!has('OTA: status')||!has('get <id> flash | install'))throw Error('The downloaded firmware does not contain LoRa OTA receiver support');
    return {name:candidate.name.replace(/\.zip$/i,'.bin'),bytes:image.image,identity:image,
      installHashSupported:has('ota installhash <64-hex-container-sha256>')};
  }
  async function findInstalled(dest,role,versionReply,options={}){
    validateDest(dest);const roles={repeater:'repeater',room:'roomServer',sensor:'sensor'};
    if(!Object.hasOwn(roles,role)||!/^[0-9a-f]{16}$/i.test(dest.baseHash||''))throw Error('Target role and installed firmware hash must be confirmed');
    const reported=/^\s*>?\s*(v?\d+\.\d+\.\d+(?:\.\d+)?(?:[-+][a-z0-9._-]+)?)(?=\s|$)/i.exec(String(versionReply||''));
    if(!reported)return null;const fetchImpl=options.fetchImpl||fetch,expectedVersion=reported[1],deadline=Date.now()+60000;
    const catalog=await json('catalog.json',12*1024*1024,fetchImpl,deadline);
    if(!Array.isArray(catalog.items)||catalog.items.length>50000)throw Error('Invalid firmware catalog');
    const matches=[],ids=new Map(),seen=new Set();
    for(const item of catalog.items){
      if(item?.project!=='MeshCore'||item.provider!=='Keymind Cascade'||item.role!==roles[role]||!['flash','flash-update'].includes(item.action)||item.version!==expectedVersion||typeof item.url!=='string'||!/^https:\/\/github\.com\/mikecarper\/MeshCore\/releases\/download\/[^/?#]+\/[^/?#]+$/i.test(item.url))continue;
      const names=envNames(item.name,false);let match=false;for(const name of names){if(!ids.has(name))ids.set(name,await idForEnv(name));if(ids.get(name)===dest.targetId){match=true;break;}}
      if(match&&!seen.has(item.url)){seen.add(item.url);matches.push(item);}
    }
    if(!matches.length)return null;if(matches.length>8)throw Error('Too many possible installed firmware images; select the exact base manually');
    const index=await json('firmware/index.json',4*1024*1024,fetchImpl,deadline);let total=0,found=null;
    for(const item of matches){let entry,pieces;try{({entry,pieces}=mirrorEntry(item,index));}catch(e){if(/not available/.test(e.message))continue;throw e;}
      const budget=Math.max(entry.size,pieces.reduce((n,p)=>n+Math.max(p.size,p.gzipSize),0));
      if((total+=budget)>16*1024*1024)throw Error('Installed firmware search is too large; select the exact base manually');
      const image=await download(item,index,fetchImpl,deadline);
      if(image.targetId!==dest.targetId||image.hwId!==dest.hwId||image.fwVersion!==dest.version||hex(image.bodyHash)!==dest.baseHash.toLowerCase())continue;
      if(found&&hex(found.identity.imageHash)!==hex(image.imageHash))throw Error('More than one installed image matches the reported short hash');
      found={name:item.name.replace(/\.zip$/i,'.bin'),bytes:image.image,identity:image};
    }
    return found;
  }
  return {discover,load,recommend,recommendVerified,findInstalled,envNames,idForEnv};
});
