/* Browser-owned, expiring radio2-only leases for a verified direct OTA link. */
(function(root,factory){
  const api=factory(root);
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaRadio2Only=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(root){
  'use strict';
  const KEY='meshcore-browser-lora-ota-radio2-only-v1';
  const full=/^[a-f0-9]{64}$/i,tokenPattern=/^[a-f0-9]{16}$/;
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const bare=value=>String(value??'').trim().replace(/^>\s*/, '');
  function parse(value){
    const text=bare(value);
    if(/^(?:err(?:or)?\s*:?\s*)?(?:unknown|unsupported)\b/i.test(text)||/^err(?:or)?\s*:?\s*radio2\.only unsupported\b/i.test(text))return {state:'unsupported'};
    if(/^off$/i.test(text))return {state:'off'};
    const m=/^(on|pending)\s+([a-f0-9]{16})$/i.exec(text);
    if(!m)throw Error('Cannot verify radio2-only status.');
    return {state:m[1].toLowerCase(),token:m[2].toUpperCase()};
  }
  function create({storage,random,wait=pause}={}){
    if(!storage)try{storage=root.localStorage;}catch(_){}
    function load(){
      let value;
      try{value=JSON.parse(storage?.getItem(KEY)||'null');}catch(_){throw Error('The radio2-only recovery record is unreadable.');}
      if(value===null)return null;
      if(!value||value.version!==1||!full.test(value.source||'')||!full.test(value.target||'')||
         value.source!==value.source.toLowerCase()||value.target!==value.target.toLowerCase()||
         value.source===value.target||!tokenPattern.test(value.token||'')||
         typeof value.sourceOwned!=='boolean'||typeof value.targetOwned!=='boolean')
        throw Error('The radio2-only recovery record is invalid.');
      return value;
    }
    function equal(a,b){if(a===null||b===null)return a===b;return !!a&&!!b&&['version','source','target','token','sourceOwned','targetOwned'].every(k=>a[k]===b[k]);}
    function save(record,previous){
      if(!storage)throw Error('Radio2-only recovery storage is unavailable; primary reception was not changed.');
      if(!equal(load(),previous))throw Error('Radio2-only recovery ownership changed.');
      try{storage.setItem(KEY,JSON.stringify(record));}catch(_){throw Error('Could not save radio2-only recovery; primary reception was not changed.');}
      if(!equal(load(),record))throw Error('The radio2-only recovery record changed.');
    }
    function clear(record){if(!equal(load(),record))throw Error('Radio2-only recovery ownership changed.');try{storage.removeItem(KEY);}catch(_){throw Error('Radio2-only state was restored but its recovery record could not be cleared.');}}
    function randomToken(){
      const bytes=new Uint8Array(8);const crypto=root.crypto||(typeof require==='function'?require('node:crypto').webcrypto:null);
      if(random)random(bytes);else if(crypto?.getRandomValues)crypto.getRandomValues(bytes);else throw Error('Secure radio2-only ownership is unavailable.');
      return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
    }
    function binding(link,target,canContinue){
      if(!link||!full.test(link.sourceKey||'')||!full.test(target||'')||link.sourceKey.toLowerCase()===target.toLowerCase()||
        !(link.browserRawSource===true&&link.otaSupported===true||link.browserRawSource!==true&&typeof link.withRadio2OnlyBurst==='function'&&link.otaPathValue==='OFF')||link.relays?.length||typeof link.localCommand!=='function'||typeof link.remoteCommand!=='function')
        throw Error('Radio2-only setup requires the verified direct OTA source and target.');
      const source=link.sourceKey.toLowerCase(),key=target.toLowerCase();
      const active=()=>{
        if(link.failure||link.peerFailure||link.running===false||link.opened===false||link.portOpened===false||
           link.sourceKey?.toLowerCase()!==source||link.browserRawSource!==true&&link.otaPathValue!=='OFF'||typeof link.knownRepeater==='string'&&link.knownRepeater.toLowerCase()!==key||
           canContinue&&canContinue()!==true)throw Error('The verified OTA connection changed; no further radio2-only command was sent.');
      };
      return {source,target:key,active};
    }
    async function poll(read,wanted,token,active){
      let state;
      for(let n=0;n<20;n++){
        active();state=parse(await read('get radio2.only'));active();
        if(state.state===wanted&&(wanted==='off'||state.token===token.toUpperCase()))return state;
        if(state.state==='unsupported')throw Error('Radio2-only became unavailable.');
        if(state.state!=='off'&&state.token!==token.toUpperCase())throw Error('Radio2-only belongs to another session.');
        await wait(250);
      }
      throw Error('Radio2-only change is still pending; it will expire with the temporary radio2 window.');
    }
    async function activate(link,target,password,radio,{canContinue,progress=()=>{},io}={}){
      if(!radio||![radio.sourceProfile,radio.targetProfile].includes(1))return {state:'not-needed'};
      if(link?.browserRawSource!==true&&(link.otaRadioProfile!==radio.sourceProfile||link.otaTargetProfile!==radio.targetProfile))throw Error('The selected update profiles changed.');
      if(link?.browserRawSource!==true&&!io)return link.withRadio2OnlyBurst(target,password,io=>activate(link,target,password,radio,{canContinue,progress,io}));
      const proof=binding(link,target,canContinue);proof.active();
      let record=load(),persisted=record;
      if(record&&(record.source!==proof.source||record.target!==proof.target))throw Error('Another update has pending radio2-only recovery.');
      if(!record)record={version:1,source:proof.source,target:proof.target,token:randomToken(),sourceOwned:false,targetOwned:false};
      const remote=io?.remote||(cmd=>link.remoteCommand(proof.target,password,cmd,
        cmd.startsWith('get ')?{maxDurationMs:8000,allowReacquire:false}:{optionalReplyTimeoutMs:8000,allowReacquire:false}));
      const endpoints=[{name:'target',profile:radio.targetProfile,read:remote,field:'targetOwned'},
        {name:'source',profile:radio.sourceProfile,read:io?.local||(cmd=>link.localCommand(cmd)),field:'sourceOwned'}];
      for(const endpoint of endpoints){
        if(endpoint.profile!==1)continue;
        proof.active();const state=parse(await endpoint.read('get radio2.only'));proof.active();
        if(state.state==='unsupported'){progress(endpoint.name+' firmware cannot pause primary reception; it remains unchanged.');continue;}
        if(state.state!=='off'){
          if(state.token!==record.token.toUpperCase()||!record[endpoint.field]){
            progress(endpoint.name+' radio2-only belongs to another session; it remains unchanged.');continue;
          }
          if(state.state==='pending')await poll(endpoint.read,'on',record.token,proof.active);
          continue;
        }
        record={...record,[endpoint.field]:true};save(record,persisted);persisted=record;proof.active();
        if(!equal(load(),record))throw Error('Radio2-only ownership changed before activation.');
        let failure;
        try{const reply=bare(await endpoint.read('set radio2.only on '+record.token.toUpperCase()));
          if(!/^OK(?:\s|$)/i.test(reply))throw Error(reply||'Radio2-only activation was not acknowledged.');}
        catch(error){failure=error;}
        try{await poll(endpoint.read,'on',record.token,proof.active);}
        catch(error){throw failure||error;}
        progress(endpoint.name+' primary reception paused for the verified radio2 update window.');
      }
      return {state:'ready',record};
    }
    async function restore(link,target,password,{progress=()=>{},io}={}){
      let record=load();if(!record)return {state:'none'};
      if(link?.browserRawSource!==true&&!io)return link.withRadio2OnlyBurst(target,password,io=>restore(link,target,password,{progress,io}));
      const proof=binding(link,target);if(record.source!==proof.source||record.target!==proof.target)
        throw Error('Radio2-only recovery belongs to another source or target.');
      const remote=io?.remote||(cmd=>link.remoteCommand(proof.target,password,cmd,
        cmd.startsWith('get ')?{maxDurationMs:8000,allowReacquire:false}:{optionalReplyTimeoutMs:8000,allowReacquire:false}));
      let firstError;
      for(const endpoint of [{name:'target',read:remote,field:'targetOwned'}, {name:'source',read:io?.local||(cmd=>link.localCommand(cmd)),field:'sourceOwned'}]){
        try{
        if(!record[endpoint.field])continue;
        proof.active();if(!equal(load(),record))throw Error('Radio2-only recovery ownership changed.');
        const state=parse(await endpoint.read('get radio2.only'));proof.active();
        if(state.state==='unsupported')throw Error(endpoint.name+' cannot verify radio2-only recovery.');
        if(state.state!=='off'&&state.token===record.token.toUpperCase()){
          let failure;
          try{const reply=bare(await endpoint.read('set radio2.only off '+record.token.toUpperCase()));
            if(!/^OK(?:\s|$)/i.test(reply))throw Error(reply||'Radio2-only restore was not acknowledged.');}
          catch(error){failure=error;}
          try{await poll(endpoint.read,'off',record.token,proof.active);}catch(error){throw failure||error;}
        }
        // A different owner is never cleared by this browser.
        const previous=record;record={...record,[endpoint.field]:false};save(record,previous);
        progress(endpoint.name+' primary reception restored or owned by another session.');
        }catch(error){firstError=firstError||error;}
      }
      if(firstError)throw firstError;
      clear(record);return {state:'restored'};
    }
    // Called inside the existing authenticated admin burst, immediately after
    // a verified temporary-profile renewal. Firmware clears the old pin on
    // every generation change; re-arm only a lease this browser owns.
    async function rearm(source,target,index,read,active,progress=()=>{},refresh=false){
      const record=load();if(!record)return;
      if(record.source!==String(source).toLowerCase()||record.target!==String(target).toLowerCase())
        throw Error('Radio2-only renewal belongs to another update.');
      const field=index?'targetOwned':'sourceOwned';if(!record[field])return;
      active();if(!equal(load(),record))throw Error('Radio2-only ownership changed before renewal.');
      const before=parse(await read('get radio2.only'));active();
      if(before.state!=='off'){
        if(before.token!==record.token.toUpperCase())throw Error('Radio2-only belongs to another session.');
        if(before.state==='pending')await poll(read,'on',record.token,active);
        if(!refresh)return;
      }
      let failure;
      try{const reply=bare(await read('set radio2.only on '+record.token.toUpperCase()));
        if(!/^OK(?:\s|$)/i.test(reply))throw Error(reply||'Radio2-only renewal was not acknowledged.');}
      catch(error){failure=error;}
      // An already-on readback cannot prove that its watchdog deadline moved.
      // If its refresh acknowledgement was lost, stop instead of claiming a
      // renewed deadline; the firmware watchdog restores primary reception.
      if(failure&&before.state!=='off')throw failure;
      try{await poll(read,'on',record.token,active);}catch(error){throw failure||error;}
      progress((index?'Target':'USB source')+' primary reception paused for the renewed update window.');
    }
    return {activate,restore,load,rearm};
  }
  return {create,parse,KEY};
});
