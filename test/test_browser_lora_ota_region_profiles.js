'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const regions=require('../docs/_javascript/lora_ota_region_profiles.js');
const setup=require('../docs/_javascript/lora_ota_radio_setup.js');
const recommend=setup.recommendProfile;
const rf=value=>[value.frequency,value.bandwidth,value.sf,value.cr];
const table=[
  ['Australia',[915.800,250,10,5],'australia'],
  ['Australia narrow',[916.575,62.5,7,7],'australia'],
  ['Australia mid',[915.075,125,9,5],'australia'],
  ['Australia SA/WA',[923.125,62.5,8,8],'australia-brazil'],
  ['Australia QLD',[923.125,62.5,8,5],'australia'],
  ['Brazil',[923.125,62.5,8,8],'australia-brazil'],
  ['Canada',[910.525,62.5,7,5],'us-ca'],
  ['Costa Rica',[910.525,125,11,5],'costa-rica'],
  ['EU/UK narrow',[869.618,62.5,8,8],'eu868'],
  ['EU/UK deprecated',[869.525,250,11,5],'eu868'],
  ['Czech Republic',[869.432,62.5,7,5],'eu868'],
  ['EU433 long',[433.650,250,11,5],'eu433'],
  ['EU433 narrow',[433.650,62.5,8,8],'eu433'],
  ['Hungary',[869.618,62.5,7,5],'eu868'],
  ['Netherlands',[869.618,62.5,7,5],'eu868'],
  ['Limburg',[869.618,62.5,8,8],'eu868'],
  ['Portugal433',[433.375,62.5,9,6],'eu433'],
  ['Portugal868',[869.618,62.5,7,6],'eu868'],
  ['Slovakia',[869.618,62.5,7,5],'eu868'],
  ['Switzerland',[869.618,62.5,8,8],'eu868'],
  ['Thailand',[921,62.5,7,5],'thailand'],
  ['USA',[910.525,62.5,7,5],'us-ca'],
  ['Southern California',[927.875,62.5,7,5],'us-ca'],
  ['Vietnam narrow',[920.250,62.5,8,5],'vietnam'],
  ['Vietnam deprecated',[920.250,250,11,5],'vietnam']
];

for(const [label,tuple,id] of table)test('recognizes the complete known community tuple: '+label,()=>{
  const result=recommend(tuple);
  assert.equal(result.id,id);assert.equal(result.selection,'community');assert.equal(result.matched,true);
  assert.deepEqual(rf(result),rf(setup.regions.find(profile=>profile.id===id)));
});

test('regional defaults use the requested complete RF tuples',()=>{
  const expected={eu868:[869.525,250,5,7],eu433:[433.925,250,5,7],'us-ca':[909.950,500,5,7],
    'costa-rica':[909.950,250,5,7],australia:[921.500,250,5,7],'australia-brazil':[921.500,500,5,7],
    brazil:[921.500,500,5,7],thailand:[921.500,250,5,7],vietnam:[921.500,250,5,7]};
  assert.equal(setup.regions.length,Object.keys(expected).length);
  for(const [region,tuple] of Object.entries(expected))assert.deepEqual(rf(recommend(undefined,{region})),tuple);
});

test('a complete nominal occupied bandwidth fits inside every profile band',()=>{
  for(const profile of setup.regions){
    assert.equal(regions.fitsBand(profile),true,profile.id);
    assert.ok(profile.frequency-profile.bandwidth/2000>=profile.bounds[0]-1e-9,profile.id);
    assert.ok(profile.frequency+profile.bandwidth/2000<=profile.bounds[1]+1e-9,profile.id);
  }
  const eu=setup.regions.find(profile=>profile.id==='eu868');
  assert.equal(regions.fitsBand({...eu,frequency:869.526}),false);
  assert.equal(regions.fitsBand({...eu,bandwidth:500}),false);
});

