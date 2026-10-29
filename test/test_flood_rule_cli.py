"""Exercise the production repeater setter and packet-rule evaluation together."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_flood_rule_extensions import UTILS
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
PRELUDE = r'''
#include <algorithm>
#include <cassert>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <Utils.h>
#include <helpers/FloodRuleCLI.h>
#include <helpers/RegionNameUtils.h>
#define MESH_ENABLE_FLOOD_RULE_ENGINE 1
#define FLOOD_PACKET_FILTER_SCOPE_NAME_LEN 32
#define FLOOD_GROUP_MODERATION_NAME_LEN 32
#define FLOOD_CHANNEL_KEY_PREFIX_LEN 8
#define FLOOD_PACKET_FILTER_PATH_PREFIX_BYTES_MAX 9
#define FLOOD_PACKET_FILTER_ANY_TYPE 255
#define FLOOD_PACKET_FILTER_MAX_HOPS 63
#define FLOOD_PACKET_FILTER_SLOTS 63
#define FLOOD_GROUP_MODERATION_RATE_UNLIMITED 65535
#define REGION_DENY_FLOOD 1
static const char FLOOD_PACKET_FILTER_USAGE[]="Err - usage";
static const char FLOOD_PACKET_FILTER_DUPLICATE[]="Err - duplicate";
mesh::Packet::Packet() : header(0), payload_len(0), path_len(0), transport_codes{}, path{}, payload{},
    _rssi(0), _snr(0), tx_cr(0), flood_retry_policy(0) {}
static std::vector<uint8_t> decrypted;
static bool authentic = true;
int mesh::Utils::MACThenDecrypt(const uint8_t* secret, uint8_t* out, const uint8_t*, int) {
  if (!authentic || secret[0]!=0x42)return 0;
  assert(decrypted.size()<=MAX_PACKET_PAYLOAD);
  memcpy(out,decrypted.data(),decrypted.size());return int(decrypted.size());
}
struct RegionEntry { const char* name="Town"; uint8_t flags=0; bool isWildcard()const{return false;} };
struct TransportKey { bool isNull()const{return true;} uint16_t calcTransportCode(const mesh::Packet*)const{return 0;} };
struct RegionMap {
  static bool is_name_char(uint8_t c){return std::isalnum(c)||c=='_'||c=='-';}
  RegionEntry* findByNamePrefix(const char*){return nullptr;}
  int getTransportKeysFor(const RegionEntry&,TransportKey*,unsigned){return 0;}
};
void StrHelper::strzcpy(char* out,const char* in,size_t size){if(size)snprintf(out,size,"%s",in);}
void StrHelper::strncpy(char* out,const char* in,size_t size){if(size)snprintf(out,size,"%s",in);}
static void deriveFloodFilterScopeKey(const char*,TransportKey&){}
// Channel cryptography/resolution is separately exercised. The setter still
// performs every production key-vs-hash and content/routing compatibility gate.
static bool parseFloodRuleChannel(const char* text,uint8_t* secret,uint8_t& key_len,
                                 uint8_t* prefix,char* name,size_t capacity) {
  memset(secret,0,PUB_KEY_SIZE);memset(prefix,0,FLOOD_CHANNEL_KEY_PREFIX_LEN);
  uint8_t hash;
  if(FloodFilterPolicy::parseChannelHashMatcher(text,hash)){
    key_len=1;secret[0]=hash;prefix[0]=hash;snprintf(name,capacity,"hash:%02X",hash);return true;
  }
  if(strcmp(text,"#weather")&&strcmp(text,"public"))return false;
  key_len=16;secret[0]=0x42;snprintf(name,capacity,"%s",text);return true;
}
class MyMesh {
public:
@ENTRY@;
  FloodPacketFilterEntry rows[63]{};
  FloodPacketFilterEntry* flood_packet_filters=rows;
  uint8_t flood_packet_filter_slots=63,flood_channel_data_rule_slot=255;
  uint32_t flood_filter_probability_seed=0x12345678;
  RegionMap region_map;
  bool save_ok=true;
  unsigned saves=0;
  void setFloodPacketFilter(const char*,char*,bool require_explicit_action=false);
  bool saveFloodPacketFilters(){++saves;return save_ok;}
  void formatFloodPacketFilterDetail(int,char* out,size_t size,bool=false)const{snprintf(out,size,"> saved");}
  bool isAnyTempRadioActive()const{return false;}
  bool floodPacketFilterBlacklistMatches(const mesh::Packet*)const{return false;}
  bool floodPacketFilterFieldsMatch(const FloodPacketFilterEntry&,const mesh::Packet*,bool,uint16_t,bool,const RegionEntry*,uint8_t)const;
  bool authenticateFloodPacketFilterChannel(const FloodPacketFilterEntry&,const mesh::Packet*)const;
  bool resolveFloodPacketFilterTargetRegion(const char*,TransportKey&,const char*&){return false;}
  uint64_t applyFloodPacketFilterStop(uint64_t);
  uint64_t evaluateFloodPacketFilterMatches(const mesh::Packet*,bool,const RegionEntry*,uint8_t);
};
'''
SCENARIOS = r'''
static void set(MyMesh& mesh,const char* args,bool expected=true,bool strict=true){
  struct Reply {uint8_t before[8];char text[160];uint8_t after[8];} reply;
  memset(&reply,0xa5,sizeof(reply));
  mesh.setFloodPacketFilter(args,reply.text,strict);
  assert(!strncmp(reply.text,expected?"OK":"Err",expected?2:3));
  for(uint8_t byte:reply.before)assert(byte==0xa5);
  for(uint8_t byte:reply.after)assert(byte==0xa5);
}
static void plain(const std::string& text){
  decrypted.assign(5,0);decrypted.insert(decrypted.end(),text.begin(),text.end());decrypted.push_back(0);
}
static void parserAndRollback(){
  MyMesh mesh;
  set(mesh,R"(.1 grp_txt channel=#weather sender="^(Alice|Bob) [0-9]+$" text='weather (rain|sun)' len=20-80 route=flood prob=25 path=AA>BB$ drop)");
  const auto original=mesh.rows[0];
  assert(original.active&&original.drop_on_match&&original.path_hops==2&&original.extra.path_position==FloodRuleExtensions::Suffix);
  assert(original.extra.probability==25&&original.extra.payload_set&&original.extra.min_payload==20&&original.extra.max_payload==80);
  set(mesh,R"(.2 t=grp_txt c=#weather n="^(Alice|Bob) [0-9]+$" x='weather (rain|sun)' l=20-80 rt=f pr=25 pa=AA>BB$ d)");
  assert(FloodFilterPolicy::sameRuleConfiguration(mesh.rows[0],mesh.rows[1]));
  set(mesh,".1 enabled=off");assert(mesh.rows[0].active&&mesh.rows[0].extra.disabled);
  auto disabled=original;disabled.extra.disabled=1;
  assert(FloodFilterPolicy::sameRuleConfiguration(mesh.rows[0],disabled));
  const auto saves=mesh.saves;
  set(mesh,".1 off");assert(mesh.saves==saves);
  mesh.flood_channel_data_rule_slot=0;
  mesh.save_ok=false;
  set(mesh,".1 e=on",false);assert(mesh.rows[0].extra.disabled);
  assert(mesh.flood_channel_data_rule_slot==0);
  mesh.save_ok=true;
  set(mesh,".1 e=on");assert(mesh.flood_channel_data_rule_slot==255);
  mesh.save_ok=false;
  set(mesh,".1 any route=direct drop",false);
  assert(FloodFilterPolicy::sameRuleConfiguration(mesh.rows[0],original));
  mesh.save_ok=true;set(mesh,".1 on");assert(!mesh.rows[0].extra.disabled);
  assert(FloodFilterPolicy::sameRuleConfiguration(mesh.rows[0],original));
  for(const char* invalid:{".3 grp_txt c=hash:00 n=Alice drop",".3 grp_txt n=Alice drop",
      ".3 advert c=#weather n=Alice drop",".3 grp_txt c=#weather n=[ drop",
      ".3 any route=direct scope=Town", ".3 any route=any retry",
      ".3 any l=1-2 len=2-3 drop", ".3 any pa=AA>BB>CC>DD>EE drop",
      ".3 any", ".3 off", ".0 any drop", ".64 any drop"}) {
    unsigned previous=mesh.saves;set(mesh,invalid,false);assert(mesh.saves==previous&&!mesh.rows[2].active);
  }
  set(mesh,".3 any route=direct drop");assert(mesh.rows[2].extra.route==FloodRuleExtensions::DirectRoute);
  set(mesh,".4 any",true,false);assert(mesh.rows[3].drop_on_match);
}
static void allowAndDirect(){
  MyMesh mesh;
  set(mesh,".1 grp_txt c=#weather n=^Alice$ a pri=2 rt=a");
  set(mesh,".2 grp_txt c=#weather d pri=1 rt=a");
  mesh::Packet packet;packet.header=ROUTE_TYPE_FLOOD|(PAYLOAD_TYPE_GRP_TXT<<PH_TYPE_SHIFT);packet.payload_len=19;
  assert(mesh.rows[0].stop_on_match&&!mesh.rows[0].drop_on_match&&mesh.rows[1].drop_on_match);
  plain("Alice: hello");assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==1);
  plain("Bob: hello");assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==2);
  plain(std::string(140,'x')+"Alice: hello");assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==2);
  packet.header=ROUTE_TYPE_DIRECT|(PAYLOAD_TYPE_GRP_TXT<<PH_TYPE_SHIFT);
  plain("Alice: hello");assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==1);
  plain("Bob: hello");assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==2);
  set(mesh,".2 off");assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==0);
  set(mesh,".2 on");authentic=false;
  assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==0);authentic=true;
  // Existing radio rules keep their flood-only behavior until route is stated.
  MyMesh legacy;set(legacy,".1 any drop");
  assert(legacy.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==0);
  set(legacy,".1 any rt=d drop");assert(legacy.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==1);
}
static void budgetExhaustion(){
  MyMesh mesh;
  for(unsigned slot=1;slot<=63;++slot){
    char command[128];snprintf(command,sizeof(command),".%u grp_txt c=#weather x=^(a|aa)*b$ d",slot);set(mesh,command);
  }
  mesh::Packet packet;packet.header=ROUTE_TYPE_FLOOD|(PAYLOAD_TYPE_GRP_TXT<<PH_TYPE_SHIFT);packet.payload_len=19;
  plain("Alice: "+std::string(160,'a'));
  // A whole-packet work limit returns the reserved suppression bit rather
  // than accidentally permitting a packet after a partial rule evaluation.
  assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==(uint64_t(1)<<63));
}
static void caseInsensitiveSenders(){
  MyMesh mesh;
  set(mesh,R"(.1 grp_txt c=public n="(?i)^(Jeffery Epstein|Ghislaine Maxwell)$" d)");
  assert(mesh.rows[0].drop_on_match);
  mesh::Packet packet;packet.header=ROUTE_TYPE_FLOOD|(PAYLOAD_TYPE_GRP_TXT<<PH_TYPE_SHIFT);packet.payload_len=35;
  for(const char* message:{"Jeffery Epstein: hello","jEfFeRy ePsTeIn: hello","GHISLAINE MAXWELL: hello"}){
    plain(message);assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==1);
  }
  for(const char* message:{"Jeffery Epstein Jr: hello","XGhislaine Maxwell: hello","Alice: Ghislaine Maxwell"}){
    plain(message);assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==0);
  }
  // Removing the opt-in modifier retains the original case-sensitive behavior.
  set(mesh,R"(.1 grp_txt c=public n="^(Jeffery Epstein|Ghislaine Maxwell)$" d)");
  plain("GHISLAINE MAXWELL: hello");assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==0);
  plain("Ghislaine Maxwell: hello");assert(mesh.evaluateFloodPacketFilterMatches(&packet,true,nullptr,0)==1);
}
int main(){parserAndRollback();allowAndDirect();budgetExhaustion();caseInsensitiveSenders();}
'''


class FloodRuleCliTests(unittest.TestCase):
    def test_actual_repeater_parser_and_evaluator(self):
        source = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        header = (ROOT / "examples/simple_repeater/MyMesh.h").read_text()
        parts = [PRELUDE.replace("@ENTRY@", extract_braced(header, "struct FloodPacketFilterEntry"))]
        for marker in ("static const char* skipFloodFilterSpaces(", "static bool floodFilterAsciiEqual(",
                       "static bool floodFilterAsciiStartsWith(", "static bool parseFloodFilterUnsigned(",
                       "static bool parseFloodFilterPayloadType(", "static bool parseFloodFilterHopSpec(",
                       "static bool normalizeFloodFilterScopeName("):
            parts.append(extract_braced(source, marker))
        marker = "static bool parseFloodModerationUnsigned("
        parts.append(extract_braced(source[source.rindex(marker):], marker))
        for marker in ("void MyMesh::setFloodPacketFilter(", "bool MyMesh::floodPacketFilterFieldsMatch(",
                       "bool MyMesh::authenticateFloodPacketFilterChannel(", "uint64_t MyMesh::applyFloodPacketFilterStop(",
                       "uint64_t MyMesh::evaluateFloodPacketFilterMatches("):
            parts.append(extract_braced(source, marker))
        parts.append(SCENARIOS)
        with tempfile.TemporaryDirectory(prefix="flood-rule-cli-") as folder:
            folder = Path(folder)
            (folder / "Utils.h").write_text(UTILS)
            cpp = folder / "cli.cpp"
            cpp.write_text("\n".join(parts))
            binary = folder / "cli"
            flags = []
            if os.name != "nt":
                flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
            subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", *flags,
                            "-I" + str(folder), "-I" + str(ROOT / "src"), str(cpp), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
