#!/usr/bin/env python3
"""Exercise browser Zopfli output with MeshCore's actual OTA decoder."""
import base64
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

NODE_SCRIPT = r"""
const fs=require('fs'),vm=require('vm'),path=require('path');
const root=path.resolve('docs/_javascript');
const api=require(path.join(root,'lora_ota_zopfli.js'));
class Worker {
  constructor() {
    this.terminated=false;
    const s={console,URL,Uint8Array,ArrayBuffer,WebAssembly,TextDecoder,
      setTimeout,clearTimeout,performance,location:{href:'http://example.test/_javascript/lora_ota_zopfli_worker.js'}};
    s.self=s;
    s.fetch=async()=>({ok:true,arrayBuffer:async()=>{
      const b=fs.readFileSync(path.join(root,'vendor/zopfli/zopfli.wasm'));
      return b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength);
    }});
    s.postMessage=v=>{if(!this.terminated)this.onmessage?.({data:v});};
    s.importScripts=n=>vm.runInContext(fs.readFileSync(path.join(root,n),'utf8'),s);
    vm.createContext(s);
    vm.runInContext(fs.readFileSync(path.join(root,'lora_ota_zopfli_worker.js'),'utf8'),s);
    this.scope=s;
  }
  postMessage(data){this.scope.onmessage({data});}
  terminate(){this.terminated=true;}
}
(async()=>{
  const encoder=api.create({workerFactory:()=>new Worker()});
  const blocks=[Uint8Array.of(42),
    Uint8Array.from({length:1024},(_,i)=>(i*i+i)%251),
    Uint8Array.from({length:2048},(_,i)=>i%29),
    Uint8Array.from({length:2048},(_,i)=>(Math.imul(i,33)^(i>>>2))&255)];
  const results=[];
  for(const input of blocks){
    const output=await encoder.compress(input);
    results.push({input:Buffer.from(input).toString('base64'),output:Buffer.from(output).toString('base64')});
  }
  encoder.dispose();process.stdout.write(JSON.stringify(results));
})().catch(error=>{console.error(error);process.exit(1);});
"""


class BrowserZopfliTargetDecoderTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node') and shutil.which('gcc'), 'node and gcc are required')
    def test_real_zopfli_streams_round_trip_in_target_tinf(self):
        with tempfile.TemporaryDirectory(prefix='meshcore-zopfli-tinf-') as tmp:
            library = Path(tmp) / ('tinf.dll' if os.name == 'nt' else 'tinf.so')
            subprocess.run(['gcc','-shared','-O2','-DMESHCORE_TINF_IMPLEMENTATION',
                str(ROOT/'src/helpers/ota/tinf/tinflate.c'),'-o',str(library)],
                check=True,capture_output=True,text=True)
            module=ctypes.CDLL(str(library))
            decode=module.tinf_uncompress_exact
            decode.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint),ctypes.c_void_p,ctypes.c_uint]
            decode.restype=ctypes.c_int
            proc=subprocess.run(['node','-e',NODE_SCRIPT],cwd=ROOT,check=True,
                capture_output=True,text=True,timeout=90)
            for item in json.loads(proc.stdout):
                raw=base64.b64decode(item['input']); encoded=base64.b64decode(item['output'])
                with self.subTest(size=len(raw)):
                    output=ctypes.create_string_buffer(len(raw)); length=ctypes.c_uint(len(raw))
                    src=ctypes.create_string_buffer(encoded)
                    self.assertEqual(decode(output,ctypes.byref(length),src,len(encoded)),0)
                    self.assertEqual(length.value,len(raw))
                    self.assertEqual(output.raw,raw)
                    length.value=len(raw)
                    trailing=ctypes.create_string_buffer(encoded+b'\0')
                    self.assertNotEqual(decode(output,ctypes.byref(length),trailing,len(encoded)+1),0)
            if os.name=='nt':
                ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(module._handle))


if __name__=='__main__':
    unittest.main()
