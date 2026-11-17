const test=require('node:test');
const assert=require('node:assert/strict');
const {CompanionTransport}=require('../docs/_javascript/lora_ota_companion.js');
const {Source,wrap}=require('../docs/_javascript/lora_ota_companion_source.js');
const mota=require('../docs/_javascript/lora_ota_package.js');
const crypto=require('node:crypto');

const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const request=(block,mask=0xefff)=>Uint8Array.of(0x31,0,6,1,2,3,4,block,0,mask&255,mask>>8);
const log=packet=>Uint8Array.of(0x88,0,0,...packet);

test('coalesces only pending identical data requests and retains a retry after loss',async()=>{
  const source=new CompanionTransport({},{});source.serving=true;
  const received=[],sent=[];let release;
  const blocked=new Promise(resolve=>{release=resolve;});
  source.seeder={replies:async packet=>{received.push([...packet]);return [Uint8Array.of(packet[7])];}};
  source.sendRadio=async frame=>{sent.push(frame[0]);if(sent.length===1)await blocked;};
  const first=request(3),differentMask=request(3,0xe003),next=request(4);
  source.onFrame(log(first));await tick();
  // The first packet is still in flight. Preserve one new identical request,
  // which may represent loss, but not another copy of that same queued retry.
  source.onFrame(log(first));source.onFrame(log(first));
  source.onFrame(log(differentMask));source.onFrame(log(next));
  assert.equal(source.radioQueue.length,3);
  assert.deepEqual([...source.radioQueue[0]],[...first]);
  assert.deepEqual([...source.radioQueue[1]],[...differentMask]);
  assert.deepEqual([...source.radioQueue[2]],[...next]);
  release();while(source.radioRunning)await tick();
  assert.equal(received.length,4);assert.deepEqual(sent,[3,3,3,4]);
  // A retry arriving after both bursts is real loss evidence and must run.
  source.onFrame(log(first));while(source.radioRunning)await tick();
  assert.equal(received.length,5);assert.equal(sent.length,5);
});

test('does not coalesce malformed data or other OTA messages',async()=>{
  const source=new CompanionTransport({},{});source.serving=true;source.radioRunning=true;
  for(const packet of [Uint8Array.of(0x31,0,6,1,2,3),Uint8Array.of(0x31,0,8,1,2,3,4,0,0)]){
    source.onFrame(log(packet));source.onFrame(log(packet));
  }
  assert.equal(source.radioQueue.length,4);
});

test('a retry storm yields fewer actual DATA/proof frames without losing a later repair',async()=>{
  // A complete, verified 2 KiB-block mOTA package and the real browser source
  // produce the same wire frames that the transport would transmit over LoRa.
  const body=Buffer.alloc(2301);for(let i=0;i<body.length;i++)body[i]=(i*13)&255;
  const end=Buffer.alloc(56);end.write('EndF');end.writeUInt32LE(body.length,4);
  crypto.createHash('sha256').update(body).digest().copy(end,8,0,8);
  end.writeUInt32LE(0x01120002,16);end.writeUInt32LE(0x12345678,20);end.write('test_board',24);
  const info=await mota.parseMota(await mota.buildFullMota(Buffer.concat([body,end]),{blockSize:2048}));
  const makeRequest=(mid,mask)=>wrap(Uint8Array.of(6,...mid,0,0,mask&255,mask>>8));
  async function simulation(coalesce){
    const transport=new CompanionTransport({},{}),seeder=await Source.create(info,'aa'.repeat(32));
    transport.serving=true;transport.seeder=seeder;
    // Reproduce the prior FIFO admission as the control. Both paths use the
    // actual source packet generator and the same mocked receiver/loss pattern.
    if(!coalesce)transport.onFrame=function(frame){
      if(frame[0]===0x88&&frame.length>5&&this.serving){if(this.radioQueue.length<64){this.radioQueue.push(frame.slice(3));this.pumpRadio();}}
    };
    let release;const firstSent=new Promise(resolve=>{release=resolve;});let hold;
    const held=new Promise(resolve=>{hold=resolve;});
    const received=new Set(),frames=[];let dropped=false;
    transport.sendRadio=async frame=>{
      frames.push(frame);
      if(frame[2]===7){
        const packed=frame[9]|frame[10]<<8,index=(packed>>11)&15;
        if(index===3&&!dropped)dropped=true;else received.add(index);
      }
      if(frames.length===1){release();await held;}
    };
    const full=makeRequest(seeder.mid,0xafff);
    transport.onFrame(log(full));await firstSent;
    // Nine duplicate requests arrive while the initial response is busy.
    for(let i=0;i<9;i++)transport.onFrame(log(full));
    hold();while(transport.radioRunning)await tick();
    const stormFrames=frames.length;
    assert.equal(dropped,true);assert.equal(received.size,12,'a queued retry repairs the intentionally lost fragment');
    // A new missing-fragment report after the queue drains must still be sent.
    received.delete(3);transport.onFrame(log(makeRequest(seeder.mid,0xa008)));
    while(transport.radioRunning)await tick();
    assert.equal(received.has(3),true,'the later selective retry repairs the hole');
    assert.equal(frames.length-stormFrames,2,'one DATA fragment plus proof');
    return stormFrames;
  }
  const baseline=await simulation(false),optimized=await simulation(true);
  assert.equal(baseline,130);assert.equal(optimized,26);
});
