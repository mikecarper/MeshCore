"""Preserve every advert record across real RTC banks and owner lifecycles."""
from pathlib import Path
import os
import re
import resource
import signal
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/FloodAdvertLimiter.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <type_traits>
@BANKS@
#if FLOOD_ADVERT_RTC_FAST_SLOTS || FLOOD_ADVERT_RTC_SLOW_SLOTS
#define MAKE_LIMITER(name) mesh::StaticFloodAdvertLimiter<> name(repeaterRtcAdvertFastBank(), repeaterRtcAdvertSlowBank())
#else
#define MAKE_LIMITER(name) mesh::StaticFloodAdvertLimiter<> name
#endif
#define CHECK(value) do { if (!(value)) std::abort(); } while (0)
static void keyFor(unsigned i, uint8_t key[PUB_KEY_SIZE]) {
 memset(key, 0, PUB_KEY_SIZE);
 key[0] = uint8_t(i); key[1] = uint8_t(i >> 8);
 key[31] = uint8_t(i ^ 0xa5);
}
static void show(mesh::FloodAdvertLimiter& limiter, uint32_t now) {
 mesh::FloodAdvertLimiter::LimitedEntry rows[128];
 size_t count = limiter.listLimited(now, 0, rows, 128);
 std::printf("limited=%u\n", unsigned(count));
 for (size_t i = 0; i < count; ++i)
  std::printf("%u %u %u %u %u %u %u %u\n", rows[i].key[0],rows[i].key[1],
   rows[i].wait_ms,rows[i].recovery_ms,rows[i].forwarded,rows[i].quota,rows[i].hops,rows[i].reasons);
}
static void populate(mesh::FloodAdvertLimiter& limiter, uint32_t now) {
 for (unsigned i = 0; i < 128; ++i) {
  uint8_t key[PUB_KEY_SIZE],hash[MAX_HASH_SIZE] = {};
  keyFor(i,key);
  for (unsigned h=0; h < 11; ++h) {
   hash[0]=uint8_t(h);hash[7]=uint8_t(i);
   limiter.observe(key,hash,8,now+h);
   auto d=limiter.check(key,hash,now+h);
   std::printf("%u:%u:%u ",i,h,unsigned(d));
   if(d==mesh::FloodAdvertLimiter::Decision::Allow) limiter.commit(key,hash,now+h);
   limiter.noteKnownCopy(key,hash,now+h+1);
  }
 }
 std::puts("");
}
int main(int argc,char**argv) {
 const char* mode=argc>1?argv[1]:"trace";
#if FLOOD_ADVERT_RTC_FAST_SLOTS
 if(!std::strcmp(mode,"misaligned")) {
  mesh::StaticFloodAdvertLimiter<> invalid(
    reinterpret_cast<mesh::FloodAdvertLimiter::Entry*>(
      reinterpret_cast<unsigned char*>(repeaterRtcAdvertFastBank())+1),
    repeaterRtcAdvertSlowBank());
  return 0;
 }
#endif
 if(!std::strcmp(mode,"owners")) {
  MAKE_LIMITER(first);
  MAKE_LIMITER(second);
  return 0;
 }
 if(!std::strcmp(mode,"lifetime")) {
#if FLOOD_ADVERT_RTC_FAST_SLOTS
  std::memset(repeater_rtc_advert_fast,0xff,sizeof(repeater_rtc_advert_fast));
#endif
#if FLOOD_ADVERT_RTC_SLOW_SLOTS
  std::memset(repeater_rtc_advert_slow,0xff,sizeof(repeater_rtc_advert_slow));
#endif
  for(unsigned restart=0;restart<3;++restart) {
   MAKE_LIMITER(limiter);
   CHECK(limiter.listLimited(0,0,nullptr,0)==0);
#if FLOOD_ADVERT_RTC_FAST_SLOTS
   for(unsigned char b: reinterpret_cast<const unsigned char(&)[sizeof(repeater_rtc_advert_fast)]>(repeater_rtc_advert_fast)) CHECK(b==0);
#endif
#if FLOOD_ADVERT_RTC_SLOW_SLOTS
   for(unsigned char b: reinterpret_cast<const unsigned char(&)[sizeof(repeater_rtc_advert_slow)]>(repeater_rtc_advert_slow)) CHECK(b==0);
#endif
   populate(limiter,1000);
   CHECK(limiter.listLimited(1011,0,nullptr,0)==128);
#if FLOOD_ADVERT_RTC_FAST_SLOTS
   CHECK(repeater_rtc_advert_fast[0].key[0] == 128-FLOOD_ADVERT_RTC_FAST_SLOTS-FLOOD_ADVERT_RTC_SLOW_SLOTS);
   CHECK(repeater_rtc_advert_fast[FLOOD_ADVERT_RTC_FAST_SLOTS-1].key[0] == 127-FLOOD_ADVERT_RTC_SLOW_SLOTS);
#endif
#if FLOOD_ADVERT_RTC_SLOW_SLOTS
   CHECK(repeater_rtc_advert_slow[0].key[0] == 128-FLOOD_ADVERT_RTC_SLOW_SLOTS);
   CHECK(repeater_rtc_advert_slow[FLOOD_ADVERT_RTC_SLOW_SLOTS-1].key[0] == 127);
#endif
   limiter.reset();
   CHECK(limiter.listLimited(1011,0,nullptr,0)==0);
   populate(limiter,2000);
  }
  return 0;
 }
 MAKE_LIMITER(limiter);
 for(unsigned epoch=0;epoch<2;++epoch) {
  uint32_t now=epoch?0xfffff000U:1000U;
  limiter.reset();populate(limiter,now);show(limiter,now+20);
  now+=mesh::FloodAdvertLimiter::WINDOW_MS+1;
  populate(limiter,now);show(limiter,now+20);
  uint8_t extra[PUB_KEY_SIZE],hash[MAX_HASH_SIZE]={};keyFor(511,extra);
  limiter.observe(extra,hash,8,now+30);
  std::printf("capacity=%u\n",unsigned(limiter.check(extra,hash,now+30)));
  uint8_t first[PUB_KEY_SIZE];keyFor(0,first);limiter.clear(first);
  limiter.observe(extra,hash,8,now+31);
  std::printf("cleared=%u\n",unsigned(limiter.check(extra,hash,now+31)));
  show(limiter,now+mesh::FloodAdvertLimiter::BAD_INTERVAL_MS);
  limiter.tick(now+mesh::FloodAdvertLimiter::RECOVERY_MS+100);
  show(limiter,now+mesh::FloodAdvertLimiter::RECOVERY_MS+100);
 }
}
'''


class FloodAdvertRtcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='advert-rtc-')
        cls.work=Path(cls.temp.name)
        (cls.work/'sdkconfig.h').write_text('#define CONFIG_IDF_TARGET_ESP32S3 1\n#define CONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP 1\n')
        (cls.work/'esp_attr.h').write_text('#define RTC_FAST_ATTR __attribute__((section(".rtc.force_fast")))\n#define RTC_SLOW_ATTR __attribute__((section(".rtc.force_slow")))\n')
        src=(ROOT/'examples/simple_repeater/MyMesh.cpp').read_text()
        start=src.index('#if FLOOD_ADVERT_RTC_FAST_SLOTS\n')
        end=src.index('\n#endif',src.index('mesh::FloodAdvertLimiter::Entry* repeaterRtcAdvertSlowBank()',start))+len('\n#endif')
        end=src.index('\n#endif',end)+len('\n#endif')
        cls.banks=src[start:end]
        cls.compiler=[os.environ.get('CXX','g++')]

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def build(self,fast,slow):
        name='probe-%d-%d'%(fast,slow)
        cpp=self.work/(name+'.cpp');binary=self.work/name
        cpp.write_text(HARNESS.replace('@BANKS@',self.banks))
        flags=['-std=c++11','-Os','-DNDEBUG','-Wall','-Wextra','-Werror',
               '-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',
               '-DFLOOD_ADVERT_RTC_FAST_SLOTS='+str(fast),'-DFLOOD_ADVERT_RTC_SLOW_SLOTS='+str(slow),
               '-DESP32_PLATFORM=1','-DMESHCORE_EXPANDED_PARTITION_PROFILE=1',
               '-I',str(self.work),'-isystem',str(ROOT/'src'),'-isystem',str(ROOT/'test/mocks')]
        r=subprocess.run(self.compiler+flags+[str(cpp),str(ROOT/'src/helpers/FloodAdvertLimiter.cpp'),'-o',str(binary)],capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        return binary

    def test_all_records_and_decisions_match_original_contiguous_table(self):
        baseline=subprocess.run([str(self.build(0,0))],capture_output=True,text=True,check=True,timeout=15).stdout
        self.assertIn('limited=128',baseline)
        for fast,slow in ((50,50),(16,0),(16,32)):
            with self.subTest(fast=fast,slow=slow):
                r=subprocess.run([str(self.build(fast,slow))],capture_output=True,text=True,timeout=15)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(r.stdout,baseline)

    def test_retained_bytes_reset_and_all_bank_boundaries_survive_recreation(self):
        for fast,slow in ((50,50),(16,0),(16,32)):
            with self.subTest(fast=fast,slow=slow):
                r=subprocess.run([str(self.build(fast,slow)),'lifetime'],capture_output=True,text=True,timeout=15)
                self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_release_build_rejects_a_second_owner(self):
        def no_core(): resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        r=subprocess.run([str(self.build(50,50)),'owners'],capture_output=True,text=True,preexec_fn=no_core,timeout=15)
        self.assertEqual(r.returncode,-signal.SIGABRT,r.stdout+r.stderr)

    def test_concrete_sections_and_small_constructor_stack(self):
        source=self.work/'sections.cpp'
        source.write_text('#include <helpers/FloodAdvertLimiter.h>\n'+self.banks+
            '\nmesh::StaticFloodAdvertLimiter<> limiter(repeaterRtcAdvertFastBank(),repeaterRtcAdvertSlowBank());\n'
            'void reset_limiter() { limiter.reset(); }\n')
        obj=self.work/'sections.o'
        r=subprocess.run(self.compiler+['-std=c++11','-Os','-DNDEBUG','-fstack-usage',
            '-DFLOOD_ADVERT_RTC_FAST_SLOTS=50','-DFLOOD_ADVERT_RTC_SLOW_SLOTS=50',
            '-DESP32_PLATFORM=1','-DMESHCORE_EXPANDED_PARTITION_PROFILE=1',
            '-I',str(self.work),'-isystem',str(ROOT/'src'),'-isystem',str(ROOT/'test/mocks'),
            '-c',str(source),'-o',str(obj)],capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        table=subprocess.run(['objdump','-h',str(obj)],capture_output=True,text=True,check=True).stdout
        for bank in ('fast','slow'):
            row=next(line for line in table.splitlines() if '.rtc.force_'+bank in line)
            self.assertEqual(int(row.split()[2],16),7000)
            self.assertGreaterEqual(int(row.split()[-1].split('**')[-1]),2)
        stack=(self.work/'sections.su').read_text()
        for line in stack.splitlines():
            self.assertLessEqual(int(line.split('\t')[1]),256,line)

    def test_default_layout_has_no_rtc_owner_or_sdk_dependency(self):
        source=self.work/'default.cpp'
        source.write_text('#include <helpers/FloodAdvertLimiter.h>\n'
            '#include <cstddef>\n'
            'constexpr size_t base = ((2*sizeof(void*)+sizeof(uint32_t)+alignof(void*)-1)/alignof(void*))*alignof(void*);\n'
            'static_assert(sizeof(mesh::StaticFloodAdvertLimiter<>)==base+128*140,"original default layout");\n'
            'mesh::StaticFloodAdvertLimiter<> limiter;\n')
        obj=self.work/'default.o'
        r=subprocess.run(self.compiler+['-std=c++11','-Os','-DNDEBUG',
            '-isystem',str(ROOT/'src'),'-isystem',str(ROOT/'test/mocks'),
            '-c',str(source),'-o',str(obj)],capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        symbols=subprocess.run(['nm','-C',str(obj)],capture_output=True,text=True,check=True).stdout
        self.assertNotIn('EntryStorage',symbols)
        self.assertNotIn('repeaterRtcAdvert',symbols)

    def test_release_build_rejects_unaligned_bank(self):
        def no_core(): resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        r=subprocess.run([str(self.build(50,50)),'misaligned'],capture_output=True,text=True,preexec_fn=no_core,timeout=15)
        self.assertEqual(r.returncode,-signal.SIGABRT,r.stdout+r.stderr)

    def test_invalid_hardware_and_capacity_policies_fail_compilation(self):
        source=self.work/'invalid.cpp'
        source.write_text('#include <helpers/FloodAdvertLimiter.h>\n'
            'mesh::StaticFloodAdvertLimiter<>* pointer;\n')
        common=['-std=c++11','-fsyntax-only','-DFLOOD_ADVERT_RTC_FAST_SLOTS=50',
                '-I',str(self.work),'-isystem',str(ROOT/'src'),'-isystem',str(ROOT/'test/mocks')]
        for flags in ([],['-DESP32_PLATFORM=1'],
                      ['-DESP32_PLATFORM=1','-DMESHCORE_EXPANDED_PARTITION_PROFILE=1','-DFLOOD_ADVERT_RTC_SLOW_SLOTS=80'],
                      ['-DESP32_PLATFORM=1','-DMESHCORE_EXPANDED_PARTITION_PROFILE=1','-DFLOOD_ADVERT_RTC_SLOW_SLOTS=-1']):
            with self.subTest(flags=flags):
                r=subprocess.run(self.compiler+common+flags+[str(source)],capture_output=True,text=True,timeout=15)
                self.assertNotEqual(r.returncode,0)
        isolated=self.work/'unsupported';isolated.mkdir(exist_ok=True)
        for config in ('#define CONFIG_IDF_TARGET_ESP32 1\n',
                       '#define CONFIG_IDF_TARGET_ESP32S3 1\n#define CONFIG_ESP_SYSTEM_ALLOW_RTC_FAST_MEM_AS_HEAP 0\n'):
            (isolated/'sdkconfig.h').write_text(config)
            r=subprocess.run(self.compiler+['-I',str(isolated)]+common+
                ['-DESP32_PLATFORM=1','-DMESHCORE_EXPANDED_PARTITION_PROFILE=1',str(source)],capture_output=True,text=True,timeout=15)
            self.assertNotEqual(r.returncode,0)

    def test_only_exact_full_recipes_select_physical_banks(self):
        text=(ROOT/'build_legacy.sh').read_text()
        start=text.index('  case "${env_name,,}" in',text.index('  # Preserve all 128 advert-source records'))
        end=text.index('\n  esac',start)+len('\n  esac')
        case=text[start:end]
        for target,fast,slow in (
            ('Heltec_Wireless_Tracker_repeater',50,50),
            ('Heltec_Wireless_Tracker_repeater_observer_mqtt',50,50),
            ('heltec_tracker_v2_repeater',50,50),
            ('Heltec_T190_repeater_',16,32),
            ('Heltec_T190_repeater_observer_mqtt',16,32),
            ('Heltec_v3_repeater',16,32),
            ('Heltec_v3_repeater_observer_mqtt',16,32),
            ('Heltec_v3_repeater_bridge_rs232',16,32),
            ('Heltec_v3_repeater_bridge_espnow',16,32),
            ('Heltec_v3_room_server',0,0),
            ('Heltec_v3_companion_radio_full',0,0),
            ('Heltec_T190_room_server_',0,0),
            ('RAK_4631_repeater',0,0),
            ('heltec_tracker_v2_companion_radio_full_femon',0,0)):
            with self.subTest(target=target):
                r=subprocess.run(['bash','-c','env_name="$1"\nPLATFORMIO_BUILD_FLAGS=""\n'+case+'\nprintf "%s" "$PLATFORMIO_BUILD_FLAGS"','bash',target],capture_output=True,text=True,check=True,timeout=10)
                flags=dict(re.findall(r'-D([A-Z0-9_]+)=(\d+)',r.stdout))
                self.assertEqual(int(flags.get('FLOOD_ADVERT_RTC_FAST_SLOTS',0)),fast)
                self.assertEqual(int(flags.get('FLOOD_ADVERT_RTC_SLOW_SLOTS',0)),slow)


if __name__=='__main__': unittest.main()
