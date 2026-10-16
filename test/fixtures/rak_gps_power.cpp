#include <helpers/sensors/EnvironmentSensorManager.h>
#include <helpers/sensors/I2CAddressClaimPolicy.h>
#include <helpers/sensors/NmeaSentenceProbe.h>
#include <helpers/sensors/MicroNMEALocationProvider.h>
#include <cassert>
#include <cstring>
#include <cstdio>
#include <initializer_list>

#define WB_IO2 21
#define PIN_GPS_TX 15
#define PIN_GPS_RX 16
#define TELEM_RAK12500_ADDRESS 0x42
#define RAK_UART_GPS_PROBE_TIMEOUT_MS 1200UL
#define TELEM_WIRE (&Wire)
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define POWERSAVING_DEBUG_PRINTLN(...) ((void)0)
#define COM_TYPE_UBX 1
#define SFE_UBLOX_GNSS_ID_GPS 0
#define SFE_UBLOX_GNSS_ID_GALILEO 1
#define SFE_UBLOX_GNSS_ID_GLONASS 2
#define SFE_UBLOX_GNSS_ID_SBAS 3
#define SFE_UBLOX_GNSS_ID_BEIDOU 4
#define SFE_UBLOX_GNSS_ID_IMES 5
#define SFE_UBLOX_GNSS_ID_QZSS 6
#define VAL_CFG_SUBSEC_IOPORT 1

void LocationProvider::sendSentence(const char*) {}
static uint32_t gpsResetPin = static_cast<uint32_t>(-1);
static bool i2cGPSFlag = false, serialGPSFlag = false;
static const char meshcore_capability_rak_wisblock_gps[] = "linked-provider";

struct SerialMock : Stream {
  unsigned begins=0, ends=0, polls=0;
  bool installed=false;
  std::deque<uint8_t> incoming;
  void setPins(int tx,int rx) { event("uartPins",tx,rx); }
  void begin(unsigned baud) { installed=true; ++begins; event("uartBegin",baud); }
  void end() { assert(installed&&"pinned UARTE end must not be called twice"); installed=false; ++ends; event("uartEnd"); }
  int available() override { ++polls; return installed ? int(incoming.size()) : 0; }
  int read() override { if(incoming.empty()) return -1; int c=incoming.front(); incoming.pop_front(); return c; }
  void nmea() {
    const char* body="GPRMC,valid";
    uint8_t checksum=0;
    for(const char* c=body;*c;++c) checksum^=uint8_t(*c);
    char sentence[64]; std::snprintf(sentence,sizeof(sentence),"$%s*%02X\r\n",body,checksum);
    for(const char* c=sentence;*c;++c) incoming.push_back(uint8_t(*c));
  }
} Serial1;

namespace mesh { namespace ota {
struct OtaStoreQspiNrf52 {
  inline static uint8_t selected=0;
  inline static uint32_t rak_id=0,w25_id=0;
  inline static bool header_present=false;
  inline static bool cached=false;
  inline static unsigned probes=0;
  static bool headerW25Detected() { autoDetect(); event("headerProbe"); return header_present; }
  static uint8_t autoDetect() { if(!cached) { cached=true; ++probes; event("norProbe"); assert(digitalRead(WB_IO2)==HIGH); } return selected; }
  static void autoProbeIds(uint32_t& rak,uint32_t& w25) { autoDetect(); rak=rak_id; w25=w25_id; }
};
}}

