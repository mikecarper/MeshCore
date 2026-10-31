#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY

#include "UniversalLoRaRadio.h"
#include <new>
#include <helpers/RegulatorSettings.h>
#if MESH_RADIO_SWITCH_TUNING
#include <helpers/RadioProfileCLI.h>
#endif

#if MESH_RADIO_AUTO_SX127X
extern const char meshcore_capability_universal_lora_radio[];
const char meshcore_capability_universal_lora_radio[] __attribute__((used)) =
    "meshcore.capability.radio.autodetect.sx126x_sx127x.v1";
#endif
#if MESH_RADIO_AUTO_SX1268
extern const char meshcore_capability_universal_sx126x_radio[];
const char meshcore_capability_universal_sx126x_radio[] __attribute__((used)) =
    "meshcore.capability.radio.autodetect.sx1262_sx1268.v1";
#endif
#if MESH_RADIO_AUTO_LR1121
extern const char meshcore_capability_universal_lr1121_radio[];
const char meshcore_capability_universal_lr1121_radio[] __attribute__((used)) =
    "meshcore.capability.radio.autodetect.sx1262_lr1121.v1";
#endif

UniversalLoRaRadio::UniversalLoRaRadio(Module& sx126_module, Module& sx127_module,
                                     mesh::MainBoard& board
#if MESH_RADIO_AUTO_LR1121
                                     , Module* lr1121_module
#endif
                                     )
    : sx126_module_(sx126_module), sx127_module_(sx127_module),
#if MESH_RADIO_AUTO_LR1121
      lr1121_module_(lr1121_module),
#endif
      board_(board) {}

UniversalLoRaRadio::~UniversalLoRaRadio() {
#if MESH_RADIO_AUTO_LR1121
  if (mesh::autoLoRaRxPowerSavingFamily() == family_)
    mesh::autoLoRaRxPowerSavingFamily() = mesh::LoRaRadioFamily::None;
#endif
  if (family_ == mesh::LoRaRadioFamily::SX126x) {
    static_cast<SX1262Backend*>(backend_)->~SX1262Backend();
    static_cast<CustomSX1262*>(physical_)->~CustomSX1262();
  }
#if MESH_RADIO_AUTO_SX1268
  else if (family_ == mesh::LoRaRadioFamily::SX1268) {
    static_cast<SX1268Backend*>(backend_)->~SX1268Backend();
    static_cast<CustomSX1268*>(physical_)->~CustomSX1268();
  }
#endif
#if MESH_RADIO_AUTO_SX127X
  else if (family_ == mesh::LoRaRadioFamily::SX127x) {
    static_cast<SX127Backend*>(backend_)->~SX127Backend();
    static_cast<CustomSX1276*>(physical_)->~CustomSX1276();
  }
#endif
#if MESH_RADIO_AUTO_LR1121
  else if (family_ == mesh::LoRaRadioFamily::LR1121) {
    static_cast<LR1121Backend*>(backend_)->~LR1121Backend();
    static_cast<CustomLR1121*>(physical_)->~CustomLR1121();
  }
#endif
}

