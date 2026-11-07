#include "helpers/BrowserRadioDefaults.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

namespace defaults = mesh::browserRadioDefaults;

#if defined(BROWSER_RADIO_DEFAULTS_STARTUP_ONLY)

struct Prefs {
  float freq = 433.175f, bw = 62.5f;
  uint8_t sf = 9, cr = 8;
  int8_t tx_power_dbm = 7;
  uint16_t primary_radio_preamble = 32;
  bool rx_powersaving_enabled = true;
  uint8_t rx_ps_level = 2;
  uint16_t rx_ps_preamble = 48;
  uint32_t rx_ps_rx_us = 123, rx_ps_sleep_us = 456;
};

static void samePrefs(const Prefs& actual, const Prefs& expected) {
  assert(actual.freq == expected.freq && actual.bw == expected.bw);
  assert(actual.sf == expected.sf && actual.cr == expected.cr);
  assert(actual.tx_power_dbm == expected.tx_power_dbm);
  assert(actual.primary_radio_preamble == expected.primary_radio_preamble);
  assert(actual.rx_powersaving_enabled == expected.rx_powersaving_enabled);
  assert(actual.rx_ps_level == expected.rx_ps_level);
  assert(actual.rx_ps_preamble == expected.rx_ps_preamble);
  assert(actual.rx_ps_rx_us == expected.rx_ps_rx_us);
  assert(actual.rx_ps_sleep_us == expected.rx_ps_sleep_us);
}

struct MockFS {
  std::string existing, error;
  mutable unsigned probes = 0;
};

static bool imageReadable = true;
static unsigned imageReads = 0, warnings = 0;
static int8_t clampCap = 22;
static defaults::Settings patched{915250000U, 125000U, 7, 5, 40, 16, true};

namespace mesh {
namespace browserRadioDefaults {
bool read(Settings& value) {
  ++imageReads;
  if (!imageReadable) return false;
  value = patched;
  return true;
}
}
inline int8_t clampLoRaTxPower(int8_t value, float freq) {
  assert(freq == patched.frequencyHz / 1000000.0f);
  return value > clampCap ? clampCap : (value < -9 ? -9 : value);
}
inline bool filePresence(MockFS* fs, const char* path, bool& present) {
  if (!fs) return false;
  ++fs->probes;
  present = fs->existing == path;
  return fs->error != path;
}
struct Log {
  void println(const char*) { ++warnings; }
};
inline Log& usbLoggingPort() { static Log log; return log; }
}

struct MockDriver {
  bool powerValid = true;
  unsigned checks = 0;
  int8_t lastPower = 0;
  Prefs* prefs = nullptr;
  Prefs before;
  bool isTxPowerValid(int8_t value) {
    ++checks;
    lastPower = value;
    if (prefs) samePrefs(*prefs, before); // Pure validation before every mutation.
    return powerValid && value >= -9 && value <= 22;
  }
};

static MockDriver radio_driver;

struct Profiles {
  Prefs* prefs = nullptr;
  Prefs before;
  bool accepted = true, preambleSave = true, preambleRestore = true;
  unsigned accepts = 0, saves = 0, restores = 0, adopts = 0;
  uint16_t preamble = 32;
  bool acceptsPrimary(float freq, float bw, uint8_t sf, uint8_t cr, uint16_t pre) {
    ++accepts;
    samePrefs(*prefs, before);  // Acceptance must precede all prefs mutations.
    assert(preamble == 32);
    assert(freq == patched.frequencyHz / 1000000.0f);
    assert(bw == patched.bandwidthHz / 1000.0f);
    assert(sf == patched.sf && cr == patched.cr && pre == patched.preamble);
    return accepted;
  }
  uint16_t primaryPreamble() const { return preamble; }
  bool savePrimaryPreamble(uint16_t value) {
    ++saves;
    samePrefs(*prefs, before);  // Persist preamble before publishing the tuple.
    preamble = value;  // Even an unsuccessful writer may have published bytes.
    return preambleSave;
  }
  void adoptPrimaryPreamble(uint16_t value) { ++adopts; preamble = value; }
  bool restorePrimaryPreamble(uint16_t value) {
    ++restores;
    if (preambleRestore) preamble = value;
    return preambleRestore;
  }
};

struct ProfileRadio {
  Profiles* active;
  Profiles* profiles() const { return active; }
};

