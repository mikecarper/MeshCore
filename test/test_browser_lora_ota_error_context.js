'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {errorDetails,collectFailureIdentity,probeHybridRam,legacyHybridRam,readHybridRam}=require('../docs/_javascript/lora_ota_checks.js');

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

test('preparation diagnostics preserve full released firmware basenames and hashes without mutating metadata',()=>{
  const installedName='GAT562_30S_Mesh_Kit_repeater_lora_ota_no_external_sensors-full-ota-v1.18.0.1-halo-keymind-cascade-dev-03214a16.bin';
  const selectedName='GAT562_30S_Mesh_Kit_repeater_lora_ota_no_external_sensors-full-ota-v1.18.0.2-halo-keymind-cascade-dev-6cc343a3.bin';
  const imageHash='c308f6e097045692d3b9ab7f4f542b8a01ce6ed21954e905734ab1f84fe4706e',bodyHash='1122334455667788';
  const preparation={installed:{name:'/home/private-owner/firmware/'+installedName,hwId:'GAT562_30S_Mesh_Kit',version:0x01120001,imageHash,bodyHash},
    selected:{name:'C:\\private-owner\\firmware\\'+selectedName,hwId:'GAT562_30S_Mesh_Kit',version:0x01120002,imageHash:'92'.repeat(32),bodyHash:'ab'.repeat(8)},
    search:{enabled:true,depth:'deep',objective:'data',result:'No published path for these exact images',candidates:6},
    ram:{confirmed:false,reason:'The bootloader identity could not be confirmed',bootloaderVersion:'0.11.0-OTAFIX2.4.5',
      bootloaderStatus:'BL board=239A0029 target=D50D2D44 name=GAT562_DFU crc=12345678 abi=3 caps=0A | staged:none mid=- hash=-'},
    space:{workspace:643072,staged:327680,required:970752,available:815104}};
  const snapshot=structuredClone(preparation);
  for(const entry of Object.values(preparation))Object.freeze(entry);Object.freeze(preparation);
  const error=Object.freeze(Object.assign(Error('An intermediate update is needed.'),{otaPreparation:preparation,otaLocalCheck:true}));
  const output=errorDetails(error,{step:'Prepare update'});
  assert.ok(output.includes('Installed firmware file: '+installedName));assert.ok(output.includes('Selected firmware file: '+selectedName));
  assert.ok(!output.includes('private-owner'));assert.ok(!output.includes('/home/'));assert.ok(!output.includes('C:\\'));
  assert.match(output,/Installed firmware hardware: GAT562_30S_Mesh_Kit/);
  assert.match(output,/Installed firmware version: 1\.18\.0\.1/);assert.match(output,/Selected firmware version: 1\.18\.0\.2/);
  assert.ok(output.includes('Installed firmware image SHA-256: '+imageHash));assert.ok(output.includes('Installed firmware body hash: '+bodyHash));
  assert.ok(output.includes('Selected firmware image SHA-256: '+'92'.repeat(32)));
  assert.match(output,/Automatic search: enabled; depth: deep; objective: least radio data; candidates: 6/);
  assert.match(output,/Search result: No published path for these exact images/);
  assert.match(output,/Retained RAM support: not confirmed \(The bootloader identity could not be confirmed\)/);
  assert.match(output,/Reported bootloader version: 0\.11\.0-OTAFIX2\.4\.5/);
  assert.match(output,/Reported bootloader identity: BL board=239A0029 target=D50D2D44 name=GAT562_DFU crc=12345678 abi=3 caps=0A/);
  assert.match(output,/Rewrite workspace: 628 KiB \(643072 bytes\)/);
  assert.match(output,/Staged package in flash: 320 KiB \(327680 bytes\)/);
  assert.match(output,/Total flash required: 948 KiB \(970752 bytes\)/);
  assert.match(output,/Flash available: 796 KiB \(815104 bytes\)/);
  assert.match(output,/Reason: An intermediate update is needed\.$/);
  assert.deepEqual(preparation,snapshot);assert.equal(error.message,'An intermediate update is needed.');
});

