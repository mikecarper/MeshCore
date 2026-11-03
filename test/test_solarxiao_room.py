"""Run real SolarXiao room telemetry/history and role-initialization boundaries."""
from pathlib import Path
import configparser
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced
from test_solarxiao_carrier import FILESYSTEM_WITH_METADATA, WIRE

ROOT = Path(__file__).resolve().parents[1]
ROOM = ROOT / "examples/simple_room_server"
SENSORS = ROOT / "src/helpers/sensors"


def conditional_block(source, marker):
    start = source.index(marker)
    depth = 0
    for directive in re.finditer(r"(?m)^\s*#\s*(if|ifdef|ifndef|endif)\b[^\n]*(?:\n|$)", source[start:]):
        if directive.group(1) == "endif":
            depth -= 1
            if depth == 0:
                return source[start:start + directive.end()]
        else:
            depth += 1
    raise AssertionError("unterminated production feature block: " + marker)


SENSOR_BOUNDARY = r'''
#pragma once
#include <cstdint>
#include <vector>
#include <cassert>
#include <cmath>
#include <helpers/sensors/LPPDataHelpers.h>
struct CayenneLPP {
  std::vector<uint8_t> data;size_t capacity;
  explicit CayenneLPP(size_t cap=180):capacity(cap) {}
  void reset() {data.clear();}
  size_t getSize() const {return data.size();}
  const uint8_t* getBuffer() const {return data.data();}
  void add(uint8_t ch,uint8_t type) {
    const size_t size=LPPData::getDataSize(type);
    if(data.size()+2+size>capacity)return;
    data.push_back(ch);data.push_back(type);data.insert(data.end(),size,0);
  }
  void addVoltage(uint8_t c,float) {add(c,LPP_VOLTAGE);}
  void addCurrent(uint8_t c,float) {add(c,LPP_CURRENT);}
  void addPower(uint8_t c,float) {add(c,LPP_POWER);}
  void addTemperature(uint8_t c,float) {add(c,LPP_TEMPERATURE);}
  void addGenericSensor(uint8_t c,float) {add(c,LPP_GENERIC_SENSOR);}
};
namespace mesh {
struct MainBoard {
  uint16_t battery_mv=3850;float mcu_c=26.75f;
  unsigned battery_reads=0,temperature_reads=0;
  uint16_t getBattMilliVolts() {++battery_reads;return battery_mv;}
  float getMCUTemperature() {++temperature_reads;return mcu_c;}
};
struct SensorManager {
  struct VoltageSensorReading {uint8_t channel;float voltage;bool valid;};
};
}
'''

