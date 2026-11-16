'use strict';
const assert=require('node:assert/strict');
const {prefix,scan,localPrefixes,detectPlatform,platformHelp}=require('../docs/_javascript/lora_ota_wifi_scan.js');

async function main(){
  assert.equal(prefix('192.168.1.0/24'),'192.168.1');
  assert.equal(prefix('192.168.1.123'),'192.168.1');
  assert.equal(prefix('192.168.1.123/24'),'192.168.1');
  assert.equal(prefix('10.20.30'),'10.20.30');
  for(const bad of ['8.8.8.0/24','172.32.1.0/24','192.168.01.0/24','192.168.1.999','192.168.1.0/16','example.com'])
    assert.throws(()=>prefix(bad));
  assert.equal(detectPlatform({userAgent:'Mozilla iPhone'}),'apple');
  assert.equal(detectPlatform({userAgent:'Macintosh',platform:'MacIntel',maxTouchPoints:5}),'apple');
  assert.equal(detectPlatform({userAgent:'Mozilla Android Linux'}),'android');
  assert.equal(detectPlatform({userAgent:'Mozilla Windows'}),'windows');
  assert.equal(detectPlatform({platform:'MacIntel',maxTouchPoints:0}),'mac');
  assert.equal(detectPlatform({userAgent:'Linux'}),'linux');
  assert.equal(detectPlatform({}),'unknown');
  for(const value of ['apple','android','windows','mac','linux','unknown'])assert(platformHelp[value]);

  class Peer {
    constructor(config){assert.deepEqual(config,{iceServers:[]});}
    createDataChannel(){} async createOffer(){return {};}
    async setLocalDescription(){
      for(const address of ['192.168.1.5','host-abc.local','10.0.1.8','8.8.8.8','192.168.1.7'])
        this.onicecandidate({candidate:{candidate:`candidate:1 1 udp 42 ${address} 123 typ host`}});
      this.onicecandidate({candidate:null});
    }
    close(){this.closed=true;}
  }
  assert.deepEqual(await localPrefixes({Peer}),['10.0.1','192.168.1']);
  class Mdns extends Peer {async setLocalDescription(){this.onicecandidate({candidate:{candidate:'candidate:1 1 udp 42 hidden.local 123 typ host'}});this.onicecandidate({candidate:null});}}
  assert.deepEqual(await localPrefixes({Peer:Mdns}),[]);
  assert.deepEqual(await localPrefixes({Peer:null}),[]);
  class Hung extends Peer {async createOffer(){return new Promise(()=>{});}}
  assert.deepEqual(await localPrefixes({Peer:Hung,timeout:10}),[]);

  let active=0,max=0,calls=0;
  const fetch=async(url,options)=>{
    calls++;active++;max=Math.max(max,active);
    assert.equal(options.method,'GET');assert.equal(options.credentials,'omit');
    assert.equal(options.targetAddressSpace,'local');assert.equal(options.redirect,'error');
    await new Promise(resolve=>setTimeout(resolve,1));active--;
    const body=url.includes('.42:')?'{"meshcoreOta":1}':'{"other":1}';
    return new Response(body,{status:200});
  };
  let progress=0;
  const found=await scan('192.168.1.0/24',{fetch,onProgress:n=>progress=n});
  assert.deepEqual(found,['192.168.1.42']);assert.equal(calls,254);assert.equal(progress,254);assert(max<=8);
  const controller=new AbortController();
  const pending=scan('192.168.1',{signal:controller.signal,fetch:(_url,{signal})=>new Promise((resolve,reject)=>{
    signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true});
  })});
  setTimeout(()=>controller.abort(),10);
  await assert.rejects(pending,/Scan canceled/);
  console.log('PASS bounded private-network scan and local-IP guidance');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
