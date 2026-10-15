"""Exercise production Companion media/SSH routing without radios or flash writes."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method


ROOT = Path(__file__).resolve().parents[1]


HEADER_HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <vector>
#include <helpers/CompanionTrafficRouting.h>

int main() {
 using namespace mesh;
 using namespace mesh::companion;
 for (const auto kind : {TrafficKind::Media, TrafficKind::Ssh}) {
   const char* name=kind==TrafficKind::Media ? "media" : "ssh";
   char text[96];
   snprintf(text,sizeof(text),"get tx.%s",name);
   auto parsed=parseTrafficRouteCommand(text);
   assert(parsed.matched && parsed.valid && !parsed.set && parsed.kind==kind);
   for (uint8_t policy=RADIO_TX_AUTO; policy<=RADIO_TX_OFF; ++policy) {
     snprintf(text,sizeof(text),"set tx.%s %s",name,radioTxPolicyName(policy));
     parsed=parseTrafficRouteCommand(text);
     assert(parsed.matched && parsed.valid && parsed.set && parsed.kind==kind && parsed.policy==policy);
   }
   for (const char* value : {"", "radio3", "RADIO", "radio trailing", "-1", "auto both"}) {
     snprintf(text,sizeof(text),"set tx.%s %s",name,value);
     parsed=parseTrafficRouteCommand(text);
     assert(parsed.matched && !parsed.valid);
   }
   snprintf(text,sizeof(text),"get tx.%s extra",name);
   parsed=parseTrafficRouteCommand(text);
   assert(parsed.matched && !parsed.valid);
 }
 for (const char* text : {"get tx.mediax", "set tx.sshx radio", "get tx.user Bob", "get tx.channel 0", "set tx.radio both", ""})
   assert(!parseTrafficRouteCommand(text).matched);
 assert(!parseTrafficRouteCommand(nullptr).matched);

 assert(rawTrafficKind(nullptr,0)==TrafficKind::Other);
 assert(rawTrafficKind(nullptr,20)==TrafficKind::Other);
 const uint8_t normal[]={0x56,0,1,2,3,0,99};
 const uint8_t salted[]={0x57,0,1,2,3,0,99,42};
 assert(rawTrafficKind(normal,sizeof(normal))==TrafficKind::Media);
 assert(rawTrafficKind(salted,sizeof(salted))==TrafficKind::Media);
 for (size_t n=0;n<sizeof(normal);++n) assert(rawTrafficKind(normal,n)==TrafficKind::Other);
 for (size_t n=0;n<sizeof(salted);++n) assert(rawTrafficKind(salted,n)==TrafficKind::Other);
 // A complete binary media fetch is media, including optional missing indices
 // and reverse-path hint. A truncated packet cannot commandeer a TX policy.
 const uint8_t fetch_all[]={0x72,0,1,2,3,0,1,2,3,4,5,6,0};
 const uint8_t fetch_missing[]={0x72,0,1,2,3,3,1,2,3,4,5,6,2,0,17};
 const uint8_t fetch_path[]={0x72,0,1,2,3,7,1,2,3,4,5,6,2,0,17,2,11,12};
 for (const auto* data : {fetch_all,fetch_missing,fetch_path}) {
   const size_t n=data==fetch_all ? sizeof(fetch_all) : data==fetch_missing ? sizeof(fetch_missing) : sizeof(fetch_path);
   assert(rawTrafficKind(data,n)==TrafficKind::Media);
 }
 for (size_t n=0;n<sizeof(fetch_all);++n) assert(rawTrafficKind(fetch_all,n)==TrafficKind::Other);
 assert(rawTrafficKind(fetch_missing,sizeof(fetch_missing)-1)==TrafficKind::Other);
 assert(rawTrafficKind(fetch_path,sizeof(fetch_path)-1)==TrafficKind::Other);
 std::vector<uint8_t> reserved(fetch_all,fetch_all+sizeof(fetch_all));
 reserved[5]=0x80;
 assert(rawTrafficKind(reserved.data(),reserved.size())==TrafficKind::Other);

 const uint8_t ssh[]={0x53,0x53,0x48,0x01,0x99};
 assert(rawTrafficKind(ssh,sizeof(ssh))==TrafficKind::Ssh);
 for (size_t n=0;n<sizeof(ssh);++n) assert(rawTrafficKind(ssh,n)==TrafficKind::Other);
 for (unsigned i=0;i<4;++i) {
   uint8_t unknown[sizeof(ssh)]; memcpy(unknown,ssh,sizeof(ssh)); unknown[i]^=0x20;
   assert(rawTrafficKind(unknown,sizeof(unknown))==TrafficKind::Other);
 }
 const uint8_t opaque[]={0x9f,0x5c,0x53,0x53,0x48,0x01,0x11};
 const uint8_t ssh_banner[]={'S','S','H','-','2','.','0'};
 const uint8_t ipv4[]={0x45,0,0,20,0,0,0,0,64,6,0,0,10,0,0,1,10,0,0,2};
 assert(rawTrafficKind(opaque,sizeof(opaque))==TrafficKind::Other);
 assert(rawTrafficKind(ssh_banner,sizeof(ssh_banner))==TrafficKind::Other);
 assert(rawTrafficKind(ipv4,sizeof(ipv4))==TrafficKind::Other);
 assert(groupTrafficKind(0x5643)==TrafficKind::Media);
 assert(groupTrafficKind(0x5353)==TrafficKind::Ssh);
 for (uint16_t type : {0,1,0x5654,0x5642,0x5352,0xffff}) assert(groupTrafficKind(type)==TrafficKind::Other);
 for (uint8_t media=0;media<=4;++media) for (uint8_t ssh_policy=0;ssh_policy<=4;++ssh_policy) {
   assert(trafficPolicy(TrafficKind::Other,media,ssh_policy)==RADIO_TX_AUTO);
   assert(trafficPolicy(TrafficKind::Media,media,ssh_policy)==media);
   assert(trafficPolicy(TrafficKind::Ssh,media,ssh_policy)==ssh_policy);
 }

 RadioProfiles profiles;
 profiles.cross=RadioCrossMode::Off;
 for (const auto mode : {RadioProfileMode::Off,RadioProfileMode::Rx,RadioProfileMode::RxTx}) {
   profiles.secondary.mode=mode;
   const bool secondary=mode==RadioProfileMode::RxTx;
   assert(trafficTransmitMask(RADIO_TX_AUTO,&profiles)==1);
   assert(trafficTransmitMask(RADIO_TX_PRIMARY,&profiles)==1);
   assert(trafficTransmitMask(RADIO_TX_SECONDARY,&profiles)==(secondary ? 2 : 0));
   assert(trafficTransmitMask(RADIO_TX_BOTH,&profiles)==(secondary ? 3 : 1));
   assert(trafficTransmitMask(RADIO_TX_OFF,&profiles)==0);
 }
 profiles.secondary.mode=RadioProfileMode::RxTx;
 profiles.cross=RadioCrossMode::On;
 assert(trafficTransmitMask(RADIO_TX_AUTO,&profiles)==3);
 profiles.secondary.params.protocol=RadioProfileProtocol::Meshtext;
 assert(trafficTransmitMask(RADIO_TX_SECONDARY,&profiles)==0);
 assert(trafficTransmitMask(RADIO_TX_BOTH,&profiles)==1);
 assert(trafficTransmitMask(RADIO_TX_AUTO,&profiles)==1);
 profiles.primary.protocol=RadioProfileProtocol::Meshtext;
 assert(trafficTransmitMask(RADIO_TX_PRIMARY,&profiles)==0);
 assert(trafficTransmitMask(RADIO_TX_BOTH,&profiles)==0);
 assert(trafficTransmitMask(RADIO_TX_AUTO,&profiles)==0);
 for (uint8_t policy=0;policy<=4;++policy)
   assert(trafficTransmitMask(policy,nullptr)==((policy==RADIO_TX_AUTO || policy==RADIO_TX_PRIMARY || policy==RADIO_TX_BOTH) ? 1 : 0));
}
'''


METHOD_HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <vector>
#include <helpers/CompanionTrafficRouting.h>
#define OUT_PATH_UNKNOWN 0xff
#define MAX_GROUP_DATA_LENGTH 163
namespace mesh {
struct GroupChannel { uint8_t secret[32]={},hash[1]={},tx_radio=RADIO_TX_AUTO; };
struct Packet { uint8_t tx_radio=RADIO_TX_AUTO; };
}
struct Radio {
 mesh::RadioProfiles config;
 bool available=true;
 mesh::RadioProfiles* profiles() { return available ? &config : nullptr; }
};
struct Prefs { uint8_t tx_media=mesh::RADIO_TX_AUTO,tx_ssh=mesh::RADIO_TX_AUTO; };
class MyMesh {
public:
 Prefs _prefs,saved;
 Radio radio; Radio* _radio=&radio;
 unsigned writes=0,group_calls=0,raw_calls=0,direct_calls=0;
 uint8_t last_policy=0,last_path_len=0;
 uint16_t last_type=0;
 std::vector<uint8_t> last_data;
 mesh::Packet raw_packet;
 bool save_ok=true,allocate_ok=true,send_ok=true;
 bool savePrefs() { ++writes; if (!save_ok) return false; saved=_prefs; return true; }
 template<typename T> bool savePreference(T& field,T value) {
   const T before=field; field=value; if (savePrefs()) return true; field=before; return false;
 }
 mesh::Packet* createRawData(const uint8_t* data,size_t len) {
   ++raw_calls; if (!allocate_ok) return nullptr; raw_packet.tx_radio=mesh::RADIO_TX_AUTO;
   last_data.assign(data,data+len); return &raw_packet;
 }
 bool sendDirect(mesh::Packet* packet,uint8_t*,uint8_t n) {
   ++direct_calls; last_policy=packet->tx_radio; last_path_len=n;
   return send_ok && mesh::companion::trafficTransmitMask(last_policy,_radio->profiles());
 }
 bool sendGroupData(mesh::GroupChannel& channel,uint8_t*,uint8_t n,uint16_t type,const uint8_t* data,int len) {
   ++group_calls; last_policy=channel.tx_radio; last_type=type; last_path_len=n;
   if (len<0 || len>MAX_GROUP_DATA_LENGTH) return false;
   last_data.assign(data,data+len);
   return send_ok && mesh::companion::trafficTransmitMask(channel.tx_radio,_radio->profiles());
 }
 bool handleTrafficRoutingCommand(const char*,char*,size_t);
 bool sendClientGroupData(mesh::GroupChannel&,uint8_t*,uint8_t,uint16_t,const uint8_t*,int);
 bool sendClientRawData(const uint8_t*,size_t,uint8_t*,uint8_t);
};
@METHODS@

int main() {
 using namespace mesh;
 MyMesh node;
 node.radio.config.secondary.mode=RadioProfileMode::RxTx;
 node.radio.config.cross=RadioCrossMode::Off;
 char reply[160];
 auto cmd=[&](const char* text) { memset(reply,0,sizeof(reply)); assert(node.handleTrafficRoutingCommand(text,reply,sizeof(reply))); };
 assert(!node.handleTrafficRoutingCommand("get tx.user Bob",reply,sizeof(reply)));
 for (const char* name : {"media","ssh"}) {
   char text[64];
   snprintf(text,sizeof(text),"get tx.%s",name); cmd(text);
   assert(strstr(reply,"auto") && strstr(reply,"active=channel/global"));
   for (uint8_t policy=0;policy<=4;++policy) {
     snprintf(text,sizeof(text),"set tx.%s %s",name,radioTxPolicyName(policy)); cmd(text);
     assert(!strncmp(reply,"OK",2));
     assert((!strcmp(name,"media") ? node._prefs.tx_media : node._prefs.tx_ssh)==policy);
   }
 }
 assert(node.saved.tx_media==RADIO_TX_OFF && node.saved.tx_ssh==RADIO_TX_OFF);
 const Prefs durable=node.saved;
 const auto previous_writes=node.writes;
 for (const char* text : {"set tx.media", "set tx.ssh radio3", "get tx.ssh extra"}) {
   cmd(text); assert(!strncmp(reply,"Error",5));
   assert(node._prefs.tx_media==durable.tx_media && node._prefs.tx_ssh==durable.tx_ssh);
 }
 assert(node.writes==previous_writes);
 node.save_ok=false;
 // Even setting the current value must attempt a durable save; otherwise a
 // boot following a failed flash-time preference write can report false OK.
 cmd("set tx.media off"); assert(!strncmp(reply,"Error",5) && node.writes==previous_writes+1);
 cmd("set tx.media both"); assert(!strncmp(reply,"Error",5));
 assert(node._prefs.tx_media==RADIO_TX_OFF && node.saved.tx_media==durable.tx_media);
 cmd("set tx.ssh radio2"); assert(!strncmp(reply,"Error",5));
 assert(node._prefs.tx_ssh==RADIO_TX_OFF && node.saved.tx_ssh==durable.tx_ssh);
 node.save_ok=true;
 cmd("set tx.media radio2");
 node.radio.config.secondary.mode=RadioProfileMode::Rx;
 cmd("get tx.media"); assert(strstr(reply,"unavailable") && strstr(reply,"active=off"));
 node.radio.config.secondary.mode=RadioProfileMode::RxTx;
 node.radio.config.secondary.params.protocol=RadioProfileProtocol::Meshtext;
 cmd("get tx.media"); assert(strstr(reply,"unavailable") && strstr(reply,"active=off"));
 node.radio.config.secondary.params.protocol=RadioProfileProtocol::MeshCore;
 cmd("set tx.media auto"); cmd("set tx.ssh auto");
 const uint8_t media[]={0x56,0,1,2,3,0,99};
 const uint8_t ssh[]={'S','S','H',1,42};
 const uint8_t unknown[]={1,2,3,4,5,6,7};
 uint8_t path[]={0x11,0x22};
 GroupChannel channel;
 channel.tx_radio=RADIO_TX_SECONDARY;
 channel.secret[0]=0x42;
 const auto original=channel;
 for (uint8_t policy=0;policy<=4;++policy) {
   node._prefs.tx_media=policy; node._prefs.tx_ssh=RADIO_TX_BOTH;
   bool result=node.sendClientGroupData(channel,path,2,0x5643,media,sizeof(media));
   const uint8_t expected=policy==RADIO_TX_AUTO ? original.tx_radio : policy;
   assert(result==(expected!=RADIO_TX_OFF));
   if (result) { assert(node.last_policy==expected && node.last_type==0x5643 && node.last_path_len==2); }
   assert(!memcmp(&channel,&original,sizeof(channel)));
   result=node.sendClientRawData(media,sizeof(media),path,2);
   assert(result==(policy!=RADIO_TX_OFF));
   if (result) assert(node.last_policy==policy && node.last_data==std::vector<uint8_t>(media,media+sizeof(media)));
 }
 node._prefs.tx_media=RADIO_TX_OFF;
 for (uint8_t policy=0;policy<=4;++policy) {
   node._prefs.tx_ssh=policy;
   bool result=node.sendClientGroupData(channel,path,2,0x5353,ssh,sizeof(ssh));
   const uint8_t expected=policy==RADIO_TX_AUTO ? original.tx_radio : policy;
   assert(result==(expected!=RADIO_TX_OFF));
   if (result) assert(node.last_policy==expected);
   result=node.sendClientRawData(ssh,sizeof(ssh),path,2);
   assert(result==(policy!=RADIO_TX_OFF));
   if (result) assert(node.last_policy==policy && node.last_data==std::vector<uint8_t>(ssh,ssh+sizeof(ssh)));
   assert(!memcmp(&channel,&original,sizeof(channel)));
 }
 node._prefs.tx_media=node._prefs.tx_ssh=RADIO_TX_OFF;
 assert(node.sendClientGroupData(channel,path,2,0x5654,unknown,sizeof(unknown)));
 assert(node.last_policy==original.tx_radio && node.last_type==0x5654);
 assert(node.sendClientRawData(unknown,sizeof(unknown),path,2));
 assert(node.last_policy==RADIO_TX_AUTO);
 node._prefs.tx_media=RADIO_TX_SECONDARY;
 node.radio.config.secondary.mode=RadioProfileMode::Off;
 assert(!node.sendClientRawData(media,sizeof(media),path,2));
 assert(!node.sendClientGroupData(channel,path,2,0x5643,media,sizeof(media)));
 node.radio.config.secondary.mode=RadioProfileMode::Rx;
 assert(!node.sendClientRawData(media,sizeof(media),path,2));
 node.radio.config.secondary.mode=RadioProfileMode::RxTx;
 node.radio.config.secondary.params.protocol=RadioProfileProtocol::Meshtext;
 assert(!node.sendClientRawData(media,sizeof(media),path,2));
 node.radio.config.secondary.params.protocol=RadioProfileProtocol::MeshCore;
 node.allocate_ok=false;
 assert(!node.sendClientRawData(media,sizeof(media),path,2));
 node.allocate_ok=true; node.send_ok=false;
 assert(!node.sendClientRawData(media,sizeof(media),path,2));
 assert(!node.sendClientGroupData(channel,path,2,0x5643,media,sizeof(media)));
 assert(!node.sendClientRawData(nullptr,20,path,2));
 assert(!node.sendClientRawData(media,3,path,2));
 assert(!node.sendClientGroupData(channel,path,2,0x5643,media,-1));
 assert(!node.sendClientGroupData(channel,path,2,0x5643,media,MAX_GROUP_DATA_LENGTH+1));
 assert(!node.sendClientGroupData(channel,path,2,0x5643,nullptr,1));
 assert(!node.handleTrafficRoutingCommand("get tx.media",nullptr,1));
 assert(!node.handleTrafficRoutingCommand("get tx.media",reply,0));
 // Reply formatting never writes past the caller's advertised capacity.
 node.send_ok=true;
 for (size_t cap=1;cap<12;++cap) {
   char bounded[16]; memset(bounded,0x6b,sizeof(bounded));
   assert(node.handleTrafficRoutingCommand("get tx.media",bounded,cap));
   assert(bounded[cap-1]==0);
   for (size_t i=cap;i<sizeof(bounded);++i) assert(bounded[i]==0x6b);
 }
}
'''


RAW_FRAME_HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <memory>
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-parameter"
#include <Packet.h>
#pragma GCC diagnostic pop
@CONSTANTS@
namespace mesh {
@PACKET_METHODS@
}
class MyMesh {
public:
 unsigned ok=0,errors=0,sends=0;
 uint8_t error=0;
 bool send_ok=true;
 const uint8_t* frame=nullptr;
 size_t frame_length=0;
 void writeOKFrame() { ++ok; }
 void writeErrFrame(uint8_t value) { ++errors; error=value; }
 bool sendClientRawData(const uint8_t* data,size_t n,uint8_t* path,uint8_t encoded) {
   ++sends;
   const size_t bytes=(encoded & 63)*((encoded >> 6)+1);
   assert(frame_length>=2+bytes+4);
   assert(data==frame+2+bytes && n==frame_length-2-bytes);
   assert(!memcmp(path,frame+2,bytes));
   return send_ok;
 }
 void onFrame(uint8_t* cmd_frame,size_t len) {
   @RAW_CLAUSE@
   else writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
 }
};

int main() {
 // Every buffer is allocated to its exact advertised frame length. Reading
 // even one byte past the received frame is therefore an ASan failure.
 for (size_t length=6;length<=80;++length) for (unsigned encoded=0;encoded<256;++encoded) {
   auto frame=std::unique_ptr<uint8_t[]>(new uint8_t[length]);
   for (size_t i=0;i<length;++i) frame[i]=static_cast<uint8_t>(i*29+encoded);
   frame[0]=CMD_SEND_RAW_DATA;
   frame[1]=static_cast<uint8_t>(encoded);
   const unsigned width=(encoded >> 6)+1;
   const size_t bytes=(encoded & 63)*width;
   const bool valid=width!=4 && bytes<=MAX_PATH_SIZE;
   const bool fits=length>=2+bytes+4;
   for (const bool result : {false,true}) {
     MyMesh node;
     node.send_ok=result; node.frame=frame.get(); node.frame_length=length;
     node.onFrame(frame.get(),length);
     if (!valid) {
       assert(node.errors==1 && node.error==ERR_CODE_UNSUPPORTED_CMD && !node.sends && !node.ok);
     } else if (!fits) {
       assert(node.errors==1 && node.error==ERR_CODE_ILLEGAL_ARG && !node.sends && !node.ok);
     } else {
       assert(node.sends==1);
       if (result) assert(node.ok==1 && !node.errors);
       else assert(!node.ok && node.errors==1 && node.error==ERR_CODE_BAD_STATE);
     }
   }
 }
}
'''


class CompanionTrafficRoutingTest(unittest.TestCase):
    def compile_and_run(self, source):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            cpp = work / 'test.cpp'
            cpp.write_text(source)
            build = subprocess.run([
                'g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-include', 'cstdio', '-fsanitize=address,undefined',
                '-fno-pie', '-no-pie', '-I', str(ROOT / 'src'),
                str(cpp), '-o', str(work / 'test'),
            ], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(work / 'test')], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_classification_parser_and_profile_permissions(self):
        self.compile_and_run(HEADER_HARNESS)

    def test_real_cli_and_client_sends(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        methods = '\n'.join(method(source, signature) for signature in (
            'bool MyMesh::handleTrafficRoutingCommand(',
            'bool MyMesh::sendClientGroupData(',
            'bool MyMesh::sendClientRawData(',
        ))
        self.compile_and_run(METHOD_HARNESS.replace('@METHODS@', methods))

    def test_binary_and_local_control_paths_use_routing(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        local = method(source, 'bool MyMesh::handleLocalControlCommand(')
        self.assertIn('handleTrafficRoutingCommand(command, reply, reply_size)', local)
        raw_start = source.index('cmd_frame[0] == CMD_SEND_RAW_DATA')
        raw_end = source.index('} else if (cmd_frame[0] == CMD_SEND_LOGIN', raw_start)
        self.assertIn('sendClientRawData(', source[raw_start:raw_end])
        self.assertNotIn('createRawData(', source[raw_start:raw_end])
        group_start = source.index('cmd_frame[0] == CMD_SEND_CHANNEL_DATA')
        group_end = source.index('} else if (cmd_frame[0] == CMD_GET_CONTACTS', group_start)
        self.assertIn('sendClientGroupData(', source[group_start:group_end])

    def test_real_raw_frame_path_bounds_and_failed_send_response(self):
        source = (ROOT / 'examples/companion_radio/MyMesh.cpp').read_text()
        packet = (ROOT / 'src/Packet.cpp').read_text()
        clause = method(source, '} else if (cmd_frame[0] == CMD_SEND_RAW_DATA')
        # The extracted clause is still production code: strip only the
        # previous command's closing brace and its `else` connective.
        clause = clause.removeprefix('} else ')
        names = ('CMD_SEND_RAW_DATA', 'ERR_CODE_UNSUPPORTED_CMD',
                 'ERR_CODE_ILLEGAL_ARG', 'ERR_CODE_BAD_STATE')
        constants = '\n'.join(re.search(
            r'^#define\s+' + name + r'\s+\d+.*$', source, re.MULTILINE).group(0)
            for name in names)
        packet_methods = '\n'.join(method(packet, signature) for signature in (
            'bool Packet::isValidPathLen(', 'size_t Packet::writePath(',
        ))
        self.compile_and_run(RAW_FRAME_HARNESS.replace('@CONSTANTS@', constants)
                             .replace('@PACKET_METHODS@', packet_methods)
                             .replace('@RAW_CLAUSE@', clause))


if __name__ == '__main__':
    unittest.main()
