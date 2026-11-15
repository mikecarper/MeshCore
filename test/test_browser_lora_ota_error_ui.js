'use strict';
// Actual UI wiring in an isolated DOM stub; no browser, USB or radio access.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const checks=require('../docs/_javascript/lora_ota_checks.js');
const source=fs.readFileSync(require.resolve('../docs/_javascript/lora_ota_browser.js'),'utf8');
function workbench(){
  const elements=new Map();
  const get=name=>{
    if(!elements.has(name)){const listeners=new Map();elements.set(name,{value:'',textContent:'',hidden:true,disabled:false,checked:false,dataset:{},files:[],options:[],tagName:'DIV',parentElement:null,addEventListener(type,fn){listeners.set(type,fn);},emit(type){return listeners.get(type)?.();},querySelectorAll:()=>[],replaceChildren(){},scrollIntoView(){}});}
    return elements.get(name);
  };
  const ui={querySelector:selector=>get(/data-role="([^"]+)"/.exec(selector)[1]),querySelectorAll:()=>[],addEventListener(){},dispatchEvent(){}};
  for(const name of ['diagnostics','investigation-details'])get(name).tagName='DETAILS';
  get('source').value='companion';get('radio-region').value='auto';
  const context=vm.createContext({document:{querySelector:()=>ui},navigator:{serial:{}},LoraOtaChecks:checks,LoraOtaRadioSetup:{regions:[]},LoraOtaCompanion:{preservedMessages:()=>[],preservedSnapshot:()=>({messages:[],token:''})},console,setTimeout,clearTimeout,TextEncoder,TextDecoder,URL,URLSearchParams,Uint8Array,Date,CustomEvent:class{constructor(type,init){this.type=type;this.detail=init?.detail;}}});
  const instrumented=source.replace(/\}\)\(\);\s*$/,'globalThis.failureUi={fail,run,onInvestigationProgress,failurePath,failureRadio,setLink(value){link=value;},setContacts(value){contacts=value;},setInvestigation(value){investigationActive=value;},setStep(value){operationErrorStep=value;},setPhase(value){updatePhase=value;}};})();');
  assert.notEqual(instrumented,source);vm.runInContext(instrumented,context);
  return {get,api:context.failureUi};
}
test('main error and diagnostic log show identity command, direct path and Radio 2 without changing the error',()=>{
  const ui=workbench(),error=Object.assign(Error('Remote command timed out; result is unknown'),{otaStep:'Verifying target identity',otaCommand:'get public.key',otaEndpoint:'remote target',otaPath:'direct (0 hops; enforced by guarded session)',otaRadio:'Radio 2 (selected source OTA profile)',otaTimeoutMs:7000,otaAttempt:7,otaMaxAttempts:7,loraReplyTimeout:true});
  ui.api.setLink({otaSupported:true,otaRadioProfile:0});ui.api.setStep('Find matching firmware');ui.api.fail(error);
  const message=ui.get('error-details').textContent;
  for(const expected of ['Find matching firmware','Verifying target identity','get public.key','remote target','0 hops','Radio 2','7','result is unknown'])assert.ok(message.includes(expected),message);
  assert.ok(ui.get('status').textContent.includes(message));assert.equal(ui.get('workflow-status').dataset.state,'error');assert.equal(ui.get('diagnostics').open,true);
  assert.equal(error.message,'Remote command timed out; result is unknown');assert.equal(error.loraReplyTimeout,true);
});
test('USB failure retains command and parent radio context beside reconnect instructions',()=>{
  const ui=workbench(),error=Object.assign(Error('USB command timed out'),{otaStep:'Reading source reply mailbox',otaCommand:'get ota.direct.reply 0',otaEndpoint:'USB Companion',otaRadio:'USB/local (no LoRa transmission)',otaRequestCommand:'get public.key',otaRequestStep:'Verifying target identity',otaRequestRadio:'Radio 2 (selected source OTA profile)'});
  ui.api.setLink({failure:error});ui.api.fail(error,'Find matching firmware');
  const message=ui.get('error-details').textContent;
  assert.match(message,/get ota\.direct\.reply 0/);assert.match(message,/get public\.key/);assert.match(message,/Radio 2/);assert.match(ui.get('workflow-status').textContent,/Reconnect USB source/);assert.equal(ui.get('reconnect').hidden,false);
});
test('each action resets context and validation failures cannot inherit a previous remote command',async()=>{
  const ui=workbench();ui.api.setLink({otaSupported:true,otaRadioProfile:0});
  await ui.api.run(async()=>{throw Object.assign(Error('No reply'),{otaCommand:'get public.key',otaStep:'Verifying target identity'});},'Find matching firmware');
  await ui.api.run(async()=>{throw Error('Firmware checksum is invalid');},'Validate selected firmware');
  const message=ui.get('error-details').textContent;
  assert.match(message,/Validate selected firmware/);assert.match(message,/Firmware checksum is invalid/);assert.doesNotMatch(message,/get public\.key|Verifying target identity/);
});
test('known hop counts are labelled stored or selected rather than claimed as a verified direct path',()=>{
  const ui=workbench(),key='bb'.repeat(32);ui.get('key').value=key;
  ui.api.setLink({otaSupported:false});ui.api.setContacts([{publicKey:key,hops:3}]);ui.api.fail(Error('No target reply'),'Check target');
  assert.match(ui.get('error-details').textContent,/Stored contact route: 3 hops/);
  ui.api.setLink({relays:['aa','bb']});ui.api.fail(Error('No target reply'),'Check route');
  assert.match(ui.get('error-details').textContent,/2 relay hops/);assert.match(ui.get('error-details').textContent,/not confirmed/);
  ui.api.setLink({});ui.api.setContacts([]);ui.api.fail(Error('No target reply'),'Check target');
  assert.match(ui.get('error-details').textContent,/Unknown/);assert.doesNotMatch(ui.get('error-details').textContent,/Direct \(0 hops/);
});
test('error display and logs redact the entered password and unsafe command arguments',()=>{
  const ui=workbench(),secret='PrivateAdminValue';ui.get('password').value=secret;
  ui.api.fail(Object.assign(Error('Login rejected for '+secret),{otaCommand:'login '+secret,otaStep:'Logging in to target'}),'Find matching firmware');
  const displayed=ui.get('workflow-status').textContent+' '+ui.get('status').textContent+' '+ui.get('error-details').textContent;
  assert.doesNotMatch(displayed,new RegExp(secret));assert.match(displayed,/login|Logging in/);
});
test('verbose checkbox is off by default, opens for a halting error and can hide the details again',()=>{
  const ui=workbench();assert.equal(ui.get('verbose-errors').checked,false);assert.equal(ui.get('verbose-error-panel').hidden,true);
  ui.api.fail(Object.assign(Error('No target reply'),{otaCommand:'ota status'}),'Check OTA support');
  assert.equal(ui.get('verbose-errors').checked,true);assert.equal(ui.get('verbose-error-panel').hidden,false);assert.match(ui.get('error-details').textContent,/ota status/);
  assert.doesNotMatch(ui.get('workflow-status').textContent,/Path:|Radio:|Command:/);
  ui.get('verbose-errors').checked=false;ui.get('verbose-errors').emit('change');assert.equal(ui.get('verbose-error-panel').hidden,true);assert.equal(ui.get('diagnostics').open,false);
  ui.api.fail(Error('Another failure'),'Validate firmware');assert.equal(ui.get('verbose-errors').checked,true);assert.equal(ui.get('verbose-error-panel').hidden,false);
});
test('local preparation failures keep actionable text visible and technical details collapsed',async()=>{
  const ui=workbench(),calls=[];
  ui.api.setLink({running:true,sourceKey:'aa'.repeat(32),diagnosticRead:async command=>{calls.push(command);}});
  await ui.api.fail(Object.assign(Error('Needs 840 KiB; 796 KiB is available. No firmware was uploaded.'),
    {code:'OTA_DELTA_SPACE',otaLocalCheck:true,otaEndpoint:'Browser',otaStep:'Build differential package'}),'Prepare update');
  assert.match(ui.get('workflow-status').textContent,/Needs 840 KiB; 796 KiB/);
  assert.match(ui.get('workflow-status').textContent,/No firmware was uploaded/);
  assert.match(ui.get('error-details').textContent,/Command: none \(local browser calculation\)/);
  assert.equal(ui.get('verbose-errors').checked,false);assert.equal(ui.get('verbose-error-panel').hidden,true);
  assert.deepEqual(calls,[]);
});
test('halting failures collect target ver and board while retaining cached source identity and original failure',async()=>{
  const ui=workbench(),key='bb'.repeat(32),calls=[],error=Object.assign(Error('Remote command timed out; result is unknown'),{otaCommand:'ota self',otaRadio:'Radio 2 (selected source OTA profile)',loraReplyTimeout:true});
  const link={running:true,sourceKey:'aa'.repeat(32),firmwareVersion:'v1.18.0.2-source',firmwareBoardName:'Source board',otaSupported:true,otaRadioProfile:1,diagnosticRead:async(target,password,command,options)=>{assert.equal(target,key);assert.equal(password,'');assert.equal(options.timeoutMs,5000);calls.push(command);link.otaRadioProfile=0;return command==='ver'?'v1.18.0.2-target':'Target board';}};
  ui.get('key').value=key;ui.api.setLink(link);await ui.api.fail(error,'Prepare update');
  assert.deepEqual(calls,['ver','board']);const message=ui.get('error-details').textContent;
  for(const text of ['ota self','result is unknown','v1.18.0.2-source','Source board','v1.18.0.2-target','Target board','Radio 2'])assert.ok(message.includes(text),message);
  assert.equal(error.message,'Remote command timed out; result is unknown');assert.equal(error.loraReplyTimeout,true);assert.equal(error.otaIdentity,undefined);
});
test('post-failure reads keep the action reserved and cannot overlap another operation',async()=>{
  const ui=workbench(),calls=[];let release;
  ui.get('key').value='bb'.repeat(32);ui.api.setLink({running:true,sourceKey:'aa'.repeat(32),diagnosticRead:async(_key,_password,command)=>{calls.push(command);return command==='ver'?new Promise(resolve=>{release=resolve;}):'Target board';}});
  const action=ui.api.run(async()=>{throw Error('Original preparation failure');},'Prepare update');
  await new Promise(resolve=>setImmediate(resolve));assert.equal(typeof release,'function');let secondRan=false;
  const second=ui.api.run(async()=>{secondRan=true;},'Other action');assert.equal(second,action);assert.equal(secondRan,false);
  release('v1.18.0.2-target');await action;assert.deepEqual(calls,['ver','board']);assert.equal(secondRan,false);
});
test('a disconnect during failure diagnostics discards late identity and sends no next command',async()=>{
  const ui=workbench(),calls=[];let release;
  ui.get('key').value='bb'.repeat(32);ui.api.setLink({running:true,sourceKey:'aa'.repeat(32),diagnosticRead:async(_key,_password,command)=>{calls.push(command);return new Promise(resolve=>{release=resolve;});}});
  const pending=ui.api.fail(Error('Original failure'),'Prepare update');assert.equal(typeof release,'function');ui.api.setLink(null);release('LATE_PRIVATE_VERSION');await pending;
  assert.deepEqual(calls,['ver']);assert.doesNotMatch(ui.get('error-details').textContent,/LATE_PRIVATE_VERSION/);
});
test('cached target version and board appear on failure without any new LoRa request',async()=>{
  const ui=workbench(),key='bb'.repeat(32),calls=[];ui.get('key').value=key;
  ui.api.setLink({running:true,sourceKey:'aa'.repeat(32),cachedFailureIdentity:target=>{assert.equal(target,key);return {version:'v1.18.0.2-known',board:'Known target board'};},diagnosticRead:async(...args)=>{calls.push(args);throw Error('Unexpected LoRa query');}});
  await ui.api.fail(Object.assign(Error('Original ota self failure'),{otaCommand:'ota self'}),'Prepare update');
  assert.deepEqual(calls,[]);assert.match(ui.get('error-details').textContent,/v1\.18\.0\.2-known/);assert.match(ui.get('error-details').textContent,/Known target board/);assert.match(ui.get('error-details').textContent,/cached/i);
});