test('preparation diagnostics redact secrets, reject invalid image metadata, and never retain addresses or paths',()=>{
  const secret='VERY_PRIVATE_'+('s'.repeat(150))+'_END',hashSecret='ba'.repeat(32),url='https://private.example/firmware.bin?token=secret';
  const output=errorDetails({message:'Preparation failed',otaPreparation:{
    installed:{name:'/private/'+secret+'/release-'+secret+'.bin',hwId:'board-'+secret,version:-1,imageHash:hashSecret,bodyHash:'not-a-firmware-hash'},
    selected:{name:url,hwId:'Board\nInjected: yes',version:'1.18.0.2',imageHash:'ff'.repeat(31),bodyHash:'a'.repeat(17)},
    search:{enabled:'yes',depth:'untrusted',objective:'untrusted',result:secret+' '+url,candidates:Infinity},
    ram:{confirmed:true,reason:secret+' '+url,bootloaderVersion:secret,bootloaderStatus:url+' '+secret},space:{workspace:-1,staged:NaN,required:Infinity,available:'815104'}
  }},{secrets:[secret,hashSecret]});
  for(const value of [secret,'s'.repeat(20),'VERY_PRIVATE','_END',hashSecret,url,'private.example','/private/','Injected:'])assert.ok(!output.includes(value),value);
  assert.match(output,/Installed firmware file: release-<hidden>\.bin/);
  assert.match(output,/Installed firmware image SHA-256: <hidden>/);
  assert.match(output,/Selected firmware file: <address hidden>/);
  assert.match(output,/Installed firmware version: not available/);assert.match(output,/Selected firmware version: not available/);
  assert.match(output,/Selected firmware hardware: not available/);assert.match(output,/Selected firmware image SHA-256: not available/);
  assert.match(output,/Selected firmware body hash: not available/);
  assert.match(output,/Automatic search: not available; depth: not available; objective: not available; candidates: not available/);
  for(const label of ['Rewrite workspace','Staged package in flash','Total flash required','Flash available'])assert.ok(output.includes(label+': not available'));
  assert.ok(!/[^\x20-\x7e\n]/.test(output));assert.ok(output.length<3500);
  for(const name of ['bad\nname.bin','x'.repeat(513)+'.bin','invalid.txt','',null]){
    const text=errorDetails({message:'Failed',otaPreparation:{installed:{name}}});assert.match(text,/Installed firmware file: not available/);
  }
  const addresses=errorDetails({message:'Failed',otaPreparation:{installed:{name:'/home/private/release-192.168.1.22.bin',hwId:'board-8.8.8.8'},
    selected:{name:'release-8.8.4.4.bin',hwId:'fd00::1234'}}});
  for(const address of ['192.168.1.22','8.8.8.8','8.8.4.4','fd00::1234'])assert.ok(!addresses.includes(address),address);
  const pathSecret=errorDetails({message:'Failed',otaPreparation:{installed:{name:'/private/PRIVATE/SECRET.bin'}}},{secrets:['PRIVATE/SECRET']});
  assert.ok(!pathSecret.includes('SECRET'));assert.ok(!pathSecret.includes('PRIVATE'));
});

test('preparation diagnostics distinguish missing fields and bound packed versions',()=>{
  const missing=errorDetails({message:'Failed',otaPreparation:{}});
  assert.match(missing,/Installed firmware file: not available/);assert.match(missing,/Selected firmware version: not available/);
  assert.match(missing,/Retained RAM support: not available/);assert.match(missing,/Search result: not available/);
  for(const [input,expected] of [[0,'0.0.0.0'],[0xffffffff,'255.255.255.255'],[0x01120002,'1.18.0.2'],[1.5,'not available'],[0x100000000,'not available']]){
    assert.ok(errorDetails({message:'Failed',otaPreparation:{selected:{version:input}}}).includes('Selected firmware version: '+expected));
  }
  for(const value of [null,[],42,'invalid'])assert.ok(!errorDetails({message:'Failed',otaPreparation:value}).includes('Update preparation details:'));
});

test('retained RAM probing uses an exact legacy identity after explicit unsupported replies or exceptions only',async()=>{
  const verified='BL board=239A0029 target=D50D2D44 name=GAT562_DFU crc=03F81FDD abi=3 caps=0A | staged:none mid=- hash=-';
  for(const exception of [false,true]){
    const calls=[],reply=await probeHybridRam(async command=>{
      calls.push(command);if(command==='ota ram'){if(exception)throw Error('Unknown OTA command. Type `ota help`.');return 'Error: unsupported';}return verified;
    });
    assert.equal(reply,'OTA RAM: hybrid=1 abi=1 record=72 arena=65536');assert.deepEqual(calls,['ota ram','ota bootloader status']);
  }
  for(const message of ['Remote command timed out','Access denied','Unexpected response']){
    const original=Error(message),calls=[];
    await assert.rejects(probeHybridRam(async command=>{calls.push(command);throw original;}),error=>error===original);
    assert.deepEqual(calls,['ota ram']);
  }
  await assert.rejects(probeHybridRam(async command=>command==='ota ram'?'unsupported':verified.replace('03F81FDD','DEADBEEF')),/does not match a verified published image/);
});

