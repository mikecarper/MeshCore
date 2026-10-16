"""Execute RAK15002 SD/NOR/Ethernet lifetime guards without hardware or PIO."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_rak_companion_ethernet_worker import build_worker

ROOT = Path(__file__).resolve().parents[1]
FLAGS = ["NRF52_PLATFORM", "RAK_4631", "OTA_SD_STORE", "OTA_RAK15002_SD_STORE",
         "SPI_INTERFACES_COUNT=1", "PIN_SPI_MISO=29", "PIN_SPI_SCK=3", "PIN_SPI_MOSI=30",
         "WB_IO6=10", "RAK4631_COMBINED_ETHERNET"]
SPI = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <vector>
constexpr int INPUT=0,OUTPUT=1,INPUT_PULLUP=2,LOW=0,HIGH=1;
struct Peripheral { unsigned id; };
inline Peripheral periph1{1},periph2{2},periph3{3};
#define NRF_SPIM1 (&periph1)
#define NRF_SPIM2 (&periph2)
#define NRF_SPIM3 (&periph3)
inline bool inserted=false;
inline unsigned begin_count[4]={},end_count[4]={};
inline std::vector<int> touched;
inline void pinMode(int pin,int){touched.push_back(pin);}
inline void digitalWrite(int pin,int){touched.push_back(pin);}
inline int digitalRead(int pin){assert(pin==10);return inserted?LOW:HIGH;}
struct SPIClass {
  Peripheral* peripheral; int miso,sck,mosi;
  SPIClass(Peripheral* p,int i,int c,int o):peripheral(p),miso(i),sck(c),mosi(o){}
  void begin(){++begin_count[peripheral->id];}
  void end(){++end_count[peripheral->id];}
};
inline SPIClass SPI(NRF_SPIM3,45,43,44),SPI1(NRF_SPIM2,9,10,11);
'''
PRELUDE = r'''
#include <helpers/ota/OtaSdSpiBusNrf52.h>
#include <cassert>
#include <cstring>
#include <new>
#include <initializer_list>
using namespace mesh::ota;
'''


def run_native(test, source, files=None, defines=FLAGS, negative=False):
    compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        test.skipTest("host C++17 compiler required")
    with tempfile.TemporaryDirectory(prefix="rak-shared-spi-") as folder:
        work = Path(folder)
        sources = {"main.cpp": source, "SPI.h": SPI, "Arduino.h": '#pragma once\n#include "SPI.h"\n'}
        sources.update(files or {})
        for name, content in sources.items():
            (work / name).write_text(content)
        sanitizer = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                     if sys.platform.startswith("linux") else [])
        command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                   *sanitizer, *["-D" + item for item in defines], "-I" + str(work),
                   "-I" + str(ROOT / "src"), *[str(work / name) for name in sources if name.endswith(".cpp")],
                   "-o", str(work / "case")]
        built = subprocess.run(command, text=True, capture_output=True, timeout=60)
        test.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        ran = subprocess.run([str(work / "case")], text=True, capture_output=True, timeout=15)
        if negative:
            test.assertNotEqual(ran.returncode, 0)
        else:
            test.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


class Rak15002SharedSpiTests(unittest.TestCase):
    def test_shared_nor_ethernet_compile_gate_accepts_only_admitted_runtime_profiles(self):
        header = (ROOT / "src/helpers/ota/OtaStoreQspiNrf52.h").read_text()
        guards = header.split("#if defined(OTA_QSPI_SHARED_WISBLOCK_SPI) && defined(RAK_3401)", 1)[0]
        base = {"NRF52_PLATFORM", "RAK_4631", "OTA_RAK_AUTO_STORE",
                "OTA_QSPI_SHARED_WISBLOCK_SPI", "ETHERNET_ENABLED"}
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++17 compiler required")
        for extra, missing, admitted in (
                ({"RAK4631_COMBINED_ETHERNET"}, set(), True),
                ({"OTA_RAK15002_SD_STORE", "OTA_SD_STORE"}, set(), True),
                (set(), set(), False),
                ({"OTA_RAK15002_SD_STORE"}, {"RAK_4631"}, False),
                ({"OTA_RAK15002_SD_STORE"}, {"OTA_RAK_AUTO_STORE"}, False),
                ({"OTA_RAK15002_SD_STORE"}, {"NRF52_PLATFORM"}, False)):
            with self.subTest(extra=extra, missing=missing):
                built = subprocess.run([compiler, "-x", "c++", "-fsyntax-only",
                    *["-D" + value for value in sorted((base | extra) - missing)], "-"],
                    input=guards + "\nint main() {}\n", capture_output=True, text=True, timeout=15)
                self.assertEqual(built.returncode == 0, admitted, built.stderr)

    def test_owner_shared_across_translation_units_no_legacy_setter_steal(self):
        app = PRELUDE + r'''
void* secondOwner();
int main(){
  assert(secondOwner()==&rak4631_shared_spi_owner());
  assert(!rak4631_try_acquire_spi(RakSharedSpiOwner::None));
  for(auto device:{RakSharedSpiOwner::Sd,RakSharedSpiOwner::Nor}){
    assert(rak4631_try_acquire_spi(device));
    assert(rak4631_try_acquire_spi(device));
    rak4631_set_ethernet_spi_owner(true);assert(!rak4631_ethernet_owns_spi());
    rak4631_set_ethernet_spi_owner(false);assert(rak4631_spi_owner()==device);
    assert(!rak4631_try_acquire_spi(RakSharedSpiOwner::Ethernet));
    rak4631_release_spi(RakSharedSpiOwner::Ethernet);assert(rak4631_spi_owner()==device);
    rak4631_release_spi(device);assert(rak4631_spi_owner()==RakSharedSpiOwner::None);
  }
  rak4631_set_ethernet_spi_owner(true);assert(rak4631_ethernet_owns_spi());
  assert(!rak4631_try_acquire_spi(RakSharedSpiOwner::Sd));
  rak4631_set_ethernet_spi_owner(false);assert(!rak4631_ethernet_owns_spi());
}
'''
        other = PRELUDE + "void* secondOwner(){return &rak4631_shared_spi_owner();}"
        run_native(self, app, {"second.cpp": other})
        header = (ROOT / "src/helpers/nrf52/Rak4631SharedSpi.h").read_text()
        broken = header.replace("inline std::atomic<RakSharedSpiOwner>& rak4631_shared_spi_owner()",
                                "static std::atomic<RakSharedSpiOwner>& rak4631_shared_spi_owner()")
        self.assertNotEqual(header, broken)
        local_app = app.replace("<helpers/ota/OtaSdSpiBusNrf52.h>", '"Owner.h"')
        local_other = other.replace("<helpers/ota/OtaSdSpiBusNrf52.h>", '"Owner.h"')
        run_native(self, local_app, {"second.cpp": local_other, "Owner.h": broken}, negative=True)

    def test_sd_bus_and_active_low_card_detect_never_reconfigure_radio(self):
        run_native(self, PRELUDE + r'''
int main(){
  assert(&ota_sd_spi_bus()!=&SPI&&&ota_sd_spi_bus()!=&SPI1);
  assert(ota_sd_spi_bus().peripheral==NRF_SPIM2);
  assert(ota_sd_spi_bus().miso==29&&ota_sd_spi_bus().sck==3&&ota_sd_spi_bus().mosi==30);
  assert(SPI.peripheral==NRF_SPIM3&&SPI.miso==45&&SPI.sck==43&&SPI.mosi==44);
  assert(!ota_rak15002_sd_card_present());inserted=true;assert(ota_rak15002_sd_card_present());
  for(auto pin:touched)assert(pin==10);
  assert(!begin_count[1]&&!begin_count[2]&&!begin_count[3]);
}
''')

    def test_rak_store_uses_fat32_aliases_without_changing_legacy_sd_types(self):
        # The new optional RAK15002 STORE profile intentionally excludes
        # exFAT. Existing MeshTower and CACHE-only profiles retain the wider
        # SdFs/FsFile type pair, so both selections are compiled explicitly.
        source = (ROOT / "src/helpers/ota/OtaStoreSdNrf52.cpp").read_text()
        bound_start = source.index("static_assert(sizeof(")
        bound = source[bound_start:source.index(");", bound_start) + 2]
        run_native(self, r'''
#include <helpers/ota/OtaSdTypesNrf52.h>
class SdFat32 {};
class FatFile {};
#include <type_traits>
static_assert(std::is_same<mesh::ota::OtaSdFs, SdFat32>::value, "RAK store must be FAT16/FAT32 only");
static_assert(std::is_same<mesh::ota::OtaSdFile, FatFile>::value, "RAK store must use FatFile");
@BOUND@
int main() {}
'''.replace("@BOUND@", bound), {"SdFat.h": '#error "SD public headers must not leak SdFat File into LittleFS callers"\n'},
                   defines=["NRF52_PLATFORM", "OTA_SD_STORE", "OTA_RAK15002_SD_STORE"])
        run_native(self, r'''
#include <helpers/ota/OtaSdTypesNrf52.h>
class SdFs {};
class FsFile {};
#include <type_traits>
static_assert(std::is_same<mesh::ota::OtaSdFs, SdFs>::value, "legacy SD must retain SdFs");
static_assert(std::is_same<mesh::ota::OtaSdFile, FsFile>::value, "legacy SD must retain FsFile");
int main() {}
''', defines=["NRF52_PLATFORM", "OTA_SD_STORE"])

    def test_user_gpio_reserves_exact_sd_bus_and_detect_only_for_new_profile(self):
        run_native(self, r'''
#include <helpers/UserGpioPinPolicy.h>
#include <cassert>
int main(){
 for(uint8_t pin=0;pin<48;++pin){
  const bool claimed=pin==3||pin==10||pin==26||pin==29||pin==30;
  assert(UserGpioPinPolicy::isFirmwareReserved(pin)==claimed);
 }
}
''')
        run_native(self, r'''
#include <helpers/UserGpioPinPolicy.h>
#include <cassert>
int main(){for(uint8_t pin=0;pin<48;++pin)assert(!UserGpioPinPolicy::isFirmwareReserved(pin));}
''', defines=[])

    def test_real_sd_mount_retains_owner_failed_acquire_has_no_spi_and_pins_release(self):
        source = (ROOT / "src/helpers/ota/OtaStoreSdNrf52.cpp").read_text()
        signatures = ("OtaStoreSdNrf52::OtaStoreSdNrf52()", "OtaStoreSdNrf52::~OtaStoreSdNrf52()",
                      "bool OtaStoreSdNrf52::acquireBus()", "bool OtaStoreSdNrf52::busAccessAllowed() const",
                      "void OtaStoreSdNrf52::releaseBusClaim()", "bool OtaStoreSdNrf52::releaseBus()",
                      "void OtaStoreSdNrf52::fail(", "void OtaStoreSdNrf52::resetStoreState()",
                      "bool OtaStoreSdNrf52::beginCardOnly()", "bool OtaStoreSdNrf52::mount()",
                      "bool OtaStoreSdNrf52::write(", "bool OtaStoreSdNrf52::read(",
                      "uint32_t OtaStoreSdNrf52::capacity() const", "void OtaStoreSdNrf52::checkpoint()")
        receiver = source[source.index("bool OtaStoreSdNrf52::locate_file()") :]
        install = ("bool OtaStoreSdNrf52::write(", "bool OtaStoreSdNrf52::read(",
                   "void OtaStoreSdNrf52::checkpoint()")
        methods = "\n".join(extract_braced(receiver if name in install else source, name) for name in signatures)
        mock = r'''
inline unsigned fs_allocations=0,file_allocations=0,io=0;
inline bool mount_ok=true;
inline void allowed(){assert(rak4631_spi_owner()==RakSharedSpiOwner::Sd);++io;}
struct SdSpiConfig{SdSpiConfig(int,int,int,SPIClass*){}};
constexpr int DEDICATED_SPI=0;
#define SD_SCK_MHZ(x) (x)
#define OTA_SD_SCK_MHZ 4
constexpr uint32_t MOTA_SD_AUTH_SECTOR_SIZE=512;
struct Card{uint32_t sectorCount(){allowed();return 2048;}bool syncDevice(){allowed();return true;}};
class SdFs{public: SdFs(){++fs_allocations;} bool begin(SdSpiConfig){allowed();ota_sd_spi_bus().begin();return mount_ok;}
 bool cardBegin(SdSpiConfig c){return begin(c);}void end(){allowed();}Card* card(){allowed();static Card c;return &c;}};
class FsFile{public:bool opened=false;FsFile(){++file_allocations;}explicit operator bool()const{return opened;}
 void close(){allowed();opened=false;}bool seekSet(uint32_t){allowed();return true;}
 size_t write(const uint8_t*,uint32_t n){allowed();return n;}int read(uint8_t*,uint32_t n){allowed();return n;}
 bool sync(){allowed();return true;}};
using SdFat32=SdFs;using FatFile=FsFile;
namespace mesh{namespace ota{using OtaSdFs=::SdFat32;using OtaSdFile=::FatFile;}}
static void clear_sd_auth_record(){}
'''
        header = (ROOT / "src/helpers/ota/OtaStoreSdNrf52.h").read_text()
        cls = extract_braced(header, "class OtaStoreSdNrf52 : public OtaStore").replace("private:", "public:") + ";"
        stubs = r'''
bool OtaStoreSdNrf52::begin(uint32_t){return false;}
bool OtaStoreSdNrf52::discard(){return false;}void OtaStoreSdNrf52::clear(){}
bool OtaStoreSdNrf52::set_meta_size(uint32_t){return false;}
bool OtaStoreSdNrf52::finalize(){return false;}bool OtaStoreSdNrf52::reopen(){return false;}
bool OtaStoreSdNrf52::plan_layout(bool,uint32_t,uint32_t,uint32_t,bool){return false;}
'''
        runner = r'''
int main(){
 OtaStoreSdNrf52 store;assert(!fs_allocations&&!file_allocations);inserted=true;
 for(auto peer:{RakSharedSpiOwner::Ethernet,RakSharedSpiOwner::Nor}){
  assert(rak4631_try_acquire_spi(peer));touched.clear();const unsigned before=io;
  assert(!store.probeMedia());assert(io==before&&!fs_allocations&&!begin_count[2]);
  for(auto pin:touched)assert(pin==10);
  rak4631_release_spi(peer);
 }
 inserted=false;assert(!store.probeMedia()&&!fs_allocations);
 inserted=true;mount_ok=false;assert(!store.probeMedia());
 assert(rak4631_spi_owner()==RakSharedSpiOwner::None&&begin_count[2]==1&&end_count[2]==1);
 mount_ok=true;assert(store.probeMedia());assert(rak4631_spi_owner()==RakSharedSpiOwner::Sd);
 assert(fs_allocations==1&&file_allocations==1&&!begin_count[3]&&!end_count[3]);
 store._file->opened=true;store._total=20;uint8_t bytes[20]={};
 assert(store.read(0,bytes,20)&&store.write(0,bytes,20));
 assert(!store.releaseBus()&&rak4631_spi_owner()==RakSharedSpiOwner::Sd);
 const unsigned before=io;inserted=false;assert(!store.read(0,bytes,20)&&!store.write(0,bytes,20));
 store.checkpoint();assert(!store.capacity()&&io==before);
 store._total=0;assert(store.releaseBus());assert(rak4631_spi_owner()==RakSharedSpiOwner::None);
 assert(!store._mounted&&!store._file->opened&&end_count[2]==2);
 assert(rak4631_try_acquire_spi(RakSharedSpiOwner::Ethernet));
 const unsigned ended=end_count[2];assert(store.releaseBus()&&end_count[2]==ended);
 assert(rak4631_ethernet_owns_spi());rak4631_set_ethernet_spi_owner(false);
}
'''
        code = PRELUDE + '#include <helpers/ota/OtaStore.h>\n' + mock
        code += "namespace mesh{class MainBoard;namespace ota{" + cls + methods + stubs + "}}\n" + runner
        run_native(self, code)

    def test_actual_nor_discovery_skips_card_eth_and_sd_owners_without_touching_pads(self):
        source = (ROOT / "src/helpers/ota/OtaStoreQspiNrf52.cpp").read_text()
        methods = "\n".join(extract_braced(source, name) for name in (
            "uint8_t OtaStoreQspiNrf52::autoDetect()",
            "bool OtaStoreQspiNrf52::headerW25Detected()",
            "void OtaStoreQspiNrf52::autoProbeIds("))
        harness = PRELUDE + r'''
namespace mesh { namespace ota {
static uint8_t auto_cs_pin=0xff,auto_detection=0xff;
static uint32_t auto_jedec_id=0,auto_rak_probe_id=0,auto_w25_probe_id=0;
static bool auto_header_w25=false;
constexpr uint8_t HEADER_SCK_PIN=16,HEADER_IO0_PIN=17,HEADER_IO1_PIN=15;
static unsigned gpio=0,probes=0;
uint8_t arduino_to_physical(uint32_t pin){return pin;}
void nrf_gpio_pin_set(uint32_t){++gpio;}
void nrf_gpio_cfg_output(uint32_t){++gpio;}
void nrf_gpio_cfg_default(uint32_t){++gpio;}
uint32_t probe_nor(uint8_t cs){
 assert(!inserted&&rak4631_spi_owner()==RakSharedSpiOwner::Nor);++probes;
 return cs==31?0xef4015:0;
}
class OtaStoreQspiNrf52 {public:
 static uint8_t autoDetect();static bool headerW25Detected();
 static void autoProbeIds(uint32_t&,uint32_t&);
};
@METHODS@
} }
int main(){
 inserted=true;assert(!OtaStoreQspiNrf52::autoDetect()&&!gpio&&!probes);
 inserted=false;
 for(auto other:{RakSharedSpiOwner::Ethernet,RakSharedSpiOwner::Sd}){
  assert(rak4631_try_acquire_spi(other));
  assert(!OtaStoreQspiNrf52::autoDetect()&&!gpio&&!probes&&auto_detection==0xff);
  rak4631_release_spi(other);
 }
 assert(OtaStoreQspiNrf52::autoDetect()==2&&probes==2&&auto_cs_pin==31&&auto_jedec_id==0xef4015);
 assert(rak4631_spi_owner()==RakSharedSpiOwner::None);
 const auto done=gpio;inserted=true;
 assert(!OtaStoreQspiNrf52::autoDetect()&&gpio==done&&probes==2);
 uint32_t rak=0,w25=0;OtaStoreQspiNrf52::autoProbeIds(rak,w25);
 assert(rak==0&&w25==0xef4015&&gpio==done&&probes==2);
}
'''
        run_native(self, harness.replace("@METHODS@", methods),
                   defines=FLAGS + ["OTA_RAK_AUTO_STORE"])

    def test_real_archive_handles_recheck_owner_even_when_initialized(self):
        source = (ROOT / "src/helpers/ota/OtaCacheSdNrf52.cpp").read_text()
        methods = "\n".join(extract_braced(source, name) for name in (
            "bool OtaCacheSdNrf52::ready()", "bool OtaCacheSdNrf52::initialize()",
            "bool OtaCacheSdNrf52::write(uint32_t", "bool OtaCacheSdNrf52::read(uint32_t",
            "uint32_t OtaCacheSdNrf52::capacity() const", "void OtaCacheSdNrf52::checkpoint()"))
        harness = PRELUDE + r'''
namespace mesh { namespace ota {
static unsigned calls=0;
static void access(){assert(inserted&&rak4631_spi_owner()==RakSharedSpiOwner::Sd);++calls;}
struct FsFile{explicit operator bool()const{return true;}
 bool seekSet(uint32_t){access();return true;}size_t write(const uint8_t*,uint32_t n){access();return n;}
 int read(uint8_t*,uint32_t n){access();return n;}bool sync(){access();return true;}};
struct Card{uint32_t sectorCount(){access();return 2048;}bool syncDevice(){access();return true;}};
struct SdFs{Card* card(){access();static Card c;return &c;}bool exists(const char*){access();return false;}};
struct OtaStoreSdNrf52{bool _mounted=true;SdFs fs;SdFs* _sd=&fs;
 bool busAccessAllowed()const{return inserted&&rak4631_spi_owner()==RakSharedSpiOwner::Sd;}
 bool mount(){return busAccessAllowed();}const char* last_error()const{return "busy";}};
class OtaCacheSdNrf52{public:
 OtaStoreSdNrf52* _owner;FsFile* _file;uint32_t _total=20;bool _initialized=true,_enabled=true;bool failed=false;char _error[80]={};
 static constexpr const char* DISABLED_PATH="/mota/cache.off";
 bool ready();bool initialize();bool write(uint32_t,const uint8_t*,uint32_t);
 bool read(uint32_t,uint8_t*,uint32_t)const;uint32_t capacity()const;void checkpoint();
 void fail(const char*){failed=true;}bool ensureDir(){access();return true;}bool rescan(){access();return true;}
};
@METHODS@
} }
int main(){
 OtaStoreSdNrf52 store;FsFile file;OtaCacheSdNrf52 cache;cache._owner=&store;cache._file=&file;
 inserted=true;uint8_t bytes[20]={};
 assert(rak4631_try_acquire_spi(RakSharedSpiOwner::Sd));
 assert(cache.initialize()&&cache.write(0,bytes,20)&&cache.read(0,bytes,20)&&cache.capacity());
 cache.checkpoint();rak4631_release_spi(RakSharedSpiOwner::Sd);
 const auto before=calls;
 for(auto peer:{RakSharedSpiOwner::Ethernet,RakSharedSpiOwner::Nor}){
  assert(rak4631_try_acquire_spi(peer));
  assert(!cache.initialize()&&!cache.write(0,bytes,20)&&!cache.read(0,bytes,20)&&!cache.capacity());
  cache.checkpoint();assert(calls==before);rak4631_release_spi(peer);
 }
 assert(rak4631_try_acquire_spi(RakSharedSpiOwner::Sd));inserted=false;
 assert(!cache.initialize()&&!cache.read(0,bytes,20)&&!cache.capacity());cache.checkpoint();assert(calls==before);
}
'''
        for seeder in (False, True):
            with self.subTest(source_only=seeder):
                run_native(self, harness.replace("@METHODS@", methods),
                           defines=FLAGS + (["OTA_SEEDER_ONLY"] if seeder else []))

    def test_source_only_volume_cannot_stage_write_resume_or_approve_installation(self):
        source = (ROOT / "src/helpers/ota/OtaStoreSdNrf52.cpp").read_text()
        # Preserve the actual preprocessor seam, not extracted method bodies:
        # capacity must stay available to BOTH vtables, not hidden in #else.
        gate = source.index("#if defined(OTA_SEEDER_ONLY)\n// The source-only context")
        start = source.rfind("uint32_t OtaStoreSdNrf52::capacity() const", 0, gate)
        self.assertGreaterEqual(start, 0, "volume capacity must be outside the receiver-only branch")
        end = source.index("#endif // OTA_SEEDER_ONLY", gate) + len("#endif // OTA_SEEDER_ONLY")
        methods = source[start:end]
        header = (ROOT / "src/helpers/ota/OtaStoreSdNrf52.h").read_text()
        cls = extract_braced(header, "class OtaStoreSdNrf52 : public OtaStore").replace("private:", "public:") + ";"
        code = r'''
#include <helpers/ota/OtaStore.h>
#include <cassert>
#include <cstring>
static unsigned card_queries=0;
struct Card{uint32_t sectorCount(){++card_queries;return 2048;}};
class SdFs{public:Card card_value;Card* card(){return &card_value;}};
class FsFile;
using SdFat32=SdFs;using FatFile=FsFile;
namespace mesh{namespace ota{using OtaSdFs=::SdFat32;using OtaSdFile=::FatFile;}}
namespace mesh {class MainBoard;namespace ota {
static bool ready=false;
constexpr uint32_t MOTA_SD_AUTH_SECTOR_SIZE=512;
@CLASS@
OtaStoreSdNrf52::OtaStoreSdNrf52()=default;
OtaStoreSdNrf52::~OtaStoreSdNrf52()=default;
bool OtaStoreSdNrf52::busAccessAllowed()const{return ready;}
void OtaStoreSdNrf52::fail(const char* message){strncpy(_error,message,sizeof(_error)-1);}
@METHODS@
} }
int main(){
 mesh::ota::OtaStoreSdNrf52 volume;uint8_t bytes[32]={};
 assert(!volume.capacity());
 assert(!volume.plan_layout(true,100,0,100,false));assert(!volume.begin(100));
 assert(!volume.set_meta_size(8)&&!volume.write(0,bytes,32)&&!volume.read(0,bytes,32));
 assert(!volume.finalize()&&!volume.reopen()&&!volume.discard());
 volume.clear();volume.checkpoint();assert(!volume.staged_size());
 assert(!volume.approve_for_bootloader(bytes,bytes));
 assert(strstr(volume.last_error(),"source-only"));
 assert(!card_queries);SdFs fs;volume._sd=&fs;volume._mounted=true;
 mesh::ota::ready=true;assert(volume.capacity()==1048576&&card_queries==1);
}
'''
        # No SdFat, GPIO, retained-memory or flash functions are declared: an
        # accidental filesystem/hardware side effect is a compile failure.
        run_native(self, code.replace("@CLASS@", cls).replace("@METHODS@", methods),
                   defines=FLAGS + ["OTA_SEEDER_ONLY"])

    def test_actual_companion_eth_is_opt_in_and_busy_handoff_never_touches_spi(self):
        with tempfile.TemporaryDirectory(prefix="rak-companion-shared-spi-") as folder:
            binary = build_worker(self, folder)
            for scenario in (0, 11, 12, 13, 15):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(binary), str(scenario)], text=True,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_companion_eth_cs_reacquisition_negative_controls(self):
        for mutation in ("missing_cs_direction", "missing_cs_preload"):
            with self.subTest(mutation=mutation):
                self._run_companion_eth_probe(mutation)

    def _run_companion_eth_probe(self, mutation=None):
        cpp = (ROOT / "src/helpers/ethernet/RAK13800/RAK13800EthernetInterface.cpp").read_text()
        if mutation:
            statement = ("    digitalWrite(PIN_ETHERNET_SS, HIGH);\n"
                         "    pinMode(PIN_ETHERNET_SS, OUTPUT);")
            removed = ("    pinMode(PIN_ETHERNET_SS, OUTPUT);" if mutation == "missing_cs_direction"
                       else "    digitalWrite(PIN_ETHERNET_SS, HIGH);")
            startup = extract_braced(cpp, "void RAK13800EthernetInterface::runWorker()")
            self.assertEqual(startup.count(statement), 1)
            cpp = cpp.replace(startup, startup.replace(statement, statement.replace(removed, "")))
        with tempfile.TemporaryDirectory(prefix="rak-companion-cs-negative-") as folder:
            binary = build_worker(self, folder, cpp)
            result = subprocess.run([str(binary), "13"], text=True,
                                    capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("CS must be preloaded" if mutation == "missing_cs_preload"
                          else "pin_modes[26].load() == OUTPUT", result.stderr)


if __name__ == "__main__":
    unittest.main()
