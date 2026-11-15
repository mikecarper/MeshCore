'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {channel,restoreSource,confirmSource,captureSourceProfile,restoreSourceProfile}=require('../docs/_javascript/lora_ota_recovery.js');
const {DirectSession}=require('../docs/_javascript/lora_ota_session.js');
const SOURCE='aa'.repeat(32),TARGET='bb'.repeat(32);
const record=()=>({phase:'pull attempted',source:SOURCE,target:TARGET,id:'01234567',
  packageHash:'cc'.repeat(32),bodyHash:'dd'.repeat(8),radio:{tuple:[908.5,62.5,7,5],preamble:128,sourceProfile:0,targetProfile:1,
    verifiedAt:Date.now(),expiresAt:Date.now()+120000}});
function source({repeater=false,active=false,secondary=false}={}){
  const calls=[],saved=[909.95,250,5,5];let current=active?[908.5,62.5,7,5]:null,second=null,preamble=128;
  const link={sourceKey:SOURCE,knownRepeater:TARGET,browserRawSource:!repeater,localCliSupported:true,
    otaSupported:false,usesUsbFolderFallback:!repeater,otaRadioProfile:0,otaTargetProfile:0,serving:false,failure:null,
    investigationAuthenticated:false,investigationVerified:false,investigationPermission:'admin',
    async closeRadioSession(){calls.push(['close']);},
    async selectOtaRadio(profile){calls.push(['select',profile]);link.otaRadioProfile=profile;},
    async probeOtaRadio(){calls.push(['probe']);link.supportsOtaRadio2=secondary;return secondary;},
    async localCommand(command){
      calls.push(['local',command]);
      if(command==='get radio')return saved.join(',');
      if(command==='ota stats')return 'fetch idle 0/0';
      if(command==='ota folder')return 'folder=off serving=0:';
      if(command==='get tempradio')return current?'TempRadio active: '+current.join(',')+' 120s left,preamble='+preamble:'TempRadio inactive';
      if(command==='get radio2'||command==='get radio2.cross'||command==='get radioat2'||command==='get tempradioat2')
        return command.includes('at2')?'none':'off';
      if(command==='get tempradio2')return second?second.join(',')+',rxtx,'+preamble:'off';
      if(command==='get radio2.status')return 'rxtx; RX=0,0 TX=0,0 switches=1 errors=0 max=200us preamble=32,'+preamble;
      if(command.startsWith('tempradio ')||command.startsWith('set tempradio2 '))return setTemporary(command);
      throw Error('Unexpected source command '+command);
    },
    async applyTemporaryRadio(command){calls.push(['temporary',command]);return setTemporary(command);},
    async remoteCommand(){throw Error('Recovery may never contact or mutate the target');},
  };
  function setTemporary(command){
    const values=command.replace(/^(?:set tempradio2|tempradio) /,'').split(',');
    const tuple=values.slice(0,4).map(Number);
    preamble=Number(values.at(-1));
    if(command.startsWith('set '))second=tuple;else current=tuple;
    return 'OK - temporary radio pending';
  }
  return {link,calls,saved,set current(value){current=value;},get current(){return current;}};
}

function passiveSource(){
  let clock=100000;
  const calls=[],port={},tuple=[908.5,62.5,7,5],values=new Map([
    ['ota stats','serve idle | fetch idle'],['ota folder','folder=off serving=0:'],
    ['get tempradio','TempRadio inactive'],['get tempradio2','908.500,62.500,7,5,rxtx,128; 0d2h0m (120 min) left'],
    ['get radio2.status','rxtx; RX=0,0 TX=0,0 switches=1 errors=0 max=200us preamble=32,128'],
    ['get radio2.cross','off']]);
  const previous={port,sourceKey:SOURCE,knownRepeater:TARGET,browserRawSource:true,otaSupported:true,localCliSupported:true,
    otaRadioProfile:1,otaTargetProfile:1,failure:Error('USB response timed out'),
    otaLeaseProof:{source:SOURCE,target:TARGET,sourceProfile:1,targetProfile:1,tuple:tuple.slice(),preamble:128},
    sourceTempRadio:{state:'active',profile:1,tuple:tuple.slice(),preamble:128}};
  const link={port,sourceKey:SOURCE,knownRepeater:'',browserRawSource:true,otaSupported:true,localCliSupported:true,
    running:true,portOpened:true,guard:false,authenticated:false,serving:false,failure:null,otaRadioProfile:0,otaTargetProfile:0,
    async probeOtaRadio(){calls.push(['probe']);link.supportsOtaRadio2=true;return true;},
    async localCommand(command){
      calls.push(['local',command]);assert(values.has(command),'Unexpected passive read '+command);
      const value=values.get(command);if(value instanceof Error)throw value;return typeof value==='function'?value():value;
    },
    async selectOtaRadio(profile){calls.push(['select',profile]);link.otaRadioProfile=profile;},
    async applyTemporaryRadio(){assert.fail('Passive profile recovery must not retune');},
    async remoteCommand(){assert.fail('Passive profile recovery must not send target commands');},
    async verifyPeer(){assert.fail('Target proof belongs to the caller after local profile selection');}};
  return {previous,link,calls,values,now:()=>clock,set clock(value){clock=value;},get clock(){return clock;}};
}

