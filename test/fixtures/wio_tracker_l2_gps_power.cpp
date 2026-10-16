#include <Arduino.h>
#include <Wire.h>
#include <WioTrackerL2LocationProvider.h>
#include "GpsTap.h"
#include <helpers/SensorManager.h>

#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define POWERSAVING_DEBUG_PRINTLN(...) ((void)0)

void LocationProvider::sendSentence(const char*) {}

@EXPANDER@

// Keep only ESP32 startup hardware out of this host harness. The expander
// initialization, output shadows and GNSS power/reset methods are production.
void WioTrackerL2Board::begin() { expander_ok = initExpander(); }
uint16_t WioTrackerL2Board::getBattMilliVolts() { return 0; }
bool WioTrackerL2Board::isExternalPowered() { return false; }
void WioTrackerL2Board::setLed(bool) {}

class EnvironmentSensorManager : public SensorManager {
public:
  LocationProvider* _location;
  bool gps_detected=false, gps_active=false, gps_serial_transport=false;
  bool gps_serial_open=false;
  bool gps_serial_transport_blocked=false, gps_discovery_pending=false;
  bool gps_discovery_preference_known=false;
  uint32_t gps_discovery_started_at=0;
  explicit EnvironmentSensorManager(LocationProvider& location) : _location(&location) {}
  LocationProvider* getLocationProvider() override { return _location; }
  bool telemetryGpsDetected() const override { return gps_detected; }
  bool telemetryGpsActive() const override { return gps_active; }
  void telemetryGpsStart() override { start_gps(); }
  void telemetryGpsStop() override { stop_gps(); }
  bool setSettingValue(const char*,const char*) override;
  void setPowerSavingEnabled(bool) override;
  void finishBasicGpsDiscovery(bool found);
  void serviceBasicGpsDiscovery();
  void initBasicGPS();
  void startGpsSerialTransport();
  void stopGpsSerialTransport();
  void armGpsPowerSavingCycle();
  void start_gps();
  void stop_gps();
  void loop() override;
};

@LIFECYCLE@

WioTrackerL2Board board;
GpsTapStream gps_tap(Serial1);
mesh::RTCClock clock_source;
WioTrackerL2LocationProvider gps(board, gps_tap, &clock_source);
EnvironmentSensorManager sensors(gps);
struct RadioMock { void powerOff() { event("radio-off"); } } radio_driver;

@SHUTDOWN@

size_t first(const char* name) {
  for (size_t i=0;i<events.size();++i) if(events[i].name==name)return i;
  return events.size();
}
void assertOff() {
  assert(!Wire.gnssOn()&&!gps.isEnabled()&&!Serial1.installed);
  assert(modes[PIN_GPS_TX]==INPUT&&modes[PIN_GPS_RX]==INPUT);
}
void start() {
  // Match EnvironmentSensorManager's UART setup before provider begin().
  Serial1.setPins(PIN_GPS_TX,PIN_GPS_RX);Serial1.begin(9600);
  events.clear();gps.begin();
  assert(gps.isEnabled()&&Wire.gnssOn()&&Serial1.installed);
  assert(first("uart-end")<first("gnss-on"));
  assert(first("gnss-on")<first("uart-begin"));
}
void stop() {
  events.clear();gps.stop();assertOff();
  assert(first("uart-end")<first("gnss-off"));
}

int main(int argc,char** argv) {
  assert(argc==2&&events.empty()&&Wire.transactions==0);
  const std::string mode=argv[1];
  if(mode=="failed") {
    Wire.present=false;board.begin();assert(!board.expanderOK());
    Serial1.setPins(PIN_GPS_TX,PIN_GPS_RX);Serial1.begin(9600);
    unsigned probes=Wire.transactions;gps.begin();
    assertOff();assert(Wire.transactions==probes);
    gps.stop();assertOff();assert(Wire.transactions==probes);
    return 0;
  }
  board.begin();
  assert(board.expanderOK()&&!Wire.gnssOn()&&!gps.isEnabled());
  if(mode=="boot")return 0;
  if(mode=="cycles") {
    for(unsigned cycle=0;cycle<4;++cycle) {
      start();assert(!gps.isValid());
      unsigned probes=Wire.transactions,begins=Serial1.begins,ends=Serial1.ends;
      gps.begin();assert(Wire.transactions==probes);
      assert(Serial1.begins==begins&&Serial1.ends==ends);
      stop();probes=Wire.transactions;ends=Serial1.ends;
      gps.stop();assert(Wire.transactions==probes&&Serial1.ends==ends);
    }
    return 0;
  }
  if(mode=="manager") {
    sensors.initBasicGPS();
    assert(sensors.gps_detected&&!sensors.gps_active);assertOff();
    assert(sensors.setSettingValue("gps","1"));
    assert(sensors.gps_active&&gps.isEnabled()&&Wire.gnssOn());
    assert(sensors.setSettingValue("gps","0"));assertOff();
    sensors.setPowerSavingEnabled(true);
    assert(sensors.setSettingValue("gps","1"));
    assert(sensors.gps_active&&gps.getGPSPowerSaving()&&Wire.gnssOn());
    gps.setNextGPSOff(millis()+1);gps.stopTimeSync();now_ms+=2;
    sensors.loop();assert(!sensors.gps_active);assertOff();
    uint32_t wake=gps.getNextGPSOn();assert(wake!=0);
    now_ms=wake;sensors.loop();
    assert(sensors.gps_active&&gps.isEnabled()&&Wire.gnssOn());
    assert(sensors.setSettingValue("gps","0"));assertOff();
    return 0;
  }
  assert(mode=="tapshutdown");
  start();
  Serial1.feed("$GPGGA,123456,4760.000,N,12200.000,W,1,08,1.0,1.0,M,0.0,M,,*00\r\n");
  gps.loop();assert(gps.isValid());
  assert(gps_tap.ggaFix()==1&&gps_tap.ggaUsed()==8);
  events.clear();board.shutdownPeripherals();assertOff();
  assert(first("radio-off")<first("gnss-off"));
  assert(first("uart-end")<first("gnss-off"));
  start();assert(!gps.isValid());stop();
}
