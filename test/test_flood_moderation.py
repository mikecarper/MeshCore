"""Execute real moderation decoding and FGM1 saves with reset and I/O faults."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method
from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/CLIReplyText.h>
#include <helpers/FloodGroupPlainText.h>
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
#define PUB_KEY_SIZE 32
#define CIPHER_KEY_SIZE 16
#define CIPHER_BLOCK_SIZE 16
#define CIPHER_MAC_SIZE 2
#define PATH_HASH_SIZE 1
#define MAX_PACKET_PAYLOAD 250
#define TXT_TYPE_PLAIN 0
#define PAYLOAD_TYPE_GRP_TXT 5
#define FLOOD_GROUP_MODERATION_SLOTS 16
#define FLOOD_CHANNEL_KEY_PREFIX_LEN 8
#define FLOOD_GROUP_MODERATION_NAME_LEN 32
#define FLOOD_GROUP_MODERATION_PATH_HOPS_MAX 3
#define FLOOD_GROUP_MODERATION_PATH_BYTES_MAX 9
#define FLOOD_GROUP_MODERATION_HOPS_ALL 255
#define FLOOD_GROUP_MODERATION_RATE_UNLIMITED 65535
#define FLOOD_PACKET_FILTER_MAX_HOPS 63
#define FLOOD_CHANNEL_SCOPE_SLOTS 31
#define FLOOD_RETRY_BRIDGE_BUCKETS 6
#define FLOOD_GROUP_MODERATION_FILE "/flood_grp_mod"
#define FLOOD_GROUP_MODERATION_TEMP_FILE "/flood_grp_mod.tmp"
#define FLOOD_GROUP_MODERATION_BACKUP_FILE "/flood_grp_mod.bak"
#define FILE_O_WRITE 1
#define MESH_DEBUG_PRINTLN(...) ((void)0)
struct PowerCut {};
struct MockFS;
struct File {
  MockFS* fs = nullptr;
  std::string path;
  size_t cursor = 0;
  bool valid = false;
  explicit operator bool() const { return valid; }
  size_t size() const;
  size_t read(uint8_t*, size_t);
  size_t write(const uint8_t*, size_t);
  void close();
  void flush();
};
struct MockFS {
  std::map<std::string, std::vector<uint8_t>> files;
  unsigned operations = 0, cut_after = 0;
  bool short_write = false, corrupt_write = false;
  std::string fail_open_path, fail_read_path, fail_rename_from;
  std::string false_after_rename, noop_rename_from, corrupt_rename_to;
  void checkpoint() { if (++operations == cut_after) throw PowerCut{}; }
  bool exists(const char* path) { bool found = files.count(path); checkpoint(); return found; }
  bool remove(const char* path) { bool removed = files.erase(path); checkpoint(); return removed; }
  bool rename(const char* from, const char* to) {
    if (fail_rename_from == from) { checkpoint(); return false; }
    if (noop_rename_from == from) { checkpoint(); return true; }
    if (!files.count(from) || files.count(to)) { checkpoint(); return false; }
    files[to] = files[from]; files.erase(from);
    if (corrupt_rename_to == to) files[to][0] ^= 0x40;
    checkpoint(); return false_after_rename != from;
  }
  File open(const char* path, const char* mode, bool = false) {
    if (fail_open_path == path) { checkpoint(); return {}; }
    if (*mode == 'w') files[path].clear();
    File file{this, path, 0, files.count(path) != 0}; checkpoint(); return file;
  }
  File open(const char* path, int mode) { return open(path, mode ? "w" : "r"); }
};
size_t File::size() const { return fs->files.at(path).size(); }
size_t File::read(uint8_t* out, size_t size) {
  if (fs->fail_read_path == path) { fs->checkpoint(); return 0; }
  size = std::min(size, fs->files[path].size() - cursor);
  memcpy(out, fs->files[path].data() + cursor, size); cursor += size;
  fs->checkpoint(); return size;
}
size_t File::write(const uint8_t* in, size_t size) {
  if (fs->short_write) size /= 2;
  auto& bytes = fs->files[path]; bytes.insert(bytes.end(), in, in + size);
  if (fs->corrupt_write && size) bytes.back() ^= 0x80;
  fs->checkpoint(); return size;
}
void File::close() { fs->checkpoint(); }
void File::flush() { fs->checkpoint(); }
#define FILESYSTEM MockFS
namespace mesh {
struct Packet {
  uint8_t payload[MAX_PACKET_PAYLOAD] = {}, payload_len = 19;
  uint8_t hops = 2, path[9] = {}, width = 1, type = PAYLOAD_TYPE_GRP_TXT;
  bool flood = true;
  bool isRouteFlood() const { return flood; }
  uint8_t getPayloadType() const { return type; }
  uint8_t getPathHashCount() const { return hops; }
  uint8_t getPathHashSize() const { return width; }
};
static std::vector<uint8_t> plaintext;
static bool authenticated = true;
struct Utils {
  static bool isHexChar(char c) { return (c>='0'&&c<='9')||(c>='A'&&c<='F')||(c>='a'&&c<='f'); }
  static bool fromHex(uint8_t* dest, size_t count, const char* text) {
    for(size_t i=0;i<count;++i) {
      unsigned value=0;if(sscanf(text+i*2,"%2x",&value)!=1)return false;dest[i]=uint8_t(value);
    } return true;
  }
  static void toHex(char* dest, const uint8_t* bytes, size_t size) {
    for(size_t i=0;i<size;++i)sprintf(dest+i*2,"%02X",bytes[i]);
  }
  static void sha256(uint8_t* dest, size_t length, const uint8_t*, size_t) { memset(dest, 0x11, length); }
  static int MACThenDecrypt(const uint8_t*, uint8_t* dest, const uint8_t*, size_t) {
    if (!authenticated) return 0;
    memcpy(dest, plaintext.data(), plaintext.size()); return int(plaintext.size());
  }
};
}
struct Clock { uint32_t now = 10; uint32_t getMillis() const { return now; } };
namespace FloodFilterPolicy {
  enum {SCOPE_PATH_NONE=0,SCOPE_PATH_BLACKLIST=1,SCOPE_PATH_BRIDGE_BUCKET_BASE=2};
}
static const char FLOOD_CHANNEL_SCOPE_USAGE[]="Err - scope usage";
static std::string moderation_detail_reply="> saved";
struct MyMesh {
  @ENTRY@
  MockFS* _fs;
  Clock clock; Clock* _ms = &clock;
  FloodGroupModerationEntry flood_group_moderation[FLOOD_GROUP_MODERATION_SLOTS] = {};
  explicit MyMesh(MockFS& fs) : _fs(&fs) {}
  bool readFloodGroupModerationFile(const char*, FloodGroupModerationEntry*, bool* = nullptr) const;
  bool recoverFloodGroupModeration();
  void loadFloodGroupModeration();
  bool saveFloodGroupModeration();
  bool decodeFloodGroupPlainText(const mesh::Packet*,const uint8_t*,uint8_t,uint32_t&,char*,size_t) const;
  bool decodeFloodGroupPlainTextSpan(const mesh::Packet*,const uint8_t*,uint8_t,uint8_t*,size_t,mesh::FloodGroupPlainText::View&) const;
  bool shouldBlockFloodGroupTextForward(const mesh::Packet*);
  void setFloodGroupModeration(const char*,char*);
  void setFloodChannelScope(const char*,char*);
  uint8_t scope_path=0;bool scope_slow=false;
  void formatFloodGroupModerationDetail(int,char* reply,size_t size) const { snprintf(reply,size,"%s",moderation_detail_reply.c_str()); }
};
namespace StrHelper { void strncpy(char* dest,const char* source,size_t size) { snprintf(dest,size,"%s",source); } }
const uint8_t FLOOD_PUBLIC_CHANNEL_SECRET[16]={};
@SPACES@
@ASCII@
@UNSIGNED@
@MOD_UNSIGNED@
@TOKEN@
@CHANNEL@
@MOD_PATH@
@NAME_VALID@
@SCOPE_INDEX@
static File openFloodSettingsRead(FILESYSTEM* fs, const char* path) { return fs->open(path,"r"); }
@WRITE@
@HASH@
@VERIFY@
@PATH@
@READ@
@RECOVER@
@LOAD@
@SAVE@
@DECODE@
@SPAN@
@BLOCK@
@SET@
@SET_SCOPE@
static const char* target = FLOOD_GROUP_MODERATION_FILE;
static const char* scratch = FLOOD_GROUP_MODERATION_TEMP_FILE;
static const char* backup = FLOOD_GROUP_MODERATION_BACKUP_FILE;

void configure(MyMesh& mesh, const char* name, uint16_t rate = 65535, uint8_t hops = 2) {
  auto& entry = mesh.flood_group_moderation[0];
  entry = {}; entry.active = true; entry.key_len = 16;
  memset(entry.hash_prefix, 0x11, sizeof(entry.hash_prefix));
  strcpy(entry.channel_name, "public"); strcpy(entry.sender, name);
  entry.max_hops = hops; entry.rate_per_minute = rate;
}
std::vector<uint8_t> plain(const std::string& text) {
  std::vector<uint8_t> bytes = {0x12,0x34,0x56,0x78,0};
  bytes.insert(bytes.end(),text.begin(),text.end()); return bytes;
}
mesh::Packet packet(const std::string& name) {
  mesh::plaintext = plain(name + ": body");
  mesh::Packet packet; packet.payload[0] = 0x11;
  packet.payload_len = uint8_t(3 + ((mesh::plaintext.size() + 15) / 16) * 16);
  mesh::authenticated = true; return packet;
}
std::vector<uint8_t> golden(MyMesh& mesh, unsigned count = 16) {
  std::vector<uint8_t> bytes = {'F','G','M','1',uint8_t(count)};
  auto append = [&bytes](const void* ptr, size_t size) {
    const auto* in = static_cast<const uint8_t*>(ptr); bytes.insert(bytes.end(),in,in+size);
  };
  for (unsigned i=0;i<count;++i) {
    const auto& entry = mesh.flood_group_moderation[i];
    bytes.push_back(entry.active); bytes.push_back(entry.key_len);
    append(entry.secret,32); append(entry.channel_name,32); append(entry.sender,32);
    bytes.push_back(entry.path_hash_size); bytes.push_back(entry.path_hops);
    append(entry.path,9); bytes.push_back(entry.max_hops);
    bytes.push_back(uint8_t(entry.rate_per_minute)); bytes.push_back(uint8_t(entry.rate_per_minute >> 8));
  }
  return bytes;
}
void spans() {
  using namespace mesh::FloodGroupPlainText;
  View view;
  for (size_t size : {size_t(1),size_t(31),size_t(32),size_t(200)}) {
    auto bytes = plain(std::string(size,'A') + "  : body: more");
    assert(parse(bytes.data(),bytes.size(),0,view) && view.has_sender);
    assert(view.sender.size == size && view.text.size == 10);
    assert(std::string((const char*)view.text.data,view.text.size) == "body: more");
  }
  for (const std::string& text : std::vector<std::string>{"no colon",": empty", "   : empty", "Al\nice: body", std::string("A\0lice: body",12)}) {
    auto bytes = plain(text); assert(parse(bytes.data(),bytes.size(),0,view) && !view.has_sender);
  }
  auto bytes = plain("Alice: "); assert(parse(bytes.data(),bytes.size(),0,view));
  assert(senderEquals(view,"ALICE") && !senderEquals(view,"AliceX") && view.text.size == 0);
  bytes=plain("Alice: body");bytes.resize(32,0);
  assert(parse(bytes.data(),bytes.size(),0,view) && view.text.size==4);
  bytes[4] = 4; assert(!parse(bytes.data(),bytes.size(),0,view));
  for (size_t size=0;size<=5;++size) assert(!parse(bytes.data(),size,0,view));
  assert(!parse(nullptr,100,0,view));
}
void moderation() {
  MockFS fs; MyMesh mesh(fs); configure(mesh,"*");
  for (size_t size : {size_t(31),size_t(32),size_t(200)}) {
    auto p = packet(std::string(size,'A')); assert(mesh.shouldBlockFloodGroupTextForward(&p));
    p.hops = 1; assert(!mesh.shouldBlockFloodGroupTextForward(&p));
  }
  configure(mesh,"Alice",0,255);
  auto p = packet("aLiCe  "); assert(mesh.shouldBlockFloodGroupTextForward(&p));
  uint8_t key[32]={}; uint32_t timestamp=0; char sender[32];
  assert(mesh.decodeFloodGroupPlainText(&p,key,16,timestamp,sender,sizeof(sender)));
  assert(!strcmp(sender,"aLiCe") && timestamp==0x78563412);
  configure(mesh,std::string(31,'A').c_str(),0,255);
  p=packet(std::string(32,'A')); assert(!mesh.shouldBlockFloodGroupTextForward(&p));
  assert(!mesh.decodeFloodGroupPlainText(&p,key,16,timestamp,sender,sizeof(sender)) && !sender[0]);
  configure(mesh,"*"); p=packet("Alice"); mesh::authenticated=false;
  assert(!mesh.shouldBlockFloodGroupTextForward(&p)); mesh::authenticated=true;
  for (const std::string name : {"A\nlice", "", "   "}) {
    p=packet(name); assert(!mesh.shouldBlockFloodGroupTextForward(&p));
  }
  p=packet("Alice"); mesh::plaintext=plain("no colon");
  assert(!mesh.shouldBlockFloodGroupTextForward(&p));
  p=packet("Alice"); p.flood=false; assert(!mesh.shouldBlockFloodGroupTextForward(&p));
  p.flood=true; p.type=6; assert(!mesh.shouldBlockFloodGroupTextForward(&p));
  p.type=5; p.payload[0]^=1; assert(!mesh.shouldBlockFloodGroupTextForward(&p));
  p.payload[0]^=1; p.payload_len-=1; assert(!mesh.shouldBlockFloodGroupTextForward(&p));
  p=packet("Alice"); uint8_t too_small[15]; mesh::FloodGroupPlainText::View view;
  assert(!mesh.decodeFloodGroupPlainTextSpan(&p,key,16,too_small,sizeof(too_small),view));
}
void formats() {
  MockFS fs; MyMesh mesh(fs); configure(mesh,"Alice",3,5);
  assert(mesh.saveFloodGroupModeration()); const auto bytes=fs.files[target];
  assert(bytes==golden(mesh) && bytes.size()==1797);
  for(unsigned count=0;count<=16;++count) {
    fs.files[target]=golden(mesh,count); MyMesh boot(fs); boot.loadFloodGroupModeration();
    assert(boot.flood_group_moderation[0].active==(count!=0));
  }
  for(size_t size=0;size<bytes.size();++size) {
    fs.files.clear();fs.files[target]={bytes.begin(),bytes.begin()+size};
    MyMesh boot(fs);boot.loadFloodGroupModeration();assert(!boot.flood_group_moderation[0].active);
  }
  for(unsigned invalid : {0U,4U,5U,6U,39U,71U,103U,104U,114U}) {
    fs.files.clear();fs.files[target]=bytes;
    if(invalid==39||invalid==71) std::fill(fs.files[target].begin()+invalid,fs.files[target].begin()+invalid+32,'X');
    else fs.files[target][invalid]=invalid==114?64:0xff;
    MyMesh boot(fs);boot.loadFloodGroupModeration();assert(!boot.flood_group_moderation[0].active);
  }
  fs.files.clear();fs.files[target]=bytes;fs.files[target].push_back(0);
  MyMesh boot(fs);boot.loadFloodGroupModeration();assert(!boot.flood_group_moderation[0].active);
}
void failures() {
  MockFS base; MyMesh initial(base); configure(initial,"Alice",3,5); assert(initial.saveFloodGroupModeration());
  const auto old=base.files[target];
  for(unsigned fault=0;fault<9;++fault) {
    MockFS fs;fs.files=base.files;MyMesh mesh(fs);configure(mesh,"Bob",7,9);
    if(fault==0)fs.short_write=true;
    if(fault==1)fs.corrupt_write=true;
    if(fault==2)fs.fail_open_path=scratch;
    if(fault==3)fs.fail_read_path=scratch;
    if(fault==4)fs.fail_rename_from=target;
    if(fault==5)fs.fail_rename_from=scratch;
    if(fault==6)fs.noop_rename_from=target;
    if(fault==7)fs.noop_rename_from=scratch;
    if(fault==8)fs.corrupt_rename_to=target;
    assert(!mesh.saveFloodGroupModeration());
    MockFS reboot;reboot.files=fs.files;MyMesh boot(reboot);boot.loadFloodGroupModeration();
    assert(boot.flood_group_moderation[0].active && !strcmp(boot.flood_group_moderation[0].sender,"Alice"));
    assert(reboot.files[target]==old);
  }
  for(const char* path : {target,backup}) {
    MockFS fs;fs.files=base.files;if(path==backup){fs.files[backup]=old;fs.files.erase(target);}
    fs.fail_read_path=path;MyMesh mesh(fs);configure(mesh,"Bob");
    assert(!mesh.saveFloodGroupModeration() && fs.files[path]==old);
  }
  for(const char* path : {target,scratch}) {
    MockFS fs;fs.files=base.files;fs.false_after_rename=path;MyMesh mesh(fs);configure(mesh,"Bob");
    assert(mesh.saveFloodGroupModeration());MyMesh boot(fs);boot.loadFloodGroupModeration();
    assert(!strcmp(boot.flood_group_moderation[0].sender,"Bob"));
  }
}
void recovery() {
  MockFS original;MyMesh mesh(original);configure(mesh,"Alice",3,5);assert(mesh.saveFloodGroupModeration());
  auto old=original.files[target];configure(mesh,"Bob",7,9);assert(mesh.saveFloodGroupModeration());auto next=original.files[target];
  for(unsigned state=0;state<6;++state) {
    MockFS fs;
    if(state==0){fs.files[target]=old;fs.files[scratch]=next;}
    if(state==1){fs.files[backup]=old;fs.files[scratch]=next;}
    if(state==2){fs.files[target]={'x'};fs.files[backup]=old;fs.files[scratch]=next;}
    if(state==3){fs.files[target]=next;fs.files[backup]=old;}
    if(state==4){fs.files[scratch]=next;}
    if(state==5){fs.files[backup]=old;fs.fail_rename_from=backup;}
    MyMesh boot(fs);boot.loadFloodGroupModeration();
    assert(boot.flood_group_moderation[0].active);
    assert(!strcmp(boot.flood_group_moderation[0].sender,state==3||state==4?"Bob":"Alice"));
    if(state==5)assert(fs.files[backup]==old);
  }
  MockFS completed;completed.files[target]=old;MyMesh save(completed);configure(save,"Bob",7,9);
  assert(save.saveFloodGroupModeration());unsigned steps=completed.operations;
  for(unsigned cut=1;cut<=steps;++cut) {
    MockFS fs;fs.files[target]=old;fs.cut_after=cut;MyMesh saving(fs);configure(saving,"Bob",7,9);
    try {saving.saveFloodGroupModeration();} catch(const PowerCut&) {}
    MockFS reboot;reboot.files=fs.files;MyMesh boot(reboot);boot.loadFloodGroupModeration();
    assert(boot.flood_group_moderation[0].active);
    assert(reboot.files[target]==old||reboot.files[target]==next);
  }
}
void cli() {
  MockFS first,second;MyMesh longform(first),shortform(second);char reply[160];
  struct Case {const char* full;const char* compact;};
  for(const Case& item : std::vector<Case>{
      {" public Alice rate=3/min hops=5 path=A1B2C3,D4E5F6"," public Alice q=3 h=5 p=A1B2C3,D4E5F6"},
      {" public Alice rate=7/min hops=all path=*"," public Alice q=7/min h=all p=*"},
      {" public Alice drop path=71"," public Alice d p=71"},
      {" public 'Field User' rate=8/m hops=0"," public 'Field User' q=8/m h=0"},
      {" public '*' hops=2"," public '*' h=2"}}) {
    longform.setFloodGroupModeration(item.full,reply);assert(!strncmp(reply,"OK",2));
    shortform.setFloodGroupModeration(item.compact,reply);assert(!strncmp(reply,"OK",2));
    assert(first.files[target]==second.files[target]);
  }
  // The prefix consumes five bytes. Preserve the existing154-byte detail
  // bound, NUL termination, and caller memory for either formatter style.
  struct GuardedReply { char text[160]; uint32_t canary=0xA5B6C7D8; } bounded;
  for (bool prefixed : {false,true}) {
    moderation_detail_reply=(prefixed ? "> " : "")+std::string(159,'X');
    longform.setFloodGroupModeration(" public Alice q=9",bounded.text);
    assert(strlen(bounded.text)==159 && !strncmp(bounded.text,"OK - ",5));
    assert(std::string(bounded.text+5)==std::string(154,'X'));
    assert(bounded.canary==0xA5B6C7D8);
  }
  moderation_detail_reply="> saved";
  const auto original=second.files[target];
  for(const char* invalid : {" public Alice q=1 rate=2/min", " public Alice rate=1/min q=2",
      " public Alice d q=2", " public Alice q=2 drop", " public Alice q=", " public Alice q=-1",
      " public Alice q=65535", " public Alice q=7/sec", " public Alice q=1/min/min",
      " public Alice h=64", " public Alice p=A1,B2C3 d", " public Alice p= d",
      " public Alice d extra", " public Alice rate=7"}) {
    shortform.setFloodGroupModeration(invalid,reply);assert(!strncmp(reply,"Err",3));
    assert(second.files[target]==original);
  }
  second.short_write=true;shortform.setFloodGroupModeration(" public Alice q=4",reply);
  assert(strstr(reply,"save unconfirmed")&&strstr(reply,"reboot may differ"));
  for(const Case& item : std::vector<Case>{
      {" public scope=local path=blacklist tx=slow"," public scope=local p=bl tx=s"},
      {" public scope=local path=bucket:1 tx=fast"," public scope=local p=b1 tx=f"},
      {" public scope=local path=bucket:6"," public scope=local p=b6"}}) {
    longform.setFloodChannelScope(item.full,reply);assert(!strcmp(reply,"OK"));
    shortform.setFloodChannelScope(item.compact,reply);assert(!strcmp(reply,"OK"));
    assert(longform.scope_path==shortform.scope_path&&longform.scope_slow==shortform.scope_slow);
  }
  for(const char* invalid : {" public scope=local p=b0", " public scope=local p=b7",
      " public scope=local p=b", " public scope=local p=b1junk", " public scope=local p=blacklist",
      " public scope=local p=bl path=bucket:1", " public scope=local tx=s tx=fast",
      " public scope=local tx=x", " public scope=local tx=f path=bucket:01x"}) {
    shortform.setFloodChannelScope(invalid,reply);assert(!strncmp(reply,"Err",3));
  }
}
int main(int argc,char** argv) {
  assert(argc==2);std::string which=argv[1];
  if(which=="spans")spans();else if(which=="moderation")moderation();
  else if(which=="formats")formats();else if(which=="failures")failures();
  else if(which=="recovery")recovery();else if(which=="cli")cli();else assert(false);
}
'''


class FloodModerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            raise unittest.SkipTest('native C++ compiler unavailable')
        source = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text()
        header = (ROOT / 'examples/simple_repeater/MyMesh.h').read_text()
        entry = re.search(r'  struct FloodGroupModerationEntry \{.*?\n  \};', header, re.S).group()
        harness = HARNESS.replace('@ENTRY@', entry)
        methods = {
            'WRITE': 'static File openFloodSettingsWrite(',
            'HASH': 'static uint32_t updateFloodSettingsHash(',
            'VERIFY': 'static bool verifyFloodSettingsWrite(',
            'PATH': 'static bool floodModerationPathMatches(',
            'READ': 'bool MyMesh::readFloodGroupModerationFile(',
            'RECOVER': 'bool MyMesh::recoverFloodGroupModeration(',
            'LOAD': 'void MyMesh::loadFloodGroupModeration(',
            'SAVE': 'bool MyMesh::saveFloodGroupModeration(',
            'DECODE': 'bool MyMesh::decodeFloodGroupPlainText(',
            'SPAN': 'bool MyMesh::decodeFloodGroupPlainTextSpan(',
            'BLOCK': 'bool MyMesh::shouldBlockFloodGroupTextForward(',
            'SPACES': 'static const char* skipFloodFilterSpaces(',
            'ASCII': 'static bool floodFilterAsciiEqual(',
            'UNSIGNED': 'static bool parseFloodFilterUnsigned(',
            'MOD_UNSIGNED': 'static bool parseFloodModerationUnsigned(',
            'TOKEN': 'static int takeFloodModerationToken(',
            'CHANNEL': 'static bool parseFloodModerationChannel(',
            'MOD_PATH': 'static bool parseFloodModerationPath(',
            'NAME_VALID': 'static bool floodModerationSenderNameValid(',
            'SET': 'void MyMesh::setFloodGroupModeration(',
            'SCOPE_INDEX': 'static bool parseFloodChannelScopeIndex(',
        }
        for marker, signature in methods.items():
            harness = harness.replace('@' + marker + '@', method(source[source.rfind(signature):], signature))
        # Execute the complete production scope option parser before its
        # unrelated region lookup and filesystem mutation phase.
        setter = method(source, 'void MyMesh::setFloodChannelScope(')
        setter = setter[:setter.index('  bool direct_target =')]
        setter += '  scope_path=path_selector;scope_slow=slow_timing;strcpy(reply,"OK");\n}\n'
        harness = harness.replace('@SET_SCOPE@', setter)
        cls.temp = tempfile.TemporaryDirectory(prefix='meshcore-flood-moderation-')
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        test = work / 'moderation.cpp'
        test.write_text(harness)
        cls.binaries = {}
        for platform in ('NRF52', 'STM32', 'ESP32', 'RP2040'):
            binary = work / platform
            result = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                *SANITIZER_FLAGS, '-D' + platform + '_PLATFORM=1', '-I', str(ROOT / 'src'),
                str(test), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[platform] = binary

    def scenario(self, name):
        for platform, binary in self.binaries.items():
            with self.subTest(platform=platform):
                result = subprocess.run([str(binary), name], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_full_length_sender_and_body_views(self):
        self.scenario('spans')

    def test_wildcard_moderation_cannot_be_bypassed_by_long_sender(self):
        self.scenario('moderation')

    def test_fgm1_compatibility_and_malformed_images(self):
        self.scenario('formats')

    def test_failed_saves_preserve_previous_rules_after_reboot(self):
        self.scenario('failures')

    def test_transaction_recovery_and_every_power_cut_boundary(self):
        self.scenario('recovery')

    def test_cli_shortforms_match_full_commands_and_preserve_validation(self):
        self.scenario('cli')


if __name__ == '__main__':
    unittest.main()
