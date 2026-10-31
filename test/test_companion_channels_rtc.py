"""Exercise the actual channel table, constructor, and disk format with RTC RAM."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]

PROGRAM = r'''
#include <cassert>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <type_traits>
#include "helpers/CompanionChannelStorage.h"
#include "FakeFilesystem.h"
#include "helpers/ContactFileTransaction.h"
#define MESH_DEBUG_PRINTLN(...) do {} while(0)
#define MAX_ANON_CONTACTS 8
static mesh::Radio radio;
static mesh::MillisecondClock ms_clock;
static mesh::RNG rng;
static mesh::RTCClock rtc;
static mesh::PacketManager packets;
static mesh::MeshTables tables;
using Bank = ChannelDetails[MAX_GROUP_CHANNELS];
#if COMPANION_CHANNELS_RTC_STORAGE
struct EarlyBind {
  EarlyBind() { mesh::companion::rtcChannels()[39].name[0]='x'; }
} early_bind;
#endif
@STORAGE@

struct StrHelper { static void strncpy(char*,const char*,size_t); };
@STRNCPY@
static int decode_base64(unsigned char* source,size_t length,uint8_t* output) {
  (void)length;
  unsigned bytes=0,key=0;
  if(sscanf(reinterpret_cast<char*>(source),"%u:%u",&bytes,&key)!=2 || (bytes!=16 && bytes!=32))return 0;
  for(unsigned i=0;i<bytes;++i)output[i]=static_cast<uint8_t>(i+key);
  return static_cast<int>(bytes);
}

class BaseChatMesh : public mesh::Mesh {
  int num_contacts=0;
  uint32_t contact_table_revision=0;
  unsigned long txt_send_timeout=0;
  mesh::Packet* _pendingLoopback=nullptr;
  uint32_t connections[4];
@FIELDS@
 public:
@CONSTRUCTOR@
  ChannelDetails* addChannel(const char*,const char*);
  bool getChannel(int,ChannelDetails&);
  bool setChannel(int,const ChannelDetails&);
  int findChannelIdx(const mesh::GroupChannel&);
  int searchChannelsByHash(const uint8_t*,mesh::GroupChannel[],int);
@CALLBACKS@
  ChannelDetails* table() { return channels; }
  size_t tableBytes() const { return sizeof(channels); }
};
@OPERATIONS@

using DataStoreHost=BaseChatMesh;
static bool companionPathPresence(FakeFilesystem* fs,const char* path,bool& present) {
  if(fs->metadata_error)return false;
  present=fs->exists(path);return true;
}
@PROMOTE@
struct DataStore {
  bool _channel_load_incomplete=false;
  const char* _channel_recovery_source=nullptr;
  FakeFilesystem* _getContactsChannelsFS() {return &SPIFFS;}
  File openRead(FakeFilesystem* fs,const char* path) {return fs->open(path);}
  void loadChannels(DataStoreHost*);
  bool saveChannels(DataStoreHost*);
};
@PERSISTENCE@

struct Fixture {
  uint64_t before[4]={11,23,37,41};
  Bank bank{};
  uint64_t between[4]={43,59,61,71};
  BaseChatMesh mesh;
  uint64_t after[4]={83,89,97,101};
  explicit Fixture(Bank* external=nullptr)
    :mesh(radio,ms_clock,rng,rtc,packets,tables
#if COMPANION_CHANNELS_RTC_STORAGE
          ,external?*external:bank
#endif
    ) { (void)external; }
  void intact()const{
    const uint64_t a[]={11,23,37,41},b[]={43,59,61,71},c[]={83,89,97,101};
    assert(!memcmp(before,a,sizeof(a)) && !memcmp(between,b,sizeof(b)) && !memcmp(after,c,sizeof(c)));
  }
};
static void zero(const Bank& bank) {
  const uint8_t* bytes=reinterpret_cast<const uint8_t*>(&bank);
  for(size_t i=0;i<sizeof(bank);++i)assert(bytes[i]==0);
}
int main() {
  static_assert(sizeof(Bank)==MAX_GROUP_CHANNELS*sizeof(ChannelDetails),"array extent retained");
#if COMPANION_CHANNELS_RTC_STORAGE
  assert(mesh::companion::rtcChannels()[39].name[0]=='x');
  memset(static_cast<void*>(&companion_channels),0xa5,sizeof(companion_channels));
  companion_channels_initialized=false; // Model a new BSS boot with retained RTC bytes.
  zero(mesh::companion::rtcChannels());
  auto* bank=&mesh::companion::rtcChannels();
  (*bank)[0].name[0]='r';
  assert(&mesh::companion::rtcChannels()==bank && (*bank)[0].name[0]=='r');
  Fixture first(bank);
  zero(*bank);
  assert(first.mesh.table()==&(*bank)[0]);
#else
  Fixture first;
#endif
  Fixture independent;
  assert(first.mesh.table()!=independent.mesh.table());
  assert(first.mesh.tableBytes()==sizeof(Bank));
  const auto clean_size=sizeof(first.mesh);
#if COMPANION_CHANNELS_RTC_STORAGE
  assert(clean_size<sizeof(Bank));
#else
  assert(clean_size>sizeof(Bank));
#endif
  for(unsigned i=0;i<MAX_GROUP_CHANNELS;++i) {
    char name[32],key[32];
    snprintf(name,sizeof(name),"channel-%u",i);
    snprintf(key,sizeof(key),"%u:%u",i%2?16:32,i+1);
    auto* channel=first.mesh.addChannel(name,key);
    assert(channel==first.mesh.table()+i);
    channel->channel.tx_radio=static_cast<uint8_t>(i%5);
  }
  assert(!first.mesh.addChannel("overflow","32:99"));
  ChannelDetails value{};
  assert(!first.mesh.getChannel(-1,value) && !first.mesh.getChannel(MAX_GROUP_CHANNELS,value));
  assert(!first.mesh.setChannel(-1,value) && !first.mesh.setChannel(MAX_GROUP_CHANNELS,value));
  assert(first.mesh.getChannel(MAX_GROUP_CHANNELS-1,value));
  assert(first.mesh.findChannelIdx(value.channel)==MAX_GROUP_CHANNELS-1);
  mesh::GroupChannel found[4];
  assert(first.mesh.searchChannelsByHash(value.channel.hash,found,1)==1);
  assert(first.mesh.searchChannelsByHash(value.channel.hash,found,0)==0);
  Bank expected;
  memcpy(expected,first.mesh.table(),sizeof(expected));
  zero(independent.bank);
  ChannelDetails alternate=expected[3];strcpy(alternate.name,"independent");
  assert(independent.mesh.setChannel(3,alternate));
  assert(!memcmp(expected,first.mesh.table(),sizeof(expected)));
  assert(independent.mesh.getChannel(3,value) && !strcmp(value.name,"independent"));

  DataStore store;
  assert(store.saveChannels(&first.mesh));
  const auto durable=SPIFFS.files.at("/channels2");
  assert(durable.size()==MAX_GROUP_CHANNELS*68);
  for(unsigned i=0;i<MAX_GROUP_CHANNELS;++i) {
    assert(durable[i*68]==mesh::encodeRadioTxPolicy(expected[i].channel.tx_radio));
    assert(!memcmp(durable.data()+i*68+4,expected[i].name,32));
    assert(!memcmp(durable.data()+i*68+36,expected[i].channel.secret,32));
  }
#if COMPANION_CHANNELS_RTC_STORAGE
  memset(static_cast<void*>(&companion_channels),0xff,sizeof(companion_channels));
  companion_channels_initialized=false;
  Fixture restarted(&mesh::companion::rtcChannels());
#else
  Fixture restarted;
#endif
  store.loadChannels(&restarted.mesh);
  assert(!store._channel_load_incomplete);
  assert(!memcmp(expected,restarted.mesh.table(),sizeof(expected)));
  assert(store.saveChannels(&restarted.mesh) && SPIFFS.files.at("/channels2")==durable);
  // A failed publication preserves the existing disk image in either storage mode.
  SPIFFS.max_write=2;
  assert(!store.saveChannels(&restarted.mesh));
  assert(SPIFFS.files.at("/channels2")==durable);
  first.intact();independent.intact();restarted.intact();
  printf("channels=%zu mesh=%zu\n",sizeof(Bank),sizeof(BaseChatMesh));
}
'''


class CompanionChannelsRtcTests(unittest.TestCase):
    def compiler(self):
        result = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not result:
            self.skipTest("host C++ compiler required")
        return result

    def prepare(self, work):
        base = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text()
        header = (ROOT / "src/helpers/BaseChatMesh.h").read_text()
        mesh = (ROOT / "src/Mesh.h").read_text()
        store = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        (work / "helpers").mkdir()
        (work / "Arduino.h").write_text("#pragma once\n#include <stdint.h>\n#include <stddef.h>\n#include <string.h>\n#include <stdio.h>\n")
        (work / "sdkconfig.h").write_text("#ifndef CONFIG_IDF_TARGET_ESP32\n#define CONFIG_IDF_TARGET_ESP32 1\n#endif\n")
        (work / "esp_attr.h").write_text('#define RTC_SLOW_ATTR __attribute__((section(".rtc.force_slow")))\n')
        group = method(mesh, "class GroupChannel {") + ";"
        (work / "Mesh.h").write_text('''#pragma once
#include <stdint.h>
#include <stddef.h>
#include <RadioTxPolicy.h>
#define PATH_HASH_SIZE 1
#define PUB_KEY_SIZE 32
namespace mesh {
''' + group + '''
struct Radio{};struct MillisecondClock{};struct RNG{};struct RTCClock{};
struct PacketManager{};struct MeshTables{};struct Packet{};
struct Mesh { Mesh(Radio&,MillisecondClock&,RNG&,RTCClock&,PacketManager&,MeshTables&){} };
struct Utils { static void sha256(uint8_t* out,size_t size,const uint8_t* input,size_t len) {
  for(size_t i=0;i<size;++i)out[i]=static_cast<uint8_t>(input[0]+len+i);
} };
}
''')
        shutil.copyfile(ROOT / "test/fixtures/contact_cache/FakeFilesystem.h", work / "FakeFilesystem.h")
        shutil.copyfile(ROOT / "src/helpers/ContactFileTransaction.h", work / "helpers/ContactFileTransaction.h")
        (work / "helpers/IdentityStore.h").write_text('#pragma once\n#include "FakeFilesystem.h"\n#define FILESYSTEM FakeFilesystem\n')
        (work / "helpers/PersistentStoreFormat.h").write_text('#pragma once\nnamespace mesh { namespace storage { constexpr unsigned CONTACT_RECORD_SIZE=148; } }\n')
        storage_start = base.index("#if COMPANION_CHANNELS_RTC_STORAGE\n")
        storage = base[storage_start:base.index("#endif", storage_start) + len("#endif")]
        fields_start = header.index("#ifdef MAX_GROUP_CHANNELS\n#if COMPANION_CHANNELS_RTC_STORAGE")
        fields = header[fields_start:header.index("  mesh::Packet* _pendingLoopback;", fields_start)]
        operations = "\n".join(method(base, signature) for signature in (
            "ChannelDetails* BaseChatMesh::addChannel(", "bool BaseChatMesh::getChannel(",
            "bool BaseChatMesh::setChannel(", "int BaseChatMesh::findChannelIdx(",
            "int BaseChatMesh::searchChannelsByHash("))
        callbacks = "\n".join(method(companion, signature).replace(" override", "") for signature in (
            "bool onChannelLoaded(", "bool getChannelForSave("))
        values = dict(STORAGE=storage, FIELDS=fields, CONSTRUCTOR=method(header,"BaseChatMesh(mesh::Radio&"),
                      OPERATIONS=operations, CALLBACKS=callbacks,
                      STRNCPY=method((ROOT / "src/helpers/TxtDataHelpers.cpp").read_text(), "void StrHelper::strncpy("),
                      PROMOTE=method(store,"static bool promoteCompanionRecoveryFile("),
                      PERSISTENCE=method(store,"void DataStore::loadChannels(")+"\n"+method(store,"bool DataStore::saveChannels("))
        program = PROGRAM
        for key, value in values.items():
            program = program.replace("@" + key + "@", value)
        self.assertNotRegex(program, r"@[A-Z]+@")
        (work / "test.cpp").write_text(program)

    def flags(self, work, definitions):
        return [self.compiler(), "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                "-Wno-class-memaccess", *["-D" + item for item in definitions],
                "-I" + str(work), "-I" + str(ROOT / "src")]

    def test_actual_channel_operations_constructor_and_disk_roundtrip(self):
        with tempfile.TemporaryDirectory(prefix="companion-channels-rtc-") as directory:
            work = Path(directory)
            self.prepare(work)
            for enabled in (0, 1):
                with self.subTest(rtc=enabled):
                    definitions = ["ESP32_PLATFORM", "TBEAM_SX1262", "MESH_RADIO_AUTO_FAMILY=1",
                                   "MAX_GROUP_CHANNELS=40", "COMPANION_CHANNELS_RTC_STORAGE="+str(enabled)]
                    executable = work / ("check" + str(enabled) + (".exe" if os.name=="nt" else ""))
                    sanitize = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                                 "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
                    result = subprocess.run([*self.flags(work,definitions), *sanitize,
                        str(work / "test.cpp"), "-o", str(executable)], capture_output=True,text=True)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    result = subprocess.run([str(executable)],capture_output=True,text=True,timeout=30)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertIn("channels=2640",result.stdout)
                    objdump=shutil.which("objdump")
                    if objdump:
                        symbols=subprocess.run([objdump,"-t",str(executable)],check=True,capture_output=True,text=True).stdout
                        rows=[line for line in symbols.splitlines() if "companion_channelsE" in line]
                        self.assertEqual(len(rows),enabled,rows)
                        if enabled and os.name!="nt": self.assertIn(".rtc.force_slow",rows[0])

    def test_opt_in_rejects_other_boards_and_cpus(self):
        with tempfile.TemporaryDirectory(prefix="companion-channels-guard-") as directory:
            work=Path(directory);self.prepare(work)
            source=work/"guard.cpp";source.write_text('#include <helpers/CompanionChannelStorage.h>\n')
            good=["ESP32_PLATFORM","TBEAM_SX1262","MESH_RADIO_AUTO_FAMILY=1","MAX_GROUP_CHANNELS=40","COMPANION_CHANNELS_RTC_STORAGE=1"]
            invalid=[[x for x in good if x!=item] for item in good[:-1]]
            invalid += [good+["CONFIG_IDF_TARGET_ESP32=0"], ["COMPANION_CHANNELS_RTC_STORAGE=2"]]
            for definitions in invalid:
                with self.subTest(definitions=definitions):
                    result=subprocess.run([*self.flags(work,definitions),"-fsyntax-only",str(source)],capture_output=True,text=True)
                    self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([*self.flags(work,[]),"-fsyntax-only",str(source)],check=True)


if __name__ == "__main__":
    unittest.main()