static bool bus_available=true;
static bool ensureI2cBusReleased(TwoWire*) { event("i2cBus"); return bus_available; }
struct GnssMock {
  bool present=false, begin_ok=true, output_ok=true, rate_ok=true, psm_ok=true, valid=false;
  unsigned begins=0, polls=0, flushes=0, output_calls=0, rate_calls=0, nv_writes=0;
  std::vector<bool> psm;
  bool begin(TwoWire&,uint8_t address) { ++begins; event("gnssBegin",address); return present&&begin_ok; }
  bool powerSaveMode(bool enabled) { psm.push_back(enabled); event("gnssPsm",enabled); return psm_ok; }
  bool setI2COutput(unsigned output) { ++output_calls; event("gnssOutput",output); return output_ok; }
  void enableGNSS(bool,unsigned) {}
  bool setMeasurementRate(unsigned rate) { ++rate_calls; event("gnssRate",rate); return rate_ok; }
  void saveConfigSelective(unsigned) { ++nv_writes; }
  void flushPVT() { ++flushes; }
  bool getPVT(unsigned) { ++polls; return valid; }
  bool getGnssFixOk(unsigned) { ++polls; return valid; }
  long getLatitude(unsigned) { return 476100000; }
  long getLongitude(unsigned) { return -1223300000; }
  long getAltitude(unsigned) { return 125000; }
  int getSIV(unsigned) { return valid?8:0; }
  bool getDateValid(unsigned) { return valid; }
  bool getTimeValid(unsigned) { return valid; }
  long getUnixEpoch(unsigned) { return 1790942400; }
} ublox_GNSS;

@HELPERS@
@PROVIDER@
static RAK12500LocationProvider RAK12500_provider;

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

struct TestLocation : LocationProvider {
  unsigned loops=0,resets=0,clears=0;
  int pin=-1;
  mesh::RTCClock clock;
  bool valid=false;
  void reset() override { ++resets; valid=false; }
  void begin() override {}
  void stop() override {}
  void loop() override { ++loops; }
  bool isEnabled() override { return pin<0 || digitalRead(pin)==HIGH; }
  bool clearPositionFix() override { valid=false; ++clears; return true; }
  void setPinEn(int value) override { pin=value; }
  int getPinEn() override { return pin; }
  mesh::RTCClock* getRTCClock() override { return &clock; }
  long getLatitude() override { return 47610000; }
  long getLongitude() override { return -122330000; }
  long getAltitude() override { return 125000; }
  long satellitesCount() override { return valid?8:0; }
  bool isValid() override { return valid; }
  long getTimestamp() override { return 1790942400; }
  void acceptedClock() { markTimeSyncApplied(); stopTimeSync(); }
};

struct TestEnvironment : EnvironmentSensorManager {
  explicit TestEnvironment(TestLocation& location):EnvironmentSensorManager(location) {}
  bool active() const { return gps_active; }
  bool userEnabled() const { return isGpsTelemetryUserEnabled(); }
  bool transportAvailable() const { return isGpsTelemetryTransportAvailable(); }
  using EnvironmentSensorManager::start_gps;
  using EnvironmentSensorManager::stop_gps;
  using EnvironmentSensorManager::rakGPSInit;
  using EnvironmentSensorManager::_wisblock_power;
};