struct DataStore {
  MockFS fs;
  MockFS* _fs = &fs;
  bool _prefs_load_incomplete = false;
  bool _primary_storage_unavailable = false;
  bool _volatile_primary_fs = false;
  bool _secondary_authority_unknown = false;
  std::vector<bool> saveResults{true};
  std::vector<Prefs> saved;
  bool savePrefs(const Prefs& prefs, double, double) {
    saved.push_back(prefs);
    return saved.size() <= saveResults.size() ? saveResults[saved.size() - 1] : false;
  }
#define BROWSER_RADIO_DEFAULTS_STORE_GATE
#include BROWSER_RADIO_DEFAULTS_STARTUP_IMPLEMENTATION
#undef BROWSER_RADIO_DEFAULTS_STORE_GATE
};

struct MyMesh {
  Prefs _prefs;
  DataStore storage;
  DataStore* _store = &storage;
  Profiles _radio_profiles;
  ProfileRadio attachedRadio{&_radio_profiles};
  ProfileRadio* _radio = &attachedRadio;
  bool _radio_available = true, publishedAvailable = true, rxReady = true;
  unsigned availabilityChanges = 0, rxCalculations = 0;
  struct Sensors { double node_lat = 1, node_lon = 2; } sensors;
  MockDriver radio_driver;
  MyMesh() { _radio_profiles.prefs = &_prefs; _radio_profiles.before = _prefs; }
  void setRadioAvailable(bool available) {
    ++availabilityChanges;
    publishedAvailable = available;
  }
  bool recalcRxPowerSavingFromLevel(uint8_t, uint8_t sf, float bw,
                                  uint16_t, uint32_t* rx, uint32_t* sleep) {
    ++rxCalculations;
    assert(sf == patched.sf && bw == patched.bandwidthHz / 1000.0f);
    *rx = 700; *sleep = 800;
    return rxReady;
  }
#define BROWSER_RADIO_DEFAULTS_COMPANION_SEED
#include BROWSER_RADIO_DEFAULTS_STARTUP_IMPLEMENTATION
#undef BROWSER_RADIO_DEFAULTS_COMPANION_SEED
};

struct CommonCLI {
  Prefs prefs;
  Prefs* _prefs = &prefs;
  Profiles _radio_profiles;
  ProfileRadio attachedRadio{&_radio_profiles};
  struct Callbacks {
    ProfileRadio* active;
    ProfileRadio* getProfileRadio() { return active; }
  } callbacks{&attachedRadio};
  Callbacks* _callbacks = &callbacks;
  bool rxReady = true, saveReady = true;
  unsigned rxCalculations = 0;
  std::vector<Prefs> saved;
  CommonCLI() {
    _radio_profiles.prefs = _prefs; _radio_profiles.before = prefs;
    radio_driver.prefs = _prefs; radio_driver.before = prefs;
  }
  bool isValidLoRaBandwidth(float bandwidth) {
    return bandwidth == patched.bandwidthHz / 1000.0f;
  }
  bool recalculateRxPowerSavingFromLevel(Prefs* value) {
    ++rxCalculations;
    value->rx_ps_rx_us = 700; value->rx_ps_sleep_us = 800;
    return rxReady;
  }
  bool saveCommonPrefs() { saved.push_back(prefs); return saveReady; }
#define BROWSER_RADIO_DEFAULTS_INFRASTRUCTURE_SEED
#if !defined(BROWSER_RADIO_DEFAULTS_NO_TX_VALIDATOR)
#define WRAPPER_CLASS MockDriver
#endif
#include BROWSER_RADIO_DEFAULTS_STARTUP_IMPLEMENTATION
#undef WRAPPER_CLASS
#undef BROWSER_RADIO_DEFAULTS_INFRASTRUCTURE_SEED
};

static void resetImage() {
  imageReadable = true; imageReads = 0; warnings = 0;
  clampCap = 22; radio_driver = MockDriver{};
  patched = {915250000U, 125000U, 7, 5, 40, 16, true};
}

static void seededTuple(const Prefs& prefs, bool txOverride) {
  assert(prefs.freq == 915.25f && prefs.bw == 125.0f);
  assert(prefs.sf == 7 && prefs.cr == 5);
  assert(prefs.tx_power_dbm == (txOverride ? 22 : 7));
}

static void noCompanionChanges(const MyMesh& mesh) {
  samePrefs(mesh._prefs, mesh._radio_profiles.before);
  assert(mesh._radio_profiles.saves == 0 && mesh._radio_profiles.restores == 0);
  assert(mesh.storage.saved.empty());
  assert(mesh._radio_available && mesh.publishedAvailable);
  assert(mesh.availabilityChanges == 0);
}

