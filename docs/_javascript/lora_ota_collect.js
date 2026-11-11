/* Explicit, bounded collection of cached neighbor data for admin route hints. */
(function(root,factory){
  const node=typeof module==='object'&&module.exports;
  const api=factory(node?require('./lora_ota_discovery.js'):root.LoraOtaDiscovery);
  if(node)module.exports=api;else root.LoraOtaCollect=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(discovery){
  'use strict';
  const KEY=/^[0-9a-f]{64}$/i;
  function validateKey(k){if(typeof k!=='string'||!KEY.test(k))throw Error('Full 64-character public keys are required');return k.toLowerCase();}
  async function collectAdminNeighbors(options){
    const {link,allowlist}=options||{},maxQueries=options?.maxQueries??12,maxAttempts=options?.maxAttempts??24,
      maxRelays=options?.maxRelays??3,maxAgeSeconds=options?.maxAgeSeconds??900,minSnrDb=options?.minSnrDb,
      timeoutMs=options?.timeoutMs??180000,progress=options?.progress??(()=>{}),now=options?.now??Date.now;
    if(!link||!link.running||!link.aclOnly||!link.supportsRoutedPath||typeof link.withAdmin!=='function'||
       typeof link.localCommand!=='function'||typeof link.job!=='function'||!Array.isArray(allowlist)||allowlist.length<2||allowlist.length>64)
      throw Error('An open ACL-enabled routed repeater and authorized allowlist are required');
    if(!Number.isInteger(maxQueries)||maxQueries<1||maxQueries>16||!Number.isInteger(maxAttempts)||maxAttempts<1||maxAttempts>32||
       !Number.isInteger(maxRelays)||maxRelays<0||maxRelays>8||!Number.isInteger(maxAgeSeconds)||maxAgeSeconds<1||
       !Number.isFinite(minSnrDb)||minSnrDb< -32||minSnrDb>31.75||!Number.isInteger(timeoutMs)||timeoutMs<1000||timeoutMs>1800000)
      throw Error('Invalid collection limits');
    const source=validateKey(link.sourceKey),keys=allowlist.map(validateKey),known=new Set(keys);
    if(known.size!==keys.length||!known.has(source))throw Error('Allowlist must uniquely include the source key');
    const byPrefix=new Map(),routePrefixes=new Map();
    for(const k of keys){const p=k.slice(0,8),r=k.slice(0,6);byPrefix.set(p,(byPrefix.get(p)||[]).concat(k));routePrefixes.set(r,(routePrefixes.get(r)||[]).concat(k));}
    if(routePrefixes.get(source.slice(0,6)).length!==1)throw Error('Source route prefix is ambiguous');
    return link.withAdmin(async()=>{
      const start=now(),deadline=start+timeoutMs;
      if(!Number.isSafeInteger(start)||!Number.isSafeInteger(deadline))throw Error('Invalid collection clock');
      let selected=[],owned=false,queries=0,attempts=0,truncated=false,error=null;
      const observations=[],unavailable=[],queued=new Set(),done=new Set([source]),queue=[];
      const expected=p=>'OTApeer path='+(p.length?p.join(','):'off');
      const checkTime=()=>{if(now()>=deadline){truncated=true;return false;}return true;};
      async function assertPath(){if(await link.localCommand('ota.peer path')!==expected(selected))throw Error('The source OTA path changed; it was not overwritten');}
      async function idle(){
        const status=await link.localCommand('ota.peer status');
        if(!/^OTApeer v=1 session=[01] busy=0 peers=\d+$/.test(status))throw Error('Source OTA admin is busy or unavailable');
      }
      async function select(path){
        await idle();await assertPath();
        if(path.join(',')===selected.join(','))return;
        // Mark ownership before issuing the write; the device may apply it even
        // when its reply is lost. Readback must exactly match before continuing.
        const previous=selected;selected=path.slice();owned=true;let result;
        try{result=await link.localCommand('ota.peer path '+(path.length?path.join(','):'off'));}
        catch(_){result=await link.localCommand('ota.peer path');}
        if(result!==expected(path)){
          // A rejected/lost write may leave our previous route intact. Keep
          // ownership of that exact route so finally can restore it safely.
          const actual=await link.localCommand('ota.peer path');
          if(actual===expected(previous))selected=previous;
          throw Error('Could not confirm the source OTA path');
        }
      }
      function addRows(observer,reply,path){
        const rows=discovery.parseNeighbors(observer,reply,now());
        observations.push(...rows);
        rows.sort((a,b)=>b.snrQuarterDb-a.snrQuarterDb||a.prefix.localeCompare(b.prefix));
        for(const row of rows){
          const candidates=byPrefix.get(row.prefix)||[];
          if(candidates.length!==1||row.ageSeconds>maxAgeSeconds||row.snrQuarterDb/4<minSnrDb)continue;
          const candidate=candidates[0];
          if(candidate===source||candidate===observer||done.has(candidate)||routePrefixes.get(candidate.slice(0,6)).length!==1)continue;
          const nextPath=observer===source?[]:[...path,observer];
          if(nextPath.length>maxRelays||nextPath.includes(candidate))continue;
          const id=candidate+'@'+nextPath.join(',');if(queued.has(id))continue;
          queued.add(id);queue.push({candidate,path:nextPath});
        }
      }
      try{
        if(await link.localCommand('ota.peer path')!=='OTApeer path=off')throw Error('A source OTA path is already configured; it was not changed');
        await idle();
        // checkClock may advance a stale source clock, as normal admin requests
        // require a recent timestamp. No radio or OTA settings are altered.
        await link.checkClock();
        if(!checkTime())throw Error('Neighbor collection timed out');
        addRows(source,await link.localCommand('neighbors'),[]);queries++;progress({phase:'neighbors',key:source,queries,attempts});
        while(queue.length&&queries<maxQueries&&attempts<maxAttempts&&checkTime()){
          const {candidate,path}=queue.shift();if(done.has(candidate))continue;
          await select(path.map(k=>k.slice(0,6).toUpperCase()));
          if(!checkTime())break;
          attempts++;
          try {await link.job('ota.peer acl '+candidate);}
          catch(e){
            // A timeout is ambiguous; never continue or change the route while
            // the source might still be executing an earlier admin request.
            if(!/permission denied|not authorized|access denied|acl denied/i.test(String(e?.message||e)))throw e;
            unavailable.push(candidate);done.add(candidate);continue;
          }
          await assertPath();if(!checkTime())break;
          const reply=await link.job('ota.peer cmd.acl '+candidate+' neighbors');
          await assertPath();queries++;done.add(candidate);addRows(candidate,reply,path);
          progress({phase:'neighbors',key:candidate,queries,attempts});
        }
        if(queue.length&&(queries>=maxQueries||attempts>=maxAttempts||now()>=deadline))truncated=true;
      }catch(e){error=e;}
      if(owned){
        try{
          await idle();await assertPath();
          if(selected.length){const reply=await link.localCommand('ota.peer path off');if(reply!=='OTApeer path=off')throw Error('Path restore was not confirmed');selected=[];}
        }catch(e){throw Error((error?String(error.message)+'; ':'')+'Source path could not be safely restored: '+String(e.message));}
      }
      if(error)throw error;
      return {source,observations,queries,attempts,truncated,unavailable};
    });
  }
  return {collectAdminNeighbors};
});