static constexpr bool compile_held = mesh::gps::wisBlockCompileRailHolds()!=0;
static void fresh() {
  now_ms=100; delay_count=0; hardware_events.clear();
  std::memset(pin_values,0,sizeof(pin_values)); pin_values[WB_IO2]=HIGH;
  Serial1=SerialMock{}; ublox_GNSS=GnssMock{}; RAK12500_provider=RAK12500LocationProvider{};
  mesh::ota::OtaStoreQspiNrf52::selected=0;
  mesh::ota::OtaStoreQspiNrf52::rak_id=mesh::ota::OtaStoreQspiNrf52::w25_id=0;
  mesh::ota::OtaStoreQspiNrf52::header_present=false;
  mesh::ota::OtaStoreQspiNrf52::cached=false;
  mesh::ota::OtaStoreQspiNrf52::probes=0;
  i2cGPSFlag=serialGPSFlag=false; gpsResetPin=uint32_t(-1); bus_available=true;
}
static size_t firstEvent(const char* name,int a=-999,int b=-999,size_t after=0) {
  for(size_t i=after;i<hardware_events.size();++i) {
    const auto& e=hardware_events[i];
    if(e.name==name&&(a==-999||e.a==a)&&(b==-999||e.b==b)) return i;
  }
  return hardware_events.size();
}
static void bootUart(TestEnvironment& sensors,TestLocation& location,bool shared=false) {
  Serial1.nmea(); assert(sensors.begin());
  assert(sensors.isGPSDetected()&&sensors.active()&&!sensors.userEnabled());
  assert(location.pin<0 && sensors.isGpsPowered());
  assert(digitalRead(WB_IO2)==HIGH);
  sensors.configureSharedPeripheralMode(shared);
  assert(sensors._wisblock_power.isConfigured()&&!sensors._wisblock_power.discoveryActive());
  assert(sensors.setSettingValue("gps","0"));
  assert(!sensors.active()&&!sensors.userEnabled()&&!Serial1.installed);
}
static void requireNoLow() {
  for(const auto& e:hardware_events) assert(!(e.name=="write"&&e.a==WB_IO2&&e.b==LOW));
}
static void requireUartReleasedBeforePowerOff(int pin=WB_IO2,int value=LOW) {
  const size_t end=firstEvent("uartEnd");
  const size_t tx=firstEvent("pinMode",PIN_GPS_TX,INPUT,end);
  const size_t rx=firstEvent("pinMode",PIN_GPS_RX,INPUT,end);
  const size_t off=firstEvent("write",pin,value,end);
  assert(off<hardware_events.size());
  assert(end<tx&&tx<off&&end<rx&&rx<off);
}
static void uart(bool shared) {
  fresh(); TestLocation location; TestEnvironment sensors(location); bootUart(sensors,location,shared);
  const bool held=shared||compile_held;
  assert(sensors.isGpsPowered()==held && digitalRead(WB_IO2)==int(held));
  if(!held) requireUartReleasedBeforePowerOff();
  else requireNoLow();
  assert(sensors.gpsUsesSerialUart(1)&&sensors.gpsSerialTransportMayConflict(1));
  assert(!sensors.gpsSerialTransportCanYield(1));
  assert(!sensors.gpsUsesSerialUart(2)&&!sensors.gpsSerialTransportMayConflict(2));
  const unsigned begins=Serial1.begins,ends=Serial1.ends,loops=location.loops;
  assert(!sensors.setGpsSerialTransportBlocked(1,true));
  assert(!sensors.setGpsSerialTransportBlocked(2,true));
  assert(sensors.transportAvailable()&&Serial1.begins==begins&&Serial1.ends==ends);
  now_ms+=1000; sensors.loop(); assert(location.loops==loops&&!sensors.active());
  hardware_events.clear(); const uint32_t started=millis();
  assert(sensors.setSettingValue("gps","1"));
  assert(sensors.active()&&sensors.userEnabled()&&Serial1.installed&&sensors.isGpsPowered());
  const size_t high=firstEvent("write",WB_IO2,HIGH),opened=firstEvent("uartBegin");
  assert(high<opened);
  assert(hardware_events[opened].time>=started+(held?0:500));
  assert(location.clears==1&&location.pin<0);
  sensors.setPowerSavingEnabled(false); now_ms+=600001; sensors.loop();
  assert(sensors.active());  // Manual on plus power-saving off is continuous.
  hardware_events.clear(); assert(sensors.setSettingValue("gps","0"));
  assert(!sensors.active()&&!Serial1.installed&&sensors.isGpsPowered()==held);
  if(!held) requireUartReleasedBeforePowerOff();
  else requireNoLow();
  sensors.setPowerSavingEnabled(true); now_ms+=1000; sensors.loop();
  sensors.setPowerSavingEnabled(false); assert(!sensors.active());
  const unsigned saved_begins=Serial1.begins;
  sensors.configureSharedPeripheralMode(!shared);  // Boot-only mode cannot revoke/reconfigure owners.
  assert(sensors.isGpsPowered()==held&&Serial1.begins==saved_begins);
}
static void hourly() {
  fresh(); TestLocation location; TestEnvironment sensors(location); bootUart(sensors,location);
  sensors.applyGpsTimeSyncInterval(1); sensors.setPowerSavingEnabled(true);
  assert(sensors.setSettingValue("gps","1"));
  assert(sensors.active()&&location.getGPSPowerSaving());
  assert(location.getNextGPSOff()!=0);
  now_ms+=100; location.acceptedClock(); sensors.loop();
  assert(!sensors.active()&&!sensors.isGpsPowered());
  const uint32_t deadline=uint32_t(location.getNextGPSOn());
  assert(deadline==millis()+3600000U);
  now_ms=deadline-1; sensors.loop(); assert(!sensors.active());
  now_ms=deadline; hardware_events.clear(); sensors.loop();
  assert(sensors.active()&&Serial1.installed);
  assert(firstEvent("write",WB_IO2,HIGH)<firstEvent("uartBegin"));
  // Explicit off cancels both clock/duty deadlines and must not be undone by them.
  assert(sensors.setSettingValue("gps","0"));
  assert(!location.getGPSPowerSaving()&&location.getNextGPSOn()==0&&location.getNextGPSOff()==0);
  const unsigned begins=Serial1.begins; now_ms+=3600100; sensors.loop();
  assert(!sensors.active()&&!sensors.isGpsPowered()&&Serial1.begins==begins);
}
static void repeatedDiscovery() {
  fresh(); TestLocation location; TestEnvironment sensors(location); bootUart(sensors,location);
  hardware_events.clear(); Serial1.nmea();
  assert(sensors.begin());
  assert(!sensors.active()&&!sensors.userEnabled()&&!sensors.isGpsPowered());
  assert(!Serial1.installed&&!sensors._wisblock_power.discoveryActive());
  assert(firstEvent("write",WB_IO2,HIGH)<firstEvent("uartBegin"));
  requireUartReleasedBeforePowerOff();
  assert(sensors.setSettingValue("gps","1"));
  hardware_events.clear(); Serial1.nmea(); assert(sensors.begin());
  assert(sensors.active()&&sensors.userEnabled()&&sensors.isGpsPowered()&&Serial1.installed);
  requireNoLow();
}
static void preferenceBeforeDiscovery() {
  for(bool shared:{false,true}) {
    for(bool enabled:{false,true}) {
      fresh(); TestLocation location; TestEnvironment sensors(location);
      sensors.configureSharedPeripheralMode(shared);
      // Companion loads mode before sensors.begin(): saved GPS-only must not
      // cut supply until the first discovery pass has actually completed.
      assert(sensors._wisblock_power.isConfigured());
      assert(sensors._wisblock_power.discoveryActive());
      assert(sensors.isGpsPowered()&&digitalRead(WB_IO2)==HIGH); requireNoLow();
      Serial1.nmea(); assert(sensors.begin());
      assert(sensors.isGPSDetected()&&sensors.active()&&!sensors.userEnabled());
      assert(sensors.isGpsPowered()&&Serial1.installed); requireNoLow();
      assert(!sensors._wisblock_power.discoveryActive());
      assert(sensors.setSettingValue("gps",enabled?"1":"0"));
      assert(sensors.active()==enabled&&sensors.userEnabled()==enabled);
      const bool powered=enabled||shared||compile_held;
      assert(sensors.isGpsPowered()==powered&&digitalRead(WB_IO2)==int(powered));
      if(!powered) requireUartReleasedBeforePowerOff();
      else requireNoLow();
    }
  }
}
static void actualPoweredStatus() {
  fresh(); TestLocation location; TestEnvironment sensors(location); bootUart(sensors,location);
  assert(!sensors.isGpsPowered());
  // Status must read the applied GPIO, not a provider whose absent enable pin
  // means 'always powered', and not merely the desired policy state.
  assert(location.isEnabled());
  pin_values[WB_IO2]=HIGH; assert(sensors.isGpsPowered());
  pin_values[WB_IO2]=LOW; assert(!sensors.isGpsPowered());
}
static void absent() {
  fresh(); TestLocation location; TestEnvironment sensors(location);
  assert(sensors.begin()); assert(!sensors.isGPSDetected()&&!sensors.active());
  assert(digitalRead(WB_IO2)==HIGH&&!sensors._wisblock_power.isConfigured());
  requireNoLow(); sensors.configureSharedPeripheralMode(false);
  assert(digitalRead(WB_IO2)==int(compile_held));
  const unsigned polls=Serial1.polls,begins=Serial1.begins;
  now_ms+=1000; sensors.loop(); assert(Serial1.polls==polls&&Serial1.begins==begins);
  assert(!sensors.setSettingValue("gps","1"));
  assert(!sensors.gpsSerialTransportCanYield(1)&&sensors.gpsSerialTransportMayConflict(1));
}
static void storage() {
  fresh(); TestLocation location; TestEnvironment sensors(location);
  mesh::ota::OtaStoreQspiNrf52::header_present=true;
  mesh::ota::OtaStoreQspiNrf52::selected=2;
  mesh::ota::OtaStoreQspiNrf52::w25_id=0xef4015;
  assert(sensors.begin()); assert(!sensors.isGPSDetected()&&Serial1.begins==0);
  sensors.configureSharedPeripheralMode(false);
  assert(sensors._wisblock_power.holds()&mesh::gps::ExternalStorage);
  assert(!sensors.canRemoveSharedStoragePowerForDeepSleep());
  assert(digitalRead(WB_IO2)==HIGH); requireNoLow();
  sensors.configureSharedPeripheralMode(true);
  assert(digitalRead(WB_IO2)==HIGH); requireNoLow();
}
static void fixedStorage() {
  fresh(); TestLocation location; TestEnvironment sensors(location); bootUart(sensors,location);
  assert(sensors._wisblock_power.holds()&mesh::gps::ExternalStorage);
  assert(!sensors.canRemoveSharedStoragePowerForDeepSleep());
  assert(sensors.isGpsPowered()&&digitalRead(WB_IO2)==HIGH);
  assert(!sensors.active()&&!Serial1.installed); requireNoLow();
  assert(mesh::ota::OtaStoreQspiNrf52::probes==0);
  assert(sensors.setSettingValue("gps","1"));
  assert(sensors.active()&&sensors.isGpsPowered());
  assert(sensors.setSettingValue("gps","0"));
  assert(!sensors.active()&&sensors.isGpsPowered()); requireNoLow();
}
static void storageIds() {
  for(uint8_t selected: {uint8_t(0),uint8_t(1),uint8_t(2),uint8_t(3)}) {
    for(uint32_t id: {0U,0xffffffU,0x123456U,0xef4015U}) {
      for(bool header: {false,true}) {
        fresh(); TestLocation location; TestEnvironment sensors(location);
        mesh::ota::OtaStoreQspiNrf52::selected=selected;
        if(header) mesh::ota::OtaStoreQspiNrf52::w25_id=id;
        else mesh::ota::OtaStoreQspiNrf52::rak_id=id;
        // Supply is protected while the read-only probe identifies a physical owner.
        assert(sensors.begin()); requireNoLow();
        sensors.configureSharedPeripheralMode(false);
        const bool storage_held=selected!=0||(id!=0&&id!=0xffffffU);
        assert(bool(sensors._wisblock_power.holds()&mesh::gps::ExternalStorage)==storage_held);
        assert(sensors.canRemoveSharedStoragePowerForDeepSleep()==!storage_held);
        assert(digitalRead(WB_IO2)==int(storage_held||compile_held));
        assert(mesh::ota::OtaStoreQspiNrf52::probes>0);
      }
    }
  }
}
static void i2c(bool shared,unsigned wake_failure=0) {
  fresh(); TestLocation location; TestEnvironment sensors(location); ublox_GNSS.present=true;
  assert(sensors.begin()); assert(sensors.isGPSDetected()&&sensors.active()&&!sensors.userEnabled());
  assert(i2cGPSFlag&&!serialGPSFlag&&sensors.getLocationProvider()==&RAK12500_provider);
  assert(RAK12500_provider.getPinEn()<0);
  sensors.configureSharedPeripheralMode(shared);
  assert(sensors.setSettingValue("gps","0"));
  const bool held=shared||compile_held;
  assert(!sensors.active()&&sensors.isGpsPowered()==held);
  if(held) { requireNoLow(); assert(ublox_GNSS.psm.back()); }
  else assert(ublox_GNSS.psm.empty());
  const unsigned begins=ublox_GNSS.begins,polls=ublox_GNSS.polls;
  now_ms+=1000; sensors.loop(); assert(ublox_GNSS.polls==polls);
  hardware_events.clear(); const uint32_t started=millis();
  const unsigned writes=ublox_GNSS.nv_writes;
  if(wake_failure==1) ublox_GNSS.begin_ok=false;
  if(wake_failure==2) ublox_GNSS.output_ok=false;
  if(wake_failure==3) ublox_GNSS.rate_ok=false;
  if(wake_failure==4) bus_available=false;
  assert(sensors.setSettingValue("gps","1"));
  if(wake_failure) {
    assert(!held&&!sensors.active()&&!sensors.isGPSDetected()&&!sensors.isGpsPowered());
    assert(sensors.userEnabled());  // Persisted intent is not silently rewritten after failure.
    const unsigned failed_begins=ublox_GNSS.begins;
    for(unsigned i=0;i<10;++i) { now_ms+=1000; sensors.loop(); }
    assert(ublox_GNSS.begins==failed_begins&&ublox_GNSS.polls==polls);
    assert(!sensors.setSettingValue("gps","1"));
    return;
  }
  assert(ublox_GNSS.nv_writes==writes);  // Cold duty wake must not write receiver flash/BBR again.
  assert(sensors.active()&&sensors.isGpsPowered());
  if(held) {
    assert(ublox_GNSS.begins==begins&&!ublox_GNSS.psm.back()); requireNoLow();
  } else {
    assert(ublox_GNSS.begins==begins+1);
    assert(firstEvent("write",WB_IO2,HIGH)<firstEvent("gnssBegin"));
    assert(hardware_events[firstEvent("gnssBegin")].time>=started+500);
  }
  assert(ublox_GNSS.flushes>0);  // A cold wake never reuses a previous position as fresh.
  assert(sensors.setSettingValue("gps","0"));
  assert(!sensors.active()&&sensors.isGpsPowered()==held);
}
static void initialConfigFailure() {
  for(unsigned failed_step:{1U,2U}) {
    fresh(); TestLocation location; TestEnvironment sensors(location); ublox_GNSS.present=true;
    if(failed_step==1) ublox_GNSS.output_ok=false;
    else ublox_GNSS.rate_ok=false;
    assert(sensors.begin());
    assert(!sensors.isGPSDetected()&&!sensors.active()&&!i2cGPSFlag&&!serialGPSFlag);
    assert(sensors.getLocationProvider()==&location&&!Serial1.installed);
    assert(ublox_GNSS.output_calls==1&&ublox_GNSS.rate_calls==(failed_step==1?0U:1U));
    assert(ublox_GNSS.nv_writes==0&&!sensors.userEnabled());
    requireNoLow(); sensors.configureSharedPeripheralMode(false);
    assert(!sensors.isGpsPowered()&&digitalRead(WB_IO2)==LOW);
    const unsigned begins=ublox_GNSS.begins,polls=ublox_GNSS.polls;
    for(unsigned i=0;i<10;++i) { now_ms+=1000; sensors.loop(); }
    assert(ublox_GNSS.begins==begins&&ublox_GNSS.polls==polls);
    assert(!sensors.setSettingValue("gps","1"));
  }
}
static void dedicated() {
#ifdef PIN_GPS_EN
  fresh(); TestLocation location; TestEnvironment sensors(location); Serial1.nmea();
  assert(sensors.begin()&&sensors.isGPSDetected()&&location.pin==PIN_GPS_EN);
  sensors.configureSharedPeripheralMode(true);
  assert(sensors.setSettingValue("gps","0"));
  requireUartReleasedBeforePowerOff(PIN_GPS_EN,!PIN_GPS_EN_ACTIVE);
  assert(!sensors.active()&&!sensors.isGpsPowered());
  assert(digitalRead(PIN_GPS_EN)==!PIN_GPS_EN_ACTIVE);
  assert(digitalRead(WB_IO2)==HIGH); requireNoLow();
  assert(sensors.gpsSerialTransportCanYield(1));
  const unsigned off_ends=Serial1.ends;
  assert(sensors.setGpsSerialTransportBlocked(1,true));
  assert(Serial1.ends==off_ends);  // Already-off GPS must not close UARTE twice.
  assert(!sensors.transportAvailable()&&!sensors.isGpsPowered());
  assert(sensors.setSettingValue("gps","1"));
  assert(sensors.userEnabled()&&!sensors.active()&&!sensors.isGpsPowered());
  hardware_events.clear(); assert(sensors.setGpsSerialTransportBlocked(1,false));
  assert(sensors.active()&&Serial1.installed&&sensors.isGpsPowered());
  assert(firstEvent("write",PIN_GPS_EN,PIN_GPS_EN_ACTIVE)<firstEvent("uartBegin"));
  assert(digitalRead(WB_IO2)==HIGH); requireNoLow();
  const unsigned on_ends=Serial1.ends;
  hardware_events.clear(); assert(sensors.setGpsSerialTransportBlocked(1,true));
  assert(!sensors.active()&&!Serial1.installed&&Serial1.ends==on_ends+1);
  requireUartReleasedBeforePowerOff(PIN_GPS_EN,!PIN_GPS_EN_ACTIVE);
  assert(!sensors.isGpsPowered()&&sensors.userEnabled()); requireNoLow();
  assert(sensors.setGpsSerialTransportBlocked(1,false));
  assert(sensors.active()&&sensors.isGpsPowered()&&Serial1.installed);
#else
  assert(false&&"dedicated scenario requires a separate enable pin");
#endif
}

