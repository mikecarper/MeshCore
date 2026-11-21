#include "SolarXiaoPower.h"
#if defined(MESH_SOLARXIAO_POWER) && MESH_SOLARXIAO_POWER
#include "SolarXiaoPowerCore.h"
#include "SolarXiaoCarrier.h"
#include <helpers/CheckedSettingsFile.h>
#include <helpers/FilePresence.h>
#include <Wire.h>
#include <math.h>

namespace mesh { namespace solar {
extern const char meshcore_capability_solarxiao_power[] = "MESHCORE_CAPABILITY:solarxiao_power:v1";
namespace {
class CarrierBus : public Bus {
  bool ready(uint8_t address) {
    return (address==0x68 || address==0x40)
        && digitalRead(PIN_BOARD_SDA)==HIGH && digitalRead(PIN_BOARD_SCL)==HIGH;
  }
public:
  bool read(uint8_t address, uint8_t reg, uint16_t& value) override {
    if (!ready(address)) return false;
    Wire.beginTransmission(address);
    Wire.write(reg);
    if (Wire.endTransmission(false)!=0) return false;
    if (Wire.requestFrom(address,uint8_t(2))!=2) {
      while (Wire.available()) Wire.read();
      return false;
    }
    const int first=Wire.read(), second=Wire.read();
    if (first<0 || second<0) return false;
    // LTC4162 SMBus words are little endian; INA260 registers are big endian.
    value=address==0x68 ? uint16_t(first)|(uint16_t(second)<<8)
                        : (uint16_t(first)<<8)|second;
    return true;
  }
  bool write(uint8_t address,uint8_t reg,uint16_t value) override {
    if (address!=0x68 || !ready(address)) return false;
    Wire.beginTransmission(address);
    Wire.write(reg); Wire.write(uint8_t(value)); Wire.write(uint8_t(value>>8));
    return Wire.endTransmission()==0;
  }
};
class CarrierStore : public Store {
  static constexpr const char* backup="/solar_power.bak";
  bool saveCopy(const char* name,const uint8_t* data,size_t size) {
    uint8_t checked[Settings::IMAGE_SIZE];
    if (storage::readFixedSettingsFile(fs,name,checked,size) && !memcmp(checked,data,size)) return true;
    AtomicFileWriter writer(fs,name);
    return writer && writer.write(data,size)==size && writer.commit()
        && storage::readFixedSettingsFile(fs,name,checked,size) && !memcmp(checked,data,size);
  }
public:
  FILESYSTEM* fs=nullptr;
  static constexpr const char* path="/solar_power";
  bool available() const override { return fs!=nullptr; }
  bool exists() const override {
    if (!fs) return false;
    bool present;
    // Defaults are appropriate only when both names are confirmed absent.
    return !mesh::filePresence(fs,path,present) || present
        || !mesh::filePresence(fs,backup,present) || present;
  }
  bool load(uint8_t* data,size_t size) override {
    return storage::readFixedSettingsFile(fs,path,data,size);
  }
  bool loadBackup(uint8_t* data,size_t size) override {
    return storage::readFixedSettingsFile(fs,backup,data,size);
  }
  bool save(const uint8_t* data,size_t size) override {
    // Keep the acknowledged backup until the new primary is verified.
    return fs && data && size==Settings::IMAGE_SIZE
        && saveCopy(path,data,size) && saveCopy(backup,data,size);
  }
};
constexpr const char* CarrierStore::path;
constexpr const char* CarrierStore::backup;
class SelectionStore : public Store {
public:
  FILESYSTEM* fs=nullptr;
  static constexpr const char* path="/solar_board";
  bool available() const override { return fs!=nullptr; }
  bool exists() const override {
    bool present=false;
    // Unknown metadata must force a complete checked read, never a default.
    return fs && (!mesh::filePresence(fs,path,present) || present);
  }
  bool load(uint8_t* data,size_t size) override {
    return storage::readFixedSettingsFile(fs,path,data,size);
  }
  bool save(const uint8_t* data,size_t size) override {
    return storage::writeCheckedSettingsFile(fs,path,data,size)
        == AtomicFileWriter::Outcome::Committed;
  }
};
constexpr const char* SelectionStore::path;
MainBoard* main_board=nullptr;
bool history_available=false;
CarrierBus transport;
CarrierStore persistence;
SelectionStore selection_store;
CarrierSelection selection(selection_store);
Controller controller(transport,persistence);
}

void beginSettings(MainBoard& board,FILESYSTEM* fs,bool telemetry_history_available) {
  const volatile char* marker=meshcore_capability_solarxiao_power;
  (void)*marker;
  main_board=&board;
  history_available=telemetry_history_available;
  selection_store.fs=fs;
  selection.begin();
  persistence.fs=fs; controller.begin();
}
void service() {
  const bool native_before=selection.nativeAllowed();
  selection.service(transport,millis());
  if (!native_before && selection.nativeAllowed()) controller.reload();
  if (selection.nativeAllowed()) controller.service(millis());
}
bool handleCommand(const char* command,char* reply,size_t capacity,bool local) {
  float battery=NAN,temperature=NAN;
  if (main_board && command && (!strcmp(command,"get solar readings") || !strcmp(command,"get solar battery"))) {
    battery=main_board->getBattMilliVolts()/1000.0f;
    temperature=main_board->getMCUTemperature();
  }
  const bool native_before=selection.nativeAllowed();
  if (selection.handle(command,reply,capacity,battery,temperature,history_available)) {
    if (!native_before && selection.nativeAllowed()) controller.reload();
    return true;
  }
  if (!command || !reply || !capacity) return false;
  const bool mppt=(!strncmp(command,"get mppt",8) && (!command[8] || command[8]==' '))
      || !strncmp(command,"set mppt ",9) || !strncmp(command,"mppt ",5);
  if (mppt && !selection.nativeAllowed()) {
    const char* error=selection.isBasic()
        ? "Err - basic (Base/Advanced) has no digital charger/INA interface; use get solar readings"
        : selection.isAuto()
        ? "Err - auto has not verified INA260; self-only; use get solar.board or set solar.board mppt for manual override"
        : "Err - carrier selection unconfirmed; set solar.board auto|basic|mppt";
    snprintf(reply,capacity,"%s",error);
    return true;
  }
  return controller.handle(command,reply,capacity,local);
}
void addTelemetry(CayenneLPP& lpp) {
  if (!selection.nativeAllowed()) return;
  Snapshot s; controller.snapshot(s);
  if (s.monitor) {
    lpp.addVoltage(2,s.battery_v); lpp.addCurrent(2,s.battery_a); lpp.addPower(2,s.battery_w);
  }
  if (!s.charger) return;
  if (isfinite(s.vin_v)) {
    lpp.addVoltage(3,s.vin_v); lpp.addCurrent(3,s.iin_a);
    lpp.addPower(3,fmaxf(0,s.vin_v*s.iin_a));
  }
  if (isfinite(s.vout_v)) lpp.addVoltage(4,s.vout_v);
  if (isfinite(s.charger_vbat_v)) lpp.addVoltage(5,s.charger_vbat_v);
  if (isfinite(s.ibat_a)) lpp.addCurrent(5,s.ibat_a);
  if (isfinite(s.die_c)) lpp.addTemperature(6,s.die_c);
  if (isfinite(s.ntc_c)) lpp.addTemperature(7,s.ntc_c);
  lpp.addGenericSensor(8,s.state); lpp.addGenericSensor(9,s.status);
  lpp.addGenericSensor(10,s.system);
  for (unsigned i=0;i<3;++i) lpp.addGenericSensor(11+i,s.alerts[i]);
}
uint8_t voltageChannels(uint8_t* channels,uint8_t capacity) {
  // History allocates its schema once at boot, before Auto can verify MPPT.
  // Reserve stable IDs without I/O; inactive/unavailable samples stay missing.
  const uint8_t count=capacity<4 ? capacity : 4;
  for (uint8_t i=0;channels && i<count;++i) channels[i]=2+i;
  return count;
}
uint8_t queryVoltages(SensorManager::VoltageSensorReading* readings,uint8_t capacity) {
  if (!selection.nativeAllowed() || !readings || !capacity) return 0;
  Snapshot s; controller.snapshot(s);
  const float volts[]={s.battery_v,s.vin_v,s.vout_v,s.charger_vbat_v};
  const uint8_t count=capacity<4 ? capacity : 4;
  for (uint8_t i=0;i<count;++i) {
    readings[i].channel=2+i;
    readings[i].valid=(i==0?s.monitor:s.charger) && isfinite(volts[i]);
    readings[i].voltage=readings[i].valid ? volts[i] : 0;
  }
  return count;
}
}} // namespace mesh::solar
#endif
