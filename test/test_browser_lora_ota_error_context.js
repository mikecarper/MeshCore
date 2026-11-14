'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {errorDetails}=require('../docs/_javascript/lora_ota_checks.js');

test('remote failures name action, command, endpoint, path and bounded retry details',()=>{
  const error=Object.assign(Error('Remote command timed out; result is unknown'),{
    otaStep:'Verifying target identity',otaCommand:'get public.key',otaEndpoint:'remote target',
    otaPath:'Direct, 0 hops (guarded Companion)',otaTimeoutMs:7000,otaAttempt:7,otaMaxAttempts:7,
    loraReplyTimeout:true,attempts:7
  });
  const before=Object.getOwnPropertyDescriptors(error),message=error.message;
  Object.freeze(error);
  const output=errorDetails(error,{step:'Find matching firmware'});
  assert.match(output,/Step: Find matching firmware/);
  assert.match(output,/Stage: Verifying target identity/);
  assert.match(output,/Endpoint: remote target/);
  assert.match(output,/Path: Direct, 0 hops \(guarded Companion\)/);
  assert.match(output,/Command: get public\.key/);
  assert.match(output,/Timeout: 7 s/);assert.match(output,/Attempt: 7 of 7/);
  assert.match(output,/Reason: Remote command timed out; result is unknown/);
  assert.equal(error.message,message);assert.equal(error.loraReplyTimeout,true);assert.equal(error.attempts,7);
  for(const [key,descriptor] of Object.entries(before))assert.equal(Object.getOwnPropertyDescriptor(error,key).value,descriptor.value);
});

test('source mailbox failure retains the enclosing remote request without overwriting its reason',()=>{
  const output=errorDetails(Object.assign(Error('Companion command timed out; reconnect before retrying'),{
    otaStep:'Reading source reply mailbox',otaCommand:'get ota.direct.reply 40',otaEndpoint:'USB Companion',
    otaRequestStep:'Verifying target identity',otaRequestCommand:'get public.key',otaTimeoutMs:15000,
    otaPath:'Unknown (no route reported)'
  }),{step:'Find matching firmware'});
  assert.match(output,/Stage: Reading source reply mailbox/);assert.match(output,/Endpoint: USB Companion/);
  assert.match(output,/Command: get ota\.direct\.reply 40/);
  assert.match(output,/Request step: Verifying target identity/);assert.match(output,/Request command: get public\.key/);
  assert.match(output,/Path: Unknown \(no route reported\)/);
  assert.match(output,/Reason: Companion command timed out; reconnect before retrying/);
});

test('routed and unknown path labels remain descriptions, not invented route proof',()=>{
  for(const path of ['Reported contact path: 2 hops','Unknown (source did not report a route)']){
    const output=errorDetails(Object.assign(Error('No reply'),{otaPath:path,otaCommand:'ota status'}),{step:'Check target'});
    assert.ok(output.includes('Path: '+path));assert.doesNotMatch(output,/Path: Direct/);
  }
  assert.doesNotMatch(errorDetails(Error('No reply')),/Path:/);
});

test('captured Radio2 request context survives cleanup while USB and unknown radio scope remain distinct',()=>{
  const sourceState={radio:'Radio2 (temporary source profile)'},error={message:'Mailbox timed out',
    otaStep:'Read source mailbox',otaCommand:'get ota.direct.reply 0',otaRadio:'USB/local (no LoRa transmission)',
    otaRequestCommand:'ota status',otaRequestRadio:sourceState.radio};
  sourceState.radio='Primary radio after cleanup';
  const output=errorDetails(error,{step:'Check target'});
  assert.match(output,/Radio: USB\/local \(no LoRa transmission\)/);
  assert.match(output,/Request radio: Radio2 \(temporary source profile\)/);
  assert.doesNotMatch(output,/after cleanup/);
  assert.match(errorDetails({message:'Failed',otaRadio:'Unknown (no source profile reported)'}),/Radio: Unknown \(no source profile reported\)/);
  const secret='RADIO-SECRET',key='a1'.repeat(32),url='https://private.example/radio';
  const privateOutput=errorDetails({message:'Failed',otaRadio:secret+' '+key,otaRequestRadio:url+' '+secret},{secrets:[secret]});
  for(const value of [secret,key,url])assert.ok(!privateOutput.includes(value));
});

test('binary operation labels and safe read commands remain useful without arbitrary arguments',()=>{
  for(const command of ['reading the Companion clock','sending an admin login','USB binary command 26',
    'ver','get storage.layout','get public.key','get bootloader.ver','ota status','ota self','ota stats','ota config',
    'ota bootloader status','ota key list','ota installhash','ota qspi','ota ls 128','ota.peer timeout']){
    assert.ok(errorDetails({message:'Unavailable',otaCommand:command}).includes('Command: '+command),command);
  }
});