static void dedicatedProviderStop() {
#if defined(PIN_GPS_EN) && PIN_GPS_EN >= 0
  fresh();
  const int reset_pin=7;
  MicroNMEALocationProvider location(Serial1,nullptr,reset_pin,PIN_GPS_EN);
  EnvironmentSensorManager sensors(location);
  Serial1.nmea(); assert(sensors.begin());
  assert(sensors.isGPSDetected()&&sensors.isGpsPowered());
  // RAK discovery controls power directly, without provider.begin(). Match
  // NRF52Board::shutdownPeripherals(), which stops the selected provider.
  sensors.getLocationProvider()->stop();
  assert(digitalRead(PIN_GPS_EN)==!PIN_GPS_EN_ACTIVE);
  assert(digitalRead(reset_pin)==GPS_RESET_ACTIVE);
  // A repeated stop must still force safe pins if another owner woke GPS.
  digitalWrite(PIN_GPS_EN,PIN_GPS_EN_ACTIVE);
  digitalWrite(reset_pin,!GPS_RESET_ACTIVE);
  sensors.getLocationProvider()->stop();
  assert(digitalRead(PIN_GPS_EN)==!PIN_GPS_EN_ACTIVE);
  assert(digitalRead(reset_pin)==GPS_RESET_ACTIVE);
#else
  assert(false&&"dedicated provider scenario requires a separate enable pin");
#endif
}