static void testStoreGate() {
  resetImage();
  DataStore store;
  assert(store.canSeedBrowserRadioDefaults());
  assert(store.fs.probes == 13);
  store.fs.probes = 0;
  store._prefs_load_incomplete = true;
  assert(!store.canSeedBrowserRadioDefaults() && store.fs.probes == 0);
  store._prefs_load_incomplete = false;
  for (bool* flag : {&store._primary_storage_unavailable, &store._volatile_primary_fs,
                    &store._secondary_authority_unknown}) {
    *flag = true;
#if defined(NRF52_PLATFORM)
    assert(!store.canSeedBrowserRadioDefaults() && store.fs.probes == 0);
#else
    assert(store.canSeedBrowserRadioDefaults());
    store.fs.probes = 0;
#endif
    *flag = false;
  }
  store.fs.existing = "/radio_profiles.tmp";
  assert(!store.canSeedBrowserRadioDefaults());
  store.fs.existing.clear(); store.fs.error = "/com_prefs";
  assert(!store.canSeedBrowserRadioDefaults());
  store._fs = nullptr;
  assert(!store.canSeedBrowserRadioDefaults());
}

static void testCompanionSeed() {
  for (unsigned stop = 0; stop < 11; ++stop) {
    resetImage();
    MyMesh mesh;
    if (stop == 1) mesh._radio_available = false;
    if (stop == 2) imageReadable = false;
    if (stop == 3) mesh.storage._prefs_load_incomplete = true;
    if (stop == 4) mesh.storage.fs.existing = "/new_prefs.bak";
    if (stop == 5) mesh._radio_profiles.accepted = false;
    if (stop == 6) mesh.radio_driver.powerValid = false;
    if (stop == 7) mesh.rxReady = false;
    if (stop == 8) mesh._radio = nullptr;
    if (stop == 9) mesh.attachedRadio.active = nullptr;
    if (stop == 10) mesh.storage.fs.error = "/new_prefs";
    mesh.seedBrowserRadioDefaults(stop != 0);
    // Availability was an input in this one case, not a side effect.
    if (stop == 1) mesh._radio_available = true;
    noCompanionChanges(mesh);
    if (stop < 2 || stop == 8 || stop == 9) assert(imageReads == 0);
    if (stop <= 4 || stop >= 8) assert(mesh._radio_profiles.accepts == 0);
  }
  for (bool txOverride : {false, true}) {
    resetImage(); patched.txOverride = txOverride;
    MyMesh mesh;
    mesh.seedBrowserRadioDefaults(true);
    seededTuple(mesh._prefs, txOverride);
    assert(mesh._prefs.rx_ps_rx_us == 700 && mesh._prefs.rx_ps_sleep_us == 800);
    assert(mesh._radio_profiles.preamble == patched.preamble);
    assert(mesh._radio_profiles.accepts == 1 && mesh._radio_profiles.saves == 1);
    assert(mesh._radio_profiles.restores == 0 && mesh.storage.saved.size() == 1);
    seededTuple(mesh.storage.saved.front(), txOverride);
    assert(mesh._radio_available && mesh.publishedAvailable && warnings == 0);
  }
  for (bool restored : {false, true}) {
    resetImage(); MyMesh mesh;
    mesh._radio_profiles.preambleSave = false;
    mesh._radio_profiles.preambleRestore = restored;
    mesh.seedBrowserRadioDefaults(true);
    samePrefs(mesh._prefs, mesh._radio_profiles.before);
    assert(mesh.storage.saved.empty());
    assert(mesh._radio_profiles.saves == 1 && mesh._radio_profiles.restores == 1);
    assert(mesh._radio_profiles.adopts == 1);
    assert(mesh._radio_available == restored && mesh.publishedAvailable == restored);
    assert(mesh.availabilityChanges == (restored ? 0U : 1U));
    assert(warnings == (restored ? 0U : 1U));
  }
  for (bool preambleRestored : {false, true}) {
    for (bool prefsRestored : {false, true}) {
      resetImage(); MyMesh mesh;
      mesh.storage.saveResults = {false, prefsRestored};
      mesh._radio_profiles.preambleRestore = preambleRestored;
      mesh.seedBrowserRadioDefaults(true);
      samePrefs(mesh._prefs, mesh._radio_profiles.before);
      assert(mesh.storage.saved.size() == 2);
      seededTuple(mesh.storage.saved[0], true);
      samePrefs(mesh.storage.saved[1], mesh._radio_profiles.before);
      assert(mesh._radio_profiles.restores == 1);
      const bool confirmed = preambleRestored && prefsRestored;
      assert(mesh._radio_available == confirmed && mesh.publishedAvailable == confirmed);
      assert(mesh.availabilityChanges == (confirmed ? 0U : 1U));
      assert(warnings == (confirmed ? 0U : 1U));
    }
  }
  resetImage(); MyMesh noRx;
  noRx._prefs.rx_powersaving_enabled = false;
  noRx._radio_profiles.before = noRx._prefs;
  noRx.rxReady = false;
  noRx.seedBrowserRadioDefaults(true);
  assert(noRx.rxCalculations == 0 && noRx.storage.saved.size() == 1);
}

