#include <Mesh.h>
#include <helpers/BeaconManager.h>
#include <helpers/BeaconFrames.h>
#include <helpers/UTF8Helpers.h>
#include <cstdio>
#include <limits>

unsigned power_hardware_calls = 0;
uint64_t beacon_test_time = 0;
@PRODUCTION_METHODS@

static bool boardAllows(int power, float frequency) {
  // Explicit board caps are independent of the chip and calibrated PA range.
  (void)power;
  (void)frequency;
#ifdef MIN_LORA_TX_POWER
  if (power < MIN_LORA_TX_POWER) return false;
#endif
#ifdef MAX_LORA_TX_POWER
  if (power > MAX_LORA_TX_POWER) return false;
#endif
  return true;
}
static bool driverAllows(int power, float frequency) {
  (void)power;
#if defined(TEST_STM32)
  (void)frequency;
#ifdef TEST_NO_PA
  return false;
#elif defined(TEST_LP_ONLY)
  return power >= -17 && power <= 14;
#elif defined(TEST_HP_ONLY)
  return power >= -9 && power <= 22;
#else
  return power >= -17 && power <= 22;
#endif
#elif MESH_RADIO_PROFILE_CHIP == 1276
  (void)frequency;
  return (power >= -4 && power <= 17) || power == 20;
#elif MESH_RADIO_PROFILE_CHIP == 1272
  (void)frequency;
  return power >= -1 && power <= 20;
#elif MESH_RADIO_PROFILE_CHIP == 1110
  (void)frequency;
  return power >= -17 && power <= 22;
#elif MESH_RADIO_PROFILE_CHIP == 1121
  if (frequency > 1000) return power >= -18 && power <= 13;
#ifdef LR1121_FORCE_HP_PA
  return power >= -9 && power <= 22;
#else
  return power >= -17 && power <= 22;
#endif
#elif MESH_RADIO_PROFILE_CHIP == 2021
  return frequency >= 1900 ? power >= -19 && power <= 12 : power >= -9 && power <= 22;
#else
  (void)frequency;
  return power >= -9 && power <= 22;
#endif
}

static void capability(RadioLibWrapper& wrapper, PhysicalLayer& chip) {
  mesh::Radio unsupported;
  assert(!unsupported.validateBeaconPower(909.5f, 17));
  assert(!wrapper.validateBeaconPower(std::numeric_limits<float>::quiet_NaN(), 10));
  for (bool normal_high_band : {false, true}) {
    chip.highFreq = normal_high_band;
    chip.frequency = normal_high_band ? 2440.0f : 909.5f;
    for (float requested : {433.875f, 909.5f, 2440.0f}) {
      for (int power = -128; power <= 127; ++power) {
        const bool expected = boardAllows(power, requested) && driverAllows(power, requested);
        assert(wrapper.validateBeaconPower(requested, int8_t(power)) == expected);
      }
      assert(chip.frequency == (normal_high_band ? 2440.0f : 909.5f));
      assert(chip.highFreq == normal_high_band && power_hardware_calls == 0);
    }
  }
}

#if !defined(TEST_NO_PA)
static void image(const char* path, std::vector<uint8_t>& bytes, bool write) {
  FILE* file = std::fopen(path, write ? "wb" : "rb");
  assert(file);
  if (write) assert(std::fwrite(bytes.data(), 1, bytes.size(), file) == bytes.size());
  else {
    bytes.clear();
    for (int value; (value = std::fgetc(file)) != EOF;) bytes.push_back(uint8_t(value));
    assert(!std::ferror(file));
  }
  assert(std::fclose(file) == 0);
}

