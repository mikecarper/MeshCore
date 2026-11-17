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

    def test_verified_secondary_setup_and_disconnect_restore(self):
        result=run_harness(self.supporting(radio_setup_harness()),timeout=70)
        self.assertNotIn('error',result,result)
        timeline=result['timeline']
        for endpoint in ('local','remote'):
            commands=[row[1] for row in timeline if row[0]==endpoint]
            ons=[i for i,c in enumerate(commands) if c.startswith('set radio2.only on ')]
            offs=[i for i,c in enumerate(commands) if c.startswith('set radio2.only off ')]
            self.assertTrue(ons,(endpoint,commands));self.assertTrue(offs,(endpoint,commands));self.assertLess(ons[0],offs[-1])
            self.assertTrue(any(c=='get tempradio2' for c in commands[:ons[0]]))

    def test_full_update_unpins_before_install_and_reconnect(self):
        result=run_harness(self.supporting(quiet_forwarding_harness()),timeout=80)
        self.assertNotIn('error',result,result)
        timeline=result['timeline']
        remote=[row[1] for row in timeline if row[0]=='remote']
        local=[row[1] for row in timeline if row[0]=='local']
        target_off=next(i for i,c in enumerate(remote) if c.startswith('set radio2.only off '))
        install=next(i for i,c in enumerate(remote) if c=='ota install' or c.startswith('ota installhash '))
        self.assertLess(target_off,install)
        self.assertTrue(any(c.startswith('set radio2.only off ') for c in local))

    def test_disconnect_during_first_activation_restores_owned_target(self):
        harness=self.supporting(radio_setup_harness())
        old="targetOnly=state==='on'?'on '+token:'off';reply='OK - radio2.only '+state;"
        new=old+"if(state==='on')queueMicrotask(()=>el('disconnect').click());"
        self.assertIn(old,harness);harness=harness.replace(old,new,1)
        old="el('inspect').click();await wait(()=>!el('inspect').disabled);"
        new="el('inspect').click();await wait(()=>!el('connect').disabled);await report({sourceOnly,targetOnly,timeline,record:localStorage.getItem('meshcore-browser-lora-ota-radio2-only-v1')});return;"
        self.assertIn(old,harness);harness=harness.replace(old,new,1)
        result=run_harness(harness,timeout=70)
        self.assertNotIn('error',result,result)
        self.assertEqual(result['sourceOnly'],'off');self.assertEqual(result['targetOnly'],'off');self.assertIsNone(result['record'])
        remote=[row[1] for row in result['timeline'] if row[0]=='remote']
        self.assertTrue(any(c.startswith('set radio2.only on ') for c in remote))
        self.assertTrue(any(c.startswith('set radio2.only off ') for c in remote))

    def test_default_radio_settings_and_forwarding_pause_use_radio2_only(self):
        # The shared harness turns forwarding off for unrelated tests. Restore
        # the real page's checked default, and leave its 368-symbol value alone.
        harness=self.supporting(quiet_forwarding_harness())
        harness=harness.replace("el('quiet-forwarding').checked=true;",
                                "el('quiet-forwarding').checked=el('quiet-forwarding').defaultChecked;",1)
        harness=harness.replace(',128',',368').replace('preamble=128','preamble=368')
        result=run_harness(harness,timeout=80,radio2_preamble=None)
        self.assertNotIn('error',result,result)
        self.assertTrue(result['quietDefault'])
        timeline=result['timeline']
        for endpoint in ('local','remote'):
            commands=[row[1] for row in timeline if row[0]==endpoint]
            setup=next(i for i,c in enumerate(commands) if c.startswith('set tempradio2 '))
            enabled=next(i for i,c in enumerate(commands) if c.startswith('set radio2.only on '))
            self.assertTrue(commands[setup].endswith(',368'),commands[setup])
            self.assertLess(setup,enabled)
            self.assertTrue(any(c.startswith('set radio2.only off ') for c in commands[enabled+1:]))

if __name__=='__main__':unittest.main()
