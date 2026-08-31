#include <gtest/gtest.h>
#include <Arduino.h>

#define ENV_INCLUDE_GPS 1
#include "../../src/helpers/SensorManager.cpp"

class TestGpsSensorManager : public SensorManager {
public:
  bool detected = true;
  bool active = false;
  uint8_t starts = 0;
  uint8_t stops = 0;

  void setUserEnabled(bool enabled) { setGpsTelemetryUserEnabled(enabled); }
  void setTransportAvailable(bool available) {
    setGpsTelemetryTransportAvailable(available);
  }
  bool receiverRequired(unsigned long now) const {
    return gpsTelemetryReceiverRequired(now);
  }
  bool queryLocation(CayenneLPP& telemetry) {
    return queryGpsTelemetry(TELEM_PERM_LOCATION, telemetry);
  }
  void acceptFix(float lat, float lon, float altitude, unsigned long now) {
    processGpsTelemetryFix(lat, lon, altitude, now);
  }
  void stopReceiver() { telemetryGpsStop(); }

protected:
  bool telemetryGpsDetected() const override { return detected; }
  bool telemetryGpsActive() const override { return active; }
  void telemetryGpsStart() override {
    active = true;
    starts++;
  }
  void telemetryGpsStop() override {
    active = false;
    stops++;
  }
};

TEST(GpsTransportOwnership, BlockingCancelsAcquisitionAndRemoteHold) {
  resetArduinoMock();
  TestGpsSensorManager sensors;
  CayenneLPP telemetry(64);

  EXPECT_FALSE(sensors.queryLocation(telemetry));
  EXPECT_TRUE(sensors.active);
  EXPECT_TRUE(sensors.receiverRequired(millis()));

  // Hardware-specific ownership code stops the receiver before handing its
  // UART to the bridge; the base transition clears acquisition and hold state.
  sensors.stopReceiver();
  sensors.setTransportAvailable(false);
  EXPECT_FALSE(sensors.active);
  EXPECT_FALSE(sensors.receiverRequired(millis()));
  EXPECT_FALSE(sensors.queryLocation(telemetry));
  EXPECT_EQ(1, sensors.starts);
}

TEST(GpsTransportOwnership, UserPreferenceSurvivesBlockAndRestartsOnRelease) {
  resetArduinoMock();
  TestGpsSensorManager sensors;

  sensors.setUserEnabled(true);
  ASSERT_TRUE(sensors.active);
  ASSERT_EQ(1, sensors.starts);

  sensors.stopReceiver();
  sensors.setTransportAvailable(false);
  EXPECT_FALSE(sensors.active);

  sensors.setTransportAvailable(true);
  EXPECT_TRUE(sensors.active);
  EXPECT_EQ(2, sensors.starts);
}

TEST(GpsTransportOwnership, LastGoodCacheSurvivesTemporaryUartOwnership) {
  resetArduinoMock();
  TestGpsSensorManager sensors;
  CayenneLPP telemetry(64);

  EXPECT_FALSE(sensors.queryLocation(telemetry));
  sensors.acceptFix(47.61f, -122.33f, 125.0f, millis());
  g_mock_millis += 31000;
  sensors.acceptFix(47.61f, -122.33f, 125.0f, millis());
  ASSERT_TRUE(sensors.queryLocation(telemetry));
  ASSERT_EQ(1, telemetry.gps_count);

  sensors.stopReceiver();
  sensors.setTransportAvailable(false);
  EXPECT_TRUE(sensors.queryLocation(telemetry));
  EXPECT_EQ(2, telemetry.gps_count);
  EXPECT_FALSE(sensors.receiverRequired(millis()));

  sensors.setTransportAvailable(true);
  EXPECT_TRUE(sensors.queryLocation(telemetry));
  EXPECT_EQ(3, telemetry.gps_count);
  EXPECT_FLOAT_EQ(47.61f, telemetry.last_latitude);
  EXPECT_FLOAT_EQ(-122.33f, telemetry.last_longitude);
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