static void upgrade(RadioLibWrapper& wrapper, bool legacy) {
  MemoryFS fs;
  if (!legacy) {
    image("upgrade.beacons", fs.files["/beacons"], false);
    image("upgrade.ids", fs.files["/beacon_ids"], false);
  }
  mesh::Mesh mesh;
  mesh._radio = &wrapper;
  mesh::RTCClock rtc;
  mesh::BeaconManager manager;
  manager.begin(&fs, &mesh, &rtc, "Upgrade test");
  char reply[256];
  const auto command = [&](const char* text) {
    assert(manager.handle(text, reply, sizeof(reply)));
    assert(std::strncmp(reply, "Err", 3));
  };
  const auto tick = [&](unsigned hours) {
    g_mock_millis += hours * mesh::BeaconManager::HourMillis;
    manager.loop(g_mock_millis);
  };
  if (legacy) {
    // These images are produced by the real manager and old Mesh validator,
    // not hand-crafted bytes that the old firmware might never have saved.
    command("set beacon 1 meshcore 909.5,125,7,5,100 Old MC");
    command("set beacon 2 meshcore 909.5,125,7,5,22 Sibling");
    command("set beacon 3 meshtext US LongFast 100 Old MT");
    tick(6);
    assert(mesh.sends.size() == 1 && mesh.sends[0].id != 0);
    image("upgrade.beacons", fs.files.at("/beacons"), true);
    image("upgrade.ids", fs.files.at("/beacon_ids"), true);
    return;
  }
  const auto original = fs.files;
  assert(original.at("/beacons")[6] == 1); // Durable IDs were initialized.
  command("get beacon");
  command("get beacon 1"); assert(std::strstr(reply, "100") && std::strstr(reply, "Old MC"));
  command("get beacon 3"); assert(std::strstr(reply, "100") && std::strstr(reply, "Old MT"));
  tick(2); // Unsupported MC consumes no queue attempt or storage write.
  assert(mesh.attempts == 0 && mesh.sends.empty() && fs.files == original);
  tick(2); // A supported sibling still transmits.
  assert(mesh.attempts == 1 && mesh.sends.size() == 1 && mesh.sends[0].slot.power == 22);
  assert(fs.files == original);
  tick(2); // Unsupported MT must be rejected before durable nextId().
  assert(mesh.attempts == 1 && mesh.sends.size() == 1 && fs.files == original);
  command("set beacon.interval 12");
  command("set beacon 2 meshcore 909.5,125,7,5,20 Updated sibling");
  const auto& before = original.at("/beacons");
  const auto& after = fs.files.at("/beacons");
  for (unsigned index : {0U, 2U})
    assert(std::equal(before.begin() + 8 + index * 98, before.begin() + 8 + (index + 1) * 98,
                      after.begin() + 8 + index * 98));
  assert(fs.files.at("/beacon_ids") == original.at("/beacon_ids"));
  command("get beacon 3"); assert(std::strstr(reply, "100"));
  command("set beacon 1 meshcore 909.5,125,7,5,20 Repaired");
  command("delete beacon 3");
  assert(fs.files.at("/beacon_ids") == original.at("/beacon_ids"));
  manager.begin(&fs, &mesh, &rtc, "Upgrade test");
  command("get beacon 1"); assert(std::strstr(reply, "Repaired"));
  command("get beacon.interval"); assert(std::strstr(reply, "12"));
  tick(1);
  assert(mesh.sends.size() == 2 && mesh.sends.back().slot.power == 20);
  tick(1);
  assert(mesh.sends.size() == 3 && std::strstr(mesh.sends.back().slot.text, "Updated sibling"));
  assert(fs.files.at("/beacon_ids") == original.at("/beacon_ids"));
  assert(power_hardware_calls == 0);
}