test('same-page passive recovery selects an exact surviving radio2 after USB reset without retuning',async()=>{
  for(const targetProfile of [0,1]){
    const f=passiveSource();f.previous.otaTargetProfile=f.previous.otaLeaseProof.targetProfile=targetProfile;
    const token=captureSourceProfile(f.previous,TARGET,{now:f.now});assert(token);
    assert.equal(f.previous.failure.message,'USB response timed out');
    const result=await restoreSourceProfile(f.link,TARGET,token,{now:f.now});
    assert.equal(result.state,'selected');assert.equal(result.requiresVerification,true);
    assert.equal(result.source,SOURCE);assert.equal(result.target,TARGET);
    assert.deepEqual(result.tuple,[908.5,62.5,7,5]);assert.equal(result.preamble,128);
    assert.equal(f.link.otaRadioProfile,1);assert.equal(f.link.otaTargetProfile,targetProfile);
    assert.deepEqual(f.calls.filter(c=>c[0]==='select'),[['select',1]]);
    assert(f.calls.every(c=>c[0]==='probe'||c[0]==='select'||c[0]==='local'&&/^(?:get |ota stats$|ota folder$)/.test(c[1])));
    assert.equal(f.calls.at(-1)[0],'select');
  }
});

test('passive capture uses active source readback only when no contradictory lease proof exists',()=>{
  const f=passiveSource();delete f.previous.otaLeaseProof;
  const token=captureSourceProfile(f.previous,TARGET,{now:f.now});assert(token);
  assert(Object.isFrozen(token));assert(Object.isFrozen(token.tuple));
  assert.throws(()=>{token.tuple[0]=433;},TypeError);
  assert.deepEqual(f.previous.sourceTempRadio.tuple,[908.5,62.5,7,5]);
  for(const mutate of [p=>{p.sourceTempRadio.state='pending';},p=>{p.sourceTempRadio.profile=0;},
    p=>{p.sourceTempRadio.preamble=0;},p=>{p.otaRadioProfile=0;},p=>{p.otaTargetProfile=2;},
    p=>{p.knownRepeater=42;},p=>{p.port=null;},p=>{p.otaSupported=false;},p=>{p.relays=[TARGET];}]){
    const g=passiveSource();delete g.previous.otaLeaseProof;mutate(g.previous);
    assert.equal(captureSourceProfile(g.previous,TARGET,{now:g.now}),null);
  }
  for(const mutate of [p=>{p.target='cc'.repeat(32);},p=>{p.source='cc'.repeat(32);},p=>{p.sourceProfile=0;},
    p=>{p.targetProfile=0;},p=>{p.tuple=[908.5,62.5,13,5];},p=>{p.preamble=Infinity;}]){
    const g=passiveSource();mutate(g.previous.otaLeaseProof);
    assert.equal(captureSourceProfile(g.previous,TARGET,{now:g.now}),null);
  }
});