test('legacy retained RAM version proof is restricted to stable internal OTAFIX 2.4.6 through 2.4.12',()=>{
  const status='BL board=239A0029 target=2D0DF000 name=4631_DFU crc=FF374B1B abi=3 caps=0A | staged:none mid=- hash=-';
  const proof='OTA RAM: hybrid=1 abi=1 record=72 arena=65536';
  for(let minor=6;minor<=12;minor++)for(const value of ['OTAFIX2.4.'+minor,'0.11.0-OTAFIX2.4.'+minor,'v0.11.0-OTAFIX2.4.'+minor,'> 0.11.0-OTAFIX2.4.'+minor])
    assert.equal(legacyHybridRam(status,value),proof,value);
  for(const value of [undefined,null,5,{},'','OTAFIX2.4.5','OTAFIX2.4.13','OTAFIX2.5.0','0.11.0-OTAFIX2.5.1','OTAFIX2.4.12-preview',
    '0.11.0-OTAFIX2.4.12-custom','0.11.0-OTAFIX2.4.6-tests','vendor-OTAFIX2.4.12','OTAFIX2.4.06','0.12.0-OTAFIX2.4.12','0.11.0','OTAFIX2.4.12 anything'])
    assert.throws(()=>legacyHybridRam(status,value),/cannot confirm retained RAM/,String(value));
  for(const bad of [status.replace('caps=0A','caps=09'),status.replace('caps=0A','caps=06'),status.replace('caps=0A','caps=1E'),
    status.replace('abi=3','abi=4'),status.replace('abi=3','abi=2'),status.replace('board=239A0029','board=00000000'),
    status.replace('target=2D0DF000','target=FFFFFFFF'),status.replace('crc=FF374B1B','crc=00000000'),status.replace('crc=FF374B1B','crc=FFFFFFFF'),
    status.replace('name=4631_DFU','name='),status.replace('name=4631_DFU','name=4631_QSPI_DFU'),status.replace('name=4631_DFU','name=4631_SD_DFU'),
    status.replace('staged:none','staged:ready'),status.replace('mid=-','mid=12345678'),status.replace('hash=-','hash=abcdef'),status+' caps=0A'])
    assert.throws(()=>legacyHybridRam(bad,'0.11.0-OTAFIX2.4.12'),/cannot confirm retained RAM/,bad);
});

test('legacy retained RAM probe reads version only when the exact CRC is not already known',async()=>{
  const status='BL board=239A0029 target=2D0DF000 name=4631_DFU crc=FF374B1B abi=3 caps=0A | staged:none mid=- hash=-';
  const proof='OTA RAM: hybrid=1 abi=1 record=72 arena=65536';
  for(const throws of [false,true]){
    const calls=[];
    assert.equal(await probeHybridRam(async command=>{
      calls.push(command);
      if(command==='ota ram'){if(throws)throw Error('Error: unsupported');return 'Unknown OTA command';}
      return command==='ota bootloader status'?status:'0.11.0-OTAFIX2.4.6';
    }),proof);
    assert.deepEqual(calls,['ota ram','ota bootloader status','get bootloader.ver']);
  }
  for(const reply of ['OTA RAM: hybrid=0','OTA RAM: hybrid=1 abi=1 record=72 arena=32768','OTA RAM: hybrid=1 abi=1 record=72 arena=65536 extra']){
    const calls=[];await assert.rejects(probeHybridRam(async command=>{calls.push(command);return reply;}),/cannot be confirmed/);
    assert.deepEqual(calls,['ota ram']);
  }
  const calls=[],timeout=Error('Version read timed out');
  await assert.rejects(probeHybridRam(async command=>{
    calls.push(command);if(command==='ota ram')return 'unsupported';if(command==='ota bootloader status')return status;throw timeout;
  }),error=>error===timeout);
  assert.deepEqual(calls,['ota ram','ota bootloader status','get bootloader.ver']);
  const plan={storage:'internal',layoutFlags:16},info={blob:new Uint8Array(5000)},sessionCalls=[];
  assert.equal(await readHybridRam(plan,info,async()=>{sessionCalls.push('ram');throw Error('Unknown OTA command');},
    async()=>{sessionCalls.push('identity');return status;},async()=>{sessionCalls.push('version');return 'OTAFIX2.4.12';}),proof);
  assert.deepEqual(sessionCalls,['ram','identity','version']);
  await assert.rejects(readHybridRam(plan,info,async()=> 'unsupported',async()=>status),/RAM capability/);
  let read=false;assert.equal(await readHybridRam({storage:'qspi',layoutFlags:16},info,()=>{read=true;}),null);assert.equal(read,false);
});
