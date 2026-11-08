"""Execute Companion's guarded Binary modes and decrypted RX admission offline."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_direct_ota_admin_client import STUB
from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]
COMPANION_SEND_DIRECT = r'''
bool sendDirect(Packet* p,const uint8_t* path,uint8_t length){
  // Companion supplies an explicit selected policy. Dispatcher binds the current
  // profile on admission; the repeater client's pre-bound profile is separate.
  assert(p&&p==last_packet&&p==&packets.back());
  assert(path==nullptr&&length==0&&(p->tx_radio==RADIO_TX_PRIMARY||p->tx_radio==RADIO_TX_SECONDARY));
  if(type==PAYLOAD_TYPE_PATH)sent.assign(p->payload,p->payload+p->payload_len);
  if(send_ok)admitted.push_back(p);
  pending=send_ok;return send_ok;
}
'''
HARNESS = r'''
#include <helpers/CompanionDirectAdminGuard.h>
#define COMPANION_FEATURE_DIRECT_OTA_ADMIN FULL
#define MAX_FRAME_SIZE 512
#define PUSH_CODE_LOG_RX_DATA 0x88
#define CMD_SEND_PATH_DISCOVERY_REQ 52
#define ERR_CODE_NOT_FOUND 1
#define ERR_CODE_BAD_STATE 2
#define ERR_CODE_ILLEGAL_ARG 3
#define ERR_CODE_TABLE_FULL 4
#define MAX_ANON_CONTACTS 8
#define ADV_TYPE_NONE 0
#define ADV_TYPE_REPEATER 2
#define ADV_TYPE_ROOM 3
#define ADV_TYPE_SENSOR 4
#define TXT_TYPE_CLI_DATA 1
#define TXT_TYPE_CLI_COMMAND 3
struct ContactInfo {
  mesh::Identity id;
  uint8_t type=0, out_path_len=0, tx_radio=0, flags=0;
  char name[32]{};
  uint32_t sync_since=0;
  uint8_t out_path[64]{};
  mutable uint8_t secret[32]{};
  const uint8_t* getSharedSecret(const mesh::LocalIdentity&) const {memset(secret,0x44,32);return secret;}
  bool setPath(const uint8_t*,uint8_t length){out_path_len=length;memset(out_path,0,sizeof(out_path));return true;}
};
struct BaseSerialInterface {
  BaseSerialInterface* route=this;BaseSerialInterface* captureReplyRoute(){return route;}
  std::vector<std::vector<uint8_t>> frames;
  bool isConnected()const{return true;}
  void writeFrame(const uint8_t* data,size_t length){frames.emplace_back(data,data+length);}
};
struct Timer { uint32_t now=100;uint32_t getMillis()const{return now;} };
struct BaseChatMesh:mesh::Mesh {
  ContactInfo contact,anonymous[MAX_ANON_CONTACTS];
  int data_calls=0,path_calls=0,login_calls=0,clear_calls=0,lookup_length=0;
  BaseChatMesh(){memset(contact.id.pub_key,0x11,32);contact.type=2;contact.out_path_len=2;contact.out_path[0]=0xAA;
    contact.flags=31;contact.sync_since=987;contact.tx_radio=2;strcpy(contact.name,"Saved contact");}
  ContactInfo* lookupContactByPubKey(const uint8_t* key,int n){
    lookup_length=n;
    for(auto& p:anonymous) if(!memcmp(key,p.id.pub_key,n)) return &p;
    return !memcmp(key,contact.id.pub_key,n)?&contact:nullptr;
  }
  ContactInfo* getContactPtrByIdx(unsigned i){return i<MAX_ANON_CONTACTS?&anonymous[i]:nullptr;}
  bool isTransientContact(const ContactInfo& p){for(auto& a:anonymous)if(&a==&p)return true;return false;}
  void resetContactValue(ContactInfo& c){c=ContactInfo();}
  bool clearTransientContact(ContactInfo& c){assert(isTransientContact(c));resetContactValue(c);++clear_calls;return true;}
  virtual uint8_t getContactTxRadio(const mesh::Identity&)const{return 2;}
  int sendLogin(const ContactInfo&,const char*,uint32_t&);
  int sendContactPacket(const ContactInfo& peer,mesh::Packet* packet,uint32_t& timeout){
    assert(peer.out_path_len==0);timeout=0;
    packet->tx_radio=getContactTxRadio(peer.id);
    return sendDirect(packet,nullptr,peer.out_path_len)?1:0;
  }
  void onPeerDataRecv(mesh::Packet*,uint8_t,int,const uint8_t*,uint8_t*,size_t){++data_calls;}
  bool onPeerPathRecv(mesh::Packet*,int,const uint8_t*,uint8_t*,uint8_t,uint8_t,uint8_t*,uint8_t){++path_calls;return true;}
  void onContactResponse(const ContactInfo&,const uint8_t*,uint8_t){++login_calls;}
};
struct MyMesh:BaseChatMesh {
  Timer timer;Timer* _ms=&timer;
  BaseSerialInterface wire;BaseSerialInterface* _serial=&wire;
  BaseSerialInterface* _ota_direct_usb_route=&wire;
  uint8_t cmd_frame[64]{};int response=-1;
  mesh::CompanionDirectAdminGuard _ota_direct_admin;
  mesh::Radio* _radio=&profile_radio;
  bool _ota_direct_radio_owned=false;uint8_t _ota_direct_radio_profile=0;uint32_t _ota_direct_radio_generation=0;
  bool directOtaRadioEligible(uint8_t)const;
  bool directOtaRadioCurrent()const;
  bool _cli_rescue=false,_cli_line_overflow=false;char cli_command[32]{};
  uint8_t out_frame[MAX_FRAME_SIZE]{};
  void resetUsbHostSessionInput();
  @SET_ROUTE@
  void logRxRaw(float,float,const uint8_t*,int);
  MyMesh(){profile_radio.expose_profiles=true;}
  void releasePacket(mesh::Packet*){}
  ContactInfo _ota_direct_contact;
  ContactInfo* _ota_direct_transient=nullptr;
  ContactInfo* routed=nullptr;
  void writeOKFrame(){response=0;}
  void writeErrFrame(int value){response=value;}
  void dispatch(size_t len){
    response=-1;
#if FULL
    if(handleDirectOtaFrame(len)) return;
#else
    (void)len;
#endif
  }
  bool handleDirectOtaFrame(size_t len);
  bool handleDirectOtaCommand(const char*,uint32_t,char*,size_t);
  void closeDirectOtaAdmin();
  void mode(int value,const mesh::Identity* peer=nullptr,uint8_t type=ADV_TYPE_REPEATER){
    cmd_frame[0]=52;cmd_frame[1]=value;memcpy(cmd_frame+2,(peer?peer->pub_key:contact.id.pub_key),32);
    cmd_frame[34]=type;dispatch(value==1?35:34);
  }
  bool command(const char* command,uint32_t stamp,char* reply){
#if FULL
    return handleDirectOtaCommand(command,stamp,reply,160);
#else
    (void)command;(void)stamp;(void)reply;return false;
#endif
  }
  void expect(const char* prefix){cmd_frame[0]=52;cmd_frame[1]=4;memcpy(cmd_frame+2,contact.id.pub_key,32);memcpy(cmd_frame+34,prefix,3);dispatch(37);assert(response==0);}
  void onPeerDataRecv(mesh::Packet*,uint8_t,int,const uint8_t*,uint8_t*,size_t);
#if FULL
  uint8_t getContactTxRadio(const mesh::Identity&)const;
#else
  uint8_t getContactTxRadio(const mesh::Identity& peer)const override{return BaseChatMesh::getContactTxRadio(peer);}
#endif
  bool onPeerPathRecv(mesh::Packet*,int,const uint8_t*,uint8_t*,uint8_t,uint8_t,uint8_t*,uint8_t);
  void rawRoute(mesh::Packet* pkt){
#if FULL
    @RAW_ROUTE@
#else
    (void)pkt;
#endif
  }
  void cliRoute(uint8_t txt_type,const uint8_t* pub_key_prefix){
#if FULL
    ContactInfo* recipient=&contact;
    @CLI_ROUTE@
    routed=recipient;
#else
    (void)txt_type;(void)pub_key_prefix;
#endif
  }
  void loginRoute(const uint8_t* pub_key){
#if FULL
    ContactInfo* recipient=&contact;
    @LOGIN_ROUTE@
    routed=recipient;
#else
    (void)pub_key;
#endif
  }
};
@CALLBACKS@
@CURRENT@
@RAW_LOG@
@HOST_RESET@
@BINARY@
@SEND_LOGIN@
static void repeaterPrefix(char*& command,char*& reply){@REPEATER_PREFIX@}
static void roomPrefix(char*& command,char*& reply){@ROOM_PREFIX@}
static void sensorPrefix(char*& command,char*& reply){@SENSOR_PREFIX@}
[[maybe_unused]] static void body(uint8_t* data,uint32_t stamp,const char* prefix,const char* text){
  memset(data,0,184);memcpy(data,&stamp,4);data[4]=4;memcpy(data+5,prefix,3);memcpy(data+8,text,strlen(text));
}
int main(){
  MyMesh m;char reply[160]{};
  for(char a='!';a<='~';++a)for(char b='!';b<='~';++b){
    if(a=='|'||b=='|')continue;
    char input[]={a,b,'|','g','e','t',' ','p','u','b','l','i','c','.','k','e','y',0};
    for(auto parser:{repeaterPrefix,roomPrefix,sensorPrefix}){
      char* command=input;char* output=reply;
      parser(command,output);
      assert(!strcmp(command,"get public.key")&&output==reply+3&&!memcmp(reply,input,3));
    }
#if FULL
    assert(m._ota_direct_admin.expect(reinterpret_cast<const uint8_t*>(input),m.timer.now)==false);
#endif
  }
  m.mode(1);
#if FULL
  assert(m.response==0&&m.calls==0); // capability negotiation has no RF
  assert(m.contact.out_path_len==2&&m.contact.out_path[0]==0xAA);
  assert(m.lookup_length==PUB_KEY_SIZE&&m._ota_direct_contact.id.matches(m.contact.id));
  assert(m._ota_direct_contact.out_path_len==0&&m._ota_direct_contact.tx_radio==1);
  assert(m._ota_direct_contact.type==ADV_TYPE_REPEATER&&m._ota_direct_contact.sync_since==0);
  assert(m._ota_direct_contact.flags==0&&m._ota_direct_contact.name[0]==0);
  assert(m.contact.flags==31&&m.contact.sync_since==987&&m.contact.tx_radio==2);
  assert(!strcmp(m.contact.name,"Saved contact"));
  assert(m.getContactTxRadio(m.contact.id)==mesh::RADIO_TX_PRIMARY);
  m.cliRoute(TXT_TYPE_CLI_DATA,m.contact.id.pub_key);assert(m.routed==&m._ota_direct_contact);
  m.cliRoute(TXT_TYPE_CLI_COMMAND,m.contact.id.pub_key);assert(m.routed==&m._ota_direct_contact);
  m.loginRoute(m.contact.id.pub_key);assert(m.routed==&m._ota_direct_contact);
  mesh::Identity another;assert(m.getContactTxRadio(another)==2);
  mesh::Packet ota;ota.payload_type=12;m.rawRoute(&ota);assert(ota.tx_radio==mesh::RADIO_TX_PRIMARY);
  assert(m.command("get ota.direct",0,reply)&&strstr(reply,"v=2 active=1 busy=0"));
  assert(m.command("get ota.direct.capabilities",0,reply)&&strstr(reply,"guarded_cli=1"));
  for(char a='!';a<='~';++a)for(char b='!';b<='~';++b){
    if(a=='|'||b=='|')continue;
    const uint8_t prefix[]={uint8_t(a),uint8_t(b),'|'};
    assert(m._ota_direct_admin.expect(prefix,m.timer.now));
    assert(m.command("get ota.direct.reply 0",0,reply));
    const char* tag=strstr(reply,"req=")+4;assert(tag[0]==a&&tag[1]==b&&tag[2]==' ');
  }
  assert(!m.command("get ota.direct",1,reply));
  BaseSerialInterface foreign;m.wire.route=&foreign;
  m.mode(1);assert(m.response==ERR_CODE_BAD_STATE);
  m.response=-1;m.routed=nullptr;m.cliRoute(TXT_TYPE_CLI_DATA,m.contact.id.pub_key);assert(m.response==ERR_CODE_BAD_STATE&&!m.routed);
  m.response=-1;m.loginRoute(m.contact.id.pub_key);assert(m.response==ERR_CODE_BAD_STATE&&!m.routed);
  assert(m.command("get ota.direct.reply 0",0,reply)&&strstr(reply,"physical USB"));
  m.wire.route=&m.wire;

  uint8_t data[184]{};mesh::Packet packet;packet.radio_generation=m.profile_radio.config.generation[0];packet.direct=false;
  uint8_t secret[32],other[32];memset(secret,0x44,32);memset(other,0x77,32);
  m.onPeerDataRecv(&packet,PAYLOAD_TYPE_RESPONSE,0,secret,data,13);assert(m.login_calls==0);
  m._ota_direct_admin.beginLogin(m.contact.id,m.timer.now);
  uint32_t login_stamp=1000;memcpy(data,&login_stamp,4);data[7]=3;
  m.onPeerDataRecv(&packet,PAYLOAD_TYPE_RESPONSE,0,secret,data,13);assert(m.login_calls==1);
  packet.path_len=1;m.onPeerDataRecv(&packet,PAYLOAD_TYPE_RESPONSE,0,secret,data,13);assert(m.login_calls==1);
  m.onPeerDataRecv(&packet,PAYLOAD_TYPE_RESPONSE,0,other,data,13);assert(m.data_calls==1);
  packet.path_len=0;
  const size_t before_path=m.admitted.size();
  const int before_path_allocations=m.calls;
  m.mode(2);assert(m.response==0&&m.type==PAYLOAD_TYPE_PATH);
  assert(m.calls==before_path_allocations+1&&m.admitted.size()==before_path+1);
  assert(m.admitted.back()==m.last_packet&&m.last_packet==&m.packets.back());
  assert(m.last_packet->tx_radio==mesh::RADIO_TX_PRIMARY);
  assert(m.sent.size()==10&&m.sent[0]==0x11&&m.sent[1]==0x55);
  assert(m.sent[2]==0xCA&&m.sent[3]==0xFE&&m.sent[4]==0&&m.sent[5]==0xFF);
  assert(m.command("get ota.direct",0,reply)&&strstr(reply,"busy=1"));
  m.pending=false;assert(m.command("get ota.direct",0,reply)&&strstr(reply,"busy=0"));
  m.alloc_ok=false;m.mode(2);assert(m.response==ERR_CODE_BAD_STATE);
  assert(m.calls==before_path_allocations+1&&m.admitted.size()==before_path+1);
  m.alloc_ok=true;m.send_ok=false;m.mode(2);assert(m.response==ERR_CODE_BAD_STATE);
  assert(m.calls==before_path_allocations+2&&m.admitted.size()==before_path+1);
  assert(m.last_packet!=m.admitted.back()&&m.last_packet->tx_radio==mesh::RADIO_TX_PRIMARY);
  m.send_ok=true;

  // Exact MAC-bound peer, fresh timestamp, DIRECT0 and command prefix are all
  // required. Ordinary private messages and unmatched CLI replies stay in inbox.
  m.expect("Ab|");
  body(data,1001,"Ab|","reply");
  m.onPeerDataRecv(&packet,PAYLOAD_TYPE_TXT_MSG,0,secret,data,16);
  assert(m.command("get ota.direct.reply 0",0,reply)&&strstr(reply,"state=pending"));
  packet.direct=true;packet.path_len=0x40;
  body(data,999,"Ab|","stale");m.onPeerDataRecv(&packet,PAYLOAD_TYPE_TXT_MSG,0,secret,data,16);
  assert(m.command("get ota.direct.reply 0",0,reply)&&strstr(reply,"state=pending"));
  body(data,1001,"ZZ|","other CLI");m.onPeerDataRecv(&packet,PAYLOAD_TYPE_TXT_MSG,0,secret,data,32);assert(m.data_calls==2);
  body(data,1001,"Ab|","unrelated message");data[4]=0;
  m.onPeerDataRecv(&packet,PAYLOAD_TYPE_TXT_MSG,0,secret,data,32);assert(m.data_calls==3);
  std::string expected(160,'X');body(data,1001,"Ab|",expected.c_str());
  m.onPeerDataRecv(&packet,PAYLOAD_TYPE_TXT_MSG,0,secret,data,176);assert(m.data_calls==3);
  for(unsigned offset: {0U,40U,80U,120U}){
    std::string command="get ota.direct.reply "+std::to_string(offset);
    assert(m.command(command.c_str(),0,reply));
    assert(strstr(reply,"req=Ab state=ready bytes=160"));
    const char* hex=strstr(reply,"data=")+5;assert(strlen(hex)==80);
    for(unsigned i=0;i<80;++i)assert(hex[i]==(i%2?'8':'5'));
  }
  m.onPeerDataRecv(&packet,PAYLOAD_TYPE_TXT_MSG,0,secret,data,176);assert(m.data_calls==3); // duplicate is consumed
  assert(m.command("get ota.direct.reply 161",0,reply)&&strstr(reply,"invalid reply offset"));
  assert(m.command("get ota.direct.reply -1",0,reply)&&strstr(reply,"invalid reply offset"));
  assert(!m.onPeerPathRecv(&packet,0,secret,data,1,0,data,0));assert(m.path_calls==0);
  assert(!m.onPeerPathRecv(&packet,0,secret,data,0,0,data,0));assert(m.path_calls==0);
  assert(m.contact.out_path_len==2&&m.contact.out_path[0]==0xAA);

  // A changed primary profile fences both new commands and OTA raw frames.
  ++m.profile_radio.config.generation[0];m.mode(2);assert(m.response==ERR_CODE_BAD_STATE);
  m.response=-1;m.routed=nullptr;m.cliRoute(TXT_TYPE_CLI_DATA,m.contact.id.pub_key);assert(m.response==ERR_CODE_BAD_STATE&&!m.routed);
  m.response=-1;m.loginRoute(m.contact.id.pub_key);assert(m.response==ERR_CODE_BAD_STATE&&!m.routed);
  m.response=-1;m.rawRoute(&ota);assert(m.response==ERR_CODE_BAD_STATE);
  const int prior_login_calls=m.login_calls;
  m.onPeerDataRecv(&packet,PAYLOAD_TYPE_RESPONSE,0,secret,data,13);assert(m.login_calls==prior_login_calls);
  m.mode(3);assert(m.response==0&&!m._ota_direct_admin.active(m.timer.now));
  assert(m.getContactTxRadio(m.contact.id)==2);
  m.mode(3);assert(m.response==0); // cleanup is idempotent
  m.contact.type=ADV_TYPE_ROOM;
  m.mode(1,nullptr,ADV_TYPE_ROOM);
  assert(m.response==0&&m._ota_direct_contact.type==ADV_TYPE_ROOM);
  assert(m._ota_direct_contact.sync_since==0&&m._ota_direct_contact.id.matches(m.contact.id));
  uint32_t timeout=0;
  const size_t before_login=m.admitted.size();
  const int before_login_allocations=m.calls;
  assert(m.sendLogin(m._ota_direct_contact,"secret",timeout)==1);
  assert(m.type==PAYLOAD_TYPE_ANON_REQ&&m.calls==before_login_allocations+1);
  assert(m.admitted.size()==before_login+1&&m.admitted.back()==m.last_packet);
  assert(m.last_packet==&m.packets.back()&&m.last_packet->tx_radio==mesh::RADIO_TX_PRIMARY);
  assert(m.sent.size()==14&&!memcmp(m.sent.data()+8,"secret",6));
  for(unsigned i=4;i<8;++i)assert(m.sent[i]==0); // Room login requests no historical cursor.
  assert(m.contact.type==ADV_TYPE_ROOM&&m.contact.sync_since==987);
  assert(m.contact.out_path_len==2&&m.contact.out_path[0]==0xAA&&m.contact.tx_radio==2);
  m.mode(3);assert(m.response==0);
  m.mode(1,nullptr,ADV_TYPE_SENSOR);assert(m.response==0&&m._ota_direct_contact.type==ADV_TYPE_SENSOR);
  m.mode(3);assert(m.response==0);
  mesh::Identity fresh;memset(fresh.pub_key,0x22,32);m.mode(1,&fresh);
  assert(m.response==0&&m._ota_direct_transient);
  assert(m._ota_direct_contact.id.matches(fresh)&&m._ota_direct_contact.type==ADV_TYPE_REPEATER);
  assert(m._ota_direct_contact.out_path_len==0&&m._ota_direct_contact.tx_radio==mesh::RADIO_TX_PRIMARY);
  assert(m._ota_direct_transient->type==ADV_TYPE_NONE&&m.contact.out_path_len==2);
  m.mode(3,&fresh);assert(m.response==0&&m.clear_calls==1);
  for(auto& slot:m.anonymous){memset(slot.id.pub_key,0x33,32);slot.type=ADV_TYPE_NONE;}
  m.mode(1,&fresh);assert(m.response==ERR_CODE_TABLE_FULL); // never evict occupied transient requests
  for(auto& slot:m.anonymous)assert(slot.id.pub_key[0]==0x33);

  // Profile selection is a USB-owned RAM setting. Merely enabling radio2 is
  // insufficient: it must be a live temporary MeshCore RXTX profile.
  MyMesh secondary;
  assert(secondary.command("get ota.direct.radio",0,reply));
  assert(!strcmp(reply,"> ota.direct.radio v=1 profile=radio"));
  assert(!secondary.command("set ota.direct.radio radio2",1,reply));
  secondary.wire.route=&foreign;
  assert(secondary.command("set ota.direct.radio radio2",0,reply)&&strstr(reply,"physical USB"));
  assert(secondary._ota_direct_radio_profile==0);secondary.wire.route=&secondary.wire;
  assert(secondary.command("set ota.direct.radio radio3",0,reply)&&strstr(reply,"ERR use"));
  auto& profiles=secondary.profile_radio.config;
  profiles.secondary.mode=mesh::RadioProfileMode::RxTx;
  assert(secondary.command("set ota.direct.radio radio2",0,reply)&&strstr(reply,"ERR"));
  profiles.secondary_temporary=true;profiles.secondary.mode=mesh::RadioProfileMode::Rx;
  assert(secondary.command("set ota.direct.radio radio2",0,reply)&&strstr(reply,"ERR"));
  profiles.secondary.mode=mesh::RadioProfileMode::RxTx;
  profiles.secondary.params.protocol=mesh::RadioProfileProtocol::Meshtext;
  assert(secondary.command("set ota.direct.radio radio2",0,reply)&&strstr(reply,"ERR"));
  profiles.secondary.params.protocol=mesh::RadioProfileProtocol::MeshCore;
  secondary.pending=true;
  assert(secondary.command("set ota.direct.radio radio2",0,reply)&&strstr(reply,"idle"));
  secondary.pending=false;
  assert(secondary.command("set ota.direct.radio radio2",0,reply)&&!strcmp(reply,"OK - ota.direct.radio radio2"));
  assert(secondary.command("get ota.direct.radio",0,reply)&&!strcmp(reply,"> ota.direct.radio v=1 profile=radio2"));
  assert(secondary.command("get ota.direct.capabilities",0,reply)&&!strcmp(reply,"> ota.direct.cap guarded_cli=1 guarded_radio=1"));
  secondary.mode(1);assert(secondary.response==0&&secondary.directOtaRadioCurrent());
  assert(secondary._ota_direct_contact.tx_radio==mesh::RADIO_TX_SECONDARY);
  assert(secondary.getContactTxRadio(secondary.contact.id)==mesh::RADIO_TX_SECONDARY);
  assert(secondary.command("set ota.direct.radio radio",0,reply)&&strstr(reply,"idle"));
  assert(secondary._ota_direct_radio_profile==1);
  secondary.cliRoute(TXT_TYPE_CLI_DATA,secondary.contact.id.pub_key);assert(secondary.routed==&secondary._ota_direct_contact);
  secondary.loginRoute(secondary.contact.id.pub_key);assert(secondary.routed==&secondary._ota_direct_contact);
  const int before_invalid_policy=secondary.calls;
  for(uint8_t invalid:{uint8_t(0),uint8_t(mesh::RADIO_TX_BOTH),uint8_t(255)})
    assert(!secondary._ota_direct_admin.sendReturnPath(secondary,secondary.contact.id,secondary.timer.now,invalid));
  assert(secondary.calls==before_invalid_policy&&!secondary.pending);
  secondary.mode(2);assert(secondary.response==0&&secondary.last_packet->tx_radio==mesh::RADIO_TX_SECONDARY);
  assert(secondary.last_packet->radio_profile==1&&secondary.last_packet->radio_origin==1);
  secondary.pending=false;
  mesh::Packet secondary_ota;secondary_ota.payload_type=12;
  secondary.rawRoute(&secondary_ota);
  assert(secondary_ota.tx_radio==mesh::RADIO_TX_SECONDARY&&secondary_ota.radio_profile==1&&secondary_ota.radio_origin==1);
  // Changes to the normal channel do not substitute for the owned channel.
  ++profiles.generation[0];assert(secondary.directOtaRadioCurrent());

  mesh::Packet secondary_rx;secondary_rx.radio_profile=0;secondary_rx.radio_generation=profiles.generation[0];
  secondary._ota_direct_admin.beginLogin(secondary.contact.id,secondary.timer.now);
  memset(data,0,sizeof(data));login_stamp=2000;memcpy(data,&login_stamp,4);data[7]=3;
  secondary.onPeerDataRecv(&secondary_rx,PAYLOAD_TYPE_RESPONSE,0,secret,data,13);
  assert(secondary.login_calls==0);
  assert(!secondary.onPeerPathRecv(&secondary_rx,0,secret,data,0,PAYLOAD_TYPE_RESPONSE,data,13));
  assert(secondary.login_calls==0);
  secondary_rx.radio_profile=1;secondary_rx.radio_generation=profiles.generation[1]+1;
  secondary.onPeerDataRecv(&secondary_rx,PAYLOAD_TYPE_RESPONSE,0,secret,data,13);assert(secondary.login_calls==0);
  secondary_rx.radio_generation=profiles.generation[1];
  secondary.onPeerDataRecv(&secondary_rx,PAYLOAD_TYPE_RESPONSE,0,secret,data,13);assert(secondary.login_calls==1);

  // Preserve the existing 0x88 envelope, but do not expose wrong-profile or
  // stale-generation OTA requests to the selected USB seeder.
  const uint8_t raw_ota[]={0x32,0,1},raw_other[]={0x0A,0,1};
  secondary.profile_radio.rx_profile=1;secondary.profile_radio.rx_generation=profiles.generation[1];
  secondary.logRxRaw(2,-90,raw_ota,3);assert(secondary.wire.frames.size()==1);
  assert(secondary.wire.frames.back()==std::vector<uint8_t>({0x88,8,uint8_t(-90),0x32,0,1}));
  secondary.profile_radio.rx_profile=0;
  secondary.logRxRaw(2,-90,raw_ota,3);assert(secondary.wire.frames.size()==1);
  secondary.logRxRaw(2,-90,raw_other,3);assert(secondary.wire.frames.size()==2);
  secondary.profile_radio.rx_profile=1;++secondary.profile_radio.rx_generation;
  secondary.logRxRaw(2,-90,raw_ota,3);assert(secondary.wire.frames.size()==2);

  ++profiles.generation[1];secondary.response=-1;secondary_ota.tx_radio=0;
  secondary.rawRoute(&secondary_ota);assert(secondary.response==ERR_CODE_BAD_STATE&&secondary_ota.tx_radio==0);
  secondary.mode(3);assert(secondary.response==0&&secondary._ota_direct_radio_profile==1);
  secondary.response=-1;secondary.rawRoute(&secondary_ota);
  assert(secondary.response==ERR_CODE_BAD_STATE&&secondary_ota.tx_radio==0); // no legacy AUTO/primary fallback after close
  profiles.secondary_temporary=false;secondary.mode(1);assert(secondary.response==ERR_CODE_BAD_STATE);
  profiles.secondary_temporary=true;secondary.mode(1);assert(secondary.response==0);
  secondary.resetUsbHostSessionInput();
  assert(!secondary._ota_direct_radio_owned&&secondary._ota_direct_radio_profile==0);
  assert(secondary.command("set ota.direct.radio radio2",0,reply)&&strstr(reply,"OK"));
  secondary.setDirectAdminUsbRoute(nullptr);assert(secondary._ota_direct_radio_profile==0&&!secondary._ota_direct_usb_route);
#else
  assert(m.response==-1&&m.calls==0);
  assert(!m.command("get ota.direct",0,reply));
#endif
}
'''



class CompanionDirectAdminGuardTest(unittest.TestCase):
    def test_guard_and_production_companion_wiring(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        binary = extract_braced(source, "bool MyMesh::handleDirectOtaFrame(")
        current = extract_braced(source, "bool MyMesh::directOtaRadioCurrent(")
        eligible = extract_braced(source, "bool MyMesh::directOtaRadioEligible(")
        getter = extract_braced(source, "bool MyMesh::handleDirectOtaCommand(")
        closer = extract_braced(source, "void MyMesh::closeDirectOtaAdmin(")
        raw_log = extract_braced(source, "void MyMesh::logRxRaw(")
        host_reset = extract_braced(source, "void MyMesh::resetUsbHostSessionInput(")
        set_route = extract_braced((ROOT / "examples/companion_radio/MyMesh.h").read_text(), "void setDirectAdminUsbRoute(")
        base = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text()
        send_login = extract_braced(base, "int BaseChatMesh::sendLogin(")
        callbacks = "\n".join(extract_braced(source, signature) for signature in (
            "uint8_t MyMesh::getContactTxRadio(", "void MyMesh::onPeerDataRecv(", "bool MyMesh::onPeerPathRecv("))
        raw = extract_braced(source, "if (pkt->getPayloadType() == PAYLOAD_TYPE_OTA\n            && (_ota_direct_radio_owned || _ota_direct_radio_profile != 0))")
        cli_route = extract_braced(source, 'if ((txt_type == TXT_TYPE_CLI_DATA || txt_type == TXT_TYPE_CLI_COMMAND)')
        login_route = extract_braced(source, 'if (_ota_direct_radio_owned && !memcmp(_ota_direct_contact.id.pub_key, pub_key, PUB_KEY_SIZE))')
        self.assertIn("_ota_direct_admin.beginLogin(recipient->id", source)
        self.assertIn("if (handleDirectOtaCommand(command, sender_timestamp, reply, reply_capacity))", source)
        self.assertIn("closeDirectOtaAdmin();", extract_braced(source, "void MyMesh::resetUsbHostSessionInput("))
        self.assertIn("setDirectAdminUsbRoute(&usb_serial_interface)", (ROOT / "examples/companion_radio/main.cpp").read_text())
        self.assertIn("recipient = &_ota_direct_contact", source)
        self.assertIn("(txt_type == TXT_TYPE_CLI_DATA || txt_type == TXT_TYPE_CLI_COMMAND)",source)
        self.assertIn("|| !_ota_direct_admin.active(_ms->getMillis()) || !directOtaRadioCurrent()",source)
        self.assertIn("if (handleDirectOtaFrame(len)) return;", source)
        self.assertIn("&& (!COMPANION_FEATURE_DIRECT_OTA_ADMIN || cmd_frame[1] == 0))", source)
        mesh = (ROOT / "src/Mesh.cpp").read_text()
        start = mesh.index("Packet* Mesh::createPathReturn(const uint8_t* dest_hash,")
        end = mesh.index("\nPacket* Mesh::createDatagram(", start)
        stub = STUB.replace("@PATH_BUILDER@", mesh[start:end])
        stub = stub.replace(extract_braced(stub, "bool sendDirect("), COMPANION_SEND_DIRECT)
        stub = stub.replace("#define PAYLOAD_TYPE_PATH 8", "#define PAYLOAD_TYPE_PATH 8\n#define PAYLOAD_TYPE_OTA 12")
        stub = stub.replace("RadioProfiles config;bool expose_profiles=false;", """
          RadioProfiles config;bool expose_profiles=false;
          uint8_t rx_profile=0;uint32_t rx_generation=1;
          uint8_t receiveProfile()const{return rx_profile;}
          uint32_t receiveProfileGeneration()const{return rx_generation;}
        """)
        stub = stub.replace("bool direct=true;", "uint8_t payload_type=0;uint8_t getPayloadType()const{return payload_type;}\n  bool direct=true;")
        prefix_blocks = []
        for board in ("simple_repeater", "simple_room_server"):
            server = (ROOT / "examples" / board / "MyMesh.cpp").read_text()
            prefix_blocks.append(extract_braced(server, "if (strlen(command) > 4 && command[2] == '|') {"))
        sensor = (ROOT / 'examples/simple_sensor/SensorMesh.cpp').read_text()
        sensor_prefix = extract_braced(sensor, "if (strlen(command) > 4 && command[2] == '|') {")
        self.assertIn('RESP_SERVER_LOGIN_OK',extract_braced(sensor, 'uint8_t SensorMesh::handleLoginReq('))
        self.assertIn('TXT_TYPE_CLI_DATA || flags == TXT_TYPE_CLI_COMMAND',extract_braced(sensor, 'void SensorMesh::onPeerDataRecv('))
        self.assertIn('applyReceivedClientPath',extract_braced(sensor, 'bool SensorMesh::onPeerPathRecv('))
        self.assertIn('mesh::ota::handle_ota_command', (ROOT / 'src/helpers/CommonCLI.cpp').read_text())
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-direct-") as folder:
            folder = Path(folder)
            (folder / "Mesh.h").write_text(stub)
            for full in (0, 1):
                code = HARNESS.replace("@BINARY@", (binary + "\n" + getter + "\n" + closer) if full else "")
                code = code.replace("@CURRENT@", (eligible + "\n" + current) if full else "")
                code = code.replace("@RAW_LOG@", raw_log if full else "")
                code = code.replace("@HOST_RESET@", host_reset if full else "")
                code = code.replace("@SET_ROUTE@", set_route)
                code = code.replace("@RAW_ROUTE@", raw)
                code = code.replace("@CLI_ROUTE@", cli_route)
                code = code.replace("@LOGIN_ROUTE@", login_route)
                code = code.replace("@CALLBACKS@", callbacks if full else "")
                code = code.replace("@SEND_LOGIN@", send_login if full else "")
                code = code.replace("@REPEATER_PREFIX@", prefix_blocks[0])
                code = code.replace("@ROOM_PREFIX@", prefix_blocks[1])
                code = code.replace("@SENSOR_PREFIX@", sensor_prefix)
                (folder / "test.cpp").write_text(code)
                executable = folder / "guard.exe"
                sanitize = (["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
                            if sys.platform.startswith("linux") else [])
                result = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                         *sanitize, f"-DFULL={full}", "-I" + str(folder), "-I" + str(ROOT / "src"),
                                         str(folder / "test.cpp"), "-o", str(executable)],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

                result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_direct_helper_defaults_preserve_existing_rak_full_features(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ preprocessor is required")
        full = ["COMPANION_RADIO_FULL=1", "ENABLE_OTA=1", "ENABLE_USB_INTERFACE=1", "BLE_PIN_CODE=123456"]
        cases = (("esp32", full + ["ESP32_PLATFORM=1", "WIFI_SSID=fixture"], 1, 1),
                 ("nrf52", full + ["NRF52_PLATFORM=1"], 1, 1),
                 ("rak4631", full + ["NRF52_PLATFORM=1", "RAK_4631=1"], 1, 1),
                 ("reduced", ["NRF52_PLATFORM=1", "RAK_4631=1"], 0, 0),
                 ("rak opt-out", full + ["NRF52_PLATFORM=1", "RAK_4631=1", "COMPANION_FEATURE_DIRECT_OTA_ADMIN=0"], 0, 1))
        code = ('#include "examples/companion_radio/CompanionFeatures.h"\n'
                'DIRECT_HELPER COMPANION_FEATURE_DIRECT_OTA_ADMIN\n'
                'EXISTING_OTA COMPANION_FEATURE_OTA_CLI\n'
                'EXISTING_TEMP COMPANION_FEATURE_TEMP_RADIO\n')
        for name, flags, direct, existing in cases:
            with self.subTest(profile=name):
                result = subprocess.run([compiler, "-E", "-P", "-x", "c++", "-I" + str(ROOT),
                                         "-I" + str(ROOT / "src"), *["-D" + flag for flag in flags], "-"],
                                        input=code, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"DIRECT_HELPER {direct}", result.stdout)
                self.assertIn(f"EXISTING_OTA {existing}", result.stdout)
                self.assertIn(f"EXISTING_TEMP {existing}", result.stdout)


if __name__ == "__main__":
    unittest.main()
