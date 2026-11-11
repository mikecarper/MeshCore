'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../docs/_javascript/lora_ota_chain_ui.js');
const key='bb'.repeat(32), radio='910.525,62.5,7,5,32';
function fixture({scheduled=false,active=false,lost=false,download=false,unsupported=false}={}){
  const commands=[];let target=active?radio:'off';
  const link={sourceKey:'aa'.repeat(32),async verifyPeer(k,p){assert.equal(k,key);assert.equal(p,'');},
    async localCommand(cmd){commands.push('local '+cmd);if(cmd==='get tempradio')return radio;throw Error('unexpected '+cmd)},
    async remoteCommand(k,p,cmd){assert.equal(k,key);commands.push(cmd);
      if(cmd==='ota status')return 'OTA hw=board | target:12345678 | maxblk:1024 | '+(download?'download: fetching id=12345678':'no download');
      if(cmd==='ota self')return 'base_hash=1122334455667788 | maxblk:1024';
      if(cmd==='ver')return 'v1.17.1.9';if(cmd==='get bootloader.ver')return 'Error: unsupported';
      if(cmd==='get tempradio')return target;if(cmd==='get radio')return radio;
      if(cmd==='get tempradioat')return scheduled?'slots: 1':'slots:';
      if(cmd==='get radioat')return '-none-';
      if(cmd.startsWith('tempradio.ifidle ')){if(unsupported)return 'unknown command';target=radio;if(lost)throw Error('timeout');return 'OK - temp params';}
      throw Error('unexpected '+cmd);
    }};
  return {link,commands};
}
test('image evidence requires OTA receiver markers and matching nRF layout',()=>{
  const encoder=new TextEncoder(), marker=encoder.encode('OTA: status | stats | ls | get <id> flash | install');
  const image=new Uint8Array(300), base=new Uint8Array(300);image.set(marker);base.set(marker);
  const live={nrf:false,codecs:[0,1],external:false,sd:false,maxBlock:2048};
  assert.equal(ui.evidence(image,live,base).maxBlock,1024);
  assert.throws(()=>ui.evidence(new Uint8Array(300),live,base),/marker/);
  assert.throws(()=>ui.evidence(image,{...live,nrf:true},base),/in-place/);
});
test('preamble requires an explicit current or legacy value',()=>{
  assert.equal(ui.preamble('> 910.525,62.5,7,5,32'),32);
  assert.equal(ui.preamble('TempRadio active: 910.525024,62.5,7,5 14000s left,preamble=16'),16);
  for(const value of ['> 910.525,62.5,7,5','TempRadio active: 910.525,62.5,7,5 300s left,preamble=',
    'TempRadio active: 910.525,62.5,7,5 300s left,preamble=auto','> 910.525,62.5,7,5,0'])
    assert.throws(()=>ui.preamble(value),/preamble/);
});
test('reads authenticated live firmware and conservative radio capabilities',async()=>{
  const f=fixture(), live=await ui.readLive(f.link,key,'');
  assert.equal(live.targetId,0x12345678);assert.equal(live.bodyHash,'1122334455667788');
  assert.deepEqual(live.codecs,[0,1]);assert.equal(live.wireProfile,'legacy');
});
test('rearm confirms idle state and sends once, including a lost acknowledgement',async()=>{
  for(const lost of [false,true]){
    const f=fixture({lost});assert.equal(await ui.rearm(f.link,key,'',[910.525,62.5,7,5],30,{wait:async()=>{},preamble:32}),true);
    assert.equal(f.commands.filter(c=>c.startsWith('tempradio.ifidle ')).length,1);
    assert.ok(f.commands.includes('tempradio.ifidle 910.525,62.5,7,5,30,32'));
    assert.ok(f.commands.indexOf('get tempradioat')<f.commands.findIndex(c=>c.startsWith('tempradio.ifidle ')));
    assert.ok(f.commands.indexOf('get radioat')<f.commands.findIndex(c=>c.startsWith('tempradio.ifidle ')));
  }
});
test('existing scheduled, active or download state refuses rearm without mutation',async()=>{
  for(const options of [{scheduled:true},{active:true},{download:true}]){
    const f=fixture(options);
    await assert.rejects(ui.rearm(f.link,key,'',[910.525,62.5,7,5],30,{wait:async()=>{},preamble:32}));
    assert.equal(f.commands.filter(c=>c.startsWith('tempradio.ifidle ')).length,0);
  }
});
test('old firmware never falls back to an unconditional radio change',async()=>{
  const f=fixture({unsupported:true});
  await assert.rejects(ui.rearm(f.link,key,'',[910.525,62.5,7,5],30,{wait:async()=>{},preamble:32}),/manually/);
  assert.equal(f.commands.filter(c=>c.startsWith('tempradio.ifidle ')).length,1);
  assert.equal(f.commands.filter(c=>c.startsWith('tempradio ')).length,0);
});