bool UniversalLoRaRadio::selectFamily(mesh::LoRaRadioFamily family) {
#if MESH_RADIO_AUTO_SX127X
  volatile const char* capability = meshcore_capability_universal_lora_radio;
  (void)*capability;
#endif
#if MESH_RADIO_AUTO_LR1121
  volatile const char* lr1121_capability = meshcore_capability_universal_lr1121_radio;
  (void)*lr1121_capability;
#endif
#if MESH_RADIO_AUTO_SX1268
  volatile const char* sx126x_capability = meshcore_capability_universal_sx126x_radio;
  (void)*sx126x_capability;
#endif
  // Selection never touches SPI. The board must positively identify the family
  // before calling this; a later hardware failure cannot rebind active IRQs.
  if (family != mesh::LoRaRadioFamily::SX126x
#if MESH_RADIO_AUTO_SX1268
      && family != mesh::LoRaRadioFamily::SX1268
#endif
#if MESH_RADIO_AUTO_SX127X
      && family != mesh::LoRaRadioFamily::SX127x
#endif
#if MESH_RADIO_AUTO_LR1121
      && family != mesh::LoRaRadioFamily::LR1121
#endif
      ) return false;
  if (backend_) return family_ == family;
#if MESH_RADIO_AUTO_LR1121
  if (family == mesh::LoRaRadioFamily::LR1121
      && (!lr1121_module_ || !lr1121_initializer_ || !lr1121_deep_init_)) return false;
#endif
  if (family == mesh::LoRaRadioFamily::SX126x) {
    auto* chip = new (&chip_storage_) CustomSX1262(&sx126_module_);
    physical_ = chip;
    backend_ = new (&driver_storage_) SX1262Backend(*chip, board_);
  }
#if MESH_RADIO_AUTO_SX1268
  else if (family == mesh::LoRaRadioFamily::SX1268) {
    auto* chip = new (&chip_storage_) CustomSX1268(&sx126_module_);
    physical_ = chip;
    backend_ = new (&driver_storage_) SX1268Backend(*chip, board_);
  }
#endif
#if MESH_RADIO_AUTO_SX127X
  else if (family == mesh::LoRaRadioFamily::SX127x) {
    auto* chip = new (&chip_storage_) CustomSX1276(&sx127_module_);
    physical_ = chip;
    backend_ = new (&driver_storage_) SX127Backend(*chip, board_);
  }
#endif
#if MESH_RADIO_AUTO_LR1121
  else if (family == mesh::LoRaRadioFamily::LR1121) {
    auto* chip = new (&chip_storage_) CustomLR1121(lr1121_module_);
    physical_ = chip;
    auto* driver = new (&driver_storage_) LR1121Backend(*chip, board_);
    driver->setDeepInitCallback(lr1121_deep_init_);
    backend_ = driver;
  }
#endif
  family_ = family;
#if MESH_RADIO_AUTO_LR1121
  mesh::autoLoRaRxPowerSavingFamily() = family;
#endif
  return true;
}

bool UniversalLoRaRadio::stdInit(SPIClass* spi) {
  if (!backend_ || !mesh::power::reapplyDeferredRadioRegulator(*this)) return false;
#if MESH_RADIO_SWITCH_TUNING
  if (!mesh::RadioProfileCLI::reapplyDeferredBootTuning(*this)) return false;
#endif
  if (family_ == mesh::LoRaRadioFamily::SX126x)
    return static_cast<CustomSX1262*>(physical_)->std_init(spi);
#if MESH_RADIO_AUTO_SX1268
  if (family_ == mesh::LoRaRadioFamily::SX1268)
    return static_cast<CustomSX1268*>(physical_)->std_init(spi);
#endif
#if MESH_RADIO_AUTO_SX127X
  if (family_ == mesh::LoRaRadioFamily::SX127x)
    return static_cast<CustomSX1276*>(physical_)->std_init(spi);
#endif
#if MESH_RADIO_AUTO_LR1121
  if (family_ == mesh::LoRaRadioFamily::LR1121)
    return lr1121_initializer_(*static_cast<CustomLR1121*>(physical_), spi);
#endif
  return false;
}

#if MESH_RADIO_AUTO_LR1121
bool UniversalLoRaRadio::setLR1121Initializer(LR1121Initializer initialize,
                                            bool (*deep_init)()) {
  if (backend_ || !initialize || !deep_init) return false;
  lr1121_initializer_ = initialize;
  lr1121_deep_init_ = deep_init;
  return true;
}
#endif

const char* UniversalLoRaRadio::familyName() const {
  if (family_ == mesh::LoRaRadioFamily::SX126x) return "SX126x";
#if MESH_RADIO_AUTO_SX1268
  if (family_ == mesh::LoRaRadioFamily::SX1268) return "SX1268";
#endif
#if MESH_RADIO_AUTO_LR1121
  if (family_ == mesh::LoRaRadioFamily::LR1121) return "LR1121";
#endif
#if MESH_RADIO_AUTO_SX127X
  if (family_ == mesh::LoRaRadioFamily::SX127x) return "SX127x";
#endif
  return "none";
}

void UniversalLoRaRadio::powerOff() {
  if (family_ == mesh::LoRaRadioFamily::SX126x)
    static_cast<SX1262Backend*>(backend_)->powerOff();
#if MESH_RADIO_AUTO_SX1268
  else if (family_ == mesh::LoRaRadioFamily::SX1268)
    static_cast<SX1268Backend*>(backend_)->powerOff();
#endif
#if MESH_RADIO_AUTO_SX127X
  else if (family_ == mesh::LoRaRadioFamily::SX127x)
    static_cast<SX127Backend*>(backend_)->powerOff();
#endif
#if MESH_RADIO_AUTO_LR1121
  else if (family_ == mesh::LoRaRadioFamily::LR1121)
    static_cast<LR1121Backend*>(backend_)->powerOff();
#endif
}

