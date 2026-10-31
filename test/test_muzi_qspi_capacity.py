"""Run reviewed CustomLFS JEDEC detection/mounting with the scoped Muzi policy."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nrf52_qspi_dma_fix", ROOT / "scripts/nrf52_qspi_dma_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)
FIXTURE = ROOT / "test/fixtures/customlfs_qspi_dma"
RAW = (FIXTURE / "CustomLFS_QSPIFlash.cpp").read_text(encoding="ascii")
HEADER = (FIXTURE / "CustomLFS_QSPIFlash.h").read_text(encoding="ascii")

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <vector>
#define HEX 16
#define NRFX_SUCCESS 0
#define NRF_QSPI_CINSTR_LEN_4B 4
#define NRF_QSPI_READOC_FASTREAD 1
#define NRF_QSPI_WRITEOC_PP 2
#define NRF_QSPI_ADDRMODE_24BIT 3
#define NRF_QSPI_FREQ_32MDIV2 4
#define NRF_QSPI_MODE_0 0
#define QSPI_IRQn 5
using nrfx_err_t=int;
struct SerialStub {
 template<class T> void print(T){} template<class T> void print(T,int){}
 template<class T> void println(T){} template<class T> void println(T,int){}
 void println(){}
} Serial;
struct nrf_qspi_cinstr_conf_t {int opcode,length;bool io2_level,io3_level,wipwait,wren;};
static uint8_t observed_id[3];
static bool read_ok=true,ready_ok=true,format_ok=true,post_format_mount_ok=true;
static unsigned reads=0,mounts=0,formats=0,data_reads=0,yields=0;
static uint32_t now_ms=0,read_tick=1,fail_address=0xffffffffU,stored_blocks=0;
static std::vector<uint8_t> media;
static const uint8_t original_file[]={0x73,0x61,0x66,0x65,0x00,0x76,0x31};
uint32_t millis(){return now_ms;}
void delay(unsigned ms){now_ms+=ms;++yields;}
nrfx_err_t nrfx_qspi_cinstr_xfer(const nrf_qspi_cinstr_conf_t* cfg,void*,uint8_t* result){
 assert(cfg->opcode==0x9f&&cfg->length==4&&cfg->io2_level&&cfg->io3_level);
 assert(!cfg->wipwait&&!cfg->wren);++reads;
 std::memcpy(result,observed_id,3);return read_ok?0:-1;
}
static unsigned g_ADigitalPinMap[48];
struct QspiConfig {
 struct {unsigned sck_pin,csn_pin,io0_pin,io1_pin,io2_pin,io3_pin;} pins;
 struct {int readoc,writeoc,addrmode;bool dpmconfig;} prot_if;
 struct {int sck_freq,spi_mode;bool dpmen;} phy_if;
};
int nrfx_qspi_init(QspiConfig* cfg,void (*)(int,void*),void*){
 assert(cfg->pins.sck_pin==3&&cfg->pins.csn_pin==26);
 assert(cfg->pins.io0_pin==30&&cfg->pins.io1_pin==29&&cfg->pins.io2_pin==28&&cfg->pins.io3_pin==2);
 assert(cfg->prot_if.addrmode==NRF_QSPI_ADDRMODE_24BIT);return 0;
}
void NVIC_SetPriority(int irq,int priority){assert(irq==QSPI_IRQn&&priority==2);}
struct {int TASKS_ACTIVATE;} hardware;
#define NRF_QSPI (&hardware)
struct Config {
 void* context;
 void (*read)();void (*prog)();void (*erase)();void (*sync)();
 uint32_t read_size,prog_size,block_size,block_count,lookahead;
 void *read_buffer,*prog_buffer,*lookahead_buffer,*file_buffer;
};
struct Adafruit_LittleFS {
 Config* active=nullptr;
 bool begin(Config* config){
  active=config;++mounts;
  return stored_blocks==config->block_count&&(formats==0||post_format_mount_ok);
 }
 bool format(){
  ++formats;
  if(!format_ok)return false;
  assert(active&&active->block_count==media.size()/4096);
  stored_blocks=active->block_count;media[0]=0;return true;
 }
};
@DESCRIPTOR@
@TABLE@
struct CustomLFS_QSPIFlash : Adafruit_LittleFS {
 QspiConfig _qspi_config{};
 const QSPIFlashChip* _chip=nullptr;
 bool _qspi_initialized=false,_operation_pending=false,_operation_complete=false;
 int _current_operation=0,_operation_result=0;
 static constexpr int QSPI_OP_NONE=0;
 bool _quad_mode_enabled=false,_quad_read_enabled=false,_quad_write_enabled=false;
 uint32_t _total_size=1048576,_sector_size=4096,_page_size=256,_clock_frequency=0;
 Config _lfs_config{};
 uint32_t _flash_addr=0,_flash_total_size=0,_block_size=0;
 static void qspi_event_handler(int,void*){}
 static void _qspi_read(){}static void _qspi_prog(){}static void _qspi_erase(){}static void _qspi_sync(){}
 bool waitForQSPIReady(){return ready_ok;}
 bool qspiRead(uint32_t address,void* destination,uint32_t count){
  assert(_qspi_initialized&&destination&&count==1024&&address%1024==0);
  assert((reinterpret_cast<uintptr_t>(destination)&3)==0);
  assert(address+count<=media.size());++data_reads;now_ms+=read_tick;
  if(address==fail_address)return false;
  std::memcpy(destination,media.data()+address,count);return true;
 }
 bool detectChip();void _configure_lfs();
 bool begin(uint8_t sck,uint8_t cs,uint8_t io0,uint8_t io1,uint8_t io2,uint8_t io3);
};
@METHODS@
static void reset(uint8_t m,uint8_t t,uint8_t c){
 observed_id[0]=m;observed_id[1]=t;observed_id[2]=c;
 reads=mounts=formats=data_reads=yields=0;
 read_ok=ready_ok=format_ok=post_format_mount_ok=true;
 now_ms=0;read_tick=1;fail_address=0xffffffffU;stored_blocks=0;
 media.clear();
}
int main(){
 for(unsigned i=0;i<48;++i)g_ADigitalPinMap[i]=i;
 const uint8_t ids[][3]={{0xef,0x40,0x15},{0xef,0x40,0x17},{0xef,0x40,0x18},{0xc8,0x40,0x17}};
 const uint32_t sizes[]={2097152,8388608,16777216,8388608};
 for(unsigned i=0;i<4;++i){
  reset(ids[i][0],ids[i][1],ids[i][2]);media.assign(sizes[i],0xff);
  stored_blocks=sizes[i]/4096;CustomLFS_QSPIFlash fs;
  assert(fs.begin(3,26,30,29,28,2)&&reads==1&&mounts==1&&formats==0);
  assert(fs._chip&&fs._total_size==sizes[i]&&fs._flash_total_size==sizes[i]);
  assert(fs._lfs_config.block_count==sizes[i]/4096&&fs._lfs_config.block_size==4096);
  assert(fs._chip->jedec_id[2]==ids[i][2]&&fs._chip->address_bits==24);
  assert(fs._chip->quad_enable_register==2&&fs._chip->quad_enable_bit==1);
  for(void* buffer:{fs._lfs_config.read_buffer,fs._lfs_config.prog_buffer,fs._lfs_config.lookahead_buffer})
   assert((reinterpret_cast<uintptr_t>(buffer)&3)==0);
  assert(!fs.begin(3,26,30,29,28,2)&&reads==1&&mounts==1&&formats==0&&data_reads==0);

  // An old 1 MB fallback filesystem mounts as-is, including its stored files.
  reset(ids[i][0],ids[i][1],ids[i][2]);media.assign(sizes[i],0xff);stored_blocks=256;
  std::memcpy(media.data()+1000,original_file,sizeof(original_file));CustomLFS_QSPIFlash old;
  assert(old.begin(3,26,30,29,28,2)&&mounts==2&&formats==0&&data_reads==0);
  assert(old._total_size==sizes[i]&&old._flash_total_size==1048576&&old._lfs_config.block_count==256);
  assert(std::memcmp(media.data()+1000,original_file,sizeof(original_file))==0);

  // A freshly erased part gets all its physical capacity only after a full scan.
  reset(ids[i][0],ids[i][1],ids[i][2]);media.assign(sizes[i],0xff);CustomLFS_QSPIFlash blank;
  assert(blank.begin(3,26,30,29,28,2)&&mounts==3&&formats==1);
  assert(data_reads==sizes[i]/1024&&yields==sizes[i]/4096&&media[0]==0);
  assert(blank._total_size==sizes[i]&&blank._flash_total_size==sizes[i]);
  assert(blank._lfs_config.block_count==sizes[i]/4096);

  // Even the last byte invalidates the empty-media proof; no partial scan erases it.
  reset(ids[i][0],ids[i][1],ids[i][2]);media.assign(sizes[i],0xff);
  media.back()=0x7f;CustomLFS_QSPIFlash preserved;
  assert(!preserved.begin(3,26,30,29,28,2)&&reads==1&&mounts==2&&formats==0);
  assert(data_reads==sizes[i]/1024&&media.back()==0x7f);
  assert(preserved._lfs_config.block_count==sizes[i]/4096&&preserved._flash_total_size==sizes[i]);
 }
 // Same capacity byte with a different vendor/type is not a recognized part.
 const uint8_t rejected[][3]={{0,0,0},{255,255,255},{0x11,0x40,0x17},
  {0xef,0x60,0x17},{0xef,0x40,0x16},{0xef,0x40,0x19},{0xc8,0x40,0x15},{0xc2,0x28,0x15}};
 for(auto& id:rejected){
  reset(id[0],id[1],id[2]);CustomLFS_QSPIFlash fs;
  assert(!fs.begin(3,26,30,29,28,2)&&reads==1&&mounts==0&&formats==0);
  assert(fs._flash_total_size==0);
 }
 reset(0xef,0x40,0x17);read_ok=false;CustomLFS_QSPIFlash io_error;
 assert(!io_error.begin(3,26,30,29,28,2)&&mounts==0&&formats==0);
 reset(0xef,0x40,0x17);ready_ok=false;CustomLFS_QSPIFlash busy;
 assert(!busy.begin(3,26,30,29,28,2)&&reads==0&&mounts==0&&formats==0);

 reset(0xef,0x40,0x15);media.assign(2097152,0xff);fail_address=2097152-1024;
 CustomLFS_QSPIFlash late_error;
 assert(!late_error.begin(3,26,30,29,28,2)&&formats==0&&data_reads==2048&&mounts==2);
 reset(0xef,0x40,0x15);media.assign(2097152,0xff);read_tick=200;
 CustomLFS_QSPIFlash deadline;
 assert(!deadline.begin(3,26,30,29,28,2)&&formats==0&&mounts==2&&data_reads<2048&&now_ms>=60000);
 reset(0xef,0x40,0x15);media.assign(2097152,0xff);format_ok=false;
 CustomLFS_QSPIFlash format_error;
 assert(!format_error.begin(3,26,30,29,28,2)&&formats==1&&mounts==2&&media[0]==0xff);
 reset(0xef,0x40,0x15);media.assign(2097152,0xff);post_format_mount_ok=false;
 CustomLFS_QSPIFlash remount_error;
 assert(!remount_error.begin(3,26,30,29,28,2)&&formats==1&&mounts==3);
}
'''


class MuziQspiCapacityTests(unittest.TestCase):
    def test_exact_reviewed_transform_and_idempotence(self):
        patched = FIX.patched_muzi_source(RAW)
        self.assertEqual(FIX.digest(patched), FIX.MUZI_PATCHED_SHA256)
        self.assertEqual(FIX.patched_muzi_source(patched), patched)
        self.assertEqual(FIX.patched_muzi_source(FIX.patched_source(RAW)), patched)
        self.assertEqual(FIX.patched_muzi_source(RAW.replace("\n", "\r\n")), patched)
        self.assertIn(FIX.MUZI_SAFE_MOUNT_BLOCK, patched)
        self.assertNotIn(FIX.MUZI_MOUNT_BLOCK, patched)
        for changed in (RAW + "// changed dependency\n", RAW.replace("0xEF, 0x40, 0x15", "0xEF, 0x40, 0x17")):
            with self.assertRaises(RuntimeError):
                FIX.patched_muzi_source(changed)

    def test_actual_jedec_table_geometry_mount_and_failure_paths(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("host C++ compiler required")
        patched = FIX.patched_muzi_source(RAW)
        descriptor = HEADER[HEADER.index("struct QSPIFlashChip {"):HEADER.index("class CustomLFS_QSPIFlash")]
        table = patched[patched.index("const QSPIFlashChip qspiFlashChips[]"):patched.index("// Global instance")]
        methods = "\n".join(body(patched, signature) for signature in
            ("bool CustomLFS_QSPIFlash::detectChip()", "void CustomLFS_QSPIFlash::_configure_lfs()",
             "bool CustomLFS_QSPIFlash::begin("))
        code = HARNESS.replace("@DESCRIPTOR@", descriptor).replace("@TABLE@", table).replace("@METHODS@", methods)
        with tempfile.TemporaryDirectory(prefix="muzi-qspi-") as folder:
            work = Path(folder)
            source = work / "check.cpp"
            binary = work / ("check.exe" if os.name == "nt" else "check")
            source.write_text(code)
            sanitize = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                        if sys.platform.startswith("linux") else [])
            result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                *sanitize, str(source), "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_build_middleware_is_scoped_and_leaves_dependency_unchanged(self):
        class Node:
            def __init__(self, path): self.path = path
            def srcnode(self): return self
            def get_abspath(self): return str(self.path)
        class Env(dict):
            def subst(self, value):
                self_test.assertEqual(value, "$BUILD_DIR")
                return str(self.build)
            def File(self, path): return Path(path)
        self_test = self
        with tempfile.TemporaryDirectory(prefix="muzi-qspi-private-") as folder:
            root = Path(folder)
            source = root / "libdeps/CustomLFS/src"
            source.mkdir(parents=True)
            for path in FIXTURE.iterdir():
                shutil.copyfile(path, source / path.name)
            originals = {p.name: p.read_bytes() for p in source.iterdir()}
            env = Env(CPPDEFINES=[("MUZI_BASE", 1), ("MESH_MUZI_QSPI_CAPACITY_GUARD", 1)])
            env.build = root / "muzi"
            node = Node(source / "CustomLFS_QSPIFlash.cpp")
            patched = FIX.replace_qspi_source(env, node)
            self.assertEqual(patched.read_text(), FIX.patched_muzi_source(RAW))
            self.assertEqual({p.name: p.read_bytes() for p in source.iterdir()}, originals)
            env["CPPDEFINES"] = [("MUZI_BASE", 1)]
            env.build = root / "legacy"
            regular = FIX.replace_qspi_source(env, node)
            self.assertEqual(regular.read_text(), FIX.patched_source(RAW))
            self.assertIn(FIX.MUZI_MOUNT_BLOCK, regular.read_text())
            self.assertNotIn(FIX.MUZI_SAFE_MOUNT_BLOCK, regular.read_text())
            for flags in (["MESH_MUZI_QSPI_CAPACITY_GUARD"],
                          [("MUZI_BASE", 1), ("MESH_MUZI_QSPI_CAPACITY_GUARD", 2)]):
                env["CPPDEFINES"] = flags
                with self.assertRaises(RuntimeError):
                    FIX.replace_qspi_source(env, node)


if __name__ == "__main__":
    unittest.main()
