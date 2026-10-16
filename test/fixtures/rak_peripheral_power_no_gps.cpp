#include <helpers/sensors/EnvironmentSensorManager.h>
#include <helpers/sensors/I2CAddressClaimPolicy.h>
#include <cassert>
#include <cstring>
#include <initializer_list>

#define WB_IO2 21
#define TELEM_WIRE (&Wire)
#define MESH_DEBUG_PRINTLN(...) ((void)0)

void LocationProvider::sendSentence(const char*) {}

namespace mesh { namespace ota {
struct OtaStoreQspiNrf52 {
  inline static unsigned probes=0;
  inline static uint32_t raw_id=0;
  static uint8_t autoDetect() { ++probes; assert(digitalRead(WB_IO2)==HIGH); return 0; }
  static void autoProbeIds(uint32_t& rak,uint32_t& w25) { rak=raw_id; w25=0; }
};
}}

static bool ensureI2cBusReleased(TwoWire*) { return true; }
struct SensorDef {
  uint32_t address;
  const char* name;
  uint8_t (*init)(TwoWire*,uint8_t);
  void (*query)(uint8_t,uint8_t,CayenneLPP&);
  bool (*query_voltage)(uint8_t,float&);
};
static const SensorDef SENSOR_TABLE[1] = {};
static const size_t SENSOR_TABLE_SIZE=0;
bool EnvironmentSensorManager::i2c_probe(TwoWire&,uint8_t) { return false; }
@METHODS@

struct TestEnvironment : EnvironmentSensorManager {
  using EnvironmentSensorManager::_wisblock_power;
};

int main(int argc,char** argv) {
  assert(argc==2&&!std::strcmp(argv[1],"no_gps"));
  for(bool prefs_first:{false,true}) {
    for(bool shared:{false,true}) {
      for(uint32_t id:{0U,0xffffffU,0x123456U}) {
        now_ms=100; hardware_events.clear();
        std::memset(pin_values,0,sizeof(pin_values)); pin_values[WB_IO2]=HIGH;
        mesh::ota::OtaStoreQspiNrf52::raw_id=id;
        mesh::ota::OtaStoreQspiNrf52::probes=0;
        TestEnvironment sensors;
        assert(!sensors.canRemoveSharedStoragePowerForDeepSleep());
        if(prefs_first) {
          sensors.configureSharedPeripheralMode(shared);
          assert(sensors._wisblock_power.discoveryActive());
          assert(!sensors.canRemoveSharedStoragePowerForDeepSleep());
          assert(digitalRead(WB_IO2)==HIGH);
        }
        assert(sensors.begin());
        if(!prefs_first) {
          // Even a no-GPS image must wait for validated mode after discovery.
          assert(digitalRead(WB_IO2)==HIGH);
          assert(!sensors.canRemoveSharedStoragePowerForDeepSleep());
          sensors.configureSharedPeripheralMode(shared);
        }
        assert(sensors._wisblock_power.isConfigured()&&!sensors._wisblock_power.discoveryActive());
        const bool storage=id!=0&&id!=0xffffffU;
        const bool held=shared||storage||mesh::gps::wisBlockCompileRailHolds()!=0;
        assert(digitalRead(WB_IO2)==int(held));
        assert(bool(sensors._wisblock_power.holds()&mesh::gps::ExternalStorage)==storage);
        const unsigned probes=mesh::ota::OtaStoreQspiNrf52::probes;
        const size_t events=hardware_events.size();
        assert(sensors.canRemoveSharedStoragePowerForDeepSleep()==!storage);
        assert(sensors.canRemoveSharedStoragePowerForDeepSleep()==!storage);
        assert(mesh::ota::OtaStoreQspiNrf52::probes==probes&&hardware_events.size()==events);
        assert(mesh::ota::OtaStoreQspiNrf52::probes>0);
        assert(!sensors.isGPSDetected()&&!sensors.isGpsPowered());
        assert(!sensors.setSettingValue("gps","1"));
        assert(!sensors.gpsUsesSerialUart(1)&&!sensors.gpsSerialTransportCanYield(1));
        assert(!sensors.setGpsSerialTransportBlocked(1,true));
        if(held) {
          for(const auto& event:hardware_events)
            assert(!(event.name=="write"&&event.a==WB_IO2&&event.b==LOW));
        }
      }
    }
  }
}
