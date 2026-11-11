'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto').webcrypto;
const mota=require('../docs/_javascript/lora_ota_package.js');
const delta=require('../docs/_javascript/lora_ota_delta.js');
const {ChainRunner}=require('../docs/_javascript/lora_ota_chain.js');
const {DirectSession}=require('../docs/_javascript/lora_ota_session.js');

const source='aa'.repeat(32), target='bb'.repeat(32);
const capability={otaConfirmed:true,nrf:false,external:false,sd:false,maxBlock:1024,codecs:[0,1]};
async function image(version,change) {
  const bytes=new Uint8Array(4152), end=bytes.length-56, v=new DataView(bytes.buffer);
  for(let i=0;i<end;i++)bytes[i]=(i*29+7)&255;
  for(let i=0;i<change;i++)bytes[100+i]^=version&255;
  bytes.set(Buffer.from('EndF'),end);v.setUint32(end+4,end,true);
  bytes.set(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes.slice(0,end))).slice(0,8),end+8);
  v.setUint32(end+16,version,true);v.setUint32(end+20,0x12345678,true);bytes.set(Buffer.from('board'),end+24);
  return bytes;
}
async function fixture() {
  const a=await image(0x01110001,0), b=await image(0x01110002,8), c=await image(0x01110003,16);
  const fs=await Promise.all([a,b,c].map(x=>mota.parseEndF(x)));
  const nodes=fs.map((f,i)=>({id:'abc'[i],version:f.fwVersion,targetId:f.targetId,hwId:f.hwId,
    bodyHash:Buffer.from(f.bodyHash).toString('hex'),imageHash:Buffer.from(f.imageHash).toString('hex'),validated:true,capabilities:capability}));
  const steps=[];
  for(let i=0;i<2;i++) {
    const built=await delta.buildSequentialDelta(fs[i].image,fs[i+1].image), info=await mota.parseMota(built.blob);
    const packageHash=Buffer.from(await crypto.subtle.digest('SHA-256',info.blob)).toString('hex');
    steps.push({from:nodes[i].id,to:nodes[i+1].id,packageHash,package:{info,blob:info.blob},edge:{nrfGeometry:null},targetImage:fs[i+1].image});
  }
  let live={...capability,...nodes[0]}, chain=null, stepRecord=null, failPull=false, failInstall=false, applyBeforeFail=false;
  let rearmFails=false, rearmConfirmed=true;
  const calls=[], pulls=[], installs=[], rearms=[];
  const makeRecord=(step,phase)=>({phase,source,target,id:Buffer.from(step.package.info.merkleRoot).toString('hex'),packageHash:step.packageHash});
  const readLive=async ({stage,index})=>{calls.push('live '+stage+' '+index);return {...live};};
  const link={sourceKey:source};
  const readStepJournal=()=>stepRecord, writeStepJournal=r=>{stepRecord=r;};
  const createSession=async (step,{index})=>({link,key:target,info:step.package.info,readJournal:readStepJournal,journal:writeStepJournal,
    async start({resume}) {
      calls.push('start '+index+' '+resume);
      if (!resume) {pulls.push(index); stepRecord=makeRecord(step,'pull attempted');}
      if (failPull) {failPull=false;throw Error('request timed out');}
      stepRecord=makeRecord(step,'ready');
    },
    async install() {
      calls.push('install '+index); installs.push(index); stepRecord=makeRecord(step,'install attempted');
      if (failInstall) {failInstall=false;if(applyBeforeFail)live={...capability,...nodes[index+1]};throw Error('install reply timed out');}
      live={...capability,...nodes[index+1]}; stepRecord=makeRecord(step,'installed');
    },
    async inspect() {
      calls.push('inspect '+index);
      if(live.version===nodes[index+1].version) {stepRecord=makeRecord(step,'installed');return {download:'installed'};}
      return {download:'ready'};
    }
  });
  const args={prepared:{nodes,steps},link,targetKey:target,readLive,createSession,
    readStepJournal,writeStepJournal,readChainJournal:()=>chain,writeChainJournal:r=>{chain=r;calls.push('journal '+r.phase+' '+r.index);},
    rearm:async ({index})=>{calls.push('rearm '+index);rearms.push(index);if(rearmFails){rearmFails=false;throw Error('rearm timeout');}},
    confirmRearm:async ({index})=>{calls.push('confirm '+index);return rearmConfirmed;}};
  return {args,calls,pulls,installs,rearms,get chain(){return chain;},get stepRecord(){return stepRecord;},
    failNextPull(){failPull=true;},failNextInstall(apply){failInstall=true;applyBeforeFail=apply;},
    failNextRearm(){rearmFails=true;},setRearmConfirmed(v){rearmConfirmed=v;},setLive(v){live=v;}};
}

