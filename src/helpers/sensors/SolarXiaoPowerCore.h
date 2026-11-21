#pragma once

#include <stddef.h>
#include <stdint.h>

// Portable charger logic. The board adapter owns Wire and filesystem access.
namespace mesh { namespace solar {

class Bus {
public:
  virtual ~Bus() {}
  virtual bool read(uint8_t address, uint8_t reg, uint16_t& value) = 0;
  virtual bool write(uint8_t address, uint8_t reg, uint16_t value) = 0;
};

class Store {
public:
  virtual ~Store() {}
  virtual bool available() const = 0;
  virtual bool exists() const = 0;
  virtual bool load(uint8_t* data, size_t size) = 0;
  virtual bool loadBackup(uint8_t*, size_t) { return false; }
  virtual bool save(const uint8_t* data, size_t size) = 0;
};

struct Settings {
  static constexpr size_t REG_COUNT = 0x30;
  static constexpr size_t IMAGE_SIZE = 224;
  uint16_t masks[REG_COUNT];
  uint16_t values[REG_COUNT];
  uint16_t charge_stop_mv, charge_resume_mv;
  // Reference schematic values, configurable for other carrier revisions.
  float rsnsb_mohm, rsnsi_mohm, ntc_bias_ohm, ntc_r25_ohm, ntc_beta;
  uint8_t calibration_user_mask;
  void defaults();
  bool valid() const;
  void encode(uint8_t image[IMAGE_SIZE]) const;
  bool decode(const uint8_t image[IMAGE_SIZE]);
};

struct Snapshot {
  bool charger, monitor, adc_active;
  uint8_t chemistry, cells;
  uint16_t identity, config, charger_config, state, status, system, telemetry;
  uint16_t alerts[3];
  uint16_t raw_vbat, raw_vin, raw_vout, raw_ibat, raw_iin, raw_die, raw_ntc, raw_bsr;
  float battery_v, battery_a, battery_w;
  float vin_v, vout_v, charger_vbat_v, ibat_a, iin_a, die_c, ntc_c, bsr_ohm;
};

class Controller {
  Bus& bus;
  Store& store;
  Settings settings;
  bool storage_healthy = true;
  bool loaded_backup = false;
  bool apply_ok = false;
  uint8_t last_cells = 0;
  uint32_t next_service = 0;
  bool serviced = false;
  bool started = false;
  bool voltage_hold = true;

  bool identify(uint16_t& identity);
  bool readMonitorVoltage(uint16_t& raw);
  bool holdForVoltage();
  bool verifyWritableIdentity(char* reply, size_t capacity);
  bool update(uint8_t reg, uint16_t mask, uint16_t bits);
  bool save(const Settings& candidate);
  bool setField(const char* key, const char* value, char* reply, size_t capacity);
  bool getField(const char* key, char* reply, size_t capacity);
  bool action(const char* arguments, bool local, char* reply, size_t capacity);

public:
  Controller(Bus& transport, Store& persistence) : bus(transport), store(persistence) {
    settings.defaults();
  }
  void begin();
  void reload();
  void service(uint32_t now);
  bool snapshot(Snapshot& result);
  bool readMonitor(float& volts, float& amps, float& watts);
  bool handle(const char* command, char* reply, size_t capacity, bool local);
  const Settings& configuration() const { return settings; }
  bool settingsHealthy() const { return storage_healthy; }
  bool settingsApplied() const { return apply_ok; }
  uint8_t lastCellCount() const { return last_cells; }
  static uint16_t writableMask(uint8_t reg);
  static bool readableRegister(uint8_t reg);
  static const char* chemistryName(uint8_t chemistry);
  static const char* stateName(uint16_t state);
};

}} // namespace mesh::solar