static void testInfrastructureSeed() {
  for (unsigned stop = 0; stop < 9; ++stop) {
    resetImage(); CommonCLI cli; MockFS fs;
#if defined(BROWSER_RADIO_DEFAULTS_NO_TX_VALIDATOR)
    patched.txOverride = false;
#endif
    const Prefs before = cli.prefs;
    if (stop == 1) imageReadable = false;
    if (stop == 2) fs.existing = "/com_prefs.tmp";
    if (stop == 3) fs.error = "/radio_profiles.reply";
    if (stop == 4) cli._radio_profiles.accepted = false;
    if (stop == 5) cli.rxReady = false;
    if (stop == 6) cli.saveReady = false;
    if (stop == 7) cli.callbacks.active = nullptr;
    if (stop == 8) cli.attachedRadio.active = nullptr;
    cli.seedInfrastructure(stop != 0, &fs);
    samePrefs(cli.prefs, before);
    assert(cli.saved.size() == (stop == 6 ? 1U : 0U));
    if (stop <= 3 || stop >= 7) assert(cli._radio_profiles.accepts == 0);
    if (stop == 0) assert(imageReads == 0);
    if (stop == 5) assert(cli.rxCalculations == 1); // Failed recalc mutated RX first.
    if (stop == 6) {
      seededTuple(cli.saved[0], patched.txOverride);
      assert(cli.saved[0].primary_radio_preamble == patched.preamble);
      assert(cli.saved[0].rx_ps_rx_us == 700 && cli.saved[0].rx_ps_sleep_us == 800);
    }
  }
  for (bool txOverride : {false, true}) {
    resetImage(); patched.txOverride = txOverride;
    CommonCLI cli; MockFS fs;
    cli.seedInfrastructure(true, &fs);
#if defined(BROWSER_RADIO_DEFAULTS_NO_TX_VALIDATOR)
    if (txOverride) {
      samePrefs(cli.prefs, cli._radio_profiles.before);
      assert(cli.saved.empty() && cli._radio_profiles.accepts == 0);
      assert(radio_driver.checks == 0);
      continue;
    }
    assert(radio_driver.checks == 0);
#else
    assert(radio_driver.checks == 1 && radio_driver.lastPower == (txOverride ? 22 : 7));
#endif
    seededTuple(cli.prefs, txOverride);
    assert(cli._radio_profiles.accepts == 1 && cli.saved.size() == 1);
    assert(cli.prefs.primary_radio_preamble == patched.preamble);
    assert(cli.prefs.rx_ps_rx_us == 700 && cli.prefs.rx_ps_sleep_us == 800);
    samePrefs(cli.saved[0], cli.prefs);
  }
}