test('command values, keys, URLs and protocol tags never become new diagnostic arguments',()=>{
  const key='a1'.repeat(32),secret='DO-NOT-LOG-ME',url='https://private.example/firmware?token=private';
  for(const command of ['set wifi.password '+secret,'password '+secret,'login '+secret,
    'ota key add '+key,'ota pull 12345678 flash','ota installhash '+key,'ota.peer login '+key+' '+secret,
    'unknown '+url,'AB|get public.key','get name '+secret,'CLI command <arguments hidden>']){
    const output=errorDetails({message:'Command failed',otaCommand:command});
    assert.doesNotMatch(output,new RegExp(secret));assert.ok(!output.includes(key));assert.ok(!output.includes(url));assert.ok(!output.includes('AB|'));
    assert.ok(output.includes('<arguments hidden>')||output.includes('Command: not identified'),command);
  }
  assert.match(errorDetails({message:'Failed',otaCommand:'set wifi.password '+secret}),/Command: set wifi\.password <arguments hidden>/);
  assert.match(errorDetails({message:'Failed',otaCommand:'ota pull 12345678 flash'}),/Command: ota pull <arguments hidden>/);
});

test('exact secrets are redacted in every field before truncation, including overlapping values',()=>{
  const secret='START-'+('s'.repeat(180))+'-END';
  const error={message:'Target refused '+secret+'\nfailed (manifest timeout) 2/63 id=12345678',
    otaStep:'Working '+secret,otaCommand:'login '+secret,otaEndpoint:'USB '+secret,
    otaPath:'Route '+secret,otaRequestStep:'Request '+secret,otaRequestCommand:'password '+secret};
  const output=errorDetails(error,{step:'Action '+secret,secrets:[secret,'START-','password-test','password-test-long',null]});
  assert.ok(!output.includes('START-'));assert.ok(!output.includes('s'.repeat(20)));assert.ok(!output.includes('-END'));
  assert.match(output,/failed \(manifest timeout\) 2\/63 id=12345678/);
  assert.match(output,/<hidden>/);assert.equal(error.message.startsWith('Target refused '+secret),true);
  const overlap=errorDetails(Error('password-test-long'),{secrets:['password-test','password-test-long']});
  assert.ok(!overlap.includes('-long'));assert.ok(!overlap.includes('password-test'));
});

test('untrusted metadata is bounded ASCII and reason keeps useful failure progress',()=>{
  const key='b2'.repeat(32),url='https://private.example/?key='+key;
  const output=errorDetails({message:'Target OTA status: failed (storage error) 2/63 id=12345678\n'+url+' AB|get '+key,
    otaStep:'Check\r\nInjected: '+('x '.repeat(500))+'\u0000',otaEndpoint:'USB\u001b[31m Companion',
    otaPath:'Reported '+key+' '+url,otaRequestStep:'R\t\u2603',otaCommand:'get public.key\npassword secret'});
  assert.ok(!/[^\x20-\x7e\n]/.test(output));assert.ok(!output.includes(key));assert.ok(!output.includes(url));assert.ok(!output.includes('AB|'));
  assert.match(output,/failed \(storage error\) 2\/63 id=12345678/);
  assert.match(output,/Command: not identified for this failure/);
  assert.ok(output.split('\n').every(line=>line.length<=2100));assert.ok(output.length<3500);
});

test('invalid metadata never turns into misleading timeout or attempt values',()=>{
  for(const extra of [{otaTimeoutMs:-1},{otaTimeoutMs:Infinity},{otaTimeoutMs:'7000'},
    {otaTimeoutMs:86400001},{otaAttempt:0},{otaAttempt:1.5},{otaAttempt:1001},{otaAttempt:'7'}]){
    const output=errorDetails({message:'Failed',...extra});assert.doesNotMatch(output,/Timeout:|Attempt:/);
  }
  assert.match(errorDetails({message:'Failed',otaAttempt:2,otaMaxAttempts:1}),/Attempt: 2\n/);
  assert.doesNotMatch(errorDetails({message:'Failed',otaAttempt:2,otaMaxAttempts:1}),/of 1/);
  assert.match(errorDetails({message:'Failed',otaTimeoutMs:1200}),/Timeout: 1\.2 s/);
});

test('unidentified commands and later failures do not inherit stale context or claim nothing was sent',()=>{
  errorDetails({message:'Old failure',otaStep:'Identity',otaCommand:'get public.key',otaPath:'Direct, 0 hops'});
  const output=errorDetails(Error('The firmware file is invalid'),{step:'Validate firmware'});
  assert.match(output,/Step: Validate firmware/);assert.match(output,/Command: not identified for this failure/);
  assert.match(output,/Reason: The firmware file is invalid/);
  assert.doesNotMatch(output,/get public\.key|Identity|Path:|no command was sent|Command: none/i);
  for(const value of [null,undefined,{},17])assert.match(errorDetails(value),/Reason: Unknown failure/);
  assert.match(errorDetails('A plain failure'),/Reason: A plain failure/);
});
