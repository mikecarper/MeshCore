const test=require('node:test');
const assert=require('node:assert/strict');
const prefs=require('../docs/_javascript/lora_ota_preferences.js');

function fixture(initial={}){
  const fields={};
  for(const [role,value] of Object.entries({connection:'usb',source:'auto','wifi-url':'',key:'',
    acl:false,'quiet-forwarding':true,'auto-search':true,'auto-objective':'steps','auto-depth':'standard',
    relays:'',candidates:'',snr:'-5',age:'24',...initial})){
    fields[role]={value:typeof value==='boolean'?'':value,checked:typeof value==='boolean'?value:false,
      listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},emit(name){this.listeners[name]?.();}};
  }
  return {fields,querySelector(selector){return fields[/data-role="([^"]+)"/.exec(selector)?.[1]]||null;}};
}
function storage(raw=null){return {raw,getItem(key){assert.equal(key,prefs.KEY);return this.raw;},
  setItem(key,value){assert.equal(key,prefs.KEY);this.raw=value;}};}

test('saves and restores valid nonsecret choices and no password',()=>{
  const saved=storage(),first=fixture();const controller=prefs.install(first,{storage:saved});
  first.fields.connection.value='wifi';first.fields['wifi-url'].value='192.168.1.96';
  first.fields.source.value='companion';first.fields.key.value='ab'.repeat(32);
  first.fields.acl.checked=true;first.fields['quiet-forwarding'].checked=false;
  first.fields['auto-objective'].value='data';controller.save();
  assert.deepEqual(Object.keys(JSON.parse(saved.raw)).sort(),['connection','source','wifi-url','key','acl',
    'quiet-forwarding','auto-search','auto-objective','auto-depth','relays','candidates','snr','age','radio2-preamble'].sort());
  const second=fixture();prefs.install(second,{storage:saved});
  assert.equal(second.fields.connection.value,'wifi');assert.equal(second.fields['wifi-url'].value,'192.168.1.96');
  assert.equal(second.fields.key.value,'ab'.repeat(32));assert.equal(second.fields.acl.checked,true);
  assert.equal(second.fields['quiet-forwarding'].checked,false);assert.equal(second.fields['auto-objective'].value,'data');
});
test('explicit link choice wins while other choices restore',()=>{
  const saved=storage(JSON.stringify({connection:'wifi',key:'CD'.repeat(32),'wifi-url':'10.0.0.3'}));
  const ui=fixture({connection:'bluetooth'});prefs.install(ui,{storage:saved,ignoreSavedConnection:true});
  assert.equal(ui.fields.connection.value,'bluetooth');assert.equal(ui.fields.key.value,'cd'.repeat(32));
  assert.equal(ui.fields['wifi-url'].value,'10.0.0.3');
});
test('invalid stored values, addresses containing credentials, and partial keys are ignored',()=>{
  const saved=storage(JSON.stringify({connection:'anything',source:'repeater',key:'ABCD','wifi-url':'ws://user:secret@192.168.1.1:5005/ota',acl:'true'}));
  const ui=fixture();const controller=prefs.install(ui,{storage:saved});
  assert.equal(ui.fields.connection.value,'usb');assert.equal(ui.fields.source.value,'repeater');
  assert.equal(ui.fields.key.value,'');assert.equal(ui.fields['wifi-url'].value,'');assert.equal(ui.fields.acl.checked,false);
  ui.fields['wifi-url'].value='ws://user:secret@192.168.1.1:5005/ota';ui.fields.key.value='abcd';controller.save();
  assert.equal(JSON.parse(saved.raw)['wifi-url'],'');assert.equal(JSON.parse(saved.raw).key,'');
  assert.ok(!saved.raw.includes('secret'));
});
test('field changes persist, programmatic target selection can be saved, and blocked storage is harmless',()=>{
  const saved=storage(),ui=fixture(),controller=prefs.install(ui,{storage:saved});
  ui.fields.connection.value='bluetooth';ui.fields.connection.emit('change');
  assert.equal(JSON.parse(saved.raw).connection,'bluetooth');
  controller.save({targetKey:'ff'.repeat(32)});assert.equal(JSON.parse(saved.raw).key,'ff'.repeat(32));
  assert.doesNotThrow(()=>prefs.install(fixture(),{storage:{getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}}}).save());
});
test('restores validated manual radio fields only after available region options exist',()=>{
  const initial={'radio-region':'auto','radio-frequency':'915.1','radio-bandwidth':'250','radio-sf':'7','radio-cr':'5',
    'radio-preamble':'128','radio2-preamble':'368','radio-minutes':'120','radio-reset':''};
  const saved=storage(),first=fixture(initial);first.fields['radio-region'].options=[{value:'auto'},{value:'us'}];
  const writer=prefs.install(first,{storage:saved});first.fields['radio-region'].value='us';
  first.fields['radio-frequency'].emit('input');
  const second=fixture({...initial,'radio-frequency':'','radio-sf':'9'});
  second.fields['radio-region'].options=[{value:'auto'},{value:'us'}];
  const reader=prefs.install(second,{storage:saved});assert.equal(second.fields['radio-frequency'].value,'');
  assert.equal(reader.restoreRadio(),true);assert.equal(second.fields['radio-region'].value,'us');
  assert.equal(second.fields['radio-frequency'].value,'915.1');assert.equal(second.fields['radio-sf'].value,'7');
  first.fields['radio-reset'].emit('click');assert.equal(JSON.parse(saved.raw).radioManual,undefined);
  const corrupt=JSON.parse(saved.raw);corrupt.radioManual=true;corrupt.radio={...initial,'radio-frequency':'9000'};
  saved.raw=JSON.stringify(corrupt);assert.equal(prefs.install(fixture(initial),{storage:saved}).restoreRadio(),false);
});
test('retains selected contact during unrelated edits while allowing the key to be cleared',()=>{
  const saved=storage(),ui=fixture();let selected='af'.repeat(32);
  prefs.install(ui,{storage:saved,getTargetKey:()=>selected||ui.fields.key.value});
  ui.fields.connection.value='bluetooth';ui.fields.connection.emit('change');
  assert.equal(JSON.parse(saved.raw).key,selected);
  ui.fields.key.value='';ui.fields.key.emit('input');selected='';
  assert.equal(JSON.parse(saved.raw).key,'');
});
test('restores bounded valid relay choices and independent radio2 preamble',()=>{
  const saved=storage(),initial={'radio2-preamble':'368'};
  const first=fixture(initial);prefs.install(first,{storage:saved});
  first.fields.relays.value='AA'.repeat(32)+', '+ 'bb'.repeat(32);first.fields.relays.emit('input');
  first.fields.candidates.value='cc'.repeat(32);first.fields.candidates.emit('input');
  first.fields.snr.value='-12.25';first.fields.snr.emit('input');
  first.fields.age.value='4.5';first.fields.age.emit('input');
  first.fields['radio2-preamble'].value='512';first.fields['radio2-preamble'].emit('input');
  const second=fixture(initial);const reader=prefs.install(second,{storage:saved});
  assert.equal(second.fields.relays.value,'aa'.repeat(32)+'\n'+'bb'.repeat(32));
  assert.equal(second.fields.candidates.value,'cc'.repeat(32));
  assert.equal(second.fields.snr.value,'-12.25');assert.equal(second.fields.age.value,'4.5');
  assert.equal(reader.restoreRadio(),false);assert.equal(second.fields['radio2-preamble'].value,'512');
  first.fields.relays.value=Array(9).fill('dd'.repeat(32)).join('\n');first.fields.relays.emit('input');
  assert.equal(JSON.parse(saved.raw).relays,'');
  first.fields.candidates.value='cc'.repeat(32)+' bad';first.fields.candidates.emit('input');
  assert.equal(JSON.parse(saved.raw).candidates,'');
  first.fields['radio2-preamble'].value='65536';first.fields['radio2-preamble'].emit('input');
  assert.equal(JSON.parse(saved.raw)['radio2-preamble'],'');
});
