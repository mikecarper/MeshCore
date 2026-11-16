const assert=require('node:assert/strict');
if(!globalThis.crypto)globalThis.crypto=require('node:crypto').webcrypto;
const {Decoder,CompanionTransport}=require('../docs/_javascript/lora_ota_companion.js');
const enc=new TextEncoder(),dec=new TextDecoder(),key='bb'.repeat(32),source='aa'.repeat(32);
const fromHex=s=>Uint8Array.from(s.match(/../g),x=>parseInt(x,16));
function wire(body){return Uint8Array.of(62,body.length&255,body.length>>8,...body)}
class Port{
  constructor(){this.waiter=null;this.q=[];this.frames=[];this.guard=false;this.guardOwned=false;this.guardExpiresAt=Infinity;this.expireBeforeArm=false;this.correlation='';this.reply='';this.remoteAttempts={};this.timeoutFirst={};this.pendingReply=false;this.clock=Math.floor(Date.now()/1000);this.legacy=false;this.noCli=false;this.loginPermissions=1;this.loginCode=0x85;this.suppressLogin=false;this.targetType=3;this.targetPath=255;this.rxps='Error: RX power saving is unsupported on this radio';}
  push(v){if(this.waiter){let f=this.waiter;this.waiter=null;f({value:v,done:false});}else this.q.push(v);}
  async open(){
    this.readable={getReader:()=>({read:()=>this.q.length?Promise.resolve({value:this.q.shift(),done:false}):new Promise(r=>this.waiter=r),cancel:async()=>{if(this.waiter)this.waiter({done:true});},releaseLock(){}})};
    this.writable={getWriter:()=>({write:async b=>this.write(b),close:async()=>{},releaseLock(){}})};
  }
  async write(b){
    assert.equal(b[0],60);assert.equal(b.length,b[1]+256*b[2]+3);const f=b.slice(3);this.frames.push(f);let out;
    if(this.guard&&Date.now()>=this.guardExpiresAt)this.guard=false;
    assert.ok(f.length<=(f[0]===65?188:176),'Command exceeds the real USB receive envelope');
    if(f[0]===1){out=new Uint8Array(58);out[0]=5;out.set(fromHex(source),4);const v=new DataView(out.buffer);v.setUint32(48,910525,true);v.setUint32(52,62500,true);out[56]=7;out[57]=5;}
    else if(f[0]===22){out=new Uint8Array(82);out[0]=13;out[1]=13;out.set(enc.encode('06-Nov-2026'),8);out.set(enc.encode('Test board'),20);out.set(enc.encode(this.noCli?'v1.16.05':'v1.18.0.2-preview'),60);}
    else if(f[0]===4){
      const contact=(pub,type,fav,path,name)=>{const b=new Uint8Array(148);b[0]=3;b.set(fromHex(pub),1);b[33]=type;b[34]=fav?1:0;b[35]=path;b.set(enc.encode(name),100);return b;};
      queueMicrotask(()=>{this.push(wire(Uint8Array.of(2,4,0,0,0)));this.push(wire(contact('11'.repeat(32),2,false,2,'Repeater')));this.push(wire(contact(key,this.targetType,true,this.targetPath,'Room')));this.push(wire(contact('33'.repeat(32),4,false,1,'Sensor')));this.push(wire(contact('44'.repeat(32),1,false,0,'Chat')));this.push(wire(Uint8Array.of(4,0,0,0,0)));});return;
    }
    else if(f[0]===5){out=Uint8Array.of(9,...new Uint8Array(new Uint32Array([this.clock]).buffer));}
    else if(f[0]===42){assert.equal(f.length,34);assert.equal(f[1],0);out=this.advertFrame||Uint8Array.of(1,2);}
    else if(f[0]===6){this.clock=new DataView(f.buffer,f.byteOffset,f.byteLength).getUint32(1,true);out=Uint8Array.of(0);}
    else if(f[0]===52){
      assert.equal(Buffer.from(f.slice(2,34)).toString('hex'),key);
      if(f[1]===4&&this.expireBeforeArm){this.expireBeforeArm=false;this.guard=false;}
      if((f[1]===1&&this.guardOwned&&!this.guard)||([2,4].includes(f[1])&&!this.guard))out=Uint8Array.of(1,4);
      else{
        if(f[1]===1){this.guard=true;this.guardOwned=true;}
        if(f[1]===3){this.guard=false;this.guardOwned=false;}
        if(f[1]===4)this.correlation=dec.decode(f.slice(34,36));
        if([1,2,4].includes(f[1]))this.guardExpiresAt=Date.now()+300000;
        out=Uint8Array.of(0);
      }
    }
    else if(f[0]===26){assert(this.guard);assert(['','password'].includes(dec.decode(f.slice(33))));out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);if(!this.suppressLogin)queueMicrotask(()=>this.push(wire(Uint8Array.of(this.loginCode,this.loginPermissions,...fromHex(key).slice(0,6),0,0,0,0,3,1))));}
    else if(f[0]===2){assert(this.guard);assert.equal(f[1],1);assert.equal(Buffer.from(f.slice(7,13)).toString('hex'),key.slice(0,12));const txt=dec.decode(f.slice(13)),cmd=txt.slice(3);assert.equal(txt.slice(0,3),this.correlation+'|');this.remoteAttempts[cmd]=(this.remoteAttempts[cmd]||0)+1;this.pendingReply=this.remoteAttempts[cmd]<=(this.timeoutFirst[cmd]||0);this.reply=cmd==='get public.key'?'> '+key:'> 9';out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);}
    else if(f[0]===66&&this.noCli){out=Uint8Array.of(1,2);}
    else if(f[0]===66){const cmd=dec.decode(f.slice(1));let reply;
      if(cmd==='get ota.direct')reply=`> ota.direct v=2 active=${this.guard?1:0} busy=0`;
      else if(cmd==='ver')reply='v1.18.0.2-dev-12345678 (Build: 06-Nov-2026)';
      else if(cmd==='get usb.raw.max')reply='> 188';
      else if(cmd==='get ota.direct.capabilities')reply=this.legacy?'Unknown command':this.capabilities||'> ota.direct.cap guarded_cli=1 guarded_radio=1';
      else if(cmd==='get radio.rxps')reply=this.rxps;
      else if(cmd==='get radio')reply='> 910.525,62.5,7,5';
      else if(cmd.startsWith('get ota.direct.reply ')){const at=Number(cmd.split(' ').pop()),bytes=enc.encode(this.reply),part=bytes.slice(at,at+40);reply=this.pendingReply?`> ota.reply req=${this.correlation} state=pending bytes=0 off=0 data=-`:`> ota.reply req=${this.correlation} state=ready bytes=${bytes.length} off=${at} data=${Buffer.from(part).toString('hex')||'-'}`;}
      else if(cmd==='get tempradio')reply='TempRadio active: 910.525,62.5,7,5 100s left,preamble=32';
      else throw Error('unexpected CLI '+cmd);out=Uint8Array.of(29,...enc.encode(reply));
    }else if(f[0]===65){assert.deepEqual(Array.from(f.slice(2,4)),[0x32,0]);out=Uint8Array.of(0);}
    else throw Error('unexpected frame '+f[0]);
    queueMicrotask(()=>this.push(wire(out)));
  }
  async close(){}
}
// Retry/budget unit models replace the RF layer entirely. Authorization and
// its live USB lease are exercised separately against the complete Port above.
function modelAuthorization(c){c.ensureGuardedSession=async()=>{if(!c.authenticated)await c.login('');};return c;}
async function run(){
  {
    const p=new Port();p.capabilities='> ota.direct.cap guarded_cli=1 guarded_radio=1 guarded_wifi=0 guarded_ble=1';
    const c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000});
    await c.open();assert.equal(c.otaSupported,true);assert.equal(c.wifiGuardSupported,false);assert.equal(c.bleGuardSupported,true);await c.close();
  }
  {
    const p=new Port(),c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000});
    await c.open();assert.equal(c.bleGuardSupported,false);await c.close();
  }
  {
    const p=new Port(),c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000});
    await c.open();
    assert.equal(await c.advertPath(key),null);
    p.advertFrame=Uint8Array.of(22,4,3,2,1,0x42,0xaa,0xbb,0xcc,0xdd);
    assert.deepEqual(await c.advertPath(key),{timestamp:0x01020304,hops:2,path:['AABB','CCDD']});
    p.advertFrame=Uint8Array.of(22,0,0,0,0,0);
    assert.deepEqual(await c.advertPath(key),{timestamp:0,hops:0,path:[]});
    p.advertFrame=Uint8Array.of(22,0,0,0,0,0xc1,0xaa,0xbb,0xcc,0xdd);
    await assert.rejects(c.advertPath(key),/Invalid advertisement path/);
    p.advertFrame=Uint8Array.of(1,1);
    await assert.rejects(c.advertPath(key),error=>error.companionErrorCode===1);
    await c.close();
  }
  {
    const c=new CompanionTransport({},{}),originalNow=Date.now,clock=originalNow();
    Object.assign(c,{serving:true,otaSupported:true,usbRawFrameMax:188,otaRadioProfile:1,preamble:368,
      radio:[910.525,62.5,7,5],sourceTempRadio:{radio:[909.95,500,5,7],preamble:368}});
    const raw=new Uint8Array(186);raw.set([0x32,0,7]);let sent=0;c.command=async()=>{sent++;};
    Date.now=()=>clock;
    try{
      await c.sendRadioOrdered(raw);
      assert.equal(c.nextTx-clock,250,'SF5/BW500 with preamble 368 retains the fast sender interval');
      c.sourceTempRadio.radio=[909.95,125,10,7];c.nextTx=0;
      await c.sendRadioOrdered(raw);const longPreambleDelay=c.nextTx-clock;
      assert.ok(longPreambleDelay>10000,'slow radio2 uses its own airtime rather than the saved primary');
      c.preamble=128;c.nextTx=0;await c.sendRadioOrdered(raw);
      assert.ok(c.nextTx-clock<longPreambleDelay,'preamble changes are included in the actual packet budget');
      assert.equal(sent,3);
    }finally{Date.now=originalNow;}
  }
  {
    const p=new Port(),c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000,remoteTimeout:75000});
    await c.open();await c.contacts();c.selectTarget(key);await c.verifyPeer(key,'password');
    const options=c.options,seen=[],remoteReply=c.remoteReply.bind(c);
    c.remoteReply=async(text,...args)=>{seen.push([text,c.options.remoteTimeout,c.options.commandTimeout]);return remoteReply(text,...args);};
    p.timeoutFirst['get tempradio2']=1;
    await assert.rejects(c.withOtaAdminBurst(key,'password',io=>io.remote('get tempradio2'),{readTimeoutMs:5}),
      error=>error.loraReplyTimeout===true&&error.otaTimeoutMs===5);
    assert.equal(c.options,options);assert.equal(c.failure,null);
    await c.withOtaAdminBurst(key,'password',async io=>{await io.remote('ota status');await io.remote('set repeat off');},{readTimeoutMs:5});
    assert.deepEqual(seen,[['get tempradio2',5,1000],['ota status',5,1000],['set repeat off',75000,1000]]);
    assert.equal(p.remoteAttempts['get tempradio2'],1);assert.equal(c.options,options);
    const before=p.frames.length;
    for(const readTimeoutMs of [0,-1,NaN,Infinity,20001,'5'])
      await assert.rejects(c.withOtaAdminBurst(key,'password',io=>io.remote('ota status'),{readTimeoutMs}),/Invalid/);
    assert.equal(p.frames.length,before);await c.close();
  }
  // Prepare can be followed by an arbitrarily long human pause. The firmware
  // lease expires after five minutes, not when the page clears its JS flags.
  for(const nextAction of ['verify','read','write','burst','mailbox-race']){
    const p=new Port(),c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000,remoteTimeout:120});
    await c.open();await c.contacts();c.selectTarget(key);await c.verifyPeer(key,'password');
    const before=p.frames.length,proofs=p.remoteAttempts['get public.key'];
    assert.equal(await c.verifyPeer(key,'password'),key);
    assert.equal(p.remoteAttempts['get public.key'],proofs,'an active owned lease needs no extra LoRa identity read');
    assert(p.frames.slice(before).every(frame=>frame[0]===66),'active cache refresh stays USB-only');
    const savedNow=Date.now;
    try{
      if(nextAction==='mailbox-race')p.expireBeforeArm=true;
      else{Date.now=()=>savedNow()+300001;p.clock=Math.floor(Date.now()/1000);}
      if(nextAction==='verify')assert.equal(await c.verifyPeer(key,'password'),key);
      else if(nextAction==='burst')assert.equal(await c.withOtaAdminBurst(key,'password',io=>io.remote('ota status')),'> 9');
      else if(nextAction==='write'){
        p.timeoutFirst['ota install']=1;
        await assert.rejects(c.remoteCommand(key,'password','ota install'),error=>error.loraReplyTimeout===true);
        assert.equal(p.remoteAttempts['ota install'],1,'a lost install reply never replays the install');
      }else assert.equal(await c.remoteCommand(key,'password','ota status'),'> 9');
      assert.equal(p.remoteAttempts['get public.key'],proofs+1,'expired ownership requires one new full-key proof');
      assert.equal(p.frames.slice(before).filter(frame=>frame[0]===52&&frame[1]===3).length,1,'one exact-target local close clears the expired owner');
      assert.equal(p.frames.slice(before).filter(frame=>frame[0]===26).length,1);
      assert.equal(c.authenticated,true);assert.equal(c.authenticatedPassword,'password');assert.equal(c.guard,true);
      assert(!p.frames.slice(before).some(frame=>frame[0]===65||frame[0]===66&&dec.decode(frame.slice(1)).startsWith('set ')));
    }finally{Date.now=savedNow;await c.close();}
  }
  for(const lost of ['login','identity']){
    const p=new Port(),c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000,remoteTimeout:120});
    await c.open();await c.contacts();c.selectTarget(key);
    if(lost==='login')p.suppressLogin=true;else p.timeoutFirst['get public.key']=1;
    await assert.rejects(c.verifyPeer(key,'password'),error=>error.loraReplyTimeout===true);
    assert.equal(c.authenticated,false);assert.equal(c.guard,true);assert.equal(p.guard,true);
    p.suppressLogin=false;const before=p.frames.length;
    assert.equal(await c.verifyPeer(key,'password'),key);
    assert.equal(p.frames.slice(before).filter(frame=>frame[0]===52&&frame[1]===3).length,1);
    assert.equal(p.frames.slice(before).filter(frame=>frame[0]===26).length,1);
    assert.equal(p.frames.slice(before).filter(frame=>frame[0]===2).length,1,'only the full-key proof goes over LoRa');
    assert.equal(c.authenticated,true);await c.close();
  }
  {
    const p=new Port(),c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000,remoteTimeout:75000});
    await c.open();await c.contacts();c.selectTarget(key);await c.verifyPeer(key,'password');
    const options=c.options,proofs=p.remoteAttempts['get public.key'];
    assert.equal(await c.remoteCommand(key,'password','ota status',{maxDurationMs:5000,allowReacquire:false}),'> 9');
    assert.equal(p.remoteAttempts['get public.key'],proofs);
    p.timeoutFirst['set repeat off']=1;
    await assert.rejects(c.remoteCommand(key,'password','set repeat off',
      {optionalReplyTimeoutMs:5,allowReacquire:false}),error=>error.loraReplyTimeout&&error.otaTimeoutMs===5);
    assert.equal(p.remoteAttempts['set repeat off'],1);assert.equal(c.options,options);
    p.expireBeforeArm=true;const beforeRace=p.frames.length;
    await assert.rejects(c.remoteCommand(key,'password','set repeat on',
      {optionalReplyTimeoutMs:5,allowReacquire:false}),error=>error.otaOptionalUnavailable===true);
    assert.equal(p.remoteAttempts['set repeat on'],undefined);
    assert(!p.frames.slice(beforeRace).some(frame=>frame[0]===26||frame[0]===52&&frame[1]===3));
    const before=p.frames.length;
    for(const [command,limits] of [['ota status',{maxDurationMs:5000,allowReacquire:false}],
      ['set repeat on',{optionalReplyTimeoutMs:5000,allowReacquire:false}]])
      await assert.rejects(c.remoteCommand(key,'password',command,limits),error=>
        error.otaEndpoint==='USB Companion'&&error.otaOptionalUnavailable===true&&/optional check needs an active/.test(error.message));
    assert(p.frames.slice(before).every(frame=>frame[0]===66),'optional expired-session checks never log in, close, or send RF');
    const invalid=[['set repeat on',{optionalReplyTimeoutMs:5000}],['ota status',{optionalReplyTimeoutMs:5000,allowReacquire:false}],
      ['ota install',{allowReacquire:false}],['set radio2.flood off',{optionalReplyTimeoutMs:5000,allowReacquire:false}],
      ['set repeat off extra',{optionalReplyTimeoutMs:5000,allowReacquire:false}],['set repeat off',{optionalReplyTimeoutMs:10001,allowReacquire:false}],
      ['set repeat off',{optionalReplyTimeoutMs:NaN,allowReacquire:false}],['ota status',{allowReacquire:'no'}]];
    const sent=p.frames.length;
    for(const [command,limits] of invalid)await assert.rejects(c.remoteCommand(key,'password',command,limits),/Optional command limits/);
    assert.equal(p.frames.length,sent);assert.equal(c.options,options);
    await c.close();
  }
  // Preparation retries only unanswered read-only radio requests, never the
  // login, a mutation or a source USB failure; each attempt gets a fresh tag.
  {
    const p=new Port(),c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000,remoteTimeout:75000,investigationTimeout:120});
    await c.open();await c.contacts();c.selectTarget(key);await c.verifyPeer(key,'password');
    const options=c.options,loginCount=p.frames.filter(frame=>frame[0]===26).length;
    p.timeoutFirst['ota self']=1;assert.equal(await c.remoteCommand(key,'password','ota self'),'> 9');assert.equal(p.remoteAttempts['ota self'],2);assert.equal(c.options,options);
    p.timeoutFirst['ota status']=11;await assert.rejects(c.remoteCommand(key,'password','ota status'),error=>{assert.equal(error.message,'Remote command timed out; result is unknown');assert.equal(error.otaAttempt,11);assert.equal(error.otaMaxAttempts,11);assert.equal(error.attempts,11);assert.equal(error.otaTimeoutMs,120);assert.equal(error.loraReplyTimeout,true);return true;});
    assert.equal(p.remoteAttempts['ota status'],11);assert.equal(p.frames.filter(frame=>frame[0]===26).length,loginCount);assert.equal(c.options,options);
    const tags=p.frames.filter(frame=>frame[0]===2).map(frame=>dec.decode(frame.slice(13)).slice(0,3));assert.equal(new Set(tags).size,tags.length);await c.close();
  }
  for(const [hops,expected] of [[0,7000],[null,20000],[1,20000]]){
    const c=modelAuthorization(new CompanionTransport({}, {knownRepeater:key,remoteTimeout:75000}));c.otaSupported=true;c.authenticated=true;c.running=true;c.contactList=[{publicKey:key,hops}];
    const options=c.options,timeouts=[];c.remoteReply=async()=>{timeouts.push(c.options.remoteTimeout);if(timeouts.length===1){const error=Error('Remote command timed out; result is unknown');error.loraReplyTimeout=true;throw error;}return '> self';};
    assert.equal(await c.remoteCommand(key,'','ota self'),'> self');assert.deepEqual(timeouts,[expected,expected]);assert.equal(c.options,options);
  }
  // A command that still has no confirmed authentication gets seven read
  // attempts; an authenticated guarded target gets eleven, not three.
  for(const confirmed of [false,true]){
    const c=modelAuthorization(new CompanionTransport({}, {knownRepeater:key}));c.otaSupported=true;c.running=true;c.authenticated=confirmed;
    c.login=async()=>{};let calls=0;
    c.remoteReply=async()=>{calls++;throw Object.assign(Error('Remote command timed out; result is unknown'),{loraReplyTimeout:true});};
    await assert.rejects(c.remoteCommand(key,'','ota self'),error=>{
      assert.equal(error.otaMaxAttempts,confirmed?11:7);assert.equal(error.otaAttempt,confirmed?11:7);
      assert.equal(error.attempts,confirmed?11:7);return true;
    });assert.equal(calls,confirmed?11:7);
  }
  {
    const make=()=>{const c=modelAuthorization(new CompanionTransport({}, {knownRepeater:key,remoteTimeout:75000}));
      c.otaSupported=true;c.running=true;c.authenticated=true;c.supportsOtaRadio2=true;c.otaRadioProfile=1;return c;};
    const originalNow=Date.now;let now=1000;
    try{
      Date.now=()=>now;
      for(const lateSuccess of [false,true]){
        const c=make(),options=c.options,timeouts=[];
        c.remoteReply=async()=>{timeouts.push(c.options.remoteTimeout);now+=10;
          if(lateSuccess)return '> too late';
          throw Object.assign(Error('Remote command timed out; result is unknown'),{loraReplyTimeout:true});};
        await assert.rejects(c.remoteCommand(key,'','ota self',{maxDurationMs:10}),error=>{
          assert.equal(error.loraReplyTimeout,true);assert.equal(error.otaBudgetExhausted,true);
          assert.equal(error.otaAttempt,1);assert.equal(error.otaMaxAttempts,11);assert.equal(error.attempts,1);
          assert.equal(error.otaCommand,'ota self');assert.equal(error.otaEndpoint,'remote target');
          assert.match(error.otaPath,/direct \(0 hops/);assert.match(error.otaRadio,/Radio 2/);return true;
        });assert.deepEqual(timeouts,[10]);assert.equal(c.options,options);assert.equal(c.authenticated,true);
        assert.equal(c.diagnosticDeadline,undefined);
      }
      const c=make(),options=c.options,timeouts=[];
      c.remoteReply=async()=>{timeouts.push(c.options.remoteTimeout);now+=4;if(timeouts.length===1)throw Object.assign(Error('miss'),{loraReplyTimeout:true});return '> ok';};
      assert.equal(await c.remoteCommand(key,'','ota self',{maxDurationMs:10}),'> ok');
      assert.deepEqual(timeouts,[10,6]);assert.equal(c.options,options);
      const queued=make();let release,calls=0;queued.admin=new Promise(resolve=>{release=resolve;});queued.remoteReply=async()=>{calls++;return '> no';};
      const pending=queued.remoteCommand(key,'','ota self',{maxDurationMs:10});now+=10;release();
      await assert.rejects(pending,error=>error.otaBudgetExhausted&&error.otaAttempt===0);assert.equal(calls,0);
      for(const invalid of [0,-1,NaN,Infinity,'10'])await assert.rejects(queued.remoteCommand(key,'','ota self',{maxDurationMs:invalid}),/finite positive/);
      await assert.rejects(queued.remoteCommand(key,'','set tx 9',{maxDurationMs:10}),/read-only/);assert.equal(calls,0);
      const backwards=make(),before=backwards.options;
      backwards.remoteReply=async()=>{now--;return '> no';};
      await assert.rejects(backwards.remoteCommand(key,'','ota self',{maxDurationMs:10}),/Invalid remote read clock/);assert.equal(backwards.options,before);
    }finally{Date.now=originalNow;}
    const c=make();let calls=0;c.remoteReply=async()=>{calls++;return '> ok';};
    assert.equal(await c.remoteCommand(key,'','ota self',{replyTimeoutMs:5}),'> ok');assert.equal(calls,1);
  }
  for(const command of ['set tx 9','ota pull 01234567 flash','ota install','ota cancel','login','ota self extra']){
    const c=modelAuthorization(new CompanionTransport({}, {knownRepeater:key}));c.otaSupported=true;c.authenticated=true;c.running=true;let calls=0;
    const original=Object.assign(Error('Remote command timed out; result is unknown'),{loraReplyTimeout:true});c.remoteReply=async()=>{calls++;throw original;};
    await assert.rejects(c.remoteCommand(key,'',command),error=>error===original);assert.equal(calls,1,command);
  }
  for(const kind of ['USB','failure','disconnect','cancel','other']){
    const c=modelAuthorization(new CompanionTransport({}, {knownRepeater:key}));c.otaSupported=true;c.authenticated=true;c.running=true;let calls=0;
    const original=Object.assign(Error(kind==='other'?'Malformed mailbox':'Remote command timed out; result is unknown'),{loraReplyTimeout:kind!=='other'});
    if(kind==='USB')original.otaEndpoint='USB Companion';
    c.remoteReply=async()=>{calls++;if(kind==='failure')c.failure=original;if(kind==='disconnect')c.running=false;if(kind==='cancel')c.investigationCancelled=true;throw original;};
    await assert.rejects(c.remoteCommand(key,'','ota self'),error=>error===original);assert.equal(calls,1,kind);
  }
  {
    const p=new Port();p.suppressLogin=true;const c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000,remoteTimeout:120});
    await c.open();await c.contacts();c.selectTarget(key);await assert.rejects(c.remoteCommand(key,'password','ota self'),/login timed out/);assert.equal(p.frames.filter(frame=>frame[0]===26).length,1);assert.equal(p.remoteAttempts['ota self'],undefined);await c.close();
  }
  {
    const p=new Port(),c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000,remoteTimeout:75000});
    await c.open();await c.contacts();c.selectTarget(key);await c.verifyPeer(key,'password');const options=c.options,before=p.frames.length;
    p.timeoutFirst.ver=1;await assert.rejects(c.diagnosticRead(key,'password','ver',{timeoutMs:120}),error=>error.loraReplyTimeout&&error.otaCommand==='ver');assert.equal(p.remoteAttempts.ver,1);assert.equal(c.options,options);assert.equal(c.diagnosticDeadline,undefined);
    assert.equal(await c.diagnosticRead(key,'password','board',{timeoutMs:120}),'> 9');assert.equal(p.remoteAttempts.board,1);assert.equal(c.options,options);assert.equal(c.diagnosticDeadline,undefined);
    let sent=p.frames.length;assert.equal(await c.diagnosticRead(key,'password','board',{timeoutMs:120}),'> 9');assert.equal(p.frames.length,sent,'cached board requires no new radio request');
    assert.equal(await c.remoteCommand(key,'password','ver'),'> 9');sent=p.frames.length;assert.equal(await c.diagnosticRead(key,'password','ver',{timeoutMs:120}),'> 9');assert.equal(p.frames.length,sent,'normal prior version reply requires no new radio request');
    const cached=c.cachedFailureIdentity(key);assert.equal(cached.version,'9');assert.equal(cached.board,'9');assert.equal(cached.origin,'earlier target reply in this session');cached.version='changed snapshot';assert.equal(c.cachedFailureIdentity(key).version,'9');
    const frames=p.frames.slice(before);assert(!frames.some(frame=>frame[0]===26||frame[0]===65||frame[0]===52&&[1,2,3].includes(frame[1])));assert(frames.every(frame=>[2,52,66].includes(frame[0])));await c.close();
  }
  {
    const make=()=>{const c=new CompanionTransport({}, {knownRepeater:key});c.sourceKey=source;c.otaSupported=true;c.guard=true;c.authenticated=true;c.authenticatedPassword='password';c.running=true;return c;};
    for(const command of ['get public.key','ota self','set tx 9','ver extra','board extra',' ver']){const c=make();let calls=0;c.remoteReply=async()=>{calls++;};await assert.rejects(c.diagnosticRead(key,'password',command),/only ver or board/);assert.equal(calls,0);}
    for(const patch of [{guard:false},{authenticated:false},{running:false},{otaSupported:false},{failure:Error('USB failed')},{investigationCancelled:true},{pending:{}},{investigationAttempt:1},{serving:true},{seeder:{}},{radioRunning:true},{radioQueue:[1]},{usbFolderSource:{}},{usbFolderAttached:true}]){
      const c=Object.assign(make(),patch);let calls=0;c.remoteReply=async()=>{calls++;};await assert.rejects(c.diagnosticRead(key,'password','ver'),/existing healthy|cancel/i);assert.equal(calls,0);
    }
    const c=make();let calls=0;c.remoteReply=async()=>{calls++;};await assert.rejects(c.diagnosticRead(key,'wrong password','ver'),/existing healthy/);await assert.rejects(c.diagnosticRead('cc'.repeat(32),'password','ver'),/Target differs/);for(const timeoutMs of [0,-1,5001,Infinity,NaN,'5000'])await assert.rejects(c.diagnosticRead(key,'password','ver',{timeoutMs}),/timeout/);assert.equal(calls,0);
    await assert.rejects(c.diagnosticRead(key,'password','ver',{canContinue:()=>false}),/canceled/);await assert.rejects(c.diagnosticRead(key,'password','ver',{canContinue:'invalid'}),/continuation guard/);assert.equal(calls,0);
    let release;c.admin=new Promise(resolve=>{release=resolve;});const pending=c.diagnosticRead(key,'password','ver');c.running=false;release();await assert.rejects(pending,/existing healthy/);assert.equal(calls,0);
    const queued=make();queued.remoteReply=async()=>{calls++;};let allow=true,releaseQueued;queued.admin=new Promise(resolve=>{releaseQueued=resolve;});const waiting=queued.diagnosticRead(key,'password','ver',{canContinue:()=>allow});allow=false;releaseQueued();await assert.rejects(waiting,/canceled/);assert.equal(calls,0);
  }
  {
    const c=new CompanionTransport({}, {knownRepeater:key});c.sourceKey=source;
    for(const reply of ['','Error: unsupported','Unknown command','permission denied','?','none'])c.rememberFailureIdentity('board',reply);assert.equal(c.cachedFailureIdentity(key),null);
    c.rememberFailureIdentity('ota status','OTA hw=heltec_v4_2_v4_3 t=1');let cached=c.cachedFailureIdentity(key);assert.equal(cached.board,'hardware ID heltec_v4_2_v4_3 (reported by ota status)');assert.equal(cached.boardOrigin,'earlier OTA status hardware ID');
    c.rememberFailureIdentity('board','> Heltec V4.3 OLED');c.rememberFailureIdentity('ver','v1.18.0.2 (Build: test)');c.rememberFailureIdentity('ota status','OTA hw=another_target t=1');cached=c.cachedFailureIdentity(key);assert.equal(cached.board,'Heltec V4.3 OLED');assert.equal(cached.version,'v1.18.0.2 (Build: test)');assert.equal(cached.boardOrigin,'earlier board reply');
    c.sourceKey='cc'.repeat(32);assert.equal(c.cachedFailureIdentity(key),null);c.sourceKey=source;c.selectTarget('dd'.repeat(32));assert.equal(c.cachedFailureIdentity('dd'.repeat(32)),null);c.selectTarget(key);assert.equal(c.cachedFailureIdentity(key),null);
    for(const hardware of ['unknown','unset','none','-','?','0'])c.rememberFailureIdentity('ota status','OTA hw='+hardware+' t=1');assert.equal(c.cachedFailureIdentity(key),null);
  }
  {
    const p=new Port(),c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000});await c.open();await c.contacts();c.selectTarget(key);await c.verifyPeer(key,'password');
    let allowed=true;const write=p.write.bind(p);p.write=bytes=>{const result=write(bytes),frame=bytes.slice(3);if(frame[0]===52&&frame[1]===4)allowed=false;return result;};const before=p.frames.length;
    await assert.rejects(c.diagnosticRead(key,'password','ver',{timeoutMs:120,canContinue:()=>allowed}),/canceled/);assert.deepEqual(p.frames.slice(before).map(frame=>frame[0]),[52]);assert.equal(c.diagnosticCanContinue,undefined);await c.close();assert.equal(c.cachedFailureIdentity(key),null);
  }
  {
    const c=new CompanionTransport({}, {knownRepeater:key});c.localCliSupported=true;
    c.command=async()=>{throw Error('USB disconnected');};
    await assert.rejects(c.localCommandBinary('set tempradio2 PRIVATE_SETTINGS'),error=>{assert.equal(error.otaCommand,'set tempradio2 <arguments hidden>');assert(!JSON.stringify(error).includes('PRIVATE_SETTINGS'));return true;});
  }
  // Error metadata keeps original objects/messages and hides every argument.
  for(const command of ['get ota.direct.reply 0','set wifi.password PRIVATE_WIFI_SECRET']){
    const c=new CompanionTransport({}, {commandTimeout:5});c.running=true;c.writer={write:async()=>{}};
    let caught;await assert.rejects(c.commandAny(Uint8Array.of(66,...enc.encode(command)),[29]),error=>{caught=error;return /Companion response timed out/.test(error.message);});
    assert.equal(caught,c.failure);assert.equal(caught.otaEndpoint,'USB Companion');assert.equal(caught.otaTimeoutMs,5);
    assert.equal(caught.otaCommand,command.startsWith('get')?'get ota.direct.reply <arguments hidden>':'set <arguments hidden>');
    assert.equal(caught.otaRadio,command.startsWith('get')?'USB/local command (no LoRa transmission)':'USB/local command (LoRa transmission not confirmed)');
    assert(!JSON.stringify(caught).includes('PRIVATE_WIFI_SECRET'));c.running=false;
  }
  {
    const c=new CompanionTransport({}, {knownRepeater:key,remoteTimeout:1234});c.guardMode=async()=>{};c.supportsOtaRadio2=true;c.otaRadioProfile=1;
    const inner=Object.assign(Error('Original mailbox failure'),{otaStep:'Reading source reply mailbox',otaCommand:'get ota.direct.reply <arguments hidden>',otaEndpoint:'USB Companion',otaTimeoutMs:17,otaRadio:'USB/local command (no LoRa transmission)',loraReplyTimeout:true});
    c.command=async()=>Uint8Array.of(6);c.localCommand=async()=>{c.otaRadioProfile=0;throw inner;};
    await assert.rejects(c.remoteReply('get public.key'),error=>{
      assert.equal(error,inner);assert.equal(error.message,'Original mailbox failure');assert.equal(error.otaEndpoint,'USB Companion');assert.equal(error.otaTimeoutMs,17);assert.equal(error.otaRequestCommand,'get public.key');assert.equal(error.otaRequestStep,'Verifying target identity');assert.match(error.otaRequestRadio,/Source: Radio 2/);assert.match(error.otaPath,/direct.*0 hops.*enforced/);assert.equal(error.loraReplyTimeout,true);return true;
    });
  }
  {
    const c=new CompanionTransport({}, {knownRepeater:key,investigationTimeout:12});c.investigationEnabled=true;c.investigationAuthenticated=true;c.contactList=[{publicKey:key,hops:3}];c.contacts=async()=>c.contactList;
    const original=Error('Original remote rejection');c.command=async()=>{throw original;};
    await assert.rejects(c.standardReply('ota status'),error=>{assert.equal(error,original);assert.equal(error.otaCommand,'ota status');assert.equal(error.otaEndpoint,'remote target');assert.match(error.otaPath,/stored route: 3 hops.*not verified/);return true;});
    let calls=0;await assert.rejects(c.retryInvestigation('ota status',async()=>{calls++;const error=Error('Remote command timed out; result is unknown');error.loraReplyTimeout=true;throw error;}),error=>{assert.equal(error.message,'Remote command timed out; result is unknown');assert.equal(error.otaAttempt,11);assert.equal(error.otaMaxAttempts,11);assert.equal(error.attempts,11);return true;});assert.equal(calls,11);
  }
  {
    const descriptor=Object.getOwnPropertyDescriptor(globalThis,'navigator');Object.defineProperty(globalThis,'navigator',{configurable:true,value:{locks:{request:async(_name,_options,fn)=>fn({})}}});
    try{
      const c=new CompanionTransport({}, {knownRepeater:key,inboxPollDelay:0});c.investigationEnabled=true;c.investigationAuthenticated=true;c.contactList=[{publicKey:key,hops:0}];c.contacts=async()=>c.contactList;c.command=async()=>Uint8Array.of(6);c.reserveInbox=()=> 'reservation';
      const inner=Object.assign(Error('Original inbox timeout'),{otaStep:'Waiting for USB command reply',otaCommand:'reading a queued message',otaEndpoint:'USB Companion',otaTimeoutMs:31,otaRadio:'USB/local command (no LoRa transmission)'});c.commandAny=async()=>{throw inner;};
      await assert.rejects(c.standardReply('ota status'),error=>{assert.equal(error.message,'Original inbox timeout; a queued message may have been removed before it could be saved');assert.equal(error.otaEndpoint,'USB Companion');assert.equal(error.otaTimeoutMs,31);assert.equal(error.otaRequestCommand,'ota status');assert.equal(error.otaRadio,'USB/local command (no LoRa transmission)');return true;});
      c.sourceKey=source;c.finishInbox=()=>{};c.commandAny=async()=>Uint8Array.of(7,...fromHex(key).slice(0,6),0,1,0,0,0,0,...enc.encode([...c.used].at(-1)+'|Heltec V4.3 OLED'));
      assert.equal(await c.standardReply('board'),'Heltec V4.3 OLED');assert.equal(c.cachedFailureIdentity(key).board,'Heltec V4.3 OLED');
    }finally{if(descriptor)Object.defineProperty(globalThis,'navigator',descriptor);else delete globalThis.navigator;}
  }
  const d=new Decoder(),frame=wire(Uint8Array.of(29,1,2));assert.deepEqual(d.feed(frame.slice(0,2)),[]);assert.deepEqual(Array.from(d.feed(frame.slice(2))[0]),[29,1,2]);
  const port=new Port(),link=new CompanionTransport(port,{openSettle:0,commandTimeout:1000,remoteTimeout:1000});
  await link.open();assert.equal(link.sourceKey,source);assert.equal(link.firmwareVersion,'v1.18.0.2-dev-12345678');assert.equal(link.firmwareBoardName,'Test board');assert.equal(port.frames.filter(frame=>frame[0]===22).length,1);assert.equal(link.otaMode,'guarded');assert.equal(await link.localCommand('get radio'),'> 910.525,62.5,7,5');
  assert.equal(await link.localCommand('get radio.rxps'),'> off,unsupported');port.rxps='Error: unsupported';assert.equal(await link.localCommand('get radio.rxps'),'Error: unsupported');
  const contacts=await link.contacts();assert.equal(contacts.length,4);assert.equal(contacts[0].key,key);assert.equal(contacts[0].hops,null);assert.equal(contacts.find(c=>c.type===4).name,'Sensor');
  link.selectTarget('33'.repeat(32));assert.equal(link.peerType,4);link.selectTarget(key);
  await link.verifyPeer(key,'password');assert.equal(await link.remoteCommand(key,'password','get tx'),'> 9');
  assert.throws(()=>link.selectTarget('11'.repeat(32)),/active OTA session/);
  await assert.rejects(()=>link.remoteCommand('cc'.repeat(32),'password','get tx'),/Target differs/);
  await assert.rejects(()=>link.setPath(['11']),/zero-hop/);
  globalThis.LoraOtaCompanionSource={Source:{create:async()=>({advert:()=>Uint8Array.of(0x32,0,1),replies:async()=>[]})}};
  await link.serve({},new Uint8Array(),null);await new Promise(r=>setTimeout(r,20));await link.stopServing();
  assert(port.frames.some(f=>f[0]===65));await link.close();assert.equal(port.guard,false);
  const old=new Port();old.legacy=true;const oldLink=new CompanionTransport(old,{openSettle:0,commandTimeout:1000});
  await oldLink.open();assert.equal(oldLink.otaSupported,false);assert.equal((await oldLink.contacts()).length,4);oldLink.selectTarget(key);
  await assert.rejects(()=>oldLink.verifyPeer(key,'password'),/inbox warning/);assert(!old.frames.some(f=>f[0]===26||f[0]===65));await oldLink.close();
  const stock=new Port();stock.noCli=true;const stockLink=new CompanionTransport(stock,{openSettle:0,commandTimeout:1000});
  await stockLink.open();assert.equal(stockLink.otaSupported,false);assert.equal(stockLink.firmwareVersion,'v1.16.05');assert.equal(stockLink.otaMode,'legacy');assert.equal((await stockLink.contacts()).length,4);stockLink.selectTarget(key);
  await assert.rejects(()=>stockLink.verifyPeer(key,'password'),/inbox warning/);assert(!stock.frames.some(f=>f[0]===26||f[0]===65));await stockLink.close();
  const stale=new Port();stale.clock-=3600;const staleLink=new CompanionTransport(stale,{knownRepeater:key,openSettle:0,commandTimeout:1000});
  await staleLink.open();await assert.rejects(()=>staleLink.verifyPeer(key,'password'),/clock is out of sync/);assert(!stale.frames.some(f=>f[0]===6));await staleLink.close();
  const guest=new Port();guest.loginPermissions=2;const guestLink=new CompanionTransport(guest,{knownRepeater:key,openSettle:0,commandTimeout:1000});
  await guestLink.open();await assert.rejects(()=>guestLink.verifyPeer(key,'password'),/rejected the admin login/);assert(!guest.frames.some(f=>f[0]===2));await guestLink.close();
  const sensor=new Port();sensor.targetType=4;const sensorLink=new CompanionTransport(sensor,{openSettle:0,commandTimeout:1000});
  await sensorLink.open();await sensorLink.contacts();sensorLink.selectTarget(key);await sensorLink.verifyPeer(key,'password');
  assert(sensor.frames.some(f=>f[0]===52&&f[1]===1&&f[34]===4));await sensorLink.close();
  // The guarded investigation retries only unanswered reads, rearming its
  // mailbox with a new tag each time. A successful answer stops the attempts.
  const retry=new Port();retry.timeoutFirst={'get public.key':6,'ota status':10};const events=[];
  const retryLink=new CompanionTransport(retry,{openSettle:0,commandTimeout:1000,remoteTimeout:120,investigationTimeout:120,onInvestigationProgress:e=>events.push(e)});
  await retryLink.open();await retryLink.contacts();retryLink.selectTarget(key);
  await retryLink.investigateVerifyPeer(key,'');
  assert.equal(retry.remoteAttempts['get public.key'],7);
  assert.equal(await retryLink.investigateCommand(key,'','ota status'),'> 9');
  assert.equal(retry.remoteAttempts['ota status'],11);
  const sent=retry.frames.filter(f=>f[0]===2).map(f=>dec.decode(f.slice(13)));
  assert.equal(new Set(sent.map(s=>s.slice(0,3))).size,18);
  assert(events.some(e=>e.stage==='retrying-command'&&e.command==='get public.key'&&e.attempt===6&&e.maxAttempts===7));
  assert(events.some(e=>e.stage==='waiting-reply'&&e.command==='ota status'&&e.attempt===11&&e.maxAttempts===11));
  assert.equal(await retryLink.investigateCommand(key,'','ota ram'),'> 9');
  assert.equal(retry.remoteAttempts['ota ram'],1);
  const before=retry.frames.filter(f=>f[0]===2).length;
  for(const command of ['set tx 9','ota ram extra','ota ram;reboot'])
    await assert.rejects(()=>retryLink.investigateCommand(key,'',command),/read.only/i);
  assert.equal(retry.frames.filter(f=>f[0]===2).length,before);
  let calls=0;retryLink.remoteReply=async()=>{calls++;const e=Error('Remote command timed out; result is unknown');e.loraReplyTimeout=true;throw e;};
  await assert.rejects(()=>retryLink.investigateCommand(key,'','ota self'),e=>e.loraReplyTimeout&&e.attempts===11);assert.equal(calls,11);
  calls=0;retryLink.remoteReply=async()=>{calls++;throw Error('Serial connection lost');};
  await assert.rejects(()=>retryLink.investigateCommand(key,'','ota self'),/Serial connection lost/);assert.equal(calls,1);
  calls=0;retryLink.remoteReply=async()=>{calls++;retryLink.investigationCancelled=true;const e=Error('Remote command timed out; result is unknown');e.loraReplyTimeout=true;throw e;};
  await assert.rejects(()=>retryLink.investigateCommand(key,'','ota self'),/cancel/i);assert.equal(calls,1);
  await retryLink.close();
  const missing=new Port();missing.timeoutFirst={'get public.key':7};const missingEvents=[];
  const missingLink=new CompanionTransport(missing,{openSettle:0,commandTimeout:1000,investigationTimeout:80,onInvestigationProgress:e=>missingEvents.push(e)});
  await missingLink.open();await missingLink.contacts();missingLink.selectTarget(key);
  await assert.rejects(()=>missingLink.investigateVerifyPeer(key,''),e=>e.loraReplyTimeout&&e.attempts===7);
  assert.equal(missing.remoteAttempts['get public.key'],7);
  assert(missingEvents.some(e=>e.stage==='waiting-reply'&&e.command==='get public.key'&&e.attempt===7&&e.maxAttempts===7));
  await missingLink.close();
  const defaults=new Port(), defaultEvents=[];
  const defaultLink=new CompanionTransport(defaults,{openSettle:0,commandTimeout:1000,onInvestigationProgress:e=>defaultEvents.push(e)});
  await defaultLink.open();await defaultLink.contacts();defaultLink.selectTarget(key);
  await defaultLink.investigateVerifyPeer(key,'');
  assert(defaultEvents.some(e=>e.stage==='waiting-login'&&e.timeoutMs===20000));
  assert(defaults.frames.some(f=>f[0]===26&&f.length===33));
  assert(defaultEvents.some(e=>e.stage==='waiting-reply'&&e.command==='get public.key'&&e.timeoutMs===20000));
  defaultEvents.length=0;
  await defaultLink.remoteCommand(key,'','ota status');
  assert(defaultEvents.some(e=>e.stage==='waiting-reply'&&e.command==='ota status'&&e.timeoutMs===20000));
  await defaultLink.close();
  // Route evidence comes from the actual contact decoder, not a default
  // 'direct' assumption. Zero hops is valid with every supported hash width.
  for(const [path,expected] of [[0,7000],[64,7000],[128,7000],[1,20000],[65,20000],[129,20000],[255,20000]]){
    const p=new Port();p.targetPath=path;const events=[],timers=[];
    const c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000,onInvestigationProgress:e=>events.push(e)});
    await c.open();await c.contacts();c.selectTarget(key);
    assert.equal(c.investigationReplyTimeout(),expected);
    assert(!p.frames.some(f=>[2,10,26,52,65].includes(f[0])));
    const originalTimer=globalThis.setTimeout;
    globalThis.setTimeout=(fn,ms,...rest)=>{timers.push(ms);return originalTimer(fn,ms,...rest);};
    try{await c.investigateVerifyPeer(key,'');}finally{globalThis.setTimeout=originalTimer;}
    assert(timers.includes(expected));
    assert(events.some(e=>e.stage==='waiting-login'&&e.timeoutMs===expected));
    assert(events.some(e=>e.stage==='waiting-reply'&&e.command==='get public.key'&&e.timeoutMs===expected));
    events.length=0;await c.investigateCommand(key,'','ota status');
    assert(events.some(e=>e.stage==='waiting-reply'&&e.command==='ota status'&&e.timeoutMs===expected));
    await c.close();
  }
  const unknown=new CompanionTransport(new Port(),{knownRepeater:key});
  assert.equal(unknown.investigationReplyTimeout(),20000);
  unknown.contactList=[{publicKey:'cc'.repeat(32),hops:0}];
  assert.equal(unknown.investigationReplyTimeout(),20000);
  unknown.contactList=[{publicKey:key}];assert.equal(unknown.investigationReplyTimeout(),20000);
  for(const hops of [0,1,null,undefined]){
    const c=new CompanionTransport(new Port(),{knownRepeater:key,investigationTimeout:1234});
    c.contactList=[{publicKey:key,hops}];assert.equal(c.investigationReplyTimeout(),1234);
  }
  for(const type of ['rejected','timeout']){
    const p=new Port();p.loginCode=type==='rejected'?0x86:0x85;p.suppressLogin=type==='timeout';const events=[];
    const c=new CompanionTransport(p,{openSettle:0,commandTimeout:1000,investigationTimeout:120,onInvestigationProgress:e=>events.push(e)});
    await c.open();await c.contacts();c.selectTarget(key);await c.investigateVerifyPeer(key,'');
    assert(events.some(e=>e.stage==='login-fallback'));
    assert.equal(p.remoteAttempts['get public.key'],1);
    assert.equal(p.frames.filter(f=>f[0]===26).length,1);
    await c.close();
  }
  const rejected=new Port();rejected.loginCode=0x86;
  const rejectedLink=new CompanionTransport(rejected,{openSettle:0,commandTimeout:1000});
  await rejectedLink.open();await rejectedLink.contacts();rejectedLink.selectTarget(key);
  await assert.rejects(()=>rejectedLink.investigateVerifyPeer(key,'password'),error=>{assert.match(error.message,/rejected/i);assert.equal(error.otaStep,'Logging in to target');assert.equal(error.otaCommand,'login <arguments hidden>');assert.equal(error.otaEndpoint,'remote target');assert(!JSON.stringify(error).includes('password'));return true;});
  assert(!rejected.frames.some(f=>f[0]===2));await rejectedLink.close();
}
run().then(()=>console.log('Companion transport tests passed')).catch(e=>{console.error(e);process.exitCode=1;});
