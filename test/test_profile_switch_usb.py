"""Check firmware-side USB results are immutable, bounded and sequence-scoped."""
from pathlib import Path
import unittest
import test_sx1262_batched_modulation as compiler

ROOT = Path(__file__).resolve().parents[1]


class UsbResultCacheTests(unittest.TestCase):
    compile_run = compiler.BatchedModulationTests.compile_run

    def test_cache_replay_never_executes_an_operation(self):
        source = (ROOT / "tools/hil/ProfileSwitchUsb.h").read_text().replace("#pragma once", "")
        harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
struct {
  std::string output;unsigned flushes=0;
  void print(const char* text) { output+=text; }
  void println(const char* text) { output+=text;output+='\n'; }
  void flush() { ++flushes; }
} Serial;
@SOURCE@
int main() {
  recordBenchResult(lastTxResult,42,"{\"sent\":%u,\"rc\":0}\n",42u);
  const auto expected=Serial.output;assert(lastTxResult.sequence==42);
  Serial.output.clear();replayBenchResult("sent",42);assert(Serial.output==expected);
  Serial.output.clear();replayBenchResult("sent",43);
  assert(Serial.output.find("unavailable")!=std::string::npos);
  Serial.output.clear();replayBenchResult("listening",42);
  assert(Serial.output.find("unavailable")!=std::string::npos);
  Serial.output.clear();recordBenchResult(lastTxResult,43,"%03000d",1);
  assert(lastTxResult.sequence==0 && Serial.output.find("overflow")!=std::string::npos);
  Serial.output.clear();replayBenchResult("sent",42);
  assert(Serial.output.find("unavailable")!=std::string::npos);
  assert(Serial.flushes==2);
}
'''
        self.compile_run(harness.replace("@SOURCE@", source))


if __name__ == "__main__":
    unittest.main()