HISTORY_HARNESS = r'''
#include <cassert>
#include <cmath>
#include <cstring>
#include <string>
#include <algorithm>
#include <helpers/IdentityStore.h>
#include <helpers/SensorManager.h>
#include <helpers/TelemetryHistory.h>
#include <helpers/ExternalVoltageHistory.h>
#include <helpers/NoiseFloorHistory.h>
#include <helpers/ClientACLResponse.h>
#include <Wire.h>
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#define TELEM_CHANNEL_SELF 1
#define TELEM_PERM_ENVIRONMENT 4
#define PERM_ACL_ROLE_MASK 7
#define PERM_ACL_GUEST 0
#define PERM_ACL_READ_ONLY 1
#define PERM_ACL_READ_WRITE 2
#define PERM_ACL_ADMIN 3
#define REQ_TYPE_GET_TELEMETRY_DATA 3
#define MESH_DEBUG_PRINTLN(...) do {} while(0)
using std::isfinite;
using std::isnan;
static uint32_t now_ms=100;
uint32_t millis() {return now_ms;}
int digitalRead(int pin) {assert(pin==30 || pin==31);return 1;}
FakeWire Wire;
#include "@ADAPTER@"
using namespace mesh;
using namespace mesh::solar;
void optionalQuery(uint8_t channel,uint8_t,CayenneLPP& lpp) {
  assert(channel==14);lpp.addVoltage(channel,5);
}
bool optionalVoltage(uint8_t,float& value) {value=5;return true;}
class EnvironmentSensorManager:public SensorManager {
  struct ActiveSensor {
    void(*query)(uint8_t,uint8_t,CayenneLPP&);
    bool(*query_voltage)(uint8_t,float&);uint8_t sub_channel;
  };
  ActiveSensor _active_sensors[1]={{optionalQuery,optionalVoltage,0}};
  int _active_sensor_count=ROOM_OPTIONAL_SENSOR_COUNT;
  uint8_t next_available_channel=2;
public:
  bool querySensors(uint8_t,CayenneLPP&);
  uint8_t getVoltageSensorChannels(uint8_t*,uint8_t) const;
  uint8_t queryVoltageSensors(VoltageSensorReading*,uint8_t);
};
@ENV_METHODS@
struct Clock {uint32_t epoch=0;uint32_t getCurrentTime() const {return epoch;}};
struct ClientInfo {
  uint8_t permissions=PERM_ACL_ADMIN;
  bool isAdmin() const {return (permissions&PERM_ACL_ROLE_MASK)==PERM_ACL_ADMIN;}
};
struct CommonCLI {
  MainBoard& board;
  MainBoard* getBoard() {return &board;}
  void handleCommand(uint32_t sender_timestamp,char* command,char* reply) {
    @COMMON_HOOK@
    strcpy(reply,"unhandled");
  }
};
class MyMesh {
public:
  MainBoard board;
  CommonCLI _cli{board};
  Clock clock;
  EnvironmentSensorManager sensors;
  CayenneLPP telemetry{@ROOM_TELEMETRY_CAPACITY@};
  uint8_t reply_data[184];
  mesh::TelemetryHistory telemetry_history;
  mesh::ExternalVoltageHistory external_voltage_history;
  mesh::NoiseFloorHistory noise_floor_history;
  Clock* getRTCClock() {return &clock;}
  void configureHistory() {
    @HISTORY_INIT@
  }
  void sampleTelemetryHistory(bool emergency_only = false);
  bool handleTelemetryHistoryCommand(const char*,char*,size_t=160,bool=true);
  int handleRequest(ClientInfo*,uint32_t,uint8_t*,size_t,size_t);
  void handleCommand(uint32_t sender_timestamp,char* command,char* reply,
                     int gpio_client_index=-1,uint8_t gpio_path_hash_size=1) {
    (void)gpio_path_hash_size;
    @TIMESTAMP_GUARD@
    @PREFIX@
    _cli.handleCommand(sender_timestamp,command,reply);
  }
  struct Morse {void beginCommand(const uint8_t*) {} void endCommand() {}} morse;
  void remoteCommand(ClientInfo* client,uint32_t sender_timestamp,const char* body,char* reply) {
    uint8_t temp[192]={};char text[160];strncpy(text,body,sizeof(text)-1);text[sizeof(text)-1]=0;
    const int i=0;
    struct Packet {uint8_t getPathHashSize() const {return 1;}} real_packet;
    Packet* packet=&real_packet;
    @ADMIN_GATE@
    strcpy(reply,reinterpret_cast<char*>(&temp[5]));
  }
};
@ROOM_METHODS@
std::string cli(MyMesh& room,const char* command) {
  char body[160]={},reply[160]={};strncpy(body,command,sizeof(body)-1);
  room.handleCommand(0,body,reply);return reply;
}
void has(const std::string& text,const char* wanted) {assert(text.find(wanted)!=std::string::npos);}
std::vector<uint8_t> entries(const uint8_t* data,size_t n) {
  assert(LPPData::boundedPrefix(data,n,n)==n);std::vector<uint8_t> channels;
  for(size_t i=0;i<n;i+=2+LPPData::getDataSize(data[i+1]))channels.push_back(data[i]);
  return channels;
}
bool present(const std::vector<uint8_t>& channels,uint8_t wanted) {
  return std::find(channels.begin(),channels.end(),wanted)!=channels.end();
}
int main(int argc,char** argv) {
  assert(argc==2);std::string scenario=argv[1];MockFS fs;MyMesh room;
  beginSettings(room.board,&fs,true);
  if(scenario=="history-late-detection") {
    Wire.ina[0xfe]=0;room.configureHistory();assert(!Wire.calls);
    assert(room.external_voltage_history.channelCount()==4+ROOM_OPTIONAL_SENSOR_COUNT);
    assert(!room.external_voltage_history.populatedChannelCount());
    room.sampleTelemetryHistory();assert(!Wire.calls);
    assert(room.telemetry_history.voltageSampleCount()==1);
    assert(!room.external_voltage_history.channelHasValue(2));
    assert(room.external_voltage_history.channelHasValue(14)==bool(ROOM_OPTIONAL_SENSOR_COUNT));
    now_ms=100;service();assert(!selection.nativeAllowed());
    for(auto item:Wire.log)assert(item.address==0x40 && (item.reg==0xfe || item.reg==0xff));
    Wire.ina[0xfe]=0x5449;Wire.charger[0x4a]=0;
    now_ms=5100;service();assert(selection.nativeAllowed());
    room.clock.epoch=1800;room.sampleTelemetryHistory();
    assert(room.external_voltage_history.channelHasValue(2));
    assert(!room.external_voltage_history.channelHasValue(3));
    Wire.charger[0x4a]=1;room.clock.epoch=3600;room.sampleTelemetryHistory();
    assert(room.external_voltage_history.channelHasValue(3));
    const auto samples=room.external_voltage_history.sampleCount();
    cli(room,"set solar.board basic");const auto calls=Wire.calls;
    room.clock.epoch=5400;room.sampleTelemetryHistory();assert(Wire.calls==calls);
    assert(room.external_voltage_history.channelHasValue(2));
    assert(room.external_voltage_history.sampleCount()==samples+1);
    uint8_t source[8]={},snapshot[184];
    const size_t n=room.external_voltage_history.formatBinarySnapshot(source,2,0,snapshot,sizeof(snapshot));
    assert(n>20 && snapshot[19]==4);
    auto code=[&](unsigned item) {
      uint16_t value=0;for(unsigned bit=0;bit<15;++bit) {
        const unsigned at=item*15+bit;value=(value<<1)|((snapshot[20+at/8]>>(7-at%8))&1);
      }return value;
    };
    assert(code(0)==0 && mesh::ExternalVoltageHistory::decodeMillivolts(code(1))==13200);
    assert(mesh::ExternalVoltageHistory::decodeMillivolts(code(2))==13200 && code(3)==0);
    cli(room,"set solar.board mppt");Wire.ina[2]=10720;
    room.clock.epoch=7200;room.sampleTelemetryHistory();
    assert(room.external_voltage_history.sampleCount()==samples+2);
    assert(room.external_voltage_history.formatBinarySnapshot(source,2,0,snapshot,sizeof(snapshot))>20);
    assert(snapshot[19]==5 && mesh::ExternalVoltageHistory::decodeMillivolts(code(4))==13400);
    assert(room.external_voltage_history.channelHasValue(14)==bool(ROOM_OPTIONAL_SENSOR_COUNT));
    char reply[160]={};assert(room.handleTelemetryHistoryCommand("get telemetry.volt.i2c",reply));
    assert(strstr(reply,"2")!=nullptr);
  } else if(scenario=="telemetry-permissions-bounds") {
    ClientInfo client;uint8_t payload[]={REQ_TYPE_GET_TELEMETRY_DATA,0};
    now_ms=100;service();assert(selection.nativeAllowed());
    for(uint8_t role:{PERM_ACL_GUEST,PERM_ACL_READ_ONLY,PERM_ACL_READ_WRITE,PERM_ACL_ADMIN}) {
      client.permissions=role;
      for(uint8_t disabled:{uint8_t(0),uint8_t(TELEM_PERM_ENVIRONMENT)}) {
        payload[1]=disabled;
        for(size_t cap=4;cap<=184;++cap) {
          memset(room.reply_data,0xa5,sizeof(room.reply_data));
          const auto calls=Wire.calls;
          const int n=room.handleRequest(&client,123,payload,sizeof(payload),cap);
          assert(n>=4 && size_t(n)<=cap);
          auto channels=entries(room.reply_data+4,n-4);
          const bool env=role!=PERM_ACL_GUEST && !disabled;
          if(!env)assert(Wire.calls==calls);
          if(cap==184) {
            assert(present(channels,1));assert(present(channels,2)==env);
            assert(present(channels,13)==env);
            assert(present(channels,14)==(env && ROOM_OPTIONAL_SENSOR_COUNT));
          }
          for(size_t i=n;i<sizeof(room.reply_data);++i)assert(room.reply_data[i]==0xa5);
        }
      }
    }
    assert(!room.handleRequest(&client,123,payload,1,184));
    cli(room,"set solar.board basic");client.permissions=PERM_ACL_ADMIN;payload[1]=0;
    const auto calls=Wire.calls;const int n=room.handleRequest(&client,123,payload,2,184);
    auto channels=entries(room.reply_data+4,n-4);assert(present(channels,1) && !present(channels,2));
    assert(Wire.calls==calls);
  } else if(scenario=="admin-cli") {
    uint8_t key[32]={};(void)key;
    for(uint8_t role:{PERM_ACL_GUEST,PERM_ACL_READ_ONLY,PERM_ACL_READ_WRITE,PERM_ACL_ADMIN}) {
      ClientInfo client;client.permissions=role;char reply[160]={};
      const auto writes=fs.writes;
      room.remoteCommand(&client,100,"set solar.board basic",reply);
      if(role==PERM_ACL_ADMIN)assert(fs.writes>writes && selection.isBasic());
      else {assert(fs.writes==writes);has(reply,"admin permission required");}
    }
    ClientInfo client;char reply[160]={};room.remoteCommand(&client,0,"mppt ship arm",reply);
    assert(!Wire.writes);
    cli(room,"set solar.board mppt");room.remoteCommand(&client,0,"mppt ship arm",reply);
    has(reply,"requires local USB");assert(!Wire.writes);
    std::vector<char> guard(176,'~');
    char command[]="xy|get solar capabilities";room.handleCommand(0,command,guard.data()+8);
    assert(!memcmp(guard.data()+8,"xy|",3));
    for(unsigned i=0;i<8;++i)assert(guard[i]=='~' && guard[168+i]=='~');
  } else assert(false);
}
'''


class SolarXiaoRoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not cls.compiler:
            raise unittest.SkipTest("host C++11 compiler required")
        cls.temp = tempfile.TemporaryDirectory(prefix="solarxiao-room-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        (directory / "helpers").mkdir()
        (directory / "helpers/IdentityStore.h").write_text(FILESYSTEM_WITH_METADATA, encoding="ascii")
        (directory / "helpers/SensorManager.h").write_text(SENSOR_BOUNDARY, encoding="ascii")
        (directory / "Wire.h").write_text(WIRE, encoding="ascii")
        (directory / "Packet.h").write_text(
            "#pragma once\n#include <cstdint>\n#include <cstddef>\n"
            "#define MAX_PACKET_PAYLOAD 184\n#define CIPHER_MAC_SIZE 2\n#define CIPHER_BLOCK_SIZE 16\n"
            "namespace mesh {class Packet {public: static bool isValidPathLen(uint8_t);};}\n",
            encoding="ascii")
        room = (ROOM / "MyMesh.cpp").read_text()
        env = (SENSORS / "EnvironmentSensorManager.cpp").read_text()
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        begin = extract_braced(room, "void MyMesh::begin(")
        history_start = begin.index("#if MESH_ENABLE_TELEMETRY_HISTORY\n  uint8_t voltage_channels")
        history_init = conditional_block(begin[history_start:], "#if MESH_ENABLE_TELEMETRY_HISTORY")
        handler = extract_braced(room, "int MyMesh::handleRequest(")
        prelude = handler[:handler.index("  if (payload[0] == mesh::ROOM_MAIL_REQUEST_SUBTYPE)")]
        request = prelude + extract_braced(handler, "if (payload[0] == REQ_TYPE_GET_TELEMETRY_DATA)") + "\nreturn 0;\n}\n"
        command = extract_braced(room, "void MyMesh::handleCommand(uint32_t sender_timestamp,")
        timestamp_guard = re.search(r"if \(gpio_client_index >= 0 && sender_timestamp == 0\) sender_timestamp = 1;", command).group()
        prefix = extract_braced(command, "if (strlen(command) > 4 && command[2] == '|')")
        receive = extract_braced(room, "void MyMesh::onPeerDataRecv(")
        gate = extract_braced(receive[receive.index("if (!handleClientPathCommand"):], "if (client->isAdmin())")
        end = receive[receive.index(gate) + len(gate):]
        otherwise = extract_braced(end, "else")
        gate += " " + otherwise
        gate = gate.replace("_cli.morseReply().beginCommand(client->id.pub_key);", "morse.beginCommand(nullptr);")
        gate = gate.replace("_cli.morseReply().endCommand();", "morse.endCommand();")
        # Only the unrelated Morse reply tracker is replaced. Keep the actual
        # admin gate, remote timestamp and room command dispatch unchanged.
        replacements = {
            "@ADAPTER@": str(SENSORS / "SolarXiaoPower.cpp"),
            "@ENV_METHODS@": "\n".join(extract_braced(env, signature) for signature in (
                "bool EnvironmentSensorManager::querySensors(",
                "uint8_t EnvironmentSensorManager::getVoltageSensorChannels(",
                "uint8_t EnvironmentSensorManager::queryVoltageSensors(")),
            "@ROOM_METHODS@": "\n".join((request,
                extract_braced(room, "void MyMesh::sampleTelemetryHistory("),
                extract_braced(room, "bool MyMesh::handleTelemetryHistoryCommand("))),
            "@ROOM_TELEMETRY_CAPACITY@": re.search(r"\btelemetry\(([^)\n]+)\)", room).group(1),
            "@HISTORY_INIT@": history_init,
            "@COMMON_HOOK@": conditional_block(common, "#if defined(MESH_SOLARXIAO_POWER) && MESH_SOLARXIAO_POWER"),
            "@TIMESTAMP_GUARD@": timestamp_guard,
            "@PREFIX@": prefix,
            "@ADMIN_GATE@": gate,
        }
        harness = HISTORY_HARNESS
        for marker, value in replacements.items():
            harness = harness.replace(marker, value)
        cls.binaries = {}
        for optional in (0, 1):
            cpp = directory / ("room" + str(optional) + ".cpp")
            cpp.write_text(harness, encoding="ascii")
            binary = directory / ("room" + str(optional))
            built = subprocess.run([cls.compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DNRF52_PLATFORM", "-DMESH_SOLARXIAO_POWER=1", "-DMESH_ENABLE_TELEMETRY_HISTORY=1",
                "-DMAX_PACKET_PAYLOAD=184", "-DHIGH=1", "-DPIN_BOARD_SDA=30", "-DPIN_BOARD_SCL=31",
                "-DROOM_OPTIONAL_SENSOR_COUNT=" + str(optional),
                "-I" + str(directory), "-I" + str(ROOT / "src"), "-I" + str(SENSORS), str(cpp),
                str(SENSORS / "SolarXiaoCarrier.cpp"), str(SENSORS / "SolarXiaoPowerCore.cpp"),
                "-o", str(binary)], text=True, capture_output=True, timeout=45)
            if built.returncode:
                raise AssertionError(built.stdout + built.stderr)
            cls.binaries[optional] = binary

    def scenario(self, name):
        for optional, binary in self.binaries.items():
            with self.subTest(optional_sensor=bool(optional)):
                result = subprocess.run([str(binary), name], text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_room_history_survives_late_detection_delayed_charger_and_mode_changes(self):
        self.scenario("history-late-detection")

    def test_actual_room_live_telemetry_respects_permissions_and_route_bounds(self):
        self.scenario("telemetry-permissions-bounds")

    def test_actual_room_admin_gate_remote_local_distinction_and_prefixed_reply_bounds(self):
        self.scenario("admin-cli")

    def test_actual_room_boot_and_loop_hooks_propagate_persistent_store_and_history(self):
        main = (ROOM / "main.cpp").read_text()
        setup = extract_braced(main, "void setup()")
        # Conditional Ethernet branches each open the same logical block, so
        # the raw loop has unbalanced braces before preprocessing.
        loop = main[main.index("\nvoid loop()") :]
        start = setup.index("FILESYSTEM* regulator_prefs_fs = fs;")
        settings = setup[start:setup.index("#ifdef DISPLAY_CLASS", start)]
        start = loop.index("mesh::power::serviceRegulatorSettings();")
        periodic = loop[start:loop.index("board.loop();", start) + len("board.loop();")]
        self.assertIn("#include <helpers/TelemetryHistoryConfig.h>", (ROOM / "MyMesh.h").read_text())
        history = conditional_block((ROOT / "src/helpers/TelemetryHistoryConfig.h").read_text(),
                                    "#ifndef MESH_ENABLE_TELEMETRY_HISTORY")
        self.assertLess(setup.index("board.begin()"), setup.index("mesh::solar::beginSettings"))
        self.assertLess(setup.index("mesh::solar::beginSettings"), setup.index("sensors.begin()"))
        self.assertLess(setup.index("mesh::solar::beginSettings"), setup.index("radio_init()"))
        harness = r'''
#include <cassert>
#include <vector>
#define MESH_DEBUG_PRINTLN(...) do {} while(0)
@HISTORY@
struct Store {};
#define FILESYSTEM Store
static unsigned solar_begin=0,solar_service=0,reg_begin=0,reg_service=0,board_loops=0;
static std::vector<int> order;
static Store* selected=nullptr;static bool actual_history=false;
struct Board {void loop() {++board_loops;order.push_back(3);}} board;
struct Radio {} radio_driver;
namespace mesh {
struct RadioProfileCLI {static void configureBootTuning(Store*,Radio&) {}};
namespace leds {void beginSettings(Board&,Store*) {}}
namespace power {
bool beginRegulatorSettings(Board&,Radio&,Store*) {++reg_begin;return true;}
void serviceRegulatorSettings() {++reg_service;order.push_back(1);}
}
namespace solar {
void beginSettings(Board&,Store* fs,bool history) {++solar_begin;selected=fs;actual_history=history;}
void service() {++solar_service;order.push_back(2);}
}
}
void initialize(Store* fs,bool volatile_primary_fs) {
  @SETTINGS@
}
void periodic() {@PERIODIC@}
int main() {
  Store fs;
  for(bool volatile_store:{false,true}) {
    initialize(&fs,volatile_store);
    assert(reg_begin==solar_begin || !MESH_SOLARXIAO_POWER);
    if(MESH_SOLARXIAO_POWER) {
      assert(selected==(volatile_store?nullptr:&fs));
      assert(actual_history==bool(MESH_ENABLE_TELEMETRY_HISTORY));
    }
    order.clear();periodic();
    assert(order==std::vector<int>(MESH_SOLARXIAO_POWER?std::initializer_list<int>{1,2,3}:std::initializer_list<int>{1,3}));
  }
  assert(reg_begin==2 && reg_service==2 && board_loops==2);
  assert(solar_begin==2*MESH_SOLARXIAO_POWER && solar_service==2*MESH_SOLARXIAO_POWER);
}
'''.replace("@HISTORY@", history).replace("@SETTINGS@", settings).replace("@PERIODIC@", periodic)
        directory = Path(self.temp.name)
        cpp = directory / "hooks.cpp"
        cpp.write_text(harness, encoding="ascii")
        for enabled, history_enabled in ((0, None), (1, None), (1, 0)):
            with self.subTest(carrier_feature=enabled, history=history_enabled):
                binary = directory / "hooks"
                flags = ["-DMESH_SOLARXIAO_POWER=" + str(enabled), "-DNRF52_PLATFORM"]
                if history_enabled is not None:
                    flags.append("-DMESH_ENABLE_TELEMETRY_HISTORY=" + str(history_enabled))
                built = subprocess.run([self.compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                    *flags, str(cpp), "-o", str(binary)], text=True, capture_output=True, timeout=30)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_feature_is_inherited_by_canonical_room_ota_targets_and_leaves_recovery_unchanged(self):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read([ROOT / "platformio.ini", *sorted((ROOT / "variants").glob("*/platformio.ini"))])

        def option(section, key, stack=()):
            pair = (section, key)
            if pair in stack:
                raise AssertionError("recursive PlatformIO option: " + repr(pair))
            value = config.get(section, key, fallback=None)
            if value is None:
                parents = config.get(section, "extends", fallback="").replace(",", " ").split()
                return "\n".join(option(parent, key, stack + (pair,)) for parent in parents)

            def replace(match):
                reference_section, reference_key = match.group(1).rsplit(".", 1)
                return option(reference_section, reference_key, stack + (pair,))

            return re.sub(r"\$\{([^}]+)\}", replace, value)

        for radio in ("30S", "33S"):
            for suffix, enabled in (("_room_server_lora_ota", True), ("_room_server", False),
                                    ("_companion_radio_ble", False)):
                target = "env:solarxiao_" + radio + suffix
                with self.subTest(target=target):
                    flags = option(target, "build_flags")
                    present = bool(re.search(r"-D\s*MESH_SOLARXIAO_POWER=1\b", flags))
                    self.assertEqual(present, enabled)
                    if enabled:
                        self.assertIn("+<helpers/sensors>", option(target, "build_src_filter"))
                        self.assertIn("+<../examples/simple_room_server/*.cpp>", option(target, "build_src_filter"))
                        self.assertIn("-D OTA_QSPI_STORE=1", flags)


if __name__ == "__main__":
    unittest.main()
