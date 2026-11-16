/* Bounded, explicitly requested discovery on one private IPv4 /24. */
(function(root, factory) {
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaWifiScan=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const platformHelp={
    apple:'On iPhone or iPad: open Settings > Wi-Fi, tap the ⓘ next to the connected network, and find IP Address under IPv4 Address.',
    android:'On Android: open Settings > Network & internet > Internet or Wi-Fi, tap the connected network or its gear, and look for IP address. Names vary by phone.',
    mac:'On macOS: open System Settings > Network, select the connected Wi-Fi service, then Details > TCP/IP and find IPv4 Address.',
    windows:'On Windows: open Settings > Network & internet > Wi-Fi, select the connected network, and find its IPv4 address. You can also run ipconfig.',
    linux:'On Linux: open your connected Wi-Fi network’s settings and find IPv4 Address, or run ip -4 addr show.',
    unknown:'Choose your phone or computer above for instructions to find its Wi-Fi IPv4 address.'
  };
  function detectPlatform(nav={}){
    const ua=String(nav.userAgent||''),platform=String(nav.userAgentData?.platform||nav.platform||'');
    if(/iPhone|iPad|iPod/i.test(ua)||(/Mac/i.test(platform+' '+ua)&&Number(nav.maxTouchPoints)>1))return 'apple';
    if(/Android/i.test(ua+' '+platform))return 'android';
    if(/Windows/i.test(ua+' '+platform))return 'windows';
    if(/Mac/i.test(ua+' '+platform))return 'mac';
    if(/Linux/i.test(ua+' '+platform))return 'linux';
    return 'unknown';
  }
  function prefix(input){
    const value=String(input||'').trim().replace(/\/24$/,'');
    const parts=value.split('.');
    if(parts.length===4){
      const last=parts.pop();
      if(!/^(?:0|[1-9]\d{0,2})$/.test(last)||Number(last)>255)
        throw Error('Enter a private IPv4 address or network prefix.');
    }
    if(parts.length!==3||parts.some(p=>!/^(?:0|[1-9]\d{0,2})$/.test(p)||Number(p)>255))
      throw Error('Enter a private network prefix such as 192.168.1.0/24.');
    const [a,b]=parts.map(Number);
    if(!(a===10||(a===172&&b>=16&&b<=31)||(a===192&&b===168)))
      throw Error('Only private networks can be scanned.');
    return parts.join('.');
  }
  // No STUN servers, media permission, or public-address lookup. Browsers
  // commonly mask host candidates with mDNS, so an empty result is normal.
  async function localPrefixes({Peer=globalThis.RTCPeerConnection,timeout=1500}={}){
    if(typeof Peer!=='function')return [];
    let peer,timer;
    const found=new Set();
    try{
      peer=new Peer({iceServers:[]});
      const complete=new Promise(resolve=>{
        peer.onicecandidate=event=>{
          if(!event.candidate){resolve();return;}
          const fields=String(event.candidate.candidate||'').split(/\s+/);
          if(fields[6]!=='typ'||fields[7]!=='host')return;
          const ip=fields[4],parts=ip?.split('.')||[];
          if(parts.length!==4||!parts.every(p=>/^(?:0|[1-9]\d{0,2})$/.test(p)&&Number(p)<256))return;
          try{found.add(prefix(parts.slice(0,3).join('.')));}catch(_){}
        };
      });
      const gather=(async()=>{
        peer.createDataChannel('meshcore-local-discovery');
        const offer=await peer.createOffer();
        await peer.setLocalDescription(offer);
        await complete;
      })();
      await Promise.race([gather,new Promise(resolve=>{timer=setTimeout(resolve,timeout);})]);
    }catch(_){}finally{clearTimeout(timer);try{peer?.close();}catch(_){}}
    return [...found].sort();
  }
  async function scan(input,{fetch:fetcher=globalThis.fetch,signal,onProgress=()=>{},onFound=()=>{},
      timeout=1500,concurrency=8}={}){
    const network=prefix(input);
    if(typeof fetcher!=='function'||!Number.isInteger(concurrency)||concurrency<1||concurrency>8||
        !Number.isFinite(timeout)||timeout<100||timeout>5000)throw Error('Invalid discovery settings.');
    if(signal?.aborted)throw new DOMException('Scan canceled','AbortError');
    let next=1,done=0;
    const found=[];
    async function worker(){
      while(next<=254&&!signal?.aborted){
        const address=network+'.'+next++,controller=new AbortController();
        const cancel=()=>controller.abort();
        signal?.addEventListener('abort',cancel,{once:true});
        const timer=setTimeout(cancel,timeout);
        try{
          const response=await fetcher('http://'+address+':5005/ota-info',{
            method:'GET',mode:'cors',credentials:'omit',cache:'no-store',redirect:'error',
            targetAddressSpace:'local',signal:controller.signal
          });
          // Tiny bounded responses only. Never follow redirects or accept an
          // arbitrary web server merely because its port is reachable.
          if(response.ok&&response.body){
            const reader=response.body.getReader();let bytes=0,body='';
            try{
              const decoder=new TextDecoder();
              for(;;){const part=await reader.read();if(part.done)break;
                bytes+=part.value.byteLength;if(bytes>512)throw Error('Discovery response too large.');
                body+=decoder.decode(part.value,{stream:true});
              }
              body+=decoder.decode();
              if(JSON.parse(body)?.meshcoreOta===1){found.push(address);onFound(address);}
            }finally{try{await reader.cancel();}catch(_){} reader.releaseLock();}
          }
        }catch(_){}finally{
          clearTimeout(timer);signal?.removeEventListener('abort',cancel);done++;onProgress(done,254);
        }
      }
    }
    await Promise.all(Array.from({length:concurrency},worker));
    if(signal?.aborted)throw new DOMException('Scan canceled','AbortError');
    return found;
  }
  return {prefix,scan,localPrefixes,detectPlatform,platformHelp};
});
