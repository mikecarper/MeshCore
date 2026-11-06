/* Bounded zero-hop mOTA source for a binary USB Companion. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LoraOtaCompanionSource = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const ADV=1, QUERY=2, HAVE=3, GET_MANIFEST=4, MANIFEST=5, REQ=6, DATA=7,
    REQ_PROOF=8, PROOF=9, GET_LEAVES=10, LEAVES=11;
  const api = globalThis.crypto?.subtle || (typeof require === 'function' ? require('node:crypto').webcrypto.subtle : null);
  const concat=(...parts)=>{const a=new Uint8Array(parts.reduce((n,p)=>n+p.length,0));let at=0;for(const p of parts){a.set(p,at);at+=p.length;}return a;};
  const equal=(a,b)=>a.length===b.length&&a.every((v,i)=>v===b[i]);
  const u16=(a,n)=>a[n]|a[n+1]<<8;
  const le16=n=>Uint8Array.of(n&255,(n>>>8)&255);
  const le32=n=>Uint8Array.of(n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255);
  async function mh4(data) { if(!api)throw Error('Web Crypto is unavailable');return new Uint8Array(await api.digest('SHA-256',data)).slice(0,4); }
  function decode(raw) {
    if (!(raw instanceof Uint8Array) || raw.length<3 || raw.length>186 || ![0x31,0x32].includes(raw[0]) || raw[1]!==0) return null;
    const msg=raw.subarray(2); return msg[0]>=ADV&&msg[0]<=LEAVES ? msg : null;
  }
  function wrap(message) {
    if (!(message instanceof Uint8Array)||message.length<1||message.length>184) throw Error('Invalid OTA radio message');
    return concat(Uint8Array.of(0x32,0),message);
  }
  class Source {
    constructor(info, seeder, digest, levels, compress) {
      this.info=info; this.seeder=seeder;this.digest=digest;this.levels=levels;this.compress=compress;
      this.mid=info.merkleRoot;this.encoded=new Map();
    }
    static async create(info, sourceKey, options={}) {
      if (!info || !(info.blob instanceof Uint8Array) || !(info.payload instanceof Uint8Array) ||
          !(info.merkleRoot instanceof Uint8Array) || info.merkleRoot.length!==4 ||
          ![1024,2048].includes(info.blockSize) || !Number.isInteger(info.blockCount) ||
          info.blockCount<1 || info.blockCount>65535 || !/^[0-9a-f]{64}$/i.test(sourceKey))
        throw Error('A validated OTA package and full Companion public key are required');
      const sha=new Uint8Array(await api.digest('SHA-256',info.blob));
      const seeder=await mh4(concat(new TextEncoder().encode(sourceKey),sha));
      const digest=await mh4(info.merkleRoot);
      let level=[];for(let i=0;i<info.blockCount;i++)level.push(info.blob.slice(205+4*i,209+4*i));
      const levels=[level];
      while(level.length>1) {
        const next=await Promise.all(Array.from({length:Math.ceil(level.length/2)},(_,i)=>
          2*i+1<level.length?mh4(concat(level[2*i],level[2*i+1])):Promise.resolve(level[2*i])));
        levels.push(next);level=next;
      }
      if(!equal(levels[levels.length-1][0],info.merkleRoot))throw Error('The OTA Merkle tree does not match the package');
      return new Source(info,seeder,digest,levels,options.compress || null);
    }
    advert() { return wrap(concat(Uint8Array.of(ADV),this.seeder,Uint8Array.of(1),this.digest)); }
    proof(index) {
      if(index<0||index>=this.info.blockCount)throw Error('Invalid OTA block');
      let at=index, siblings=[];
      for(const level of this.levels.slice(0,-1)) {
        const other=at^1;if(other<level.length)siblings.push(level[other]);at=Math.floor(at/2);
      }
      if(siblings.length>255)throw Error('OTA proof is too long');
      return concat(Uint8Array.of(PROOF),this.mid,le16(index),Uint8Array.of(siblings.length),...siblings);
    }
    fragments(op, body, mask) {
      const count=Math.ceil(body.length/176), result=[];
      if(count<1||count>16)throw Error('Unsupported metadata size');
      for(let i=0;i<count;i++)if(mask&(1<<i))
        result.push(concat(Uint8Array.of(op),this.mid,Uint8Array.of(i,count),body.subarray(i*176,(i+1)*176)));
      return result;
    }
    async blocks(index,mask) {
      if(index>=this.info.blockCount)throw Error('Invalid OTA block');
      const v2=!!(mask&0x8000)&&(!(mask&0x2000)||!(mask&0x1000)), extended=v2&&!!(mask&0x2000);
      if(v2 && extended!==(this.info.blockSize>1024))throw Error('Invalid OTA fragment geometry');
      const raw=this.info.payload.subarray(index*this.info.blockSize,(index+1)*this.info.blockSize);
      let encoded=raw, compressed=false;
      if(v2&&(mask&0x4000)&&this.compress) {
        if(!this.encoded.has(index)) {
          let candidate;try { candidate=new Uint8Array(await this.compress(raw)); } catch (_) { candidate=raw; }
          const use=candidate.length>0&&candidate.length<raw.length;
          this.encoded.set(index,{data:use?candidate:raw.slice(),compressed:use});
        }
        const cached=this.encoded.get(index);encoded=cached.data;compressed=cached.compressed;
      }
      const width=v2?171:160, requested=(v2?mask&(extended?0x0fff:0x1fff):mask) & ((1<<Math.ceil(raw.length/width))-1);
      const stream=v2?await mh4(encoded):null, out=[];
      for(let i=0,at=0;at<encoded.length;i++,at+=width) {
        if(!(requested&(1<<i)))continue;
        const offset=v2?(0x8000|(i<<(extended?11:10))|(encoded.length-1)|(!extended&&compressed?0x4000:0)):at;
        out.push(concat(Uint8Array.of(DATA),this.mid,le16(index),le16(offset),...(v2?[stream]:[]),encoded.subarray(at,at+width)));
      }
      if(requested)out.push(this.proof(index));
      return out;
    }
    async replies(radio) {
      const m=decode(radio); if(!m)return [];
      try {
        if(m[0]===QUERY) {
          if(![13,17].includes(m.length))return [];
          if(!equal(m.subarray(1,5),this.seeder)||!equal(m.subarray(5,9),this.digest))return [];
          const view=new DataView(m.buffer,m.byteOffset,m.byteLength), target=view.getUint32(9,true),
            wanted=m.length===17?view.getUint32(13,true):0;
          if(wanted&&!(wanted&1))return [];
          const rows=target===0||target===this.info.targetId ? concat(this.mid,le32(this.info.targetId),
            le32(this.info.fwVersion),Uint8Array.of(this.info.codecId,this.info.flags),le16(this.info.blockCount)) : new Uint8Array();
          return [wrap(concat(Uint8Array.of(HAVE),this.seeder,this.digest,Uint8Array.of(0,1,rows.length?1:0),rows))];
        }
        if(![GET_MANIFEST,GET_LEAVES,REQ,REQ_PROOF].includes(m[0])||m.length<5||!equal(m.subarray(1,5),this.mid))return [];
        let frames=[];
        if(m[0]===GET_MANIFEST||m[0]===GET_LEAVES) {
          if(m.length!==7)return [];
          frames=this.fragments(m[0]===GET_MANIFEST?MANIFEST:LEAVES,
            m[0]===GET_MANIFEST?this.info.blob.subarray(8,205):this.info.blob.subarray(205,205+4*this.info.blockCount),u16(m,5));
        } else if(m[0]===REQ_PROOF) {
          if(m.length!==7)return [];frames=[this.proof(u16(m,5))];
        } else {
          if(m.length<9||(m.length-5)%4||(m.length-5)/4>4)return [];
          for(let i=5;i<m.length;i+=4)frames.push(...await this.blocks(u16(m,i),u16(m,i+2)));
        }
        if(frames.length>64)return [];
        return frames.map(wrap);
      } catch (_) { return []; }
    }
  }
  return {Source,decode,wrap};
});
