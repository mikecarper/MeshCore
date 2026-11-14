const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createHash}=require('node:crypto');
const {gzipSync}=require('node:zlib');
const catalog=require('../docs/_javascript/lora_ota_catalog.js');
const hash=b=>createHash('sha256').update(b).digest('hex');
async function fixture({hw='test-board',markers=true,installHash=true}={}){
  const env='test_board_repeater',targetId=await catalog.idForEnv(env),version='v1.18.0.2';
  const name=env+'-ota-'+version+'.bin',url='https://github.com/mikecarper/MeshCore/releases/download/'+version+'/'+name;
  const body=Buffer.from(markers?'OTA: status\0get <id> flash | install'+(installHash?'\0ota installhash <64-hex-container-sha256>':''):'no ota');
  const trailer=Buffer.alloc(56);trailer.write('EndF');trailer.writeUInt32LE(body.length,4);Buffer.from(hash(body),'hex').copy(trailer,8,0,8);trailer.writeUInt32LE(0x01120002,16);trailer.writeUInt32LE(targetId,20);trailer.write(hw,24,'ascii');
  const image=Buffer.concat([body,trailer]),gz=gzipSync(image),piece={sha256:hash(image),size:image.length,gzipSha256:hash(gz),gzipSize:gz.length,path:hash(image)+'.gz'};
  const controls={familyTag:version,profiles:{[env]:{otaRole:'lora-receiver',updateMethods:['lora']}}};
  const item={project:'MeshCore',provider:'Keymind Cascade',role:'repeater',action:'flash-update',hardware:'Test',architecture:'esp32',version,name,url};
  const index={schemaVersion:1,entries:[{url,...piece}]},dest={targetId,hwId:'test-board',version:0x01120001};
  const fetchImpl=async u=>new Response(u.endsWith('/catalog.json')?JSON.stringify({items:[item]}):u.endsWith('/firmware_controls.json')?JSON.stringify(controls):u.endsWith('/firmware/index.json')?JSON.stringify(index):gz);
  return {dest,item,index,piece,controls,image,body,fetchImpl};
}
test('finds and verifies an exact role and target, then checks OTA evidence',async()=>{
  const f=await fixture(),choices=await catalog.discover(f.dest,'repeater',{fetchImpl:f.fetchImpl});assert.equal(choices.length,1);
  assert.equal(catalog.recommend(choices,f.dest),choices[0]);const image=await catalog.load(choices[0],f.dest,{fetchImpl:f.fetchImpl});assert.equal(image.identity.targetId,f.dest.targetId);
  assert.equal((await catalog.discover(f.dest,'room',{fetchImpl:f.fetchImpl})).length,0);
  assert.equal(catalog.recommend([choices[0],{...choices[0],name:'other'}],f.dest),null);
  assert.equal(catalog.recommend([{...choices[0],version:'v1.18.0.3-preview2'}],f.dest),null);
  assert.equal((await catalog.discover({...f.dest,version:0x01120002},'repeater',{fetchImpl:f.fetchImpl})).length,0);
});
test('rejects tampered mirror, wrong hardware, absent OTA evidence and downgrade',async()=>{
  const f=await fixture(),[choice]=await catalog.discover(f.dest,'repeater',{fetchImpl:f.fetchImpl});
  const tampered=async u=>u.endsWith('.gz')?new Response(Buffer.alloc(f.piece.gzipSize)):f.fetchImpl(u);
  await assert.rejects(catalog.load(choice,f.dest,{fetchImpl:tampered}),/hash/);
  await assert.rejects(catalog.load(choice,{...f.dest,hwId:'different'},{fetchImpl:f.fetchImpl}),/identity/);
  await assert.rejects(catalog.load(choice,{...f.dest,targetId:f.dest.targetId+1},{fetchImpl:f.fetchImpl}),/identity/);
  await assert.rejects(catalog.load(choice,{...f.dest,version:0x01120002},{fetchImpl:f.fetchImpl}),/identity/);
  const no=await fixture({markers:false}),[candidate]=await catalog.discover(no.dest,'repeater',{fetchImpl:no.fetchImpl});
  await assert.rejects(catalog.load(candidate,no.dest,{fetchImpl:no.fetchImpl}),/LoRa OTA receiver support/);
});
test('excludes missing LoRa qualification and bootloader or merged artifacts',async()=>{
  const f=await fixture();
  f.controls.profiles.test_board_repeater.updateMethods=['wifi'];
  assert.equal((await catalog.discover(f.dest,'repeater',{fetchImpl:f.fetchImpl})).length,0);
  f.controls.profiles.test_board_repeater.updateMethods=['lora'];
  f.controls.familyTag='another-version';
  assert.equal((await catalog.discover(f.dest,'repeater',{fetchImpl:f.fetchImpl})).length,0);
  assert.deepEqual(catalog.envNames('test_board_repeater-with-bootloader-2.4.12-full-ota-v1.18.0.2.zip'),[]);
  assert.deepEqual(catalog.envNames('test_board_repeater-ota-v1.18.0.2-merged.bin'),[]);
  await assert.rejects(catalog.discover(f.dest,'unknown',{fetchImpl:f.fetchImpl}),/role/);
});
test('requires public release metadata to automatically recommend a dev-tagged release',async()=>{
  const f=await fixture(),[candidate]=await catalog.discover(f.dest,'repeater',{fetchImpl:f.fetchImpl});
  const tagged={...candidate,version:'v1.18.0.2-dev-abc'},url=new URL(candidate.url),parts=url.pathname.split('/');
  const stable={tag_name:parts[5],draft:false,prerelease:false,assets:[{name:candidate.name,browser_download_url:candidate.url}]};
  const fetchImpl=async()=>new Response(JSON.stringify(stable));
  assert.equal(await catalog.recommendVerified([tagged],f.dest,{fetchImpl}),tagged);
  stable.prerelease=true;assert.equal(await catalog.recommendVerified([tagged],f.dest,{fetchImpl}),null);
  stable.prerelease=false;stable.assets[0].browser_download_url+='x';assert.equal(await catalog.recommendVerified([tagged],f.dest,{fetchImpl}),null);
  assert.equal(await catalog.recommendVerified([tagged,{...tagged,name:'another'}],f.dest,{fetchImpl}),null);
  assert.equal(await catalog.recommendVerified([tagged],f.dest,{fetchImpl:async()=>{throw Error('offline');}}),null);
});
test('installed base lookup requires exact version, role, hardware and reported body hash',async()=>{
  const f=await fixture(),dest={...f.dest,version:0x01120002,baseHash:hash(f.body).slice(0,16)};
  const base=await catalog.findInstalled(dest,'repeater',f.item.version+' (Build: 1 Jan)',{fetchImpl:f.fetchImpl});
  assert.equal(hash(base.bytes),hash(f.image));
  assert.equal(await catalog.findInstalled({...dest,baseHash:'0000000000000000'},'repeater',f.item.version,{fetchImpl:f.fetchImpl}),null);
  assert.equal(await catalog.findInstalled({...dest,hwId:'wrong'},'repeater',f.item.version,{fetchImpl:f.fetchImpl}),null);
  assert.equal(await catalog.findInstalled(dest,'room',f.item.version,{fetchImpl:f.fetchImpl}),null);
  assert.equal(await catalog.findInstalled(dest,'repeater','v1.18.0.1 (Build: old)',{fetchImpl:f.fetchImpl}),null);
});