static void terminalStorage() {
  SensorManager unknown;
  assert(!unknown.canRemoveSharedStoragePowerForDeepSleep());
  for(uint8_t holds:{uint8_t(0),uint8_t(mesh::gps::RadioPower),uint8_t(mesh::gps::EthernetPower),
                     uint8_t(mesh::gps::ExternalStorage),uint8_t(0x80)}) {
    for(bool shared:{false,true}) {
      fresh();TestLocation location;TestEnvironment sensors(location);
      sensors._wisblock_power=mesh::gps::WisBlockSharedPowerPolicy(holds);
      assert(!sensors.canRemoveSharedStoragePowerForDeepSleep());
      assert(sensors._wisblock_power.configure(shared?mesh::gps::WisBlockPeripheralMode::Shared
                                                   :mesh::gps::WisBlockPeripheralMode::GpsOnly));
      assert(!sensors.canRemoveSharedStoragePowerForDeepSleep());
      sensors._wisblock_power.setDiscoveryActive(false);
      const size_t events=hardware_events.size();
      const unsigned probes=mesh::ota::OtaStoreQspiNrf52::probes;
      const bool allowed=(holds&(mesh::gps::ExternalStorage|0x80))==0;
      assert(sensors.canRemoveSharedStoragePowerForDeepSleep()==allowed);
      sensors._wisblock_power.setGpsActive(true);
      assert(sensors.canRemoveSharedStoragePowerForDeepSleep()==allowed);
      assert(hardware_events.size()==events&&mesh::ota::OtaStoreQspiNrf52::probes==probes);
      sensors._wisblock_power.setDiscoveryActive(true);
      assert(!sensors.canRemoveSharedStoragePowerForDeepSleep());
    }
  }
}

