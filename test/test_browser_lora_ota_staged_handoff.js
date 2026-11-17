'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const setup=require('../docs/_javascript/lora_ota_radio_setup.js');
const SOURCE='34'.repeat(32),TARGET='12'.repeat(32);
function fixture({frequency=910.525,bandwidth=62.5,sf=7,primaryActive=false,unsupported=false,repeater=false,lose='',busy=false,fail='',cancelAt=0,mutateSavedAt=0}={}){
 const events=[],messages=[],normal=[frequency,bandwidth,sf,5],saved={source:normal.slice(),target:normal.slice()},state={source:null,target:null};let active=true,clock=10000;const tempPrimary={source:primaryActive?normal.slice():null,target:primaryActive?normal.slice():null};
 const link={browserRawSource:!repeater,localCliSupported:true,otaSupported:true,otaMode:'guarded',sourceKey:SOURCE,
   knownRepeater:TARGET,serving:false,guard:false,authenticated:false,otaRadioProfile:0,otaTargetProfile:0,supportsOtaRadio2:!unsupported,
   async probeOtaRadio(){}, async verifyPeer(){this.guard=true;this.authenticated=true;this.ownsPeer=true;events.push(['verify',this.otaRadioProfile]);},
   async closeRadioSession(){this.guard=false;this.authenticated=false;this.ownsPeer=false;events.push(['close']);},
   async resetRemoteSession(){this.guard=false;this.authenticated=false;events.push(['reset']);},
   async selectOtaRadio(profile){assert.equal(this.guard,false);this.otaRadioProfile=profile;events.push(['select',profile]);},
   async applyTemporaryRadio(command){assert.equal(this.guard,false);return read('source',command);},
   async localCommand(command){return read('source',command);},
   async remoteCommand(key,password,command){assert.equal(key,TARGET);assert.equal(this.guard,true);return read('target',command);}};
 let setterCount=0;
 async function read(who,cmd){events.push([who,cmd]);
   if(cmd==='get public.key')return '> '+TARGET;
   if(cmd==='get radio')return '> '+saved[who].join(',');
   if(cmd==='get tempradio')return tempPrimary[who]?'> '+tempPrimary[who].join(',')+',128':'> off';
   if(cmd==='ota config')return 'ota config: autofetch=off autoinstall=off hops=0';
   if(cmd==='ota status')return 'OTA hw=board | no download';
   if(cmd==='ota stats')return 'OTA | fetch idle';
   if(cmd==='ota folder')return 'folder=off';
   if(cmd==='get radio2')return unsupported&&who==='target'?'Error: unsupported':busy&&who==='target'?'> 908.5,250,7,5,rxtx,128':'> off (manual)';
   if(cmd==='get radio2.cross')return '> off';
   if(['get radioat','get tempradioat','get radioat2','get tempradioat2'].includes(cmd))return '> -none-';
   if(cmd==='get tempradio2')return state[who]?'> '+state[who].tuple.join(',')+',rxtx,'+state[who].pre+'; (120 min) left':unsupported&&who==='target'?'Error: unsupported':'> off';
   if(cmd==='get radio2.status')return '> '+(state[who]?'rxtx':'off')+'; RX=0,0 TX=0,0 switches=1 errors=0 max=200us preamble=32,'+(state[who]?.pre||32);
   if(cmd.startsWith('set tempradio2 ')){
     setterCount++;let parts=cmd.slice(15).split(',');state[who]={tuple:parts.slice(0,4).map(Number),pre:Number(parts[6])};
     if(setterCount===cancelAt)active=false;if(setterCount===mutateSavedAt)saved.source[0]=911.125;
     if(fail===who+setterCount)throw Error('deliberate failure');
     if(lose===who+setterCount)throw Object.assign(Error('timed out'),{loraReplyTimeout:true});
     return 'OK - temporary';
   }
   if(cmd.startsWith('tempradio ')){tempPrimary[who]=cmd.slice(10).split(',').slice(0,4).map(Number);return 'OK - temporary';}
   throw Error(who+': unexpected '+cmd);
 }
 return {link,events,messages,state,normal,run:()=>setup.prepare(link,TARGET,'pw',
    {frequency,bandwidth,sf,cr:5,minutes:120,preamble:128},
    {stagedOffset:true,secondaryPreamble:368,progress:m=>messages.push(m),canContinue:()=>active,wait:async ms=>{clock+=ms;},now:()=>clock})};
}
test('default stages mirror then +200 kHz on both devices, before selecting radio2',async()=>{
 const f=fixture(),result=await f.run();assert.equal(result.stagedOffset,true);assert.deepEqual(result.source,[910.725,62.5,7,5]);
 const writes=f.events.filter(([,c])=>typeof c==='string'&&c.startsWith('set tempradio2 ')).map(([a,c])=>[a,c]);
 assert.deepEqual(writes,[['target','set tempradio2 910.525,62.5,7,5,rxtx,120,368'],['source','set tempradio2 910.525,62.5,7,5,rxtx,120,368'],
 ['target','set tempradio2 910.725,62.5,7,5,rxtx,120,368'],['source','set tempradio2 910.725,62.5,7,5,rxtx,120,368']]);
 assert.ok(f.events.findIndex(([e,p])=>e==='select'&&p===1)>f.events.findIndex(([e,c])=>e==='source'&&c===writes[3][1]));
 assert.equal(f.events.some(([,c])=>typeof c==='string'&&/^(?:tempradio |normalradio|set radio2.cross)/.test(c)),false);
});
test('200 kHz offset warns when normal and update bandwidths overlap',async()=>{
 const f=fixture({frequency:920.250,bandwidth:250,sf:11});const result=await f.run();assert.equal(result.stagedOffset,true);
 assert.ok(f.messages.some(m=>m.includes('overlaps the normal channel bandwidth')));
});
test('lost target shift acknowledgement is resolved by readback and never resent',async()=>{
 const f=fixture({lose:'target3'}),result=await f.run();assert.equal(result.stagedOffset,true);
 assert.equal(f.events.filter(([a,c])=>a==='target'&&typeof c==='string'&&c.startsWith('set tempradio2 910.725')).length,1);
 assert.ok(f.messages.some(m=>m.includes('acknowledgement was lost')));
});
test('failure during shift stops before changing source frequency',async()=>{
 const f=fixture({fail:'target3'});await assert.rejects(f.run(),error=>/deliberate failure/.test(error.message)&&/both devices/.test(error.message)&&error.sourceSetupAttempted===true&&error.targetSetupAttempted===true);
 assert.equal(f.events.some(([a,c])=>a==='source'&&typeof c==='string'&&c.startsWith('set tempradio2 910.725')),false);
});

