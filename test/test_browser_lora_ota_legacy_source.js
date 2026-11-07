const assert=require('node:assert/strict');
if(!globalThis.crypto)globalThis.crypto=require('node:crypto').webcrypto;
const {CompanionTransport}=require('../docs/_javascript/lora_ota_companion.js');
const enc=new TextEncoder(),dec=new TextDecoder(),key='bb'.repeat(32),source='aa'.repeat(32);
const bytes=s=>Uint8Array.from(s.match(/../g),x=>parseInt(x,16));
const wire=f=>Uint8Array.of(62,f.length&255,f.length>>8,...f);
class OldPort{
  constructor(options={}){this.q=[];this.waiter=null;this.sent=[];this.options=options;}
  push(f){if(this.waiter){const w=this.waiter;this.waiter=null;w({value:wire(f),done:false});}else this.q.push(wire(f));}
  async open(){
    this.readable={getReader:()=>({read:()=>this.q.length?Promise.resolve({value:this.q.shift(),done:false}):new Promise(r=>this.waiter=r),cancel:async()=>{if(this.waiter)this.waiter({done:true});},releaseLock(){}})};
    this.writable={getWriter:()=>({write:async b=>{const f=b.slice(3);this.sent.push(f);if(f[0]===1){const x=new Uint8Array(58);x[0]=5;x.set(bytes(source),4);const v=new DataView(x.buffer);v.setUint32(48,910525,true);v.setUint32(52,62500,true);x[56]=7;x[57]=5;this.push(x);}else if(f[0]===22){assert.equal(f[1],4);if(this.options.noDevice)this.push(Uint8Array.of(1,2));else{const x=new Uint8Array(82);x[0]=13;x[1]=14;x.set(enc.encode('01-Nov-2026'),8);x.set(enc.encode('test manufacturer'),20);x.set(enc.encode('v1.16.05-halo-keymi'),60);this.push(x);}}else if(f[0]===66){if(!this.options.cli)this.push(Uint8Array.of(0,1));else{const cmd=dec.decode(f.slice(1));const reply=cmd==='get ota.direct'?(this.options.noGuard?'Unknown command':'> ota.direct v=2 active=0 busy=0'):cmd==='get ota.direct.capabilities'?'> ota.direct.cap guarded_cli=1 guarded_radio=1':cmd==='ver'?'v1.18.0.2-dev-abcdef12 (Build: 01-Nov-2026)':'Unknown command';this.push(Uint8Array.of(29,...enc.encode(reply)));}}else throw Error('unexpected command');},close:async()=>{},releaseLock(){}})};
  }
  async close(){}
}
async function run(){
  const old=new OldPort(),link=new CompanionTransport(old,{openSettle:0,commandTimeout:200});
  await link.open();assert.equal(link.localCliSupported,false);assert.equal(link.otaSupported,false);
  assert.equal(link.firmwareVersion,'v1.16.05-halo-keymi');assert.equal(link.firmwareVersionTruncated,true);assert.equal(link.firmwareBuildDate,'01-Nov-2026');assert.equal(link.firmwareProtocol,14);assert.equal(link.otaMode,'legacy');assert.equal(link.rawPacketSupport,'unverified');
  assert.deepEqual(await link.savedSourceRadio(),[910.525,62.5,7,5]);
  await assert.rejects(()=>link.localCommand('get tempradio'),/cannot report/);
  assert.equal(old.sent.filter(f=>f[0]===66).length,1);await link.close();
  const modernPort=new OldPort({cli:true}),modern=new CompanionTransport(modernPort,{openSettle:0,commandTimeout:200});
  await modern.open();assert.equal(modern.otaMode,'guarded');assert.equal(modern.firmwareVersion,'v1.18.0.2-dev-abcdef12');assert.equal(modern.firmwareVersionTruncated,false);await modern.close();
  const unguardedPort=new OldPort({cli:true,noGuard:true}),unguarded=new CompanionTransport(unguardedPort,{openSettle:0,commandTimeout:200});
  await unguarded.open();assert.equal(unguarded.firmwareVersion,'v1.18.0.2-dev-abcdef12');assert.equal(unguarded.otaMode,'legacy');await unguarded.close();
  const unknownPort=new OldPort({noDevice:true}),unknown=new CompanionTransport(unknownPort,{openSettle:0,commandTimeout:200});
  await unknown.open();assert.equal(unknown.firmwareVersion,null);assert.equal(unknown.otaMode,'legacy');await unknown.close();

  // An old source serves only after the target has proved its full identity.
  const t=new CompanionTransport({}, {knownRepeater:key,investigationTimeout:30});
  t.investigationEnabled=true;t.investigationAuthenticated=true;t.investigationVerified=true;t.investigationPassword='';t.contactList=[{publicKey:key}];t.sourceKey=source;
  t.contacts=async()=>t.contactList;t.reserveInbox=()=>1;t.finishInbox=()=>{};
  let sent=[],tag='';t.command=async f=>{sent.push(f);tag=dec.decode(f.slice(13)).slice(0,3);return Uint8Array.of(6);};
  t.commandAny=async()=>Uint8Array.of(16,0,0,0,...bytes(key).slice(0,6),255,1,0,0,0,0,...enc.encode(tag+'OK'));
  const previousNavigator=globalThis.navigator;
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:{locks:{request:async(_k,_o,fn)=>fn({})}}});
  assert.equal(await t.remoteCommand(key,'','ota pull 1234ABCD flash'),'OK');
  assert.equal(await t.remoteCommand(key,'','ota install'),'OK');
  assert.equal(await t.remoteCommand(key,'','ota installhash '+'a'.repeat(64)),'OK');
  await assert.rejects(()=>t.remoteCommand(key,'','set tx 9'),/approved OTA/);
  t.investigationVerified=false;await assert.rejects(()=>t.remoteCommand(key,'','ota install'),/full public key/);
  assert.equal(sent.length,3);
  t.investigationVerified=true;let attempts=0;
  t.standardReply=async()=>{attempts++;const error=Error('Remote command timed out; result is unknown');error.loraReplyTimeout=true;throw error;};
  await assert.rejects(()=>t.remoteCommand(key,'','ota install'),/timed out/);assert.equal(attempts,1);
  attempts=0;await assert.rejects(()=>t.remoteCommand(key,'','ota status'),e=>e.loraReplyTimeout===true&&e.attempts===11);assert.equal(attempts,11);
  Object.defineProperty(globalThis,'navigator',{configurable:true,value:previousNavigator});

  // Current and older temp-radio response formats both supply pacing data.
  const s=new CompanionTransport({},{});s.radio=[910.525,62.5,7,5];s.localCliSupported=true;s.otaSupported=false;
  let temp='> 910.525,62.5,7,5,32',primary='> 910.525,62.5,7,5';
  s.localCommand=async cmd=>cmd==='get tempradio'?temp:primary;
  s.advertise=()=>{};globalThis.LoraOtaCompanionSource={Source:{create:async()=>({})}};
  await s.serve({},new Uint8Array());assert.equal(s.preamble,32);
  temp='TempRadio active: 910.525,62.5,7,5 100s left,preamble=32';await s.checkSourceRadio();
  temp='> 911.150,62.5,7,5,32';await assert.rejects(()=>s.checkSourceRadio(),/changed/);await s.stopServing();
  console.log('Legacy Companion OTA source tests passed');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
