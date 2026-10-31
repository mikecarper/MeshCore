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

UniversalLoRaRadio::UniversalLoRaRadio(Module& sx126_module, Module& sx127_module,
                                     mesh::MainBoard& board)
    : sx126_module_(sx126_module), sx127_module_(sx127_module), board_(board) {}

UniversalLoRaRadio::~UniversalLoRaRadio() {
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
}

bool UniversalLoRaRadio::selectFamily(mesh::LoRaRadioFamily family) {
#if MESH_RADIO_AUTO_SX127X
  volatile const char* capability = meshcore_capability_universal_lora_radio;
  (void)*capability;
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
      ) return false;
  if (backend_) return family_ == family;
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
  else {
    auto* chip = new (&chip_storage_) CustomSX1276(&sx127_module_);
    physical_ = chip;
    backend_ = new (&driver_storage_) SX127Backend(*chip, board_);
  }
#endif
  family_ = family;
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
  return false;
}

const char* UniversalLoRaRadio::familyName() const {
  if (family_ == mesh::LoRaRadioFamily::SX126x) return "SX126x";
#if MESH_RADIO_AUTO_SX1268
  if (family_ == mesh::LoRaRadioFamily::SX1268) return "SX1268";
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
}

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
