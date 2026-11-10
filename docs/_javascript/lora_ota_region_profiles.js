/* Browser OTA RF suggestions. Community presets identify a profile, not a country licence. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.LoraOtaRegionProfiles=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const communitySource='https://api.meshcore.nz/api/v1/config';
  const bandwidths=[7.8,10.4,15.6,20.8,31.25,41.7,62.5,125,250,500];
  const validTuple=value=>Array.isArray(value)&&value.length===4&&value.every(n=>typeof n==='number'&&Number.isFinite(n))&&
    value[0]>=150&&value[0]<=2500&&bandwidths.includes(value[1])&&Number.isInteger(value[2])&&value[2]>=5&&value[2]<=12&&
    Number.isInteger(value[3])&&value[3]>=4&&value[3]<=8;
  const same=(a,b)=>a.every((n,i)=>Math.abs(n-b[i])<=(i===0?.001:.00001)+1e-9);
  function fitsBand(profile){
    return !!profile&&Array.isArray(profile.bounds)&&profile.bounds.length===2&&
      profile.bounds.every(Number.isFinite)&&profile.bounds[0]<profile.bounds[1]&&
      validTuple([profile.frequency,profile.bandwidth,profile.sf,profile.cr])&&
      profile.frequency-profile.bandwidth/2000>=profile.bounds[0]-1e-9&&
      profile.frequency+profile.bandwidth/2000<=profile.bounds[1]+1e-9;
  }
  function profile(id,label,frequency,bandwidth,bounds,ambiguous=false){
    const value={id,label,frequency,bandwidth,sf:5,cr:7,bounds:Object.freeze(bounds),ambiguous};
    if(!fitsBand(value))throw Error('OTA profile exceeds its nominal band: '+id);
    return Object.freeze(value);
  }
  const profiles=Object.freeze([
    profile('eu868','EU / UK 868 MHz',869.525,250,[869.4,869.65]),
    profile('eu433','EU 433 MHz',433.925,250,[433.05,434.79]),
    profile('us-ca','US / Canada',909.950,500,[902,928],true),
    profile('costa-rica','Costa Rica',909.950,250,[902,915]),
    profile('australia','Australia',921.500,250,[915,928]),
    profile('australia-brazil','Australia / Brazil',921.500,500,[915,928],true),
    profile('brazil','Brazil',921.500,500,[915,928]),
    profile('thailand','Thailand',921.500,250,[920,925]),
    profile('vietnam','Vietnam',921.500,250,[920,923])
  ]);
  // Snapshot of community suggestions, checked 10 November 2026. Match all
  // four fields: an arbitrary frequency in a broad allocation is not evidence
  // of a country, and shared suggestions must keep their ambiguity visible.
  const matches=[
    ['eu868',[869.618,62.5,8,8]],
    ['eu868',[869.618,62.5,7,5]],
    ['eu868',[869.618,62.5,7,6]],
    ['eu868',[869.525,250,11,5]],
    ['eu868',[869.432,62.5,7,5]],
    ['eu433',[433.650,250,11,5]],
    ['eu433',[433.650,62.5,8,8]],
    ['eu433',[433.375,62.5,9,6]],
    ['us-ca',[910.525,62.5,7,5]],
    ['us-ca',[927.875,62.5,7,5]],
    ['costa-rica',[910.525,125,11,5]],
    ['australia',[915.800,250,10,5]],
    ['australia',[916.575,62.5,7,7]],
    ['australia',[915.075,125,9,5]],
    ['australia',[923.125,62.5,8,5]],
    ['australia-brazil',[923.125,62.5,8,8]],
    ['thailand',[921.000,62.5,7,5]],
    ['vietnam',[920.250,62.5,8,5]],
    ['vietnam',[920.250,250,11,5]]
  ];
  // These published board identities select an SX127x driver. Generic ESP32,
  // T-Beam and universal OTA IDs can also identify an SX126x radio, so they
  // must not imply a minimum SF. SX127x explicit-header packets need SF7+.
  const sx127xSourceBoards=new Set([
    'heltec v2','lilygo t-lora v2.1-1.6','geprc linkflow 900m tx',
    'lilygo t3 s3 sx127x','lilygo t-beam sx127x'
  ]);
  const sx127xTargetIds=new Set([
    'heltec_v2','lilygo_tlora_v2_1_1_6','lilygo_t3s3_sx1276','tbeam_sx1276','geprc_linkflow_900'
  ]);
  const identity=value=>typeof value==='string'?value.trim().toLowerCase():'';
  function compatibleDefault(value,hardware){
    if(!value||value.sf!==5||!['region','community'].includes(value.selection))return value;
    if(!sx127xSourceBoards.has(identity(hardware?.sourceBoard))&&!sx127xTargetIds.has(identity(hardware?.targetHwId)))return value;
    return {...value,sf:7,hardwareAdjusted:true,hardwareReason:'SX127x radios require SF7 or higher'};
  }
  function copy(profile,selection){
    return {...profile,bounds:profile.bounds.slice(),selection,matched:selection==='community'};
  }
  function recommend(savedTuple,options={}){
    if(!options||typeof options!=='object'||Array.isArray(options))return null;
    const region=options.region===undefined?'auto':options.region;
    if(region!=='auto'){
      const selected=profiles.find(value=>value.id===region);
      return selected?compatibleDefault(copy(selected,'region'),options.hardware):null;
    }
    if(!validTuple(savedTuple))return null;
    const match=matches.find(([,tuple])=>same(savedTuple,tuple));
    if(match)return compatibleDefault(copy(profiles.find(value=>value.id===match[0]),'community'),options.hardware);
    return {id:'custom',label:'Current radio settings',frequency:savedTuple[0],bandwidth:savedTuple[1],
      sf:savedTuple[2],cr:savedTuple[3],selection:'saved',matched:false,ambiguous:false};
  }
  return {profiles,recommend,validTuple,fitsBand,communitySource};
});
