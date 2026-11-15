'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {errorDetails,collectFailureIdentity}=require('../docs/_javascript/lora_ota_checks.js');

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

test('failure identity snapshots cached USB details and reads only ver then board once',async()=>{
  const source=Object.freeze({firmwareVersion:'v1.18.0.2',firmwareBoardName:'Heltec V4 OLED'}),calls=[];
  let running=false;
  const result=await collectFailureIdentity({source,timeoutMs:1234,read:async(command,options)=>{
    assert.equal(running,false,'identity requests must be sequential');running=true;calls.push([command,options]);await Promise.resolve();running=false;
    return command==='ver'?'> v1.18.0.1 (Build: 24-Oct-2026)\r\n':'> RAK 3401';
  }});
  assert.deepEqual(calls,[['ver',{timeoutMs:1234}],['board',{timeoutMs:1234}]]);
  assert.deepEqual(result.source,{version:'v1.18.0.2',board:'Heltec V4 OLED',origin:'cached USB identity'});
  assert.deepEqual(result.target.version,{command:'ver',origin:'fresh read',attempted:true,value:'v1.18.0.1 (Build: 24-Oct-2026)'});
  assert.deepEqual(result.target.board,{command:'board',origin:'fresh read',attempted:true,value:'RAK 3401'});
  assert.equal(source.firmwareVersion,'v1.18.0.2');
});

test('known target version and board require no additional LoRa requests',async()=>{
  const target=Object.freeze({version:'v1.18.0.2',board:'RAK 3401',origin:'earlier target reply in this session'});let calls=0,guards=0;
  const result=await collectFailureIdentity({target,read:async()=>{calls++;throw Error('must not read');},canContinue:()=>{guards++;return true;}});
  assert.equal(calls,0);assert.equal(guards,0);
  for(const field of ['version','board']){
    assert.equal(result.target[field].value,target[field]);assert.equal(result.target[field].origin,'cached target reply');assert.equal(result.target[field].attempted,false);
  }
  const output=errorDetails({message:'Original failure',otaIdentity:result});
  assert.match(output,/Target version \(cached earlier in this session; may predate update\): v1\.18\.0\.2/);
  assert.match(output,/Target board \(cached earlier in this session; may predate update\): RAK 3401/);
  assert.doesNotMatch(output,/fresh read/);assert.equal(target.origin,'earlier target reply in this session');
});

test('only a missing target identity field is queried once',async()=>{
  for(const [target,expected] of [[{version:'v1.18.0.2'},'board'],[{board:'Heltec V4'},'ver']]){
    const calls=[],result=await collectFailureIdentity({target,read:async command=>{calls.push(command);return command==='ver'?'v1.18.0.1':'RAK 3401';}});
    assert.deepEqual(calls,[expected]);
    const known=expected==='ver'?'board':'version',fresh=expected==='ver'?'version':'board';
    assert.equal(result.target[known].origin,'cached target reply');assert.equal(result.target[known].attempted,false);
    assert.equal(result.target[fresh].origin,'fresh read');assert.equal(result.target[fresh].attempted,true);
  }
});

test('a disconnected collector keeps cached target identity while suppressing missing-field reads',async()=>{
  let calls=0;
  const result=await collectFailureIdentity({target:{version:'v1.18.0.2'},canContinue:()=>false,read:async()=>{calls++;}});
  assert.equal(calls,0);assert.equal(result.target.version.value,'v1.18.0.2');assert.equal(result.target.version.origin,'cached target reply');
  assert.equal(result.target.board.attempted,false);assert.match(result.target.board.error,/Connection changed/);
  const both=await collectFailureIdentity({target:{version:'v1.18.0.2',board:'RAK 3401'},canContinue:()=>false});
  assert.equal(both.target.board.value,'RAK 3401');assert.equal(both.target.version.value,'v1.18.0.2');
});

test('target cache is snapshotted before a missing-field read and invalid cached values are not accepted',async()=>{
  const target={version:'v1.18.0.2'},result=await collectFailureIdentity({target,read:async()=>{target.version='v9.9.9';return 'RAK 3401';}});
  assert.equal(result.target.version.value,'v1.18.0.2');assert.equal(result.target.version.origin,'cached target reply');
  const calls=[],invalid=await collectFailureIdentity({target:{version:'ERR unavailable',board:''},read:async command=>{calls.push(command);return command==='ver'?'v1.18.0.2':'Heltec V4';}});
  assert.deepEqual(calls,['ver','board']);assert.equal(invalid.target.version.origin,'fresh read');assert.equal(invalid.target.board.origin,'fresh read');
});

test('both identity reads may fail without replacing or modifying the original failure',async()=>{
  const original=Object.freeze(Object.assign(Error('Remote command timed out; result is unknown'),{loraReplyTimeout:true,otaCommand:'get public.key'})),calls=[];
  const result=await collectFailureIdentity({read:async command=>{calls.push(command);throw original;}});
  assert.deepEqual(calls,['ver','board']);
  for(const field of ['version','board']){
    assert.equal(result.target[field].origin,'not available');assert.equal(result.target[field].attempted,true);
    assert.equal(result.target[field].error,original.message);assert.equal(result.target[field].value,undefined);
  }
  assert.equal(original.message,'Remote command timed out; result is unknown');assert.equal(original.loraReplyTimeout,true);assert.equal(original.otaCommand,'get public.key');
  assert.equal(Object.hasOwn(original,'otaIdentity'),false);
});