static void testInfrastructureTxSafety() {
  resetImage(); clampCap = 40; // Simulate a board without a configured TX cap.
  CommonCLI rejected; MockFS fs;
  rejected.seedInfrastructure(true, &fs);
  samePrefs(rejected.prefs, rejected._radio_profiles.before);
  assert(rejected.saved.empty() && rejected._radio_profiles.accepts == 0);
  assert(rejected.rxCalculations == 0 && rejected._radio_profiles.preamble == 32);
#if defined(BROWSER_RADIO_DEFAULTS_NO_TX_VALIDATOR)
  assert(radio_driver.checks == 0); // Unknown driver cannot accept any override.
#else
  assert(radio_driver.checks == 1 && radio_driver.lastPower == 40);
#endif

  for (int8_t power : {int8_t(-9), int8_t(17), int8_t(22)}) {
    resetImage(); clampCap = 40; patched.txPowerDbm = power;
    CommonCLI cli; MockFS absent;
    cli.seedInfrastructure(true, &absent);
#if defined(BROWSER_RADIO_DEFAULTS_NO_TX_VALIDATOR)
    samePrefs(cli.prefs, cli._radio_profiles.before);
    assert(cli.saved.empty() && cli._radio_profiles.accepts == 0);
    assert(cli.rxCalculations == 0 && radio_driver.checks == 0);
#else
    assert(radio_driver.checks == 1 && radio_driver.lastPower == power);
    assert(cli._radio_profiles.accepts == 1 && cli.saved.size() == 1);
    assert(cli.prefs.tx_power_dbm == power && cli.saved[0].tx_power_dbm == power);
    assert(cli.prefs.freq == 915.25f && cli.prefs.bw == 125.0f);
    assert(cli.prefs.primary_radio_preamble == patched.preamble);
#endif
  }

  resetImage(); clampCap = 40; patched.txOverride = false;
  CommonCLI compiledPower; MockFS absent;
#if defined(BROWSER_RADIO_DEFAULTS_NO_TX_VALIDATOR)
  radio_driver.powerValid = false; // No validator call is allowed in this variant.
#endif
  compiledPower.seedInfrastructure(true, &absent);
  seededTuple(compiledPower.prefs, false);
  assert(compiledPower.saved.size() == 1 && compiledPower._radio_profiles.accepts == 1);
  assert(compiledPower.prefs.primary_radio_preamble == patched.preamble);
#if defined(BROWSER_RADIO_DEFAULTS_NO_TX_VALIDATOR)
  assert(radio_driver.checks == 0);
#else
  assert(radio_driver.checks == 1 && radio_driver.lastPower == 7);
#endif
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (std::strcmp(argv[1], "store_gate") == 0) testStoreGate();
  else if (std::strcmp(argv[1], "companion") == 0) testCompanionSeed();
  else if (std::strcmp(argv[1], "infrastructure") == 0) testInfrastructureSeed();
  else if (std::strcmp(argv[1], "infrastructure_tx") == 0) testInfrastructureTxSafety();
  else assert(false);
}

#else

static defaults::Settings sentinel() {
  return {123456789U, 98765U, 13, 9, -7, 4321, true};
}

static void assertUnchanged(const defaults::Settings& actual,
                            const defaults::Settings& expected) {
  assert(actual.frequencyHz == expected.frequencyHz);
  assert(actual.bandwidthHz == expected.bandwidthHz);
  assert(actual.sf == expected.sf);
  assert(actual.cr == expected.cr);
  assert(actual.txPowerDbm == expected.txPowerDbm);
  assert(actual.preamble == expected.preamble);
  assert(actual.txOverride == expected.txOverride);
}

#if defined(BROWSER_RADIO_DEFAULTS_IMAGE_ONLY)

// This build deliberately contains no second copy of the patch marker. The
// Python harness inspects the linked binary and validates these emitted bytes.
int main(int argc, char** argv) {
  const defaults::Settings before = sentinel();
  defaults::Settings result = before;
  if (argc == 2 && std::strcmp(argv[1], "patched-read") == 0) {
    // The harness changes this executable's on-disk record after linking.
    // Exercise production read(), not a local copy or test-only decoder.
    assert(defaults::read(result));
    const defaults::Settings expected = {915250000U, 125000U, 10, 7, -3, 257, true};
    assertUnchanged(result, expected);
    std::puts("patched-read OK");
    return 0;
  }
  assert(argc == 1);
  assert(!defaults::read(result));
  assertUnchanged(result, before);
  uint8_t bytes[64];
  const volatile uint8_t* flash = defaults::image;
  for (size_t i = 0; i < sizeof(bytes); ++i) bytes[i] = flash[i];
  assert(!defaults::decode(bytes, sizeof(bytes), result));
  assertUnchanged(result, before);
  assert(std::fwrite(bytes, 1, sizeof(bytes), stdout) == sizeof(bytes));
  return 0;
}

#else

using Image = std::array<uint8_t, 64>;

// Independent CRC implementation: fixtures must not inherit a production
// checksum bug, including when testing a malformed record with a valid CRC.
static uint32_t referenceChecksum(const uint8_t* bytes, size_t length) {
  uint32_t crc = 0xffffffffU;
  for (size_t i = 0; i < length; ++i) {
    crc ^= bytes[i];
    for (unsigned bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ ((crc & 1U) ? 0xedb88320U : 0U);
    }
  }
  return crc ^ 0xffffffffU;
}

