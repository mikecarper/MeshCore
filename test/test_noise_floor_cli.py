"""Run real noise CLI dispatch, prefix handling, and room/repeater permission gates."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <string>
#include <vector>
#include <helpers/NoiseFloorHistory.h>
#include <helpers/TelemetryHistory.h>
#include <helpers/ExternalVoltageHistory.h>
#include <helpers/TelemetryDailyExtremaCLI.h>
struct ClientInfo { bool admin=true; bool isAdmin() const { return admin; } };
class Repeater {
public:
  mesh::NoiseFloorHistory noise_floor_history;
  mesh::TelemetryHistory telemetry_history;
  mesh::ExternalVoltageHistory external_voltage_history;
  void handleCommand(char* command,char* reply,ClientInfo* sender) {
    @REPEATER_PREFIX@
    @REPEATER_NOISE@
    strcpy(reply,"Err - unrecognized command");
  }
};
class MyMesh {
public:
  mesh::NoiseFloorHistory noise_floor_history;
  mesh::TelemetryHistory telemetry_history;
  mesh::ExternalVoltageHistory external_voltage_history;
  bool handleTelemetryHistoryCommand(const char*,char*,size_t=160,bool=true);
  void handleCommand(uint32_t sender_timestamp,char* command,char* reply,
                     int client_index=-1,uint8_t path_hash_size=1) {
    (void)sender_timestamp; (void)client_index; (void)path_hash_size;
    @ROOM_PREFIX@
    @ROOM_DISPATCH@
    strcpy(reply,"Err - unrecognized command");
  }
  void remote(ClientInfo* client,char* text,char* reply) {
    uint8_t temp[165]={};
    const uint32_t sender_timestamp=100;
    const int i=0;
    struct Packet {uint8_t getPathHashSize() const {return 1;}} real_packet;
    Packet* packet=&real_packet;
    @ROOM_GATE@
    strcpy(reply,reinterpret_cast<char*>(&temp[5]));
  }
};
@ROOM_HISTORY@

static void seed(mesh::NoiseFloorHistory& history) {
  history.advance(999900);
  for (uint32_t bucket=0;bucket<176;++bucket) {
    for (uint8_t profile=0;profile<2;++profile) for (uint8_t slot=0;slot<3;++slot) {
      assert(history.observe(profile,999900+bucket*900+slot*300,
                             profile ? -66.0f : -119.0f,bucket*3+slot));
    }
    history.advance(999900+(bucket+1)*900);
  }
}
static void seedValues(mesh::TelemetryHistory& history) {
  for (uint32_t i=0;i<336;++i) {
    const uint32_t epoch=1800000000U+i*1800U;
    history.record(epoch,int16_t(i%70-25),true,uint16_t(3000+i%1000),0,0,false);
  }
}
static std::vector<uint8_t> decode(const char* text) {
  const char* alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::vector<uint8_t> result;
  uint32_t bits=0;unsigned count=0;
  for (;*text && *text!='=';++text) {
    const char* found=strchr(alphabet,*text);assert(found);
    bits=(bits<<6)|uint32_t(found-alphabet);count+=6;
    if (count>=8) {count-=8;result.push_back(uint8_t(bits>>count));}
  }
  return result;
}
static void verify(const char* reply,unsigned profile,bool prefix) {
  assert(strlen(reply)==(prefix ? 159U : 158U));
  assert(!memcmp(reply,prefix ? "xy|" : "> ",prefix ? 3 : 2));
  const auto bytes=decode(reply+(prefix ? 3 : 2));
  assert(bytes.size()==7+110 && bytes[0]==(profile ? 0x16 : 0x15));
  assert(bytes[1]==uint8_t(999900) && bytes[2]==uint8_t(999900>>8)
      && bytes[3]==uint8_t(999900>>16) && bytes[4]==uint8_t(999900>>24));
  assert(bytes[5]==15 && bytes[6]==176);
  for (unsigned sample=0;sample<176;++sample) {
    uint8_t code=0;
    for (unsigned bit=0;bit<5;++bit) {
      const unsigned at=sample*5+bit;
      code=uint8_t((code<<1)|((bytes[7+at/8]>>(7-at%8))&1));
    }
    assert(code==(profile ? 28 : 1));
  }
}
template<class Invoke> static void bounded(const std::string& text,Invoke invoke,
                                          bool success,unsigned profile,bool prefix) {
  std::vector<char> storage(176,'~');
  std::vector<char> command(text.begin(),text.end());command.push_back(0);
  invoke(command.data(),storage.data()+8);
  for (unsigned i=0;i<8;++i) assert(storage[i]=='~' && storage[168+i]=='~');
  assert(memchr(storage.data()+8,0,160));
  if (success) verify(storage.data()+8,profile,prefix);
  else assert(strstr(storage.data()+8,"Err - "));
}
template<class Invoke> static void extrema(const std::string& text,Invoke invoke,
                                            bool allowed,bool prefix,uint8_t series) {
  std::vector<char> storage(176,'~');
  std::vector<char> command(text.begin(),text.end());command.push_back(0);
  invoke(command.data(),storage.data()+8);
  for (unsigned i=0;i<8;++i) assert(storage[i]=='~' && storage[168+i]=='~');
  const char* reply=storage.data()+8;
  assert(memchr(reply,0,160));
  if (!allowed) { assert(strstr(reply,"Err - ") && strstr(reply,"permi"));return; }
  assert(!memcmp(reply,prefix ? "xy|" : "> ",prefix ? 3 : 2));
  const auto bytes=decode(reply+(prefix ? 3 : 2));
  if (series==0) {
    assert(bytes.size()==112 && bytes[0]==0x18 && bytes[1]==1 && bytes[2]==30);
    assert(strlen(reply)==(prefix ? 155U : 154U));
    // Verify the actual stored readings survive dispatch and encoding: the
    // newest day has temperature -17..30 C, and rounded battery 3.29..3.34 V.
    unsigned bit=0;
    auto readBits=[&](unsigned width) {
      unsigned value=0;
      while(width--) {value=(value<<1)|((bytes[7+bit/8]>>(7-bit%8))&1);++bit;}
      return value;
    };
    assert(readBits(6)==47 && readBits(6)==0);
    for (unsigned value:{35U,36U,37U,82U,83U,0U}) assert(readBits(8)==value);
    assert(readBits(6)==41 && readBits(6)==0);
    for (unsigned value:{143U,143U,144U,147U,148U,0U}) assert(readBits(8)==value);
  } else {
    assert(bytes[0]==0x17 && bytes[1]==1 && bytes[2]==series);
  }
}
int main() {
  Repeater repeater;MyMesh room;
  seed(repeater.noise_floor_history);seed(room.noise_floor_history);
  seedValues(repeater.telemetry_history);
  seedValues(room.telemetry_history);
  ClientInfo admin,denied;denied.admin=false;
  for (unsigned profile:{0U,1U}) for (bool prefix:{false,true}) {
    const std::string text=std::string(prefix ? "xy|" : "")
        +(profile ? "get telemetry.noise2" : "get telemetry.noise");
    for (const std::string& suffix:{std::string(),std::string(" 1")}) {
      bounded(text+suffix,[&](char* command,char* reply) {
        repeater.handleCommand(command,reply,nullptr);
      },true,profile,prefix);
      bounded(text+suffix,[&](char* command,char* reply) {
        repeater.handleCommand(command,reply,&admin);
      },true,profile,prefix);
      bounded(text+suffix,[&](char* command,char* reply) {
        room.handleCommand(0,command,reply);
      },true,profile,prefix);
      bounded(text+suffix,[&](char* command,char* reply) {
        room.remote(&admin,command,reply);
      },true,profile,prefix);
      bounded(text+suffix,[&](char* command,char* reply) {
        repeater.handleCommand(command,reply,&denied);
      },false,profile,prefix);
      bounded(text+suffix,[&](char* command,char* reply) {
        room.remote(&denied,command,reply);
      },false,profile,prefix);
    }
  }
  for (const char* bad:{"get telemetry.noisefake","get telemetry.noise2fake",
                         "get telemetry.noise 0","get telemetry.noise2 6"}) {
    bounded(std::string("xy|")+bad,[&](char* command,char* reply) {
      repeater.handleCommand(command,reply,&admin);
    },false,0,true);
    bounded(std::string("xy|")+bad,[&](char* command,char* reply) {
      room.remote(&admin,command,reply);
    },false,0,true);
  }
  struct Request { const char* command;uint8_t series; };
  for (const Request request: {
      Request{"get telemetry.minmax",0},Request{"get telemetry.temp minmax",1},
      Request{"get telemetry.volt minmax",2}}) {
    for (bool prefix:{false,true}) {
      const std::string text=std::string(prefix ? "xy|" : "")+request.command;
      extrema(text,[&](char* c,char* r){repeater.handleCommand(c,r,nullptr);},true,prefix,request.series);
      extrema(text,[&](char* c,char* r){repeater.handleCommand(c,r,&admin);},true,prefix,request.series);
      extrema(text,[&](char* c,char* r){repeater.handleCommand(c,r,&denied);},false,prefix,request.series);
      extrema(text,[&](char* c,char* r){room.handleCommand(0,c,r);},true,prefix,request.series);
      extrema(text,[&](char* c,char* r){room.remote(&admin,c,r);},true,prefix,request.series);
      extrema(text,[&](char* c,char* r){room.remote(&denied,c,r);},false,prefix,request.series);
    }
  }
  for (const char* bad:{"get telemetry.minmax extra", "get telemetry.temp minmax extra",
                        "get telemetry.volt minmax extra"}) {
    std::vector<char> c(bad,bad+strlen(bad));c.push_back(0);char reply[160]={};
    repeater.handleCommand(c.data(),reply,&admin);
    assert(strstr(reply,"Err - "));
  }
  Repeater empty;
  char emptyCommand[]="get telemetry.minmax";char emptyReply[160]={};
  empty.handleCommand(emptyCommand,emptyReply,&admin);
  assert(strstr(emptyReply,"history is empty"));
  puts("noise CLI prefix, permission and payload checks passed");
}
'''


class NoiseFloorCLITests(unittest.TestCase):
    def test_actual_prefix_dispatch_permissions_and_full_pages_fit_160_bytes(self):
        repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        repeater_handler = extract_braced(repeater, "void MyMesh::handleCommand(uint32_t sender_timestamp,")
        room_handler = extract_braced(room, "void MyMesh::handleCommand(uint32_t sender_timestamp,")

        def prefix(handler):
            declaration = re.search(r"const bool prefixed_reply[^;]+;", handler).group()
            return declaration + "\n" + extract_braced(handler, "if (strlen(command) > 4 && command[2] == '|')")

        start = repeater_handler.index('  if (mesh::telemetry_daily_cli::handle(')
        end = repeater_handler.index('  const char* telemetry_args', start)
        receiver = extract_braced(room, "void MyMesh::onPeerDataRecv(")
        gate = extract_braced(receiver[receiver.index("if (!handleClientPathCommand"):], "if (client->isAdmin())")
        end_gate = receiver[receiver.index(gate) + len(gate):]
        gate += " " + extract_braced(end_gate, "else")
        gate = gate.replace("_cli.morseReply().beginCommand(client->id.pub_key);", "")
        gate = gate.replace("_cli.morseReply().endCommand();", "")
        replacements = {
            "@REPEATER_PREFIX@": prefix(repeater_handler),
            "@REPEATER_NOISE@": repeater_handler[start:end],
            "@ROOM_PREFIX@": prefix(room_handler),
            "@ROOM_DISPATCH@": re.search(
                r"if \(handleTelemetryHistoryCommand\([\s\S]*?\)\) return;", room_handler).group(),
            "@ROOM_GATE@": gate,
            "@ROOM_HISTORY@": extract_braced(room, "bool MyMesh::handleTelemetryHistoryCommand("),
        }
        harness = HARNESS
        for marker, value in replacements.items():
            harness = harness.replace(marker, value)
        flags = [] if os.name == "nt" else ["-fsanitize=address,undefined",
            "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        with tempfile.TemporaryDirectory(prefix="meshcore-noise-cli-") as directory:
            directory = Path(directory)
            for legacy in (False, True):
                with self.subTest(old_prefix_plumbing=legacy):
                    cpp, binary = directory / "cli.cpp", directory / "cli"
                    code = harness.replace("prefixed_reply ? 157 : 160, !prefixed_reply",
                        "prefixed_reply ? 160 : 160, true") if legacy else harness
                    cpp.write_text(code, encoding="ascii")
                    built = subprocess.run([os.environ.get("CXX") or shutil.which("g++") or "g++",
                        "-std=c++17", "-Wall", "-Wextra", "-Werror", *flags,
                        "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                    if legacy:
                        self.assertNotEqual(checked.returncode, 0)
                        self.assertIn("storage[i]", checked.stderr)
                    else:
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                        self.assertIn("noise CLI prefix, permission and payload checks passed", checked.stdout)


if __name__ == "__main__":
    unittest.main()
