/* Restore only this browser's verified update channel on the same USB source. */
(function(root,factory){
  const setup=typeof module==='object'&&module.exports?require('./lora_ota_radio_setup.js'):root.LoraOtaRadioSetup;
  const api=factory(setup);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaRecovery=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(setup){
  'use strict';
  const owned=new WeakMap(), fullKey=value=>typeof value==='string'&&/^[a-f0-9]{64}$/i.test(value);
  const bare=value=>String(value).trim().replace(/^>\s*/,'');
  const same=(a,b)=>Array.isArray(a)&&Array.isArray(b)&&a.length===b.length&&a.every((v,i)=>Math.abs(v-b[i])<=(i===0?.001:.00001));
  const waitDefault=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  function channel(record,sourceKey,targetKey,now=Date.now()){
    if(!fullKey(sourceKey)||!fullKey(targetKey)||sourceKey.toLowerCase()===targetKey.toLowerCase()||
      !record||!['pull attempted','ready'].includes(record.phase)||
      !fullKey(record.source)||!fullKey(record.target)||record.source.toLowerCase()!==sourceKey.toLowerCase()||
      record.target.toLowerCase()!==targetKey.toLowerCase()||typeof record.id!=='string'||!/^[a-f0-9]{8}$/i.test(record.id)||
      typeof record.packageHash!=='string'||!/^[a-f0-9]{64}$/i.test(record.packageHash)||
      typeof record.bodyHash!=='string'||!/^[a-f0-9]{16}$/i.test(record.bodyHash))return null;
    const radio=record.radio;
    if(!radio||!Array.isArray(radio.tuple)||radio.tuple.length!==4||
      ![0,1].includes(radio.sourceProfile)||![0,1].includes(radio.targetProfile)||
      !Number.isSafeInteger(now)||!Number.isSafeInteger(radio.verifiedAt)||!Number.isSafeInteger(radio.expiresAt)||
      radio.verifiedAt<=0||radio.verifiedAt>now+30000||radio.expiresAt<=now||radio.expiresAt<=radio.verifiedAt||
      radio.expiresAt-radio.verifiedAt>10080*60000)return null;
    try{setup.settings({frequency:radio.tuple[0],bandwidth:radio.tuple[1],sf:radio.tuple[2],cr:radio.tuple[3],
      preamble:radio.preamble,minutes:2});}catch(_){return null;}
    return {tuple:radio.tuple.slice(),preamble:radio.preamble,sourceProfile:radio.sourceProfile,
      targetProfile:radio.targetProfile,verifiedAt:radio.verifiedAt,expiresAt:radio.expiresAt,
      id:record.id.toLowerCase(),packageHash:record.packageHash.toLowerCase()};
  }
  function validLink(link,target){
    if(!link||link.failure||link.serving||!fullKey(link.sourceKey)||!fullKey(target)||
      link.knownRepeater?.toLowerCase()!==target.toLowerCase()||link.relays?.length||
      typeof link.localCommand!=='function'||link.browserRawSource&&link.localCliSupported===false)
      throw Error('The same idle USB source and complete target key are required for channel recovery');
    if(link.browserRawSource&&typeof link.applyTemporaryRadio!=='function')
      throw Error('This Companion cannot safely restore a temporary source channel');
  }
  async function snapshot(link){
    const read=command=>link.localCommand(command);
    const saved=setup.primary(await read('get radio'));
    const stats=bare(await read('ota stats'));
    if(!/\bfetch idle\b/i.test(stats))throw Error('Source has an active or unverified OTA download');
    if(!/^folder=off\b/i.test(bare(await read('ota folder'))))throw Error('Source has an attached or unverified OTA folder');
    const primary=await setup.readTemporary(read,0);
    if(!['off','active'].includes(primary.state))throw Error('Source temporary primary is pending or unverified');
    return {saved,primary};
  }
  function matches(state,radio){return state.state==='active'&&same(state.tuple,radio.tuple)&&state.preamble===radio.preamble;}
  async function readback(link,radio,{wait=waitDefault,now=()=>Date.now()}={}){
    const start=now();let previous=start;
    for(let attempt=0;attempt<71;attempt++){
      const current=now();if(!Number.isFinite(current)||current<previous||current-start>35000)break;
      previous=current;
      const state=await setup.readTemporary(command=>link.localCommand(command),radio.sourceProfile);
      const after=now();if(!Number.isFinite(after)||after<previous||after-start>35000)break;
      previous=after;
      if(matches(state,radio))return state;
      if(!['off','pending','active'].includes(state.state))throw Error('Source recovery channel cannot be verified');
      await wait(500);
    }
    throw Error('Source recovery channel did not become active; no target command was sent');
  }
  async function writeTemporary(link,radio,minutes,options){
    const command=setup.commands({frequency:radio.tuple[0],bandwidth:radio.tuple[1],sf:radio.tuple[2],cr:radio.tuple[3],
      preamble:radio.preamble,minutes},{sourceProfile:radio.sourceProfile,targetProfile:radio.targetProfile}).source;
    const reply=await(link.browserRawSource?link.applyTemporaryRadio(command):link.localCommand(command));
    if(!/^OK(?:\s|$)/i.test(bare(reply)))throw Error('Source recovery channel was not acknowledged: '+bare(reply));
    await readback(link,radio,options);
  }
  async function restoreSource(link,target,record,options={}){
    if(options.override===true)return {state:'unavailable',reason:'Using the explicitly selected update channel'};
    // Raw-only Companions can send OTA frames without containing a firmware
    // source themselves. Their unsupported local OTA getters cannot establish
    // ownership for an automatic retune; retain their current-channel flow.
    if(link?.browserRawSource===true&&link.otaSupported!==true&&link.usesUsbFolderFallback!==true)
      return {state:'unavailable',reason:'This Companion uses its current update channel'};
    const radio=channel(record,link?.sourceKey,target,options.now?options.now():Date.now());
    if(!radio)return {state:'unavailable',reason:'No matching verified update channel is saved'};
    validLink(link,target);
    // Old Full builds reserve the native file queue lazily. Preserve their
    // queued messages before reading folder state; this changes no RF peer.
    if(link.usesUsbFolderFallback===true&&typeof link.ensureUsbFolderReady==='function')
      await link.ensureUsbFolderReady();
    // A failed probe may retain a RAM-only return-path owner. Closing that
    // source-local owner sends no target command and permits profile selection.
    if(typeof link.closeRadioSession==='function')await link.closeRadioSession();
    const before=await snapshot(link),read=command=>link.localCommand(command);
    let state=before.primary;
    if(radio.sourceProfile===1){
      if(typeof link.probeOtaRadio!=='function'||!await link.probeOtaRadio()||link.supportsOtaRadio2!==true)
        throw Error('This source cannot recover its recorded secondary update channel');
      if(before.primary.state!=='off')throw Error('A primary temporary channel already owns this source');
      state=await setup.readTemporary(read,1);
      if(!matches(state,radio)){
        if(state.state!=='off'||setup.secondary(await read('get radio2')).state!=='off'||
          setup.crossing(await read('get radio2.cross'))!=='off'||
          !setup.schedule(await read('get radioat2'),'get radioat2').empty||
          !setup.schedule(await read('get tempradioat2'),'get tempradioat2').empty)
          throw Error('Source secondary radio is already in use; it was not changed');
      }
    }else{
      const secondary=setup.secondary(await read('get radio2'));
      const temporary=setup.secondary(await read('get tempradio2'),'get tempradio2');
      if(!['off','unsupported'].includes(secondary.state)||!['off','unsupported'].includes(temporary.state))
        throw Error('Source secondary radio is already in use; it was not changed');
    }
    const changed=!matches(state,radio);
    if(changed){
      options.progress?.('Checking the saved update channel on the USB source.');
      await writeTemporary(link,radio,2,options);
    }
    if(!same(setup.primary(await read('get radio')),before.saved))throw Error('Source saved channel changed during recovery');
    if(typeof link.selectOtaRadio==='function')await link.selectOtaRadio(radio.sourceProfile);
    else if(radio.sourceProfile)throw Error('Source radio selection cannot be verified');
    link.otaTargetProfile=radio.targetProfile;
    const result={state:changed?'restored':'unchanged',...radio,requiresVerification:true};
    owned.set(result,{link,source:link.sourceKey.toLowerCase(),target:target.toLowerCase(),saved:before.saved,
      targetExplicit:record.radio.explicitProfiles?.[1]===true,
      radio:{...radio,tuple:radio.tuple.slice()}});
    return result;
  }
  async function confirmSource(link,result,{verifiedKey,adminVerified=false,...options}={}){
    const token=owned.get(result);
    if(!token||token.link!==link||!fullKey(verifiedKey)||verifiedKey.toLowerCase()!==token.target)
      throw Error('The recovered target must prove its complete public key before extending the source window');
    const now=options.now?options.now():Date.now();
    if(!Number.isSafeInteger(now)||token.radio.expiresAt<=now||!same(result.tuple,token.radio.tuple)||
      ['preamble','sourceProfile','targetProfile','verifiedAt','expiresAt','id','packageHash'].some(name=>result[name]!==token.radio[name]))
      throw Error('The saved update channel expired or changed before verification');
    validLink(link,token.target);
    if(link.sourceKey.toLowerCase()!==token.source)throw Error('Source identity changed during channel recovery');
    const authenticated=link.browserRawSource
      ?link.otaSupported===true?link.authenticated===true:link.investigationAuthenticated===true&&link.investigationVerified===true
      :link.authenticatedKey?.toLowerCase()===token.target;
    const role=link.investigationPermission||'unknown';
    if(!authenticated||link.browserRawSource&&link.otaSupported!==true&&
      !(role==='admin'||role==='unknown'&&adminVerified===true))
      throw Error('Existing target administration must be verified before extending the source window');
    if(!same(setup.primary(await link.localCommand('get radio')),token.saved))throw Error('Source saved channel changed during recovery');
    const current=await setup.readTemporary(command=>link.localCommand(command),result.sourceProfile);
    if(!matches(current,result))throw Error('Source recovery channel changed before verification');
    await writeTemporary(link,result,120,options);
    if(!same(setup.primary(await link.localCommand('get radio')),token.saved))throw Error('Source saved channel changed during recovery');
    link.otaLeaseProof={source:link.sourceKey,target:token.target,tuple:result.tuple.slice(),preamble:result.preamble,
      sourceProfile:result.sourceProfile,targetProfile:result.targetProfile,explicitProfiles:[true,token.targetExplicit]};
    owned.delete(result);result.requiresVerification=false;
    return {...result,minutes:120};
  }
  return {channel,restoreSource,confirmSource};
});
