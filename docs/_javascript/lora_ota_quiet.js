/* Temporarily quiet only verified forwarding controls, with durable recovery. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaQuiet=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const locks=new WeakMap(),settings=new Set(['radio.flood','repeat']);
  const keys=['version','source','target','setting','original','state','restoreAttempted'];
  const bare=value=>typeof value==='string'?value.trim().replace(/^>\s*/,''):'';
  const fullKey=value=>typeof value==='string'&&/^[a-f0-9]{64}$/i.test(value);
  const copy=record=>({...record});
  function error(message,command,endpoint='browser'){
    return Object.assign(Error(message),{otaStep:'Temporarily pause or restore target forwarding',
      otaCommand:command,otaEndpoint:endpoint,otaQuietForwarding:true});
  }
  function storage(store){
    if(!store||typeof store!=='object'||['read','write','clear'].some(name=>typeof store[name]!=='function'))
      throw error('Forwarding recovery storage is unavailable.','Validate forwarding recovery storage');
  }
  function record(value){
    if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==keys.length||
        Object.keys(value).some(key=>!keys.includes(key))||value.version!==1||!fullKey(value.source)||
        !fullKey(value.target)||value.source!==value.source.toLowerCase()||value.target!==value.target.toLowerCase()||
        value.source===value.target||!settings.has(value.setting)||value.original!=='on'||
        !['pause-pending','paused','restore-pending'].includes(value.state)||typeof value.restoreAttempted!=='boolean'||
        value.restoreAttempted!==(value.state==='restore-pending'))
      throw error('The forwarding recovery record is invalid; no setting was changed.','Validate forwarding recovery record');
    return copy(value);
  }
  function load(store){const value=store.read();return value===null||value===undefined?null:record(value);}
  function equal(a,b){return !!a&&!!b&&keys.every(key=>a[key]===b[key]);}
  function save(store,value,active){
    active?.();
    const result=store.write(copy(value));
    active?.();
    if(result&&typeof result.then==='function'||!equal(load(store),value))
      throw error('The forwarding recovery record could not be saved; no further setting was changed.','Save forwarding recovery record');
  }
  function clear(store){
    const result=store.clear();
    if(result&&typeof result.then==='function'||load(store)!==null)
      throw error('Forwarding was restored, but its recovery record could not be cleared.','Clear forwarding recovery record');
  }
  function exclusive(store,action){
    storage(store);
    const next=(locks.get(store)||Promise.resolve()).catch(()=>{}).then(action);
    locks.set(store,next.catch(()=>{}));return next;
  }
  function binding(link,key,password,canContinue){
    if(canContinue!==undefined&&typeof canContinue!=='function')
      throw error('The forwarding pause cancellation check is invalid.','Validate forwarding pause cancellation');
    if(!link||!fullKey(link.sourceKey)||!fullKey(key)||link.sourceKey.toLowerCase()===key.toLowerCase()||
        typeof password!=='string'||typeof link.verifyPeer!=='function'||typeof link.remoteCommand!=='function')
      throw error('Verify the complete USB source and target identities before changing forwarding.','Verify forwarding target');
    const source=link.sourceKey.toLowerCase(),target=key.toLowerCase();
    const active=()=>{
      let allowed=true;
      if(canContinue)try{allowed=canContinue()===true;}catch(_){allowed=false;}
      if(!allowed)throw Object.assign(error('Forwarding pause cancelled; no further setting was changed.',
        'Cancel target forwarding pause'),{otaQuietCancelled:true});
      if(link.failure||link.running===false||link.opened===false||link.portOpened===false||link.serving||link.seeder||link.folderUnknown||
          link.relays!==undefined&&(!Array.isArray(link.relays)||link.relays.length)||
          !fullKey(link.sourceKey)||link.sourceKey.toLowerCase()!==source||
          typeof link.knownRepeater==='string'&&link.knownRepeater.toLowerCase()!==target||
          typeof link.target==='string'&&link.target.toLowerCase()!==target)
        throw error('Forwarding changes require the same healthy, idle USB source and direct target.','Verify forwarding target');
    };
    const read=async command=>{
      active();let reply;
      try{reply=await link.remoteCommand(target,password,command);}
      catch(cause){
        if(cause&&typeof cause==='object'){
          if(!cause.otaCommand)cause.otaCommand=command;
          if(!cause.otaEndpoint)cause.otaEndpoint='remote target';
          if(!cause.otaStep)cause.otaStep='Temporarily pause or restore target forwarding';
          cause.otaQuietForwarding=true;
        }
        throw cause;
      }
      active();return reply;
    };
    const prove=async()=>{
      active();await link.verifyPeer(target,password);active();
      if(bare(await read('get public.key')).toLowerCase()!==target)
        throw error('The selected forwarding target did not prove its complete public key.','get public.key','remote target');
    };
    active();return {source,target,active,read,prove};
  }
  function belongs(value,proof){
    if(value.source!==proof.source||value.target!==proof.target)
      throw error('The forwarding recovery record belongs to another USB source or target; no setting was changed.',
        'Validate forwarding recovery record');
  }
  function unsupported(reply,setting){
    const text=bare(reply).toLowerCase();
    return ['unsupported','error: unsupported','err unsupported','err: unsupported','error, unsupported on this role',
      'unknown command','unknown config','unknown config: '+setting,'error: unknown config: '+setting,
      'error: unknown setting: '+setting].includes(text);
  }
  function state(reply,setting){
    const text=bare(reply).toLowerCase();
    if(text==='on'||text==='off')return text;
    throw error('Cannot confirm target '+setting+' as on or off; no guessed setting was used.','get '+setting,'remote target');
  }
  function samePlan(value,plan){
    return value.source===plan.source&&value.target===plan.target&&value.setting===plan.setting&&value.original===plan.original;
  }
  function planned(value,proof){
    if(!value||value.version!==1||value.state!=='planned'||!settings.has(value.setting)||!['on','off'].includes(value.original))
      throw error('The forwarding pause plan is invalid; no setting was changed.','Validate forwarding pause plan');
    belongs(value,proof);return value;
  }
  async function confirm(proof,setting,wanted){
    let observed=null,cause=null;
    for(let attempt=0;attempt<3;attempt++){
      try{observed=state(await proof.read('get '+setting),setting);if(observed===wanted)return {confirmed:true,observed};}
      catch(e){
        cause=e;
        if(e?.otaBudgetExhausted||Number.isFinite(e?.otaAttempt)&&Number.isFinite(e?.otaMaxAttempts)&&
            e.otaMaxAttempts>0&&e.otaAttempt>=e.otaMaxAttempts||Number.isFinite(e?.attempts)&&e.attempts>=7)break;
        try{proof.active();}catch(_){break;}
      }
    }
    return {confirmed:false,observed,error:cause};
  }
  function pending(value,progress,cause){
    progress?.('Target forwarding restoration remains pending. Reconnect the same radio and use Restore forwarding; no setter was repeated.');
    return {state:'pending',record:copy(value),...(cause?{error:cause}:{})};
  }
  async function plan(link,key,password,{store,progress,canContinue}={}){
    return exclusive(store,async()=>{
      const proof=binding(link,key,password,canContinue),previous=load(store);
      if(previous){belongs(previous,proof);throw error('Restore the previous forwarding setting before preparing another pause.','Validate forwarding recovery record');}
      await proof.prove();
      let setting='radio.flood',reply=await proof.read('get '+setting);
      if(unsupported(reply,setting)){
        setting='repeat';reply=await proof.read('get '+setting);
        if(unsupported(reply,setting)){
          progress?.('This target has no supported repeater-forwarding controls; forwarding was left unchanged.');
          return {state:'skipped',reason:'Forwarding controls are unsupported',source:proof.source,target:proof.target};
        }
      }
      const original=state(reply,setting);
      progress?.(original==='off'?'Target forwarding is already off; it will not be enabled later.':
        setting==='repeat'?'Older target: the update can pause forwarding on both radio profiles, then restore it.':
          'The update can pause primary-profile flood forwarding, then restore it.');
      return {version:1,source:proof.source,target:proof.target,setting,original,state:'planned'};
    });
  }
  async function pause(link,key,password,value,{store,progress,radioVerified,canContinue}={}){
    return exclusive(store,async()=>{
      if(radioVerified!==true)throw error('Verify the update radio link before pausing forwarding.','Verify update radio link');
      const proof=binding(link,key,password,canContinue);
      if(value?.state==='skipped'){
        belongs(value,proof);return {state:'skipped',reason:value.reason};
      }
      const selected=planned(value,proof);let previous=load(store);
      if(previous){belongs(previous,proof);if(!samePlan(previous,selected))throw error('Another forwarding pause is pending; no setting was changed.','Validate forwarding recovery record');}
      await proof.prove();
      if(previous){
        if(previous.state==='restore-pending')return pending(previous,progress);
        const checked=await confirm(proof,selected.setting,'off');
        if(checked.confirmed){previous={...previous,state:'paused'};save(store,previous,proof.active);return {state:'paused',record:copy(previous)};}
        return pending(previous,progress,checked.error);
      }
      const current=state(await proof.read('get '+selected.setting),selected.setting);
      if(current!==selected.original)throw error('Target forwarding changed since preparation; no setting was changed.','get '+selected.setting,'remote target');
      if(current==='off')return {state:'already-off'};
      previous=load(store);
      if(previous)throw error('Another forwarding recovery record appeared; no setting was changed.','Validate forwarding recovery record');
      const saved={version:1,source:proof.source,target:proof.target,setting:selected.setting,original:'on',state:'pause-pending',restoreAttempted:false};
      save(store,saved,proof.active);
      progress?.(selected.setting==='repeat'?'Pausing target forwarding on both radio profiles; configuration replies stay enabled.':
        'Pausing target primary-profile flood forwarding; configuration replies stay enabled.');
      proof.active();if(!equal(load(store),saved))throw error('Forwarding recovery ownership changed; no setting was changed.','Validate forwarding recovery record');
      let cause=null;
      try{const reply=bare(await proof.read('set '+selected.setting+' off'));if(!/^OK(?:\s|$)/i.test(reply))cause=error('The forwarding pause was not acknowledged.','set '+selected.setting+' off','remote target');}
      catch(e){cause=e;}
      const checked=await confirm(proof,selected.setting,'off');
      if(!checked.confirmed)return pending(saved,progress,checked.error||cause);
      const paused={...saved,state:'paused'};if(!equal(load(store),saved))throw error('Forwarding recovery ownership changed after its pause.','Validate forwarding recovery record');
      save(store,paused,proof.active);return {state:'paused',record:copy(paused)};
    });
  }
  async function restore(link,key,password,{store,progress,retry=false}={}){
    return exclusive(store,async()=>{
      if(typeof retry!=='boolean')throw error('An explicit restore retry must be confirmed by the user.','Validate forwarding restore retry');
      const saved=load(store);if(!saved)return {state:'none'};
      const proof=binding(link,key,password);belongs(saved,proof);await proof.prove();
      const current=state(await proof.read('get '+saved.setting),saved.setting);
      if(!equal(load(store),saved))throw error('Forwarding recovery ownership changed; no setting was changed.','Validate forwarding recovery record');
      if(current===saved.original){clear(store);progress?.('Target forwarding is restored to its original setting.');return {state:'restored'};}
      if(saved.restoreAttempted&&!retry)return pending(saved,progress);
      const restoring={...saved,state:'restore-pending',restoreAttempted:true};save(store,restoring);
      progress?.('Restoring the target forwarding setting saved before the update.');
      proof.active();if(!equal(load(store),restoring))throw error('Forwarding recovery ownership changed; no setting was changed.','Validate forwarding recovery record');
      let cause=null;
      try{const reply=bare(await proof.read('set '+saved.setting+' on'));if(!/^OK(?:\s|$)/i.test(reply))cause=error('Forwarding restoration was not acknowledged.','set '+saved.setting+' on','remote target');}
      catch(e){cause=e;}
      const checked=await confirm(proof,saved.setting,'on');
      if(!checked.confirmed)return pending(restoring,progress,checked.error||cause);
      if(!equal(load(store),restoring))throw error('Forwarding recovery ownership changed after restoration.','Validate forwarding recovery record');
      clear(store);progress?.('Target forwarding is restored to its original setting.');return {state:'restored'};
    });
  }
  return {plan,pause,restore};
});
