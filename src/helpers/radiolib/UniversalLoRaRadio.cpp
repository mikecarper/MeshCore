#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY

#include "UniversalLoRaRadio.h"
#include <new>

extern const char meshcore_capability_universal_lora_radio[];
const char meshcore_capability_universal_lora_radio[] __attribute__((used)) =
    "meshcore.capability.radio.autodetect.sx126x_sx127x.v1";

UniversalLoRaRadio::UniversalLoRaRadio(Module& sx126_module, Module& sx127_module,
                                     mesh::MainBoard& board)
    : sx126_module_(sx126_module), sx127_module_(sx127_module), board_(board) {}

UniversalLoRaRadio::~UniversalLoRaRadio() {
  if (family_ == mesh::LoRaRadioFamily::SX126x) {
    static_cast<CustomSX1262Wrapper*>(backend_)->~CustomSX1262Wrapper();
    static_cast<CustomSX1262*>(physical_)->~CustomSX1262();
  } else if (family_ == mesh::LoRaRadioFamily::SX127x) {
    static_cast<SX127Backend*>(backend_)->~SX127Backend();
    static_cast<CustomSX1276*>(physical_)->~CustomSX1276();
  }
}

bool UniversalLoRaRadio::selectFamily(mesh::LoRaRadioFamily family) {
  volatile const char* capability = meshcore_capability_universal_lora_radio;
  (void)*capability;
  // Selection never touches SPI. The board must positively identify the family
  // before calling this; a later hardware failure cannot rebind active IRQs.
  if (family != mesh::LoRaRadioFamily::SX126x
      && family != mesh::LoRaRadioFamily::SX127x) return false;
  if (backend_) return family_ == family;
  if (family == mesh::LoRaRadioFamily::SX126x) {
    auto* chip = new (&chip_storage_) CustomSX1262(&sx126_module_);
    physical_ = chip;
    backend_ = new (&driver_storage_) CustomSX1262Wrapper(*chip, board_);
  } else {
    auto* chip = new (&chip_storage_) CustomSX1276(&sx127_module_);
    physical_ = chip;
    backend_ = new (&driver_storage_) SX127Backend(*chip, board_);
  }
  family_ = family;
  return true;
}

bool UniversalLoRaRadio::stdInit(SPIClass* spi) {
  if (family_ == mesh::LoRaRadioFamily::SX126x)
    return static_cast<CustomSX1262*>(physical_)->std_init(spi);
  if (family_ == mesh::LoRaRadioFamily::SX127x)
    return static_cast<CustomSX1276*>(physical_)->std_init(spi);
  return false;
}

const char* UniversalLoRaRadio::familyName() const {
  if (family_ == mesh::LoRaRadioFamily::SX126x) return "SX126x";
  if (family_ == mesh::LoRaRadioFamily::SX127x) return "SX127x";
  return "none";
}

void UniversalLoRaRadio::powerOff() {
  if (family_ == mesh::LoRaRadioFamily::SX126x)
    static_cast<CustomSX1262Wrapper*>(backend_)->powerOff();
  else if (family_ == mesh::LoRaRadioFamily::SX127x)
    static_cast<SX127Backend*>(backend_)->powerOff();
}

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
