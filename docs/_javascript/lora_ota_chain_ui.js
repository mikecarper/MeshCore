/* Conservative live checks for the browser version-chain interface. */
(function(root,factory){
  const node=typeof module==='object'&&module.exports;
  const api=factory(node?require('./lora_ota_checks.js'):root.LoraOtaChecks,
    node?require('./lora_ota_delta.js'):root.MeshCoreLoraDelta,
    node?require('./lora_ota_session.js'):root.LoraOtaSession);
  if(node)module.exports=api;else root.LoraOtaChainUi=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(checks,delta,direct){
  'use strict';
  const enc=new TextEncoder();
  function contains(bytes,text){
    const needle=enc.encode(text);outer:for(let i=0;i+needle.length<=bytes.length;i++){
      for(let j=0;j<needle.length;j++)if(bytes[i+j]!==needle[j])continue outer;
      return true;
    }return false;
  }
  function sameRadio(a,b){return a.length===b.length&&a.every((v,i)=>Math.abs(v-b[i])<=0.001);}
  function equalLayout(a,b){return a.app===b.app&&a.end===b.end&&a.ceiling===b.ceiling&&a.flags===b.flags;}
  function evidence(image,live,baseImage,intermediate=false){
    if(!(image instanceof Uint8Array)||!(baseImage instanceof Uint8Array))throw Error('Firmware evidence requires image bytes');
    if(!contains(image,'OTA: status')||!contains(image,'get <id> flash | install'))
      throw Error('This firmware lacks the application LoRa OTA receiver marker');
    if(intermediate && (!contains(image,'ota installhash <64-hex-container-sha256>') ||
        !contains(image,'tempradio.ifidle ')))
      throw Error('An intermediate must include verified install and atomic temporary-radio rearm support');
    if(live.nrf){
      if(!contains(image,'invalid in-place patch geometry')) throw Error('This nRF52 image lacks the in-place apply marker');
      const previous=delta.nrfLayout(baseImage), next=delta.nrfLayout(image);
      if(!equalLayout(previous,next))throw Error('The nRF52 intermediate uses a different flash layout');
    }
    // Image markers are evidence, not proof of live capability. Every step is
    // checked again after reboot before it can become an update source.
    return {...live,otaConfirmed:true,wireConfirmed:false,wireProfile:'legacy',deflate:false,maxBlock:1024};
  }
  async function readLive(link,key,password){
    await link.verifyPeer(key,password);
    const {status,self}=await checks.readReceiver(command=>link.remoteCommand(key,password,command));
    const version=await link.remoteCommand(key,password,'ver');
    const d=checks.destination(status,self,version);
    if(!d.nrf)await checks.ensureSequentialTarget(status,self,()=>link.remoteCommand(key,password,'get bootloader.ver'));
    if(d.maxBlock<1024)throw Error('The target block limit is too small for the browser planner');
    const caps={...d,bodyHash:d.baseHash,otaConfirmed:true,wireConfirmed:false,wireProfile:'legacy',
      deflate:false,maxBlock:1024,codecs:d.nrf?[0,1,2].filter(x=>d.codecs&(1<<x)):[0,1]};
    if(d.nrf){
      if(d.sd)throw Error('Browser-built chain packages are unsigned and cannot target SD installs');
      if(d.external){
        const qspi=await link.remoteCommand(key,password,'ota qspi');
        caps.runtimeStorage='qspi';caps.runtimeQspiApply=true;
        caps.runtimeStorageBytes=checks.qspiCapacity(status,self,qspi,d);
      }else{
        caps.runtimeStorage='internal';caps.runtimeStageCeiling=checks.internalStage(status,self,d);
      }
    }
    return caps;
  }
  function clearSchedule(value){return /^(?:>\s*)?(?:-none-|none|off|slots:\s*)$/i.test(String(value).trim());}
  function isOff(value){return /^(?:>\s*)?(?:off|inactive)$/i.test(String(value).trim());}
  function preamble(value){
    const text=String(value).trim(), match=text.match(/^(?:>\s*)?\d+(?:\.\d+)?,\d+(?:\.\d+)?,\d+,\d+,(\d+)(?:\s|$)/i) ||
      text.match(/^(?:>\s*)?TempRadio active:\s*\d+(?:\.\d+)?,\d+(?:\.\d+)?,\d+,\d+\s+\d+s left,preamble=(\d+)(?:\s|$)/i);
    if(!match || Number(match[1])<1 || Number(match[1])>65535)throw Error('Cannot confirm the active target preamble');
    return Number(match[1]);
  }
  async function activeRadio(link,key,password,expected,expectedPreamble){
    const source=direct.temp(await link.localCommand('get tempradio'));
    const reply=await link.remoteCommand(key,password,'get tempradio'), target=direct.temp(reply);
    return sameRadio(source,expected)&&sameRadio(target,expected)&&preamble(reply)===expectedPreamble;
  }
  async function rearm(link,key,password,expected,minutes,{wait=ms=>new Promise(r=>setTimeout(r,ms)),signal,preamble:targetPreamble}={}){
    if(!Number.isInteger(minutes)||minutes<1||minutes>10080)throw Error('Temporary radio duration must be 1–10080 minutes');
    if(!Number.isInteger(targetPreamble)||targetPreamble<1||targetPreamble>65535)throw Error('The original target preamble is required');
    const remote=command=>link.remoteCommand(key,password,command);
    const source=direct.temp(await link.localCommand('get tempradio'));
    if(!sameRadio(source,expected))throw Error('The source temporary radio changed; no new window was created');
    if(!isOff(await remote('get tempradio')))throw Error('The target already has an active or pending temporary radio');
    for(const cmd of ['get tempradioat','get radioat'])
      if(!clearSchedule(await remote(cmd)))throw Error('The target has scheduled or unverified radio settings');
    if(!sameRadio(direct.temp(await remote('get radio')),expected))throw Error('The target saved radio changed');
    const state=direct.download(await remote('ota status'));
    if(state.state!=='idle')throw Error('The target has an existing OTA download');
    if(signal?.aborted)throw Error('Chain stopped before radio rearm');
    // Never repeat this command after a lost reply: it may already be active.
    let uncertain=false;
    try { const reply=await remote('tempradio.ifidle '+expected.join(',')+','+minutes+','+targetPreamble);
      if(!/^\s*(?:>\s*)?OK\b/i.test(reply))throw Error('Atomic temporary-radio rearm was rejected or unsupported; rearm manually and resume: '+reply);
    }catch(error){
      if(!/timeout|timed out|disconnect|result is unknown/i.test(String(error?.message||error)))throw error;
      uncertain=true;
    }
    for(let attempt=0;attempt<23;attempt++){
      if(signal?.aborted)throw Error('Chain stopped after radio rearm');
      await wait(2000);
      try {if(await activeRadio(link,key,password,expected,targetPreamble))return true;}catch(_){/* pending or temporarily unreachable */}
    }
    throw Error(uncertain?'Radio rearm reply was lost and the window is unconfirmed':'The target temporary radio did not become active');
  }
  return {evidence,readLive,rearm,activeRadio,clearSchedule,isOff,sameRadio,preamble};
});