static void put16(Image& image, size_t offset, uint16_t value) {
  image[offset] = static_cast<uint8_t>(value);
  image[offset + 1] = static_cast<uint8_t>(value >> 8);
}

static void put32(Image& image, size_t offset, uint32_t value) {
  for (unsigned byte = 0; byte < 4; ++byte) {
    image[offset + byte] = static_cast<uint8_t>(value >> (8 * byte));
  }
}

static void seal(Image& image) {
  put32(image, 60, referenceChecksum(image.data(), 60));
}

static Image enabledImage() {
  Image image{};
  const uint8_t magic[16] = {'M', 'C', 'R', 'D', 'E', 'F', 'A', 'U',
                             'L', 'T', 'S', '0', '0', '0', '1', 0};
  std::memcpy(image.data(), magic, sizeof(magic));
  put16(image, 16, 1);
  put16(image, 18, 64);
  put32(image, 20, 3);
  put32(image, 24, 915250000U);
  put32(image, 28, 125000U);
  image[32] = 10;
  image[33] = 7;
  image[34] = static_cast<uint8_t>(-3);
  put16(image, 36, 257);
  seal(image);
  return image;
}

static void reject(const uint8_t* bytes, size_t length) {
  const defaults::Settings before = sentinel();
  defaults::Settings result = before;
  std::array<uint8_t, sizeof(result)> representation;
  std::memcpy(representation.data(), &result, sizeof(result));
  assert(!defaults::decode(bytes, length, result));
  assertUnchanged(result, before);
  assert(std::memcmp(representation.data(), &result, sizeof(result)) == 0);
}

static void reject(const Image& image) {
  reject(image.data(), image.size());
}

static defaults::Settings accept(const Image& image) {
  defaults::Settings result = sentinel();
  assert(defaults::decode(image.data(), image.size(), result));
  return result;
}

static void expectResult(const Image& image, bool valid) {
  if (valid) {
    accept(image);
  } else {
    reject(image);
  }
}

