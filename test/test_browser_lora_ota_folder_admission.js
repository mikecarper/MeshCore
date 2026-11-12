'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {CompanionTransport} = require('../docs/_javascript/lora_ota_companion.js');

function fixture(replies) {
  const events=[], port={}, link=new CompanionTransport(port, {onFolderAdmission:e=>events.push(e)});
  let attached=false;
  link.usbFolderSource={
    async start(){attached=true;return replies.shift();},
    async stop(){attached=false;},
    ownsStream(){return attached;}
  };
  return {link,events,isAttached:()=>attached};
}

test('explicit host admission is reported without treating an omitted duplicate as impossible', async()=>{
  const f=fixture(['OK folder attached (serial): advertising 0/1 host mOTAs (some omitted: serve cap/invalid/duplicate)',
    'OK folder attached (serial): advertising 0/1 host mOTAs',
    'OK folder refreshed (serial): advertising 1/1 host mOTAs']);
  await f.link.startUsbFolder();
  assert.deepEqual(f.events,[{advertised:0,offered:1}]);
  assert.equal(f.isAttached(),true,'an omitted host entry may have a matching existing source');
  await f.link.startUsbFolder();
  assert.equal(f.events.length,1,'repeated handoffs do not spam the log');
  await f.link.startUsbFolder();
  assert.deepEqual(f.events[1],{advertised:1,offered:1});
  await f.link.stopUsbFolder();
  assert.equal(f.isAttached(),false);
});

test('older acknowledgements without host counts remain supported', async()=>{
  const f=fixture(['OK folder attached']);
  await f.link.startUsbFolder();
  assert.deepEqual(f.events,[]);
  assert.equal(f.isAttached(),true);
  await f.link.stopUsbFolder();
});

test('a failed attach does not report admission', async()=>{
  const f=fixture([]);
  f.link.usbFolderSource.start=async()=>{throw Error('USB handoff failed');};
  await assert.rejects(f.link.startUsbFolder(),/USB handoff failed/);
  assert.deepEqual(f.events,[]);
});
