const {test}=require('node:test');
const assert=require('node:assert/strict');
const {choices,label}=require('../docs/_javascript/lora_ota_contacts.js');
const c=(id,type,hops,favorite=false,name=id)=>({publicKey:id.repeat(64),type,hops,favorite,name});
test('favorites first, then repeater, room and sensor by hops; omit companions',()=>{
  const list=[c('a',4,0),c('b',2,3),c('c',3,0),c('d',2,0),c('e',1,0,true),c('f',3,3,true),c('a',2,null)];
  assert.deepEqual(choices(list).map(x=>x.publicKey[0]),['f','d','b','a','c','a']);
  assert.match(label(c('d',2,0,true,'Hill')),/^★ Hill — Repeater, saved direct path/);
});
test('labels distinguish absent, direct, and multi-hop saved paths',()=>{
  assert.match(label(c('a',2,null)),/Repeater, no saved path/);
  assert.match(label(c('b',2,1)),/Repeater, saved path: 1 hop \(/);
  assert.match(label(c('c',2,3)),/Repeater, saved path: 3 hops \(/);
});
test('incoming advert paths are labeled separately from saved routes',()=>{
  const contact=c('a',2,null);
  contact.advertPath={hops:2,path:['AABB','CCDD']};
  assert.match(label(contact),/no saved path, incoming advert: AABB → CCDD \(2 hops\)/);
  contact.advertPath={hops:0,path:[]};
  assert.match(label(contact),/incoming advert: direct \(0 hops\)/);
});
test('search matches names or public-key prefixes without showing unrelated entries',()=>{
  const list=[c('a',2,0,false,'North Hill'),c('b',3,3,false,'South')];
  assert.deepEqual(choices(list,'hill').map(x=>x.name),['North Hill']);
  assert.deepEqual(choices(list,'BBBB').map(x=>x.name),['South']);
  assert.deepEqual(choices(list,'cc'),[]);
});