static void manager(RadioLibWrapper& wrapper) {
  MemoryFS fs;
  mesh::Mesh mesh;
  mesh._radio = &wrapper;
  mesh::RTCClock rtc;
  mesh::BeaconManager manager;
  manager.begin(&fs, &mesh, &rtc, "Power test");
  char reply[256], command[128];
  const float frequency = MESH_RADIO_PROFILE_CHIP == 1268 ? 433.875f : 909.5f;
  for (int bad : {-128, 100, 127}) {
    std::snprintf(command, sizeof(command), "set beacon 1 meshcore %g,125,7,5,%d Hello", double(frequency), bad);
    assert(manager.handle(command, reply, sizeof(reply)));
    assert(!std::strncmp(reply, "Err", 3));
    assert(fs.files.empty()); // Rejection happens before persistence.
  }
  int accepted = 22;
  while (!driverAllows(accepted, frequency) || !boardAllows(accepted, frequency)) --accepted;
  std::snprintf(command, sizeof(command), "set beacon 1 meshcore %g,125,7,5,%d Hello", double(frequency), accepted);
  assert(manager.handle(command, reply, sizeof(reply)) && !std::strncmp(reply, "OK", 2));
  assert(fs.files.count("/beacons") && power_hardware_calls == 0);
  manager.begin(&fs, &mesh, &rtc, "Power test");
  g_mock_millis += 2 * mesh::BeaconManager::HourMillis;
  manager.loop(g_mock_millis);
  assert(mesh.sends.size() == 1 && mesh.sends[0].slot.power == accepted);
  assert(power_hardware_calls == 0);
}
#endif

#if MESH_RADIO_PROFILE_CHIP == 1276
static void externalAmplifier() {
  mesh::MainBoard board;
  CustomSX1276 chip;
  const DacPaLevel levels[] = {{10, 30}, {17, 50}, {30, 130}, {33, 225}};
  DacPaSX1276Wrapper amplifier(chip, board, 4, levels, 4, 30);
  for (int power = -128; power <= 127; ++power)
    assert(amplifier.validateBeaconPower(909.5f, int8_t(power))
        == (boardAllows(power, 909.5f) && power >= 10 && power <= 30));
  DacPaSX1276Wrapper empty(chip, board, 4, nullptr, 0);
  assert(!empty.validateBeaconPower(909.5f, 17));
  DacPaSX1276Wrapper impossible(chip, board, 4, levels, 4, 9);
  assert(!impossible.validateBeaconPower(909.5f, 17));
  assert(power_hardware_calls == 0);
}
#endif

int main(int argc, char** argv) {
  mesh::MainBoard board;
#if defined(TEST_STM32)
  STM32WLx chip;
#ifdef TEST_LP_ONLY
  chip.module.hp = false;
#elif defined(TEST_HP_ONLY)
  chip.module.lp = false;
#elif defined(TEST_NO_PA)
  chip.module.hp = chip.module.lp = false;
#endif
#elif MESH_RADIO_PROFILE_CHIP == 1276
  SX1276 chip;
#elif MESH_RADIO_PROFILE_CHIP == 1272
  SX1272 chip;
#elif MESH_RADIO_PROFILE_CHIP == 1110
  LR1110 chip;
#elif MESH_RADIO_PROFILE_CHIP == 1121
  LR1121 chip;
#elif MESH_RADIO_PROFILE_CHIP == 2021
  LR2021 chip;
#else
  SX1262 chip;
#endif
  RadioLibWrapper wrapper(chip, board);
#if !defined(TEST_NO_PA)
  if (argc == 2 && !std::strcmp(argv[1], "manager")) { manager(wrapper); return 0; }
  if (argc == 2 && !std::strcmp(argv[1], "legacy_write")) { upgrade(wrapper, true); return 0; }
  if (argc == 2 && !std::strcmp(argv[1], "upgrade")) { upgrade(wrapper, false); return 0; }
#else
  (void)argc; (void)argv;
#endif
  capability(wrapper, chip);
#if !defined(TEST_NO_PA)
  manager(wrapper);
#endif
#if MESH_RADIO_PROFILE_CHIP == 1276
  externalAmplifier();
#endif
#if MESH_RADIO_PROFILE_CHIP == 1262 && !defined(TEST_STM32)
  IndicatorRadioHal hal;
  IndicatorSX1262Wrapper indicator(chip, board, hal);
  assert(indicator.validateBeaconPower(909.5f, 10) == boardAllows(10, 909.5f));
  assert(!indicator.validateBeaconPower(909.5f, 100));
  assert(hal.services == 0 && power_hardware_calls == 0);
#endif
  puts("PASS beacon power capability and persisted CLI validation");
}
