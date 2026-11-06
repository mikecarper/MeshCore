/* Pure scoring of explicitly authorized physical admin routes. No radio I/O. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;else root.LoraOtaDiscovery=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const KEY=/^[0-9a-f]{64}$/i, PREFIX=/^[0-9a-f]{8}$/i;
  function key(value) {
    if(typeof value!=='string'||!KEY.test(value))throw Error('Full 64-character public keys are required');
    return value.toLowerCase();
  }
  function parseNeighbors(observer,reply,observedAtMs=Date.now()) {
    observer=key(observer);
    if(typeof reply!=='string'||!Number.isSafeInteger(observedAtMs)||observedAtMs<0)throw Error('Invalid neighbors reply');
    const lines=reply.trim().replace(/^>\s*/, '').split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
    if(lines.length===1&&lines[0]==='-none-')return [];
    if(!lines.length||lines.length>8)throw Error('Invalid neighbors reply');
    return lines.map(line=>{
      const m=/^([0-9a-f]{8}):(\d+):(-?\d+)$/i.exec(line);
      if(!m)throw Error('Invalid neighbors row');
      const ageSeconds=Number(m[2]),snrQuarterDb=Number(m[3]);
      if(!Number.isSafeInteger(ageSeconds)||ageSeconds<0||!Number.isInteger(snrQuarterDb)||snrQuarterDb < -128||snrQuarterDb>127)
        throw Error('Invalid neighbors measurement');
      return {observer,prefix:m[1].toLowerCase(),ageSeconds,snrQuarterDb,observedAtMs};
    });
  }
  function prepare(options) {
    const {allowlist,observations,source:src,target:dst}=options||{};
    const source=key(src),target=key(dst);
    if(source===target||!Array.isArray(allowlist)||allowlist.length<2||allowlist.length>64||!Array.isArray(observations)||observations.length>4096)
      throw Error('Invalid route candidates or observations');
    const keys=allowlist.map(key),known=new Set(keys);
    if(known.size!==keys.length||!known.has(source)||!known.has(target))throw Error('Allowlist must uniquely contain source and target');
    const maxAgeSeconds=options.maxAgeSeconds===undefined?900:options.maxAgeSeconds;
    const minSnrDb=options.minSnrDb,maxRelays=options.maxRelays===undefined?8:options.maxRelays,limit=options.limit===undefined?10:options.limit,
      nowMs=options.nowMs===undefined?Date.now():options.nowMs;
    if(!Number.isInteger(maxAgeSeconds)||maxAgeSeconds<1||!Number.isFinite(minSnrDb)||minSnrDb < -32||minSnrDb>31.75||
       !Number.isInteger(maxRelays)||maxRelays<0||maxRelays>8||!Number.isInteger(limit)||limit<1||limit>20||!Number.isSafeInteger(nowMs)||nowMs<0)
      throw Error('Invalid route quality or search limits');
    // The control protocol uses three-byte path prefixes. Exclude all keys
    // with a known route-prefix collision, even if their four-byte neighbor
    // prefixes differ. A collision with an unknown node cannot be ruled out.
    const byRoute=new Map(),byNeighbor=new Map();
    for(const k of keys){const p=k.slice(0,6),n=k.slice(0,8);byRoute.set(p,(byRoute.get(p)||[]).concat(k));byNeighbor.set(n,(byNeighbor.get(n)||[]).concat(k));}
    const ambiguous=new Set(keys.filter(k=>byRoute.get(k.slice(0,6)).length!==1||byNeighbor.get(k.slice(0,8)).length!==1));
    if(ambiguous.has(source)||ambiguous.has(target))throw Error('Source or target has an ambiguous route prefix');
    const rejects={stale:0,unknown:0,ambiguous:ambiguous.size,invalid:0}, directed=new Map();
    for(const row of observations){
      if(!row||typeof row.observer!=='string'||!KEY.test(row.observer)||typeof row.prefix!=='string'||!PREFIX.test(row.prefix)||
         !Number.isSafeInteger(row.ageSeconds)||row.ageSeconds<0||!Number.isSafeInteger(row.observedAtMs)||row.observedAtMs<0||row.observedAtMs>nowMs||
         !Number.isInteger(row.snrQuarterDb)||row.snrQuarterDb< -128||row.snrQuarterDb>127){rejects.invalid++;continue;}
      const observer=row.observer.toLowerCase(),matches=byNeighbor.get(row.prefix.toLowerCase())||[];
      if(!known.has(observer)||matches.length!==1){rejects.unknown++;continue;}
      const neighbor=matches[0];
      if(ambiguous.has(observer)||ambiguous.has(neighbor)){rejects.ambiguous++;continue;}
      if(observer===neighbor){rejects.invalid++;continue;}
      const ageSeconds=row.ageSeconds+Math.floor((nowMs-row.observedAtMs)/1000);
      if(ageSeconds>maxAgeSeconds){rejects.stale++;continue;}
      // SNR belongs to the receiver (observer), so this edge is neighbor -> observer.
      const id=neighbor+'>'+observer,old=directed.get(id);
      if(!old||ageSeconds<old.ageSeconds||(ageSeconds===old.ageSeconds&&row.snrQuarterDb<old.snrQuarterDb))
        directed.set(id,{from:neighbor,to:observer,ageSeconds,snrQuarterDb:row.snrQuarterDb});
    }
    const adjacency=new Map(keys.filter(k=>!ambiguous.has(k)).map(k=>[k,[]]));
    for(const edge of directed.values()){
      const back=directed.get(edge.to+'>'+edge.from);
      if(!back||edge.snrQuarterDb/4<minSnrDb||back.snrQuarterDb/4<minSnrDb)continue;
      adjacency.get(edge.from).push({from:edge.from,to:edge.to,snrDb:edge.snrQuarterDb/4,
        reverseSnrDb:back.snrQuarterDb/4,ageSeconds:edge.ageSeconds,reverseAgeSeconds:back.ageSeconds});
    }
    for(const edges of adjacency.values())edges.sort((a,b)=>a.to.localeCompare(b.to));
    return {source,target,known,adjacency,rejects,maxRelays,limit};
  }
  function makeRoute(path,links) {
    return {source:path[0],target:path[path.length-1],relays:path.slice(1,-1),hops:path.length-2,linkCount:links.length,
      bottleneckSnrDb:Math.min(...links.flatMap(x=>[x.snrDb,x.reverseSnrDb])),links};
  }
  function rankRoutes(options) {
    const p=prepare(options),maxEdges=p.maxRelays+1;
    let layer=[{path:[p.source],links:[]}],examined=0;const routes=[];
    for(let edges=1;edges<=maxEdges&&layer.length;edges++){
      const next=[],found=[];
      for(const current of layer)for(const edge of p.adjacency.get(current.path.at(-1))||[]){
        if(++examined>100000)throw Error('Route search is too large; narrow the allowlist');
        if(current.path.includes(edge.to))continue;
        const path=[...current.path,edge.to],links=[...current.links,edge];
        if(edge.to===p.target)found.push(makeRoute(path,links));
        else if(edges<maxEdges)next.push({path,links});
      }
      found.sort((a,b)=>b.bottleneckSnrDb-a.bottleneckSnrDb||a.relays.join('').localeCompare(b.relays.join('')));
      routes.push(...found);
      if(routes.length>=p.limit)break;
      layer=next;
    }
    return {routes:routes.slice(0,p.limit),rejected:p.rejects};
  }
  function validateManualRoute(options,relays){
    const p=prepare(options);
    if(!Array.isArray(relays)||relays.length>p.maxRelays)throw Error('Invalid manual relay path');
    const path=[p.source,...relays.map(key),p.target];
    if(new Set(path).size!==path.length||path.some(k=>!p.known.has(k)))throw Error('Manual path must use distinct allowlisted keys');
    const links=[];
    for(let i=0;i<path.length-1;i++){
      const edge=(p.adjacency.get(path[i])||[]).find(x=>x.to===path[i+1]);
      if(!edge)throw Error('Manual path has an unknown, stale, ambiguous, or weak bidirectional link');
      links.push(edge);
    }
    return makeRoute(path,links);
  }
  return {parseNeighbors,rankRoutes,validateManualRoute};
});
