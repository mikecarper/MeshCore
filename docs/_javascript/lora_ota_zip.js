/* Bounded local Nordic DFU ZIP reader. No network access or filesystem writes. */
(function(root, factory) {
  const node = typeof module === 'object' && module.exports;
  const api = factory(node ? require('./lora_ota_package.js') : root.MeshCoreMotaPackage);
  if (node) module.exports = api; else root.MeshCoreLoraOtaZip = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function(mota) {
  'use strict';
  const MAX_ARCHIVE = 8 * 1024 * 1024, MAX_TOTAL = 8 * 1024 * 1024;
  const MAX_IMAGE = 4 * 1024 * 1024, MAX_MANIFEST = 64 * 1024, MAX_ENTRIES = 128;
  const decoder = new TextDecoder('utf-8', {fatal:true});
  const crcTable = Uint32Array.from({length:256}, (_, n) => {
    let c = n; for (let i=0; i<8; i++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1)); return c >>> 0;
  });
  function crc32(bytes) {
    let c = 0xffffffff; for (const b of bytes) c = (c >>> 8) ^ crcTable[(c ^ b) & 255];
    return (c ^ 0xffffffff) >>> 0;
  }
  function safeName(name) {
    if (!name || name.length > 512 || name.startsWith('/') || /[\\:\x00-\x1f\x7f]/.test(name)) return false;
    const parts = name.split('/');
    return parts.every((part, i) => part !== '.' && part !== '..' && (part !== '' || i === parts.length-1));
  }
  function parseJsonUnique(text) {
    // JSON.parse alone accepts duplicate object keys, which can give different
    // manifest consumers different application paths. Validate keys first.
    let i=0;
    const ws = () => { while (/\s/.test(text[i] || '') && i<text.length) i++; };
    function str() {
      const match = /"(?:[^"\\\u0000-\u001f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"/y;
      match.lastIndex=i; const m=match.exec(text); if (!m) throw Error('Invalid JSON manifest');
      i=match.lastIndex; return JSON.parse(m[0]);
    }
    function item(depth) {
      if (depth > 32) throw Error('JSON manifest is too deeply nested');
      ws(); const c=text[i];
      if (c === '"') { str(); return; }
      if (c === '{') {
        i++; ws(); const keys=new Set(); if (text[i] === '}') { i++; return; }
        for (;;) {
          ws(); const key=str(); if(keys.has(key)) throw Error('Duplicate JSON manifest key'); keys.add(key);
          ws(); if(text[i++] !== ':') throw Error('Invalid JSON manifest'); item(depth+1); ws();
          const next=text[i++]; if(next === '}') return; if(next !== ',') throw Error('Invalid JSON manifest');
        }
      }
      if (c === '[') {
        i++; ws(); if(text[i] === ']') { i++; return; }
        for (;;) { item(depth+1); ws(); const next=text[i++]; if(next === ']') return; if(next !== ',') throw Error('Invalid JSON manifest'); }
      }
      const match=/(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/y;
      match.lastIndex=i; if(!match.exec(text)) throw Error('Invalid JSON manifest'); i=match.lastIndex;
    }
    item(0); ws(); if(i !== text.length) throw Error('Invalid JSON manifest');
    return JSON.parse(text);
  }
  async function inflate(input, expected) {
    if (typeof DecompressionStream !== 'function') throw Error('This browser cannot decompress ZIP files');
    let stream;
    try { stream = new Blob([input]).stream().pipeThrough(new DecompressionStream('deflate-raw')); }
    catch (_) { throw Error('This browser cannot decompress ZIP files'); }
    const reader=stream.getReader(), chunks=[]; let length=0;
    try {
      for (;;) {
        const next=await reader.read(); if(next.done) break;
        length+=next.value.length;
        if(length>expected || length>MAX_TOTAL) throw Error('ZIP member exceeds its declared size');
        chunks.push(next.value);
      }
    } catch (error) { await reader.cancel().catch(() => {}); throw error; }
    if(length!==expected) throw Error('ZIP member has an incorrect uncompressed size');
    const result=new Uint8Array(length); let offset=0;
    for(const chunk of chunks){result.set(chunk,offset);offset+=chunk.length;} return result;
  }
  async function loadApplication(input, options={}) {
    const data=input instanceof Uint8Array ? input : input instanceof ArrayBuffer ? new Uint8Array(input) : null;
    if(!data || data.length<22 || data.length>MAX_ARCHIVE) throw Error('Invalid or oversized application ZIP');
    const view=new DataView(data.buffer,data.byteOffset,data.byteLength), u16=p=>view.getUint16(p,true), u32=p=>view.getUint32(p,true);
    let end=-1;
    for(let p=data.length-22, min=Math.max(0,data.length-65557); p>=min; p--) {
      if(u32(p)===0x06054b50 && p+22+u16(p+20)===data.length){end=p;break;}
    }
    if(end<0 || u16(end+4)!==0 || u16(end+6)!==0 || u16(end+8)!==u16(end+10)) throw Error('Invalid or multi-disk ZIP');
    const count=u16(end+10), cdSize=u32(end+12), cdOffset=u32(end+16);
    if(!count || count>MAX_ENTRIES || cdSize>1024*1024 || cdOffset+cdSize!==end) throw Error('Unsupported ZIP directory');
    const entries=[], seen=new Set(), ranges=[]; let at=cdOffset, total=0;
    for(let j=0;j<count;j++) {
      if(at+46>end || u32(at)!==0x02014b50) throw Error('Invalid ZIP directory entry');
      const flags=u16(at+8), method=u16(at+10), crc=u32(at+16), compressed=u32(at+20), size=u32(at+24);
      const nlen=u16(at+28), xlen=u16(at+30), clen=u16(at+32), disk=u16(at+34), attrs=u32(at+38), local=u32(at+42);
      const after=at+46+nlen+xlen+clen;
      if(after>end || !nlen || disk!==0 || (flags & ~0x080e) || ![0,8].includes(method) || compressed===0xffffffff || size===0xffffffff || local===0xffffffff)
        throw Error('Unsupported ZIP entry');
      let name; try {name=decoder.decode(data.subarray(at+46,at+46+nlen));} catch(_){throw Error('Invalid ZIP filename');}
      if(!safeName(name)) throw Error('Unsafe ZIP path');
      const canonical=name.normalize('NFC').toLowerCase(); if(seen.has(canonical)) throw Error('Duplicate ZIP member'); seen.add(canonical);
      const unixMode=attrs>>>16; if((unixMode & 0xf000)===0xa000) throw Error('ZIP symlinks are not supported');
      if(size>MAX_IMAGE || (total+=size)>MAX_TOTAL || (name==='manifest.json' && size>MAX_MANIFEST)) throw Error('ZIP content is too large');
      if(local+30>cdOffset || u32(local)!==0x04034b50 || u16(local+6)!==flags || u16(local+8)!==method)
        throw Error('ZIP local header does not match directory');
      const localName=u16(local+26), localExtra=u16(local+28), start=local+30+localName+localExtra;
      if(start>cdOffset || compressed>cdOffset-start || localName!==nlen) throw Error('Invalid ZIP member bounds');
      for(let k=0;k<nlen;k++) if(data[local+30+k]!==data[at+46+k]) throw Error('ZIP member name mismatch');
      const localCrc=u32(local+14), localCompressed=u32(local+18), localSize=u32(local+22);
      if(!(flags & 8) && (localCrc!==crc || localCompressed!==compressed || localSize!==size)) throw Error('ZIP local member size mismatch');
      let stop=start+compressed;
      if(flags & 8) {
        if(stop+12>cdOffset) throw Error('Invalid ZIP data descriptor');
        if(u32(stop)===0x08074b50) stop+=4;
        if(stop+12>cdOffset || u32(stop)!==crc || u32(stop+4)!==compressed || u32(stop+8)!==size) throw Error('Invalid ZIP data descriptor');
        stop+=12;
      }
      if(ranges.some(([a,b])=>local<b && stop>a)) throw Error('Overlapping ZIP members');
      ranges.push([local,stop]);
      entries.push({name,flags,method,crc,size,start,compressed}); at=after;
    }
    if(at!==end) throw Error('ZIP directory length mismatch');
    const members=new Map();
    for(const entry of entries) {
      const raw=data.subarray(entry.start,entry.start+entry.compressed);
      if(entry.method===0 && raw.length!==entry.size) throw Error('ZIP stored member size mismatch');
      const bytes=entry.method===0 ? raw.slice() : await inflate(raw,entry.size);
      if(crc32(bytes)!==entry.crc) throw Error('ZIP member checksum mismatch');
      members.set(entry.name,bytes);
    }
    const manifest=members.get('manifest.json'); let name;
    if(manifest) {
      let parsed; try {parsed=parseJsonUnique(decoder.decode(manifest));} catch(e) {throw Error('Invalid manifest.json: '+e.message);}
      const contents=parsed && parsed.manifest, app=contents && contents.application;
      if(!app || typeof app!=='object' || Array.isArray(app) || typeof app.bin_file!=='string' ||
          !safeName(app.bin_file) || !/\.bin$/i.test(app.bin_file) || !members.has(app.bin_file) ||
          Object.prototype.hasOwnProperty.call(contents,'bootloader') || Object.prototype.hasOwnProperty.call(contents,'softdevice') ||
          Object.prototype.hasOwnProperty.call(contents,'softdevice_bootloader')) throw Error('ZIP is not a single application DFU');
      name=app.bin_file;
    } else {
      const candidates=[...members.keys()].filter(value=>/\.bin$/i.test(value));
      if(candidates.length!==1) throw Error('ZIP must contain one unambiguous application BIN');
      name=candidates[0];
    }
    if([...members.keys()].some(value=>/\.bin$/i.test(value) && value!==name)) throw Error('ZIP has multiple application images');
    const image=await mota.parseEndF(members.get(name));
    if(options.targetId!==undefined && options.targetId!==image.targetId) throw Error('Application target does not match');
    if(options.hwId!==undefined && options.hwId!==image.hwId) throw Error('Application hardware does not match');
    if(options.fwVersion!==undefined && options.fwVersion!==image.fwVersion) throw Error('Application version does not match');
    if(options.bodyHash!==undefined) {
      const actual=Array.from(image.bodyHash,b=>b.toString(16).padStart(2,'0')).join('');
      if(typeof options.bodyHash!=='string' || !/^[0-9a-f]{16}$/i.test(options.bodyHash) || actual!==options.bodyHash.toLowerCase())
        throw Error('Application body hash does not match');
    }
    return {...image, member:name};
  }
  return {loadApplication, crc32, MAX_ARCHIVE, MAX_TOTAL, MAX_IMAGE, MAX_ENTRIES};
});
