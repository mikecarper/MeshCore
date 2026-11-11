"""Compile the actual bounded boot scheduler; no radio or persistence substitutes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = r'''
#include <helpers/radiolib/LR2021BootAutoTrim.h>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
using Policy=mesh::lr2021::LR2021BootAutoTrim;
using State=Policy::State;
static_assert(sizeof(Policy)<=24,"bounded resident state");

static void default_and_retained() {
  Policy p;
  p.reset(1000,true,false,true); // runtime resolves missing policy as enabled
  assert(p.enabled() && p.attempts()==0 && p.state()==State::WaitingRadio);
  assert(!p.ready(1000,false) && p.attempts()==0);
  assert(p.ready(2000,true) && p.state()==State::Ready && p.attempts()==0);
  assert(!p.ready(2001,false) && p.state()==State::WaitingRadio);
  assert(!p.started(2002));
  for(bool enabled:{false,true}) {
    p.reset(0,enabled,true,true); // saved manual/verified trim OR digital correction
    assert(p.state()==State::Retained && p.calibrated());
    assert(!p.ready(0,true) && !p.ready(4000000000UL,true) && p.attempts()==0);
  }
  p.reset(0,false,false,true); // explicit persisted opt-out, default0 is uncalibrated
  assert(p.state()==State::Disabled && !p.ready(4000000,true));
  p.setEnabled(4000000,true); assert(p.ready(4000000,true));
}
static void budget() {
  Policy p; p.reset(100,true,false,true);
  assert(p.ready(100,true) && p.started(100));
  assert(p.inProgress() && p.state()==State::WaitingReferences && p.attempts()==1);
  assert(!p.ready(101,true) && !p.started(101));
  p.finished(200,false,true);
  assert(!p.inProgress() && p.state()==State::Backoff && p.nextDelay(200)==Policy::BackoffMs);
  assert(!p.ready(200+Policy::BackoffMs-1,true));
  p.setEnabled(201,false); p.setEnabled(202,true);
  assert(p.attempts()==1 && !p.ready(202,true));
  assert(p.ready(200+Policy::BackoffMs,true));
  assert(p.started(200+Policy::BackoffMs) && p.attempts()==2);
  p.finished(300+Policy::BackoffMs,false,true);
  assert(p.state()==State::Exhausted && !p.ready(4000000000UL,true));
  p.setEnabled(400,false);p.setEnabled(500,true);
  assert(p.state()==State::Exhausted && p.attempts()==2 && !p.started(500));
}
static void wrap() {
  for(uint32_t start:{0U,UINT32_MAX-1234U}) {
    Policy p;p.reset(start,true,false,true);assert(p.ready(start,true)&&p.started(start));
    assert(!p.expired(start+Policy::AttemptTimeoutMs-1));
    assert(p.expired(start+Policy::AttemptTimeoutMs));
    assert(!p.ready(start+Policy::AttemptTimeoutMs,true)); // owner must cancel/restore, not fake finish
    const uint32_t finished=start+Policy::AttemptTimeoutMs+77;
    p.finished(finished,false,true);
    assert(p.nextDelay(finished+Policy::BackoffMs-1)==1);
    assert(!p.ready(finished+Policy::BackoffMs-1,true));
    assert(p.nextDelay(finished+Policy::BackoffMs)==0);
    assert(!p.ready(finished+Policy::BackoffMs,false));
    // A complete later millis wrap cannot restart an already-observed wait.
    assert(!p.ready(finished+2,false) && p.nextDelay(finished+2)==0);
    assert(p.ready(finished+Policy::BackoffMs,true));
  }
}
static void cancelled() {
  for(bool running:{false,true}) {
    Policy p;p.reset(100,true,false,true);
    if(running){assert(p.ready(100,true)&&p.started(100));}
    p.cancel();assert(p.state()==State::Cancelled);
    assert(p.inProgress()==running); // cancellation never invents completed rollback
    p.setEnabled(200,false);p.setEnabled(300,true);
    assert(p.state()==State::Cancelled && !p.ready(3000000,true));
    if(running)p.finished(500,false,true);
    assert(!p.inProgress() && p.state()==State::Cancelled && !p.ready(3000000,true));
    p.reset(900,true,false,true);assert(p.ready(900,true)&&p.attempts()==0);
  }
}
static void error_and_success() {
  Policy p;p.reset(0,true,false,false);assert(p.state()==State::Error);
  p.setEnabled(1,false);p.setEnabled(2,true);assert(!p.ready(2,true));
  p.reset(0,true,false,true);assert(p.ready(0,true)&&p.started(0));
  p.finished(100,false,false);assert(p.state()==State::Error && !p.inProgress());
  p.setEnabled(1000,true);assert(!p.ready(4000000,true));
  p.reset(0,true,false,true);assert(p.ready(0,true)&&p.started(0));
  p.finished(100,true,true);assert(p.state()==State::Retained && p.calibrated());
  assert(!p.ready(4000000,true));p.setEnabled(101,false);p.setEnabled(102,true);
  assert(!p.ready(4000000,true)&&p.attempts()==1);
  p.finished(103,false,false);assert(p.state()==State::Retained); // stray finish cannot replace owned success
}
static void disable_during_run() {
  Policy p;p.reset(0,true,false,true);assert(p.ready(0,true)&&p.started(0));
  p.setEnabled(5,false);assert(p.state()==State::Disabled && p.inProgress());
  assert(!p.ready(500,true));p.finished(600,false,true);
  assert(!p.inProgress()&&p.state()==State::Disabled);
  p.setEnabled(700,true);assert(p.state()==State::Backoff&&p.attempts()==1);
  assert(!p.ready(700,true));
}
int main(int argc,char**argv) {
  assert(argc==2);
  if(!strcmp(argv[1],"default"))default_and_retained();
  else if(!strcmp(argv[1],"budget"))budget();
  else if(!strcmp(argv[1],"wrap"))wrap();
  else if(!strcmp(argv[1],"cancel"))cancelled();
  else if(!strcmp(argv[1],"errors"))error_and_success();
  else if(!strcmp(argv[1],"disable"))disable_during_run();
  else assert(false);
  puts("PASS");
}
'''


class BootAutoTrim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:raise unittest.SkipTest('native C++ compiler required')
        cls.temp=tempfile.TemporaryDirectory(prefix='meshcore-boot-trim-')
        cls.addClassCleanup(cls.temp.cleanup)
        folder=Path(cls.temp.name);source=folder/'policy.cpp';source.write_text(SOURCE)
        cls.binary=folder/'policy'
        compiled=subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror',
            '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',
            '-I'+str(ROOT/'src'),str(source),'-o',str(cls.binary)],capture_output=True,text=True,timeout=30)
        if compiled.returncode:raise AssertionError(compiled.stdout+compiled.stderr)

    def check(self,case):
        result=subprocess.run([str(self.binary),case],capture_output=True,text=True,timeout=5,
            env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS',result.stdout)
    def test_default_waiting_ownership_saved_manual_or_digital_retained_and_optout(self):self.check('default')
    def test_two_attempt_budget_and_thirty_minute_backoff_cannot_be_reset_by_setting(self):self.check('budget')
    def test_exact_fifteen_minute_window_and_thirty_minute_retry_survive_clock_wrap(self):self.check('wrap')
    def test_explicit_cancel_is_sticky_current_boot_and_requires_actual_restore_completion(self):self.check('cancel')
    def test_storage_rollback_failures_are_sticky_and_verified_calibration_is_retained(self):self.check('errors')
    def test_disable_while_running_never_implies_a_completed_hardware_restore(self):self.check('disable')


if __name__=='__main__':unittest.main()
