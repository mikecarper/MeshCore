"""Portable plan and fixed-source guard checks for the 32-symbol pair bench."""
from pathlib import Path
import copy
import subprocess
import tempfile
import unittest
from profile_pair import check_scan,check_sent,main

ROOT=Path(__file__).resolve().parents[2]

class PairTests(unittest.TestCase):
    def test_plan(self):
        source=r'''
#include "ProfilePairPlan.h"
#include <RadioProfiles.h>
#include <cassert>
int main() {
  mesh::RadioProfiles p;
  p.primary={909.5f,62.5f,32,7,5};
  p.secondary.params={910.5f,500,32,8,5};
  p.secondary.mode=mesh::RadioProfileMode::Rx;
  // Match the production startup self-test before comparing visit durations.
  for (unsigned i=0; i<p.SwitchTestSamplesPerDirection; ++i) {
    p.sampleSwitch(0,1,545);
    p.sampleSwitch(1,0,545);
  }
  assert(p.switchTestReady() && p.switchBudgetUs()==600);
  assert(pairListenUs(0,0)==8397);
  assert(pairListenUs(1,0)==23171);
  assert(pairListenUs(0,0,2048)==10445 && pairListenUs(1,0,2048)==21123);
  assert(pairListenUs(0,300,1024)==9421 && pairListenUs(1,300,1024)==21847);
  assert(pairListenUs(0,300,1024)+pairListenUs(1,300,1024)+1200+300==32768);
  assert(pairPlanSupported(0,0) && pairPlanSupported(0,2048) && pairPlanSupported(4000,0));
  assert(pairPlanSupported(300,1024));
  assert(!pairPlanSupported(300,2048) && !pairPlanSupported(0,1024));
  assert(!pairPlanSupported(4000,1024) && !pairPlanSupported(0,40000));
  assert(pairListenUs(0,300,1024)==p.listenUs(0));
  assert(pairListenUs(1,300,1024)==p.listenUs(1));
  assert(pairProfiles[0].sf==7 && pairProfiles[0].bw==62.5f);
  assert(pairProfiles[1].sf==8 && pairProfiles[1].bw==500);
  assert(pairListenUs(1,4000)>4.1*512);
}'''
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'pair.exe'
            result=subprocess.run(['g++','-std=c++17','-x','c++','-',
                '-I'+str(ROOT/'tools/hil'),'-I'+str(ROOT/'src'),'-o',str(exe)],
                input=source,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(subprocess.run([str(exe)]).returncode,0)

    def test_no_production_or_fixed_source_guard_bypass(self):
        source=(ROOT/'tools/hil/profile_fixed_tx.cpp').read_text()
        self.assertIn('configuration locked until reboot',source)
        self.assertIn('seq<=lastSequence',source)
        self.assertIn('&& !fixedPair) transmit',source)
        scanner=(ROOT/'tools/hil/profile_switch_channels.h').read_text()
        self.assertIn('const auto rc=driver.hop(slot)',scanner)
        self.assertIn('pairListenUs(channelSweep.current,pairLoopUs,pairSlowExtraUs)',scanner)

    def test_collector_requires_exact_modulation_and_counts(self):
        status=dict(switch={'n':99},with_modulation={'n':99},without_modulation={'n':0},
            channels=2,active=True,pair_profiles=True,pair_loop_us=0,pair_slow_extra_us=2048,
            pair_dwell_us=[10445,21123],rx_gain_reg=0x94,retune_passes=1,settle_us=0,
            first_pass_detour=False,frequency_repeat=False,mixed_profiles=False,rollback=0,
            modulation_cache=True,continue_on_miss=True,accept_offchannel=False,
            received=1,strict_received=1,offchannel_received=0,missed=1,failures=0,
            rx_mode_errors=0,cache_errors=0,rx_errors=0,second_pass_blocked=0,
            rf_commands=99,modulation_commands=99,optimized_rx_resumes=99)
        trials=[{'valid':True},{'valid':False}]
        check_scan(status,0,2048,trials)
        followup=copy.deepcopy(status)
        followup.update(pair_loop_us=300,pair_slow_extra_us=1024,pair_dwell_us=[9421,21847])
        check_scan(followup,300,1024,trials)
        for key,value in [('rf_commands',198),('modulation_commands',0),('rx_errors',1),
                          ('pair_dwell_us',[8397,23171]),('missed',0),('rx_gain_reg',0x96)]:
            bad=copy.deepcopy(status);bad[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):check_scan(bad,0,2048,trials)
        sent=dict(sf=8,bw_khz=500,freq_khz=910500,channel=1,len=16,preamble=32,rc=0,
                  power_dbm=-9,rf_commands=0,modulation_commands=0,packet_count=123,fixed_tx=True,failed=False)
        check_sent(sent,1,123)
        for key,value in [('preamble',88),('bw_khz',125),('rf_commands',1),('packet_count',124)]:
            bad=dict(sent);bad[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):check_sent(bad,1,123)

    def test_unreviewed_case_rejected_before_hardware_access(self):
        for phases in ((),((300,2048),),((0,1024),),((300,1024),(300,1024))):
            with self.subTest(phases=phases),self.assertRaises(ValueError):main(phases)

if __name__=='__main__':unittest.main()
