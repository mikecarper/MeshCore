const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const mota=require('../docs/_javascript/lora_ota_package.js');
const {Source,wrap}=require('../docs/_javascript/lora_ota_companion_source.js');
const cat=(...p)=>Buffer.concat(p.map(x=>Buffer.from(x)));
const hex=x=>Buffer.from(x).toString('hex');
const le16=n=>Buffer.from([n&255,n>>8&255]);
const le32=n=>Buffer.from([n&255,n>>8&255,n>>16&255,n>>24&255]);
function firmware() {
  const body=Buffer.alloc(2301);for(let i=0;i<body.length;i++)body[i]=(i*13)&255;
  const end=Buffer.alloc(56);end.write('EndF');end.writeUInt32LE(body.length,4);
  crypto.createHash('sha256').update(body).digest().copy(end,8,0,8);
  end.writeUInt32LE(0x01120002,16);end.writeUInt32LE(0x12345678,20);end.write('test_board',24);
  return cat(body,end);
}
test('zero-hop Companion source matches the established Python OTA responder',async()=>{
  const info=await mota.parseMota(await mota.buildFullMota(firmware())), source=await Source.create(info,'a'.repeat(64));
  const frames=[
    cat([2],source.seeder,source.digest,le32(info.targetId)),
    cat([4],source.mid,le16(0xffff)), cat([10],source.mid,le16(0xffff)),
    cat([8],source.mid,le16(1)), cat([6],source.mid,le16(0),le16(0xffff)),
    cat([6],source.mid,le16(1),le16(0x9fff)), cat([6],source.mid,le16(0xffff),le16(1))
  ].map(x=>wrap(new Uint8Array(x)));
  const actual=[];for(const f of frames)actual.push((await source.replies(f)).map(hex));
  const script=`import json,sys
sys.path.insert(0,sys.argv[1])
from mota_kiss_direct import MotaSource,decode_radio
v=json.load(sys.stdin)
s=MotaSource(bytes.fromhex(v['blob']),bytes.fromhex(v['seed']),compressor=lambda b:b)
out=[]
for h in v['frames']:
 out.append([(b'\\x32\\x00'+decode_radio(r).message).hex() for r in s.replies(bytes.fromhex(h))])
print(json.dumps(out))
`;
  const expected=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c',script,path.resolve(__dirname,'../tools/lora_ota')],
    {input:JSON.stringify({blob:hex(info.blob),seed:hex(source.seeder),frames:frames.map(hex)}),encoding:'utf8'}));
  assert.deepEqual(actual,expected);
  assert.equal(source.advert()[0],0x32);
});
test('ignores unconsumed paths, bad IDs and malformed windows',async()=>{
  const info=await mota.parseMota(await mota.buildFullMota(firmware())), source=await Source.create(info,'a'.repeat(64));
  assert.deepEqual(await source.replies(Uint8Array.of(0x32,1,0,6,...source.mid,0,0,1,0)),[]);
  assert.deepEqual(await source.replies(wrap(Uint8Array.of(6,0,0,0,0,0,0,1,0))),[]);
  assert.deepEqual(await source.replies(wrap(Uint8Array.of(6,...source.mid,0,0,1))),[]);
});