test('executes a validated chain serially and rearms only after verified reboot',async()=>{
  const f=await fixture(), result=await new ChainRunner(f.args).run();
  assert.equal(result.phase,'complete'); assert.deepEqual(f.pulls,[0,1]);assert.deepEqual(f.installs,[0,1]);assert.deepEqual(f.rearms,[0]);
  assert.ok(f.calls.indexOf('live post-install 0')<f.calls.indexOf('rearm 0'));
  assert.ok(f.calls.indexOf('rearm 0')<f.calls.indexOf('start 1 false'));
  assert.equal(f.chain.phase,'complete');assert.equal(f.chain.index,1);
});

test('uncertain pull resumes without a second initial pull',async()=>{
  const f=await fixture();f.failNextPull();
  await assert.rejects(new ChainRunner(f.args).run(),/timed out/);assert.equal(f.chain.phase,'step starting');
  await new ChainRunner(f.args).run({resume:true});
  assert.deepEqual(f.pulls,[0,1]);assert.ok(f.calls.includes('start 0 true'));
});

test('recovery rejects changed version metadata or physical route',async()=>{
  const f=await fixture();f.failNextPull();
  await assert.rejects(new ChainRunner(f.args).run(),/timed out/);
  f.args.prepared.nodes[2].version++;
  await assert.rejects(new ChainRunner(f.args).run({resume:true}),/matching chain recovery/);
  f.args.prepared.nodes[2].version--;
  f.args.link.relays=['cc'.repeat(32)];
  await assert.rejects(new ChainRunner(f.args).run({resume:true}),/matching chain recovery/);
});

test('uncertain install is inspected, never sent twice',async()=>{
  const f=await fixture();f.failNextInstall(true);
  await assert.rejects(new ChainRunner(f.args).run(),/install reply/);
  await new ChainRunner(f.args).run({resume:true});
  assert.deepEqual(f.installs,[0,1]);assert.ok(f.calls.includes('inspect 0'));
});

test('unconfirmed install stops without retry or rearm',async()=>{
  const f=await fixture();f.failNextInstall(false);
  await assert.rejects(new ChainRunner(f.args).run(),/install reply/);
  await assert.rejects(new ChainRunner(f.args).run({resume:true}),/Prior install is unconfirmed/);
  assert.deepEqual(f.installs,[0]);assert.deepEqual(f.rearms,[]);
});

test('uncertain rearm requires read-only confirmation instead of retry',async()=>{
  const f=await fixture();f.failNextRearm();
  await assert.rejects(new ChainRunner(f.args).run(),/rearm timeout/);assert.equal(f.chain.phase,'rearm attempted');
  f.setRearmConfirmed(false);
  await assert.rejects(new ChainRunner(f.args).run({resume:true}),/rearm is unconfirmed/);
  assert.deepEqual(f.rearms,[0]);assert.deepEqual(f.installs,[0]);
  f.setRearmConfirmed(true);await new ChainRunner(f.args).run({resume:true});
  assert.deepEqual(f.rearms,[0]);assert.deepEqual(f.installs,[0,1]);
});

test('mismatched source, target or modified package stops before a pull',async()=>{
  const f=await fixture(); f.args.prepared.steps[0].package.blob[0]^=1;
  await assert.rejects(new ChainRunner(f.args).run(),/package changed/);assert.deepEqual(f.pulls,[]);
  const g=await fixture();g.setLive({...capability,...g.args.prepared.nodes[1]});
  await assert.rejects(new ChainRunner(g.args).run(),/no longer matches/);assert.deepEqual(g.pulls,[]);
  const h=await fixture();await assert.rejects(new ChainRunner(h.args).run({resume:true}),/recovery record/);
});