test('unknown custom tuples remain unchanged instead of guessing a region from their frequency',()=>{
  for(const tuple of [[865,125,9,5],[863,125,9,5],[868.125,125,9,5],[869.618,125,8,8],[870,125,9,5],
    [910.525,62.5,7,8],[915.8,250,9,5],[920.25,125,8,5],[921,62.5,8,5],[433.175,125,8,6]]){
    const result=recommend(tuple);
    assert.deepEqual(rf(result),tuple);assert.equal(result.id,'custom');assert.equal(result.selection,'saved');assert.equal(result.matched,false);
  }
});

test('New Zealand community tuples remain unchanged until their licence is verified',()=>{
  for(const tuple of [[917.375,62.5,7,5],[917.375,250,11,5]]){
    assert.deepEqual(rf(recommend(tuple)),tuple);assert.equal(recommend(tuple).id,'custom');
  }
  assert.equal(setup.regions.some(profile=>/zealand|\bnz\b/i.test(profile.id+' '+profile.label)),false);
});

test('frequency matching permits rounding error up to one kHz, not nearby custom channels',()=>{
  for(const frequency of [910.5250244,910.524,910.526])assert.equal(recommend([frequency,62.5,7,5]).id,'us-ca');
  for(const frequency of [910.5239,910.5261])assert.equal(recommend([frequency,62.5,7,5]).id,'custom');
});

test('shared Australia/Brazil and US/Canada tuples keep the ambiguity explicit',()=>{
  const shared=recommend([923.125,62.5,8,8]);
  assert.equal(shared.id,'australia-brazil');assert.equal(shared.ambiguous,true);assert.match(shared.label,/Australia.*Brazil/);
  assert.deepEqual(rf(shared),[921.5,500,5,7]);
  const north=recommend([910.525,62.5,7,5]);assert.equal(north.ambiguous,true);assert.match(north.label,/US.*Canada/);
});

test('Auto prefers a verified active tuple and preserves its preamble',()=>{
  const saved=[869.618,62.5,8,8],active={state:'active',tuple:[868.85,125,9,6],preamble:64};
  const result=recommend(saved,{active});
  assert.deepEqual(rf(result),active.tuple);assert.equal(result.preamble,64);assert.equal(result.selection,'active');
  assert.deepEqual(rf(recommend(saved,active)),active.tuple,'The earlier active-readback call shape remains valid');
  assert.deepEqual(rf(recommend(undefined,{active})),active.tuple);
});

test('an explicitly chosen region takes precedence over saved or active profiles',()=>{
  const saved=[910.525,62.5,7,5],active={state:'active',tuple:[908.5,250,7,5],preamble:128};
  const result=recommend(saved,{region:'eu868',active});
  assert.deepEqual(rf(result),[869.525,250,5,7]);assert.equal(result.selection,'region');assert.equal(result.preamble,undefined);
  assert.deepEqual(rf(recommend(undefined,{region:'eu433'})),[433.925,250,5,7]);
  assert.equal(recommend(saved,{region:'invented',active}),null);
});

test('malformed tuples, active states, regions and options never invent an automatic channel',()=>{
  for(const tuple of [null,undefined,[],[868,125,9],['868',125,9,5],[NaN,125,9,5],[868,999,9,5],[868,125,13,5],[868,125,9,9]])assert.equal(recommend(tuple),null);
  const saved=[869.618,62.5,8,8];
  for(const active of [{state:'active',tuple:[868.85,125,9,5],preamble:0},{state:'active',tuple:[868.85,125,9],preamble:32}])assert.equal(recommend(saved,{active}),null);
  for(const options of [null,[],5,'eu868',{region:5}])assert.equal(recommend(saved,options),null);
});

test('suggestions are pure copies and contain no persistent power or airtime setters',()=>{
  const saved=[869.618,62.5,8,8],active={state:'active',tuple:[868.85,125,9,6],preamble:64};
  const before=JSON.stringify({saved,active}),first=recommend(saved);
  first.bounds[0]=0;first.frequency=10;
  assert.equal(JSON.stringify({saved,active}),before);
  assert.equal(recommend(saved).bounds[0],869.4);assert.equal(recommend(saved).frequency,869.525);
  assert.ok(Object.isFrozen(setup.regions));assert.ok(setup.regions.every(profile=>Object.isFrozen(profile)&&Object.isFrozen(profile.bounds)));
  assert.equal(regions.communitySource,'https://api.meshcore.nz/api/v1/config');
  for(const profile of setup.regions)for(const name of ['tx','power','eirp','duty','airtime','autofetch','autoinstall'])assert.equal(profile[name],undefined);
});

