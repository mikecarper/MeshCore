'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {requestSerialPort}=require('../docs/_javascript/lora_ota_port.js');

test('one previously authorized radio skips the chooser without opening it',async()=>{
  const port={open(){throw Error('Selection must not open the radio');}};
  const serial={getPorts:async()=>[port],requestPort:async()=>{throw Error('Unexpected chooser');}};
  assert.equal(await requestSerialPort(serial),port);
});
for(const ports of [[],[{},{}]])test(`${ports.length} authorized radios require an explicit selection`,async()=>{
  const selected={};let choices=0;
  assert.equal(await requestSerialPort({getPorts:async()=>ports,requestPort:async()=>{choices++;return selected;}}),selected);
  assert.equal(choices,1);
});
test('older serial adapters without getPorts retain the chooser',async()=>{
  const selected={};
  assert.equal(await requestSerialPort({requestPort:async()=>selected}),selected);
});
test('canceling while authorized ports are queried never opens a chooser',async()=>{
  const controller=new AbortController();let finish;
  const selection=requestSerialPort({getPorts:()=>new Promise(resolve=>{finish=resolve;}),requestPort:async()=>{throw Error('Unexpected chooser');}},{signal:controller.signal});
  controller.abort();finish([]);
  await assert.rejects(selection,{name:'AbortError'});
});
test('query and chooser failures are not retried',async()=>{
  const queryFailure=Error('permission unavailable'),chooserFailure=Error('user canceled');
  await assert.rejects(requestSerialPort({getPorts:async()=>{throw queryFailure;},requestPort:async()=>{throw Error('Unexpected chooser');}}),queryFailure);
  await assert.rejects(requestSerialPort({getPorts:async()=>[],requestPort:async()=>{throw chooserFailure;}}),chooserFailure);
});
