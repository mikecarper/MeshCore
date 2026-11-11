"""Run the production extrema encoder and independently decode its wire format."""
import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <helpers/TelemetryDailyExtrema.h>
using mesh::TelemetryDailyExtrema;

struct Reader {
  const uint16_t* values; size_t count; uint16_t absent;
  uint16_t operator()(uint16_t offset) const {
    return offset < count ? values[offset] : absent;
  }
};
void fill(uint16_t* p, size_t n, uint16_t value) {
  for (size_t i=0; i<n; ++i) p[i]=value;
}
int main() {
  uint16_t t[337],v[337];
  fill(t,337,0);fill(v,337,0);
  // Explicit low/high sentinels sort outside the valid -50..77 C range.
  t[0]=20;t[1]=1;t[2]=40;t[3]=2;t[4]=40;t[5]=131;
  t[47]=1;t[48]=2;t[49]=20;
  // Equal extrema choose the more recent sample, including saturated values.
  v[0]=2;v[1]=255;v[2]=2;v[47]=1;v[48]=255;v[49]=3;
  char result[157];
  const uint32_t bucket=1000000;
  Reader tr={t,337,0},br={v,337,0};
  assert(TelemetryDailyExtrema::format(TelemetryDailyExtrema::TEMPERATURE,0,
      bucket,1800,tr,result,sizeof(result),true));
  puts(result);
  assert(TelemetryDailyExtrema::format(TelemetryDailyExtrema::BATTERY_VOLTAGE,0,
      bucket,1800,br,result,sizeof(result),false));
  puts(result);
  // Guard both sides of the 88-character maximum and do not truncate.
  struct { char pre;char out[88];char post; } guarded = {'a',{},'z'};
  assert(!TelemetryDailyExtrema::format(TelemetryDailyExtrema::TEMPERATURE,0,
      bucket,1800,tr,guarded.out,sizeof(guarded.out),false));
  assert(guarded.pre=='a' && guarded.post=='z' && guarded.out[0]=='\0');
  char exact[89];
  assert(TelemetryDailyExtrema::format(TelemetryDailyExtrema::TEMPERATURE,0,
      bucket,1800,tr,exact,sizeof(exact),false));
  assert(strlen(exact)==88);
  assert(!TelemetryDailyExtrema::format(TelemetryDailyExtrema::TEMPERATURE,0,
      bucket,1800,tr,exact,sizeof(exact),true));
  assert(exact[0]=='\0');
  assert(!TelemetryDailyExtrema::format(TelemetryDailyExtrema::TEMPERATURE,0,
      bucket,1800,tr,nullptr,0,false));
  assert(!TelemetryDailyExtrema::format(TelemetryDailyExtrema::TEMPERATURE,0,
      bucket,0,tr,result,sizeof(result),false));
  assert(!TelemetryDailyExtrema::format(TelemetryDailyExtrema::TEMPERATURE,1,
      bucket,1800,tr,result,sizeof(result),false));
  assert(!TelemetryDailyExtrema::format(static_cast<TelemetryDailyExtrema::Series>(3),0,
      bucket,1800,tr,result,sizeof(result),false));
  assert(!TelemetryDailyExtrema::format(static_cast<TelemetryDailyExtrema::Series>(0),0,
      bucket,1800,tr,result,sizeof(result),false));
  assert(!TelemetryDailyExtrema::format(TelemetryDailyExtrema::TEMPERATURE,0,
      UINT32_MAX/1800U+1U,1800,tr,result,sizeof(result),false));
  assert(result[0]=='\0');
  // No invented earlier sample exists before epoch zero, even if reader has it.
  assert(TelemetryDailyExtrema::format(TelemetryDailyExtrema::TEMPERATURE,0,
      0,1800,tr,result,sizeof(result),false));
  // The combined packet carries both complete series inside one CLI reply.
  assert(TelemetryDailyExtrema::formatTemperatureVoltage(
      bucket,tr,br,result,sizeof(result),true));
  assert(strlen(result)==154);puts(result);
  struct { char pre;char out[154];char post; } combined_guard = {'a',{},'z'};
  assert(!TelemetryDailyExtrema::formatTemperatureVoltage(
      bucket,tr,br,combined_guard.out,sizeof(combined_guard.out),true));
  assert(combined_guard.pre=='a' && combined_guard.post=='z'
         && combined_guard.out[0]=='\0');
  char combined_exact[155];
  assert(TelemetryDailyExtrema::formatTemperatureVoltage(
      bucket,tr,br,combined_exact,sizeof(combined_exact),true));
  assert(strlen(combined_exact)==154);
  char combined_bare[153];
  assert(TelemetryDailyExtrema::formatTemperatureVoltage(
      bucket,tr,br,combined_bare,sizeof(combined_bare),false));
  assert(strlen(combined_bare)==152);
  assert(!TelemetryDailyExtrema::formatTemperatureVoltage(
      bucket,tr,br,nullptr,0,false));
  assert(!TelemetryDailyExtrema::formatTemperatureVoltage(
      UINT32_MAX/1800U+1U,tr,br,result,sizeof(result),false));
  assert(result[0]=='\0');
  assert(TelemetryDailyExtrema::formatTemperatureVoltage(
      0,tr,br,result,sizeof(result),false));
  puts(result);
}
'''


def decode(text):
    raw = base64.b64decode(text.removeprefix("> "), validate=True)
    magic, version, series, channel, minutes = raw[:5]
    epoch = int.from_bytes(raw[5:9], "little")
    bits = {1: 8, 2: 8}[series]
    stream = "".join(f"{x:08b}" for x in raw[9:])
    pos, rows = 0, []
    for _ in range(7):
        min_index = int(stream[pos:pos + 7], 2)
        max_index = int(stream[pos + 7:pos + 14], 2)
        pos += 14
        values = []
        for _ in range(6):
            values.append(int(stream[pos:pos + bits], 2))
            pos += bits
        rows.append((min_index, max_index, values))
    assert all(bit == "0" for bit in stream[pos:])
    return (magic, version, series, channel, minutes, epoch), rows


def decode_combined(text):
    raw = base64.b64decode(text.removeprefix("> "), validate=True)
    assert len(raw) == 112
    header = (raw[0], raw[1], raw[2], int.from_bytes(raw[3:7], "little"))
    stream = "".join(f"{x:08b}" for x in raw[7:])
    pos, rows = 0, []
    for _ in range(7):
        day = []
        for _ in range(2):
            min_index = int(stream[pos:pos + 6], 2)
            max_index = int(stream[pos + 6:pos + 12], 2)
            pos += 12
            values = [int(stream[pos + i*8:pos + (i+1)*8], 2) for i in range(6)]
            pos += 48
            day.append((min_index, max_index, values))
        rows.append(day)
    assert pos == len(stream)
    return header, rows


class DailyExtremaTests(unittest.TestCase):
    def test_roundtrip_boundaries_ties_missing_and_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "test.cpp"
            binary = Path(folder) / "test"
            source.write_text(HARNESS)
            built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11",
                                    "-Wall", "-Wextra", "-Werror",
                                    "-fsanitize=undefined", "-fno-sanitize-recover=all",
                                    "-I", str(ROOT / "src"), str(source), "-o", str(binary)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)
        lines = ran.stdout.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("> "))
        parsed = [decode(line) for line in lines[:2]]
        self.assertEqual(parsed[0][0], (0x17, 1, 1, 0, 30, 1800000000))
        self.assertEqual(parsed[0][1][0], (1, 3, [40, 1, 20, 40, 2, 40]))
        self.assertEqual(parsed[0][1][1], (1, 0, [0, 20, 2, 20, 2, 1]))
        self.assertEqual(parsed[0][1][2:], [(127, 127, [0]*6)]*5)
        self.assertEqual(parsed[1][1][0], (47, 1, [255, 1, 0, 2, 255, 2]))
        self.assertEqual(parsed[1][1][1], (1, 0, [0, 3, 255, 3, 255, 1]))
        self.assertTrue(lines[2].startswith("> "))
        header, combined = decode_combined(lines[2])
        self.assertEqual(header, (0x18, 1, 30, 1800000000))
        for day in range(7):
            for series in range(2):
                source = parsed[series][1][day]
                expected = tuple(63 if i == 127 else i for i in source[:2])
                self.assertEqual(combined[day][series], expected + (source[2],))
        header, combined = decode_combined(lines[3])
        self.assertEqual(header[-1], 0)
        self.assertEqual(combined[0][0], (0, 0, [0, 20, 0, 0, 20, 0]))
        self.assertEqual(combined[0][1], (0, 0, [0, 2, 0, 0, 2, 0]))
        self.assertEqual(combined[1:], [[(63, 63, [0]*6)]*2]*6)


if __name__ == "__main__":
    unittest.main()
