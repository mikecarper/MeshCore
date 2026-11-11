/* Renew only the unchanged temporary profiles of this browser's active OTA. */
(function(root,factory){
  const node=typeof module==='object'&&module.exports;
  const api=factory(node?require('./lora_ota_radio_setup.js'):root.LoraOtaRadioSetup,
    node?require('./lora_ota_recovery.js'):root.LoraOtaRecovery);
  if(node)module.exports=api;else root.LoraOtaLease=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(setup,recovery){
  'use strict';
  const bare=value=>String(value).trim().replace(/^>\s*/,'');
  const hex=bytes=>Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('');
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  function remaining(raw){
    const text=String(raw),seconds=[...text.matchAll(/\b(\d+)s left\b/gi)],minutes=[...text.matchAll(/\((\d+) min\) left\b/gi)];
    if(seconds.length+minutes.length!==1)return null;
    const value=seconds.length?Math.max(0,Number(seconds[0][1])-1)*1000:Math.max(0,Number(minutes[0][1])-1)*60000;
    return Number.isSafeInteger(value)&&value>0&&value<=10080*60000?value:null;
  }
  function download(status,id){
    const section=/\bdownload:\s*([^|]+)/i.exec(status)?.[1];
    if(!section||/^(?:idle|failed)\b/i.test(section)||
      [...section.matchAll(/\bid=([a-f0-9]{8})\b/gi)].length!==1||
      /\bid=([a-f0-9]{8})\b/i.exec(section)[1].toLowerCase()!==id)
      throw Error('The target download changed during update-window checks');
    const progress=/\b(\d+)\/([1-9]\d*)\b/.exec(section);
    return !!progress&&/^(?:downloading|fetching|direct240(?: probe)?)\s/i.test(section.trim());
  }
  function dataReady(status,id){try{return download(status,id);}catch(_){return false;}}
  function proofMatches(proof,link,key,radio){
    return proof&&proof.source===link.sourceKey&&proof.target===key&&setup.same(proof.tuple||[],radio.tuple)&&
      proof.preamble===radio.preamble&&proof.sourceProfile===radio.sourceProfile&&proof.targetProfile===radio.targetProfile;
  }
  async function maintain(link,key,password,record,options={}){
    const now=options.now||(()=>Date.now()),wait=options.wait||sleep,threshold=options.thresholdMs??20*60000;
    const radio=recovery.channel(record,link?.sourceKey,key,now());
    if(!radio)return {state:'unavailable',record};
    if(!options.forceCheck&&radio.expiresAt-now()>threshold)return {state:'not-due',record};
    if(!link?.serving||link.failure||link.relays?.length||link.knownRepeater?.toLowerCase()!==key.toLowerCase()||
      link.otaRadioProfile!==radio.sourceProfile||link.otaTargetProfile!==radio.targetProfile)
      throw Error('Lease renewal requires the same active USB source, target and selected profiles');
    const explicit=options.explicitProfiles||record.radio.explicitProfiles||
      (proofMatches(link.otaLeaseProof,link,key,radio)?link.otaLeaseProof.explicitProfiles:[]);
    // Readbacks round frequencies and primary getters expose only effective
    // preambles. Renew only profiles this browser set explicitly; adopting an
    // unknown tuple must not silently change its underlying radio generation.
    if(explicit[0]!==true||explicit[1]!==true)
      return {state:'unavailable',record,reason:'The existing update window has no exact profile ownership proof'};
    const info=link.seeder?.info,blob=link.seeder?.blob||info?.blob;
    if(hex(info?.merkleRoot||[])!==radio.id||!(blob instanceof Uint8Array))
      throw Error('The source is serving a different package; no lease was renewed');
    const cryptoApi=globalThis.crypto?.subtle||(typeof require==='function'?require('node:crypto').webcrypto.subtle:null);
    if(!cryptoApi||hex(new Uint8Array(await cryptoApi.digest('SHA-256',blob)))!==radio.packageHash)
      throw Error('The served firmware differs from this browser update record');
    const run=async({local,remote})=>{
      const active=()=>{
        if(!link.serving||link.failure)throw link.failure||Error('Stopped; no further lease commands were sent');
        if(link.sourceKey?.toLowerCase()!==record.source.toLowerCase()||link.knownRepeater?.toLowerCase()!==key.toLowerCase()||
          link.otaRadioProfile!==radio.sourceProfile||link.otaTargetProfile!==radio.targetProfile)
          throw Error('The update source identity or selected profiles changed; lease renewal stopped');
      };
      const guarded=read=>async command=>{active();const reply=await read(command);active();return reply;};
      local=guarded(local);remote=guarded(remote);
      active();
      if(bare(await remote('get public.key')).toLowerCase()!==key.toLowerCase())throw Error('Target did not prove its full public key before lease renewal');
      if(!download(await remote('ota status'),radio.id))return {state:'waiting-data',record};
      const readers=[local,remote],profiles=[radio.sourceProfile,radio.targetProfile],states=[];
      const read=async index=>{
        active();const state=await setup.readTemporary(readers[index],profiles[index]);active();
        if(state.state==='pending')throw Error((index?'Target':'Source')+' temporary profile is pending');
        if(state.state!=='active'||!setup.same(state.tuple,radio.tuple)||state.preamble!==radio.preamble||
          profiles[index]&&setup.secondary(state.raw,'get tempradio2').mode!=='rxtx')
          throw Error((index?'Target':'Source')+' update profile changed; no further lease renewal was sent');
        return {...state,readAt:now(),remaining:remaining(state.raw)};
      };
      for(let i=0;i<2;i++)states.push(await read(i));
      if(states.some((state,i)=>profiles[i]&&setup.secondary(state.raw,'get tempradio2').automatic))
        return {state:'unavailable',record,reason:'The existing automatic preamble must retain its original update window'};
      let renewed=0;
      for(const i of [1,0]){
        const before=states[i],fallback=record.radio[i?'targetExpiresAt':'sourceExpiresAt']||radio.expiresAt;
        const expires=before.remaining===null?fallback:before.readAt+before.remaining;
        if(expires-now()>threshold)continue;
        const command=setup.commands({frequency:radio.tuple[0],bandwidth:radio.tuple[1],sf:radio.tuple[2],cr:radio.tuple[3],
          preamble:radio.preamble,minutes:120},{sourceProfile:profiles[i],targetProfile:profiles[i]}).source;
        const started=now();let acknowledged=false,error;
        active();
        try{const reply=bare(await readers[i](command));if(!/^OK(?:\s|$)/i.test(reply))throw Error(reply||'Lease renewal was not acknowledged');acknowledged=true;}
        catch(e){error=e;if(link.failure||!/timeout|timed out|result is unknown/i.test(String(e.message)))throw e;}
        let confirmed;
        for(let n=0;n<16&&now()-started<=35000;n++){
          active();
          try{
            const current=await read(i);
            if(current.remaining!==null&&current.remaining>=118*60000||current.remaining===null&&acknowledged){confirmed=current;break;}
          }catch(e){if(link.failure||!/pending|active/i.test(e.message))throw e;}
          await wait(500);
        }
        if(!confirmed)throw error||Error('The temporary lease extension could not be confirmed; its command was not repeated');
        states[i]={...confirmed,expiresAt:confirmed.remaining===null?started+119*60000:confirmed.readAt+confirmed.remaining};
        renewed++;options.progress?.((i?'Target':'USB source')+' update window extended without changing its radio channel.');
      }
      active();download(await remote('ota status'),radio.id);
      const ends=states.map((state,i)=>state.expiresAt||(state.remaining===null?
        record.radio[i?'targetExpiresAt':'sourceExpiresAt']||radio.expiresAt:state.readAt+state.remaining));
      const updated={...record,radio:{...record.radio,tuple:radio.tuple.slice(),verifiedAt:now(),
        sourceExpiresAt:ends[0],targetExpiresAt:ends[1],expiresAt:Math.min(...ends),
        explicitProfiles:[profiles[0]?true:explicit[0]===true,profiles[1]?true:explicit[1]===true]}};
      if(renewed)options.journal?.(updated);
      return {state:renewed?'renewed':'unchanged',record:updated,renewed};
    };
    if(typeof link.withOtaAdminBurst==='function')return link.withOtaAdminBurst(key,password,run,{wait:false});
    return run({local:c=>link.localCommand(c),remote:c=>link.remoteCommand(key,password,c)});
  }
  return {maintain,remaining,dataReady};
});