int main(int argc,char** argv) {
  assert(argc==2);
  if(!std::strcmp(argv[1],"uart")) uart(false);
  else if(!std::strcmp(argv[1],"shared_uart")) uart(true);
  else if(!std::strcmp(argv[1],"held_uart")) uart(false);
  else if(!std::strcmp(argv[1],"hourly")) hourly();
  else if(!std::strcmp(argv[1],"repeat")) repeatedDiscovery();
  else if(!std::strcmp(argv[1],"prefs_before")) preferenceBeforeDiscovery();
  else if(!std::strcmp(argv[1],"powered_status")) actualPoweredStatus();
  else if(!std::strcmp(argv[1],"absent")) absent();
  else if(!std::strcmp(argv[1],"storage")) storage();
  else if(!std::strcmp(argv[1],"fixed_storage")) fixedStorage();
  else if(!std::strcmp(argv[1],"storage_ids")) storageIds();
  else if(!std::strcmp(argv[1],"i2c")) i2c(false);
  else if(!std::strcmp(argv[1],"shared_i2c")) i2c(true);
  else if(!std::strcmp(argv[1],"held_i2c")) i2c(false);
  else if(!std::strcmp(argv[1],"wake_failure")) i2c(false,1);
  else if(!std::strcmp(argv[1],"wake_output_failure")) i2c(false,2);
  else if(!std::strcmp(argv[1],"wake_rate_failure")) i2c(false,3);
  else if(!std::strcmp(argv[1],"wake_bus_failure")) i2c(false,4);
  else if(!std::strcmp(argv[1],"initial_config_failure")) initialConfigFailure();
  else if(!std::strcmp(argv[1],"dedicated")) dedicated();
  else if(!std::strcmp(argv[1],"dedicated_provider_stop")) dedicatedProviderStop();
  else if(!std::strcmp(argv[1],"terminal_storage")) terminalStorage();
  else assert(false&&"unknown scenario");
}
