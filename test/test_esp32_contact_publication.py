#!/usr/bin/env python3
from pathlib import Path
import json,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/"test/fixtures/contact_publication/test.cpp"
class Publication(unittest.TestCase):
 def run_native(self,mode,platform='ESP32_PLATFORM',negative=None):
  source=(ROOT/'src/helpers/ContactFileTransaction.h').read_text().replace('#include "IdentityStore.h"','// native supplied File').replace('#include "PersistentStoreFormat.h"','#include <helpers/PersistentStoreFormat.h>')
  if negative=='cleanup':
   a='if (!defer_backup_cleanup)';self.assertEqual(source.count(a),1);source=source.replace(a,'if (true)')
  elif negative=='retirement':
   a='ok = _fs->remove(_backup);';self.assertEqual(source.count(a),1);source=source.replace(a,'ok = true; // negative: leave previous backup',1)
  elif negative=='movement':
   a='if (!source && destination) return MoveState::Moved;';self.assertEqual(source.count(a),1)
   source=source.replace(a,'return MoveState::Moved; // negative: ignore observed names')
  with tempfile.TemporaryDirectory(prefix='mesh-publication-') as folder:
   p=Path(folder);(p/'transaction_under_test.h').write_text(source);binary=p/'test'
   result=subprocess.run(['c++','-std=c++17','-O1','-g','-Wall','-Wextra','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer','-fno-pie','-no-pie','-D'+platform+'=1','-I',str(p),'-I',str(ROOT/'src'),'-I',str(ROOT/'test/fixtures/contact_cache'),'-I',str(ROOT/'test/fixtures/contact_cache/mocks'),str(FIXTURE),'-o',str(binary)],text=True,capture_output=True)
   self.assertEqual(result.returncode,0,result.stderr)
   if negative=='movement':
    healthy=subprocess.run([str(binary),'contracts'],text=True,capture_output=True)
    self.assertEqual(healthy.returncode,0,healthy.stdout+healthy.stderr)
   result=subprocess.run([str(binary),mode],text=True,capture_output=True)
   if negative:
    self.assertNotEqual(result.returncode,0,result.stdout)
    self.assertIn('writer.serviceCommit(true,8,deferred)' if negative=='movement' else 'SPIFFS.',result.stderr)
   else:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
 def test_default_and_deferred_success_contracts(self):self.run_native('contracts')
 def test_retirement_and_recovery_failure_before_temp_ownership(self):self.run_native('retire')
 def test_repeated_transaction_all_begin_cancellation_boundaries(self):self.run_native('cancel')
 def test_publish_failures_and_every_rename_crash(self):self.run_native('faults')
 def test_251_502_counts_and_unchanged_commit_cadence(self):self.run_native('counts')
 def test_other_platform_retains_cleanup_despite_true_arg(self):self.run_native('contracts','RP2040_PLATFORM')
 def test_removed_defer_guard_is_detected(self):self.run_native('contracts',negative='cleanup')
 def test_omitted_retirement_is_detected(self):self.run_native('retire',negative='retirement')
 def test_rename_outcomes_follow_observed_names_and_remain_terminal(self):
  for platform in ('ESP32_PLATFORM','RP2040_PLATFORM'):
   with self.subTest(platform=platform):self.run_native('outcomes',platform)
 def test_forced_move_success_is_detected_without_breaking_healthy_publication(self):
  for platform in ('ESP32_PLATFORM','RP2040_PLATFORM'):
   with self.subTest(platform=platform):self.run_native('outcomes',platform,negative='movement')
 def test_post_move_metadata_errors_preserve_all_remaining_authorities(self):
  for platform in ('ESP32_PLATFORM','RP2040_PLATFORM'):
   with self.subTest(platform=platform):self.run_native('metadata',platform)
 def test_ambiguous_names_never_trigger_speculative_rollback_or_cleanup(self):
  for platform in ('ESP32_PLATFORM','RP2040_PLATFORM'):
   with self.subTest(platform=platform):self.run_native('ambiguous',platform)
 def test_missing_original_target_uses_the_same_publication_contract(self):
  for platform in ('ESP32_PLATFORM','RP2040_PLATFORM'):
   with self.subTest(platform=platform):self.run_native('fresh',platform)
 def test_static_and_constructor_recovery_confirm_moves_without_losing_scratch(self):
  for platform in ('ESP32_PLATFORM','RP2040_PLATFORM'):
   with self.subTest(platform=platform):self.run_native('recovery',platform)
if __name__=='__main__':unittest.main()