for(const sourceBoard of ['Heltec V2','LILYGO T-LoRa V2.1-1.6','GEPRC LINKFLOW 900M TX','LilyGo T3 S3 SX127x','LilyGo T-Beam SX127x'])
  test('a positively identified SX127x source uses SF7 for generated defaults: '+sourceBoard,()=>{
    const saved=[910.525,62.5,7,5],hardware={sourceBoard},result=recommend(saved,{hardware});
    assert.deepEqual(rf(result),[909.95,500,7,7]);assert.equal(result.hardwareAdjusted,true);
    assert.match(result.hardwareReason,/SX127x.*SF7/);
    assert.deepEqual(rf(regions.recommend(saved,{hardware})),rf(result));
    assert.deepEqual(rf(recommend(undefined,{region:'eu868',hardware})),[869.525,250,7,7]);
    assert.deepEqual(saved,[910.525,62.5,7,5]);assert.deepEqual(hardware,{sourceBoard});
  });

for(const targetHwId of ['Heltec_v2','LilyGo_TLora_V2_1_1_6','LilyGo_T3S3_sx1276','Tbeam_SX1276','GEPRC_Linkflow_900'])
  test('a positively identified SX127x target uses SF7 for generated defaults: '+targetHwId,()=>{
    const result=recommend([869.618,62.5,8,8],{hardware:{sourceBoard:'Heltec V4',targetHwId}});
    assert.deepEqual(rf(result),[869.525,250,7,7]);assert.equal(result.hardwareAdjusted,true);
    const explicit=recommend(undefined,{region:'vietnam',hardware:{targetHwId}});
    assert.deepEqual(rf(explicit),[921.5,250,7,7]);assert.equal(explicit.selection,'region');
  });

test('unknown and ambiguous identities never guess a radio family',()=>{
  for(const sourceBoard of ['Heltec V4','Generic ESP32','LilyGo T-Beam','LilyGo T3 S3 (radio undetected)',
    'LilyGo T-Beam (radio undetected)','LilyGo T3 S3 SX126x','Heltec V2 clone',null,{},1276]){
    const result=recommend([910.525,62.5,7,5],{hardware:{sourceBoard}});
    assert.deepEqual(rf(result),[909.95,500,5,7]);assert.equal(result.hardwareAdjusted,undefined);
  }
  for(const targetHwId of ['Heltec_v4','LilyGo_T3S3_universal','LilyGo_Tbeam_universal','heltec_v2_custom',null,1276]){
    const result=recommend([910.525,62.5,7,5],{hardware:{targetHwId}});
    assert.deepEqual(rf(result),[909.95,500,5,7]);assert.equal(result.hardwareAdjusted,undefined);
  }
  assert.deepEqual(rf(recommend([910.525,62.5,7,5],{hardware:{sourceBoard:'  HELTEC V2  '}})),[909.95,500,7,7]);
});

test('hardware adaptation preserves verified temporary and user-saved custom settings',()=>{
  const hardware={sourceBoard:'Heltec V2',targetHwId:'Heltec_v2'},active={state:'active',tuple:[908.5,250,5,5],preamble:32};
  const result=recommend([910.525,62.5,7,5],{active,hardware});
  assert.deepEqual(rf(result),active.tuple);assert.equal(result.selection,'active');assert.equal(result.hardwareAdjusted,undefined);
  const custom=[911.25,125,5,6],saved=recommend(custom,{hardware});
  assert.deepEqual(rf(saved),custom);assert.equal(saved.selection,'saved');assert.equal(saved.hardwareAdjusted,undefined);
  const explicit=recommend(custom,{region:'eu868',active,hardware});
  assert.deepEqual(rf(explicit),[869.525,250,7,7]);assert.equal(explicit.selection,'region');
  assert.deepEqual(active,{state:'active',tuple:[908.5,250,5,5],preamble:32});
});