test('1.18.0.1 installed image lookup uses the exact historical mirror without weakening identity checks',async()=>{
  const f=await fixture(),version='v1.18.0.1-halo-keymind-cascade-dev-03214a16';
  const body=f.body, image=Buffer.from(f.image);image.writeUInt32LE(0x01120001,image.length-40);
  const gz=gzipSync(image),piece={sha256:hash(image),size:image.length,gzipSha256:hash(gz),gzipSize:gz.length,path:hash(image)+'.gz'};
  const name='test_board_repeater-full-ota-'+version+'.bin';
  const url='https://github.com/mikecarper/MeshCore/releases/download/lora-ota-'+version+'/'+name;
  const item={...f.item,version,name,url},dest={...f.dest,baseHash:hash(body).slice(0,16)};
  const history='https://mikecarper.github.io/MeshCore/firmware-1.18.0.1/firmware/';
  const seen=[];
  const fetchImpl=async u=>{seen.push(u);if(u.endsWith('/catalog.json'))return new Response(JSON.stringify({items:[item]}));
    if(u==='https://mikecarper.github.io/meshfirmware/firmware/index.json')return new Response(JSON.stringify({schemaVersion:1,entries:[]}));
    if(u===history+'index.json')return new Response(JSON.stringify({schemaVersion:1,entries:[{url,...piece}]}));
    if(u===history+piece.path)return new Response(gz);
    throw Error('Unexpected URL '+u);};
  const result=await catalog.findInstalled(dest,'repeater',version+' (Build: old)',{fetchImpl});
  assert.equal(hash(result.bytes),hash(image));assert.ok(seen.includes(history+'index.json'));assert.ok(seen.includes(history+piece.path));
  assert.equal(await catalog.findInstalled({...dest,hwId:'wrong'},'repeater',version,{fetchImpl}),null);
  assert.equal(await catalog.findInstalled({...dest,baseHash:'0000000000000000'},'repeater',version,{fetchImpl}),null);
  const invalid=async u=>u===history+'index.json'?new Response('{bad'):fetchImpl(u);
  await assert.rejects(catalog.findInstalled(dest,'repeater',version,{fetchImpl:invalid}),/Cannot load firmware catalog/);
  const corrupted=async u=>u===history+piece.path?new Response(Buffer.alloc(gz.length)):fetchImpl(u);
  await assert.rejects(catalog.findInstalled(dest,'repeater',version,{fetchImpl:corrupted}),/hash/);
});

