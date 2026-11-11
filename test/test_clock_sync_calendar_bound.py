#!/usr/bin/env python3
"""Compare constant clock bounds with the prior RTClib calendar semantics."""
import calendar
import datetime
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UTC = datetime.timezone.utc


def epoch(year, month, day, hour=0, minute=0, second=0):
    return int(datetime.datetime(year, month, day, hour, minute, second, tzinfo=UTC).timestamp())


def previous_upper(build, years=10):
    date = datetime.datetime.fromtimestamp(build, UTC)
    year = (date.year + years) & 0xffff
    day = date.day
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    if date.month == 2 and day == 29 and not leap:
        day = 28
    # Match DateTime's uint8 year offset and uint16 date2days result, including
    # its documented 2000-based four-year arithmetic beyond supported 2099.
    offset = (year - 2000 if year >= 2000 else year) & 0xff
    days = day + sum((31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30)[:date.month - 1])
    days += int(date.month > 2 and offset % 4 == 0)
    days = (days + 365 * offset + (offset + 3) // 4 - 1) & 0xffff
    return (946684800 + days * 86400 + date.hour * 3600 + date.minute * 60 + date.second) & 0xffffffff


REFERENCE_DATETIME = r'''
struct DateTime {
  static unsigned constructions;
  uint8_t y, m, d, hh, mm, ss;
  explicit DateTime(uint32_t epoch) {
    ++constructions;
    uint32_t t = epoch - 946684800UL;
    ss=t%60; t/=60; mm=t%60; t/=60; hh=t%24;
    uint16_t days=t/24;
    y=0;
    while (days >= 365U+(y%4==0)) { days-=365U+(y%4==0); ++y; }
    static const unsigned lengths[]={31,28,31,30,31,30,31,31,30,31,30};
    m=1;
    while (m<12) {
      const unsigned length=lengths[m-1]+(m==2 && y%4==0);
      if (days<length) break;
      days-=length; ++m;
    }
    d=days+1;
  }
  DateTime(const char*,const char*):DateTime(1078058096UL) {}
  DateTime(uint16_t year,uint8_t month,uint8_t day,uint8_t h,uint8_t mi,uint8_t s)
    :y(year>=2000?year-2000:year),m(month),d(day),hh(h),mm(mi),ss(s) {++constructions;}
  uint16_t year()const{return 2000+y;}
  uint8_t month()const{return m;} uint8_t day()const{return d;}
  uint8_t hour()const{return hh;} uint8_t minute()const{return mm;} uint8_t second()const{return ss;}
  uint32_t unixtime()const {
    static const unsigned lengths[]={31,28,31,30,31,30,31,31,30,31,30};
    uint16_t days=d;
    for(unsigned i=1;i<m;++i) days+=lengths[i-1];
    if(m>2 && y%4==0) ++days;
    days=days+365*y+(y+3)/4-1;
    return 946684800UL+uint32_t(days)*86400UL+hh*3600UL+mm*60UL+ss;
  }
};
unsigned DateTime::constructions=0;
'''


class ClockSyncCalendarBoundTests(unittest.TestCase):
    def test_constant_bounds_match_old_calendar_and_need_no_datetime_code(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler required")
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        helpers = source[source.index("static constexpr bool clockSyncLeapYear("):
                         source.index("// Channel encryption uses", source.index("static constexpr bool clockSyncLeapYear("))]
        checks = []
        samples = []
        for year in range(2000, 2100):
            for month in range(1, 13):
                days = {1, calendar.monthrange(year, month)[1]}
                if month == 2 and calendar.isleap(year):
                    days.add(28)
                for day in sorted(days):
                    for hour, minute, second in ((0, 0, 0), (12, 34, 56), (23, 59, 59)):
                        build = epoch(year, month, day, hour, minute, second)
                        for years in (0, 1, 4, 10, 99):
                            samples.append(f"{build} {years} {previous_upper(build, years)}\n")
        for date in ((2000, 2, 29), (2024, 2, 29), (2026, 11, 11), (2092, 2, 29), (2099, 12, 31)):
            build = epoch(*date, 23, 59, 59)
            checks.append(f"static_assert(clockSyncBuildMaximum({build}UL,10)=={previous_upper(build)}UL,\"leap boundary\");")
        with tempfile.TemporaryDirectory(prefix="mesh-clock-calendar-") as folder:
            binary = Path(folder) / "calendar"
            for configured in (epoch(2026, 11, 11), epoch(2024, 2, 29, 12, 34, 56), 0):
                build = configured or 1078058096
                expected = previous_upper(build)
                # Nonzero build metadata deliberately has no DateTime class:
                # a runtime-calendar dependency would be a compilation error.
                harness = "#include <cstdint>\n#include <cassert>\n#include <iostream>\n"
                harness += f"#define FIRMWARE_BUILD_EPOCH {configured}UL\n#define CLOCK_SYNC_VALID_YEARS 10\n"
                if not configured:
                    harness += REFERENCE_DATETIME
                harness += helpers + "\n" + "\n".join(checks)
                harness += f'''
int main() {{
  assert(clockSyncMinimumValidEpoch()=={build}UL);
  assert(clockSyncMaximumValidEpoch()=={expected}UL);
  assert(!clockSyncEpochIsValid({build-1}UL));
  assert(clockSyncEpochIsValid({build}UL));
  assert(clockSyncEpochIsValid({expected}UL));
  assert(!clockSyncEpochIsValid({expected+1}UL));
  uint32_t build,years,expected;
  while(std::cin>>build>>years>>expected)
    assert(clockSyncBuildMaximum(build,uint16_t(years))==expected);
'''
                if not configured:
                    harness += "  assert(DateTime::constructions==3);\n"
                harness += "}\n"
                compiled = subprocess.run([compiler, "-std=c++11", "-O0", "-Wall", "-Wextra", "-Werror",
                                           "-x", "c++", "-", "-o", str(binary)], input=harness,
                                          text=True, capture_output=True, timeout=30)
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                ran = subprocess.run([str(binary)], input="".join(samples) if configured else "",
                                     text=True, capture_output=True, timeout=15)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
