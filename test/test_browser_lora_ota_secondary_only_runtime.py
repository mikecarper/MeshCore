import unittest
from test_browser_lora_ota_nrf_runtime import run_harness
from test_browser_lora_ota_radio_setup_runtime import radio_setup_harness
from test_browser_lora_ota_quiet_runtime import quiet_forwarding_harness

class BrowserSecondaryOnlyRuntime(unittest.TestCase):
    def supporting(self,harness):
        harness=harness.replace("let sourceTemp='off'", "let sourceOnly='off',targetOnly='off';let sourceTemp='off'",1)
        harness=harness.replace("else if(text==='get radio2.only')reply='Unknown command';", """else if(text==='get radio2.only')reply='> '+targetOnly;
        else if(/^set radio2.only (on|off) [0-9A-F]{16}$/.test(text)){
          const [, ,state,token]=text.split(' ');targetOnly=state==='on'?'on '+token:'off';reply='OK - radio2.only '+state;
        }""",1)
        harness=harness.replace("else if(text==='get radio2.only')value='Unknown command';", """else if(text==='get radio2.only')value='> '+sourceOnly;
        else if(/^set radio2.only (on|off) [0-9A-F]{16}$/.test(text)){
          const [, ,state,token]=text.split(' ');sourceOnly=state==='on'?'on '+token:'off';value='OK - radio2.only '+state;
        }""",1)
        return harness

    def test_disconnect_restores_preexisting_owned_pause_without_creating_a_new_one(self):
        harness=self.supporting(radio_setup_harness())
        before="el('inspect').click();await wait(()=>!el('inspect').disabled);"
        after="el('radio-frequency').value='909.950';el('radio-bandwidth').value='500';el('radio-sf').value='5';el('radio-cr').value='7';el('radio-frequency').dispatchEvent(new Event('input'));"+before
        self.assertIn(before,harness);harness=harness.replace(before,after,1)
        before="const profiles=[window.testLink.otaRadioProfile,window.testLink.otaTargetProfile];"
        after=before+"const token='1212121212121212';sourceOnly='on '+token;targetOnly='on '+token;"+\
          "localStorage.setItem('meshcore-browser-lora-ota-radio2-only-v1',JSON.stringify({version:1,source:window.testLink.sourceKey,target:key,token,sourceOwned:true,targetOwned:true}));"
        self.assertIn(before,harness);harness=harness.replace(before,after,1)
        result=run_harness(harness,timeout=70)
        self.assertNotIn('error',result,result)
        writes=[row[1] for row in result['timeline'] if len(row)>1 and isinstance(row[1],str) and row[1].startswith('set tempradio2 ')]
        self.assertTrue(writes);self.assertTrue(all('909.950,500,5,7' in c for c in writes))
        for endpoint in ('local','remote'):
            commands=[row[1] for row in result['timeline'] if row[0]==endpoint]
            self.assertTrue(any(c.startswith('set radio2.only off ') for c in commands),(endpoint,commands))
            self.assertFalse(any(c.startswith('set radio2.only on ') for c in commands),(endpoint,commands))

if __name__=='__main__':unittest.main()
