const assert=require('node:assert/strict');
if(!globalThis.crypto)globalThis.crypto=require('node:crypto').webcrypto;
const {Decoder,CompanionTransport}=require('../docs/_javascript/lora_ota_companion.js');
const enc=new TextEncoder(),dec=new TextDecoder(),key='bb'.repeat(32),source='aa'.repeat(32);
const fromHex=s=>Uint8Array.from(s.match(/../g),x=>parseInt(x,16));
function wire(body){return Uint8Array.of(62,body.length&255,body.length>>8,...body)}
class Port{
  constructor(){this.waiter=null;this.q=[];this.frames=[];this.guard=false;this.correlation='';this.reply='';this.remoteAttempts={};this.timeoutFirst={};this.pendingReply=false;this.clock=Math.floor(Date.now()/1000);this.legacy=false;this.noCli=false;this.loginPermissions=1;this.targetType=3;this.rxps='Error: RX power saving is unsupported on this radio';}
  push(v){if(this.waiter){let f=this.waiter;this.waiter=null;f({value:v,done:false});}else this.q.push(v);}
  async open(){
    this.readable={getReader:()=>({read:()=>this.q.length?Promise.resolve({value:this.q.shift(),done:false}):new Promise(r=>this.waiter=r),cancel:async()=>{if(this.waiter)this.waiter({done:true});},releaseLock(){}})};
    this.writable={getWriter:()=>({write:async b=>this.write(b),close:async()=>{},releaseLock(){}})};
  }
  async write(b){
    assert.equal(b[0],60);assert.equal(b.length,b[1]+256*b[2]+3);const f=b.slice(3);this.frames.push(f);let out;
    if(f[0]===1){out=new Uint8Array(58);out[0]=5;out.set(fromHex(source),4);const v=new DataView(out.buffer);v.setUint32(48,910525,true);v.setUint32(52,62500,true);out[56]=7;out[57]=5;}
    else if(f[0]===4){
      const contact=(pub,type,fav,path,name)=>{const b=new Uint8Array(148);b[0]=3;b.set(fromHex(pub),1);b[33]=type;b[34]=fav?1:0;b[35]=path;b.set(enc.encode(name),100);return b;};
      queueMicrotask(()=>{this.push(wire(Uint8Array.of(2,4,0,0,0)));this.push(wire(contact('11'.repeat(32),2,false,2,'Repeater')));this.push(wire(contact(key,this.targetType,true,255,'Room')));this.push(wire(contact('33'.repeat(32),4,false,1,'Sensor')));this.push(wire(contact('44'.repeat(32),1,false,0,'Chat')));this.push(wire(Uint8Array.of(4,0,0,0,0)));});return;
    }
    else if(f[0]===5){out=Uint8Array.of(9,...new Uint8Array(new Uint32Array([this.clock]).buffer));}
    else if(f[0]===6){this.clock=new DataView(f.buffer,f.byteOffset,f.byteLength).getUint32(1,true);out=Uint8Array.of(0);}
    else if(f[0]===52){assert.equal(Buffer.from(f.slice(2,34)).toString('hex'),key);if(f[1]===1)this.guard=true;if(f[1]===3)this.guard=false;if(f[1]===4)this.correlation=dec.decode(f.slice(34,36));out=Uint8Array.of(0);}
    else if(f[0]===26){assert(this.guard);assert.equal(dec.decode(f.slice(33)),'password');out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);queueMicrotask(()=>this.push(wire(Uint8Array.of(0x85,this.loginPermissions,...fromHex(key).slice(0,6),0,0,0,0,3,1))));}
    else if(f[0]===2){assert(this.guard);assert.equal(f[1],1);assert.equal(Buffer.from(f.slice(7,13)).toString('hex'),key.slice(0,12));const txt=dec.decode(f.slice(13)),cmd=txt.slice(3);assert.equal(txt.slice(0,3),this.correlation+'|');this.remoteAttempts[cmd]=(this.remoteAttempts[cmd]||0)+1;this.pendingReply=this.remoteAttempts[cmd]<=(this.timeoutFirst[cmd]||0);this.reply=cmd==='get public.key'?'> '+key:'> 9';out=Uint8Array.of(6,0,0,0,0,0,0,0,0,0);}
    else if(f[0]===66&&this.noCli){out=Uint8Array.of(1,2);}
    else if(f[0]===66){const cmd=dec.decode(f.slice(1));let reply;
      if(cmd==='get ota.direct')reply=`> ota.direct v=2 active=${this.guard?1:0} busy=0`;
      else if(cmd==='get ota.direct.capabilities')reply=this.legacy?'Unknown command':'> ota.direct.cap guarded_cli=1 guarded_radio=1';
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
async function run(){
  const d=new Decoder(),frame=wire(Uint8Array.of(29,1,2));assert.deepEqual(d.feed(frame.slice(0,2)),[]);assert.deepEqual(Array.from(d.feed(frame.slice(2))[0]),[29,1,2]);
  const port=new Port(),link=new CompanionTransport(port,{openSettle:0,commandTimeout:1000,remoteTimeout:1000});
  await link.open();assert.equal(link.sourceKey,source);assert.equal(await link.localCommand('get radio'),'> 910.525,62.5,7,5');
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
  await assert.rejects(()=>oldLink.verifyPeer(key,'password'),/guarded zero-hop/);assert(!old.frames.some(f=>f[0]===26||f[0]===65));await oldLink.close();
  const stock=new Port();stock.noCli=true;const stockLink=new CompanionTransport(stock,{openSettle:0,commandTimeout:1000});
  await stockLink.open();assert.equal(stockLink.otaSupported,false);assert.equal((await stockLink.contacts()).length,4);stockLink.selectTarget(key);
  await assert.rejects(()=>stockLink.verifyPeer(key,'password'),/guarded zero-hop/);assert(!stock.frames.some(f=>f[0]===26||f[0]===65));await stockLink.close();
  const stale=new Port();stale.clock-=3600;const staleLink=new CompanionTransport(stale,{knownRepeater:key,openSettle:0,commandTimeout:1000});
  await staleLink.open();await assert.rejects(()=>staleLink.verifyPeer(key,'password'),/clock is out of sync/);assert(!stale.frames.some(f=>f[0]===6));await staleLink.close();
  const guest=new Port();guest.loginPermissions=2;const guestLink=new CompanionTransport(guest,{knownRepeater:key,openSettle:0,commandTimeout:1000});
  await guestLink.open();await assert.rejects(()=>guestLink.verifyPeer(key,'password'),/rejected the admin login/);assert(!guest.frames.some(f=>f[0]===2));await guestLink.close();
  const sensor=new Port();sensor.targetType=4;const sensorLink=new CompanionTransport(sensor,{openSettle:0,commandTimeout:1000});
  await sensorLink.open();await sensorLink.contacts();sensorLink.selectTarget(key);await sensorLink.verifyPeer(key,'password');
  assert(sensor.frames.some(f=>f[0]===52&&f[1]===1&&f[34]===4));await sensorLink.close();
  // The guarded investigation retries only unanswered reads, rearming its
  // mailbox with a new tag each time. A successful answer stops the attempts.
  const retry=new Port();retry.timeoutFirst={'get public.key':2,'ota status':1};const events=[];
  const retryLink=new CompanionTransport(retry,{openSettle:0,commandTimeout:1000,remoteTimeout:120,investigationTimeout:120,onInvestigationProgress:e=>events.push(e)});
  await retryLink.open();await retryLink.contacts();retryLink.selectTarget(key);
  await retryLink.investigateVerifyPeer(key,'');
  assert.equal(retry.remoteAttempts['get public.key'],3);
  assert.equal(await retryLink.investigateCommand(key,'','ota status'),'> 9');
  assert.equal(retry.remoteAttempts['ota status'],2);
  const sent=retry.frames.filter(f=>f[0]===2).map(f=>dec.decode(f.slice(13)));
  assert.equal(new Set(sent.map(s=>s.slice(0,3))).size,5);
  assert(events.some(e=>e.stage==='retrying-command'&&e.command==='get public.key'&&e.attempt===2&&e.maxAttempts===3));
  const before=sent.length;
  await assert.rejects(()=>retryLink.investigateCommand(key,'','set tx 9'),/read.only/i);
  assert.equal(retry.frames.filter(f=>f[0]===2).length,before);
  let calls=0;retryLink.remoteReply=async()=>{calls++;throw Error('Serial connection lost');};
  await assert.rejects(()=>retryLink.investigateCommand(key,'','ota self'),/Serial connection lost/);assert.equal(calls,1);
  calls=0;retryLink.remoteReply=async()=>{calls++;retryLink.investigationCancelled=true;const e=Error('Remote command timed out; result is unknown');e.loraReplyTimeout=true;throw e;};
  await assert.rejects(()=>retryLink.investigateCommand(key,'','ota self'),/cancel/i);assert.equal(calls,1);
  await retryLink.close();
  const defaults=new Port(), defaultEvents=[];
  const defaultLink=new CompanionTransport(defaults,{openSettle:0,commandTimeout:1000,onInvestigationProgress:e=>defaultEvents.push(e)});
  await defaultLink.open();await defaultLink.contacts();defaultLink.selectTarget(key);
  await defaultLink.investigateVerifyPeer(key,'');
  assert(defaultEvents.some(e=>e.stage==='waiting-reply'&&e.command==='get public.key'&&e.timeoutMs===20000));
  defaultEvents.length=0;
  await defaultLink.remoteCommand(key,'','ota status');
  assert(defaultEvents.some(e=>e.stage==='waiting-reply'&&e.command==='ota status'&&e.timeoutMs===75000));
  await defaultLink.close();
}
run().then(()=>console.log('Companion transport tests passed')).catch(e=>{console.error(e);process.exitCode=1;});