for(const count of [1,3])test('cancelling after setter '+count+' prevents all subsequent setters',async()=>{
 const f=fixture({cancelAt:count});await assert.rejects(f.run(),/preparation changed/);
 assert.equal(f.events.filter(([,c])=>typeof c==='string'&&c.startsWith('set tempradio2 ')).length,count);
});
test('changed saved primary stops handoff before a dependent source retune',async()=>{
 const f=fixture({mutateSavedAt:1});await assert.rejects(f.run(),/primary settings changed/);
 assert.equal(f.events.some(([a,c])=>a==='source'&&typeof c==='string'&&c.startsWith('set tempradio2 ')),false);
});

test('direct Repeater source also stages and selects radio2 through its owned peer session',async()=>{
 const f=fixture({repeater:true}),result=await f.run();assert.equal(result.stagedOffset,true);
 assert.deepEqual(result.source,[910.725,62.5,7,5]);
 const commands=f.events.filter(([,c])=>typeof c==='string'&&c.startsWith('set tempradio2 '));
 assert.deepEqual(commands.map(([endpoint])=>endpoint),['target','source','target','source']);
 assert.ok(f.events.some(([event,profile])=>event==='select'&&profile===1));
});
test('older source without radio2 selection uses its normal primary trial',async()=>{
 const f=fixture({unsupported:true}),result=await f.run();assert.notEqual(result.stagedOffset,true);
 assert.equal(result.sourceProfile,0);assert.equal(result.targetProfile,0);
 assert.equal(f.events.some(([,cmd])=>typeof cmd==='string'&&cmd.startsWith('set tempradio2 ')),false);
 assert.ok(f.events.some(([,cmd])=>typeof cmd==='string'&&cmd.startsWith('tempradio ')));
});
test('existing primary temporary windows are not rewritten for an automatic handoff',async()=>{
 const f=fixture({primaryActive:true});await assert.rejects(f.run(),/existing primary temporary window/);
 assert.equal(f.events.some(([,cmd])=>typeof cmd==='string'&&/^(?:tempradio |normalradio|set tempradio2 )/.test(cmd)),false);
});

test('recognized near-edge primary does not shift beyond its existing band',async()=>{
 const f=fixture({frequency:869.618}),result=await f.run();assert.notEqual(result.stagedOffset,true);
 assert.ok(f.messages.some(message=>message.includes('outside this recognized radio band')));
 assert.equal(f.events.some(([,command])=>typeof command==='string'&&command.startsWith('set tempradio2 869.818')),false);
});

test('unrecognized primary keeps its current channel rather than inventing an offset',async()=>{
 const f=fixture({frequency:910.500}),result=await f.run();assert.notEqual(result.stagedOffset,true);
 assert.equal(result.sourceProfile,0);assert.equal(result.targetProfile,0);
 assert.ok(f.messages.some(message=>message.includes('no recognized band for an automatic offset')));
 assert.equal(f.events.some(([,command])=>typeof command==='string'&&command.startsWith('set tempradio2 910.700')),false);
});
