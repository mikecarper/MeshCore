"""HIL rollback preserves production ownership and ordinary standby semantics."""
from pathlib import Path
import unittest
from test_radio_receive_contract import method
import test_sx1262_batched_modulation as compiler

ROOT=Path(__file__).resolve().parents[1]


class RollbackTests(unittest.TestCase):
    compile_run=compiler.BatchedModulationTests.compile_run

    def test_warm_rollback_only_suppresses_request(self):
        source=(ROOT/'tools/hil/profile_switch.cpp').read_text()
        body=method(source,'void setProfileStandbyWarm(')
        self.compile_run(r'''
#include <cassert>
struct CustomSX1262Wrapper { bool requested=false;virtual void setProfileStandbyWarm(bool v){requested=v;} };
struct Bench:CustomSX1262Wrapper { bool warmStandby=true; @METHOD@ };
int main(){for(bool policy:{false,true})for(bool request:{false,true}){
  Bench b;b.warmStandby=policy;b.setProfileStandbyWarm(request);assert(b.requested==(policy&&request));
}}
'''.replace('#include <cassert>','#include <cassert>\n#include <initializer_list>').replace('@METHOD@',body))

    def test_rollback_never_bypasses_hop_ownership_or_busy_checks(self):
        source=(ROOT/'tools/hil/profile_switch_channels.h').read_text()
        start=method(source,'void startChannelSweep(')
        for control in ('(channelRollback & 9)==0','(channelRollback & 4)==0',
                        '(channelRollback & 2)==0','(channelRollback & 8)==0'):
            self.assertIn(control,start)
        service=method(source,'void serviceChannelSweep(')
        self.assertIn('driver.hop(slot)',service)
        self.assertIn('rc==mesh::RadioParamApplyResult::BUSY',service)
        self.assertIn('!waitBusy()',service)
        self.assertIn('rfWordAtRead=channelTrace.rfWord',service)
        self.assertIn('payloadValid && bytes[8]==channelSweep.current',service)


if __name__=='__main__':
    unittest.main()
