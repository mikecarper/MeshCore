#include <gtest/gtest.h>

#include <cstdio>
#include <cstring>
#include <string>

#include "../../examples/companion_radio/NodePrefs.h"

class ReplayStream : public Stream {
  const char* _text;
  int _pos = 0;
  int _len;

public:
  explicit ReplayStream(const char* text) : _text(text), _len(strlen(text)) { }

  int available() override { return _len - _pos; }
  int read() override { return _pos < _len ? _text[_pos++] : -1; }
  int peek() override { return _pos < _len ? _text[_pos] : -1; }
};

class CaptureStream : public Stream {
  std::string _text;

  size_t emit(long long value) {
    char text[24];
    int length = snprintf(text, sizeof(text), "%lld", value);
    return write(reinterpret_cast<const uint8_t*>(text), length);
  }

public:
  size_t write(uint8_t value) override {
    _text.push_back(static_cast<char>(value));
    return 1;
  }

  size_t write(const uint8_t* buffer, size_t size) override {
    _text.append(reinterpret_cast<const char*>(buffer), size);
    return size;
  }

  size_t print(unsigned char value, int = DEC) override { return emit(value); }
  size_t print(int value, int = DEC) override { return emit(value); }
  size_t print(unsigned int value, int = DEC) override { return emit(value); }
  size_t print(long value, int = DEC) override { return emit(value); }
  size_t print(unsigned long value, int = DEC) override { return emit(value); }
  size_t print(long long value, int = DEC) override { return emit(value); }
  size_t print(unsigned long long value, int = DEC) override { return emit(value); }

  const std::string& text() const { return _text; }
};

TEST(CompanionNodePrefs, DevicePowerSavingIsIndependentFromRxps) {
  CompanionNodePrefs prefs = {};
  prefs.rx_powersaving_enabled = 1;
  prefs.powersaving_enabled = 0;

  EXPECT_EQ(1, prefs.rx_powersaving_enabled);
  EXPECT_EQ(0, prefs.powersaving_enabled);

  prefs.powersaving_enabled = 1;
  prefs.rx_powersaving_enabled = 0;

  EXPECT_EQ(0, prefs.rx_powersaving_enabled);
  EXPECT_EQ(1, prefs.powersaving_enabled);
}

TEST(CompanionNodePrefs, WiFiStateIsIndependentFromPowerSaving) {
  CompanionNodePrefs prefs = {};
  prefs.wifi_enabled = 1;
  prefs.powersaving_enabled = 0;

  EXPECT_EQ(1, prefs.wifi_enabled);
  EXPECT_EQ(0, prefs.powersaving_enabled);

  prefs.wifi_enabled = 0;
  prefs.powersaving_enabled = 1;

  EXPECT_EQ(0, prefs.wifi_enabled);
  EXPECT_EQ(1, prefs.powersaving_enabled);
}

TEST(CompanionNodePrefs, UsbLoggingStateIsIndependentFromTransports) {
  CompanionNodePrefs prefs = {};
  prefs.usb_logging_enabled = 1;
  prefs.wifi_enabled = 0;
  prefs.powersaving_enabled = 1;

  EXPECT_EQ(1, prefs.usb_logging_enabled);
  EXPECT_EQ(0, prefs.wifi_enabled);
  EXPECT_EQ(1, prefs.powersaving_enabled);

  prefs.usb_logging_enabled = 0;
  EXPECT_EQ(0, prefs.usb_logging_enabled);
  EXPECT_EQ(0, prefs.wifi_enabled);
  EXPECT_EQ(1, prefs.powersaving_enabled);
}

TEST(CompanionNodePrefs, CadControlsAreIndependentAndDefaultable) {
  CompanionNodePrefs prefs = {};
  prefs.cad_enabled = 1;
  prefs.cad_scan_timeout_ms = 350;
  prefs.cad_retry_delay_ms = 75;
  prefs.cad_max_duration_ms = 2500;

  EXPECT_EQ(1, prefs.cad_enabled);
  EXPECT_EQ(350, prefs.cad_scan_timeout_ms);
  EXPECT_EQ(75, prefs.cad_retry_delay_ms);
  EXPECT_EQ(2500, prefs.cad_max_duration_ms);

  // Zero means use the existing SF/BW-derived or Dispatcher adaptive timing.
  prefs.cad_scan_timeout_ms = 0;
  prefs.cad_retry_delay_ms = 0;
  prefs.cad_max_duration_ms = 0;
  EXPECT_EQ(0, prefs.cad_scan_timeout_ms);
  EXPECT_EQ(0, prefs.cad_retry_delay_ms);
  EXPECT_EQ(0, prefs.cad_max_duration_ms);
  EXPECT_EQ(1, prefs.cad_enabled);
}

