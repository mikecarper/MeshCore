#!/usr/bin/env python3
"""Run the actual role setters while saved WiFi is off or stopping."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

PREAMBLE = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <new>
#include <helpers/WirelessControl.h>
namespace mesh { namespace cli {
void formatError(char* reply,size_t size,const char* message){snprintf(reply,size,"Error: %s",message);}
}}
using namespace mesh::wireless;
struct WebConfigServer {
  static bool saved,save_ok;
  bool running=false;
  unsigned stops=0;
  static bool saveEnabled(bool value){if(!save_ok)return false;saved=value;return true;}
  bool isRunning()const{return running;}
  void requestStop(){++stops;running=false;}
};
bool WebConfigServer::saved=false;
bool WebConfigServer::save_ok=true;
struct StrHelper {
  static void strncpy(char* out,const char* in,size_t size){std::strncpy(out,in,size);out[size-1]=0;}
};
struct RadioOwner : Backend {
  bool saved=true,active=true;
  uint8_t available()const override{return WiFi;}
  uint8_t enabled()const override{return active?WiFi:0;}
  uint8_t clients()const override{return Independent;}
  bool hasWiFiPreference()const override{return true;}
  bool wifiPreference()const override{return saved;}
  bool saveWiFiPreference(bool on)override{saved=on;return true;}
  Result set(uint8_t,bool on)override{active=on;return Result::Done;}
};
struct MyMesh {
  WebConfigServer server;
  WebConfigServer* _webconfig=&server;
  unsigned starts=0;
  const char* start_reply="WebConfig started";
  bool startWebConfig(bool force,char* reply){
    assert(!force);++starts;std::strcpy(reply,start_reply);
    server.running=!std::strncmp(start_reply,"WebConfig",9);return true;
  }
  bool setWebUIEnabled(bool enabled,char* reply);
};
void boot(RadioOwner& owner,bool wifi){
  Control& current=control();current.~Control();new(&current)Control;
  owner.saved=owner.active=wifi;control().begin(owner);
  WebConfigServer::saved=false;WebConfigServer::save_ok=true;
}
'''

SCENARIOS = r'''
int main(){
  RadioOwner owner;char reply[160];
  // A successful saved setting must not implicitly enable its parent WiFi.
  boot(owner,false);
  { MyMesh node;
    assert(node.setWebUIEnabled(true,reply));
    assert(!strcmp(reply,"OK - webui on saved; WiFi is off"));
    assert(WebConfigServer::saved&&!owner.saved&&!owner.active);
    assert(node.starts==0&&!node.server.running);
  }
  // The preference write can fail independently of whether WiFi is blocked.
  boot(owner,false);WebConfigServer::save_ok=false;
  { MyMesh node;
    assert(node.setWebUIEnabled(true,reply));
    assert(strstr(reply,"Error:")&&!WebConfigServer::saved&&node.starts==0);
  }
  // Reply-drain grace leaves WiFi active, but saved WebUI must not race shutdown.
  boot(owner,true);
  assert(control().handle("set wifi off",reply,sizeof(reply),100,Independent));
  assert(!control().blocked(WiFi)&&control().pendingDisable(WiFi));
  { MyMesh node;node.server.running=true;
    assert(node.setWebUIEnabled(true,reply));
    assert(!strcmp(reply,"OK - webui on saved; WiFi is off"));
    assert(WebConfigServer::saved&&node.starts==0&&node.server.stops==0);
  }
  // Normal startup and its failures remain operational, not hidden by the save.
  boot(owner,true);
  { MyMesh node;
    assert(node.setWebUIEnabled(true,reply));
    assert(!strncmp(reply,"OK - webui on; WebConfig",strlen("OK - webui on; WebConfig")));
    assert(WebConfigServer::saved&&owner.saved&&node.starts==1&&node.server.running);
  }
  boot(owner,true);
  { MyMesh node;node.server.running=true;
    assert(node.setWebUIEnabled(true,reply));
    assert(!strcmp(reply,"OK - webui on (already active)")&&node.starts==0);
  }
  boot(owner,true);
  { MyMesh node;node.start_reply="Error: listener startup failed";
    assert(node.setWebUIEnabled(true,reply));
    assert(!strcmp(reply,"Error: listener startup failed"));
    assert(WebConfigServer::saved&&node.starts==1);
  }
  boot(owner,true);
  { MyMesh node;node.server.running=true;
    assert(node.setWebUIEnabled(false,reply));
    assert(!strcmp(reply,"OK - webui off"));
    assert(!WebConfigServer::saved&&owner.saved&&node.server.stops==1);
  }
}
'''


class WebUISavedIntentTest(unittest.TestCase):
    def test_saved_setters_respect_blocked_and_pending_wifi(self):
        with tempfile.TemporaryDirectory(prefix='webui-saved-intent-') as tmp:
            for role in ('simple_repeater','simple_room_server','companion_radio'):
                with self.subTest(role=role):
                    text=(ROOT/'examples'/role/'MyMesh.cpp').read_text()
                    if role=='companion_radio':
                        setter=method(text,'  if (strcmp(command, "set webui on") == 0')
                        setter=('bool MyMesh::setWebUIEnabled(bool enabled,char* reply){\n'
                                'const char* command=enabled?"set webui on":"set webui off";\n'
                                'const size_t reply_size=160;\n'+setter+'\nreturn false;\n}')
                    else:setter=method(text,'bool MyMesh::setWebUIEnabled(')
                    cpp=Path(tmp)/(role+'.cpp');binary=Path(tmp)/role
                    cpp.write_text(PREAMBLE+setter+'\n'+SCENARIOS)
                    subprocess.run([os.environ.get('CXX','g++'),'-std=c++11','-Wall','-Wextra','-Werror','-Wno-format-truncation',
                                    '-I'+str(ROOT/'src'),str(cpp),'-o',str(binary)],check=True)
                    subprocess.run([str(binary)],check=True)


if __name__=='__main__':unittest.main()
