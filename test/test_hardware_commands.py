#!/usr/bin/env python3
"""Exercise hardware guards, bounded replies and production role dispatch hooks."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/HardwareCommand.h>
#include <cassert>
#include <cstring>
#include <string>
using namespace mesh::hardwareCommand;

static void check(const char* text, const char* board, const char* role,
                  const char* forwarded, const char* response) {
  const char* command = text;
  char reply[164]; memset(reply, 0x5a, sizeof(reply));
  const bool consumed = apply(command, board, role, reply, 157);
  if (forwarded) {
    assert(!consumed && !strcmp(command, forwarded));
    assert(reply[0] == 0x5a);
  } else {
    assert(consumed && !strcmp(reply, response));
  }
  for (size_t i=157; i<sizeof(reply); ++i) assert(reply[i]==0x5a);
}

struct Board { const char* getManufacturerName() const { return "Heltec_tower_v2"; } } board;
struct CLI { Board* getBoard() { return &board; } } _cli;
struct ClientInfo {
  bool admin=false, region=false, filter=false;
  bool isAdmin()const{return admin;}
  bool isRegionMgr()const{return region;}
  bool isFilterMgr()const{return filter;}
};
@AUTHORIZATION_HELPERS@
static void repeater(ClientInfo* sender, char* command, char* reply) {
  @REPEATER_GUARD@
  @AUTHORIZATION_GUARD@
  strcpy(reply, command);
}
static void room(char* command, char* reply) {
  @ROOM_GUARD@
  strcpy(reply, command);
}
static void sensor(char* command, char* reply) {
  @SENSOR_GUARD@
  strcpy(reply, command);
}
static bool companion(const char* command, char* reply) {
  size_t reply_capacity=157;
  @COMPANION_GUARD@
  strcpy(reply,command);
  return false;
}

int main(int argc,char** argv) {
  assert(argc==2);const std::string scenario=argv[1];
  if(scenario=="matching") {
    for(const char* role:{"repeater","room_server","companion","sensor"}) {
      check("get radio2","Heltec_tower_v2",role,"get radio2",nullptr);
      check("@Heltec_tower_v2 get radio2","Heltec_tower_v2",role,"get radio2",nullptr);
    }
    check("@HELTEC_TOWER_V2/R ota pull A1B2C3D4 flash","Heltec_tower_v2","repeater",
          "ota pull A1B2C3D4 flash",nullptr);
    check("@#536d28c9/r get hardware","Heltec_tower_v2","repeater","get hardware",nullptr);
    check("@*/s get hardware","other","room_server","get hardware",nullptr);
    check("@Heltec_Tower_V2/s get radio2","Heltec Tower V2","room_server","get radio2",nullptr);
    assert(identifier("Heltec_tower_v2")==0x536d28c9UL);
    assert(identifier("HELTEC_TOWER_V2")==identifier("Heltec Tower V2"));
  } else if(scenario=="skips") {
    for(const char* text:{"@Heltec_v3/r set radio2 off","@Heltec_tower_v2/s set radio2 off",
                         "@#536D28C8/r ota pull A1B2C3D4 flash","@*/c set radio2 off"})
      check(text,"Heltec_tower_v2","repeater",nullptr,"OK - skipped (hardware/role)");
    check("@Heltec_tower_v2/r reboot",nullptr,"repeater",nullptr,"OK - skipped (hardware/role)");
  } else if(scenario=="malformed") {
    for(const char* text:{"@","@/r get radio2","@board/ get radio2","@board/x get radio2",
                         "@board/rr get radio2","@board/r","@board/r  ","@board/r\nget radio2",
                         "@board/r @other/r reboot","@#123/r get radio2","@#536D28CZ/r reboot",
                         "@board! get radio2","@**/r get radio2"}) {
      Selector selector;
      assert(parse(text,selector)==Parse::Invalid);
      check(text,"board","repeater",nullptr,"Err - use @board[/r|s|c|n] command");
    }
    for(size_t length=64;length<200;++length) {
      const std::string text="@"+std::string(length,'a')+"/r get radio2";
      Selector selector;assert(parse(text.c_str(),selector)==Parse::Invalid);
    }
    char reply[3]={};const char* command="@broken";
    assert(apply(command,"board","repeater",reply,sizeof(reply))&&reply[2]==0);
  } else if(scenario=="special_commands") {
    for(const char* text:{"backup prv.key 0123456789ABCDEF","BACKUP prv.key 0123456789ABCDEF",
                         "password secret","PASSWORD secret","replay reset all CONFIRM",
                         "REPLAY reset all CONFIRM","region load","REGION\t LOAD"}) {
      const std::string guarded="@board/r "+std::string(text);
      check(guarded.c_str(),"board","repeater",nullptr,
            "Err - run this command without a hardware guard");
    }
  } else if(scenario=="identity") {
    char reply[157]={};format("Heltec_tower_v2","repeater",reply,sizeof(reply));
    assert(!strcmp(reply,"> hw=heltec_tower_v2 role=repeater id=536D28C9"));
    format(nullptr,nullptr,reply,sizeof(reply));
    assert(strstr(reply,"hw=unknown role=unknown"));
  } else if(scenario=="role_dispatch") {
    char command[160],reply[160]={};ClientInfo admin;admin.admin=true;
    strcpy(command,"@Heltec_tower_v2/r ota pull A1B2C3D4 flash");
    repeater(&admin,command,reply);assert(!strcmp(reply,"ota pull A1B2C3D4 flash"));
    ClientInfo manager;manager.filter=true;
    strcpy(command,"@Heltec_tower_v2/r set flood.max 4");
    repeater(&manager,command,reply);assert(!strcmp(reply,"set flood.max 4"));
    strcpy(command,"@Heltec_tower_v2/r ota pull A1B2C3D4 flash");
    repeater(&manager,command,reply);assert(!strcmp(reply,"Err - not permitted"));
    strcpy(command,"@Heltec_tower_v2/r get radio2");
    room(command,reply);assert(!strcmp(reply,"OK - skipped (hardware/role)"));
    strcpy(command,"@Heltec_tower_v2/s get radio2");
    room(command,reply);assert(!strcmp(reply,"get radio2"));
    assert(companion("@Heltec_tower_v2/r ota pull A1B2C3D4 flash",reply));
    assert(!strcmp(reply,"OK - skipped (hardware/role)"));
    assert(companion("@Heltec_tower_v2/c get hardware",reply));
    assert(!strcmp(reply,"> hw=heltec_tower_v2 role=companion id=536D28C9"));
    strcpy(command,"@Heltec_tower_v2/r reboot");
    sensor(command,reply);assert(!strcmp(reply,"OK - skipped (hardware/role)"));
    strcpy(command,"@Heltec_tower_v2/n get hardware");
    sensor(command,reply);assert(!strcmp(reply,"get hardware"));
  } else return 2;
}
'''


class HardwareCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise RuntimeError("A host C++ compiler is required")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-hardware-command-")
        cls.addClassCleanup(cls.work.cleanup)
        repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text()
        room = (ROOT / "examples/simple_room_server/MyMesh.cpp").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        sensor = (ROOT / "examples/simple_sensor/SensorMesh.cpp").read_text()
        helper_signatures = (
            "static bool commandFamilyMatches(", "static bool commandListMatches(",
            "static bool isCommonManagerReadOnlyAllowed(", "static bool isRegionMgrAllowed(",
            "static bool isFilterMgrAllowed(",
        )
        helpers = '\n'.join(body(repeater, sig) for sig in helper_signatures)
        handler = body(repeater, "void MyMesh::handleCommand(uint32_t sender_timestamp,")
        auth_start = handler.rindex("if (sender && !sender->isAdmin())", 0,
                                    handler.index("mesh::cli::handleACLGet("))
        auth = body(handler[auth_start:], "if (sender && !sender->isAdmin())")
        fixture = HARNESS.replace("@AUTHORIZATION_HELPERS@", helpers)
        fixture = fixture.replace("@AUTHORIZATION_GUARD@", auth)
        for source, label, role in ((repeater, "REPEATER", "repeater"),
                                     (room, "ROOM", "room_server"),
                                     (sensor, "SENSOR", "sensor")):
            start = source.index("  const char* hardware_command = command;")
            end = source.index("#endif", start)
            snippet = source[start:end].replace("FIRMWARE_ROLE", '"' + role + '"')
            fixture = fixture.replace("@" + label + "_GUARD@", snippet)
        start = companion.index("  if (mesh::hardwareCommand::apply(command,")
        end = companion.index("#endif", start)
        fixture = fixture.replace("@COMPANION_GUARD@", companion[start:end])
        fixture = '#include <helpers/FloodCommandAliases.h>\n' + fixture
        work = Path(cls.work.name)
        source = work / "fixture.cpp"
        source.write_text(fixture)
        cls.binary = work / "hardware-command"
        sanitizers = (["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
                      if os.environ.get("MESHCORE_FLEET_SANITIZERS") == "1" else [])
        built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                "-DMESH_ENABLE_FLOOD_RULE_ENGINE=1", *sanitizers,
                                "-I", str(ROOT / "src"), str(source), "-o", str(cls.binary)],
                               capture_output=True, text=True, timeout=60)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def test_guard_matching_and_compact_identity(self):
        self.scenario("matching")

    def test_wrong_board_and_wrong_role_skip_without_dispatch(self):
        self.scenario("skips")

    def test_malformed_nested_and_overlong_guards_fail_closed(self):
        self.scenario("malformed")

    def test_sensitive_and_recovery_commands_keep_their_original_handlers(self):
        self.scenario("special_commands")

    def test_machine_readable_hardware_identity(self):
        self.scenario("identity")

    def test_production_role_hooks_preserve_manager_permissions(self):
        self.scenario("role_dispatch")

    def scenario(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