test('fresh local passive readback permits another same-page reconnect without inventing target proof',async()=>{
  const f=passiveSource(),token=captureSourceProfile(f.previous,TARGET,{now:f.now});
  const result=await restoreSourceProfile(f.link,TARGET,token,{now:f.now});
  assert.equal(result.requiresVerification,true);assert.equal(f.link.otaLeaseProof,undefined);
  assert.deepEqual(f.link.sourceTempRadio.tuple,[908.5,62.5,7,5]);
  assert.deepEqual(f.link.sourceTempRadio.radio,[908.5,62.5,7,5]);
  assert.notEqual(f.link.sourceTempRadio.tuple,f.link.sourceTempRadio.radio);
  assert.equal(f.link.sourceTempRadio.state,'active');assert.equal(f.link.sourceTempRadio.profile,1);
  assert.equal(captureSourceProfile(f.link,TARGET,{now:f.now}),null,'The caller must first select the exact target');
  f.link.knownRepeater=TARGET;f.link.failure=Error('Second USB timeout');
  const next=captureSourceProfile(f.link,TARGET,{now:f.now});assert(next);
  assert.deepEqual(next.tuple,[908.5,62.5,7,5]);assert.equal(next.targetProfile,1);
  f.link.sourceTempRadio.tuple[0]=433;
  assert.deepEqual(next.tuple,[908.5,62.5,7,5]);
  assert.equal(f.link.otaLeaseProof,undefined);
});

test('foreign, copied, expired or changed passive profile ownership produces no source I/O',async()=>{
  for(const kind of ['absent','copied','expired','clock-back','port','source','target','selected-target','failure','closed','serving']){
    const f=passiveSource();let token=captureSourceProfile(f.previous,TARGET,{now:f.now}),target=TARGET;
    if(kind==='absent')token=null;
    if(kind==='copied')token={...token};
    if(kind==='expired')f.clock=token.expiresAt;
    if(kind==='clock-back')f.clock=token.capturedAt-1;
    if(kind==='port')f.link.port={};
    if(kind==='source')f.link.sourceKey='cc'.repeat(32);
    if(kind==='target')target='cc'.repeat(32);
    if(kind==='selected-target')f.link.knownRepeater='cc'.repeat(32);
    if(kind==='failure')f.link.failure=Error('USB stopped');
    if(kind==='closed')f.link.running=false;
    if(kind==='serving')f.link.serving=true;
    assert.equal((await restoreSourceProfile(f.link,target,token,{now:f.now})).state,'unavailable',kind);
    assert.deepEqual(f.calls,[],kind);
  }
});

test('busy guard, pending or different live profiles never authorize passive selection',async()=>{
  for(const [command,reply] of [
    ['guard',true],['authenticated',true],['ota stats','fetch active 1/2'],['ota folder','folder=on'],
    ['get tempradio','TempRadio active: 910.525,62.5,7,5 120s left,preamble=32'],['get tempradio2','off'],
    ['get tempradio2','908.600,62.500,7,5,rxtx,128'],['get tempradio2','908.500,62.500,7,5,rxtx,64'],
    ['get radio2.status','radio2 change pending reply transmission; previous channel retained'],
    ['get radio2.status','off; RX=0,0 TX=0,0 switches=1 errors=0 max=200us preamble=32,128'],
    ['get radio2.cross','on'],['get radio2.cross','auto']]){
    const f=passiveSource(),token=captureSourceProfile(f.previous,TARGET,{now:f.now});
    if(command==='guard'||command==='authenticated')f.link[command]=reply;else f.values.set(command,reply);
    assert.equal((await restoreSourceProfile(f.link,TARGET,token,{now:f.now})).state,'unconfirmed',command+':'+reply);
    assert(!f.calls.some(c=>c[0]==='select'));
  }
});

