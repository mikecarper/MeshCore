'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {parseNeighbors,rankRoutes,validateManualRoute}=require('../docs/_javascript/lora_ota_discovery.js');
const k=p=>p.padEnd(64,'0');
const S=k('11111111'),A=k('22222222'),B=k('33333333'),C=k('44444444'),T=k('55555555');
const obs=(observer,neighbor,snrQuarterDb,ageSeconds=10)=>({observer,prefix:neighbor.slice(0,8),snrQuarterDb,ageSeconds,observedAtMs:1000});
const pair=(a,b,ab,ba,age=10)=>[obs(b,a,ab,age),obs(a,b,ba,age)];
const opts=observations=>({source:S,target:T,allowlist:[S,A,B,C,T],observations,minSnrDb:-5,maxAgeSeconds:900,limit:10,maxRelays:4,nowMs:1000});

test('parses bounded zero-hop neighbor rows and signed quarter-dB SNR',()=>{
  assert.deepEqual(parseNeighbors(S,'> 22222222:41:-17\n33333333:0:25',1000),[
    {observer:S,prefix:'22222222',ageSeconds:41,snrQuarterDb:-17,observedAtMs:1000},
    {observer:S,prefix:'33333333',ageSeconds:0,snrQuarterDb:25,observedAtMs:1000}]);
  assert.deepEqual(parseNeighbors(S,'-none-'),[]);
  for(const bad of ['2222:2:1','22222222:-1:0','22222222:1:128','22222222:1:-129','garbage'])
    assert.throws(()=>parseNeighbors(S,bad));
});

test('prefers fewer relays above floor then strongest bidirectional bottleneck',()=>{
  const rows=[...pair(S,A,0,4),...pair(A,T,-4,4),...pair(S,B,40,36),...pair(B,T,32,28),
    ...pair(S,C,48,48),...pair(C,B,48,48),...pair(S,T,-16,-12)];
  const found=rankRoutes(opts(rows)).routes;
  assert.equal(found[0].hops,0);assert.equal(found[0].linkCount,1);assert.deepEqual(found[0].relays,[]);assert.equal(found[0].bottleneckSnrDb,-4);
  assert.deepEqual(found[1].relays,[B]);assert.equal(found[1].bottleneckSnrDb,7);
  assert.deepEqual(found[2].relays,[A]);assert.equal(found[2].bottleneckSnrDb,-1);
  const stricter=rankRoutes({...opts(rows),minSnrDb:0}).routes;
  assert.deepEqual(stricter[0].relays,[B]);
});

test('requires fresh measurements in both directions and rejects unknown entries',()=>{
  const rows=[...pair(S,A,8,8),obs(T,A,4),...pair(S,B,8,8),...pair(B,T,8,8,901),
    obs(S,k('66666666'),50),{observer:S,prefix:'oops',ageSeconds:1,snrQuarterDb:1}];
  const found=rankRoutes(opts(rows));assert.deepEqual(found.routes,[]);
  assert.equal(found.rejected.stale,2);assert.equal(found.rejected.unknown,1);assert.equal(found.rejected.invalid,1);
});

test('uses newest sample per directed link and conservative tie handling',()=>{
  const rows=[...pair(S,A,16,16,20),obs(A,S,-17,5),obs(A,S,-8,5),...pair(A,T,20,20)];
  const route=rankRoutes(opts(rows)).routes[0];
  assert.equal(route.bottleneckSnrDb,-4.25);
  assert.equal(route.links[0].snrDb,-4.25);
  assert.equal(route.links[0].reverseSnrDb,4);
});

test('ages observations while gathering other nodes',()=>{
  const options={...opts([...pair(S,A,8,8),...pair(A,T,8,8)]),nowMs:901000};
  assert.deepEqual(rankRoutes(options).routes,[]);
  assert.equal(rankRoutes(options).rejected.stale,4);
});

test('excludes known colliding 3-byte route prefixes, including manual path',()=>{
  const collision=k('22222299');
  const options={...opts([...pair(S,A,4,4),...pair(A,T,4,4)]),allowlist:[S,A,collision,T]};
  const result=rankRoutes(options);assert.deepEqual(result.routes,[]);assert.ok(result.rejected.ambiguous>=2);
  assert.throws(()=>validateManualRoute(options,[A]),/ambiguous/);
  assert.throws(()=>rankRoutes({...options,allowlist:[S,A,collision,T,k('11111199')]}),/ambiguous route prefix/);
});

test('manual path applies same freshness, quality and allowlist checks',()=>{
  const options=opts([...pair(S,A,12,12),...pair(A,T,8,8),...pair(S,B,4,4),...pair(B,T,4,4)]);
  assert.deepEqual(validateManualRoute(options,[A]).relays,[A]);
  assert.throws(()=>validateManualRoute(options,[C]),/unknown/);
  assert.throws(()=>validateManualRoute(options,[k('99999999')]),/allowlisted/);
  assert.throws(()=>validateManualRoute(options,[A,A]),/distinct/);
  assert.throws(()=>rankRoutes({...options,minSnrDb:undefined}),/quality/);
});
