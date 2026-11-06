'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {planRoute, estimateEdge, siblingCount, airtime} = require('../docs/_javascript/lora_ota_plan.js');

const radio={sf:7,bandwidth:62.5,cr:5,preamble:32};
const caps={codecs:[0,1],maxBlock:2048,wireProfile:'v2',pipeline:2,deflate:false,proactiveProof:true};
const node=(id,version,ch)=>({id,version,targetId:0x12345678,hwId:'board',bodyHash:ch.repeat(16),imageHash:ch.repeat(64),validated:true,capabilities:{...caps}});
const a=node('a',1,'a'), b=node('b',2,'b'), c=node('c',3,'c');
const current={node:'a',version:1,targetId:a.targetId,hwId:a.hwId,bodyHash:a.bodyHash};
function edge(from,to,size,kind='delta',blockSize=1024) { return {from:from.id,to:to.id,kind,codec:kind==='full'?0:1,
  payloadSize:size,blockSize,baseHash:from.bodyHash,targetImageHash:to.imageHash,packageHash:(from.bodyHash[0]+to.bodyHash[0]).repeat(32),verified:true,compatible:true}; }
const baseOpts=()=>({nodes:[a,b,c],current,goal:'c',relays:2});

test('proof geometry matches the promoted-odd tree',()=>{
  assert.deepEqual(Array.from({length:5},(_,i)=>siblingCount(5,i)),[3,3,3,3,1]);
  assert.throws(()=>siblingCount(0,0),/geometry/);
});
test('clean legacy and negotiated v2 counts include manifest, proofs and flight requests',()=>{
  const legacy=estimateEdge({payloadSize:1024,blockSize:1024},{wireProfile:'legacy',proactiveProof:false});
  assert.deepEqual(legacy.counts,{manifest:3,data:7,requests:1,proofRequests:1,proofs:1,overhead:0});
  assert.equal(legacy.logicalPackets,13); assert.equal(legacy.originMeshBytes,1355);
  const e1=estimateEdge({payloadSize:100*1024,blockSize:1024},caps,{relays:2,radio});
  const e2=estimateEdge({payloadSize:100*1024,blockSize:2048},caps,{relays:2,radio});
  assert.equal(e1.counts.data,600); assert.equal(e1.counts.requests,51); assert.equal(e1.rfTransmissions,(751+3)*3);
  assert.equal(e2.counts.data,600); assert.equal(e2.counts.requests,26); assert.equal(e2.rfTransmissions,(676+3)*3);
  // Golden values from tools/lora_ota/rak3401_route_search.py.
  assert.equal(e2.originMeshBytes,113682);
  const tail=estimateEdge({payloadSize:12345,blockSize:1024},caps);
  assert.equal(tail.logicalPackets,96); assert.equal(tail.originMeshBytes,14095);
  assert.ok(e1.rfAirtimeSeconds>e2.rfAirtimeSeconds);
});
test('compressed blocks use each block boundary and direct framing stays zero-hop',()=>{
  const e=estimateEdge({payloadSize:2048,blockSize:1024,encodedBlockSizes:[170,172]},
    {...caps,deflate:true},{relays:1});
  assert.equal(e.counts.data,3); assert.equal(e.counts.proofs,2);
  assert.throws(()=>estimateEdge({payloadSize:1024,blockSize:1024}, {...caps,wireProfile:'direct'},{relays:1}),/cannot be relayed/);
  assert.throws(()=>estimateEdge({payloadSize:1024,blockSize:1024,encodedBlockSizes:[10]},caps),/DEFLATE/);
});
test('packet and airtime objectives may select different numbers of installs',()=>{
  const edges=[edge(a,c,10000),edge(a,b,1000),edge(b,c,1000)];
  const few=planRoute({...baseOpts(),edges,objective:'packets'});
  const fast=planRoute({...baseOpts(),edges,objective:'airtime',radio,restartSeconds:1000});
  assert.deepEqual(few.steps.map(s=>s.to),['b','c']); assert.equal(few.totals.installs,2);
  assert.deepEqual(fast.steps.map(s=>s.to),['c']); assert.equal(fast.totals.installs,1);
  assert.equal(few.exact,false); assert.ok(few.assumptions.some(s=>s.includes('eight-byte body hash')));
  assert.ok(fast.assumptions.some(s=>s.includes('Restart duration')));
  assert.throws(()=>planRoute({...baseOpts(),edges,objective:'airtime',radio}),/explicit per-install/);
});
test('full edges need no base while deltas require the exact starting body',()=>{
  const good=edge(a,c,5000,'full'); delete good.baseHash;
  assert.equal(planRoute({...baseOpts(),edges:[good]}).steps[0].kind,'full');
  const wrong={...edge(a,c,5000),baseHash:'0'.repeat(16)};
  assert.throws(()=>planRoute({...baseOpts(),edges:[wrong]}),/No compatible/);
  assert.throws(()=>planRoute({...baseOpts(),current:{...current,bodyHash:b.bodyHash},edges:[good]}),/does not match/);
  assert.throws(()=>planRoute({...baseOpts(),current:{...current,imageHash:b.imageHash},edges:[good]}),/does not match/);
});
test('unknown or explicitly incompatible capabilities are not silently trusted',()=>{
  const unknown={...a,capabilities:{}};
  const opts={...baseOpts(),nodes:[unknown,b,c],edges:[edge(a,c,5000)]};
  assert.throws(()=>planRoute(opts),/No compatible/);
  const plan=planRoute({...opts,allowUnverified:true});
  assert.ok(plan.assumptions.some(s=>s.includes('capabilities are unknown')));
  const noCodec={...a,capabilities:{...caps,codecs:[0]}};
  assert.throws(()=>planRoute({...opts,nodes:[noCodec,b,c]}),/No compatible/);
  assert.throws(()=>planRoute({...baseOpts(),edges:[{...edge(a,c,5000),compatible:false}],allowUnverified:true}),/No compatible/);
});
test('invalid image hashes, role changes and non-upgrade edges are rejected',()=>{
  assert.throws(()=>planRoute({...baseOpts(),nodes:[{...a,imageHash:'123'},b,c],edges:[]}),/validated target/);
  const other={...c,hwId:'other'};
  assert.throws(()=>planRoute({...baseOpts(),nodes:[a,b,other],edges:[edge(a,other,1000)]}),/No compatible/);
  assert.throws(()=>planRoute({...baseOpts(),edges:[{...edge(a,c,1000),targetImageHash:b.imageHash}]}),/No compatible/);
  assert.throws(()=>planRoute({...baseOpts(),current:{...current,node:'c',version:3,bodyHash:c.bodyHash},goal:'a',edges:[edge(c,a,1000)]}),/No compatible/);
});
test('airtime uses explicit-header LoRa symbol timing',()=>{
  assert.ok(Math.abs(airtime(186,radio) - 0.643584)<1e-9);
});
