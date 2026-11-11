'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {collectAdminNeighbors}=require('../docs/_javascript/lora_ota_collect.js');
const {rankRoutes}=require('../docs/_javascript/lora_ota_discovery.js');
const key=p=>p.padEnd(64,'0'),S=key('11111111'),A=key('22222222'),B=key('33333333'),C=key('44444444'),T=key('55555555');
const row=(k,snr)=>`${k.slice(0,8)}:5:${snr}`;
class FakeLink {
  constructor(){this.running=true;this.aclOnly=true;this.supportsRoutedPath=true;this.sourceKey=S;this.path='off';this.calls=[];this.proof=[];this.clockChecked=false;}
  async withAdmin(fn){return fn();}
  async checkClock(){this.clockChecked=true;}
  async localCommand(c){
    this.calls.push('local '+c);
    if(c==='ota.peer path')return 'OTApeer path='+this.path;
    if(c==='ota.peer status')return 'OTApeer v=1 session=0 busy=0 peers=0';
    if(c.startsWith('ota.peer path ')){const p=c.slice(14);if(p===this.failPath)return 'ERR path';this.path=p;return 'OTApeer path='+this.path;}
    if(c==='neighbors')return [row(A,20),row(B,40)].join('\n');
    throw Error('unexpected '+c);
  }
  async job(c){
    this.calls.push('job '+c);
    if(c.startsWith('ota.peer acl ')){
      const target=c.slice(13);this.proof.push({target,path:this.path});
      if(this.deny===target)throw Error('ACL denied');
      if(this.timeout===target)throw Error('request timed out; result is unknown');
      return 'OK';
    }
    if(c.startsWith('ota.peer cmd.acl ')){
      const target=c.split(' ')[2];
      if(this.hijack===target)this.path='ABCDEF';
      if(target===A)return [row(S,16),row(T,8)].join('\n');
      if(target===B)return [row(S,36),row(T,28)].join('\n');
      if(target===T)return [row(A,12),row(B,32),...(this.followExtra?[row(C,8)]:[])].join('\n');
      if(target===C)return row(T,8);
    }
    throw Error('unexpected '+c);
  }
}
const settings=link=>({link,allowlist:[S,A,B,T],minSnrDb:-5,maxAgeSeconds:900,maxRelays:3,now:()=>1000});

test('collects only allowlisted ACL-proved neighbors and restores own path',async()=>{
  const link=new FakeLink(),result=await collectAdminNeighbors(settings(link));
  assert.equal(result.truncated,false);assert.equal(result.queries,4);assert.equal(result.attempts,3);
  assert.equal(result.observations.length,8);assert.equal(link.path,'off');assert(link.clockChecked);
  assert.deepEqual(link.proof.find(p=>p.target===T).path,B.slice(0,6).toUpperCase());
  const ranked=rankRoutes({source:S,target:T,allowlist:[S,A,B,T],observations:result.observations,minSnrDb:-5,nowMs:1000});
  assert.deepEqual(ranked.routes.map(r=>r.relays),[[B],[A]]);
  assert(!link.calls.some(c=>/discover|ota pull|ota install|set radio|reboot/.test(c)));
});

test('preserves pre-existing source path without any writes or RF',async()=>{
  const link=new FakeLink();link.path='ABCDEF';
  await assert.rejects(collectAdminNeighbors(settings(link)),/already configured/);
  assert.equal(link.path,'ABCDEF');assert.equal(link.calls.filter(c=>c.startsWith('job ')).length,0);
  assert(!link.calls.some(c=>c.startsWith('local ota.peer path ')));
});

test('does not overwrite another controller path during cleanup',async()=>{
  const link=new FakeLink();link.hijack=T;
  await assert.rejects(collectAdminNeighbors(settings(link)),/could not be safely restored/);
  assert.equal(link.path,'ABCDEF');assert.notEqual(link.calls.at(-1),'local ota.peer path off');
});

test('caps collection and skips explicit ACL denials without unbounded queries',async()=>{
  const link=new FakeLink(),result=await collectAdminNeighbors({...settings(link),maxQueries:1});
  assert.equal(result.queries,1);assert.equal(result.attempts,0);assert.equal(result.truncated,true);assert.equal(link.path,'off');
  const second=new FakeLink();second.deny=A;
  const partial=await collectAdminNeighbors(settings(second));assert.deepEqual(partial.unavailable,[A]);assert.equal(second.path,'off');
});

test('ambiguous or timed-out proof stops rather than assuming failure',async()=>{
  const link=new FakeLink();link.timeout=B;
  await assert.rejects(collectAdminNeighbors(settings(link)),/timed out/);assert.equal(link.path,'off');
});

test('a rejected next path restores our previously confirmed path',async()=>{
  const link=new FakeLink();link.followExtra=true;link.failPath='333333,555555';
  await assert.rejects(collectAdminNeighbors({...settings(link),allowlist:[S,A,B,C,T]}),/Could not confirm/);
  assert.equal(link.path,'off');assert.equal(link.calls.at(-1),'local ota.peer path off');
});