test('identity rejects empty, unsupported, nontext and unbounded replies without extra probes',async()=>{
  for(const reply of ['', '> ', 'ERR unknown command','Error: unavailable','unknown','unsupported',
    'Permission denied','Access denied','Not supported','Unrecognized command',new Uint8Array([1,2]),'x'.repeat(2049),'Board\u0000name','Board \u2603']){
    const calls=[],result=await collectFailureIdentity({read:async command=>{calls.push(command);return reply;}});
    assert.deepEqual(calls,['ver','board']);
    assert.equal(result.target.version.value,undefined);assert.equal(result.target.board.value,undefined);
    assert.match(result.target.version.error,/empty, rejected or not a usable/);
  }
});

test('a stopped collector performs no commands and never calls a failed continuation predicate again',async()=>{
  let reads=0,guards=0;
  const result=await collectFailureIdentity({source:{firmwareVersion:'v1.18.0.2'},canContinue:()=>{guards++;return false;},read:async()=>{reads++;}});
  assert.equal(reads,0);assert.equal(guards,1);assert.equal(result.source.version,'v1.18.0.2');
  for(const field of ['version','board']){
    assert.equal(result.target[field].attempted,false);assert.match(result.target[field].error,/Connection changed|stopped/);
  }
});

test('disconnect during a read discards its result and prevents a query against another radio',async()=>{
  for(const fails of [false,true]){
    let connected=true;const calls=[];
    const result=await collectFailureIdentity({canContinue:()=>connected,read:async command=>{
      calls.push(command);connected=false;if(fails)throw Error('Old radio failure');return 'Old radio value';
    }});
    assert.deepEqual(calls,['ver']);assert.equal(result.target.version.value,undefined);assert.equal(result.target.version.attempted,true);
    assert.equal(result.target.board.attempted,false);assert.match(result.target.version.error,/Connection changed/);
    assert.equal(result.target.board.value,undefined);
  }
});

test('identity collection handles missing read and guard failures as unavailable data',async()=>{
  const missing=await collectFailureIdentity();assert.match(missing.target.version.error,/No safe read/);assert.equal(missing.target.board.attempted,false);
  const stopped=await collectFailureIdentity({canContinue:()=>{throw Error('Device changed');},read:()=>{throw Error('must not call');}});
  assert.equal(stopped.target.version.attempted,false);assert.equal(stopped.target.board.attempted,false);
});

test('identity timeout options are bounded by the callback contract without abandoning a running read',async()=>{
  for(const [input,expected] of [[1000,1000],[5001,5000],[100000,5000],[.5,1],[0,5000],[-1,5000],[Infinity,5000],['5',5000]]){
    const options=[],result=await collectFailureIdentity({timeoutMs:input,read:async(command,option)=>{options.push(option);return command==='ver'?'v1.18.0.2':'Heltec V4';}});
    assert.deepEqual(options,[{timeoutMs:expected},{timeoutMs:expected}]);assert.equal(result.target.version.value,'v1.18.0.2');
  }
});

test('cached and fresh identity fields omit keys and private addresses while keeping the firmware version',async()=>{
  const key='a1'.repeat(32),url='https://private.example/firmware?token=private';
  const result=await collectFailureIdentity({source:{firmwareVersion:'v1.18.0.2 '+key,firmwareBoardName:'Board '+url},
    read:async command=>command==='ver'?'v1.18.0.2 '+url+' '+key:'Heltec V4 192.168.1.25 fd00::1234 C:\\private\\board'});
  const output=JSON.stringify(result);
  for(const hidden of [key,url,'192.168.1.25','fd00::1234','C:\\private\\board'])assert.ok(!output.includes(hidden),hidden);
  assert.match(result.target.version.value,/v1\.18\.0\.2/);assert.match(result.source.version,/v1\.18\.0\.2/);
  assert.ok(result.target.board.value.length<=163);
});

test('identity display distinguishes cached source, fresh target and per-field unavailable reasons safely',()=>{
  const secret='PRIVATE_ADMIN_VALUE',key='b2'.repeat(32),identity={
    source:{version:'v1.18.0.2',board:'Heltec V4 '+secret+' 192.168.1.25',origin:'malicious '+secret},
    target:{version:{value:'v1.18.0.1 '+key,origin:'fresh read'},board:{error:'No reply '+secret,origin:'not available'}}
  };
  const original=Object.freeze(Object.assign(Error('Original OTA failure'),{otaIdentity:identity,loraReplyTimeout:true}));
  const output=errorDetails(original,{step:'Check target',secrets:[secret]});
  assert.match(output,/Source version \(cached USB identity\): v1\.18\.0\.2/);
  assert.match(output,/Source board \(cached USB identity\): Heltec V4/);
  assert.match(output,/Target version \(fresh read\): v1\.18\.0\.1/);
  assert.match(output,/Target board: not available \(No reply <hidden>\)/);
  assert.match(output,/Reason: Original OTA failure/);assert.ok(!output.includes(secret));assert.ok(!output.includes(key));assert.ok(!output.includes('malicious'));
  assert.equal(original.message,'Original OTA failure');assert.equal(original.otaIdentity,identity);assert.equal(identity.source.board,'Heltec V4 '+secret+' 192.168.1.25');assert.ok(!output.includes('192.168.1.25'));
  const unavailable=errorDetails({message:'Failed',otaIdentity:{source:{},target:{}}});
  assert.match(unavailable,/Source version \(cached USB identity\): not available/);assert.match(unavailable,/Target version: not available \(not read\)/);
});
