import unittest
from profile_switch_mixed import PROFILES,check_plan,check_scan


class MixedHostTests(unittest.TestCase):
    def test_exact_equal_symbol_plan_and_peer_identity(self):
        self.assertEqual([2**p['sf']*1000/p['bw_khz'] for p in PROFILES],[8192]*4)
        reply=dict(mixed_profiles=1,symbol_us=8192,profiles=PROFILES)
        check_plan(reply)
        for key,value in (('symbol_us',4096),('profiles',PROFILES[::-1]),('mixed_profiles',0)):
            with self.assertRaises(RuntimeError): check_plan(dict(reply,**{key:value}))

    def test_every_hop_requires_a_modulation_write(self):
        status=dict(switch=dict(n=100),mixed_profiles=True,accept_offchannel=False,rollback=0,
                    rx_gain_reg=148,channel_step_khz=1000,settle_us=0,frequency_repeat=True,
                    modulation_cache=True,rf_commands=200,modulation_commands=100,
                    optimized_rx_resumes=100,failures=0,rx_mode_errors=0,cache_errors=0,rx_errors=0)
        check_scan(status,0)
        for key,value in (('modulation_commands',0),('rf_commands',100),('rx_gain_reg',150),
                          ('mixed_profiles',False),('cache_errors',1),('accept_offchannel',True)):
            with self.assertRaises(RuntimeError): check_scan(dict(status,**{key:value}),0)


if __name__=='__main__': unittest.main()
