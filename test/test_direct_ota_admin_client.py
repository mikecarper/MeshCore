"""Execute the USB repeater admin client's production state machine."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

STUB = r'''
#pragma once
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#define PUB_KEY_SIZE 32
#define PAYLOAD_TYPE_ANON_REQ 7
#define PAYLOAD_TYPE_TXT_MSG 2
#define PAYLOAD_TYPE_RESPONSE 1
#define PAYLOAD_TYPE_PATH 8
#define PATH_HASH_SIZE 1
#define MAX_COMBINED_PATH 64
#define MAX_PACKET_PAYLOAD 184
#define PH_TYPE_SHIFT 2
#define MESH_DEBUG_PRINTLN(...) do {} while(0)
namespace mesh {
enum { RADIO_TX_PRIMARY=1 };
struct Identity {
  uint8_t pub_key[32]{};
  Identity()=default;
  explicit Identity(const uint8_t* key){memcpy(pub_key,key,32);}
  bool matches(const Identity& o) const{return !memcmp(pub_key,o.pub_key,32);}
  bool isHashMatch(const uint8_t* hash) const{return pub_key[0]==hash[0];}
  size_t copyHashTo(uint8_t* out)const{*out=pub_key[0];return 1;}
};
struct LocalIdentity:Identity {
  bool invalid_peer=false;
  void calcSharedSecret(uint8_t* dest,const Identity& id)const{
    uint8_t nonzero=0;for(auto b:id.pub_key)nonzero|=b;
    for(int i=0;i<32;++i)dest[i]=invalid_peer||!nonzero?0:pub_key[i]^id.pub_key[i];
  }
};
struct Utils {
  static int digit(char c){return c>='0'&&c<='9'?c-'0':c>='A'&&c<='F'?c-'A'+10:c>='a'&&c<='f'?c-'a'+10:-1;}
  static bool fromHex(uint8_t* out,size_t n,const char* text){
    if(strlen(text)!=n*2)return false;
    for(size_t i=0;i<n;++i){int a=digit(text[i*2]),b=digit(text[i*2+1]);if(a<0||b<0)return false;out[i]=a*16+b;}
    return true;
  }
  static void toHex(char* out,const uint8_t* in,size_t n){const char* h="0123456789ABCDEF";for(size_t i=0;i<n;++i){out[i*2]=h[in[i]>>4];out[i*2+1]=h[in[i]&15];}out[n*2]=0;}
  static size_t encryptThenMAC(const uint8_t* secret,uint8_t* out,const uint8_t* plain,size_t length){assert(secret[0]==0x44);out[0]=0xCA;out[1]=0xFE;memcpy(out+2,plain,length);return length+2;}
};
struct Packet {
  uint8_t payload[184]{};
  size_t payload_len=0,path_len=0;
  int tx_radio=0;int16_t _rssi=-95;int8_t _snr=20;
  bool direct=true;
  bool isRouteDirect()const{return direct;}
  bool isRouteFlood()const{return !direct;}
  size_t getPathHashCount()const{return path_len&63;}
  uint8_t header=0;
};
struct Clock {uint32_t now=10000;uint32_t getCurrentTimeUnique(){return ++now;}};
struct Random {void random(uint8_t* out,size_t size){for(size_t i=0;i<size;++i)out[i]=uint8_t(33+i);}};
struct Mesh {
  LocalIdentity self_id;
  Clock clock;Random rng;Packet packet;
  std::vector<uint8_t> sent;
  int type=-1,calls=0;bool alloc_ok=true,send_ok=true,pending=false;
  Mesh(){memset(self_id.pub_key,0x55,32);}
  Clock* getRTCClock(){return &clock;}
  Random* getRNG(){return &rng;}
  Packet* make(int t,const uint8_t* data,size_t length){
    if(!alloc_ok)return nullptr;
    ++calls;type=t;sent.assign(data,data+length);packet=Packet();return &packet;
  }
  Packet* createDatagram(int t,const Identity&,const uint8_t*,const uint8_t* d,size_t n){return make(t,d,n);}
  Packet* createAnonDatagram(int t,const LocalIdentity&,const Identity&,const uint8_t*,const uint8_t* d,size_t n){return make(t,d,n);}
  Packet* createControlData(const uint8_t* d,size_t n){return make(11,d,n);}
  Packet* obtainNewPacket(){if(!alloc_ok)return nullptr;++calls;type=PAYLOAD_TYPE_PATH;packet=Packet();return &packet;}
  Packet* createPathReturn(const uint8_t*,const uint8_t*,const uint8_t*,uint8_t,uint8_t,const uint8_t*,size_t);
  Packet* createPathReturn(const Identity& dest,const uint8_t* secret,const uint8_t* path,uint8_t length,uint8_t type,const uint8_t* extra,size_t size){uint8_t hash[1];dest.copyHashTo(hash);return createPathReturn(hash,secret,path,length,type,extra,size);}
  bool sendDirect(Packet* p,const uint8_t* path,uint8_t length){assert(p&&path==nullptr&&length==0&&p->tx_radio==RADIO_TX_PRIMARY);if(type==PAYLOAD_TYPE_PATH)sent.assign(p->payload,p->payload+p->payload_len);pending=send_ok;return send_ok;}
protected:
  bool hasPendingRadioWork()const{return pending;}
};
@PATH_BUILDER@
}
'''

HARNESS = r'''
#include <helpers/DirectOtaAdminClient.h>
using Client=mesh::DirectOtaAdminClient;
static uint32_t millis(){return 100;}
struct UsbRole:mesh::Mesh {
  Client direct_ota_admin;
  void loop(){ @SERVICE_CALL@ }
  void dispatch(uint32_t sender_timestamp,void* sender,char* command,char* reply,bool usb_origin){
    @USB_GATE@
    strcpy(reply,"not a peer command");
  }
};
static std::string echoInput(const std::string& wire){
  struct Console {
    const std::string& input;std::string output;size_t cursor=0;
    bool available()const{return cursor<input.size();}
    char read(){return input[cursor++];}
    void print(char value){output+=value;}
  } console{wire,{}};
  constexpr size_t LOCAL_SERIAL_COMMAND_MAX=90;
  char command[LOCAL_SERIAL_COMMAND_MAX+2]{};
  int len=0;bool usb_ready=true,command_sensitive=false,command_overflow=false;
  bool line_complete=false,overlong_line_complete=false;
  size_t read_budget=256;
  auto console_available=[&](){return console.available();};
  auto console_read=[&](){return static_cast<int>(console.read());};
  @ECHO_LOOP@
  assert(line_complete||overlong_line_complete);
  return console.output;
}
static const std::string key(64,'1');
static std::string command(Client& client,mesh::Mesh& radio,const std::string& text,uint32_t now=100){
  char output[161];memset(output,0x5A,sizeof(output));
  client.handleCommand(radio,text.c_str(),output,160,now);
  assert(output[160]==0x5A&&strlen(output)<160);
  return output;
}
static std::vector<uint8_t> reply(uint32_t timestamp,uint8_t kind,const std::string& text){
  std::vector<uint8_t> data(5+text.size());memcpy(data.data(),&timestamp,4);data[4]=kind<<2;
  memcpy(data.data()+5,text.data(),text.size());return data;
}
static void deliver(Client& c,mesh::Mesh& radio,mesh::Packet& packet,uint32_t tag,const std::string& text,uint32_t now=200){
  auto data=reply(tag,1,text);c.onData(radio,&packet,PAYLOAD_TYPE_TXT_MSG,data.data(),data.size(),now);
}
static void discover(Client& c,mesh::Mesh& radio,uint32_t now,uint8_t type=2){
  command(c,radio,"ota.peer discover",now);
  mesh::Packet packet;packet.payload_len=38;packet.payload[0]=0x90|type;
  memcpy(packet.payload+2,radio.sent.data()+2,4);memset(packet.payload+6,0x11,32);
  c.onDiscovery(&packet,now+1);c.tick(now+6000);
}
static void authenticate(Client& c,mesh::Mesh& radio,uint32_t now=100,
                         bool known_repeater=false,const std::string& password="s p ace "){
  auto accepted=command(c,radio,std::string("ota.peer ")+(known_repeater?"login.repeater ":"login ")+key+" "+password,now);
  assert(accepted.find("state=pending")!=std::string::npos);
  assert(radio.type==PAYLOAD_TYPE_ANON_REQ);
  assert(std::string(radio.sent.begin()+4,radio.sent.end())==password);
  assert(c.hasSession()&&c.matchesHash(reinterpret_cast<const uint8_t*>("\x11")));
  uint8_t login[13]{};uint32_t tag=20000;memcpy(login,&tag,4);login[7]=3;
  mesh::Packet packet;
  packet.direct=false;packet.path_len=1;c.onData(radio,&packet,PAYLOAD_TYPE_RESPONSE,login,sizeof(login),now+1);
  assert(radio.type==PAYLOAD_TYPE_ANON_REQ);
  packet.direct=true;packet.path_len=1;c.onData(radio,&packet,PAYLOAD_TYPE_RESPONSE,login,sizeof(login),now+2);
  assert(radio.type==PAYLOAD_TYPE_ANON_REQ);
  packet.direct=false;packet.path_len=0x40;c.onData(radio,&packet,PAYLOAD_TYPE_RESPONSE,login,sizeof(login),now+3);
  assert(radio.type==PAYLOAD_TYPE_PATH);
  // Execute Mesh.cpp's real PATH builder: destination/source hashes followed
  // by authenticated ciphertext containing empty return path + dummy payload.
  assert(radio.sent.size()==10&&radio.sent[0]==0x11&&radio.sent[1]==0x55);
  assert(radio.sent[2]==0xCA&&radio.sent[3]==0xFE&&radio.sent[4]==0&&radio.sent[5]==0xFF);
  c.service(radio,now+3,!radio.pending);assert(radio.type==PAYLOAD_TYPE_PATH); // do not overtake the PATH
  radio.pending=false;c.service(radio,now+4,!radio.pending);
  assert(radio.type==PAYLOAD_TYPE_TXT_MSG&&radio.sent[4]==12);
  std::string proof(radio.sent.begin()+5,radio.sent.begin()+8);
  assert(std::string(radio.sent.begin()+8,radio.sent.end())=="get public.key");
  deliver(c,radio,packet,20001,proof+"> "+key,now+4); // flood proof is never accepted
  assert(command(c,radio,"ota.peer result",now+5).find("state=pending")!=std::string::npos);
  packet.direct=true;deliver(c,radio,packet,20001,"ZZ|> "+key,now+5);
  deliver(c,radio,packet,tag,proof+"> "+key,now+6); // stale server timestamp
  assert(command(c,radio,"ota.peer result",now+7).find("state=pending")!=std::string::npos);
  deliver(c,radio,packet,20001,proof+"> "+key,now+8);
  assert(command(c,radio,"ota.peer status",now+9).find("session=1 busy=0")!=std::string::npos);
  assert(command(c,radio,"ota.peer result",now+10).find("state=ok")!=std::string::npos);
  assert(!c.matchesHash(reinterpret_cast<const uint8_t*>("\x11"))); // do not steal ACL packets when idle
}
int main(){
  UsbRole role;char gate_command[]="ota.peer status",gate_reply[160];
  role.dispatch(0,nullptr,gate_command,gate_reply,false);assert(!strcmp(gate_reply,"ERR ota.peer requires physical USB"));
  role.dispatch(1,nullptr,gate_command,gate_reply,true);assert(!strcmp(gate_reply,"ERR ota.peer requires physical USB"));
  role.dispatch(0,&role,gate_command,gate_reply,true);assert(!strcmp(gate_reply,"ERR ota.peer requires physical USB"));
  role.dispatch(0,nullptr,gate_command,gate_reply,true);assert(!strcmp(gate_reply,"OTApeer v=1 session=0 busy=0 peers=0"));
  Client client;mesh::Mesh radio;mesh::Packet packet;
  assert(command(client,radio,"ota.peer status")=="OTApeer v=1 session=0 busy=0 peers=0");
  assert(command(client,radio,"ota.peer capabilities")=="OTApeer known_repeater=1");
  assert(command(client,radio,"ota.peer cmd "+key+" ver")=="ERR peer login required");
  assert(command(client,radio,"ota.peer login "+key+" password")=="ERR peer needs fresh discovery");
  command(client,radio,"ota.peer discover");assert(radio.type==11&&radio.sent[0]==0x80&&radio.sent[1]==12);
  packet.payload_len=38;packet.payload[0]=0x92;
  memcpy(packet.payload+2,radio.sent.data()+2,4);memset(packet.payload+6,0x11,32);
  packet.path_len=1;client.onDiscovery(&packet,101);assert(command(client,radio,"ota.peer peers")=="ERR peer not found");
  packet.path_len=0x80;packet.direct=false;client.onDiscovery(&packet,102);
  packet.direct=true;packet.payload[2]^=1;client.onDiscovery(&packet,103);
  packet.payload[2]^=1;client.onDiscovery(&packet,104);client.onDiscovery(&packet,105);
  assert(command(client,radio,"ota.peer status").find("peers=1")!=std::string::npos);
  assert(command(client,radio,"ota.peer peers 0").find("key="+key)!=std::string::npos);
  client.tick(6100);assert(command(client,radio,"ota.peer result",6101).find("state=ok")!=std::string::npos);
  authenticate(client,radio,6200);
  assert(command(client,radio,"ota.peer cmd "+std::string(64,'2')+" ver",6300)=="ERR peer login required");
  command(client,radio,"ota.peer cmd "+key+" ota status",6400);
  std::string prefix(radio.sent.begin()+5,radio.sent.begin()+8);
  auto original=command(client,radio,"ota.peer result",6401);
  assert(command(client,radio,"ota.peer discover",6402)=="ERR peer request pending");
  packet=mesh::Packet();packet.path_len=2;
  deliver(client,radio,packet,30000,prefix+std::string(160,'A'),6403);
  assert(command(client,radio,"ota.peer result",6404).find("state=pending")!=std::string::npos);
  packet.path_len=0;deliver(client,radio,packet,30000,prefix+std::string(160,'A'),6405);
  for(unsigned off:{0U,48U,96U,144U}){
    auto page=command(client,radio,"ota.peer result "+std::to_string(off),6406);
    assert(page.find("state=ok bytes=160 off="+std::to_string(off))!=std::string::npos);
    auto hex=page.substr(page.find("data=")+5);
    const unsigned expected=off==144?16:48;
    assert(hex.size()==expected*2);
    for(unsigned i=0;i<expected;++i)assert(hex.substr(i*2,2)=="41");
  }
  assert(command(client,radio,"ota.peer result 161",6407)=="ERR peer result offset");
  auto retained=command(client,radio,"ota.peer result",6408);
  deliver(client,radio,packet,30001,prefix+"unsolicited",6409);
  assert(command(client,radio,"ota.peer result",6410)==retained);
  command(client,radio,"ota.peer cmd "+key+" ver",6500);
  assert(command(client,radio,"ota.peer result",36500).find("state=error")!=std::string::npos);
  command(client,radio,"ota.peer close",36600);
  uint8_t secret[32];memset(secret,0xEE,32);client.sharedSecret(secret);
  for(auto b:secret)assert(b==0);
  assert(!client.hasSession());
  Client timeout;discover(timeout,radio,10);command(timeout,radio,"ota.peer login "+key+" password",6011);
  timeout.tick(36011);assert(!timeout.hasSession());
  assert(command(timeout,radio,"ota.peer result",36012).find("state=error")!=std::string::npos);
  Client denied;discover(denied,radio,10);command(denied,radio,"ota.peer login "+key+" password",6011);
  uint8_t bad[13]{};uint32_t tag=50000;memcpy(bad,&tag,4);bad[7]=4;
  denied.onData(radio,&packet,PAYLOAD_TYPE_RESPONSE,bad,sizeof(bad),6012);
  assert(!denied.hasSession());
  assert(command(denied,radio,"ota.peer result",6013).find("state=error")!=std::string::npos);
  Client failed;discover(failed,radio,10);radio.alloc_ok=false;command(failed,radio,"ota.peer login "+key+" password",6011);
  assert(!failed.hasSession()&&command(failed,radio,"ota.peer result",6012).find("state=error")!=std::string::npos);
  char input[256];strcpy(input,("ota.peer login "+key+" ").c_str());
  assert(Client::sensitiveInput(input));
  Client::clearInput(input,sizeof(input));for(char b:input)assert(b==0);
  assert(!Client::sensitiveInput(("ota.peer login "+key).c_str()));
  radio.alloc_ok=true;radio.self_id.invalid_peer=true;Client invalid;discover(invalid,radio,10);
  const int before=radio.calls;
  assert(command(invalid,radio,"ota.peer login "+key+" password",6011)=="ERR peer invalid public key");
  assert(radio.calls==before&&!invalid.hasSession());
  auto echoed=echoInput("ota.peer login "+key+" secret with spaces that overflows the input buffer\r");
  assert(echoed.find("secret")==std::string::npos&&echoed.find("overflows")==std::string::npos);
  assert(echoed.find(std::string(10,'*'))!=std::string::npos);
  radio.self_id.invalid_peer=false;Client room;discover(room,radio,100,3);
  assert(command(room,radio,"ota.peer peers 0",6101).find("type=3")!=std::string::npos);
  assert(command(room,radio,"ota.peer login "+key+" password",6102).find("state=pending")!=std::string::npos);
  assert(radio.sent.size()==16&&radio.sent[4]==0&&radio.sent[5]==0&&radio.sent[6]==0&&radio.sent[7]==0);
  assert(std::string(radio.sent.begin()+8,radio.sent.end())=="password");
  command(room,radio,"ota.peer close",6103);discover(room,radio,7000,3);
  assert(command(room,radio,"ota.peer login "+key+" password",67002)=="ERR peer needs fresh discovery");
  Client retries;discover(retries,radio,10);command(retries,radio,"ota.peer login "+key+" password",6011);
  mesh::Packet direct;uint8_t accepted_login[13]{};uint32_t stamp=60000;memcpy(accepted_login,&stamp,4);accepted_login[7]=3;
  retries.onData(radio,&direct,PAYLOAD_TYPE_RESPONSE,accepted_login,sizeof(accepted_login),6012);
  radio.pending=false;retries.service(radio,6013,!radio.pending);assert(radio.type==PAYLOAD_TYPE_TXT_MSG);
  for(uint32_t now:{14013U,22014U}){radio.pending=false;retries.service(radio,now,!radio.pending);assert(radio.type==PAYLOAD_TYPE_PATH);radio.pending=false;retries.service(radio,now+1,!radio.pending);}
  const int attempts=radio.calls;retries.service(radio,30015,!radio.pending);assert(radio.calls==attempts);
  retries.tick(36011);assert(!retries.hasSession());
  // Execute the production MyMesh loop call with Dispatcher-like protection.
  // Only the derived mesh may inspect its protected radio-work state.
  UsbRole integration;discover(integration.direct_ota_admin,integration,10);
  command(integration.direct_ota_admin,integration,"ota.peer login "+key+" password",6011);
  integration.direct_ota_admin.onData(integration,&direct,PAYLOAD_TYPE_RESPONSE,accepted_login,sizeof(accepted_login),6012);
  integration.loop();assert(integration.type==PAYLOAD_TYPE_PATH);
  integration.pending=false;integration.loop();assert(integration.type==PAYLOAD_TYPE_TXT_MSG);

  // Only an explicit repeater choice may bypass discovery. It still completes
  // the same ECDH/admin/PATH/exact-key proof, including an empty ACL password.
  Client known;mesh::Mesh known_radio;
  authenticate(known,known_radio,100,true,"");
  assert(command(known,known_radio,"ota.peer status",120)=="OTApeer v=1 session=1 busy=0 peers=0");
  assert(command(known,known_radio,"ota.peer capabilities",120)=="OTApeer known_repeater=1");
  command(known,known_radio,"ota.peer cmd "+key+" ota status",121);
  assert(known_radio.type==PAYLOAD_TYPE_TXT_MSG);
  Client undiscovered;mesh::Mesh unchecked;
  assert(command(undiscovered,unchecked,"ota.peer login "+key+" ")=="ERR peer needs fresh discovery");
  assert(unchecked.calls==0&&!undiscovered.hasSession());
  assert(command(undiscovered,unchecked,"ota.peer login.repeaterx "+key+" password").find("ERR use ota.peer ")==0);
  assert(command(undiscovered,unchecked,"ota.peer cmd "+key+" ver")=="ERR peer login required");
  assert(unchecked.calls==0&&!undiscovered.hasSession());

  // Full keys and the password separator are required, even for an empty
  // password. Reject malformed/self/invalid ECDH keys before any RF queueing.
  for(const auto& arguments:std::vector<std::string>{" password",std::string(62,'1')+" password",
      std::string(64,'G')+" password",key+"1 password",key}){
    assert(command(undiscovered,unchecked,"ota.peer login.repeater "+arguments)=="ERR peer key or arguments");
    assert(unchecked.calls==0&&!undiscovered.hasSession());
  }
  assert(command(undiscovered,unchecked,"ota.peer login.repeater "+std::string(64,'5')+" ")=="ERR peer cannot be this repeater");
  assert(command(undiscovered,unchecked,"ota.peer login.repeater "+std::string(64,'0')+" ")=="ERR peer invalid public key");
  unchecked.self_id.invalid_peer=true;
  assert(command(undiscovered,unchecked,"ota.peer login.repeater "+key+" ")=="ERR peer invalid public key");
  assert(unchecked.calls==0&&!undiscovered.hasSession());

  for(uint8_t permission:{0,1,2,4,5}){
    Client nonadmin;mesh::Mesh r;
    command(nonadmin,r,"ota.peer login.repeater "+key+" ",100);
    uint8_t response[13]{};uint32_t timestamp=70000;memcpy(response,&timestamp,4);response[7]=permission;
    nonadmin.onData(r,&direct,PAYLOAD_TYPE_RESPONSE,response,sizeof(response),101);
    assert(!nonadmin.hasSession()&&r.calls==1);
    assert(command(nonadmin,r,"ota.peer result",102).find("state=error")!=std::string::npos);
    assert(command(nonadmin,r,"ota.peer cmd "+key+" ota install",103)=="ERR peer login required");
  }
  for(const std::string& proof:std::vector<std::string>{key.substr(0,16),key.substr(0,63)+"2"}){
    Client mismatch;mesh::Mesh r;
    command(mismatch,r,"ota.peer login.repeater "+key+" ",100);
    uint8_t response[13]{};uint32_t timestamp=80000;memcpy(response,&timestamp,4);response[7]=3;
    mismatch.onData(r,&direct,PAYLOAD_TYPE_RESPONSE,response,sizeof(response),101);
    assert(command(mismatch,r,"ota.peer status",102).find("session=0 busy=1")!=std::string::npos);
    r.pending=false;mismatch.service(r,103,true);
    const std::string proof_prefix(r.sent.begin()+5,r.sent.begin()+8);
    deliver(mismatch,r,direct,80001,proof_prefix+"> "+proof,104);
    assert(!mismatch.hasSession());
    assert(command(mismatch,r,"ota.peer result",105).find("state=error")!=std::string::npos);
    assert(command(mismatch,r,"ota.peer cmd "+key+" ota install",106)=="ERR peer login required");
  }
  assert(Client::sensitiveInput(("ota.peer login.repeater "+key+" ").c_str()));
  assert(!Client::sensitiveInput(("ota.peer login.repeater "+key).c_str()));
  auto known_echo=echoInput("ota.peer login.repeater "+key+" secret that overflows the input buffer\r");
  assert(known_echo.find("secret")==std::string::npos&&known_echo.find('*')!=std::string::npos);
}
'''


class DirectOtaAdminClientTest(unittest.TestCase):
    def test_direct_admin_session_guards_and_paginated_results(self):
        source = (ROOT/'examples/simple_repeater/main.cpp').read_text()
        start = source.index('while (usb_ready && read_budget-- > 0 && console_available())')
        opening = source.index('{',start)
        end,depth=opening+1,1
        while depth:
            depth += (source[end]=='{') - (source[end]=='}')
            end += 1
        code = HARNESS.replace('@ECHO_LOOP@',source[start:end])
        mesh_source = (ROOT/'examples/simple_repeater/MyMesh.cpp').read_text()
        start = mesh_source.index('direct_ota_admin.service(')
        end = mesh_source.index(';',start)+1
        code = code.replace('@SERVICE_CALL@', mesh_source[start:end])
        start = mesh_source.index('if (mesh::DirectOtaAdminClient::isCommand(command))')
        opening = mesh_source.index('{',start)
        end,depth=opening+1,1
        while depth:
            depth += (mesh_source[end]=='{') - (mesh_source[end]=='}')
            end += 1
        code = code.replace('@USB_GATE@',mesh_source[start:end])
        with tempfile.TemporaryDirectory(prefix='meshcore-ota-peer-') as folder:
            root = Path(folder)
            mesh_cpp = (ROOT/'src/Mesh.cpp').read_text()
            start = mesh_cpp.index('Packet* Mesh::createPathReturn(const uint8_t* dest_hash,')
            end = mesh_cpp.index('\nPacket* Mesh::createDatagram(', start)
            (root/'Mesh.h').write_text(STUB.replace('@PATH_BUILDER@', mesh_cpp[start:end]))
            (root/'test.cpp').write_text(code)
            sanitizers = [] if os.name == 'nt' else [
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
            binary = root/('test.exe' if os.name == 'nt' else 'test')
            result = subprocess.run([
                'g++','-std=c++17','-Wall','-Wextra','-Werror',
                *sanitizers, '-DMESH_ENABLE_DIRECT_OTA_ADMIN=1','-I'+str(root),'-I'+str(ROOT/'src'),
                str(root/'test.cpp'),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result = subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
