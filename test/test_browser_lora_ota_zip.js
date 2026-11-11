'use strict';
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const zlib = require('node:zlib');
const {test} = require('node:test');
const zip = require('../docs/_javascript/lora_ota_zip.js');

function firmware() {
  const body=Buffer.alloc(1800); for(let i=0;i<body.length;i++)body[i]=(i*17)&255;
  const trailer=Buffer.alloc(56);trailer.write('EndF');trailer.writeUInt32LE(body.length,4);
  crypto.createHash('sha256').update(body).digest().copy(trailer,8,0,8);
  trailer.writeUInt32LE(0x01120001,16);trailer.writeUInt32LE(0x12345678,20);trailer.write('board',24);
  return Buffer.concat([body,trailer]);
}
const manifest = JSON.stringify({manifest:{application:{bin_file:'firmware.bin',dat_file:'firmware.dat',
  init_packet_data:{application_version:4294967295,device_revision:65535,device_type:82,firmware_crc16:52115,softdevice_req:[182]}},dfu_version:0.5}});
function archive(entries) {
  const locals=[], directories=[]; let offset=0;
  for(const entry of entries){
    const name=Buffer.from(entry.name), raw=Buffer.from(entry.data||''), method=entry.deflate?8:0;
    const packed=method?zlib.deflateRawSync(raw):raw, flags=entry.descriptor?8:0, checksum=zip.crc32(raw);
    const local=Buffer.alloc(30);local.writeUInt32LE(0x04034b50);local.writeUInt16LE(20,4);local.writeUInt16LE(flags,6);
    local.writeUInt16LE(method,8);local.writeUInt16LE(name.length,26);
    if(!flags){local.writeUInt32LE(checksum,14);local.writeUInt32LE(packed.length,18);local.writeUInt32LE(raw.length,22);}
    const descriptor=flags?Buffer.alloc(16):Buffer.alloc(0);
    if(flags){descriptor.writeUInt32LE(0x08074b50);descriptor.writeUInt32LE(checksum,4);descriptor.writeUInt32LE(packed.length,8);descriptor.writeUInt32LE(raw.length,12);}
    const central=Buffer.alloc(46);central.writeUInt32LE(0x02014b50);central.writeUInt16LE(20,4);central.writeUInt16LE(20,6);
    central.writeUInt16LE(flags,8);central.writeUInt16LE(method,10);central.writeUInt32LE(checksum,16);
    central.writeUInt32LE(packed.length,20);central.writeUInt32LE(raw.length,24);central.writeUInt16LE(name.length,28);
    central.writeUInt32LE(entry.symlink?0xA0000000:0,38);central.writeUInt32LE(offset,42);
    locals.push(local,name,packed,descriptor);directories.push(central,name);
    offset+=local.length+name.length+packed.length+descriptor.length;
  }
  const directory=Buffer.concat(directories), end=Buffer.alloc(22);end.writeUInt32LE(0x06054b50);
  end.writeUInt16LE(entries.length,8);end.writeUInt16LE(entries.length,10);end.writeUInt32LE(directory.length,12);end.writeUInt32LE(offset,16);
  return Buffer.concat([...locals,directory,end]);
}
const entries=(bin=firmware())=>[{name:'manifest.json',data:manifest},{name:'firmware.dat',data:Buffer.alloc(14)},
  {name:'firmware.bin',data:bin}];

test('reads stored Nordic application DFU and verifies EndF identity',async()=>{
  const image=await zip.loadApplication(archive(entries()),{targetId:0x12345678,hwId:'board',fwVersion:0x01120001});
  assert.equal(image.member,'firmware.bin');assert.deepEqual(Buffer.from(image.image),firmware());
  await assert.rejects(zip.loadApplication(archive(entries()),{targetId:0x87654321}),/target/);
  await assert.rejects(zip.loadApplication(archive(entries()),{hwId:'other'}),/hardware/);
  await assert.rejects(zip.loadApplication(archive(entries()),{bodyHash:'0000000000000000'}),/hash/);
});

test('reads raw DEFLATE with data descriptor and BIN-only archives',async()=>{
  const e=entries();e[2].deflate=true;e[2].descriptor=true;
  assert.deepEqual(Buffer.from((await zip.loadApplication(archive(e))).image),firmware());
  assert.deepEqual(Buffer.from((await zip.loadApplication(archive([{name:'firmware.bin',data:firmware(),deflate:true}]))).image),firmware());
});

test('rejects path traversal, duplicated names, symlinks, and ambiguous applications',async()=>{
  for(const name of ['../firmware.bin','/firmware.bin','sub/../firmware.bin','sub\\firmware.bin','C:firmware.bin'])
    await assert.rejects(zip.loadApplication(archive([{name,data:firmware()}])),/path/);
  await assert.rejects(zip.loadApplication(archive([...entries(),{name:'manifest.json',data:manifest}])),/Duplicate/);
  await assert.rejects(zip.loadApplication(archive([...entries(),{name:'MANIFEST.JSON',data:manifest}])),/Duplicate/);
  await assert.rejects(zip.loadApplication(archive([{name:'firmware.bin',data:firmware(),symlink:true}])),/symlink/);
  await assert.rejects(zip.loadApplication(archive([...entries(),{name:'other.bin',data:firmware()}])),/multiple/);
  await assert.rejects(zip.loadApplication(archive([{name:'manifest.json',data:'{"manifest":{"application":{"bin_file":"firmware.bin","bin_file":"evil.bin"}}}'},
    {name:'firmware.bin',data:firmware()}])),/Duplicate JSON/);
  const combined=JSON.stringify({manifest:{application:{bin_file:'firmware.bin'},bootloader:{bin_file:'bl.bin'}}});
  await assert.rejects(zip.loadApplication(archive([{name:'manifest.json',data:combined},{name:'firmware.bin',data:firmware()}])),/single application/);
});

test('rejects invalid EndF, checksums, oversized declarations and zip64',async()=>{
  const invalid=firmware();invalid[5]^=1;
  await assert.rejects(zip.loadApplication(archive(entries(invalid))),/hash/);
  const bad=archive(entries());const cd=bad.readUInt32LE(bad.length-6);bad[30+'manifest.json'.length]^=1;
  await assert.rejects(zip.loadApplication(bad),/checksum/);
  const huge=archive(entries());huge.writeUInt32LE(0xffffffff,huge.length-6);
  await assert.rejects(zip.loadApplication(huge),/directory/);
  const bomb=archive([{name:'firmware.bin',data:Buffer.alloc(4*1024*1024+1),deflate:true}]);
  await assert.rejects(zip.loadApplication(bomb),/too large/);
  const forged=archive([{name:'firmware.bin',data:firmware(),deflate:true}]);
  const central=forged.readUInt32LE(forged.length-6);
  forged.writeUInt32LE(3,22);forged.writeUInt32LE(3,central+24);
  await assert.rejects(zip.loadApplication(forged),/exceeds|size/);
  assert.ok(cd>0);
});

test('accepts a real published Nordic release archive when supplied',async t=>{
  const path=process.env.MESHCORE_TEST_REAL_DFU_ZIP;
  if(!path){t.skip('Set MESHCORE_TEST_REAL_DFU_ZIP to a release asset');return;}
  const bytes=fs.readFileSync(path), image=await zip.loadApplication(bytes);
  assert.equal(image.member,'firmware.bin');assert.ok(image.image.length>100000);
});