static void testEndianAndChecksum() {
  // Cross-checked with Python struct.pack('<HHIII') and zlib.crc32.
  const Image golden = {{
    0x4d, 0x43, 0x52, 0x44, 0x45, 0x46, 0x41, 0x55,
    0x4c, 0x54, 0x53, 0x30, 0x30, 0x30, 0x31, 0x00,
    0x01, 0x00, 0x40, 0x00, 0x03, 0x00, 0x00, 0x00,
    0x50, 0x9b, 0x8d, 0x36, 0x48, 0xe8, 0x01, 0x00,
    0x0a, 0x07, 0xfd, 0x00, 0x01, 0x01, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x5f, 0x1b, 0xdf, 0xf3
  }};
  assert(enabledImage() == golden);
  assert(defaults::checksum(golden.data(), 60) == 0xf3df1b5fU);
  const uint8_t standard[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  assert(defaults::checksum(standard, sizeof(standard)) == 0xcbf43926U);
  assert(defaults::checksum(standard, 0) == 0U);
  for (size_t length = 0; length <= golden.size(); ++length) {
    assert(defaults::checksum(golden.data(), length) ==
           referenceChecksum(golden.data(), length));
  }
  const defaults::Settings expected = {915250000U, 125000U, 10, 7, -3, 257, true};
  assertUnchanged(accept(golden), expected);
  // A decoder must not require aligned 16- or 32-bit field pointers.
  std::array<uint8_t, 65> unaligned{};
  std::memcpy(unaligned.data() + 1, golden.data(), golden.size());
  defaults::Settings result = sentinel();
  assert(defaults::decode(unaligned.data() + 1, golden.size(), result));
  assertUnchanged(result, expected);
  // Every bit, including all four CRC bytes, is covered by integrity checks.
  for (size_t offset = 0; offset < golden.size(); ++offset) {
    for (unsigned bit = 0; bit < 8; ++bit) {
      Image changed = golden;
      changed[offset] ^= static_cast<uint8_t>(1U << bit);
      reject(changed);
    }
  }
  Image swapped = golden;
  for (size_t i = 0; i < 2; ++i) {
    const uint8_t byte = swapped[60 + i];
    swapped[60 + i] = swapped[63 - i];
    swapped[63 - i] = byte;
  }
  reject(swapped);
}

static void testMetadataAndLength() {
  const Image image = enabledImage();
  reject(nullptr, 0);
  reject(nullptr, image.size());
  for (size_t length = 0; length < image.size(); ++length) {
    reject(image.data(), length);
  }
  for (size_t length : {size_t(65), size_t(128),
                         std::numeric_limits<size_t>::max()}) {
    reject(image.data(), length);
  }
  for (size_t offset = 0; offset < 16; ++offset) {
    Image changed = image;
    changed[offset] ^= 1;
    seal(changed);
    reject(changed);
  }
  for (uint16_t version : {uint16_t(0), uint16_t(2), uint16_t(0x100),
                            uint16_t(0xffff)}) {
    Image changed = image;
    put16(changed, 16, version);
    seal(changed);
    reject(changed);
  }
  for (uint16_t length : {uint16_t(0), uint16_t(60), uint16_t(63),
                           uint16_t(65), uint16_t(0x4000), uint16_t(0xffff)}) {
    Image changed = image;
    put16(changed, 18, length);
    seal(changed);
    reject(changed);
  }
}

static void testFlagsAndReserved() {
  for (unsigned bit = 2; bit < 32; ++bit) {
    Image changed = enabledImage();
    put32(changed, 20, 3U | (uint32_t(1) << bit));
    seal(changed);
    reject(changed);
  }
  for (size_t offset = 35; offset < 60; ++offset) {
    if (offset == 36 || offset == 37) continue;
    for (uint8_t value : {uint8_t(1), uint8_t(0x80), uint8_t(0xff)}) {
      Image changed = enabledImage();
      changed[offset] = value;
      seal(changed);
      reject(changed);
    }
  }
  // The signed TX field is only meaningful with the override bit present.
  for (unsigned byte = 0; byte <= 255; ++byte) {
    Image changed = enabledImage();
    put32(changed, 20, 1);
    changed[34] = static_cast<uint8_t>(byte);
    seal(changed);
    if (byte == 0) {
      const defaults::Settings result = accept(changed);
      assert(!result.txOverride && result.txPowerDbm == 0);
    } else {
      reject(changed);
    }
  }
}

static void testBounds() {
  for (uint32_t frequency : {0U, 149999999U, 150000000U, 150000001U,
                              2500000000U, 2500000001U, 0xffffffffU}) {
    Image changed = enabledImage();
    put32(changed, 24, frequency);
    seal(changed);
    expectResult(changed, frequency >= 150000000U && frequency <= 2500000000U);
  }
  const uint32_t bandwidths[] = {7800U, 10400U, 15600U, 20800U, 31250U,
                                41700U, 62500U, 125000U, 250000U, 500000U};
  for (uint32_t bandwidth : bandwidths) {
    for (int delta : {-1, 0, 1}) {
      Image changed = enabledImage();
      put32(changed, 28, bandwidth + delta);
      seal(changed);
      if (delta == 0) assert(accept(changed).bandwidthHz == bandwidth);
      else reject(changed);
    }
  }
  for (uint32_t bandwidth : {0U, 3900U, 7813U, 10417U, 15625U,
                              20833U, 41667U, 800000U, 0xffffffffU}) {
    Image changed = enabledImage();
    put32(changed, 28, bandwidth);
    seal(changed);
    reject(changed);
  }
  for (unsigned value = 0; value <= 255; ++value) {
    Image changed = enabledImage();
    changed[32] = static_cast<uint8_t>(value);
    seal(changed);
    expectResult(changed, value >= 5 && value <= 12);
    changed = enabledImage();
    changed[33] = static_cast<uint8_t>(value);
    seal(changed);
    expectResult(changed, value >= 4 && value <= 8);
    changed = enabledImage();
    changed[34] = static_cast<uint8_t>(value);
    seal(changed);
    const int signedValue = value < 128 ? int(value) : int(value) - 256;
    if (signedValue >= -9 && signedValue <= 40) {
      const defaults::Settings result = accept(changed);
      assert(result.txOverride && result.txPowerDbm == signedValue);
    } else {
      reject(changed);
    }
  }
  // Exhaust the entire uint16_t domain, including the automatic sentinel 0.
  for (uint32_t preamble = 0; preamble <= 65535U; ++preamble) {
    Image changed = enabledImage();
    put16(changed, 36, static_cast<uint16_t>(preamble));
    seal(changed);
    if (preamble == 0 || (preamble >= 8 && preamble <= 65528)) {
      assert(accept(changed).preamble == preamble);
    } else {
      reject(changed);
    }
  }
}

static void testDisabled() {
  Image disabled = enabledImage();
  put32(disabled, 20, 0);
  for (size_t offset = 24; offset < 60; ++offset) disabled[offset] = 0;
  seal(disabled);
  assert(defaults::checksum(disabled.data(), 60) ==
         referenceChecksum(disabled.data(), 60));
  reject(disabled);
  // Disabled and tx-only records must never produce an enabled result, even
  // when their radio tuple would otherwise be valid.
  for (uint32_t flags : {0U, 2U}) {
    Image changed = enabledImage();
    put32(changed, 20, flags);
    seal(changed);
    reject(changed);
    changed = disabled;
    put32(changed, 20, flags);
    seal(changed);
    reject(changed);
  }
  for (size_t offset = 24; offset < 60; ++offset) {
    Image changed = disabled;
    changed[offset] = 1;
    seal(changed);
    reject(changed);
  }
}

static void testValidBoundaries() {
  for (bool high : {false, true}) {
    for (bool txOverride : {false, true}) {
      for (uint16_t preamble : {uint16_t(0), uint16_t(8), uint16_t(65528)}) {
        Image changed = enabledImage();
        put32(changed, 20, txOverride ? 3 : 1);
        put32(changed, 24, high ? 2500000000U : 150000000U);
        put32(changed, 28, high ? 500000U : 7800U);
        changed[32] = high ? 12 : 5;
        changed[33] = high ? 8 : 5;
        changed[34] = txOverride ? static_cast<uint8_t>(high ? 40 : -9) : 0;
        put16(changed, 36, preamble);
        seal(changed);
        const defaults::Settings expected = {
          high ? 2500000000U : 150000000U, high ? 500000U : 7800U,
          static_cast<uint8_t>(high ? 12 : 5), static_cast<uint8_t>(high ? 8 : 5),
          static_cast<int8_t>(txOverride ? (high ? 40 : -9) : 0),
          preamble, txOverride
        };
        assertUnchanged(accept(changed), expected);
      }
    }
  }
}

static void testFreshStore() {
  const char* const expected[] = {
    "/new_prefs", "/new_prefs.bak", "/new_prefs.tmp", "/node_prefs",
    "/node_prefs.bak", "/com_prefs", "/com_prefs.bak", "/com_prefs.tmp",
    "/prefs.json", "/radio_profiles", "/radio_profiles.bak",
    "/radio_profiles.tmp", "/radio_profiles.reply"
  };
  constexpr size_t count = sizeof(expected) / sizeof(expected[0]);
  size_t calls = 0;
  assert(defaults::freshStore([&](const char* path, bool& present) {
    assert(calls < count && std::strcmp(path, expected[calls]) == 0);
    ++calls;
    present = false;
    return true;
  }));
  assert(calls == count);
  // Metadata alone is authoritative: committed, staged, backup and corrupt
  // files alike block seeding. No successful content read is required.
  for (size_t blocked = 0; blocked < count; ++blocked) {
    calls = 0;
    assert(!defaults::freshStore([&](const char* path, bool& present) {
      ++calls;
      present = std::strcmp(path, expected[blocked]) == 0;
      return true;
    }));
    assert(calls == blocked + 1);
    for (bool errorPresence : {false, true}) {
      calls = 0;
      assert(!defaults::freshStore([&](const char* path, bool& present) {
        ++calls;
        const bool error = std::strcmp(path, expected[blocked]) == 0;
        present = error && errorPresence;
        return !error;
      }));
      assert(calls == blocked + 1);
    }
  }
  // A callback that leaves the out parameter untouched has not proved absence.
  assert(!defaults::freshStore([](const char*, bool&) { return true; }));
  assert(!defaults::freshStore([](const char*, bool&) { return false; }));
}

int main(int argc, char** argv) {
  assert(argc == 2);
  if (std::strcmp(argv[1], "endian_crc") == 0) testEndianAndChecksum();
  else if (std::strcmp(argv[1], "metadata") == 0) testMetadataAndLength();
  else if (std::strcmp(argv[1], "flags_reserved") == 0) testFlagsAndReserved();
  else if (std::strcmp(argv[1], "bounds") == 0) testBounds();
  else if (std::strcmp(argv[1], "disabled") == 0) testDisabled();
  else if (std::strcmp(argv[1], "boundaries") == 0) testValidBoundaries();
  else if (std::strcmp(argv[1], "fresh_store") == 0) testFreshStore();
  else assert(false);
  return 0;
}

#endif

#endif
