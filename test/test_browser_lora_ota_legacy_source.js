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

  // Protocol error codes must survive the framed response. A missing CMD65 is
  // actionable, while queue-full, busy and malformed packets are not proof of
  // obsolete source firmware.
  for(const code of [1,3,4,6]){
    const sourceLink=new CompanionTransport({},{});sourceLink.serving=true;sourceLink.otaSupported=true;
    sourceLink.command=async()=>{let seen;sourceLink.pending={codes:[0],timer:setTimeout(()=>{},1000),reject:e=>{seen=e;}};sourceLink.onFrame(Uint8Array.of(1,code));throw seen;};
    await assert.rejects(()=>sourceLink.sendRadioOrdered(Uint8Array.of(0x32,0,1)),e=>e.companionErrorCode===code && (e.sourceUpdateRequired==='companion')===(code===1));
    assert.equal(sourceLink.rawPacketSupport,code===1?'unsupported':'unverified');
  }

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

  // The firmware guard owns the active profile generation, so a guarded
  // source can use a temporary OTA channel without changing its saved mesh.
  // Mirror localCommand's real get-radio side effect on the cached tuple.
  const offPrimary=new CompanionTransport({},{});
  offPrimary.sourceKey=source;offPrimary.localCliSupported=true;offPrimary.otaSupported=true;
  let saved=[910.525,62.5,12,8],active='TempRadio active: 909.500,250.00,5,5 600s left,preamble=128';
  offPrimary.radio=saved.slice();offPrimary.advertise=()=>{};
  offPrimary.localCommand=async command=>{
    if(command==='get tempradio')return active;
    assert.equal(command,'get radio');offPrimary.radio=saved.slice();return '> '+saved.join(',');
  };
  await offPrimary.serve({},new Uint8Array());
  assert.deepEqual(offPrimary.sourceSavedRadio,[910.525,62.5,12,8]);
  assert.deepEqual(offPrimary.sourceTempRadio,{radio:[909.5,250,5,5],preamble:128});
  assert.deepEqual(offPrimary.radio,saved);
  await offPrimary.checkSourceRadio();
  saved=[911.15,62.5,12,8];await assert.rejects(()=>offPrimary.checkSourceRadio(),/changed/);
  saved=[910.525,62.5,12,8];
  active='TempRadio active: 909.600,250.00,5,5 600s left,preamble=128';
  await assert.rejects(()=>offPrimary.checkSourceRadio(),/changed/);
  active='TempRadio active: 909.500,250.00,5,5 600s left,preamble=32';
  await assert.rejects(()=>offPrimary.checkSourceRadio(),/changed/);
  active='TempRadio inactive';await assert.rejects(()=>offPrimary.checkSourceRadio(),/temporary|active/i);
  active='TempRadio active: 909.500,250.00,5,5 600s left,preamble=128';
  await offPrimary.checkSourceRadio();

  // Slow saved settings would impose a much longer delay. The actual fast
  // temporary profile puts this maximum-size OTA frame at the 250 ms floor.
  const raw=new Uint8Array(186);raw[0]=0x32;
  let rawSends=0;offPrimary.command=async(frame,code)=>{
    assert.equal(code,0);assert.deepEqual(frame,Uint8Array.of(65,1,...raw));++rawSends;return Uint8Array.of(0);
  };
  const realNow=Date.now;Date.now=()=>100000;
  try{await offPrimary.sendRadioOrdered(raw);assert.equal(offPrimary.nextTx,100250);}
  finally{Date.now=realNow;}
  assert.equal(rawSends,1);await offPrimary.stopServing();

  // Without firmware generation guards, retain the existing same-channel
  // restriction rather than treating the off-primary profile as protected.
  offPrimary.otaSupported=false;
  await assert.rejects(()=>offPrimary.serve({},new Uint8Array()),/changed|saved|primary|match/i);
  assert.equal(offPrimary.serving,false);

  // Setup must refuse to disrupt an active transfer. After an explicit stop,
  // closing the guard cannot reopen/login or send a second close.
  const closing=new CompanionTransport({},{});
  closing.serving=true;closing.guard=true;closing.authenticated=true;
  let stopped=0,verified=0,loggedIn=0;const modes=[];
  closing.stopServing=async()=>{++stopped;closing.serving=false;};
  closing.guardMode=async mode=>{modes.push(mode);assert.equal(mode,3);};
  closing.verifyPeer=async()=>{++verified;throw Error('must not authenticate');};
  closing.login=async()=>{++loggedIn;throw Error('must not login');};
  await assert.rejects(()=>closing.closeRadioSession(),/Stop the transfer/);
  assert.equal(stopped,0);assert.deepEqual(modes,[]);
  assert.equal(closing.serving,true);assert.equal(closing.guard,true);assert.equal(closing.authenticated,true);
  await closing.stopServing();
  await closing.closeRadioSession();await closing.closeRadioSession();
  assert.equal(stopped,1);assert.deepEqual(modes,[3]);
  assert.equal(closing.guard,false);assert.equal(closing.authenticated,false);
  assert.equal(verified,0);assert.equal(loggedIn,0);
  // Profile capability detection is read-only. Selection is explicit, idle
  // and confirmed by readback; unknown replies do not guess support.
  const selected=new CompanionTransport({},{});selected.otaSupported=true;
  let chosen='radio',unexpected=false;const choices=[];
  selected.localCommand=async command=>{
    choices.push(command);
    if(command==='get ota.direct.radio')return unexpected?'invalid':'> ota.direct.radio v=1 profile='+chosen;
    assert.equal(command,'set ota.direct.radio radio2');chosen='radio2';return 'OK - ota.direct.radio radio2';
  };
  assert.equal(await selected.probeOtaRadio(),true);
  assert.deepEqual(choices,['get ota.direct.radio']);
  selected.guard=true;await assert.rejects(()=>selected.selectOtaRadio(1),/Close the OTA session/);
  selected.guard=false;await selected.selectOtaRadio(1);assert.equal(selected.otaRadioProfile,1);
  unexpected=true;await assert.rejects(()=>selected.probeOtaRadio(),/Cannot confirm/);
  for(const reply of ['Unknown command','Error: unsupported']){
    const unsupported=new CompanionTransport({},{});unsupported.otaSupported=true;
    unsupported.localCommand=async()=>reply;
    assert.equal(await unsupported.probeOtaRadio(),false);
    await assert.rejects(()=>unsupported.selectOtaRadio(1),/Update the USB Companion/);
    await unsupported.selectOtaRadio(0);
  }

  // Local temporary setup protects the shared queue without sending RF,
  // authenticating, or retrying an uncertain write.
  for(const lost of [false,true]){
    const temporary=new CompanionTransport({},{});temporary.otaSupported=true;temporary.localCliSupported=true;
    const events=[];
    temporary.guardMode=async mode=>{events.push(['guard',mode]);};
    temporary.localCommand=async command=>{
      assert.equal(temporary.guard,true);events.push(['local',command]);
      if(lost)throw Error('Command timed out; result is unknown');return 'OK';
    };
    temporary.verifyPeer=async()=>{throw Error('must not authenticate');};
    const action=temporary.applyTemporaryRadio('set tempradio2 909.95,250,7,5,rxtx,120,128');
    if(lost)await assert.rejects(action,/result is unknown/);else assert.equal(await action,'OK');
    assert.deepEqual(events,[['guard',1],['local','set tempradio2 909.95,250,7,5,rxtx,120,128'],['guard',3]]);
    assert.equal(temporary.guard,false);assert.equal(temporary.authenticated,false);
    const count=events.length;
    for(const command of ['set radio 909.95,250,7,5','set tempradio2 909.95,250,7,5,rx,120,128','tempradio 909.95,250,7,5;reboot'])
      await assert.rejects(()=>temporary.applyTemporaryRadio(command),/Cannot prepare/);
    assert.equal(events.length,count);
  }

  // Older USB CLI sources can arm primary TempRadio without a nonexistent
  // guard command. They cannot select radio2 or change persistent channels.
  const legacyTemporary=new CompanionTransport({},{});
  legacyTemporary.localCliSupported=true;legacyTemporary.otaSupported=false;
  const legacyWrites=[];
  legacyTemporary.localCommand=async command=>{legacyWrites.push(command);return 'OK';};
  legacyTemporary.guardMode=async()=>{throw Error('Legacy firmware has no guard API');};
  assert.equal(await legacyTemporary.applyTemporaryRadio('tempradio 910.525,62.5,7,5,2,128'),'OK');
  for(const command of ['set tempradio2 909.950,250,7,5,rxtx,120,128','set radio 909.950,250,7,5'])
    await assert.rejects(()=>legacyTemporary.applyTemporaryRadio(command),/Cannot prepare/);
  assert.deepEqual(legacyWrites,['tempradio 910.525,62.5,7,5,2,128']);

  // Radio2 pacing and every subsequent send use the active selected lease;
  // primary off and a slower saved mesh are expected in this mode.
  const secondarySource=new CompanionTransport({},{});
  Object.assign(secondarySource,{sourceKey:source,localCliSupported:true,otaSupported:true,supportsOtaRadio2:true,otaRadioProfile:1,radio:[910.525,62.5,12,8]});
  let secondaryLive=true;secondarySource.advertise=()=>{};
  secondarySource.localCommand=async command=>{
    if(command==='get radio'){secondarySource.radio=[910.525,62.5,12,8];return '> 910.525,62.5,12,8';}
    if(command==='get tempradio')return 'TempRadio inactive';
    if(command==='get tempradio2')return '> 909.950,250.000,7,5,rxtx,128;0d2h0m (120min) left';
    if(command==='get radio2.status')return (secondaryLive?'rxtx':'off')+'; RX=0,0 TX=0,0 switches=1 errors=0 max=200us preamble=32,128';
    throw Error('unexpected '+command);
  };
  await secondarySource.serve({},new Uint8Array());
  assert.deepEqual(secondarySource.sourceTempRadio.radio,[909.95,250,7,5]);
  await secondarySource.checkSourceRadio();secondaryLive=false;
  await assert.rejects(()=>secondarySource.checkSourceRadio(),/changed|active|temporary/i);
  await secondarySource.stopServing();
  console.log('Legacy Companion OTA source tests passed');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