test('passive profile recovery preserves exact USB failures and rejects mid-read identity or deadline changes',async()=>{
  const f=passiveSource(),token=captureSourceProfile(f.previous,TARGET,{now:f.now}),failure=Error('USB response timed out');
  f.values.set('get tempradio2',failure);
  await assert.rejects(restoreSourceProfile(f.link,TARGET,token,{now:f.now}),error=>error===failure);
  assert(!f.calls.some(c=>c[0]==='select'));
  for(const kind of ['identity','deadline']){
    const g=passiveSource(),snapshot=captureSourceProfile(g.previous,TARGET,{now:g.now});
    g.values.set('get radio2.cross',()=>{if(kind==='identity')g.link.sourceKey='cc'.repeat(32);else g.clock=snapshot.expiresAt;return 'off';});
    await assert.rejects(restoreSourceProfile(g.link,TARGET,snapshot,{now:g.now}),/changed|expired/);
    assert(!g.calls.some(c=>c[0]==='select'));
  }
});
test('journal recovery requires exact devices, package identity and a fully validated verified channel',()=>{
  const valid=record();assert.deepEqual(channel(valid,SOURCE,TARGET).tuple,[908.5,62.5,7,5]);
  for(const change of [r=>{r.source='11'.repeat(32);},r=>{r.target='22'.repeat(32);},r=>{r.phase='installed';},
    r=>{r.phase='prepared';},r=>{r.id='1234';},r=>{r.id=12345678;},r=>{r.packageHash='bad';},r=>{r.bodyHash='bad';},r=>{r.bodyHash=1234567812345678;},
    r=>{delete r.radio;},r=>{r.radio.preamble=0;},r=>{r.radio.preamble=Infinity;},
    r=>{r.radio.tuple=[908.5,62.5,13,5];},r=>{r.radio.tuple=[908.5,123,7,5];},
    r=>{r.radio.tuple=['908.5',62.5,7,5];},r=>{r.radio.sourceProfile=2;},r=>{r.radio.targetProfile=true;},
    r=>{r.radio.expiresAt=Date.now()-1;},r=>{r.radio.expiresAt=null;},r=>{delete r.radio.verifiedAt;},
    r=>{r.radio.verifiedAt=Date.now()+60000;},r=>{r.radio.expiresAt=Date.now()+15*86400000;}]){
    const invalid=record();change(invalid);assert.equal(channel(invalid,SOURCE,TARGET),null);
  }
  assert.equal(channel(valid,SOURCE,SOURCE),null);
  const isolated=channel(valid,SOURCE,TARGET);isolated.tuple[0]=433;
  assert.equal(valid.radio.tuple[0],908.5);
});
test('an unrelated or old-format journal produces no source I/O',async()=>{
  const f=source();const old=record();delete old.radio;
  assert.equal((await restoreSource(f.link,TARGET,old)).state,'unavailable');
  assert.deepEqual(f.calls,[]);
  assert.equal((await restoreSource(f.link,TARGET,record(),{override:true})).state,'unavailable');
  assert.deepEqual(f.calls,[]);
});
test('raw-only older Companions retain the existing channel without unsupported local OTA probes',async()=>{
  for(const capacity of [176,188])for(const guarded of [false,undefined]){
    const f=source({active:true});f.link.usesUsbFolderFallback=false;f.link.usbRawFrameMax=capacity;f.link.otaSupported=guarded;
    const recovered=await restoreSource(f.link,TARGET,record());
    assert.equal(recovered.state,'unavailable');assert.deepEqual(f.calls,[]);
    assert.deepEqual(f.current,[908.5,62.5,7,5]);assert.deepEqual(f.saved,[909.95,250,5,5]);
    assert.equal(f.link.otaRadioProfile,0);assert.equal(f.link.otaTargetProfile,0);
  }
});
test('guarded and proven native sources still require a readable idle OTA owner',async()=>{
  for(const guarded of [false,true])for(const unsupported of ['ota stats','ota folder']){
    const f=source();f.link.otaSupported=guarded;f.link.usesUsbFolderFallback=!guarded;
    const local=f.link.localCommand;
    f.link.localCommand=async command=>command===unsupported?'Unknown command':local(command);
    await assert.rejects(restoreSource(f.link,TARGET,record()),/active or unverified|attached or unverified/i);
    assert.equal(f.calls.some(c=>c[0]==='temporary'),false);
  }
});
test('only a short source lease is restored before target identity and administration are proven',async()=>{
  const f=source();const recovered=await restoreSource(f.link,TARGET,record());
  assert.equal(recovered.state,'restored');assert.equal(recovered.requiresVerification,true);
  assert.deepEqual(f.calls.filter(c=>c[0]==='temporary'),[['temporary','tempradio 908.500,62.5,7,5,2,128']]);
  assert.deepEqual(f.saved,[909.95,250,5,5]);assert.equal(f.link.otaTargetProfile,1);
  await assert.rejects(confirmSource(f.link,recovered,{verifiedKey:TARGET}),/administration/i);
  f.link.investigationAuthenticated=f.link.investigationVerified=true;
  await assert.rejects(confirmSource(f.link,recovered,{verifiedKey:'ee'.repeat(32)}),/complete public key/i);
  assert.equal(f.calls.filter(c=>c[0]==='temporary').length,1);
  const confirmed=await confirmSource(f.link,recovered,{verifiedKey:TARGET});
  assert.equal(confirmed.minutes,120);
  assert.equal(f.calls.filter(c=>c[0]==='temporary').at(-1)[1],'tempradio 908.500,62.5,7,5,120,128');
  assert.deepEqual(f.saved,[909.95,250,5,5]);
  await assert.rejects(confirmSource(f.link,recovered,{verifiedKey:TARGET}),/complete public key/);
});
test('an already matching active channel is reused before verification without a write',async()=>{
  const f=source({active:true});const recovered=await restoreSource(f.link,TARGET,record());
  assert.equal(recovered.state,'unchanged');assert.ok(!f.calls.some(c=>c[0]==='temporary'));
});
test('USB repeaters receive only source-local temporary commands',async()=>{
  const f=source({repeater:true});const recovered=await restoreSource(f.link,TARGET,record());
  assert.equal(recovered.state,'restored');assert.ok(!f.calls.some(c=>c[0]==='temporary'));
  assert.ok(f.calls.some(c=>c[1]==='tempradio 908.500,62.5,7,5,2,128'));
  f.link.authenticatedKey=TARGET;
  await confirmSource(f.link,recovered,{verifiedKey:TARGET});
  assert.ok(f.calls.some(c=>c[1]==='tempradio 908.500,62.5,7,5,120,128'));
});
test('source identity, busy folders and active downloads fail before a retune',async()=>{
  for(const kind of ['key','folder','fetch']){
    const f=source();const local=f.link.localCommand;
    if(kind==='key')f.link.knownRepeater='ee'.repeat(32);
    else f.link.localCommand=async command=>command===(kind==='folder'?'ota folder':'ota stats')
      ?kind==='folder'?'folder=on serving=1:':'fetch active 1/10':local(command);
    await assert.rejects(restoreSource(f.link,TARGET,record()),/same idle|attached|active/i);
    assert.ok(!f.calls.some(c=>c[0]==='temporary'));
  }
});
test('source or active channel changes invalidate the recovery extension token',async()=>{
  for(const kind of ['identity','channel','saved']){
    const f=source();const restored=await restoreSource(f.link,TARGET,record());
    f.link.investigationAuthenticated=f.link.investigationVerified=true;
    if(kind==='identity')f.link.sourceKey='ee'.repeat(32);
    if(kind==='channel')f.current=[909.5,62.5,7,5];
    if(kind==='saved')f.saved[0]=910.525;
    await assert.rejects(confirmSource(f.link,restored,{verifiedKey:TARGET}),/identity|channel changed/i);
    assert.equal(f.calls.filter(c=>c[0]==='temporary').length,1);
  }
});
test('expired or altered channel tokens and known non-admin roles cannot extend a lease',async()=>{
  for(const kind of ['expired','tuple','profile','preamble','guest','read-only','read-write','region manager','filter manager']){
    const f=source();const restored=await restoreSource(f.link,TARGET,record());
    f.link.investigationAuthenticated=f.link.investigationVerified=true;
    const options={verifiedKey:TARGET};
    if(kind==='expired')options.now=()=>restored.expiresAt+1;
    else if(kind==='tuple')restored.tuple[0]=433;
    else if(kind==='profile')restored.sourceProfile=1;
    else if(kind==='preamble')restored.preamble=64;
    else f.link.investigationPermission=kind;
    await assert.rejects(confirmSource(f.link,restored,options),/expired or changed|administration/i);
    assert.equal(f.calls.filter(c=>c[0]==='temporary').length,1);
  }
});
test('an unknown legacy ACL role also needs independently verified OTA administration',async()=>{
  const f=source();const recovered=await restoreSource(f.link,TARGET,record());
  f.link.investigationAuthenticated=f.link.investigationVerified=true;f.link.investigationPermission='unknown';
  await assert.rejects(confirmSource(f.link,recovered,{verifiedKey:TARGET}),/administration/i);
  assert.equal(f.calls.filter(c=>c[0]==='temporary').length,1);
  await confirmSource(f.link,recovered,{verifiedKey:TARGET,adminVerified:true});
  assert.equal(f.calls.filter(c=>c[0]==='temporary').length,2);
});
test('native queue preservation precedes recovery folder reads without target traffic',async()=>{
  const f=source();let reserved=false;
  f.link.usesUsbFolderFallback=true;f.link.usbFolderProbePending=true;
  f.link.ensureUsbFolderReady=async()=>{f.calls.push(['preserve']);reserved=true;f.link.usbFolderProbePending=false;};
  const local=f.link.localCommand;
  f.link.localCommand=async command=>{
    if(command==='ota stats'||command==='ota folder')assert.equal(reserved,true);
    return local(command);
  };
  const recovered=await restoreSource(f.link,TARGET,record());
  assert.equal(recovered.state,'restored');
  assert.equal(f.calls[0][0],'preserve');
  assert.equal(f.calls.filter(c=>c[0]==='temporary').length,1);
});
test('a free secondary profile can be restored while busy or unknown slots remain untouched',async()=>{
  const r=record();r.radio.sourceProfile=1;
  const f=source({secondary:true});const result=await restoreSource(f.link,TARGET,r);
  assert.equal(result.state,'restored');assert.ok(f.calls.some(c=>c[1]==='set tempradio2 908.500,62.5,7,5,rxtx,2,128'));
  for(const command of ['get radio2','get radio2.cross','get radioat2','get tempradioat2']){
    const busy=source({secondary:true}),read=busy.link.localCommand;
    busy.link.localCommand=async text=>text===command?'Unknown command':read(text);
    await assert.rejects(restoreSource(busy.link,TARGET,r),/secondary|crossing|scheduled|Unknown/i);
    assert.ok(!busy.calls.some(c=>c[0]==='temporary'));
  }
});
test('a missing acknowledgement never causes a repeated source retune',async()=>{
  const f=source();let writes=0;
  f.link.applyTemporaryRadio=async()=>{writes++;throw Error('USB reply timed out');};
  await assert.rejects(restoreSource(f.link,TARGET,record()),/USB reply timed out/);
  assert.equal(writes,1);
});
test('Session journals include only fresh active-channel evidence and clone its tuple',async()=>{
  const saved=[];
  const link={browserRawSource:true,otaSupported:false,usesUsbFolderFallback:true,localCliSupported:true,
    otaRadioProfile:0,otaTargetProfile:1,
    async localCommand(command){
      if(command==='get tempradio')return 'TempRadio active: 908.500,62.5,7,5 120s left,preamble=128';
      if(command==='get radio')return '909.950,250,5,5';
      if(command==='ota config')return 'autofetch=off autoinstall=off hops=0';throw Error(command);
    },async remoteCommand(_key,_password,command){
      if(command==='get tempradio')return 'off';
      if(command==='get tempradio2')return '908.500,62.5,7,5,rxtx,128';
      if(command==='get radio2.status')return 'rxtx; RX=0,0 TX=0,0 switches=1 errors=0 max=200us preamble=32,128';
      if(command==='ota config')return 'autofetch=off autoinstall=off hops=0';throw Error(command);
    }};
  const session=new DirectSession(link,TARGET,'',null,{journal:r=>saved.push(r)});
  session.recordJournal({phase:'prepared'});assert.equal(saved[0].radio,undefined);
  await session.inspectRadios();session.recordJournal({phase:'pull attempted'});
  assert.deepEqual(saved[1].radio.tuple,[908.5,62.5,7,5]);
  assert.equal(saved[1].radio.preamble,128);assert.equal(saved[1].radio.sourceProfile,0);assert.equal(saved[1].radio.targetProfile,1);
  assert.ok(saved[1].radio.verifiedAt<=Date.now());
  assert.ok(saved[1].radio.expiresAt>saved[1].radio.verifiedAt);
  assert.ok(saved[1].radio.expiresAt<=saved[1].radio.verifiedAt+119000);
  saved[1].radio.tuple[0]=433;session.recordJournal({phase:'ready'});assert.equal(saved[2].radio.tuple[0],908.5);
});
