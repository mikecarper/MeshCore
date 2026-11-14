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
