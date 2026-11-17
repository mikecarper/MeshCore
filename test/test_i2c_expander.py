"""Execute the real expander driver against modeled MCP and PCF I2C devices."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_regulator_settings import FILESYSTEM

ROOT = Path(__file__).resolve().parents[1]

ARDUINO = r'''
#pragma once
#include <stdint.h>
inline uint32_t& fakeTime() { static uint32_t value=0;return value; }
inline uint32_t millis() { return fakeTime(); }
'''

WIRE = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <vector>
struct FakeWire {
  unsigned kind=1,address=0x27,clock=400000,timeout=50;
  bool present=true,fail_write=false,fail_read=false,short_write=false,short_read=false;
  bool active=false;
  unsigned reads=0,mutations=0,begins=0,ends=0;
  uint16_t external=0xffff,port=0xffff;
  uint8_t regs[32]={},pointer=0,current=0;
  std::vector<uint8_t> tx,rx;
  std::vector<std::vector<uint8_t>> writes;
  size_t index=0;
  void (*on_mutation)()=nullptr;
  void reset(unsigned model,unsigned addr=0x27) {
    *this=FakeWire();kind=model;address=addr;regs[0]=regs[1]=0xff;
  }
  bool pcf() const { return kind>=3; }
  unsigned width() const { return kind==1||kind==3?16:8; }
  bool begin(int sda,int scl) { assert(sda==4&&scl==3);++begins;return true; }
  uint32_t getClock() { return clock; }
  bool setClock(uint32_t value) { clock=value;return true; }
  uint16_t getTimeOut() { return timeout; }
  void setTimeOut(uint16_t value) { timeout=value; }
  void beginTransmission(uint8_t addr) { assert(!active);active=true;current=addr;tx.clear(); }
  size_t write(const uint8_t* data,size_t length) {
    if(short_write)return 0;tx.insert(tx.end(),data,data+length);return length;
  }
  size_t write(uint8_t byte) { return write(&byte,1); }
  uint8_t endTransmission(bool=true) {
    assert(active);active=false;++ends;
    if(!present||current!=address||fail_write)return 4;
    assert(timeout<=20);assert(clock<=(kind==4||kind==5?100000u:400000u));
    if(tx.empty())return 0;
    if(!pcf()&&tx.size()==1) { pointer=tx[0];return 0; }
    if(on_mutation)on_mutation();++mutations;writes.push_back(tx);
    if(pcf()) {
      assert(tx.size()==width()/8);port=tx[0]|(width()==16?uint16_t(tx[1])<<8:0);
    } else {
      assert(tx.size()>=2);for(size_t i=1;i<tx.size();++i)regs[tx[0]+i-1]=tx[i];
    }
    return 0;
  }
  size_t requestFrom(uint8_t addr,uint8_t length) {
    ++reads;rx.clear();index=0;
    if(!present||addr!=address||fail_read)return 0;
    assert(timeout<=20);assert(clock<=(kind==4||kind==5?100000u:400000u));
    uint16_t gpio=external;
    if(pcf())gpio&=port;
    else if(kind==1) {
      const uint16_t direction=regs[0]|uint16_t(regs[1])<<8;
      const uint16_t latch=regs[0x14]|uint16_t(regs[0x15])<<8;
      gpio=(external&direction)|(latch&~direction);
    }else gpio=(external&regs[0])|(regs[0x0a]&~regs[0]);
    for(unsigned i=0;i<length;++i) {
      const unsigned reg=pointer+i;
      rx.push_back(pcf()?uint8_t(gpio>>(i*8)):
        kind==1&&(reg==0x12||reg==0x13)?uint8_t(gpio>>((reg-0x12)*8)):
        kind==2&&reg==0x09?uint8_t(gpio):regs[reg]);
    }
    if(short_read&&!rx.empty())rx.pop_back();return rx.size();
  }
  int available() { return int(rx.size()-index); }
  int read() { return index<rx.size()?rx[index++]:-1; }
};
extern FakeWire Wire;
'''

HARNESS = r'''
#include <helpers/IdentityStore.h>
#include <Arduino.h>
#include <Wire.h>
#include <cassert>
#include <cstring>
#include <string>
#include <helpers/I2cExpander.cpp>
FakeWire Wire;
namespace mesh { class MainBoard {}; }
using namespace mesh::expander;
using mesh::pins::Owner;
mesh::MainBoard board;
static bool reserve=false;
static bool attempted_during_save=false, claimed_during_save=false;
static MockFS* expected_store=nullptr;
static void claimDuringSave() {
  attempted_during_save=true;
  claimed_during_save=mesh::expander::tryClaim(Owner::Rotary,1);
}
static void requireSavedConfigBeforeMutation() {
  assert(expected_store&&expected_store->exists("/expander_v1"));
  const auto& record=expected_store->files["/expander_v1"];
  assert(record.size()==12&&record[4]==Wire.kind&&record[5]==Wire.address);
}
static bool conflict(uint8_t address) { return reserve&&address==0x27; }
std::string command(const char* value) {
  char response[200]={};assert(handleCommand(value,response,sizeof(response)));return response;
}
void clean() { shutdown();setAddressConflictCheck(nullptr);reserve=false; }
void parsing() {
  for(const char* token: {"x0","x15","0","63","off","-1"}) {
    const char* at=token;int16_t value=-42;
    assert(parsePinToken(at,value,true)&&!*at);
    char text[12];formatPin(value,text,sizeof(text));
    assert(std::string(text)==(std::string(token)=="-1"?"off":token));
  }
  for(const char* token: {"x16","x-1","64","79","-2","x","12x","offx","65536","X0"}) {
    const char* at=token;int16_t value;assert(!parsePinToken(at,value,true));
  }
  const char* list=" x1 , x2,0";int16_t value;
  assert(parsePinToken(list,value)&&value==65&&*list==',');
  assert(mesh::expander::tryClaim(Owner::Rotary,0));mesh::expander::release(Owner::Rotary,0);
}
void defaults() {
  clean();Wire.reset(1);MockFS fs;begin(board,&fs);
  assert(command("get expander")=="> off");assert(!configured()&&!online());
  assert(Wire.begins==0&&Wire.reads==0&&Wire.mutations==0&&fs.writes==0);
  for(const char* value:{"set expander mcp23017,0x28","set expander pcf8574a,0x27",
      "set expander pcf8575,0x127","set expander nope,0x27","set expander mcp23008,xyz"})
    assert(command(value).find("Error:")==0);
  assert(Wire.reads==0&&Wire.mutations==0&&fs.writes==0);
}
void allModels() {
  const char* names[]={"mcp23017","mcp23008","pcf8575","pcf8574","pcf8574a"};
  for(unsigned kind=1;kind<=5;++kind) {
    clean();Wire.reset(kind,kind==5?0x3f:0x27);MockFS fs;begin(board,&fs);
    expected_store=&fs;Wire.on_mutation=requireSavedConfigBeforeMutation;
    char setup[64];snprintf(setup,sizeof(setup),"set expander %s,0x%02x",names[kind-1],Wire.address);
    assert(command(setup).find("OK - ")==0);assert(configured()&&online());
    Wire.on_mutation=nullptr;expected_store=nullptr;
    assert(configIdentity()==((kind<<8)|Wire.address));
    assert(pinCount()==(kind==1||kind==3?16:8));assert(recommendedActiveLow()==(kind>=3));
    assert(ownsAddress(Wire.address)&&!ownsAddress(0x26));
    assert(fs.files["/expander_v1"].size()==12&&Wire.clock==400000&&Wire.timeout==50);
    assert(available(64)&&!available(80));assert(available(72)==(pinCount()==16));
    assert(inputAvailable(64,true));assert(inputAvailable(64,false)==(kind<3));
    if(kind==1) { assert(!available(71)&&!available(79));assert(!mesh::expander::tryClaim(Owner::Outputs,0x80)); }
    else assert(available(71));
    assert(!configurePin(80,true));
    // Input changes must not alter the unclaimed input pins, and a future
    // output's inactive latch is set without a transient active pulse.
    const unsigned mutations=Wire.mutations;
    assert(write(66,kind>=3));assert(Wire.mutations==mutations);
    assert(configurePin(66,true));
    if(kind>=3)assert((Wire.port&4)!=0);else assert((Wire.regs[kind==1?0x14:0x0a]&4)==0);
    assert(write(66,false));assert(write(66,true));
    if(kind>=3)assert((Wire.port&~4u)==(pinCount()==16?0xfffbu:0xfbu));
    const unsigned after=Wire.mutations;assert(write(66,true));assert(Wire.mutations==after);
    bool high=false;assert(read(66,high)&&high);
    Wire.external=0xfffe;uint8_t pair=77;assert(readPair(64,65,pair)&&pair==1);
    if(kind>=3) {
      assert(configurePin(64,false,true));assert(!configurePin(64,false,false));
      assert(write(66,false));assert((Wire.port&1)!=0); // grounded input was not copied to latch
    }
    assert(Wire.clock==400000&&Wire.timeout==50);
    shutdown();assert(!online()&&configured());begin(board,&fs);assert(online());
  }
}
void aliases() {
  for(unsigned kind=4;kind<=5;++kind) {
    clean();Wire.reset(kind,kind==5?0x3f:0x27);MockFS fs;begin(board,&fs);
    const char* setup=kind==4?"set expander pcf8574t,0x27":"set expander pcf8574at,0x3f";
    assert(command(setup).find("OK - ")==0);
    assert(configIdentity()==((kind<<8)|Wire.address));
    assert(command("get expander").find(kind==4?"> pcf8574,0x27":"> pcf8574a,0x3F")==0);
  }
}
void persistenceAndClaims() {
  clean();Wire.reset(3);MockFS fs;begin(board,&fs);
  assert(command("set expander pcf8575,0x27").find("OK - ")==0);
  assert(mesh::expander::tryClaim(Owner::Rotary,3));assert(!mesh::expander::tryClaim(Owner::Outputs,2));assert(claimedByOther(65,Owner::Outputs));
  assert(!claimedByOther(64,Owner::Rotary));assert(mesh::expander::tryClaim(Owner::Outputs,4));
  const unsigned writes=fs.writes;
  assert(command("set expander off").find("disable expander rotary")!=std::string::npos&&fs.writes==writes);
  Wire.fail_read=true;uint8_t pair=42;assert(!readPair(64,65,pair)&&pair==42&&!online());
  assert(ownsAddress(0x27)&&mesh::expander::held(Owner::Rotary)==3&&mesh::expander::held(Owner::Outputs)==4);
  const unsigned io=Wire.reads+Wire.mutations;fakeTime()+=2000;loop();assert(Wire.reads+Wire.mutations==io);
  assert(mesh::expander::tryClaim(Owner::Rotary,0));assert(!mesh::expander::tryClaim(Owner::Rotary,8));
  mesh::expander::release(Owner::Rotary,3);mesh::expander::release(Owner::Outputs,4);
  assert(command("set expander off")=="OK - expander off");assert(!configured());
  Wire.fail_read=false;begin(board,&fs);assert(!configured());
}
void failuresAndRecovery() {
  clean();Wire.reset(1);MockFS fs;begin(board,&fs);
  fs.fail_write=true;assert(command("set expander mcp23017,0x27").find("save unconfirmed")!=std::string::npos);
  assert(!configured()&&Wire.mutations==0);fs.fail_write=false;
  assert(command("set expander mcp23017,0x27").find("OK - ")==0);
  fs.on_write=claimDuringSave;
  assert(command("set expander mcp23017,0x27").find("OK - ")==0);
  assert(attempted_during_save&&!claimed_during_save&&mesh::expander::held(Owner::Rotary)==0);
  fs.on_write=nullptr;
  assert(write(66,true)&&configurePin(66,true));Wire.fail_write=true;
  assert(!write(66,false)&&!online());Wire.fail_write=false;
  begin(board,&fs);assert(online());
  // A partial register-pointer write must still finish its transaction.
  Wire.short_write=true;bool high=true;assert(!read(64,high)&&!Wire.active&&!online());Wire.short_write=false;
  begin(board,&fs);assert(online());Wire.short_read=true;uint8_t pair=42;
  assert(!readPair(64,65,pair)&&pair==42&&!online());Wire.short_read=false;
  Wire.present=false;begin(board,&fs);assert(configured()&&!online()&&ownsAddress(0x27));
  Wire.present=true;const unsigned before=Wire.mutations;
  setAddressConflictCheck(conflict);reserve=true;begin(board,&fs);
  assert(configured()&&!online()&&ownsAddress(0x27)&&Wire.mutations==before);
  assert(command("get expander").find("address reserved")!=std::string::npos);
  assert(command("set expander mcp23017,0x27").find("address is reserved")!=std::string::npos);
  reserve=false;begin(board,&fs);assert(online());
  fs.files["/expander_v1.bak"]=fs.files["/expander_v1"];fs.files.erase("/expander_v1");
  begin(board,&fs);assert(online()&&fs.exists("/expander_v1")&&!fs.exists("/expander_v1.bak"));
  shutdown();fs.files["/expander_v1"][4]^=1;const unsigned operations=Wire.reads+Wire.mutations;
  begin(board,&fs);assert(!configured()&&command("get expander").find("rejected")!=std::string::npos);
  assert(Wire.reads+Wire.mutations==operations); // corrupt configuration cannot touch a device
}
int main() { parsing();defaults();allModels();aliases();persistenceAndClaims();failuresAndRecovery();clean(); }
'''


class I2cExpanderTest(unittest.TestCase):
    def test_real_driver_models_persistence_and_failures(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("native C++ compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="meshcore-expanders-") as directory:
            folder = Path(directory)
            (folder / "helpers").mkdir()
            filesystem = FILESYSTEM.replace(
                "  void flush() {}", "  void flush() {}\n  bool setBufferSize(size_t) { return true; }")
            filesystem = filesystem.replace("unsigned writes=0,renames=0;", "unsigned writes=0,renames=0; void (*on_write)()=nullptr;")
            filesystem = filesystem.replace("++fs->writes;", "++fs->writes;if(fs->on_write)fs->on_write();")
            for path, value in {
                "helpers/IdentityStore.h": filesystem,
                "helpers/ContactFileTransaction.h": (ROOT / "src/helpers/ContactFileTransaction.h").read_text(),
                "helpers/PersistentStoreFormat.h": (ROOT / "src/helpers/PersistentStoreFormat.h").read_text(),
                "helpers/FilePresence.h": "#pragma once\nnamespace mesh { template<class FS> bool filePresence(FS* fs,const char* p,bool& present) { if(!fs)return false;present=fs->exists(p);return true;} }\n",
                "Arduino.h": ARDUINO, "Wire.h": WIRE,
                "target.h": "#pragma once\n#define PIN_BOARD_SDA 4\n#define PIN_BOARD_SCL 3\n",
                "test.cpp": HARNESS,
            }.items():
                (folder / path).write_text(value)
            binary = folder / "expanders.exe"
            command = [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-Wno-misleading-indentation",
                       "-DMESH_CUSTOM_PERIPHERALS=1", "-DESP32_PLATFORM=1", "-I", str(folder),
                       "-I", str(ROOT / "src"), str(folder / "test.cpp"), "-o", str(binary)]
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_disabled_platform_header_keeps_native_pins_and_rejects_virtual(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("native C++ compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="meshcore-expander-stubs-") as directory:
            folder = Path(directory)
            source = folder / "test.cpp"
            source.write_text(r'''
#include <cassert>
#include <helpers/I2cExpander.h>
namespace mesh { class MainBoard {}; }
int main() {
  mesh::MainBoard board;mesh::expander::begin(board,nullptr);
  const char* text="6";int16_t pin=-1;assert(mesh::expander::parsePinToken(text,pin)&&pin==6);
  text="x0";assert(!mesh::expander::parsePinToken(text,pin));
  assert(!mesh::expander::isPin(64)&&!mesh::expander::online());
  assert(mesh::expander::tryClaim(mesh::pins::Owner::Rotary,0));
}
''')
            binary = folder / "stubs.exe"
            built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                    "-DMESH_CUSTOM_PERIPHERALS=0", "-I", str(ROOT / "src"),
                                    str(source), "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
