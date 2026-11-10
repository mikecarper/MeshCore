"""Actual fixed-RAM observer and Companion raw callback/CLI/parser integration."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from cpp_source import body
from test_upstream_observer_recipes import Recipes
from test_t114_autodisplay_recipes import defines

ROOT=Path(__file__).resolve().parents[1]
FLAGS=('-DESP32_PLATFORM=1','-DCOMPANION_RADIO_FULL=1')
COMMON=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cmath>
#include <initializer_list>
#include <helpers/RadioProbeObserver.h>
inline void packet(uint8_t* raw,unsigned seq=0) {
  raw[0]=0x3e;raw[1]=0;memcpy(raw+2,"SPLIT1",6);
  const uint8_t nonce[]={0x01,0x23,0x45,0x67,0x89,0xab,0xcd,0xef};memcpy(raw+8,nonce,8);
  raw[16]='L';for(unsigned i=0;i<4;++i)raw[17+i]=uint8_t(seq>>(8*i));
  for(unsigned i=0;i<234;++i)raw[21+i]=uint8_t(i);
}
inline void cli(mesh::RadioProbeObserver& observer,const char* command,char* reply,bool local=true) {
  assert(observer.handle(command,reply,160,local));assert(strlen(reply)<157);
}
inline void arm(mesh::RadioProbeObserver& observer,char* reply) {
  cli(observer,"set radio.probe 0123456789ABCDEF",reply);assert(strstr(reply,"armed"));
}
'''


class Observer(unittest.TestCase):
    def native(self,source,flags=FLAGS):
        with tempfile.TemporaryDirectory()as temp:
            source_path=Path(temp)/'test.cpp';binary=Path(temp)/'test';source_path.write_text(source)
            build=subprocess.run([os.environ.get('CXX','g++'),'-std=c++11','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined','-fno-pie','-no-pie',*flags,
                '-isystem',str(ROOT/'test/mocks'),'-I'+str(ROOT/'src'),str(source_path),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)

    def test_default_off_local_only_nonce_reset_and_paged_metadata(self):
        self.native(COMMON+r'''
int main(){
 mesh::RadioProbeObserver o;static_assert(sizeof(o)==552,"fixed native RAM shape");char reply[160];uint8_t raw[256];packet(raw);
 o.observe(raw,255,-110.75f,-7.75f,1);cli(o,"get radio.probe",reply);assert(strstr(reply,"active=0")&&strstr(reply,"unique=0"));
 cli(o,"set radio.probe 0123456789abcdef",reply,false);assert(strstr(reply,"local only"));
 arm(o,reply);o.observe(raw,255,-110.75f,-7.75f,1);
 cli(o,"get radio.probe",reply);assert(strstr(reply,"unique=1")&&strstr(reply,"scope=phy255,app=0"));
 cli(o,"get radio.probe.page 0",reply);assert(strstr(reply,"nonce=0123456789abcdef,start=0,count=8,valid=01,records=92ffe101"));
 o.observe(raw,255,-50,10,0);cli(o,"get radio.probe",reply);assert(strstr(reply,"unique=1,duplicates=1,invalid=0"));
 cli(o,"get radio.probe.page 0",reply);assert(strstr(reply,"records=92ffe101"));
 packet(raw,127);o.observe(raw,255,-33000,40,7);cli(o,"get radio.probe.page 15",reply);
 assert(strstr(reply,"start=120,count=8,valid=80")&&strstr(reply,"00807fff"));
 cli(o,"set radio.probe off",reply);packet(raw,1);o.observe(raw,255,-10,1,0);
 cli(o,"get radio.probe",reply);assert(strstr(reply,"active=0")&&strstr(reply,"unique=2"));
 arm(o,reply);cli(o,"get radio.probe",reply);assert(strstr(reply,"unique=0,duplicates=0,invalid=0"));
 mesh::RadioProbeObserver reboot;cli(reboot,"get radio.probe",reply);assert(strstr(reply,"active=0"));
}
''')

    def test_every_byte_lengths_sequence_nonce_and_metadata_are_validated_before_credit(self):
        self.native(COMMON+r'''
int main(){
 mesh::RadioProbeObserver o;char reply[160];uint8_t raw[256];arm(o,reply);unsigned invalid=0;
 for(unsigned i=0;i<255;++i){packet(raw);raw[i]^=1;if(i==17)raw[17]=128;o.observe(raw,255,-90,1,0);if(i<8||i>=16)++invalid;}
 packet(raw);o.observe(raw,254,-90,1,0);++invalid;o.observe(raw,256,-90,1,0);++invalid;
 o.observe(raw,15,-90,1,0);o.observe(nullptr,255,-90,1,0);
 packet(raw,128);o.observe(raw,255,-90,1,0);++invalid;
 packet(raw);o.observe(raw,255,NAN,1,0);++invalid;o.observe(raw,255,-90,INFINITY,0);++invalid;
 cli(o,"get radio.probe",reply);char count[60];snprintf(count,sizeof(count),"unique=0,duplicates=0,invalid=%u",invalid);assert(strstr(reply,count));
 for(unsigned seq=0;seq<128;++seq){packet(raw,seq);o.observe(raw,255,-90,1,0);}
 cli(o,"get radio.probe",reply);assert(strstr(reply,"unique=128"));
 for(unsigned page=0;page<16;++page){char cmd[50];snprintf(cmd,sizeof(cmd),"get radio.probe.page %u",page);cli(o,cmd,reply);assert(strstr(reply,"valid=ff"));}
 packet(raw);raw[254]^=1;o.observe(raw,255,-90,1,0);cli(o,"get radio.probe",reply);assert(strstr(reply,"duplicates=0"));
}
''')

    def test_cli_rejects_remote_getters_bad_inputs_and_keeps_buffers_bounded(self):
        self.native(COMMON+r'''
int main(){
 mesh::RadioProbeObserver o;char reply[160];arm(o,reply);
 for(const char* command:{"get radio.probe","get radio.probe.page 0","set radio.probe off"}){cli(o,command,reply,false);assert(strstr(reply,"local only"));}
 for(const char* command:{"set radio.probe 0","set radio.probe 0123456789abcdeg","get radio.probe.page -1","get radio.probe.page 16","get radio.probe.page 999999999","get radio.probe.page 0 extra","get radio.probe.page "}){cli(o,command,reply);assert(strstr(reply,"Error:"));}
 cli(o,"get radio.probe",reply);assert(strstr(reply,"active=1")&&strstr(reply,"nonce=0123456789abcdef"));
 assert(!o.handle("get radio.probe-other",reply,sizeof(reply),true));
 char tiny[10];memset(tiny,'X',sizeof(tiny));assert(o.handle("get radio.probe",tiny,8,true));assert(tiny[7]==0&&tiny[8]=='X');
}
'''.replace('#include <cassert>','#include <cassert>\n#include <initializer_list>'))

    def test_feature_scope_full_esp_nrf_only_and_explicit_optout(self):
        source='#include <helpers/RadioProbeObserver.h>\nstatic_assert(MESH_ENABLE_RADIO_PROBE_OBSERVER==EXPECTED,"scope");\nint main(){}\n'
        cases=[((),0),(('-DESP32_PLATFORM=1',),0),(('-DNRF52_PLATFORM=1',),0),
            (('-DSTM32_PLATFORM=1','-DCOMPANION_RADIO_FULL=1'),0),
            (FLAGS,1),(('-DNRF52_PLATFORM=1','-DCOMPANION_RADIO_FULL=1'),1),
            ((*FLAGS,'-DMESH_ENABLE_RADIO_PROBE_OBSERVER=0'),0),
            ((*FLAGS,'-DMESH_PRIMARY_ESPNOW=1'),0),
            (('-DMESH_ENABLE_RADIO_PROBE_OBSERVER=1',),0)]
        for flags,expected in cases:
            with self.subTest(flags=flags):self.native(source,(*flags,'-DEXPECTED='+str(expected)))

    def test_cardputer_full_recipe_optout_keeps_bench_full_observers(self):
        recipes=Recipes()
        scopes={
            "M5Stack_Cardputer_Adv_companion_radio_full":0,
            "M5Stack_Cardputer_Adv_companion_radio_ble":0,
            "M5Stack_Cardputer_Adv_companion_radio_usb":0,
            **{target:1 for target in (
                "meshnology_w12_companion_radio_full",
                "heltec_v4_2_v4_3_companion_radio_full_femon",
                "Xiao_S3_WIO_companion_radio_full",
                "LilyGo_TDeck_companion_radio_full",
                "SenseCapIndicator-LoRa_companion_radio_full",
                "Heltec_t096_companion_radio_full_femon",
                "Heltec_tower_v2_companion_radio_full",
                "RAK_WisMesh_Tag_companion_radio_full",
                "solarxiao_33S_companion_radio_full",
                "Xiao_nrf52_companion_radio_full",
                "RAK_3401_companion_radio_full",
                "RAK_4631_companion_radio_full")}}
        source='#include <helpers/RadioProbeObserver.h>\nstatic_assert(MESH_ENABLE_RADIO_PROBE_OBSERVER==EXPECTED,"recipe scope");\nint main(){}\n'
        for target,expected in scopes.items():
            with self.subTest(target=target):
                full='_companion_radio_full' in target
                section='env:'+target
                if not recipes.config.has_section(section):
                    # Derived Full names use the existing transport recipe.
                    choices=[target.replace('_companion_radio_full','_companion_radio_'+mode,1)
                        for mode in ('usb','ble')]
                    choices.append(target.replace('_companion_radio_full','_comp_radio_usb_wifi',1))
                    section=next(('env:'+name for name in choices
                        if recipes.config.has_section('env:'+name)), '')
                self.assertTrue(section, target)
                flags=defines(recipes.value(section,'build_flags'))
                if target=='M5Stack_Cardputer_Adv_companion_radio_full':
                    self.assertEqual(flags.get('MESH_ENABLE_RADIO_PROBE_OBSERVER'),'0')
                else:
                    self.assertNotIn('MESH_ENABLE_RADIO_PROBE_OBSERVER',flags)
                relevant=[f'-D{key}={value}' for key,value in flags.items()
                    if key in ('ESP32_PLATFORM','NRF52_PLATFORM','MESH_PRIMARY_ESPNOW','MESH_ENABLE_RADIO_PROBE_OBSERVER')]
                # build.sh supplies the Full overlay after resolving this recipe.
                if full:relevant.append('-DCOMPANION_RADIO_FULL=1')
                self.native(source,(*relevant,'-DEXPECTED='+str(expected)))

    def test_actual_MyMesh_raw_callback_and_local_CLI_prefix_keep_normal_app_limit(self):
        cpp=(ROOT/'examples/companion_radio/MyMesh.cpp').read_text()
        callback=body(cpp,'void MyMesh::logRxRaw(')
        start=cpp.index('bool MyMesh::handleCommand(const char* command, uint32_t sender_timestamp,')
        end=cpp.index('  if (mesh::cli::deferForRadioMaintenance(',start)
        handler=cpp[start:end]+'  return false;\n}\n'
        dispatcher=(ROOT/'src/Dispatcher.cpp').read_text()
        parse=body(dispatcher,'bool Dispatcher::tryParsePacket(')
        receive=body(dispatcher,'void Dispatcher::checkRecv(')
        self.assertLess(receive.index('logRxRaw(snr, rssi, raw, len)'),receive.index('tryParsePacket(pkt, raw, len)'))
        self.assertLess(receive.index('if (len > 0)'),receive.index('logRxRaw(snr, rssi, raw, len)'))
        self.assertIn('sender_timestamp ? sender_timestamp : 1',cpp)
        self.native(COMMON+r'''
#include <Packet.h>
#include <helpers/BaseSerialInterface.h>
mesh::Packet::Packet(){}
struct Radio {uint8_t profile=1;uint8_t receiveProfile()const{return profile;}uint32_t receiveProfileGeneration()const{return 9;}};
struct Serial : BaseSerialInterface {
 bool connected=false;unsigned writes=0;
 void enable() override {} void disable() override {} bool isEnabled()const override{return true;}
 bool isConnected()const override{return connected;}
 bool isReadBusy()const override{return false;} bool isWriteBusy()const override{return false;}
 size_t writeFrame(const uint8_t*,size_t n)override{assert(n<=176);++writes;return n;}
 size_t checkRecvFrame(uint8_t*)override{return 0;}
};
class MyMesh {public:mesh::RadioProbeObserver _radio_probe;Radio radio;Radio* _radio=&radio;Serial serial;Serial* _serial=&serial;uint8_t out_frame[176];
 void logRxRaw(float,float,const uint8_t*,int);bool handleCommand(const char*,uint32_t,char*);};
#define MAX_FRAME_SIZE 176
#define PUSH_CODE_LOG_RX_DATA 0x88
class Dispatcher {public:Radio radio;Radio* _radio=&radio;bool tryParsePacket(mesh::Packet*,const uint8_t*,int);};
using mesh::Packet;using mesh::FLOOD_RETRY_POLICY_DEFAULT;using mesh::RADIO_TX_AUTO;
@CALLBACK@
@HANDLER@
@PARSE@
int main(){
 MyMesh mesh;char reply[160];uint8_t raw[256];packet(raw);
 assert(mesh.handleCommand("aa|get usb.raw.max",0,reply)&&!strcmp(reply,"aa|> 176"));
 assert(mesh.handleCommand("get usb.raw.max",123,reply)&&strstr(reply,"local transport"));
 assert(mesh.handleCommand("aa|set radio.probe 0123456789abcdef",0,reply)&&strstr(reply,"aa|OK"));
 mesh.logRxRaw(-7.75f,-110.75f,raw,255);assert(mesh.serial.writes==0);
 assert(mesh.handleCommand("get radio.probe",0,reply)&&strstr(reply,"unique=1"));
 assert(mesh.handleCommand("set radio.probe off",123,reply)&&strstr(reply,"local only"));
 mesh.serial.connected=true;packet(raw,1);mesh.logRxRaw(1,-90,raw,255);assert(mesh.serial.writes==0);
 Dispatcher d;mesh::Packet app;assert(!d.tryParsePacket(&app,raw,255));assert(app.payload_len==253);
 assert(d.tryParsePacket(&app,raw,42));mesh.logRxRaw(1,-90,raw,42);assert(mesh.serial.writes==1);
 assert(mesh.handleCommand("set radio.probe off",0,reply));packet(raw,2);mesh.logRxRaw(1,-90,raw,255);
 assert(mesh.handleCommand("get radio.probe",0,reply)&&strstr(reply,"unique=2"));
}
'''.replace('@CALLBACK@',callback).replace('@HANDLER@',handler).replace('@PARSE@',parse),(*FLAGS,'-std=c++17','-Wno-unused-parameter'))


if __name__=='__main__':unittest.main()
