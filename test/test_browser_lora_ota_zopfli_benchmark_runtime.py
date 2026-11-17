#!/usr/bin/env python3
"""Test the one-time compression decision on the actual page in Chromium."""
import os
import unittest
from test_browser_lora_ota_companion_runtime import HARNESS
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_routed_runtime import one_replace
from test_browser_lora_ota_runtime import BROWSER


def scenario(kind):
    setup=r'''<script>(()=>{
      const kind=KIND;
      if(kind==='legacy-native'&&!sessionStorage.getItem('bench-reloaded'))localStorage.setItem('meshcore-browser-lora-ota-preferences-v1',JSON.stringify({compression:'native'}));
      if(kind==='no-native')window.CompressionStream=undefined;
      if(kind==='real'){
        const Actual=window.Worker;
        window.Worker=class extends Actual {
          constructor(...args){super(...args);sessionStorage.setItem('bench-starts',String(Number(sessionStorage.getItem('bench-starts')||0)+1));}
          terminate(){sessionStorage.setItem('bench-terminations',String(Number(sessionStorage.getItem('bench-terminations')||0)+1));return super.terminate();}
        };
        return;
      }
      window.Worker=class {
        constructor(){sessionStorage.setItem('bench-starts',String(Number(sessionStorage.getItem('bench-starts')||0)+1));}
        postMessage(msg){if(kind==='fast'||kind==='legacy-native')setTimeout(()=>this.onmessage?.({data:{id:msg.id,data:Uint8Array.of(1).buffer}}),10);
          else if(kind==='failure'||kind==='no-native')setTimeout(()=>this.onerror?.(),10);}
        terminate(){sessionStorage.setItem('bench-terminations',String(Number(sessionStorage.getItem('bench-terminations')||0)+1));}
      };
    })();</script>'''.replace('KIND',repr(kind))
    anchor="window.addEventListener('load',async()=>{try{"
    flow=r'''
    const kind=KIND, key='meshcore-browser-lora-ota-zopfli-test-v1';
    if(!sessionStorage.getItem('bench-reloaded')){
      if(kind==='override-zopfli'||kind==='override-fast'){
        el('compression').value=kind==='override-fast'?'native':'zopfli';
        el('compression').dispatchEvent(new Event('change'));
      } else if(kind==='connection-change'){
        el('connection').value='wifi';el('connection').dispatchEvent(new Event('change'));
      }
      await wait(()=>{try{return !!JSON.parse(localStorage.getItem(key)||'null')?.result}catch(_){return false}});
      await new Promise(r=>setTimeout(r,50));
      sessionStorage.setItem('bench-first',JSON.stringify({mode:el('compression').value,summary:el('compression-summary').textContent,result:JSON.parse(localStorage.getItem(key)).result}));
      sessionStorage.setItem('bench-reloaded','1');location.reload();return;
    }
    await new Promise(r=>setTimeout(r,50));
    await report({first:JSON.parse(sessionStorage.getItem('bench-first')),mode:el('compression').value,
      summary:el('compression-summary').textContent,starts:Number(sessionStorage.getItem('bench-starts')||0),
      terminations:Number(sessionStorage.getItem('bench-terminations')||0)});return;
'''.replace('KIND',repr(kind))
    return setup+one_replace(HARNESS,anchor,anchor+flow)


class BrowserZopfliBenchmarkTest(unittest.TestCase):
    def setUp(self):
        if not BROWSER:
            if os.environ.get('MESHCORE_REQUIRE_OTA_BROWSER')=='1':self.fail('Chromium is required')
            self.skipTest('Chromium unavailable')

    def test_automatic_decision_and_saved_preference(self):
        for case,expected,result in [('fast','zopfli','passed'),('slow','native','failed'),('failure','native','failed'),
                                     ('legacy-native','native','passed'),('connection-change','native','failed'),
                                     ('override-zopfli','zopfli','manual'),('override-fast','native','manual'),
                                     ('no-native','native','failed')]:
            with self.subTest(case=case):
                actual=run_harness(scenario(case),timeout=30,compression_mode=None)
                self.assertNotIn('error',actual,actual)
                self.assertEqual(actual['first']['result'],result,actual)
                self.assertEqual(actual['first']['mode'],expected,actual)
                self.assertEqual(actual['mode'],expected,actual)
                self.assertEqual(actual['starts'],1,actual)
                self.assertEqual(actual['terminations'],1,actual)
                if case=='no-native':self.assertIn('uncompressed',actual['summary'])

    def test_real_worker_runs_once_and_reuses_its_decision(self):
        actual=run_harness(scenario('real'),timeout=30,compression_mode=None)
        self.assertNotIn('error',actual,actual)
        self.assertIn(actual['first']['result'],['passed','failed'])
        self.assertEqual(actual['mode'],'zopfli' if actual['first']['result']=='passed' else 'native')
        self.assertEqual(actual['starts'],1,actual)
        self.assertEqual(actual['terminations'],1,actual)


if __name__=='__main__':unittest.main()