test('cancellation asks the active session to stop before any install',async()=>{
  const f=await fixture(), controller=new AbortController();
  let release,started,stopped=0,installs=0;
  const startReached=new Promise(r=>{started=r;});
  f.args.createSession=async step=>({link:f.args.link,key:target,info:step.package.info,readJournal:f.args.readStepJournal,journal:f.args.writeStepJournal,
    async start(){started();await new Promise(r=>{release=r;});},
    async install(){installs++;},async inspect(){return {download:'idle'};},
    stop(){stopped++;release();}
  });
  const task=new ChainRunner(f.args).run({signal:controller.signal});
  await startReached; controller.abort();
  await assert.rejects(task,/Chain stopped/); assert.equal(stopped,1); assert.equal(installs,0);
  assert.equal(f.chain.phase,'step starting');
});

test('rejects a session factory bound to another package, source, target or journal',async()=>{
  for(const wrong of ['package','link','key','journal','reader']) {
    const f=await fixture(), original=f.args.createSession;
    f.args.createSession=async (step,context)=>{
      const session=await original(step,context);
      if(wrong==='package') session.info=f.args.prepared.steps[1].package.info;
      if(wrong==='link') session.link={sourceKey:source};
      if(wrong==='key') session.key='cc'.repeat(32);
      if(wrong==='journal') session.journal=()=>{};
      if(wrong==='reader') session.readJournal=()=>null;
      return session;
    };
    await assert.rejects(new ChainRunner(f.args).run(),/selected/);
    assert.deepEqual(f.pulls,[]);
  }
});

test('actual DirectSession installs a two-image chain with independent journals',async()=>{
  const f=await fixture();
  let node=f.args.prepared.nodes[0], state='idle', served=null, installs=0, resets=0, rearm=0;
  const radio='910.525,62.5,7,5,120', config='autofetch=off autoinstall=off hops=0';
  const version=n=>`v${n>>>24}.${(n>>>16)&255}.${(n>>>8)&255}.${n&255}`;
  const link={sourceKey:source,
    async verifyPeer(k){assert.equal(k,target);}, async resetRemoteSession(k){assert.equal(k,target);resets++;},
    async localCommand(c){return c==='get radio'||c==='get tempradio'?radio:c==='get radio.rxps'?'off':c==='ota stats'?'fetch idle':c==='ota folder'?'folder=off':config;},
    async remoteCommand(k,p,c){
      assert.equal(k,target);
      const id=served?Buffer.from(served.merkleRoot).toString('hex'):'';
      if(c==='ota status')return `hw=board target:12345678 maxblk:1024 ${state==='idle'?'no download':'download: ready to install id='+id}`;
      if(c==='ota self')return 'base_hash='+node.bodyHash;
      if(c==='ver')return version(node.version);
      if(c==='get bootloader.ver')return 'ERR unsupported';
      if(c==='get radio')return radio.split(',').slice(0,4).join(',');if(c==='get tempradio')return radio;
      if(c==='get radio.rxps')return 'off';if(c==='ota config')return config;
      if(c==='ota installhash')return 'ERR usage: ota installhash <64-hex-container-sha256>';
      if(c==='ota ls')return id;
      if(c.startsWith('ota pull ')){state='ready';return 'OK';}
      if(/^ota installhash [0-9a-f]{64}$/.test(c)){installs++;node=f.args.prepared.nodes.find(n=>n.version===served.fwVersion);state='idle';return 'OK';}
      throw Error('Unexpected '+c);
    },
    async serve(info){served=info;},async stopServing(){}
  };
  f.args.link=link;
  f.args.readLive=async()=>({...capability,...node});
  f.args.createSession=async step=>new DirectSession(link,target,'',step.package.info,{image:step.targetImage,pollMs:1,
    journal:f.args.writeStepJournal,readJournal:f.args.readStepJournal});
  f.args.rearm=async ({index})=>{assert.equal(node.id,'b');assert.equal(index,0);rearm++;};
  const result=await new ChainRunner(f.args).run();
  assert.equal(result.phase,'complete');assert.equal(node.id,'c');assert.equal(installs,2);assert.equal(resets,2);assert.equal(rearm,1);
});