test('installed embedded bootloader applications may identify a base, but are never offered as OTA candidates',async()=>{
  const name='test_board_repeater-with-bootloader-2.4.12-full-ota-v1.18.0.1-halo-keymind-cascade-dev-03214a16.zip';
  assert.deepEqual(catalog.envNames(name),[]);
  assert.ok(catalog.envNames(name,false).includes('test_board_repeater'));
  assert.deepEqual(catalog.envNames('test_board_repeater-with-bootloader-not-valid-full-ota-v1.18.0.1.zip',false).includes('test_board_repeater'),false);
  const f=await fixture(),version='v1.18.0.1-halo-keymind-cascade-dev-03214a16';
  const image=Buffer.from(f.image);image.writeUInt32LE(0x01120001,image.length-40);
  const gz=gzipSync(image),piece={sha256:hash(image),size:image.length,gzipSha256:hash(gz),gzipSize:gz.length,path:hash(image)+'.gz'};
  const imageName=name.replace('03214a16.zip','40dd42de.bin'),url='https://github.com/mikecarper/MeshCore/releases/download/lora-ota-'+version+'/'+imageName;
  const item={...f.item,version,name:imageName,url},history='https://mikecarper.github.io/MeshCore/firmware-1.18.0.1/firmware/';
  const fetchImpl=async u=>{
    if(u.endsWith('/catalog.json'))return new Response(JSON.stringify({items:[item]}));
    if(u==='https://mikecarper.github.io/meshfirmware/firmware/index.json')return new Response(JSON.stringify({schemaVersion:1,entries:[]}));
    if(u===history+'index.json')return new Response(JSON.stringify({schemaVersion:1,entries:[{url,...piece}]}));
    if(u===history+piece.path)return new Response(gz);
    throw Error('Unexpected URL '+u);
  };
  const reported='v1.18.0.1-halo-keymind-cascade-dev-40dd42de';
  const dest={...f.dest,baseHash:hash(f.body).slice(0,16)},result=await catalog.findInstalled(dest,'repeater',reported,{fetchImpl});
  assert.equal(hash(result.bytes),hash(image));
  assert.equal(await catalog.findInstalled({...dest,baseHash:'0000000000000000'},'repeater',reported,{fetchImpl}),null);
  assert.equal(await catalog.findInstalled(dest,'repeater','v1.18.0.1-halo-keymind-cascade-dev-deadbeef',{fetchImpl}),null);
});

test('released OTA receivers without installhash remain downloadable with full identity and hash checks',async()=>{
  const f=await fixture({installHash:false});
  const [choice]=await catalog.discover(f.dest,'repeater',{fetchImpl:f.fetchImpl});
  const image=await catalog.load(choice,f.dest,{fetchImpl:f.fetchImpl});
  assert.equal(image.installHashSupported,false);assert.equal(hash(image.bytes),hash(f.image));
  await assert.rejects(catalog.load(choice,{...f.dest,hwId:'wrong-board'},{fetchImpl:f.fetchImpl}),/identity/);
  const modern=await fixture(),[next]=await catalog.discover(modern.dest,'repeater',{fetchImpl:modern.fetchImpl});
  assert.equal((await catalog.load(next,modern.dest,{fetchImpl:modern.fetchImpl})).installHashSupported,true);
});
