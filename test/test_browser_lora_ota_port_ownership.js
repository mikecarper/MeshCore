'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {CompanionTransport}=require('../docs/_javascript/lora_ota_companion.js');
const {RepeaterTransport}=require('../docs/_javascript/lora_ota_serial.js');

for(const [name,Transport] of [['Companion',CompanionTransport],['Repeater',RepeaterTransport]]){
  test(`${name} retries cleanup if cancellation races a successful open`,async()=>{
    const controller=new AbortController();let finishOpen,closeCalls=0;
    const port={open:()=>new Promise(resolve=>{finishOpen=resolve;}),close:async()=>{if(++closeCalls===1)throw Error('temporary close failure');}};
    const link=new Transport(port,{openSignal:controller.signal,openRetryDelayMs:0});
    const opening=link.open();
    assert.equal(typeof finishOpen,'function');controller.abort();finishOpen();
    await assert.rejects(opening,{name:'AbortError'});
    assert.equal(closeCalls,2);assert.equal(link.portOpened,false);
  });

  test(`${name} retains ownership when repeated cleanup fails`,async()=>{
    const controller=new AbortController();let finishOpen,closeCalls=0;
    const port={open:()=>new Promise(resolve=>{finishOpen=resolve;}),close:async()=>{if(++closeCalls<=2)throw Error('temporary close failure');}};
    const link=new Transport(port,{openSignal:controller.signal,openRetryDelayMs:0});
    const opening=link.open();controller.abort();finishOpen();
    await assert.rejects(opening,{name:'AbortError'});
    assert.equal(closeCalls,2);assert.equal(link.portOpened,true);
    await link.close();assert.equal(closeCalls,3);assert.equal(link.portOpened,false);
  });

  test(`${name} closes only once if canceled open cleans up successfully`,async()=>{
    const controller=new AbortController();let finishOpen,closeCalls=0;
    const port={open:()=>new Promise(resolve=>{finishOpen=resolve;}),close:async()=>{closeCalls++;}};
    const link=new Transport(port,{openSignal:controller.signal,openRetryDelayMs:0});
    const opening=link.open();controller.abort();finishOpen();
    await assert.rejects(opening,{name:'AbortError'});
    assert.equal(closeCalls,1);assert.equal(link.portOpened,false);
  });
}