#if MESH_RADIO_AUTO_LR1121
bool UniversalLoRaRadio::SX1262Backend::validateProfile(
    const mesh::RadioProfileParams& params) const {
  if (!mesh::RadioProfiles::valid(params, false)
      || params.freq < 150.0f || params.freq > 960.0f || params.bw > 500.0f) return false;
  if (params.protocol == mesh::RadioProfileProtocol::Meshtext && !supportsMeshtext()) return false;
  if (params.protocol == mesh::RadioProfileProtocol::Ethernet && !supportsEthernetTunnel()) return false;
#if MESH_ENABLE_HOST_KISS
  if (params.protocol == mesh::RadioProfileProtocol::Kiss && !supportsHostKiss()) return false;
#else
  if (params.protocol == mesh::RadioProfileProtocol::Kiss) return false;
#endif
  return true;
}

bool UniversalLoRaRadio::LR1121Backend::validateProfile(
    const mesh::RadioProfileParams& params) const {
  if (!mesh::RadioProfiles::valid(params)) return false;
  const bool high_band = (params.freq >= 1900 && params.freq <= 2200)
      || (params.freq >= 2400 && params.freq <= 2500);
  if (!(params.freq >= 150 && params.freq <= 960) && !high_band) return false;
  if (!(params.bw == 62.5f || params.bw == 125 || params.bw == 250 || params.bw == 500
      || (high_band && (params.bw == 203.125f || params.bw == 406.25f || params.bw == 812.5f))))
    return false;
  if (params.protocol == mesh::RadioProfileProtocol::Meshtext && !supportsMeshtext()) return false;
  if (params.protocol == mesh::RadioProfileProtocol::Ethernet) return false;
#if MESH_ENABLE_HOST_KISS
  if (params.protocol == mesh::RadioProfileProtocol::Kiss && !supportsHostKiss()) return false;
#else
  if (params.protocol == mesh::RadioProfileProtocol::Kiss) return false;
#endif
  return true;
}

bool UniversalLoRaRadio::LR1121Backend::checkBeaconOutputPower(
    float frequency_mhz, int8_t power) const {
  if (frequency_mhz > 1000.0f) return power >= -18 && power <= 13;
#ifdef LR1121_FORCE_HP_PA
  return power >= -9 && power <= 22;
#else
  return power >= -17 && power <= 22;
#endif
}
#endif

#if MESH_RADIO_AUTO_SX1268
bool UniversalLoRaRadio::SX1268Backend::validateProfile(
    const mesh::RadioProfileParams& params) const {
  if (!mesh::RadioProfiles::valid(params, false)
      || params.freq < 410.0f || params.freq > 810.0f || params.bw > 500.0f) return false;
  if (params.protocol == mesh::RadioProfileProtocol::Meshtext && !supportsMeshtext()) return false;
  if (params.protocol == mesh::RadioProfileProtocol::Ethernet && !supportsEthernetTunnel()) return false;
#if MESH_ENABLE_HOST_KISS
  if (params.protocol == mesh::RadioProfileProtocol::Kiss && !supportsHostKiss()) return false;
#else
  if (params.protocol == mesh::RadioProfileProtocol::Kiss) return false;
#endif
  return true;
}
#endif

#if MESH_RADIO_AUTO_SX127X
bool UniversalLoRaRadio::SX127Backend::validateProfile(
    const mesh::RadioProfileParams& params) const {
  // The common .cpp is compiled once for a universal image. Keep SX127x's
  // actual SF/range and protocol capabilities instead of its SX126x defaults.
  if (!mesh::RadioProfiles::valid(params, false)
      || params.freq < 137.0f || params.freq > 1020.0f
      || params.sf < 7 || params.bw > 500.0f) return false;
  if (params.protocol == mesh::RadioProfileProtocol::Meshtext && !supportsMeshtext()) return false;
  if (params.protocol == mesh::RadioProfileProtocol::Ethernet && !supportsEthernetTunnel()) return false;
#if MESH_ENABLE_HOST_KISS
  if (params.protocol == mesh::RadioProfileProtocol::Kiss && !supportsHostKiss()) return false;
#else
  if (params.protocol == mesh::RadioProfileProtocol::Kiss) return false;
#endif
  return true;
}

bool UniversalLoRaRadio::SX127Backend::checkBeaconOutputPower(
    float frequency_mhz, int8_t power) const {
  (void)frequency_mhz;
  // Match SX127x::setOutputPower(): values below 2 dBm select RFO, not PA_BOOST.
  return static_cast<SX1276*>(_radio)->checkOutputPower(power, nullptr, power < 2)
      == RADIOLIB_ERR_NONE;
}
#endif

#endif