TEST(CompanionNodePrefs, BluetoothNameOverrideIsIndependentFromNodeName) {
  CompanionNodePrefs prefs = {};
  strcpy(prefs.node_name, "RidgeNode");
  strcpy(prefs.bluetooth_name, "Workshop Companion");

  char effective[64];
  mesh::companion::formatBluetoothName(
      effective, sizeof(effective), prefs.bluetooth_name, "MeshCore-",
      prefs.node_name);
  EXPECT_STREQ("Workshop Companion", effective);

  strcpy(prefs.bluetooth_name, "default");
  mesh::companion::formatBluetoothName(
      effective, sizeof(effective), prefs.bluetooth_name, "MeshCore-",
      prefs.node_name);
  EXPECT_STREQ("default", effective);

  prefs.bluetooth_name[0] = 0;
  mesh::companion::formatBluetoothName(
      effective, sizeof(effective), prefs.bluetooth_name, "MeshCore-",
      prefs.node_name);
  EXPECT_STREQ("MeshCore-RidgeNode", effective);
}

TEST(CompanionNodePrefs, BluetoothNameRequiresBoundedValidUtf8) {
  EXPECT_TRUE(mesh::companion::isValidBluetoothName("Field Unit 4"));
  EXPECT_TRUE(mesh::companion::isValidBluetoothName(
      "Field Unit \xF0\x9F\x93\xA1"));
  EXPECT_FALSE(mesh::companion::isValidBluetoothName(""));
  EXPECT_FALSE(mesh::companion::isValidBluetoothName("   "));
  EXPECT_FALSE(mesh::companion::isValidBluetoothName("bad\nname"));

  char too_long[mesh::companion::BLUETOOTH_NAME_SIZE + 1];
  memset(too_long, 'A', sizeof(too_long));
  too_long[sizeof(too_long) - 1] = 0;
  EXPECT_FALSE(mesh::companion::isValidBluetoothName(too_long));

  const char malformed[] = {'A', static_cast<char>(0x80), 0};
  EXPECT_FALSE(mesh::companion::isValidBluetoothName(malformed));
}

TEST(CompanionNodePrefs, MigratesRegressedPowerSavingDefaultOnce) {
  CompanionNodePrefs prefs = {};
  prefs.powersaving_enabled = 0;
  prefs.powersaving_policy_version = 0;

  EXPECT_TRUE(migrateCompanionPowerSavingDefault(prefs));
  EXPECT_EQ(1, prefs.powersaving_enabled);
  EXPECT_EQ(COMPANION_POWERSAVING_POLICY_VERSION,
            prefs.powersaving_policy_version);

  // Once migrated, an explicit user choice to turn power saving off remains
  // untouched on later boots.
  prefs.powersaving_enabled = 0;
  EXPECT_FALSE(migrateCompanionPowerSavingDefault(prefs));
  EXPECT_EQ(0, prefs.powersaving_enabled);
}

#if 0
// Re-enable test once we can SET fem_ values in companion
TEST(CompanionNodePrefs, RxGainSettingsRoundTripIndependently) {
  NodePrefs saved;
  saved.rx_boosted_gain = 0;
  saved.radio_fem_rxgain = 1;
  saved.radio_fem_txgain = 0;

  CaptureStream output;
  ASSERT_TRUE(saved.saveSerial(output));
  EXPECT_NE(std::string::npos, output.text().find("rxgain:0"));
  EXPECT_NE(std::string::npos, output.text().find("fem_rxgain:1"));
  EXPECT_NE(std::string::npos, output.text().find("fem_txgain:0"));

  ReplayStream input("{radio:{rxgain:1,fem_rxgain:0,fem_txgain:1}}");
  NodePrefs loaded;
  loaded.rx_boosted_gain = 0;
  loaded.radio_fem_rxgain = 1;
  loaded.radio_fem_txgain = 0;

  ASSERT_TRUE(loaded.loadSerial(input));
  EXPECT_EQ(1, loaded.rx_boosted_gain);
  EXPECT_EQ(0, loaded.radio_fem_rxgain);
  EXPECT_EQ(1, loaded.radio_fem_txgain);
}
#endif

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
