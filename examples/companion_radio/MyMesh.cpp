#include "MyMesh.h"

#include <Arduino.h> // needed for PlatformIO
#include <Mesh.h>
#include <helpers/CompanionHardwareCommandCompat.h>
#include <helpers/CompanionStatusResponse.h>
#include <helpers/IdentityGeneration.h>
#include "helpers/radiolib/RXPowerSaving.h"
#include "helpers/radiolib/RxBoostedGainDefaults.h"
#include "helpers/radiolib/CadTiming.h"

#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS
#include <helpers/CompanionTerminalDiagnostics.h>
#endif

#if defined(ESP32_PLATFORM) \
    && (defined(BOARD_HAS_PSRAM) || COMPANION_FEATURE_MEMORY_DIAGNOSTICS)
#include <esp_heap_caps.h>
#endif

#if defined(ESP32) && defined(WIFI_SSID)
#include "CompanionWiFi.h"
#include <helpers/WiFiPowerSave.h>
#endif

#if defined(ESP32_PLATFORM)
#include <helpers/ESP32TrueRandom.h>
#endif

#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
#include <helpers/esp32/WiFiRadioPolicy.h>
#endif

#include <helpers/CLICommandUtils.h>
#ifdef ENABLE_USB_INTERFACE
#include <helpers/TracePathHelpers.h>
#endif

#if COMPANION_FEATURE_OTA_CLI
#include <helpers/ota/OtaCli.h>
#endif

#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
#include <helpers/MQTTDefaults.h>
#endif

#ifdef WITH_WEBCONFIG
#include <helpers/WiFiSetupPortal.h>
#include <WiFi.h>
#include <esp_wifi.h>
#endif

static uint32_t nextRadioApplyRetryDelay(uint8_t& failure_count) {
  uint8_t shift = failure_count < 5 ? failure_count : 5;
  if (failure_count < 6) failure_count++;
  uint32_t delay_ms = 1000UL << shift;
  return delay_ms > 30000UL ? 30000UL : delay_ms;
}

static const uint32_t COMMAND_RADIO_APPLY_TIMEOUT_MS = 5000UL;

#if RXPS_FIXED_ENABLED
#if RXPS_FIXED_LEVEL < 1 || RXPS_FIXED_LEVEL > 10
#error "RXPS_FIXED_LEVEL must be between 1 and 10"
#endif
#if RXPS_FIXED_PREAMBLE != 16 && RXPS_FIXED_PREAMBLE != 32
#error "RXPS_FIXED_PREAMBLE must be 16 or 32"
#endif
#endif

#define CMD_APP_START                 1
#define CMD_SEND_TXT_MSG              2
#define CMD_SEND_CHANNEL_TXT_MSG      3
#define CMD_GET_CONTACTS              4 // with optional 'since' (for efficient sync)
#define CMD_GET_DEVICE_TIME           5
#define CMD_SET_DEVICE_TIME           6
#define CMD_SEND_SELF_ADVERT          7
#define CMD_SET_ADVERT_NAME           8
#define CMD_ADD_UPDATE_CONTACT        9
#define CMD_SYNC_NEXT_MESSAGE         10
#define CMD_SET_RADIO_PARAMS          11
#define CMD_SET_RADIO_TX_POWER        12
#define CMD_RESET_PATH                13
#define CMD_SET_ADVERT_LATLON         14
#define CMD_REMOVE_CONTACT            15
#define CMD_SHARE_CONTACT             16
#define CMD_EXPORT_CONTACT            17
#define CMD_IMPORT_CONTACT            18
#define CMD_REBOOT                    19
#define CMD_GET_BATT_AND_STORAGE      20   // was CMD_GET_BATTERY_VOLTAGE
#define CMD_SET_TUNING_PARAMS         21
#define CMD_DEVICE_QUERY              22
#define CMD_EXPORT_PRIVATE_KEY        23
#define CMD_IMPORT_PRIVATE_KEY        24
#define CMD_SEND_RAW_DATA             25
#define CMD_SEND_LOGIN                26
#define CMD_SEND_STATUS_REQ           27
#define CMD_HAS_CONNECTION            28
#define CMD_LOGOUT                    29 // 'Disconnect'
#define CMD_GET_CONTACT_BY_KEY        30
#define CMD_GET_CHANNEL               31
#define CMD_SET_CHANNEL               32
#define CMD_SIGN_START                33
#define CMD_SIGN_DATA                 34
#define CMD_SIGN_FINISH               35
#define CMD_SEND_TRACE_PATH           36
#define CMD_SET_DEVICE_PIN            37
#define CMD_SET_OTHER_PARAMS          38
#define CMD_SEND_TELEMETRY_REQ        39  // can deprecate this
#define CMD_GET_CUSTOM_VARS           40
#define CMD_SET_CUSTOM_VAR            41
#define CMD_GET_ADVERT_PATH           42
#define CMD_GET_TUNING_PARAMS         43
// NOTE: decimal command IDs 44..49 (0x2C..0x31) remain parked
#define CMD_SEND_BINARY_REQ           50
#define CMD_FACTORY_RESET             51
#define CMD_SEND_PATH_DISCOVERY_REQ   52
#define CMD_SET_FLOOD_SCOPE_KEY       54   // v8+
#define CMD_SEND_CONTROL_DATA         55   // v8+
#define CMD_GET_STATS                 56   // v8+, second byte is stats type
#define CMD_SEND_ANON_REQ             57
#define CMD_SET_AUTOADD_CONFIG        58
#define CMD_GET_AUTOADD_CONFIG        59
#define CMD_GET_ALLOWED_REPEAT_FREQ   60
#define CMD_SET_PATH_HASH_MODE        61
#define CMD_SEND_CHANNEL_DATA         62
#define CMD_SET_DEFAULT_FLOOD_SCOPE   63
#define CMD_GET_DEFAULT_FLOOD_SCOPE   64
#define CMD_SEND_RAW_PACKET           65

#if defined(RADIO_FEM_RXGAIN) && (RADIO_FEM_RXGAIN == 0)
static constexpr uint8_t DEFAULT_FEM_RX_GAIN = 0;
#else
static constexpr uint8_t DEFAULT_FEM_RX_GAIN = 1;
#endif

// Stats sub-types for CMD_GET_STATS
#define STATS_TYPE_CORE               0
#define STATS_TYPE_RADIO              1
#define STATS_TYPE_PACKETS             2

#define RESP_CODE_OK                  0
#define RESP_CODE_ERR                 1
#define RESP_CODE_CONTACTS_START      2  // first reply to CMD_GET_CONTACTS
#define RESP_CODE_CONTACT             3  // multiple of these (after CMD_GET_CONTACTS)
#define RESP_CODE_END_OF_CONTACTS     4  // last reply to CMD_GET_CONTACTS
#define RESP_CODE_SELF_INFO           5  // reply to CMD_APP_START
#define RESP_CODE_SENT                6  // reply to CMD_SEND_TXT_MSG
#define RESP_CODE_CONTACT_MSG_RECV    7  // a reply to CMD_SYNC_NEXT_MESSAGE (ver < 3)
#define RESP_CODE_CHANNEL_MSG_RECV    8  // a reply to CMD_SYNC_NEXT_MESSAGE (ver < 3)
#define RESP_CODE_CURR_TIME           9  // a reply to CMD_GET_DEVICE_TIME
#define RESP_CODE_NO_MORE_MESSAGES    10 // a reply to CMD_SYNC_NEXT_MESSAGE
#define RESP_CODE_EXPORT_CONTACT      11
#define RESP_CODE_BATT_AND_STORAGE    12 // a reply to a CMD_GET_BATT_AND_STORAGE
#define RESP_CODE_DEVICE_INFO         13 // a reply to CMD_DEVICE_QUERY
#define RESP_CODE_PRIVATE_KEY         14 // a reply to CMD_EXPORT_PRIVATE_KEY
#define RESP_CODE_DISABLED            15
#define RESP_CODE_CONTACT_MSG_RECV_V3 16 // a reply to CMD_SYNC_NEXT_MESSAGE (ver >= 3)
#define RESP_CODE_CHANNEL_MSG_RECV_V3 17 // a reply to CMD_SYNC_NEXT_MESSAGE (ver >= 3)
#define RESP_CODE_CHANNEL_INFO        18 // a reply to CMD_GET_CHANNEL
#define RESP_CODE_SIGN_START          19
#define RESP_CODE_SIGNATURE           20
#define RESP_CODE_CUSTOM_VARS         21
#define RESP_CODE_ADVERT_PATH         22
#define RESP_CODE_TUNING_PARAMS       23
#define RESP_CODE_STATS               24   // v8+, second byte is stats type
#define RESP_CODE_AUTOADD_CONFIG      25
#define RESP_ALLOWED_REPEAT_FREQ      26
#define RESP_CODE_CHANNEL_DATA_RECV   27
#define RESP_CODE_DEFAULT_FLOOD_SCOPE 28
#define RESP_CODE_CLI_REPLY           29  // v14+, a reply to CMD_RUN_CLI_COMMAND

#define MAX_CHANNEL_DATA_LENGTH       (MAX_FRAME_SIZE - 9)

#define SEND_TIMEOUT_BASE_MILLIS        500
#define FLOOD_SEND_TIMEOUT_FACTOR       16.0f
#define DIRECT_SEND_PERHOP_FACTOR       6.0f
#define DIRECT_SEND_PERHOP_EXTRA_MILLIS 250
#define LAZY_CONTACTS_WRITE_DELAY       5000
#define CONTACT_PAGE_WRITE_GAP          100
#define EXPECTED_ACK_RETRY_RECHECK_MILLIS 1000

static bool save_filter(const ContactInfo& c);

#ifndef DEFAULT_MULTI_ACKS
#define DEFAULT_MULTI_ACKS 0
#endif
#ifndef DEFAULT_PATH_HASH_MODE
#define DEFAULT_PATH_HASH_MODE 0
#endif
#ifndef DEFAULT_MANUAL_ADD_CONTACTS
#define DEFAULT_MANUAL_ADD_CONTACTS 0
#endif
#ifndef DEFAULT_AUTOADD_CONFIG
#define DEFAULT_AUTOADD_CONFIG 0
#endif
#ifndef DEFAULT_BUZZER_QUIET
#define DEFAULT_BUZZER_QUIET 0
#endif
#ifndef DEFAULT_CAD_ENABLED
// Preserve the tuned Companion behavior that preceded the runtime setting.
#define DEFAULT_CAD_ENABLED 1
#endif

#ifndef EMERGENCY_CLIENT_REPEAT_HOLD_MS
#define EMERGENCY_CLIENT_REPEAT_HOLD_MS 120000UL
#endif
#ifndef EMERGENCY_CLIENT_REPEAT_JITTER_MS
#define EMERGENCY_CLIENT_REPEAT_JITTER_MS 15000UL
#endif

#define PUBLIC_GROUP_PSK                "izOH6cXN6mrJ5e26oRXNcg=="
// First 16 bytes of SHA-256("#testing"), encoded as base64.
#define TESTING_GROUP_PSK               "zeXoLPUVZH3LVHp5pPBl0Q=="

#ifdef ENABLE_USB_INTERFACE
static const char* terminalContactTypeName(uint8_t type) {
  if (type == ADV_TYPE_CHAT) return "Chat";
  if (type == ADV_TYPE_REPEATER) return "Repeater";
  if (type == ADV_TYPE_ROOM) return "Room";
  if (type == ADV_TYPE_SENSOR) return "Sensor";
  return "Unknown";
}
#endif

// these are _pushed_ to client app at any time
#define PUSH_CODE_ADVERT                0x80
#define PUSH_CODE_PATH_UPDATED          0x81
#define PUSH_CODE_SEND_CONFIRMED        0x82
#define PUSH_CODE_MSG_WAITING           0x83
#define PUSH_CODE_RAW_DATA              0x84
#define PUSH_CODE_LOGIN_SUCCESS         0x85
#define PUSH_CODE_LOGIN_FAIL            0x86
#define PUSH_CODE_STATUS_RESPONSE       0x87
#define PUSH_CODE_LOG_RX_DATA           0x88
#define PUSH_CODE_TRACE_DATA            0x89
#define PUSH_CODE_NEW_ADVERT            0x8A
#define PUSH_CODE_TELEMETRY_RESPONSE    0x8B
#define PUSH_CODE_BINARY_RESPONSE       0x8C
#define PUSH_CODE_PATH_DISCOVERY_RESPONSE 0x8D

static const uint8_t EMERGENCY_CHANNEL_SECRET[CIPHER_KEY_SIZE] = {
  0xe1, 0xad, 0x57, 0x8d, 0x25, 0x10, 0x8e, 0x34,
  0x48, 0x08, 0xf3, 0x0d, 0xfd, 0xaa, 0xf9, 0x26
};

#define EMERGENCY_CLIENT_REPEAT_TABLE_SIZE 63
static uint16_t emergency_client_repeats[EMERGENCY_CLIENT_REPEAT_TABLE_SIZE];
static uint8_t emergency_client_repeat_next;
static unsigned long emergency_client_repeat_send_at;
static uint16_t emergency_client_repeat_key;
static mesh::Packet* emergency_client_repeat_packet;

#define PUSH_CODE_CONTROL_DATA          0x8E   // v8+
#define PUSH_CODE_CONTACT_DELETED       0x8F // used to notify client app of deleted contact when overwriting oldest
#define PUSH_CODE_CONTACTS_FULL         0x90 // used to notify client app that contacts storage is full

#define ERR_CODE_UNSUPPORTED_CMD        1
#define ERR_CODE_NOT_FOUND              2
#define ERR_CODE_TABLE_FULL             3
#define ERR_CODE_BAD_STATE              4
#define ERR_CODE_FILE_IO_ERROR          5
#define ERR_CODE_ILLEGAL_ARG            6

#define MAX_SIGN_DATA_LEN               (8 * 1024) // 8K

// Auto-add config bitmask
// Bit 0: If set, overwrite oldest non-favourite contact when contacts file is full
// Bits 1-4: these indicate which contact types to auto-add when manual_contact_mode = 0x01
#define AUTO_ADD_OVERWRITE_OLDEST (1 << 0)  // 0x01 - overwrite oldest non-favourite when full
#define AUTO_ADD_CHAT             (1 << 1)  // 0x02 - auto-add Chat (Companion) (ADV_TYPE_CHAT)
#define AUTO_ADD_REPEATER         (1 << 2)  // 0x04 - auto-add Repeater (ADV_TYPE_REPEATER)
#define AUTO_ADD_ROOM_SERVER      (1 << 3)  // 0x08 - auto-add Room Server (ADV_TYPE_ROOM)
#define AUTO_ADD_SENSOR           (1 << 4)  // 0x10 - auto-add Sensor (ADV_TYPE_SENSOR)

void MyMesh::writeOKFrame() {
  uint8_t buf[1];
  buf[0] = RESP_CODE_OK;
  _serial->writeFrame(buf, 1);
}
void MyMesh::writeErrFrame(uint8_t err_code) {
  uint8_t buf[2];
  buf[0] = RESP_CODE_ERR;
  buf[1] = err_code;
  _serial->writeFrame(buf, 2);
}

void MyMesh::writeDisabledFrame() {
  uint8_t buf[1];
  buf[0] = RESP_CODE_DISABLED;
  _serial->writeFrame(buf, 1);
}

void MyMesh::writeContactRespFrame(uint8_t code, const ContactInfo &contact) {
  int i = 0;
  out_frame[i++] = code;
  memcpy(&out_frame[i], contact.id.pub_key, PUB_KEY_SIZE);
  i += PUB_KEY_SIZE;
  out_frame[i++] = contact.type;
  out_frame[i++] = contact.flags;
  out_frame[i++] = contact.out_path_len;
  memcpy(&out_frame[i], contact.out_path, MAX_PATH_SIZE);
  i += MAX_PATH_SIZE;
  StrHelper::strzcpy((char *)&out_frame[i], contact.name, 32);
  i += 32;
  memcpy(&out_frame[i], &contact.last_advert_timestamp, 4);
  i += 4;
  memcpy(&out_frame[i], &contact.gps_lat, 4);
  i += 4;
  memcpy(&out_frame[i], &contact.gps_lon, 4);
  i += 4;
  memcpy(&out_frame[i], &contact.lastmod, 4);
  i += 4;
  _serial->writeFrame(out_frame, i);
}

void MyMesh::stopContactsIterator() {
  if (!_iter_started) return;
  _iter_started = false;
  if (_serial != NULL) _serial->unlockReplyRoute();
}

void MyMesh::updateContactFromFrame(ContactInfo &contact, uint32_t& last_mod, const uint8_t *frame, int len) {
  int i = 0;
  uint8_t code = frame[i++]; // eg. CMD_ADD_UPDATE_CONTACT
  memcpy(contact.id.pub_key, &frame[i], PUB_KEY_SIZE);
  i += PUB_KEY_SIZE;
  contact.type = frame[i++];
  contact.flags = frame[i++];
  contact.out_path_len = frame[i++];
  memcpy(contact.out_path, &frame[i], MAX_PATH_SIZE);
  i += MAX_PATH_SIZE;
  memcpy(contact.name, &frame[i], 32);
  i += 32;
  memcpy(&contact.last_advert_timestamp, &frame[i], 4);
  i += 4;
  if (len >= i + 8) { // optional fields
    memcpy(&contact.gps_lat, &frame[i], 4);
    i += 4;
    memcpy(&contact.gps_lon, &frame[i], 4);
    i += 4;
    if (len >= i + 4) {
      memcpy(&last_mod, &frame[i], 4);
    }
  }
}

bool MyMesh::Frame::isChannelMsg() const {
  return len > 0 && (buf[0] == RESP_CODE_CHANNEL_MSG_RECV || buf[0] == RESP_CODE_CHANNEL_MSG_RECV_V3 ||
                     buf[0] == RESP_CODE_CHANNEL_DATA_RECV);
}

int MyMesh::getOfflineQueueCapacity() const {
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
  return offline_queue_capacity;
#else
  return OFFLINE_QUEUE_SIZE;
#endif
}

MyMesh::Frame& MyMesh::offlineQueueFrameAt(int logical_index) {
  return offline_queue[(offline_queue_head + logical_index) % getOfflineQueueCapacity()];
}

void MyMesh::initializeOfflineQueue() {
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
  if (offline_queue != offline_queue_fallback || OFFLINE_QUEUE_SIZE <= offline_queue_capacity) return;

  int requested_capacity = OFFLINE_QUEUE_SIZE;
  while (requested_capacity > offline_queue_capacity) {
    void* storage = heap_caps_malloc(sizeof(Frame) * requested_capacity,
                                     MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (storage) {
      offline_queue = static_cast<Frame*>(storage);
      offline_queue_capacity = requested_capacity;
      return;
    }

    if (requested_capacity > 256) {
      requested_capacity = 256;
    } else if (requested_capacity > 128) {
      requested_capacity = 128;
    } else {
      requested_capacity = offline_queue_capacity;
    }
  }
#endif
}

void MyMesh::addToOfflineQueue(const uint8_t frame[], int len) {
  const int capacity = getOfflineQueueCapacity();
  if (!frame || len <= 0 || len > MAX_FRAME_SIZE || capacity <= 0) {
    MESH_DEBUG_PRINTLN("WARN: invalid offline queue frame length: %d", len);
    return;
  }

  if (offline_queue_len >= capacity) {
    MESH_DEBUG_PRINTLN("WARN: offline_queue is full!");
    int pos = 0;
    while (pos < offline_queue_len) {
      if (offlineQueueFrameAt(pos).isChannelMsg()) {
        for (int i = pos; i < offline_queue_len - 1; i++) { // delete oldest channel msg from queue
          offlineQueueFrameAt(i) = offlineQueueFrameAt(i + 1);
        }
        MESH_DEBUG_PRINTLN("INFO: removed oldest channel message from queue.");
        Frame& tail = offlineQueueFrameAt(offline_queue_len - 1);
        tail.len = len;
        memcpy(tail.buf, frame, len);
        return;
      }
      pos++;
    }
    MESH_DEBUG_PRINTLN("INFO: no channel messages to remove from queue.");
  } else {
    Frame& tail = offlineQueueFrameAt(offline_queue_len);
    tail.len = len;
    memcpy(tail.buf, frame, len);
    offline_queue_len++;
  }
}

int MyMesh::getFromOfflineQueue(uint8_t frame[]) {
  if (offline_queue_len > 0) {         // check offline queue
    Frame& head = offlineQueueFrameAt(0);
    size_t len = head.len; // take from top of queue
    memcpy(frame, head.buf, len);

    offline_queue_len--;
    if (offline_queue_len == 0) {
      offline_queue_head = 0;
    } else {
      offline_queue_head = (offline_queue_head + 1) % getOfflineQueueCapacity();
    }
    return len;
  }
  return 0; // queue is empty
}

float MyMesh::getAirtimeBudgetFactor() const {
  // TempRadio is a short-lived, explicitly coordinated OTA channel. Let its
  // primary transfer use the full dispatcher budget; the persisted public
  // channel duty factor becomes authoritative again as soon as TempRadio ends.
#if COMPANION_FEATURE_TEMP_RADIO
  if (isTempRadioActive()) return 0.0f;
#endif
  return _prefs.airtime_factor;
}

bool MyMesh::getCADEnabled() const {
  return _prefs.cad_enabled != 0;
}

uint32_t MyMesh::getCADFailRetryDelay() const {
  return _prefs.cad_retry_delay_ms != 0
      ? _prefs.cad_retry_delay_ms
      : BaseChatMesh::getCADFailRetryDelay();
}

uint32_t MyMesh::getCADFailMaxDuration() const {
  return _prefs.cad_max_duration_ms != 0
      ? _prefs.cad_max_duration_ms
      : mesh::Dispatcher::getCADFailMaxDuration();
}

int MyMesh::getInterferenceThreshold() const {
  return 0; // disabled for now, until currentRSSI() problem is resolved
}

int MyMesh::calcRxDelay(float score, uint32_t air_time) const {
  if (_prefs.rx_delay_base <= 0.0f) return 0;
  return (int)((powf(_prefs.rx_delay_base, 0.85f - score) - 1.0f) * air_time);
}

uint32_t MyMesh::getRetransmitDelay(const mesh::Packet *packet) {
  uint32_t t = (_radio->getEstAirtimeFor(packet->getPathByteLen() + packet->payload_len + 2) * 0.5f);
  return getRNG()->nextInt(0, 5*t + 1);
}
uint32_t MyMesh::getDirectRetransmitDelay(const mesh::Packet *packet) {
  uint32_t t = (_radio->getEstAirtimeFor(packet->getPathByteLen() + packet->payload_len + 2) * 0.2f);
  return getRNG()->nextInt(0, 5*t + 1);
}

uint8_t MyMesh::getExtraAckTransmitCount() const {
  return _prefs.multi_acks;
}

bool MyMesh::hasLocationTelemetryRecipient() {
  if (_prefs.telemetry_mode_loc == TELEM_MODE_DENY) return false;

  ContactsIterator iter = startContactsIterator();
  ContactInfo contact;
  while (iter.hasNext(this, contact)) {
    if (contact.type == ADV_TYPE_NONE) continue;
    if (_prefs.telemetry_mode_loc == TELEM_MODE_ALLOW_ALL) return true;
    if (_prefs.telemetry_mode_loc == TELEM_MODE_ALLOW_FLAGS &&
        ((contact.flags >> 1) & TELEM_PERM_LOCATION)) {
      return true;
    }
  }
  return false;
}

void MyMesh::updateGpsTelemetryPolicy() {
  sensors.setTelemetryLocationAccessAvailable(hasLocationTelemetryRecipient());
}

void MyMesh::logRxRaw(float snr, float rssi, const uint8_t raw[], int len) {
#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
  if (_mqtt_bridge && _mqtt_bridge->isRunning()) {
    _mqtt_bridge->storeRawRadioData(raw, len, snr, rssi);
  }
#endif

  if (_serial->isConnected() && len + 3 <= MAX_FRAME_SIZE) {
    int i = 0;
    out_frame[i++] = PUSH_CODE_LOG_RX_DATA;
    out_frame[i++] = (int8_t)(snr * 4);
    out_frame[i++] = (int8_t)(rssi);
    memcpy(&out_frame[i], raw, len);
    i += len;

    _serial->writeFrame(out_frame, i);
  }
}

#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
void MyMesh::logRx(mesh::Packet* packet, int, float) {
  if (_mqtt_bridge && _mqtt_bridge->isRunning()) {
    _mqtt_bridge->onPacketReceived(packet);
  }
}

void MyMesh::logTx(mesh::Packet* packet, int) {
  if (_mqtt_bridge && _mqtt_bridge->isRunning()) {
    _mqtt_bridge->sendPacket(packet);
  }
}
#endif

bool MyMesh::isAutoAddEnabled() const {
  return (_prefs.manual_add_contacts & 1) == 0;
}

bool MyMesh::shouldAutoAddContactType(uint8_t contact_type) const {
  if ((_prefs.manual_add_contacts & 1) == 0) {
    return true;
  }

  uint8_t type_bit = 0;
  switch (contact_type) {
    case ADV_TYPE_CHAT:
      type_bit = AUTO_ADD_CHAT;
      break;
    case ADV_TYPE_REPEATER:
      type_bit = AUTO_ADD_REPEATER;
      break;
    case ADV_TYPE_ROOM:
      type_bit = AUTO_ADD_ROOM_SERVER;
      break;
    case ADV_TYPE_SENSOR:
      type_bit = AUTO_ADD_SENSOR;
      break;
    default:
      return false;  // Unknown type, don't auto-add
  }

  return (_prefs.autoadd_config & type_bit) != 0;
}

bool MyMesh::shouldOverwriteWhenFull() const {
  return (_prefs.autoadd_config & AUTO_ADD_OVERWRITE_OLDEST) != 0;
}

uint8_t MyMesh::getAutoAddMaxHops() const {
  return _prefs.autoadd_max_hops;
}

void MyMesh::onContactOverwrite(const uint8_t* pub_key) {
  ContactInfo* contact = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
  if (contact) scheduleContactWriteAfterRelease(*contact);
  _store->deleteBlobByKey(pub_key, PUB_KEY_SIZE); // delete from storage
  if (_serial != NULL && _serial->isConnected()) {
    out_frame[0] = PUSH_CODE_CONTACT_DELETED;
    memcpy(&out_frame[1], pub_key, PUB_KEY_SIZE);
    _serial->writeFrame(out_frame, 1 + PUB_KEY_SIZE);
  }
}

void MyMesh::onContactsFull() {
  if (_serial != NULL && _serial->isConnected()) {
    out_frame[0] = PUSH_CODE_CONTACTS_FULL;
    _serial->writeFrame(out_frame, 1);
  }
}

void MyMesh::onDiscoveredContact(ContactInfo &contact, bool is_new, uint8_t path_len, const uint8_t* path) {
  if (_serial->isConnected()) {
    if (is_new) {
      writeContactRespFrame(PUSH_CODE_NEW_ADVERT, contact);
    } else {
      out_frame[0] = PUSH_CODE_ADVERT;
      memcpy(&out_frame[1], contact.id.pub_key, PUB_KEY_SIZE);
      _serial->writeFrame(out_frame, 1 + PUB_KEY_SIZE);
    }
  } else {
#ifdef DISPLAY_CLASS
    if (_ui) _ui->notify(UIEventType::newContactMessage);
#endif
  }

#ifdef ENABLE_USB_INTERFACE
  if (hasTerminalOutput() && _terminal_display.shouldShowAdvert()) {
    Stream& output = terminalOutput();
    output.printf("\r\nADVERT from -> %s\r\n", contact.name);
    output.printf("  type: %s\r\n", terminalContactTypeName(contact.type));
    output.print("  public key: ");
    mesh::Utils::printHex(output, contact.id.pub_key, PUB_KEY_SIZE);
    output.print("\r\n> ");
  }
#endif

  // add inbound-path to mem cache
  if (path && mesh::Packet::isValidPathLen(path_len)) {  // check path is valid
    AdvertPath* p = advert_paths;
    uint32_t oldest = 0xFFFFFFFF;
    for (int i = 0; i < ADVERT_PATH_TABLE_SIZE; i++) {   // check if already in table, otherwise evict oldest
      if (memcmp(advert_paths[i].pubkey_prefix, contact.id.pub_key, sizeof(AdvertPath::pubkey_prefix)) == 0) {
        p = &advert_paths[i];   // found
        break;
      }
      if (advert_paths[i].recv_timestamp < oldest) {
        oldest = advert_paths[i].recv_timestamp;
        p = &advert_paths[i];
      }
    }

    memcpy(p->pubkey_prefix, contact.id.pub_key, sizeof(p->pubkey_prefix));
    strcpy(p->name, contact.name);
    p->recv_timestamp = getRTCClock()->getCurrentTime();
    p->path_len = mesh::Packet::copyPath(p->path, path, path_len);
  }

  ContactInfo* stored = lookupContactByPubKey(contact.id.pub_key, PUB_KEY_SIZE);
  if (stored == &contact) scheduleContactWrite(contact);
  updateGpsTelemetryPolicy();
}

static int sort_by_recent(const void *a, const void *b) {
  return ((AdvertPath *) b)->recv_timestamp - ((AdvertPath *) a)->recv_timestamp;
}

int MyMesh::getRecentlyHeard(AdvertPath dest[], int max_num) {
  if (max_num > ADVERT_PATH_TABLE_SIZE) max_num = ADVERT_PATH_TABLE_SIZE;
  qsort(advert_paths, ADVERT_PATH_TABLE_SIZE, sizeof(advert_paths[0]), sort_by_recent);

  for (int i = 0; i < max_num; i++) {
    dest[i] = advert_paths[i];
  }
  return max_num;
}

#ifdef ENABLE_USB_INTERFACE
void MyMesh::onContactVisit(const ContactInfo& contact) {
  if (contact.type == ADV_TYPE_NONE) return;

  Stream& output = terminalOutput();
  output.printf("  %s (%s) - ", contact.name, terminalContactTypeName(contact.type));
  char relative_time[40];
  int32_t seconds_from_now = contact.last_advert_timestamp - getRTCClock()->getCurrentTime();
  AdvertTimeHelper::formatRelativeTimeDiff(relative_time, seconds_from_now, false);
  output.println(relative_time);
}
#endif

void MyMesh::onContactPathUpdated(const ContactInfo &contact) {
  out_frame[0] = PUSH_CODE_PATH_UPDATED;
  memcpy(&out_frame[1], contact.id.pub_key, PUB_KEY_SIZE);
  _serial->writeFrame(out_frame, 1 + PUB_KEY_SIZE); // NOTE: app may not be connected

  scheduleContactWrite(contact);

#ifdef ENABLE_USB_INTERFACE
  if (hasTerminalOutput()) {
    terminalOutput().printf("\r\nPATH updated -> %s\r\n> ", contact.name);
  }
#endif
}

void MyMesh::clearExpectedAck(AckTableEntry& entry, bool cancel_retries) {
  if (cancel_retries && entry.ack != 0) {
    cancelActiveRetries(entry.retry_key);
  }
  memset(&entry, 0, sizeof(entry));
}

void MyMesh::expireExpectedAcks() {
  unsigned long now = _ms->getMillis();
  unsigned long nearest_delay = 0;
  has_next_ack_expiry = false;

  for (int i = 0; i < EXPECTED_ACK_TABLE_SIZE; i++) {
    AckTableEntry& entry = expected_ack_table[i];
    if (entry.ack == 0) {
      continue;
    }

    if (entry.expires_at == now || millisHasNowPassed(entry.expires_at)) {
      if (!hasActiveRetries(entry.retry_key)) {
#ifdef ENABLE_USB_INTERFACE
        if (entry.terminal_origin && hasTerminalOutput()) {
          terminalOutput().print("\r\n  ERROR: timed out, no ACK.\r\n> ");
        }
#endif
        clearExpectedAck(entry, false);
        continue;
      }
      // Keep the semantic match alive while its lower-level retry sequence is
      // active, so a newer app submission can replace that sequence cleanly.
      entry.expires_at = futureMillis(EXPECTED_ACK_RETRY_RECHECK_MILLIS);
    }

    unsigned long delay = entry.expires_at - now;
    if (!has_next_ack_expiry || delay < nearest_delay) {
      nearest_delay = delay;
      next_ack_expiry = entry.expires_at;
      has_next_ack_expiry = true;
    }
  }

  if (!has_next_ack_expiry) {
    next_ack_expiry = 0;
  }
}

MyMesh::AckTableEntry* MyMesh::findPendingTextMessage(
    const uint8_t text_fingerprint[MAX_HASH_SIZE], uint32_t message_timestamp) {
  expireExpectedAcks();
  for (int i = 0; i < EXPECTED_ACK_TABLE_SIZE; i++) {
    AckTableEntry& entry = expected_ack_table[i];
    if (entry.ack != 0
        && entry.message_timestamp != message_timestamp
        && memcmp(entry.text_fingerprint, text_fingerprint, MAX_HASH_SIZE) == 0
        && hasActiveRetries(entry.retry_key)) {
      return &entry;
    }
  }
  return NULL;
}

ContactInfo*  MyMesh::processAck(const uint8_t *data) {
  expireExpectedAcks();

  // see if matches any in a table
  for (int i = 0; i < EXPECTED_ACK_TABLE_SIZE; i++) {
    if (expected_ack_table[i].ack != 0
        && memcmp(data, &expected_ack_table[i].ack, 4) == 0) { // got an ACK from recipient
      out_frame[0] = PUSH_CODE_SEND_CONFIRMED;
      memcpy(&out_frame[1], data, 4);
      uint32_t trip_time = _ms->getMillis() - expected_ack_table[i].msg_sent;
      memcpy(&out_frame[5], &trip_time, 4);
      _serial->writeFrame(out_frame, 9);

#ifdef ENABLE_USB_INTERFACE
      if (expected_ack_table[i].terminal_origin && hasTerminalOutput()) {
        terminalOutput().printf("\r\n  Got ACK! (round trip: %lu ms)\r\n> ",
                                (unsigned long)trip_time);
      }
#endif

      // NOTE: the same ACK can be received multiple times!
      ContactInfo* contact = expected_ack_table[i].contact;
      clearExpectedAck(expected_ack_table[i]);
      expireExpectedAcks();
      return contact;
    }
  }
  return checkConnectionsAck(data);
}

void MyMesh::queueMessage(const ContactInfo &from, uint8_t txt_type, mesh::Packet *pkt,
                          uint32_t sender_timestamp, const uint8_t *extra,
                          int extra_len, const char *text,
                          bool terminal_command_reply,
                          uint32_t terminal_command_elapsed_millis) {
  int i = 0;
  if (app_target_ver >= 3) {
    out_frame[i++] = RESP_CODE_CONTACT_MSG_RECV_V3;
    out_frame[i++] = (int8_t)(pkt->getSNR() * 4);
    out_frame[i++] = 0; // reserved1
    out_frame[i++] = 0; // reserved2
  } else {
    out_frame[i++] = RESP_CODE_CONTACT_MSG_RECV;
  }
  memcpy(&out_frame[i], from.id.pub_key, 6);
  i += 6; // just 6-byte prefix
  uint8_t path_len = out_frame[i++] = pkt->isRouteFlood() ? pkt->path_len : 0xFF;
  out_frame[i++] = txt_type;
  memcpy(&out_frame[i], &sender_timestamp, 4);
  i += 4;
  if (extra_len > 0) {
    memcpy(&out_frame[i], extra, extra_len);
    i += extra_len;
  }
  int tlen = strlen(text); // TODO: UTF-8 ??
  if (i + tlen > MAX_FRAME_SIZE) {
    tlen = MAX_FRAME_SIZE - i;
  }
  memcpy(&out_frame[i], text, tlen);
  i += tlen;
  addToOfflineQueue(out_frame, i);

  if (_serial->isConnected()) {
    uint8_t frame[1];
    frame[0] = PUSH_CODE_MSG_WAITING; // send push 'tickle'
    _serial->writeFrame(frame, 1);
  }

#ifdef ENABLE_USB_INTERFACE
  if (hasTerminalOutput()) {
    Stream& output = terminalOutput();
    const char* kind = txt_type == TXT_TYPE_CLI_DATA ? "CLI" : "MSG";
    output.printf("\r\n(%s) %s -> from %s",
                  mesh::cli::terminalInboundRouteLabel(pkt->isRouteDirect()),
                  kind, from.name);
    if (terminal_command_reply) {
      output.printf(" (round trip %lu ms)",
                    (unsigned long)terminal_command_elapsed_millis);
    }
    output.printf("\r\n  %s\r\n> ", text);
  }
#endif

#ifdef DISPLAY_CLASS
  // we only want to show text messages on display, not cli data
  bool should_display = txt_type == TXT_TYPE_PLAIN || txt_type == TXT_TYPE_SIGNED_PLAIN;
  if (should_display && _ui) {
    _ui->newMsg(path_len, from.name, text, offline_queue_len);
    if (!_serial->isConnected()) {
      _ui->notify(UIEventType::contactMessage);
    }
  }
#endif
}

static uint16_t emergencyClientRepeatKey(const mesh::Packet* packet) {
  const uint8_t* p = packet->payload;
  return ((uint16_t)p[1]) | ((uint16_t)p[2] << 8);
}

static bool __attribute__((noinline)) hasEmergencyClientRepeat(uint16_t key) {
  for (uint8_t i = 0; i < EMERGENCY_CLIENT_REPEAT_TABLE_SIZE; i++) {
    if (emergency_client_repeats[i] == key) {
      return true;
    }
  }
  return false;
}

bool MyMesh::filterRecvFloodPacket(mesh::Packet* packet) {
  if (emergency_client_repeat_packet != NULL && packet->getPathHashCount() > 0) {
    if (emergencyClientRepeatKey(packet) == emergency_client_repeat_key) {
      releasePacket(emergency_client_repeat_packet);
      emergency_client_repeat_packet = NULL;
    }
  }
  // REVISIT: try to determine which Region (from transport_codes[1]) that Sender is indicating for replies/responses
  //    if unknown, fallback to finding Region from transport_codes[0], the 'scope' used by Sender
  return false;
}

bool MyMesh::allowPacketForward(const mesh::Packet* packet) {
  if (!_prefs.isRepeatEn()) return false;
#ifdef COMPANION_MESH_CLOCK_SYNC
  _clock_sync.observeAcceptedFlood(packet);
#endif
  return true;
}

#ifdef COMPANION_MESH_CLOCK_SYNC
void MyMesh::onAdvertRecv(mesh::Packet* packet, const mesh::Identity& id,
                          uint32_t timestamp, const uint8_t* app_data,
                          size_t app_data_len) {
  BaseChatMesh::onAdvertRecv(packet, id, timestamp, app_data, app_data_len);
  _clock_sync.observeVerifiedAdvert(packet, id, timestamp);
}

void MyMesh::onGroupPacketRecv(mesh::Packet* packet) {
  _clock_sync.observeGroupPacket(packet);
}
#endif

bool MyMesh::allowFloodRetry(const mesh::Packet* packet) const {
  if (packet == NULL) return false;
  // A companion may retry its own advert once, using the core's deliberately
  // slow origin-advert delay. Do not add retries while relaying a neighbour's
  // advert; the ordinary forwarding and recent-echo guard still apply.
  return packet->getPayloadType() != PAYLOAD_TYPE_ADVERT
      || isSelfOriginAdvert(packet);
}

bool MyMesh::sendFloodScoped(const TransportKey& scope, mesh::Packet* pkt, uint32_t delay_millis) {
  if (scope.isNull()) {
    return sendFlood(pkt, delay_millis, _prefs.path_hash_mode + 1);
  } else {
    uint16_t codes[2];
    codes[0] = scope.calcTransportCode(pkt);
    codes[1] = 0;  // REVISIT: set to 'home' Region, for sender/return region?
    return sendFlood(pkt, codes, delay_millis, _prefs.path_hash_mode + 1);
  }
}

bool MyMesh::sendFloodScoped(const ContactInfo& recipient, mesh::Packet* pkt, uint32_t delay_millis) {
  // TODO: dynamic send_scope, depending on recipient and current 'home' Region
  if (send_unscoped) {
    return sendFlood(pkt, delay_millis, _prefs.path_hash_mode + 1);  // app explicitly requested un-scoped
  } else {
    TransportKey default_scope;
    memcpy(&default_scope.key, _prefs.default_scope_key, sizeof(default_scope.key));

    auto scope = send_scope.isNull() ? &default_scope : &send_scope;
    return sendFloodScoped(*scope, pkt, delay_millis);
  }
}
bool MyMesh::sendFloodScoped(const mesh::GroupChannel& channel, mesh::Packet* pkt, uint32_t delay_millis) {
  // TODO: have per-channel send_scope
  if (send_unscoped) {
    return sendFlood(pkt, delay_millis, _prefs.path_hash_mode + 1);  // app explicitly requested un-scoped
  } else {
    TransportKey default_scope;
    memcpy(&default_scope.key, _prefs.default_scope_key, sizeof(default_scope.key));

    auto scope = send_scope.isNull() ? &default_scope : &send_scope;
    return sendFloodScoped(*scope, pkt, delay_millis);
  }
}

void MyMesh::onMessageRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                           const char *text) {
  markConnectionActive(from); // in case this is from a server, and we have a connection
  // BaseChatMesh updates lastmod immediately before this callback.
  scheduleContactWrite(from);
  queueMessage(from, TXT_TYPE_PLAIN, pkt, sender_timestamp, NULL, 0, text);
}

void MyMesh::onCommandDataRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp, const char *text) {
  markConnectionActive(from); // in case this is from a server, and we have a connection
  bool terminal_command_reply = false;
  uint32_t terminal_command_elapsed_millis = 0;
#ifdef ENABLE_USB_INTERFACE
  terminal_command_reply = _terminal_command.takeReply(
      from.id.pub_key, _ms->getMillis(), terminal_command_elapsed_millis);
  if (terminal_command_reply) _terminal_command_target[0] = 0;
#endif
  queueMessage(from, TXT_TYPE_CLI_DATA, pkt, sender_timestamp, NULL, 0, text,
               terminal_command_reply, terminal_command_elapsed_millis);
}

void MyMesh::onCLICommandRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                               const char *text, char* reply) {
  markConnectionActive(from); // in case this is from a server, and we have a connection
  if (from.isRemoteCLIAllowed()) {
    if (!handleCommand(text, sender_timestamp, reply)) {
      strcat(reply, "Unknown command");   // reply may have cmd prefix from 'text'
    }
  } else {
    queueMessage(from, TXT_TYPE_CLI_COMMAND, pkt, sender_timestamp, NULL, 0, text);
  }
}

void MyMesh::onSignedMessageRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                                 const uint8_t *sender_prefix, const char *text) {
  markConnectionActive(from);
  // from.sync_since change needs to be persisted
  scheduleContactWrite(from);
  queueMessage(from, TXT_TYPE_SIGNED_PLAIN, pkt, sender_timestamp, sender_prefix, 4, text);
}

void MyMesh::onChannelMessageRecv(const mesh::GroupChannel &channel, mesh::Packet *pkt, uint32_t timestamp,
                                  const char *text) {
  const bool is_emergency_channel =
      memcmp(channel.secret, EMERGENCY_CHANNEL_SECRET,
             sizeof(EMERGENCY_CHANNEL_SECRET)) == 0;
  int i = 0;
  if (app_target_ver >= 3) {
    out_frame[i++] = RESP_CODE_CHANNEL_MSG_RECV_V3;
    out_frame[i++] = (int8_t)(pkt->getSNR() * 4);
    out_frame[i++] = 0; // reserved1
    out_frame[i++] = 0; // reserved2
  } else {
    out_frame[i++] = RESP_CODE_CHANNEL_MSG_RECV;
  }

  uint8_t channel_idx = findChannelIdx(channel);
  out_frame[i++] = channel_idx;
  uint8_t path_len = out_frame[i++] = pkt->isRouteFlood() ? pkt->path_len : 0xFF;

  out_frame[i++] = TXT_TYPE_PLAIN;
  memcpy(&out_frame[i], &timestamp, 4);
  i += 4;
  int tlen = strlen(text); // TODO: UTF-8 ??
  if (i + tlen > MAX_FRAME_SIZE) {
    tlen = MAX_FRAME_SIZE - i;
  }
  memcpy(&out_frame[i], text, tlen);
  i += tlen;
  addToOfflineQueue(out_frame, i);

  if (_serial->isConnected()) {
    uint8_t frame[1];
    frame[0] = PUSH_CODE_MSG_WAITING; // send push 'tickle'
    _serial->writeFrame(frame, 1);
  } else {
#ifdef DISPLAY_CLASS
    if (_ui) _ui->notify(UIEventType::channelMessage);
#endif
  }

#ifdef ENABLE_USB_INTERFACE
  if (hasTerminalOutput()
      && _terminal_display.shouldShowChannel(is_emergency_channel)) {
    ChannelDetails details;
    const char* channel_name = getChannel(channel_idx, details) ? details.name : "Unknown";
    terminalOutput().printf(
        "\r\nCHANNEL MSG -> %s (%s)\r\n  %s\r\n> ", channel_name,
        pkt->isRouteDirect() ? "DIRECT" : "FLOOD", text);
  }
#endif
#ifdef DISPLAY_CLASS
  // Get the channel name from the channel index
  const char *channel_name = "Unknown";
  ChannelDetails channel_details;
  if (getChannel(channel_idx, channel_details)) {
    channel_name = channel_details.name;
  }
  char channel_label[64];
  snprintf(channel_label, sizeof(channel_label), "Ch %u %s",
           (unsigned int)channel_idx, channel_name);
  if (_ui) {
    _ui->newMsg(path_len, channel_label, text, offline_queue_len,
                channel_idx, channel_name);
  }
#endif

  if (pkt->isRouteFlood() && is_emergency_channel) {
    bool zero_path = pkt->getPathHashCount() == 0;
    uint16_t key = emergencyClientRepeatKey(pkt);
    if (hasEmergencyClientRepeat(key)) {
      pkt->markDoNotRetransmit();
    } else {
      emergency_client_repeats[emergency_client_repeat_next] = key;
      emergency_client_repeat_next++;
      if (emergency_client_repeat_next >= EMERGENCY_CLIENT_REPEAT_TABLE_SIZE) emergency_client_repeat_next = 0;

      if (zero_path && emergency_client_repeat_packet == NULL) {
        emergency_client_repeat_packet = obtainNewPacket();
        if (emergency_client_repeat_packet != NULL) {
          *emergency_client_repeat_packet = *pkt;
          emergency_client_repeat_key = key;
          emergency_client_repeat_send_at = futureMillis(
              (int)(EMERGENCY_CLIENT_REPEAT_HOLD_MS + getRNG()->nextInt(0, EMERGENCY_CLIENT_REPEAT_JITTER_MS + 1)));
          pkt->markDoNotRetransmit();
        }
      }
    }
  }
}

void MyMesh::onChannelDataRecv(const mesh::GroupChannel &channel, mesh::Packet *pkt, uint16_t data_type,
                               const uint8_t *data, size_t data_len) {
  if (data_len > MAX_CHANNEL_DATA_LENGTH) {
    MESH_DEBUG_PRINTLN("onChannelDataRecv: dropping payload_len=%d exceeds frame limit=%d",
                       (uint32_t)data_len, (uint32_t)MAX_CHANNEL_DATA_LENGTH);
    return;
  }

  int i = 0;
  out_frame[i++] = RESP_CODE_CHANNEL_DATA_RECV;
  out_frame[i++] = (int8_t)(pkt->getSNR() * 4);
  out_frame[i++] = 0; // reserved1
  out_frame[i++] = 0; // reserved2

  uint8_t channel_idx = findChannelIdx(channel);
  out_frame[i++] = channel_idx;
  out_frame[i++] = pkt->isRouteFlood() ? pkt->path_len : 0xFF;
  out_frame[i++] = (uint8_t)(data_type & 0xFF);
  out_frame[i++] = (uint8_t)(data_type >> 8);
  out_frame[i++] = (uint8_t)data_len;

  int copy_len = (int)data_len;
  if (copy_len > 0) {
    memcpy(&out_frame[i], data, copy_len);
    i += copy_len;
  }
  addToOfflineQueue(out_frame, i);

  if (_serial->isConnected()) {
    uint8_t frame[1];
    frame[0] = PUSH_CODE_MSG_WAITING; // send push 'tickle'
    _serial->writeFrame(frame, 1);
  }
}

uint8_t MyMesh::onContactRequest(const ContactInfo &contact, uint32_t sender_timestamp, const uint8_t *data,
                                 uint8_t len, uint8_t *reply) {
  if (data[0] == REQ_TYPE_GET_TELEMETRY_DATA) {
    uint8_t permissions = 0;
    uint8_t cp = contact.flags >> 1; // LSB used as 'favourite' bit (so only use upper bits)

    if (_prefs.telemetry_mode_base == TELEM_MODE_ALLOW_ALL) {
      permissions = TELEM_PERM_BASE;
    } else if (_prefs.telemetry_mode_base == TELEM_MODE_ALLOW_FLAGS) {
      permissions = cp & TELEM_PERM_BASE;
    }

    if (_prefs.telemetry_mode_loc == TELEM_MODE_ALLOW_ALL) {
      permissions |= TELEM_PERM_LOCATION;
    } else if (_prefs.telemetry_mode_loc == TELEM_MODE_ALLOW_FLAGS) {
      permissions |= cp & TELEM_PERM_LOCATION;
    }

    if (_prefs.telemetry_mode_env == TELEM_MODE_ALLOW_ALL) {
      permissions |= TELEM_PERM_ENVIRONMENT;
    } else if (_prefs.telemetry_mode_env == TELEM_MODE_ALLOW_FLAGS) {
      permissions |= cp & TELEM_PERM_ENVIRONMENT;
    }

    uint8_t perm_mask = ~(data[1]);    // NEW: first reserved byte (of 4), is now inverse mask to apply to permissions
    permissions &= perm_mask;

    if (permissions & TELEM_PERM_BASE) { // only respond if base permission bit is set
      telemetry.reset();
      telemetry.addVoltage(TELEM_CHANNEL_SELF, (float)board.getBattMilliVolts() / 1000.0f);
      // query other sensors -- target specific
      sensors.querySensors(permissions, telemetry);

      float temperature = board.getMCUTemperature();
      if(!isnan(temperature)) { // Supported boards with built-in temperature sensor. ESP32-C3 may return NAN
        telemetry.addTemperature(TELEM_CHANNEL_SELF, temperature); // Built-in MCU Temperature
      }

      memcpy(reply, &sender_timestamp,
             4); // reflect sender_timestamp back in response packet (kind of like a 'tag')

      uint8_t tlen = telemetry.getSize();
      memcpy(&reply[4], telemetry.getBuffer(), tlen);
      return 4 + tlen;
    }
  }
  return 0; // unknown
}

void MyMesh::onContactResponse(const ContactInfo &contact, const uint8_t *data, uint8_t len) {
  if (data == NULL || len < 4) return;

  uint32_t tag;
  memcpy(&tag, data, 4);

  if (pending_login && memcmp(&pending_login, contact.id.pub_key, 4) == 0) { // check for login response
    // yes, is response to pending sendLogin()
#ifdef ENABLE_USB_INTERFACE
    const bool terminal_login_response = _terminal_login_pending
        && memcmp(_terminal_login_key, contact.id.pub_key,
                  sizeof(_terminal_login_key)) == 0;
#endif
    pending_login = 0;

    int i = 0;
#ifdef ENABLE_USB_INTERFACE
    bool login_success = false;
    bool modern_login = false;
#endif
    if (len >= 6 && memcmp(&data[4], "OK", 2) == 0) { // legacy Repeater login OK response
#ifdef ENABLE_USB_INTERFACE
      login_success = true;
#endif
      out_frame[i++] = PUSH_CODE_LOGIN_SUCCESS;
      out_frame[i++] = 0; // legacy: is_admin = false
      memcpy(&out_frame[i], contact.id.pub_key, 6);
      i += 6;                                     // pub_key_prefix
    } else if (len >= 13 && data[4] == RESP_SERVER_LOGIN_OK) { // new login response
#ifdef ENABLE_USB_INTERFACE
      login_success = true;
      modern_login = true;
#endif
      uint16_t keep_alive_secs = ((uint16_t)data[5]) * 16;
      if (keep_alive_secs > 0) {
        startConnection(contact, keep_alive_secs);
      }
      out_frame[i++] = PUSH_CODE_LOGIN_SUCCESS;
      out_frame[i++] = data[6]; // permissions (eg. is_admin)
      memcpy(&out_frame[i], contact.id.pub_key, 6);
      i += 6; // pub_key_prefix
      memcpy(&out_frame[i], &tag, 4);
      i += 4; // NEW: include server timestamp
      out_frame[i++] = data[7]; // NEW (v7): ACL permissions
      out_frame[i++] = data[12]; // FIRMWARE_VER_LEVEL
    } else {
      out_frame[i++] = PUSH_CODE_LOGIN_FAIL;
      out_frame[i++] = 0; // reserved
      memcpy(&out_frame[i], contact.id.pub_key, 6);
      i += 6; // pub_key_prefix
    }
    _serial->writeFrame(out_frame, i);
#ifdef ENABLE_USB_INTERFACE
    if (terminal_login_response) {
      if (hasTerminalOutput()) {
        Stream& output = terminalOutput();
        if (login_success && modern_login) {
          output.printf(
              "\r\nLOGIN -> %s accepted (ACL permissions 0x%02X, server v%u)\r\n> ",
              _terminal_login_target, (unsigned)data[7], (unsigned)data[12]);
        } else if (login_success) {
          output.printf("\r\nLOGIN -> %s accepted (legacy server)\r\n> ",
                        _terminal_login_target);
        } else {
          output.printf("\r\nLOGIN -> %s rejected\r\n> ",
                        _terminal_login_target);
        }
      }
      clearTerminalLogin();
    }
#endif
  } else if (mesh::companionStatusTagMatches(pending_status, tag)) {
    pending_status = 0;

    // Do not expose a truncated or unrelated response as repeater statistics.
    // The app parses at least 48 bytes and otherwise throws a RangeError.
    if (!mesh::companionStatusResponseIsLongEnough(len)) {
      MESH_DEBUG_PRINTLN(
          "onContactResponse(), short status response: len=%u, expected>=%u",
          (unsigned)len,
          (unsigned)mesh::COMPANION_MIN_STATUS_RESPONSE_SIZE);
      return;
    }

    int i = 0;
    out_frame[i++] = PUSH_CODE_STATUS_RESPONSE;
    out_frame[i++] = 0; // reserved
    memcpy(&out_frame[i], contact.id.pub_key, 6);
    i += 6; // pub_key_prefix
    memcpy(&out_frame[i], &data[4], len - 4);
    i += (len - 4);
    _serial->writeFrame(out_frame, i);
  } else if (len > 4 && tag == pending_telemetry) {  // check for matching response tag
    pending_telemetry = 0;

    int i = 0;
    out_frame[i++] = PUSH_CODE_TELEMETRY_RESPONSE;
    out_frame[i++] = 0; // reserved
    memcpy(&out_frame[i], contact.id.pub_key, 6);
    i += 6; // pub_key_prefix
    memcpy(&out_frame[i], &data[4], len - 4);
    i += (len - 4);
    _serial->writeFrame(out_frame, i);
  } else if (len > 4 && tag == pending_req) {  // check for matching response tag
    pending_req = 0;

    int i = 0;
    out_frame[i++] = PUSH_CODE_BINARY_RESPONSE;
    out_frame[i++] = 0; // reserved
    memcpy(&out_frame[i], &tag, 4);   // app needs to match this to RESP_CODE_SENT.tag
    i += 4;
    memcpy(&out_frame[i], &data[4], len - 4);
    i += (len - 4);
    _serial->writeFrame(out_frame, i);
  }
}

bool MyMesh::onContactPathRecv(ContactInfo& contact, uint8_t* in_path, uint8_t in_path_len, uint8_t* out_path, uint8_t out_path_len, uint8_t extra_type, uint8_t* extra, uint8_t extra_len) {
  if (extra_type == PAYLOAD_TYPE_RESPONSE && extra_len > 4) {
    uint32_t tag;
    memcpy(&tag, extra, 4);

    if (tag == pending_discovery) {  // check for matching response tag)
      pending_discovery = 0;

      if (!mesh::Packet::isValidPathLen(in_path_len) || !mesh::Packet::isValidPathLen(out_path_len)) {
        MESH_DEBUG_PRINTLN("onContactPathRecv, invalid path sizes: %d, %d", in_path_len, out_path_len);
      } else {
        int i = 0;
        out_frame[i++] = PUSH_CODE_PATH_DISCOVERY_RESPONSE;
        out_frame[i++] = 0; // reserved
        memcpy(&out_frame[i], contact.id.pub_key, 6);
        i += 6; // pub_key_prefix
        out_frame[i++] = out_path_len;
        i += mesh::Packet::writePath(&out_frame[i], out_path, out_path_len);
        out_frame[i++] = in_path_len;
        i += mesh::Packet::writePath(&out_frame[i], in_path, in_path_len);
        // NOTE: telemetry data in 'extra' is discarded at present

        _serial->writeFrame(out_frame, i);
      }
      return false;  // DON'T send reciprocal path!
    }
  }
  // let base class handle received path and data
  return BaseChatMesh::onContactPathRecv(contact, in_path, in_path_len, out_path, out_path_len, extra_type, extra, extra_len);
}

void MyMesh::onControlDataRecv(mesh::Packet *packet) {
  if (packet->payload_len + 4 > sizeof(out_frame)) {
    MESH_DEBUG_PRINTLN("onControlDataRecv(), payload_len too long: %d", packet->payload_len);
    return;
  }
  int i = 0;
  out_frame[i++] = PUSH_CODE_CONTROL_DATA;
  out_frame[i++] = (int8_t)(packet->getSNR() * 4);
  out_frame[i++] = (int8_t)packet->getRSSI();
  out_frame[i++] = packet->path_len;
  memcpy(&out_frame[i], packet->payload, packet->payload_len);
  i += packet->payload_len;

  if (_serial->isConnected()) {
    _serial->writeFrame(out_frame, i);
  } else {
    MESH_DEBUG_PRINTLN("onControlDataRecv(), data received while app offline");
  }
}

void MyMesh::onRawDataRecv(mesh::Packet *packet) {
  if (packet->payload_len + 4 > sizeof(out_frame)) {
    MESH_DEBUG_PRINTLN("onRawDataRecv(), payload_len too long: %d", packet->payload_len);
    return;
  }
  int i = 0;
  out_frame[i++] = PUSH_CODE_RAW_DATA;
  out_frame[i++] = (int8_t)(packet->getSNR() * 4);
  out_frame[i++] = (int8_t)packet->getRSSI();
  out_frame[i++] = 0xFF; // reserved (possibly path_len in future)
  memcpy(&out_frame[i], packet->payload, packet->payload_len);
  i += packet->payload_len;

  if (_serial->isConnected()) {
    _serial->writeFrame(out_frame, i);
  } else {
    MESH_DEBUG_PRINTLN("onRawDataRecv(), data received while app offline");
  }
}

void MyMesh::onTraceRecv(mesh::Packet *packet, uint32_t tag, uint32_t auth_code, uint8_t flags,
                         const uint8_t *path_snrs, const uint8_t *path_hashes, uint8_t path_len) {
  uint8_t path_sz = flags & 0x03;  // NEW v1.11+
  if (12 + path_len + (path_len >> path_sz) + 1 > sizeof(out_frame)) {
    MESH_DEBUG_PRINTLN("onTraceRecv(), path_len is too long: %d", (uint32_t)path_len);
    return;
  }
  int i = 0;
  out_frame[i++] = PUSH_CODE_TRACE_DATA;
  out_frame[i++] = 0; // reserved
  out_frame[i++] = path_len;
  out_frame[i++] = flags;
  memcpy(&out_frame[i], &tag, 4);
  i += 4;
  memcpy(&out_frame[i], &auth_code, 4);
  i += 4;
  memcpy(&out_frame[i], path_hashes, path_len);
  i += path_len;

  memcpy(&out_frame[i], path_snrs, path_len >> path_sz);
  i += path_len >> path_sz;
  out_frame[i++] = (int8_t)(packet->getSNR() * 4); // extra/final SNR (to this node)

#ifdef ENABLE_USB_INTERFACE
  if (hasTerminalOutput() && _terminal_trace_pending
      && tag == _terminal_trace_tag && auth_code == _terminal_trace_auth) {
    Stream& output = terminalOutput();
    const uint8_t hash_size = _terminal_trace_hash_size;
    const uint8_t hop_count = hash_size == 0 ? 0 : path_len / hash_size;
    const uint8_t response_hash_size = 1 << (flags & 0x03);
    const unsigned long elapsed = _ms->getMillis() - _terminal_trace_sent_at;
    output.printf("\r\nTRACE -> %s (%lu ms)\r\n",
                  _terminal_trace_target, elapsed);
    if (hash_size == 0 || response_hash_size != hash_size
        || path_len % hash_size != 0
        || hop_count >= MAX_PATH_SIZE) {
      output.print("  ERROR: malformed trace response\r\n> ");
    } else {
      output.print("  ");
      for (uint8_t hop = 0; hop < hop_count; hop++) {
        output.print(((float)(int8_t)path_snrs[hop]) / 4.0f, 2);
        output.print(" dB -> [");
        mesh::Utils::printHex(output, &path_hashes[hop * hash_size],
                              hash_size);
        output.print("] -> ");
      }
      output.print(packet->getSNR(), 2);
      output.print(" dB\r\n> ");
    }
    clearTerminalTrace();
  }
#endif

  if (_serial->isConnected()) {
    _serial->writeFrame(out_frame, i);
  } else {
    MESH_DEBUG_PRINTLN("onTraceRecv(), data received while app offline");
  }
}

uint32_t MyMesh::calcFloodTimeoutMillisFor(uint32_t pkt_airtime_millis) const {
  return SEND_TIMEOUT_BASE_MILLIS + (FLOOD_SEND_TIMEOUT_FACTOR * pkt_airtime_millis);
}
uint32_t MyMesh::calcDirectTimeoutMillisFor(uint32_t pkt_airtime_millis, uint8_t path_len) const {
  uint8_t path_hash_count = path_len & 63;
  return SEND_TIMEOUT_BASE_MILLIS +
         ((pkt_airtime_millis * DIRECT_SEND_PERHOP_FACTOR + DIRECT_SEND_PERHOP_EXTRA_MILLIS) *
          (path_hash_count + 1));
}

void MyMesh::onSendTimeout() {
  expireExpectedAcks();
}

MyMesh::MyMesh(mesh::Radio &radio, mesh::RNG &rng, mesh::RTCClock &rtc, SimpleMeshTables &tables, DataStore& store, AbstractUITask* ui)
    : BaseChatMesh(radio, *new ArduinoMillis(), rng, rtc, *new StaticPoolPacketManager(16), tables),
#ifdef COMPANION_MESH_CLOCK_SYNC
      _clock_sync(radio, _clock_sync_millis, rtc, _clock_sync_acl, sensors,
                  _prefs.airtime_factor),
#endif
      _serial(NULL), _mota_source_control(NULL),
      telemetry(MAX_PACKET_PAYLOAD - 4), _store(&store), _ui(ui), _iter(0) {
  _iter_started = false;
  _cli_rescue = false;
#ifdef ENABLE_USB_INTERFACE
  _terminal_mode = false;
  _terminal_output = NULL;
  _terminal_recipient_set = false;
  memset(_terminal_recipient_key, 0, sizeof(_terminal_recipient_key));
  _terminal_login_pending = false;
  memset(_terminal_login_key, 0, sizeof(_terminal_login_key));
  _terminal_login_expires_at = 0;
  _terminal_login_target[0] = 0;
  clearTerminalCommand();
  clearTerminalTrace();
#endif
  saved_radio_apply_pending = false;
  _radio_available = true;
  radio_apply_retry_at = 0;
  radio_apply_failures = 0;
  command_radio_apply_pending = false;
  command_radio_freq = 0.0f;
  command_radio_bw = 0.0f;
  command_radio_sf = 0;
  command_radio_cr = 0;
  command_radio_repeat = 0;
  command_radio_apply_deadline = 0;
  _scheduled_reboot_at = 0;
#if COMPANION_FEATURE_TEMP_RADIO
  _temp_radio_set_at = 0;
  _temp_radio_revert_at = 0;
  _temp_radio_retry_at = 0;
  _temp_radio_freq = 0.0f;
  _temp_radio_bw = 0.0f;
  _temp_radio_sf = 0;
  _temp_radio_cr = 0;
  _temp_radio_failures = 0;
  _temp_radio_applied = false;
#endif
  offline_queue_len = 0;
  offline_queue_head = 0;
#if defined(ESP32_PLATFORM) && defined(BOARD_HAS_PSRAM)
  offline_queue = offline_queue_fallback;
  offline_queue_capacity = OFFLINE_QUEUE_PSRAM_FALLBACK_SIZE;
#endif
  app_target_ver = 0;
  clearPendingReqs();
  memset(expected_ack_table, 0, sizeof(expected_ack_table));
  next_ack_idx = 0;
  next_ack_expiry = 0;
  has_next_ack_expiry = false;
  sign_data = NULL;
  dirty_contacts_expiry = 0;
  memset(advert_paths, 0, sizeof(advert_paths));
  memset(send_scope.key, 0, sizeof(send_scope.key));
  send_unscoped = false;

  // defaults
  _prefs.airtime_factor = 1.0; // one half
  strcpy(_prefs.node_name, "NONAME");
  _prefs.freq = LORA_FREQ;
  _prefs.sf = LORA_SF;
  _prefs.bw = LORA_BW;
  _prefs.cr = LORA_CR;
  _prefs.multi_acks = DEFAULT_MULTI_ACKS;
  _prefs.manual_add_contacts = DEFAULT_MANUAL_ADD_CONTACTS;
  _prefs.tx_power_dbm = LORA_TX_POWER;
  _prefs.buzzer_quiet = DEFAULT_BUZZER_QUIET ? 1 : 0;
  _prefs.gps_enabled = 0;       // GPS disabled by default
  _prefs.gps_interval = 0;      // Use the default 1-second fix-processing interval
  _prefs.autoadd_config = DEFAULT_AUTOADD_CONFIG;
  _prefs.path_hash_mode = DEFAULT_PATH_HASH_MODE;
  _prefs.radio_fem_txgain = 0;
#ifdef DEFAULT_RX_DELAY_BASE
  _prefs.rx_delay_base = DEFAULT_RX_DELAY_BASE;
#endif
  _prefs.setRepeatEn(false);
#if defined(USE_SX1262) || defined(USE_SX1268) || defined(USE_LR1110) \
    || defined(USE_LR2021) || defined(SX126X_RX_BOOSTED_GAIN) \
    || defined(RX_BOOSTED_GAIN)
  _prefs.rx_boosted_gain = mesh::radio::configuredRxBoostedGainDefault();
#endif
  _prefs.radio_fem_rxgain = DEFAULT_FEM_RX_GAIN;
  _prefs.rx_powersaving_enabled = RXPS_FIXED_ENABLED ? 1 : 0;
  _prefs.rx_ps_level = RXPS_FIXED_LEVEL;
  _prefs.rx_ps_preamble = RXPS_FIXED_PREAMBLE;
  _prefs.rx_ps_rx_us = RX_POWERSAVING_DEFAULT_RX_US;
  _prefs.rx_ps_sleep_us = RX_POWERSAVING_DEFAULT_SLEEP_US;
  _prefs.powersaving_enabled = 1;
  _prefs.powersaving_policy_version = 0;
  _prefs.wifi_enabled = 1;
  memset(_prefs.bluetooth_name, 0, sizeof(_prefs.bluetooth_name));
  _prefs.display_rotation_degrees = 0;
  _prefs.cad_enabled = DEFAULT_CAD_ENABLED ? 1 : 0;
  _prefs.cad_scan_timeout_ms = 0;
  _prefs.cad_retry_delay_ms = 0;
  _prefs.cad_max_duration_ms = 0;
#if defined(ENABLE_USB_INTERFACE)
  // Keep a USB Companion's primary stream exclusively framed on a fresh
  // install. nRF52 Full can add a diagnostics port; ESP32 and other single-TTY
  // builds switch the primary stream into the text terminal before logs.
  _prefs.usb_logging_enabled = 0;
#else
  _prefs.usb_logging_enabled = 1;
#endif
  recalcRxPowerSavingFromLevel(_prefs.rx_ps_level, _prefs.sf, _prefs.bw,
                               _prefs.rx_ps_preamble, &_prefs.rx_ps_rx_us,
                               &_prefs.rx_ps_sleep_us);

#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
  memset(&_mqtt_prefs, 0, sizeof(_mqtt_prefs));
  _mqtt_bridge = nullptr;
  _mqtt_configured = false;
  _mqtt_started = false;
#endif
#ifdef WITH_WEBCONFIG
  _webconfig = nullptr;
  _wc_mqtt_dirty = false;
#endif
}

void MyMesh::begin(bool has_display, bool radio_available) {
  _radio_available = radio_available;
  setRadioAvailable(radio_available);
  initializeContactStorage();
  initializeOfflineQueue();
  BaseChatMesh::begin();

  const bool is_new_install = !_store->loadMainIdentity(self_id)
      || mesh::hasReservedIdentityPrefix(self_id);
  bool identity_ready = true;
  if (is_new_install) {
    identity_ready = mesh::generateUsableLocalIdentity(self_id, radio_new_identity);
    if (identity_ready) _store->saveMainIdentity(self_id);
  }

#if defined(ESP32_PLATFORM)
  mesh::discardESP32TrueRandom();
#endif
  if (!identity_ready) {
    MESH_DEBUG_PRINTLN("Identity generation exhausted all attempts; rebooting");
    board.reboot();
    return;
  }

// if name is provided as a build flag, use that as default node name instead
#ifdef ADVERT_NAME
  strcpy(_prefs.node_name, ADVERT_NAME);
#else
  // use hex of first 4 bytes of identity public key as default node name
  char pub_key_hex[10];
  mesh::Utils::toHex(pub_key_hex, self_id.pub_key, 4);
  strcpy(_prefs.node_name, pub_key_hex);
#endif

  // if build provides default-scope, init with that
#ifdef DEFAULT_FLOOD_SCOPE_NAME
  strcpy(_prefs.default_scope_name, DEFAULT_FLOOD_SCOPE_NAME);
  {
    TransportKeyStore temp;
    TransportKey key;
    temp.getAutoKeyFor(0, "#" DEFAULT_FLOOD_SCOPE_NAME, key);
    memcpy(_prefs.default_scope_key, key.key, sizeof(key.key));
  }
#endif

  // load persisted prefs
  _store->loadPrefs(_prefs, sensors.node_lat, sensors.node_lon);

  // v1.17.1.2 repairs the Companion default-off regression for both fresh
  // installs and devices that already persisted the regressed value. The
  // appended policy marker makes this a one-time migration, so a later
  // explicit `powersaving off` choice remains persistent.
  const bool power_saving_default_migrated =
      migrateCompanionPowerSavingDefault(_prefs);

  _prefs.node_name[sizeof(_prefs.node_name) - 1] = 0;
  const bool bluetooth_name_had_terminator =
      memchr(_prefs.bluetooth_name, 0, sizeof(_prefs.bluetooth_name)) != NULL;
  _prefs.bluetooth_name[sizeof(_prefs.bluetooth_name) - 1] = 0;
  const bool bluetooth_name_repaired =
      !bluetooth_name_had_terminator
      || (_prefs.bluetooth_name[0] != 0
          && !mesh::companion::isValidBluetoothName(_prefs.bluetooth_name));
  if (bluetooth_name_repaired) {
    memset(_prefs.bluetooth_name, 0, sizeof(_prefs.bluetooth_name));
  }
  const bool display_rotation_repaired =
      _prefs.display_rotation_degrees != 0
      && _prefs.display_rotation_degrees != 90
      && _prefs.display_rotation_degrees != 180
      && _prefs.display_rotation_degrees != 270;
  if (display_rotation_repaired) {
    _prefs.display_rotation_degrees = 0;
  }

  // sanitise bad pref values
  _prefs.rx_delay_base = constrain(_prefs.rx_delay_base, 0, 20.0f);
  _prefs.airtime_factor = constrain(_prefs.airtime_factor, 0, 9.0f);
  _prefs.freq = constrain(_prefs.freq, 150.0f, 2500.0f);
  _prefs.bw = constrain(_prefs.bw, 7.8f, 500.0f);
  _prefs.sf = constrain(_prefs.sf, 5, 12);
  _prefs.cr = constrain(_prefs.cr, 5, 8);
  _prefs.tx_power_dbm = constrain(_prefs.tx_power_dbm, -9, MAX_LORA_TX_POWER);
  _prefs.multi_acks = constrain(_prefs.multi_acks, 0, 1);
  _prefs.manual_add_contacts = constrain(_prefs.manual_add_contacts, 0, 1);
  _prefs.vibe_quiet = constrain(_prefs.vibe_quiet, 0, 1);
  _prefs.gps_enabled = constrain(_prefs.gps_enabled, 0, 1);  // Ensure boolean 0 or 1
  _prefs.gps_interval = constrain(_prefs.gps_interval, 0, 86400);  // Max 24 hours
  _prefs.autoadd_config &= AUTO_ADD_OVERWRITE_OLDEST | AUTO_ADD_CHAT | AUTO_ADD_REPEATER | AUTO_ADD_ROOM_SERVER | AUTO_ADD_SENSOR;
  _prefs.path_hash_mode = constrain(_prefs.path_hash_mode, 0, 2);
  _prefs.radio_fem_rxgain_override = constrain(_prefs.radio_fem_rxgain_override, 0, 1);
  if (!_prefs.radio_fem_rxgain_override) {
    _prefs.radio_fem_rxgain = DEFAULT_FEM_RX_GAIN;
  }
  _prefs.radio_fem_rxgain = constrain(_prefs.radio_fem_rxgain, 0, 1);
  _prefs.radio_fem_txgain = constrain(_prefs.radio_fem_txgain, 0, 1);
  _prefs.rx_powersaving_enabled = constrain(_prefs.rx_powersaving_enabled, 0, 1);
  _prefs.powersaving_enabled = constrain(_prefs.powersaving_enabled, 0, 1);
  _prefs.wifi_enabled = constrain(_prefs.wifi_enabled, 0, 1);
  _prefs.usb_logging_enabled = constrain(_prefs.usb_logging_enabled, 0, 1);
  _prefs.cad_enabled = constrain(_prefs.cad_enabled, 0, 1);
  if (_prefs.cad_scan_timeout_ms != 0
      && (_prefs.cad_scan_timeout_ms < mesh::CAD_SCAN_MIN_TIMEOUT_MS
          || _prefs.cad_scan_timeout_ms > mesh::CAD_SCAN_MAX_TIMEOUT_MS)) {
    _prefs.cad_scan_timeout_ms = 0;
  }
  _prefs.rx_ps_level = constrain(_prefs.rx_ps_level, 0, 10);
  if (_prefs.rx_ps_preamble != 16 && _prefs.rx_ps_preamble != 32) {
    _prefs.rx_ps_preamble = 0;
  }
  ensureRxPowerSavingDefaults(&_prefs.rx_ps_rx_us, &_prefs.rx_ps_sleep_us);
  recalcRxPowerSavingFromLevel(_prefs.rx_ps_level, _prefs.sf, _prefs.bw,
                               _prefs.rx_ps_preamble, &_prefs.rx_ps_rx_us,
                               &_prefs.rx_ps_sleep_us);
  if (power_saving_default_migrated || bluetooth_name_repaired
      || display_rotation_repaired) {
    _store->savePrefs(_prefs, sensors.node_lat, sensors.node_lon);
  }
#if MESH_USB_LOGGING_AVAILABLE
  const bool usb_logging_enabled = _prefs.usb_logging_enabled != 0;
  mesh::setUsbLoggingEnabled(usb_logging_enabled);
  if (!mesh::saveUsbLoggingBootPreference(usb_logging_enabled)) {
    MESH_DEBUG_PRINTLN("Unable to save next-boot USB logging interface state");
  }
#endif

#ifdef BLE_PIN_CODE // 123456 by default
  if (_prefs.ble_pin == 0) {
#ifdef DISPLAY_CLASS
    if (has_display && BLE_PIN_CODE == 123456) {
      StdRNG rng;
      _active_ble_pin = rng.nextInt(100000, 999999); // random pin each session
    } else {
      _active_ble_pin = BLE_PIN_CODE; // otherwise static pin
    }
#else
    _active_ble_pin = BLE_PIN_CODE; // otherwise static pin
#endif
  } else {
    _active_ble_pin = _prefs.ble_pin;
  }
#else
  _active_ble_pin = 0;
#endif

  resetContacts();
  _store->loadContacts(this);
  if (_store->hasPendingContactWrites()) {
    dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
  }
  updateGpsTelemetryPolicy();
  bootstrapRTCfromContacts();
  addChannel("Public", PUBLIC_GROUP_PSK); // pre-configure Andy's public channel
  if (is_new_install) {
    addChannel("#testing", TESTING_GROUP_PSK);
  }
  // A saved table takes precedence over compiled defaults on later boots.
  _store->loadChannels(this);
  if (is_new_install) {
    // Make the first-boot defaults survive before a companion app changes them.
    _store->saveChannels(this);
  }

#ifdef COMPANION_MESH_CLOCK_SYNC
  // Fixed fallback policy for this Companion build. A successful host time
  // update below suppresses mesh correction for the remainder of the boot.
  _clock_sync.begin(nullptr);
#endif

  configureRadioFromPrefs();

#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  if (_prefs.wifi_enabled != 0) {
#endif
  applyMQTTDefaults(&_mqtt_prefs);
  _mqtt_configured = CompanionMqttSetupPortal::loadStoredConfig(_mqtt_prefs);
  if (!_mqtt_configured) applyMQTTDefaults(&_mqtt_prefs);
  // Companion WiFi owns the connection and its mesh-wifi NVS setting is
  // canonical. Keep MQTT reconnects from restoring a stale MQTT-pref value.
  _mqtt_prefs.wifi_power_save = getCompanionWiFiPowerSave();

  MQTTNodeInfo node_info;
  node_info.node_name = _prefs.node_name;
  node_info.freq = &_prefs.freq;
  node_info.bw = &_prefs.bw;
  node_info.sf = &_prefs.sf;
  node_info.cr = &_prefs.cr;
  node_info.repeat_flag = &_prefs.client_repeat;
  node_info.repeat_when_nonzero = true;
  _mqtt_bridge = new MQTTBridge(node_info, &_mqtt_prefs,
                                getRTCClock(), &self_id, false);
  if (_mqtt_bridge) {
    char device_id[65];
    mesh::Utils::toHex(device_id, self_id.pub_key, PUB_KEY_SIZE);
    _mqtt_bridge->setDeviceID(device_id);
    _mqtt_bridge->setFirmwareVersion(FIRMWARE_VERSION);
    _mqtt_bridge->setBoardModel(board.getManufacturerName());
    _mqtt_bridge->setBuildDate(FIRMWARE_BUILD_DATE);
    _mqtt_bridge->setStatsSources(this, _radio, &board, _ms);
  }
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  }
#endif
#endif

#ifdef WITH_WEBCONFIG
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  if (_prefs.wifi_enabled != 0) {
#endif
  void* web_mqtt_prefs = nullptr;
#ifdef WITH_MQTT_BRIDGE
  web_mqtt_prefs = &_mqtt_prefs;
#endif
  _webconfig = new WebConfigServer(this, web_mqtt_prefs, false,
                                   self_id.pub_key, FIRMWARE_VERSION,
                                   FIRMWARE_BUILD_DATE,
                                   "companion", board.getManufacturerName());
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  }
#endif
#endif
}

void MyMesh::configureRadioFromPrefs() {
  board.attachDynamicPrefs(_prefs.getCustom());

  if (!_radio_available) {
    saved_radio_apply_pending = false;
    board.setLoRaFemLnaEnabled(_prefs.radio_fem_rxgain);
    board.setLoRaFemPaGainEnabled(_prefs.radio_fem_txgain);
    MESH_DEBUG_PRINTLN("Radio unavailable: companion services are running in recovery mode");
    return;
  }

  saved_radio_apply_pending = !applySavedRadioParams();
  radio_driver.setCADScanTimeoutMillis(_prefs.cad_scan_timeout_ms);
  _radio->setCADEnabled(_prefs.cad_enabled != 0);
  if (!saved_radio_apply_pending) {
    radio_driver.setTxPower(_prefs.tx_power_dbm);
    radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain);
  }
  const bool fem_gain_changed = board.canControlLoRaFemLna()
      && board.isLoRaFemLnaEnabled() != (_prefs.radio_fem_rxgain != 0);
  if (board.setLoRaFemLnaEnabled(_prefs.radio_fem_rxgain) && fem_gain_changed) {
    _radio->recalibrateNoiseFloor();
  }
  board.setLoRaFemPaGainEnabled(_prefs.radio_fem_txgain);
  MESH_DEBUG_PRINTLN("RX Boosted Gain Mode: %s",
                     radio_driver.getRxBoostedGainMode() ? "Enabled" : "Disabled");
}

void MyMesh::activateRadio() {
  if (_radio_available) return;

  _radio_available = true;
  setRadioAvailable(true);
  configureRadioFromPrefs();
  MESH_DEBUG_PRINTLN("Radio recovery completed; mesh transport is active");
}

mesh::RadioParamApplyResult MyMesh::tryApplyRadioParams(float freq, float bw, uint8_t sf, uint8_t cr) {
  if (!_radio_available) return mesh::RadioParamApplyResult::FAILED;

  uint32_t rx_us = _prefs.rx_ps_rx_us;
  uint32_t sleep_us = _prefs.rx_ps_sleep_us;
  if (_prefs.rx_powersaving_enabled && _prefs.rx_ps_level != 0
      && !recalcRxPowerSavingFromLevel(_prefs.rx_ps_level, sf, bw,
                                       _prefs.rx_ps_preamble, &rx_us, &sleep_us)) {
    return mesh::RadioParamApplyResult::FAILED;
  }
  uint32_t timings[2] = {rx_us, sleep_us};
  const uint32_t* applied_timings = _prefs.rx_powersaving_enabled
      && radio_driver.supportsRxPowerSaving() ? timings : NULL;
  return radio_driver.trySetParams(freq, bw, sf, cr, applied_timings);
}

bool MyMesh::applySavedRadioParams() {
  return tryApplyRadioParams(_prefs.freq, _prefs.bw, _prefs.sf, _prefs.cr)
      == mesh::RadioParamApplyResult::APPLIED;
}

void MyMesh::finishRadioParamApply(float freq, float bw, uint8_t sf, uint8_t cr, uint8_t repeat) {
  _prefs.sf = sf;
  _prefs.cr = cr;
  _prefs.freq = freq;
  _prefs.bw = bw;
  _prefs.client_repeat = repeat;
  recalcRxPowerSavingFromLevel(_prefs.rx_ps_level, _prefs.sf, _prefs.bw,
                               _prefs.rx_ps_preamble, &_prefs.rx_ps_rx_us,
                               &_prefs.rx_ps_sleep_us);
  savePrefs();

  saved_radio_apply_pending = false;
  radio_apply_retry_at = 0;
  radio_apply_failures = 0;

  MESH_DEBUG_PRINTLN("OK: CMD_SET_RADIO_PARAMS: f=%d, bw=%d, sf=%d, cr=%d",
                     (uint32_t)(freq * 1000.0f), (uint32_t)(bw * 1000.0f),
                     (uint32_t)sf, (uint32_t)cr);
  writeOKFrame();
}

void MyMesh::cancelPendingRadioParamApply() {
  if (!command_radio_apply_pending) return;

  command_radio_apply_pending = false;
  command_radio_apply_deadline = 0;
  // The requested tuple was not committed. Reassert the persisted tuple in
  // case a failed hardware apply only restored part of the old configuration.
  saved_radio_apply_pending = true;
  radio_apply_retry_at = 0;
  radio_apply_failures = 0;
}

void MyMesh::servicePendingRadioParamApply() {
  if (!command_radio_apply_pending) return;
  if (!_serial || !_serial->isConnected()) {
    cancelPendingRadioParamApply();
    return;
  }

  // Leave enough queue headroom for the eventual command response.
  if (_serial->isWriteBusy()) return;

  mesh::RadioParamApplyResult result = tryApplyRadioParams(
      command_radio_freq, command_radio_bw, command_radio_sf, command_radio_cr);
  if (result == mesh::RadioParamApplyResult::APPLIED) {
    float freq = command_radio_freq;
    float bw = command_radio_bw;
    uint8_t sf = command_radio_sf;
    uint8_t cr = command_radio_cr;
    uint8_t repeat = command_radio_repeat;
    command_radio_apply_pending = false;
    command_radio_apply_deadline = 0;
    finishRadioParamApply(freq, bw, sf, cr, repeat);
  } else if (result == mesh::RadioParamApplyResult::FAILED) {
    cancelPendingRadioParamApply();
    writeErrFrame(ERR_CODE_ILLEGAL_ARG);
  } else if (command_radio_apply_deadline == _ms->getMillis()
             || millisHasNowPassed(command_radio_apply_deadline)) {
    cancelPendingRadioParamApply();
    writeErrFrame(ERR_CODE_BAD_STATE);
  }
}

#if COMPANION_FEATURE_TEMP_RADIO
static bool isFullCompanionBandwidth(float bw) {
  static const float supported[] = {
    7.8f, 10.4f, 15.6f, 20.8f, 31.25f, 41.7f,
    62.5f, 125.0f, 250.0f, 500.0f
  };
  for (float candidate : supported) {
    if (fabsf(candidate - bw) <= 0.01f) return true;
  }
  return false;
}

bool MyMesh::scheduleTempRadio(float freq, float bw, uint8_t sf, uint8_t cr,
                               uint32_t timeout_mins, char* reply,
                               size_t reply_size) {
  if (command_radio_apply_pending) {
    snprintf(reply, reply_size, "ERR companion radio change is already pending");
    return false;
  }

  _temp_radio_freq = freq;
  _temp_radio_bw = bw;
  _temp_radio_sf = sf;
  _temp_radio_cr = cr;
  _temp_radio_set_at = futureMillis(1500);  // let the local reply drain first
  _temp_radio_revert_at = futureMillis(1500 + (int)(timeout_mins * 60000UL));
  _temp_radio_retry_at = 0;
  _temp_radio_failures = 0;
  snprintf(reply, reply_size, "OK - temp params for %lu mins",
           (unsigned long)timeout_mins);
  appendRxPowerSavingAdjustmentNote(reply, reply_size, sf, bw);
  return true;
}

void MyMesh::scheduleNormalRadio(char* reply, size_t reply_size) {
  _temp_radio_set_at = 0;
  _temp_radio_revert_at = futureMillis(1500);  // keep the reply on the active tuple
  _temp_radio_retry_at = 0;
  _temp_radio_failures = 0;
  snprintf(reply, reply_size, "OK - normal radio restore scheduled");
}
#endif

static bool parseCadTimingMillis(const char* text, uint32_t minimum,
                                 uint32_t maximum, uint16_t& result) {
  if (strcmp(text, "auto") == 0 || strcmp(text, "0") == 0) {
    result = 0;
    return true;
  }

  uint32_t parsed = 0;
  if (!mesh::cli::parseUnsignedIntegerStrict(text, parsed)
      || parsed < minimum || parsed > maximum) {
    return false;
  }
  result = (uint16_t)parsed;
  return true;
}

bool MyMesh::handleCadCommand(const char* command, char* reply,
                              size_t reply_size) {
  if (command == NULL || reply == NULL || reply_size == 0) return false;

  if (strcmp(command, "get radio.cad") == 0
      || strcmp(command, "get cad") == 0) {
    char scan[32];
    char retry[24];
    char maximum[24];
    if (_prefs.cad_scan_timeout_ms == 0) {
      uint32_t effective = _radio_available
          ? radio_driver.getCADScanTimeoutMillis() : 0;
      if (effective == 0) {
        effective = mesh::calculateCadScanTimeoutMillis(_prefs.sf, _prefs.bw);
      }
      snprintf(scan, sizeof(scan), "auto(%lu)", (unsigned long)effective);
    } else {
      snprintf(scan, sizeof(scan), "%u", (unsigned)_prefs.cad_scan_timeout_ms);
    }
    if (_prefs.cad_retry_delay_ms == 0) {
      snprintf(retry, sizeof(retry), "auto");
    } else {
      snprintf(retry, sizeof(retry), "%u", (unsigned)_prefs.cad_retry_delay_ms);
    }
    if (_prefs.cad_max_duration_ms == 0) {
      snprintf(maximum, sizeof(maximum), "auto");
    } else {
      snprintf(maximum, sizeof(maximum), "%u", (unsigned)_prefs.cad_max_duration_ms);
    }
    snprintf(reply, reply_size,
             "radio.cad %s, scan=%s ms, retry=%s ms, max=%s ms",
             _prefs.cad_enabled ? "on" : "off", scan, retry, maximum);
    return true;
  }

  const char* value = NULL;
  if (strncmp(command, "set radio.cad", 13) == 0
      && (command[13] == 0 || command[13] == ' '
          || command[13] == '\t')) {
    value = command + 13;
  } else if (strncmp(command, "set cad", 7) == 0
             && (command[7] == 0 || command[7] == ' '
                 || command[7] == '\t')) {
    value = command + 7;
  } else {
    return false;
  }
  while (*value == ' ' || *value == '\t') value++;

  if (strcmp(value, "on") == 0 || strcmp(value, "off") == 0) {
    const uint8_t previous = _prefs.cad_enabled;
    _prefs.cad_enabled = strcmp(value, "on") == 0 ? 1 : 0;
    if (_radio_available) _radio->setCADEnabled(_prefs.cad_enabled != 0);
    if (!savePrefs()) {
      _prefs.cad_enabled = previous;
      if (_radio_available) _radio->setCADEnabled(previous != 0);
      snprintf(reply, reply_size, "Error: CAD changed but save failed");
    } else {
      snprintf(reply, reply_size, "OK - radio.cad %s", value);
    }
    return true;
  }

  if (strncmp(value, "timings", 7) == 0
      && (value[7] == 0 || value[7] == ' ' || value[7] == '\t')) {
    value += 7;
    while (*value == ' ' || *value == '\t') value++;
    char token_storage[64] = {0};
    const char* tokens[3] = {NULL};
    size_t token_count = 0;
    uint16_t scan_ms;
    uint16_t retry_ms;
    uint16_t max_ms;
    if (!mesh::cli::splitWhitespaceFieldsStrict(
            value, token_storage, sizeof(token_storage), tokens, 3,
            token_count)
        || token_count != 3
        || !parseCadTimingMillis(tokens[0],
              mesh::CAD_SCAN_MIN_TIMEOUT_MS,
              mesh::CAD_SCAN_MAX_TIMEOUT_MS, scan_ms)
        || !parseCadTimingMillis(tokens[1], 1, 60000, retry_ms)
        || !parseCadTimingMillis(tokens[2], 1, 60000, max_ms)) {
      snprintf(reply, reply_size,
               "Error: use set radio.cad timings <auto|100-3500> <auto|1-60000> <auto|1-60000>");
      return true;
    }

    const uint16_t previous_scan = _prefs.cad_scan_timeout_ms;
    const uint16_t previous_retry = _prefs.cad_retry_delay_ms;
    const uint16_t previous_max = _prefs.cad_max_duration_ms;
    if (_radio_available && !radio_driver.setCADScanTimeoutMillis(scan_ms)) {
      snprintf(reply, reply_size, "Error: CAD scan timing is unsupported");
      return true;
    }
    _prefs.cad_scan_timeout_ms = scan_ms;
    _prefs.cad_retry_delay_ms = retry_ms;
    _prefs.cad_max_duration_ms = max_ms;
    if (!savePrefs()) {
      _prefs.cad_scan_timeout_ms = previous_scan;
      _prefs.cad_retry_delay_ms = previous_retry;
      _prefs.cad_max_duration_ms = previous_max;
      if (_radio_available) radio_driver.setCADScanTimeoutMillis(previous_scan);
      snprintf(reply, reply_size, "Error: CAD timings changed but save failed");
    } else {
      snprintf(reply, reply_size, "OK - radio.cad timings saved");
    }
    return true;
  }

  snprintf(reply, reply_size,
           "Error: use set radio.cad <on|off> or set radio.cad timings <scan_ms|auto> <retry_ms|auto> <max_ms|auto>");
  return true;
}

bool MyMesh::handleLocalControlCommand(const char* command, char* reply,
                                       size_t reply_size) {
  if (!command || !reply || reply_size == 0) return false;
  while (*command == ' ') command++;

  if (strcmp(command, "board") == 0) {
    const char* hardware_name = board.getManufacturerName();
    snprintf(reply, reply_size, "%s",
             hardware_name != NULL ? hardware_name : "Unknown hardware");
    return true;
  }

  if (strcmp(command, "version") == 0) {
    snprintf(reply, reply_size, "Companion %s (protocol %u, build %s)",
             FIRMWARE_VERSION, (unsigned)FIRMWARE_VER_CODE,
             FIRMWARE_BUILD_DATE);
    return true;
  }

#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS
  if (strcmp(command, "memory") == 0) {
    const mesh::CompanionMemoryDiagnostics diagnostics = {
      (uint32_t)ESP.getFreeHeap(),
      (uint32_t)ESP.getMinFreeHeap(),
      (uint32_t)ESP.getMaxAllocHeap(),
      (uint32_t)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
      (uint32_t)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
      (uint32_t)ESP.getFreePsram(),
      (uint32_t)ESP.getPsramSize(),
      offline_queue_len,
      getOfflineQueueCapacity()
    };
    mesh::formatCompanionMemoryDiagnostics(reply, reply_size, diagnostics);
    return true;
  }
#endif

  if (handleCadCommand(command, reply, reply_size)) return true;

  if (strcmp(command, "get radio.rxgain") == 0) {
    if (!radio_driver.supportsRxBoostedGainMode()) {
      snprintf(reply, reply_size, "Error: unsupported");
    } else {
      snprintf(reply, reply_size, "> %s",
               _prefs.rx_boosted_gain ? "on" : "off");
    }
    return true;
  }

  if (strncmp(command, "set radio.rxgain ", 17) == 0) {
    const char* value = command + 17;
    if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) {
      snprintf(reply, reply_size,
               "Error: use set radio.rxgain on|off");
    } else if (!radio_driver.supportsRxBoostedGainMode()) {
      snprintf(reply, reply_size, "Error: unsupported");
    } else if (!applyAndSaveRxBoostedGain(strcmp(value, "on") == 0)) {
      snprintf(reply, reply_size, "Error: radio busy or save failed");
    } else {
      snprintf(reply, reply_size, "OK - radio.rxgain %s", value);
    }
    return true;
  }

  if (strcmp(command, "get radio.fem.rxgain") == 0) {
    if (!board.canControlLoRaFemLna()) {
      snprintf(reply, reply_size, "Error: unsupported");
    } else {
      snprintf(reply, reply_size, "> %s",
               board.isLoRaFemLnaEnabled() ? "on" : "off");
    }
    return true;
  }

  if (strncmp(command, "set radio.fem.rxgain ", 21) == 0) {
    const char* value = command + 21;
    if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) {
      snprintf(reply, reply_size,
               "Error: use set radio.fem.rxgain on|off");
    } else if (!board.canControlLoRaFemLna()) {
      snprintf(reply, reply_size, "Error: unsupported");
    } else if (!applyAndSaveFemRxGain(strcmp(value, "on") == 0)) {
      snprintf(reply, reply_size, "Error: failed to apply or save FEM RX gain");
    } else {
      snprintf(reply, reply_size, "OK - radio.fem.rxgain %s", value);
    }
    return true;
  }

  if (strcmp(command, "get radio.fem.txgain") == 0) {
    if (!board.canControlLoRaFemPaGain()) {
      snprintf(reply, reply_size, "Error: unsupported");
    } else {
      snprintf(reply, reply_size, "> %s",
               board.isLoRaFemPaGainEnabled() ? "on" : "off");
    }
    return true;
  }

  if (strncmp(command, "set radio.fem.txgain ", 21) == 0) {
    const char* value = command + 21;
    if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) {
      snprintf(reply, reply_size,
               "Error: use set radio.fem.txgain on|off");
    } else if (!board.canControlLoRaFemPaGain()) {
      snprintf(reply, reply_size, "Error: unsupported");
    } else if (!applyAndSaveFemTxGain(strcmp(value, "on") == 0)) {
      snprintf(reply, reply_size, "Error: failed to apply or save FEM TX gain");
    } else {
      snprintf(reply, reply_size, "OK - radio.fem.txgain %s", value);
    }
    return true;
  }

  if (strcmp(command, "get display.rotation") == 0) {
    if (_ui == NULL || !_ui->supportsDisplayRotation()) {
      snprintf(reply, reply_size, "Error: display rotation is unsupported");
    } else if (_prefs.display_rotation_degrees == 0) {
      snprintf(reply, reply_size, "display.rotation default");
    } else {
      snprintf(reply, reply_size, "display.rotation %u",
               (unsigned)_prefs.display_rotation_degrees);
    }
    return true;
  }

  if (strncmp(command, "set display.rotation", 20) == 0
      && (command[20] == 0 || command[20] == ' '
          || command[20] == '\t')) {
    const char* value = command + 20;
    while (*value == ' ' || *value == '\t') value++;
    char* end = NULL;
    const unsigned long degrees = strtoul(value, &end, 10);
    while (end != NULL && (*end == ' ' || *end == '\t')) end++;
    const bool valid = value[0] != 0 && end != NULL && *end == 0
        && (degrees == 0 || degrees == 90 || degrees == 180
            || degrees == 270);
    if (!valid) {
      snprintf(reply, reply_size,
               "Error: use set display.rotation <0|90|180|270>");
    } else if (_ui == NULL || !_ui->supportsDisplayRotation()) {
      snprintf(reply, reply_size, "Error: display rotation is unsupported");
    } else {
      const uint16_t previous = _prefs.display_rotation_degrees;
      if (!_ui->setDisplayRotationDegrees((uint16_t)degrees)) {
        snprintf(reply, reply_size, "Error: display rotation failed");
      } else {
        _prefs.display_rotation_degrees = (uint16_t)degrees;
        if (!savePrefs()) {
          _prefs.display_rotation_degrees = previous;
          _ui->setDisplayRotationDegrees(previous);
          snprintf(reply, reply_size,
                   "Error: display rotation changed but save failed");
        } else if (degrees == 0) {
          snprintf(reply, reply_size,
                   "OK - display rotation reset to board default");
        } else {
          snprintf(reply, reply_size, "OK - display rotation %lu",
                   degrees);
        }
      }
    }
    return true;
  }

  if (strcmp(command, "get bluetooth.name") == 0
      || strcmp(command, "get ble.name") == 0) {
    formatBluetoothNameStatus(reply, reply_size);
    return true;
  }

  const char* bluetooth_name_value = NULL;
  if (strncmp(command, "set bluetooth.name", 18) == 0
      && (command[18] == 0 || command[18] == ' '
          || command[18] == '\t')) {
    bluetooth_name_value = command + 18;
  } else if (strncmp(command, "set ble.name", 12) == 0
             && (command[12] == 0 || command[12] == ' '
                 || command[12] == '\t')) {
    bluetooth_name_value = command + 12;
  }
  if (bluetooth_name_value != NULL) {
    while (*bluetooth_name_value == ' ' || *bluetooth_name_value == '\t') {
      bluetooth_name_value++;
    }
    if (bluetooth_name_value[0] == 0) {
      snprintf(reply, reply_size,
               "Error: use set bluetooth.name <name|default>");
    } else {
      const bool clear_override = strcmp(bluetooth_name_value, "default") == 0
          || strcmp(bluetooth_name_value, "clear") == 0;
      applyAndSaveBluetoothName(clear_override ? "" : bluetooth_name_value,
                                reply, reply_size);
    }
    return true;
  }

  if (strncmp(command, "set pin", 7) == 0
      && (command[7] == 0 || command[7] == ' '
          || command[7] == '\t')) {
    const char* value = command + 7;
    while (*value == ' ' || *value == '\t') value++;
    int32_t parsed = 0;
    if (!mesh::cli::parseIntegerStrict(value, parsed)
        || parsed < 0 || parsed > 999999) {
      snprintf(reply, reply_size, "Error: pin must be 0-999999");
    } else {
      const uint32_t previous = _prefs.ble_pin;
      _prefs.ble_pin = static_cast<uint32_t>(parsed);
      if (!savePrefs()) {
        _prefs.ble_pin = previous;
        snprintf(reply, reply_size, "Error: pin changed but save failed");
      } else {
        snprintf(reply, reply_size, "> pin is now %06lu",
                 (unsigned long)_prefs.ble_pin);
      }
    }
    return true;
  }

#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  if (strcmp(command, "get espnow.channel") == 0) {
    formatEspNowChannel(reply, reply_size);
    return true;
  }

  if (strncmp(command, "set espnow.channel", 18) == 0
      && (command[18] == 0 || command[18] == ' '
          || command[18] == '\t')) {
    const char* value = command + 18;
    while (*value == ' ' || *value == '\t') value++;
    applyAndSaveEspNowChannel(value, reply, reply_size);
    return true;
  }
#endif

#if defined(ESP32) && defined(WIFI_SSID)
  if (strcmp(command, "get display.wifi") == 0) {
    formatCompanionWiFiDisplayStatus(reply, reply_size);
    return true;
  }

#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  if (strcmp(command, "get companion.transport") == 0) {
    snprintf(reply, reply_size, "%s",
             getCompanionTransportMode() == CompanionTransportMode::WiFi
                 ? "wifi" : "ble");
    return true;
  }

  if (strncmp(command, "set companion.transport", 23) == 0
      && (command[23] == 0 || command[23] == ' '
          || command[23] == '\t')) {
    const char* value = command + 23;
    while (*value == ' ' || *value == '\t') value++;
    CompanionTransportMode selected;
    if (strcmp(value, "wifi") == 0) {
      selected = CompanionTransportMode::WiFi;
    } else if (strcmp(value, "ble") == 0) {
      selected = CompanionTransportMode::Bluetooth;
    } else {
      snprintf(reply, reply_size,
               "Error: use set companion.transport <wifi|ble>");
      return true;
    }
    if (!selectCompanionTransportMode(selected)) {
      snprintf(reply, reply_size,
               "Error: failed to save companion transport");
    } else {
      snprintf(reply, reply_size,
               "OK - companion transport %s saved; reboot required", value);
    }
    return true;
  }
#endif

#ifdef WITH_WEBCONFIG
  if (strncmp(command, "get ", 4) == 0) {
    const mesh::cli::StandaloneWiFiKey wifi_key =
        mesh::cli::classifyStandaloneWiFiGet(command + 4);
    switch (wifi_key) {
      case mesh::cli::StandaloneWiFiKey::SSID:
        WebConfigServer::formatWiFiSSID(reply, reply_size);
        return true;
      case mesh::cli::StandaloneWiFiKey::Status:
        WebConfigServer::formatWiFiStatus(reply, reply_size);
        return true;
      case mesh::cli::StandaloneWiFiKey::CLI:
        // Companion WebConfig deliberately has no browser command terminal:
        // the page is not an authenticated repeater/room-server admin
        // surface. Do not report the saved global WebConfig preference as
        // "waiting", because supportsCliTerminal() is false for this role and
        // /api/cli can therefore never become active. The Full Companion text
        // CLI remains available over USB and TCP port 5002.
        snprintf(reply, reply_size,
                 "Error: browser CLI unavailable; use USB (or TCP 5002 on Full Companion)");
        return true;
      default:
        break;
    }
  }

  if (strncmp(command, "set ", 4) == 0) {
    const char* value = NULL;
    const mesh::cli::StandaloneWiFiKey wifi_key =
        mesh::cli::classifyStandaloneWiFiSet(command + 4, &value);
    bool saved = false;
    switch (wifi_key) {
      case mesh::cli::StandaloneWiFiKey::SSID:
        saved = WebConfigServer::setStandaloneWiFiSSID(
            value, reply, reply_size);
        break;
      case mesh::cli::StandaloneWiFiKey::Password:
        saved = WebConfigServer::setStandaloneWiFiPassword(
            value, reply, reply_size);
        break;
      case mesh::cli::StandaloneWiFiKey::CLI:
        // See the matching getter above. In particular, do not persist a
        // preference and claim success for a terminal this role cannot serve.
        snprintf(reply, reply_size,
                 "Error: browser CLI unavailable; use USB (or TCP 5002 on Full Companion)");
        return true;
      default:
        break;
    }
    if (wifi_key == mesh::cli::StandaloneWiFiKey::SSID
        || wifi_key == mesh::cli::StandaloneWiFiKey::Password) {
      if (saved) {
        char saved_ssid[32] = {0};
        char saved_password[65] = {0};
        uint8_t saved_power_save = mesh::wifi::kDefaultPowerSave;
        const bool has_ssid = WebConfigServer::loadStandaloneWiFi(
            saved_ssid, sizeof(saved_ssid), saved_password,
            sizeof(saved_password), &saved_power_save);
        memset(saved_password, 0, sizeof(saved_password));
        if (_webconfig) {
          _webconfig->reloadStandaloneWiFi();
          if (has_ssid && _webconfig->isRunning()) {
            _webconfig->requestStop();
          }
        }
        if (has_ssid) {
          scheduleCompanionWiFiCredentialReload();
          snprintf(reply, reply_size,
                   "OK - WiFi %s saved; reconnect scheduled",
                   wifi_key == mesh::cli::StandaloneWiFiKey::SSID
                       ? "SSID" : "password");
        } else {
          snprintf(reply, reply_size,
                   "OK - WiFi password saved; set wifi.ssid to connect");
        }
      }
      return true;
    }
  }

  if (strcmp(command, "get webui") == 0) {
    const bool enabled = WebConfigServer::loadEnabled(true);
    if (!_webconfig
        || (!_webconfig->isRunning() && !_webconfig->isStopping())) {
      snprintf(reply, reply_size, "> %s, inactive", enabled ? "on" : "off");
    } else if (_webconfig->mode() == WebConfigServer::MODE_SETUP) {
      char ssid[33] = {0};
      char ip[16] = {0};
      WebConfigServer::getSetupInfo(ssid, sizeof(ssid), ip, sizeof(ip));
      snprintf(reply, reply_size, "> %s, setup AP %s http://%s/",
               enabled ? "on" : "off", ssid, ip);
    } else if (_webconfig->mode() == WebConfigServer::MODE_CONNECTING) {
      snprintf(reply, reply_size, "> %s, connecting to WiFi",
               enabled ? "on" : "off");
    } else {
      snprintf(reply, reply_size, "> %s, http://%s/",
               enabled ? "on" : "off",
               WiFi.localIP().toString().c_str());
    }
    return true;
  }

  if (strcmp(command, "set webui on") == 0
      || strcmp(command, "set webui off") == 0) {
    const bool enabled = command[10] == 'o' && command[11] == 'n';
    if (!WebConfigServer::saveEnabled(enabled)) {
      snprintf(reply, reply_size, "Error: failed to save webui setting");
    } else if (!enabled) {
      if (_webconfig && _webconfig->isRunning()) _webconfig->requestStop();
      snprintf(reply, reply_size, "OK - webui off");
    } else if (_webconfig
               && (_webconfig->isRunning() || _webconfig->isStopping())) {
      snprintf(reply, reply_size, "OK - webui on (already active)");
    } else {
      char start_reply[160] = {0};
      startWebConfig(false, start_reply);
      if (strncmp(start_reply, "WebConfig", 9) == 0) {
        snprintf(reply, reply_size, "OK - webui on; %s", start_reply);
      } else {
        snprintf(reply, reply_size, "%s", start_reply);
      }
    }
    return true;
  }

  if (strncmp(command, "set webui", 9) == 0
      && (command[9] == 0 || command[9] == ' ' || command[9] == '\t')) {
    snprintf(reply, reply_size, "Error: usage set webui on|off");
    return true;
  }

  if (strcmp(command, "start webconfig") == 0
      || strcmp(command, "start webconfig ap") == 0) {
    startWebConfig(strcmp(command, "start webconfig ap") == 0, reply);
    return true;
  }
  if (strncmp(command, "start webconfig", 15) == 0
      && (command[15] == 0 || command[15] == ' ' || command[15] == '\t')) {
    snprintf(reply, reply_size, "ERR: usage start webconfig [ap]");
    return true;
  }
  if (strcmp(command, "stop webconfig") == 0) {
    if (!_webconfig || !_webconfig->isRunning()) {
      snprintf(reply, reply_size, "Err: webconfig not running");
    } else {
      _webconfig->requestStop();
      snprintf(reply, reply_size, "OK - webconfig stopping");
    }
    return true;
  }
#endif

  if (strcmp(command, "get wifi.powersave") == 0) {
    formatWiFiPowerSaving(reply, reply_size);
    return true;
  }

  if (strcmp(command, "set wifi.powersave") == 0
      || strncmp(command, "set wifi.powersave ", 19) == 0) {
    const char* value = command + 18;
    while (*value == ' ') value++;
    applyAndSaveWiFiPowerSaving(value, reply, reply_size);
    return true;
  }
#endif

#if COMPANION_FEATURE_TEMP_RADIO || COMPANION_FEATURE_OTA_CLI
#if COMPANION_FEATURE_TEMP_RADIO
  if (strcmp(command, "tempradio") == 0) {
    if (_temp_radio_set_at) {
      snprintf(reply, reply_size, "TempRadio pending: %.3f,%.2f,%u,%u",
               _temp_radio_freq, _temp_radio_bw,
               (unsigned)_temp_radio_sf, (unsigned)_temp_radio_cr);
    } else if (isTempRadioActive()) {
      uint32_t seconds = (_temp_radio_revert_at - _ms->getMillis()) / 1000UL;
      snprintf(reply, reply_size, "TempRadio active: %.3f,%.2f,%u,%u %lus left",
               _temp_radio_freq, _temp_radio_bw,
               (unsigned)_temp_radio_sf, (unsigned)_temp_radio_cr,
               (unsigned long)seconds);
    } else {
      snprintf(reply, reply_size, "TempRadio inactive");
    }
    return true;
  }

  if (strncmp(command, "tempradio ", 10) == 0) {
    float freq = 0.0f, bw = 0.0f;
    uint8_t sf = 0, cr = 0;
    uint32_t timeout_mins = 0;
    if (!mesh::cli::parseTemporaryRadioTupleStrict(
            command + 10, freq, bw, sf, cr, timeout_mins)
        || !isfinite(freq) || !isfinite(bw)
        || freq < 150.0f || freq > 2500.0f
        || !isFullCompanionBandwidth(bw)
        || sf < 5 || sf > 12 || cr < 5 || cr > 8
        || timeout_mins == 0 || timeout_mins > 10080UL) {
      snprintf(reply, reply_size,
               "ERR usage: tempradio freq,bw,sf,cr,minutes (minutes 1-10080)");
      return true;
    }
    scheduleTempRadio(freq, bw, sf, cr, timeout_mins, reply, reply_size);
    return true;
  }

  if (strcmp(command, "normalradio") == 0) {
    scheduleNormalRadio(reply, reply_size);
    return true;
  }
#endif

#if COMPANION_FEATURE_OTA_CLI
  if (strncmp(command, "ota", 3) == 0
      && (command[3] == 0 || command[3] == ' ')) {
    char ota_reply[160] = {0};
    if (!mesh::ota::handle_ota_command(command, ota_reply, board)) return false;
    snprintf(reply, reply_size, "%s", ota_reply);
    return true;
  }
#endif
#endif

  return false;
}

#if COMPANION_FEATURE_TEMP_RADIO
void MyMesh::serviceTempRadio() {
  const unsigned long now = _ms->getMillis();
  const bool retry_ready = !_temp_radio_retry_at
      || _temp_radio_retry_at == now || millisHasNowPassed(_temp_radio_retry_at);
  const bool revert_due = _temp_radio_revert_at
      && (_temp_radio_revert_at == now || millisHasNowPassed(_temp_radio_revert_at));

  if (revert_due) {
    if (hasOutbound() || !retry_ready) return;
    mesh::RadioParamApplyResult result = tryApplyRadioParams(
        _prefs.freq, _prefs.bw, _prefs.sf, _prefs.cr);
    if (result == mesh::RadioParamApplyResult::APPLIED) {
      radio_driver.setTxPower(_prefs.tx_power_dbm);
      radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain);
      _temp_radio_set_at = 0;
      _temp_radio_revert_at = 0;
      _temp_radio_retry_at = 0;
      _temp_radio_failures = 0;
      _temp_radio_applied = false;
      saved_radio_apply_pending = false;
      MESH_DEBUG_PRINTLN("Full companion restored normal radio");
    } else {
      _temp_radio_retry_at = futureMillis(
          nextRadioApplyRetryDelay(_temp_radio_failures));
    }
    return;
  }

  if (!_temp_radio_set_at
      || (_temp_radio_set_at != now && !millisHasNowPassed(_temp_radio_set_at))
      || hasOutbound() || !retry_ready) return;

  mesh::RadioParamApplyResult result = tryApplyRadioParams(
      _temp_radio_freq, _temp_radio_bw, _temp_radio_sf, _temp_radio_cr);
  if (result == mesh::RadioParamApplyResult::APPLIED) {
    _temp_radio_set_at = 0;
    _temp_radio_retry_at = 0;
    _temp_radio_failures = 0;
    _temp_radio_applied = true;
    MESH_DEBUG_PRINTLN("Full companion entered TempRadio");
  } else if (result == mesh::RadioParamApplyResult::BUSY) {
    _temp_radio_retry_at = futureMillis(250);
  } else {
    // A partially applied tuple must never become persistent. Restore the
    // saved Companion settings through the same bounded retry path.
    _temp_radio_set_at = 0;
    _temp_radio_revert_at = futureMillis(1);
    _temp_radio_retry_at = 0;
    saved_radio_apply_pending = true;
  }
}
#endif

const char *MyMesh::getNodeName() {
  return _prefs.node_name;
}
CompanionNodePrefs *MyMesh::getNodePrefs() {
  return &_prefs;
}
uint32_t MyMesh::getBLEPin() {
  return _active_ble_pin;
}

#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
static void copyMqttString(char* dest, size_t dest_size, const char* src) {
  strncpy(dest, src ? src : "", dest_size - 1);
  dest[dest_size - 1] = 0;
}

void MyMesh::serviceMQTT(const char* wifi_ssid, const char* wifi_password) {
  if (!_mqtt_started) {
    if (strcmp(_mqtt_prefs.wifi_ssid, wifi_ssid ? wifi_ssid : "") != 0) {
      copyMqttString(_mqtt_prefs.wifi_ssid, sizeof(_mqtt_prefs.wifi_ssid), wifi_ssid);
    }
    if (strcmp(_mqtt_prefs.wifi_password, wifi_password ? wifi_password : "") != 0) {
      copyMqttString(_mqtt_prefs.wifi_password, sizeof(_mqtt_prefs.wifi_password), wifi_password);
    }
  }

  if (WiFi.status() == WL_CONNECTED && _mqtt_configured && !_mqtt_started && _mqtt_bridge
#ifdef WITH_WEBCONFIG
      && !isWebConfigSetupActive()
#endif
  ) {
    _mqtt_started = true;  // begin is one-shot; avoid retrying partial allocations
    _mqtt_bridge->begin();
    if (_mqtt_bridge->isRunning()) {
      mesh::usbLoggingPort().println("MQTT companion: bridge started");
    } else {
      mesh::usbLoggingPort().println(
          "MQTT companion: bridge could not start");
    }
  }
}

void MyMesh::stopMQTT() {
  if (_mqtt_started && _mqtt_bridge) _mqtt_bridge->end();
  _mqtt_started = false;
}
#endif

#ifdef WITH_WEBCONFIG
static bool wcParseBool(const char* value, bool& out) {
  if (strcmp(value, "on") == 0) { out = true; return true; }
  if (strcmp(value, "off") == 0) { out = false; return true; }
  return false;
}

static bool wcParseLong(const char* value, long min_value, long max_value, long& out) {
  if (!value || !value[0]) return false;
  char* end = nullptr;
  long parsed = strtol(value, &end, 10);
  if (!end || *end != 0 || parsed < min_value || parsed > max_value) return false;
  out = parsed;
  return true;
}

static bool wcParseDouble(const char* value, double min_value, double max_value, double& out) {
  float parsed = 0.0f;
  if (!mesh::cli::parseDecimalStrict(value, parsed)
      || parsed < min_value || parsed > max_value) return false;
  out = parsed;
  return true;
}

static bool wcValidNtpHost(const char* value) {
  if (!value || !value[0] || strlen(value) > 63 || value[0] == '.') return false;
  size_t len = strlen(value);
  if (value[len - 1] == '.') return false;
  for (size_t i = 0; i < len; i++) {
    char c = value[i];
    if (!isalnum(static_cast<unsigned char>(c)) && c != '.' && c != '-') return false;
  }
  return true;
}

static bool wcValidHexKey(const char* value) {
  if (!value || strlen(value) != 64) return false;
  for (int i = 0; i < 64; i++) {
    if (!isxdigit(static_cast<unsigned char>(value[i]))) return false;
  }
  return true;
}

static bool wcCopyValue(char* dest, size_t dest_size, const char* value) {
  if (!dest || dest_size == 0 || !value || strlen(value) >= dest_size) return false;
  strncpy(dest, value, dest_size - 1);
  dest[dest_size - 1] = 0;
  return true;
}

void MyMesh::getNodeSnapshot(WebConfigServer::NodeSnapshot& s) {
  memset(&s, 0, sizeof(s));
  wcCopyValue(s.name, sizeof(s.name), _prefs.node_name);
  wcCopyValue(s.bluetooth_name, sizeof(s.bluetooth_name),
              _prefs.bluetooth_name);
  s.lat = sensors.node_lat;
  s.lon = sensors.node_lon;
  s.freq = _prefs.freq;
  s.bw = _prefs.bw;
  s.sf = _prefs.sf;
  s.cr = _prefs.cr;
  s.tx_power = _prefs.tx_power_dbm;
  s.airtime_factor = _prefs.airtime_factor;
  s.rx_delay = _prefs.rx_delay_base;
  s.rx_gain = _prefs.rx_boosted_gain;
  s.fem_rx_gain = board.isLoRaFemLnaEnabled();
  s.rx_ps_enabled = _prefs.rx_powersaving_enabled;
  s.rx_ps_level = _prefs.rx_ps_level;
  s.rx_ps_preamble = _prefs.rx_ps_preamble;
  s.rx_ps_rx_us = _prefs.rx_ps_rx_us;
  s.rx_ps_sleep_us = _prefs.rx_ps_sleep_us;
  s.power_saving = _prefs.powersaving_enabled;
  s.repeat = _prefs.client_repeat != 0;
  s.capabilities = WebConfigServer::CAP_LOCATION | WebConfigServer::CAP_AIRTIME
      | WebConfigServer::CAP_RX_DELAY | WebConfigServer::CAP_POWER_SAVING;
#ifdef BLE_PIN_CODE
  s.capabilities |= WebConfigServer::CAP_BLUETOOTH_NAME;
#endif
#if defined(ESP32) && defined(WIFI_SSID)
  s.capabilities |= WebConfigServer::CAP_WIFI_POWER_SAVE;
#endif
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  s.capabilities |= WebConfigServer::CAP_ESPNOW_CHANNEL;
#endif
  if (radio_driver.supportsRxBoostedGainMode()) {
    s.capabilities |= WebConfigServer::CAP_RX_GAIN;
  }
  if (board.canControlLoRaFemLna()) {
    s.capabilities |= WebConfigServer::CAP_FEM_RX_GAIN;
  }
  if (radio_driver.supportsRxPowerSaving()) {
    s.capabilities |= WebConfigServer::CAP_RX_POWER_SAVING;
  }
}

bool MyMesh::startWebConfig(bool force_ap, char* reply) {
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
  if (!isCompanionWiFiEnabled()) {
    strcpy(reply,
           "Err: WebUI unavailable while Bluetooth transport is active; select WiFi and reboot");
    return false;
  }
#endif
  if (!_webconfig) {
    strcpy(reply, "Err: WebUI unavailable (not enough memory)");
    return false;
  }
  if (_webconfig->isRunning() || _webconfig->isStopping()) {
    if (force_ap && !_webconfig->isStopping()
        && _webconfig->mode() == WebConfigServer::MODE_LAN) {
#ifdef WITH_MQTT_BRIDGE
      if (_mqtt_started && _mqtt_bridge) {
        _mqtt_bridge->end();
        _mqtt_started = false;
      }
#endif
      return _webconfig->startSetupMode(reply);
    }
    strcpy(reply, _webconfig->isStopping() ? "Err: WebUI still stopping"
                                           : "Err: WebUI already running");
    return false;
  }
  if (force_ap) {
#ifdef WITH_MQTT_BRIDGE
    if (_mqtt_started && _mqtt_bridge) {
      _mqtt_bridge->end();
      _mqtt_started = false;
    }
#endif
    return _webconfig->startSetupMode(reply);
  }
  return _webconfig->startAutoMode(reply);
}

void MyMesh::stopWebConfig() {
  if (_webconfig && _webconfig->isRunning()) _webconfig->requestStop();
}

void MyMesh::serviceWebConfig() {
  if (_webconfig) _webconfig->tick(millis());
}

bool MyMesh::isWebConfigActiveOrStopping() const {
  return _webconfig && (_webconfig->isRunning() || _webconfig->isStopping());
}

bool MyMesh::isWebConfigSetupActive() const {
  return _webconfig && _webconfig->mode() == WebConfigServer::MODE_SETUP
      && !_webconfig->isStopping();
}

bool MyMesh::isWebConfigWiFiRecoveryActive() const {
  return _webconfig && _webconfig->isSavedWiFiRecoveryActive();
}

void MyMesh::rebootNow() {
  board.reboot();
}

void MyMesh::onConfigBatchStart() {
  _wc_mqtt_dirty = false;
}

void MyMesh::onConfigBatchEnd() {
#if defined(ESP32) && defined(WIFI_SSID)
  // The WebConfig WiFi form writes the canonical mesh-wifi namespace itself.
  // Reload it here so the Companion runtime and MQTT reconnect policy cannot
  // retain a stale modem-sleep mode.
  reloadCompanionWiFiPowerSave();
  syncWiFiPowerSaving();
#endif
#ifdef WITH_MQTT_BRIDGE
  if (_wc_mqtt_dirty) {
    CompanionMqttSetupPortal::saveStoredConfig(_mqtt_prefs);
    if (_mqtt_started && _mqtt_bridge) _mqtt_bridge->end();
    _mqtt_started = false;
    MQTTPrefs verified;
    _mqtt_configured = CompanionMqttSetupPortal::loadStoredConfig(verified);
    if (_mqtt_configured) _mqtt_prefs = verified;
    // The standalone Companion setting remains canonical even if MQTT config
    // verification reloaded an older copy of this field.
    _mqtt_prefs.wifi_power_save = getCompanionWiFiPowerSave();
  }
#endif
  _wc_mqtt_dirty = false;
}

void MyMesh::execCommand(char* cmd, char* reply) {
  reply[0] = 0;
  if (handleCadCommand(cmd, reply, 160)) return;
  if (cmd && (strcmp(cmd, "get bluetooth.name") == 0
              || strcmp(cmd, "get ble.name") == 0)) {
    formatBluetoothNameStatus(reply, 160);
    return;
  }
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  if (cmd && strcmp(cmd, "get espnow.channel") == 0) {
    formatEspNowChannel(reply, 160);
    return;
  }
#endif
#if defined(ESP32) && defined(WIFI_SSID)
  if (cmd && strcmp(cmd, "get wifi.powersave") == 0) {
    formatWiFiPowerSaving(reply, 160);
    return;
  }
#endif
  if (!cmd || strncmp(cmd, "set ", 4) != 0) {
    strcpy(reply, "Error: unsupported command");
    return;
  }
  char* key = cmd + 4;
  char* split = strchr(key, ' ');
  if (!split) {
    strcpy(reply, "Error: missing value");
    return;
  }
  *split = 0;
  const char* value = split + 1;

  if (strcmp(key, "name") == 0) {
    if (!value[0] || !wcCopyValue(_prefs.node_name, sizeof(_prefs.node_name), value)) {
      strcpy(reply, "Error: name must be 1-31 characters");
    } else {
      savePrefs();
      strcpy(reply, "OK");
    }
    return;
  }
  if (strcmp(key, "bluetooth.name") == 0) {
    applyAndSaveBluetoothName(value, reply, 160);
    return;
  }
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  if (strcmp(key, "espnow.channel") == 0) {
    applyAndSaveEspNowChannel(value, reply, 160);
    return;
  }
#endif
  if (strcmp(key, "lat") == 0 || strcmp(key, "lon") == 0) {
    double parsed;
    const bool latitude = key[1] == 'a';
    if (!wcParseDouble(value, latitude ? -90.0 : -180.0,
                       latitude ? 90.0 : 180.0, parsed)) {
      strcpy(reply, latitude ? "Error: latitude must be -90 to 90"
                             : "Error: longitude must be -180 to 180");
    } else {
      if (latitude) sensors.node_lat = parsed; else sensors.node_lon = parsed;
      savePrefs();
      strcpy(reply, "OK");
    }
    return;
  }
  if (strcmp(key, "radio") == 0) {
    float freq, bw;
    uint8_t sf, cr;
    if (!mesh::cli::parseRadioTupleStrict(value, freq, bw, sf, cr)
        || !isfinite(freq) || !isfinite(bw) || freq < 150.0f || freq > 2500.0f
        || bw < 7.0f || bw > 500.0f || sf < 5 || sf > 12 || cr < 5 || cr > 8) {
      strcpy(reply, "Error: radio must be freq,bw,sf,cr");
    } else {
      _prefs.freq = freq;
      _prefs.bw = bw;
      _prefs.sf = sf;
      _prefs.cr = cr;
      recalcRxPowerSavingFromLevel(_prefs.rx_ps_level, _prefs.sf, _prefs.bw,
                                   _prefs.rx_ps_preamble, &_prefs.rx_ps_rx_us,
                                   &_prefs.rx_ps_sleep_us);
      savePrefs();
      strcpy(reply, "OK - reboot required");
    }
    return;
  }
  if (strcmp(key, "tx") == 0) {
    long parsed;
    if (!wcParseLong(value, -9, MAX_LORA_TX_POWER, parsed)) {
      snprintf(reply, 160, "Error: TX power must be -9 to %d", MAX_LORA_TX_POWER);
    } else {
      _prefs.tx_power_dbm = static_cast<int8_t>(parsed);
      if (_radio_available) radio_driver.setTxPower(_prefs.tx_power_dbm);
      savePrefs();
      strcpy(reply, "OK");
    }
    return;
  }
  if (strcmp(key, "af") == 0 || strcmp(key, "rxdelay") == 0) {
    double parsed;
    const bool is_af = strcmp(key, "af") == 0;
    if (!wcParseDouble(value, 0.0, is_af ? 9.0 : 20.0, parsed)) {
      strcpy(reply, is_af ? "Error: airtime factor must be 0-9"
                          : "Error: RX delay must be 0-20");
    } else {
      if (is_af) _prefs.airtime_factor = parsed; else _prefs.rx_delay_base = parsed;
      savePrefs();
      strcpy(reply, "OK");
    }
    return;
  }
  if (strcmp(key, "radio.rxgain") == 0 || strcmp(key, "repeat") == 0) {
    bool enabled;
    if (!wcParseBool(value, enabled)) {
      strcpy(reply, "Error: must be on or off");
    } else {
      if (strcmp(key, "radio.rxgain") == 0) {
        if (!radio_driver.supportsRxBoostedGainMode()) {
          strcpy(reply, "Error: RX boosted gain unsupported");
          return;
        }
        if (!applyAndSaveRxBoostedGain(enabled)) {
          strcpy(reply, "Error: radio busy; retry");
          return;
        }
      } else {
        _prefs.client_repeat = enabled;
        savePrefs();
      }
      strcpy(reply, "OK");
    }
    return;
  }
  if (strcmp(key, "radio.rxps") == 0) {
    applyAndSaveRxPowerSaving(value, reply);
    return;
  }
  if (strcmp(key, "powersaving") == 0) {
    applyAndSavePowerSaving(value, reply);
    return;
  }
#if defined(ESP32) && defined(WIFI_SSID)
  if (strcmp(key, "wifi.powersave") == 0) {
    applyAndSaveWiFiPowerSaving(value, reply, 160);
    return;
  }
#endif
  if (strcmp(key, "radio.fem.rxgain") == 0) {
    bool enabled;
    if (!wcParseBool(value, enabled)) {
      strcpy(reply, "Error: must be on or off");
    } else if (!board.canControlLoRaFemLna()) {
      strcpy(reply, "Error: unsupported");
    } else if (!applyAndSaveFemRxGain(enabled)) {
      strcpy(reply, "Error: failed to apply FEM RX gain");
    } else {
      strcpy(reply, "OK");
    }
    return;
  }
  if (strcmp(key, "radio.fem.txgain") == 0) {
    bool enabled;
    if (!wcParseBool(value, enabled)) {
      strcpy(reply, "Error: must be on or off");
    } else if (!board.canControlLoRaFemPaGain()) {
      strcpy(reply, "Error: unsupported");
    } else if (!applyAndSaveFemTxGain(enabled)) {
      strcpy(reply, "Error: failed to apply FEM TX gain");
    } else {
      strcpy(reply, "OK");
    }
    return;
  }

#ifdef WITH_MQTT_BRIDGE
  bool changed = false;
  if (strcmp(key, "mqtt.origin") == 0) {
    changed = wcCopyValue(_mqtt_prefs.mqtt_origin, sizeof(_mqtt_prefs.mqtt_origin), value);
  } else if (strcmp(key, "mqtt.iata") == 0) {
    changed = wcCopyValue(_mqtt_prefs.mqtt_iata, sizeof(_mqtt_prefs.mqtt_iata), value);
    if (changed) {
      for (char* p = _mqtt_prefs.mqtt_iata; *p; p++) *p = toupper(static_cast<unsigned char>(*p));
    }
  } else if (strcmp(key, "mqtt.status") == 0 || strcmp(key, "mqtt.packets") == 0
             || strcmp(key, "mqtt.raw") == 0 || strcmp(key, "mqtt.rx") == 0
             || strcmp(key, "snmp") == 0) {
    bool enabled;
    if (!wcParseBool(value, enabled)) {
      strcpy(reply, "Error: must be on or off");
      return;
    }
    if (strcmp(key, "mqtt.status") == 0) _mqtt_prefs.mqtt_status_enabled = enabled;
    else if (strcmp(key, "mqtt.packets") == 0) _mqtt_prefs.mqtt_packets_enabled = enabled;
    else if (strcmp(key, "mqtt.raw") == 0) _mqtt_prefs.mqtt_raw_enabled = enabled;
    else if (strcmp(key, "mqtt.rx") == 0) _mqtt_prefs.mqtt_rx_enabled = enabled;
    else _mqtt_prefs.snmp_enabled = enabled;
    changed = true;
  } else if (strcmp(key, "mqtt.tx") == 0) {
    if (strcmp(value, "off") == 0) _mqtt_prefs.mqtt_tx_enabled = 0;
    else if (strcmp(value, "on") == 0) _mqtt_prefs.mqtt_tx_enabled = 1;
    else if (strcmp(value, "advert") == 0) _mqtt_prefs.mqtt_tx_enabled = 2;
    else {
      strcpy(reply, "Error: MQTT TX must be off, on, or advert");
      return;
    }
    changed = true;
  } else if (strcmp(key, "mqtt.interval") == 0) {
    long minutes;
    if (!wcParseLong(value, 1, 60, minutes)) {
      strcpy(reply, "Error: interval must be 1-60 minutes");
      return;
    }
    _mqtt_prefs.mqtt_status_interval = static_cast<uint32_t>(minutes) * 60000UL;
    changed = true;
  } else if (strcmp(key, "mqtt.ntp") == 0) {
    if (strcmp(value, "none") == 0) {
      _mqtt_prefs.mqtt_ntp_server[0] = 0;
      changed = true;
    } else if (wcValidNtpHost(value)) {
      changed = wcCopyValue(_mqtt_prefs.mqtt_ntp_server,
                            sizeof(_mqtt_prefs.mqtt_ntp_server), value);
    }
  } else if (strcmp(key, "mqtt.owner") == 0) {
    if (!value[0]) {
      _mqtt_prefs.mqtt_owner_public_key[0] = 0;
      changed = true;
    } else if (wcValidHexKey(value)) {
      changed = wcCopyValue(_mqtt_prefs.mqtt_owner_public_key,
                            sizeof(_mqtt_prefs.mqtt_owner_public_key), value);
    }
  } else if (strcmp(key, "mqtt.email") == 0) {
    changed = wcCopyValue(_mqtt_prefs.mqtt_email, sizeof(_mqtt_prefs.mqtt_email), value);
  } else if (strcmp(key, "timezone") == 0) {
    changed = wcCopyValue(_mqtt_prefs.timezone_string, sizeof(_mqtt_prefs.timezone_string), value);
  } else if (strcmp(key, "timezone.offset") == 0) {
    long offset;
    if (!wcParseLong(value, -12, 14, offset)) {
      strcpy(reply, "Error: timezone offset must be -12 to 14");
      return;
    }
    _mqtt_prefs.timezone_offset = static_cast<int8_t>(offset);
    changed = true;
  } else if (strcmp(key, "snmp.community") == 0) {
    changed = wcCopyValue(_mqtt_prefs.snmp_community,
                          sizeof(_mqtt_prefs.snmp_community), value);
  } else if (strncmp(key, "mqtt", 4) == 0 && key[4] >= '1'
             && key[4] <= ('0' + MAX_MQTT_SLOTS) && key[5] == '.') {
    int slot = key[4] - '1';
    const char* field = key + 6;
    if (strcmp(field, "preset") == 0) {
      const bool valid = findMQTTPreset(value) != nullptr
                      || strcmp(value, MQTT_PRESET_CUSTOM) == 0
                      || strcmp(value, MQTT_PRESET_NONE) == 0;
      if (!valid) {
        strcpy(reply, "Error: unknown MQTT preset");
        return;
      }
      if (findMQTTPreset(value)) {
        for (int i = 0; i < MAX_MQTT_SLOTS; i++) {
          if (i != slot && strcmp(_mqtt_prefs.mqtt_slot_preset[i], value) == 0) {
            snprintf(reply, 160, "Error: preset already assigned to slot %d", i + 1);
            return;
          }
        }
      }
      changed = wcCopyValue(_mqtt_prefs.mqtt_slot_preset[slot],
                            sizeof(_mqtt_prefs.mqtt_slot_preset[slot]), value);
    } else if (strcmp(field, "server") == 0) {
      changed = wcCopyValue(_mqtt_prefs.mqtt_slot_host[slot],
                            sizeof(_mqtt_prefs.mqtt_slot_host[slot]), value);
    } else if (strcmp(field, "port") == 0) {
      long port;
      if (!wcParseLong(value, 1, 65535, port)) {
        strcpy(reply, "Error: port must be 1-65535");
        return;
      }
      _mqtt_prefs.mqtt_slot_port[slot] = static_cast<uint16_t>(port);
      changed = true;
    } else if (strcmp(field, "username") == 0) {
      changed = wcCopyValue(_mqtt_prefs.mqtt_slot_username[slot],
                            sizeof(_mqtt_prefs.mqtt_slot_username[slot]), value);
    } else if (strcmp(field, "password") == 0) {
      changed = wcCopyValue(_mqtt_prefs.mqtt_slot_password[slot],
                            sizeof(_mqtt_prefs.mqtt_slot_password[slot]), value);
    } else if (strcmp(field, "token") == 0) {
      changed = wcCopyValue(_mqtt_prefs.mqtt_slot_token[slot],
                            sizeof(_mqtt_prefs.mqtt_slot_token[slot]), value);
    } else if (strcmp(field, "topic") == 0) {
      if (strcmp(_mqtt_prefs.mqtt_slot_preset[slot], MQTT_PRESET_CUSTOM) != 0) {
        strcpy(reply, "Error: topic only applies to custom slots");
        return;
      }
      changed = wcCopyValue(_mqtt_prefs.mqtt_slot_topic[slot],
                            sizeof(_mqtt_prefs.mqtt_slot_topic[slot]), value);
    } else if (strcmp(field, "audience") == 0) {
      changed = wcCopyValue(_mqtt_prefs.mqtt_slot_audience[slot],
                            sizeof(_mqtt_prefs.mqtt_slot_audience[slot]), value);
    }
  }

  if (changed) {
    _wc_mqtt_dirty = true;
    strcpy(reply, "OK");
  } else if (reply[0] == 0) {
    strcpy(reply, "Error: invalid or unsupported value");
  }
#else
  strcpy(reply, "Error: unsupported setting for this companion");
#endif
}

void MyMesh::buildStatsJson(char* buf, size_t buf_size) {
  char ip[20] = "";
  int wifi_rssi = 0;
  if (WiFi.status() == WL_CONNECTED) {
    strncpy(ip, WiFi.localIP().toString().c_str(), sizeof(ip) - 1);
    wifi_rssi = WiFi.RSSI();
  } else if (_webconfig && _webconfig->mode() == WebConfigServer::MODE_SETUP) {
    strncpy(ip, WiFi.softAPIP().toString().c_str(), sizeof(ip) - 1);
  }
  int pos = snprintf(buf, buf_size,
      "{\"uptime_s\":%lu,\"batt_mv\":%u,"
      "\"heap_free\":%lu,\"heap_min\":%lu,\"heap_max_alloc\":%lu,"
      "\"noise\":%d,\"rssi\":%d,\"snr\":%.1f,"
      "\"airtime_s\":%lu,\"rx_airtime_s\":%lu,"
      "\"recv\":%lu,\"sent\":%lu,\"rx_err\":%lu,"
      "\"sent_flood\":%lu,\"sent_direct\":%lu,\"recv_flood\":%lu,\"recv_direct\":%lu,"
      "\"tx_queue\":%d,\"wifi_rssi\":%d,\"ip\":\"%s\",\"mqtt_queue\":%d,\"slots\":[",
      (unsigned long)(millis() / 1000), (unsigned)board.getBattMilliVolts(),
      (unsigned long)ESP.getFreeHeap(), (unsigned long)ESP.getMinFreeHeap(),
      (unsigned long)ESP.getMaxAllocHeap(),
      (int)_radio->getNoiseFloor(), (int)radio_driver.getLastRSSI(),
      radio_driver.getLastSNR(),
      (unsigned long)(getTotalAirTime() / 1000), (unsigned long)(getReceiveAirTime() / 1000),
      (unsigned long)radio_driver.getPacketsRecv(), (unsigned long)radio_driver.getPacketsSent(),
      (unsigned long)radio_driver.getPacketsRecvErrors(),
      (unsigned long)getNumSentFlood(), (unsigned long)getNumSentDirect(),
      (unsigned long)getNumRecvFlood(), (unsigned long)getNumRecvDirect(),
      (int)_mgr->getOutboundCount(0xFFFFFFFF), wifi_rssi, ip,
#ifdef WITH_MQTT_BRIDGE
      _mqtt_bridge ? _mqtt_bridge->getQueueSize() : 0);
#else
      0);
#endif
  if (pos < 0 || pos >= static_cast<int>(buf_size) - 3) return;

#ifdef WITH_MQTT_BRIDGE
  bool first = true;
  for (int i = 0; i < RUNTIME_MQTT_SLOTS; i++) {
    MQTTBridge::SlotStatusSnapshot status;
    if (!MQTTBridge::getSlotStatusSnapshot(i, &status)) continue;
    int written = snprintf(buf + pos, buf_size - pos,
        "%s{\"n\":%d,\"name\":\"%s\",\"state\":\"%s\"}",
        first ? "" : ",", i + 1, status.name, status.state);
    if (written < 0 || written >= static_cast<int>(buf_size - pos)) break;
    pos += written;
    first = false;
  }
#endif
  snprintf(buf + pos, buf_size - pos, "]}");
}
#endif

struct FreqRange {
  uint32_t lower_freq, upper_freq;
};

static FreqRange repeat_freq_ranges[] = {
  #ifdef ALLOWED_REPEAT_FREQ_RANGE
  ALLOWED_REPEAT_FREQ_RANGE
  #else
  { 433000, 433000 },
  { 869495, 869495 },
  { 918000, 918000 }
  #endif
};

bool MyMesh::isValidClientRepeatFreq(uint32_t f) const {
  for (int i = 0; i < sizeof(repeat_freq_ranges)/sizeof(repeat_freq_ranges[0]); i++) {
    auto r = &repeat_freq_ranges[i];
    if (f >= r->lower_freq && f <= r->upper_freq) return true;
  }
  return false;
}

void MyMesh::startInterface(BaseSerialInterface &serial) {
  _serial = &serial;
  serial.enable();
}

void MyMesh::cancelSerialResponseStream() {
  stopContactsIterator();
}

void MyMesh::handleCmdFrame(size_t len) {
  if (len == 0) {
    writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    return;
  }

  if (cmd_frame[0] == CMD_DEVICE_QUERY && len >= 2) { // sent when app establishes connection
    app_target_ver = cmd_frame[1];                    // which version of protocol does app understand

    int i = 0;
    out_frame[i++] = RESP_CODE_DEVICE_INFO;
    out_frame[i++] = FIRMWARE_VER_CODE;
    out_frame[i++] = MAX_CONTACTS / 2;   // v3+
    out_frame[i++] = MAX_GROUP_CHANNELS; // v3+
    memcpy(&out_frame[i], &_active_ble_pin, 4);
    i += 4;
    memset(&out_frame[i], 0, 12);
    strcpy((char *)&out_frame[i], FIRMWARE_BUILD_DATE);
    i += 12;
    StrHelper::strzcpy((char *)&out_frame[i], board.getManufacturerName(), 40);
    i += 40;
    StrHelper::strzcpy((char *)&out_frame[i], FIRMWARE_VERSION, 20);
    i += 20;
    out_frame[i++] = _prefs.isRepeatEn() ? 1 : 0;   // v9+
    out_frame[i++] = _prefs.path_hash_mode;  // v10+
    _serial->writeFrame(out_frame, i);
  } else if (cmd_frame[0] == CMD_APP_START &&
             len >= 8) { // sent when app establishes connection, respond with node ID
    //  cmd_frame[1..7]  reserved future
    char *app_name = (char *)&cmd_frame[8];
    cmd_frame[len] = 0; // make app_name null terminated
    MESH_DEBUG_PRINTLN("App %s connected", app_name);

    stopContactsIterator(); // stop any left-over ContactsIterator
    cancelPendingRadioParamApply();
    int i = 0;
    out_frame[i++] = RESP_CODE_SELF_INFO;
    out_frame[i++] = ADV_TYPE_CHAT; // what this node Advert identifies as (maybe node's pronouns too?? :-)
    out_frame[i++] = _prefs.tx_power_dbm;
    out_frame[i++] = MAX_LORA_TX_POWER;
    memcpy(&out_frame[i], self_id.pub_key, PUB_KEY_SIZE);
    i += PUB_KEY_SIZE;

    int32_t lat, lon;
    lat = (sensors.node_lat * 1000000.0);
    lon = (sensors.node_lon * 1000000.0);
    memcpy(&out_frame[i], &lat, 4);
    i += 4;
    memcpy(&out_frame[i], &lon, 4);
    i += 4;
    out_frame[i++] = _prefs.multi_acks; // new v7+
    out_frame[i++] = _prefs.advert_loc_policy;
    out_frame[i++] = (_prefs.telemetry_mode_env << 4) | (_prefs.telemetry_mode_loc << 2) |
                     (_prefs.telemetry_mode_base); // v5+
    out_frame[i++] = _prefs.manual_add_contacts;

    uint32_t freq = _prefs.freq * 1000;
    memcpy(&out_frame[i], &freq, 4);
    i += 4;
    uint32_t bw = _prefs.bw * 1000;
    memcpy(&out_frame[i], &bw, 4);
    i += 4;
    out_frame[i++] = _prefs.sf;
    out_frame[i++] = _prefs.cr;

    int tlen = strlen(_prefs.node_name); // revisit: UTF_8 ??
    memcpy(&out_frame[i], _prefs.node_name, tlen);
    i += tlen;
    _serial->writeFrame(out_frame, i);
  } else if (mesh::companion::isRunCliFrame(cmd_frame[0], len)) { // V14+
    int i = 1;
    char *text = (char *)&cmd_frame[i];
    int tlen = len - i;
    if (memchr(text, 0, tlen) != NULL) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else {
      text[tlen] = 0; // ensure null

      reply_buf[0] = 0;
      if (!handleCommand(text, 0, reply_buf)) {
        strcat(reply_buf, "Unknown command");   // reply_buf may have cmd prefix from 'text'
      }
      out_frame[0] = RESP_CODE_CLI_REPLY;
      int rlen = strlen(reply_buf);
      memcpy(&out_frame[1], reply_buf, rlen);
      _serial->writeFrame(out_frame, 1 + rlen);
    }
  } else if (cmd_frame[0] == CMD_SEND_TXT_MSG && len >= 14) {
    int i = 1;
    uint8_t txt_type = cmd_frame[i++];
    uint8_t attempt = cmd_frame[i++];
    uint32_t msg_timestamp;
    memcpy(&msg_timestamp, &cmd_frame[i], 4);
    i += 4;
    uint8_t *pub_key_prefix = &cmd_frame[i];
    i += 6;
    ContactInfo *recipient = lookupContactByPubKey(pub_key_prefix, 6);
    if (recipient && (txt_type == TXT_TYPE_PLAIN || txt_type == TXT_TYPE_CLI_DATA || txt_type == TXT_TYPE_CLI_COMMAND)) {
      char *text = (char *)&cmd_frame[i];
      int tlen = len - i;
      uint32_t est_timeout;
      text[tlen] = 0; // ensure null

      uint8_t text_fingerprint[MAX_HASH_SIZE] = { 0 };
      uint8_t packet_retry_key[MAX_HASH_SIZE] = { 0 };
      AckTableEntry* replacement_entry = NULL;
      if (txt_type == TXT_TYPE_PLAIN) {
        mesh::Utils::sha256(text_fingerprint, sizeof(text_fingerprint),
                            recipient->id.pub_key, PUB_KEY_SIZE,
                            (const uint8_t*)text, strlen(text));
      }

      int result;
      uint32_t expected_ack;
      if (txt_type == TXT_TYPE_CLI_DATA || txt_type == TXT_TYPE_CLI_COMMAND) {
        const uint32_t logical_request_id = msg_timestamp;
        msg_timestamp = getRTCClock()->getCurrentTimeUnique(); // Use node's RTC instead of app timestamp to avoid tripping replay protection
        result = sendCommandData(*recipient, msg_timestamp, attempt, txt_type,
                                 text, est_timeout, logical_request_id);
        expected_ack = 0; // no Ack expected
      } else {
        const uint32_t app_timestamp = msg_timestamp;
        const bool is_room_post = recipient->type == ADV_TYPE_ROOM;
        if (is_room_post
            && !room_message_timestamps.find(text_fingerprint, app_timestamp,
                                             &msg_timestamp)) {
          // Room login/control packets already use the node's monotonic clock.
          // Give a new post that same clock source, then preserve the mapping
          // so application retries keep one logical server-side timestamp.
          msg_timestamp = getRTCClock()->getCurrentTimeUnique();
        }
        replacement_entry = findPendingTextMessage(text_fingerprint, msg_timestamp);
        result = sendMessage(*recipient, msg_timestamp, attempt, text, expected_ack, est_timeout,
                             packet_retry_key, NULL, text_fingerprint);
        if (result != MSG_SEND_FAILED && is_room_post) {
          room_message_timestamps.remember(text_fingerprint, app_timestamp,
                                           msg_timestamp);
        }
      }
      if (result == MSG_SEND_FAILED) {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      } else {
        if (replacement_entry != NULL) {
          // The newest successfully-queued submission wins. Keep the older
          // entry intact if composition, validation, or queueing failed.
          clearExpectedAck(*replacement_entry, false);
        }
        if (expected_ack) {
          AckTableEntry& entry = replacement_entry != NULL
              ? *replacement_entry
              : expected_ack_table[next_ack_idx];
          // Reusing this circular slot changes only ACK bookkeeping. Retry
          // ownership lives in Mesh and ends only on an ACK, retry completion,
          // or a successfully queued semantic replacement.
          clearExpectedAck(entry, false);
          entry.msg_sent = _ms->getMillis(); // add to circular table
          entry.expires_at = futureMillis(est_timeout);
          entry.ack = expected_ack;
          entry.message_timestamp = msg_timestamp;
          entry.contact = recipient;
          memcpy(entry.text_fingerprint, text_fingerprint, sizeof(entry.text_fingerprint));
          memcpy(entry.retry_key, packet_retry_key, sizeof(entry.retry_key));
          if (replacement_entry == NULL) {
            next_ack_idx = (next_ack_idx + 1) % EXPECTED_ACK_TABLE_SIZE;
          }
        }
        if (replacement_entry != NULL || expected_ack != 0) {
          expireExpectedAcks();
        }

        out_frame[0] = RESP_CODE_SENT;
        out_frame[1] = (result == MSG_SEND_SENT_FLOOD) ? 1 : 0;
        memcpy(&out_frame[2], &expected_ack, 4);
        memcpy(&out_frame[6], &est_timeout, 4);
        _serial->writeFrame(out_frame, 10);
      }
    } else {
      writeErrFrame(recipient == NULL
                        ? ERR_CODE_NOT_FOUND
                        : ERR_CODE_UNSUPPORTED_CMD); // unknown recipient, or unsupported TXT_TYPE_*
    }
  } else if (cmd_frame[0] == CMD_SEND_CHANNEL_TXT_MSG) { // send GroupChannel text msg
    if (len < 7) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      return;
    }
    int i = 1;
    uint8_t txt_type = cmd_frame[i++]; // should be TXT_TYPE_PLAIN
    uint8_t channel_idx = cmd_frame[i++];
    uint32_t msg_timestamp;
    memcpy(&msg_timestamp, &cmd_frame[i], 4);
    i += 4;
    const char *text = (char *)&cmd_frame[i];

    if (txt_type != TXT_TYPE_PLAIN) {
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
    } else {
      ChannelDetails channel;
      bool success = getChannel(channel_idx, channel);
      if (success && sendGroupMessage(msg_timestamp, channel.channel, _prefs.node_name, text, len - i)) {
        writeOKFrame();
      } else {
        writeErrFrame(ERR_CODE_NOT_FOUND); // bad channel_idx
      }
    }
  } else if (cmd_frame[0] == CMD_SEND_CHANNEL_DATA) { // send GroupChannel datagram
    if (len < 3) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      return;
    }
    int i = 1;
    uint8_t channel_idx = cmd_frame[i++];
    uint8_t path_len = cmd_frame[i++];

    // validate path len, allowing 0xFF for flood
    if (!mesh::Packet::isValidPathLen(path_len) && path_len != OUT_PATH_UNKNOWN) {
      MESH_DEBUG_PRINTLN("CMD_SEND_CHANNEL_DATA invalid path size: %d", path_len);
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      return;
    }

    // parse provided path if not flood
    uint8_t path[MAX_PATH_SIZE];
    size_t path_bytes = 0;
    if (path_len != OUT_PATH_UNKNOWN) {
      path_bytes = (size_t)(path_len & 63) * (size_t)((path_len >> 6) + 1);
    }
    if ((size_t)i + path_bytes + 2 > len) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      return;
    }
    if (path_len != OUT_PATH_UNKNOWN) {
      i += mesh::Packet::writePath(path, &cmd_frame[i], path_len);
    }

    uint16_t data_type = ((uint16_t)cmd_frame[i]) | (((uint16_t)cmd_frame[i + 1]) << 8);
    i += 2;
    const uint8_t *payload = &cmd_frame[i];
    int payload_len = (len > (size_t)i) ? (int)(len - i) : 0;

    ChannelDetails channel;
    if (!getChannel(channel_idx, channel)) {
      writeErrFrame(ERR_CODE_NOT_FOUND); // bad channel_idx
    } else if (data_type == DATA_TYPE_RESERVED) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else if (payload_len > MAX_CHANNEL_DATA_LENGTH) {
      MESH_DEBUG_PRINTLN("CMD_SEND_CHANNEL_DATA payload too long: %d > %d", payload_len, MAX_CHANNEL_DATA_LENGTH);
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else if (sendGroupData(channel.channel, path, path_len, data_type, payload, payload_len)) {
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_TABLE_FULL);
    }
  } else if (cmd_frame[0] == CMD_GET_CONTACTS) { // get Contact list
    if (_iter_started) {
      writeErrFrame(ERR_CODE_BAD_STATE); // iterator is currently busy
    } else {
      if (len >= 5) { // has optional 'since' param
        memcpy(&_iter_filter_since, &cmd_frame[1], 4);
      } else {
        _iter_filter_since = 0;
      }

      // CONTACTS_START, every CONTACT, and END_OF_CONTACTS are one response
      // transaction. Keep them on the transport which requested the list.
      _serial->lockReplyRoute();
      _iter = startContactsIterator();
      _iter_started = true;
      _most_recent_lastmod = 0;

      uint8_t reply[5];
      reply[0] = RESP_CODE_CONTACTS_START;
      uint32_t count = getNumContacts(); // total, NOT filtered count
      memcpy(&reply[1], &count, 4);
      _serial->writeFrame(reply, 5);
    }
  } else if (cmd_frame[0] == CMD_SET_ADVERT_NAME && len >= 2) {
    int nlen = len - 1;
    if (nlen > sizeof(_prefs.node_name) - 1) nlen = sizeof(_prefs.node_name) - 1; // max len
    memcpy(_prefs.node_name, &cmd_frame[1], nlen);
    _prefs.node_name[nlen] = 0; // null terminator
    savePrefs();
    writeOKFrame();
  } else if (cmd_frame[0] == CMD_SET_ADVERT_LATLON && len >= 9) {
    int32_t lat, lon, alt = 0;
    memcpy(&lat, &cmd_frame[1], 4);
    memcpy(&lon, &cmd_frame[5], 4);
    if (len >= 13) {
      memcpy(&alt, &cmd_frame[9], 4); // for FUTURE support
    }
    if (lat <= 90 * 1E6 && lat >= -90 * 1E6 && lon <= 180 * 1E6 && lon >= -180 * 1E6) {
      sensors.node_lat = ((double)lat) / 1000000.0;
      sensors.node_lon = ((double)lon) / 1000000.0;
      savePrefs();
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG); // invalid geo coordinate
    }
  } else if (cmd_frame[0] == CMD_GET_DEVICE_TIME) {
    uint8_t reply[5];
    reply[0] = RESP_CODE_CURR_TIME;
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply[1], &now, 4);
    _serial->writeFrame(reply, 5);
  } else if (cmd_frame[0] == CMD_SET_DEVICE_TIME && len >= 5) {
    uint32_t secs;
    memcpy(&secs, &cmd_frame[1], 4);
    uint32_t curr = getRTCClock()->getCurrentTime();
    if (secs >= curr) {
      getRTCClock()->setCurrentTime(secs);
#ifdef COMPANION_MESH_CLOCK_SYNC
      _clock_sync.onManualClockSet();
#endif
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    }
  } else if (cmd_frame[0] == CMD_SEND_SELF_ADVERT) {
    mesh::Packet* pkt;
    if (_prefs.advert_loc_policy == ADVERT_LOC_NONE) {
      pkt = createSelfAdvert(_prefs.node_name);
    } else {
      pkt = createSelfAdvert(_prefs.node_name, sensors.node_lat, sensors.node_lon);
    }
    if (pkt) {
      if (len >= 2 && cmd_frame[1] == 1) { // optional param (1 = flood, 0 = zero hop)
        unsigned long delay_millis = 0;
        TransportKey default_scope;
        memcpy(&default_scope.key, _prefs.default_scope_key, sizeof(default_scope.key));
        sendFloodScoped(default_scope, pkt, delay_millis);
      } else {
        sendZeroHop(pkt);
      }
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_TABLE_FULL);
    }
  } else if (cmd_frame[0] == CMD_RESET_PATH && len >= 1 + 32) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    if (recipient) {
      recipient->out_path_len = OUT_PATH_UNKNOWN;
      // recipient->lastmod = ??   shouldn't be needed, app already has this version of contact
      scheduleContactWrite(*recipient);
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND); // unknown contact
    }
  } else if (cmd_frame[0] == CMD_ADD_UPDATE_CONTACT && len >= 1 + 32 + 2 + 1) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    uint32_t last_mod = getRTCClock()->getCurrentTime();  // fallback value if not present in cmd_frame
    if (recipient) {
      updateContactFromFrame(*recipient, last_mod, cmd_frame, len);
      recipient->lastmod = last_mod;
      scheduleContactWrite(*recipient);
      updateGpsTelemetryPolicy();
      writeOKFrame();
    } else {
      ContactInfo contact;
      updateContactFromFrame(contact, last_mod, cmd_frame, len);
      contact.lastmod = last_mod;
      contact.sync_since = 0;
      if (addContact(contact)) {
        ContactInfo* added = lookupContactByPubKey(contact.id.pub_key, PUB_KEY_SIZE);
        if (added) scheduleContactWrite(*added);
        updateGpsTelemetryPolicy();
        writeOKFrame();
      } else {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      }
    }
  } else if (cmd_frame[0] == CMD_REMOVE_CONTACT && len >= 1 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    ContactInfo removed;
    if (recipient) removed = *recipient;
    if (recipient && removeContact(*recipient)) {
      scheduleContactWriteAfterRelease(removed);
      _store->deleteBlobByKey(pub_key, PUB_KEY_SIZE);
      updateGpsTelemetryPolicy();
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND); // not found, or unable to remove
    }
  } else if (cmd_frame[0] == CMD_SHARE_CONTACT && len >= 1 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    if (recipient) {
      if (shareContactZeroHop(*recipient)) {
        writeOKFrame();
      } else {
        writeErrFrame(ERR_CODE_TABLE_FULL); // unable to send
      }
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND);
    }
  } else if (cmd_frame[0] == CMD_GET_CONTACT_BY_KEY && len >= 1 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *contact = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    if (contact) {
      writeContactRespFrame(RESP_CODE_CONTACT, *contact);
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND); // not found
    }
  } else if (cmd_frame[0] == CMD_EXPORT_CONTACT) {
    if (len < 1 + PUB_KEY_SIZE) {
      // export SELF
      mesh::Packet* pkt;
      if (_prefs.advert_loc_policy == ADVERT_LOC_NONE) {
        pkt = createSelfAdvert(_prefs.node_name);
      } else {
        pkt = createSelfAdvert(_prefs.node_name, sensors.node_lat, sensors.node_lon);
      }
      if (pkt) {
        pkt->header |= ROUTE_TYPE_FLOOD; // would normally be sent in this mode

        out_frame[0] = RESP_CODE_EXPORT_CONTACT;
        uint8_t out_len = pkt->writeTo(&out_frame[1]);
        releasePacket(pkt); // undo the obtainNewPacket()
        _serial->writeFrame(out_frame, out_len + 1);
      } else {
        writeErrFrame(ERR_CODE_TABLE_FULL); // Error
      }
    } else {
      uint8_t *pub_key = &cmd_frame[1];
      ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
      uint8_t out_len;
      if (recipient && (out_len = exportContact(*recipient, &out_frame[1])) > 0) {
        out_frame[0] = RESP_CODE_EXPORT_CONTACT;
        _serial->writeFrame(out_frame, out_len + 1);
      } else {
        writeErrFrame(ERR_CODE_NOT_FOUND); // not found
      }
    }
  } else if (cmd_frame[0] == CMD_IMPORT_CONTACT && len > 2 + 32 + 64) {
    if (importContact(&cmd_frame[1], len - 1)) {
      updateGpsTelemetryPolicy();
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    }
  } else if (cmd_frame[0] == CMD_SYNC_NEXT_MESSAGE) {
    int out_len;
    if ((out_len = getFromOfflineQueue(out_frame)) > 0) {
      _serial->writeFrame(out_frame, out_len);
#ifdef DISPLAY_CLASS
      if (_ui) _ui->msgRead(offline_queue_len);
#endif
    } else {
      out_frame[0] = RESP_CODE_NO_MORE_MESSAGES;
      _serial->writeFrame(out_frame, 1);
    }
  } else if (cmd_frame[0] == CMD_SET_RADIO_PARAMS) {
    if (len < 11) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      return;
    }
#if COMPANION_FEATURE_TEMP_RADIO
    if (isTempRadioActive() || _temp_radio_set_at != 0
        || _temp_radio_revert_at != 0) {
      writeErrFrame(ERR_CODE_BAD_STATE);
      return;
    }
#endif
    int i = 1;
    uint32_t freq;
    memcpy(&freq, &cmd_frame[i], 4);
    i += 4;
    uint32_t bw;
    memcpy(&bw, &cmd_frame[i], 4);
    i += 4;
    uint8_t sf = cmd_frame[i++];
    uint8_t cr = cmd_frame[i++];
    uint8_t repeat = 0;  // default - false
    if (len > i) {
      repeat = cmd_frame[i++];   // FIRMWARE_VER_CODE  9+
    }

    if (repeat && !isValidClientRepeatFreq(freq)) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else if (freq >= 150000 && freq <= 2500000 && sf >= 5 && sf <= 12 && cr >= 5 && cr <= 8 && bw >= 7000 &&
        bw <= 500000) {
      float new_freq = (float)freq / 1000.0;
      float new_bw = (float)bw / 1000.0;
      if (command_radio_apply_pending) {
        writeErrFrame(ERR_CODE_BAD_STATE);
        return;
      }

      mesh::RadioParamApplyResult result = tryApplyRadioParams(new_freq, new_bw, sf, cr);
      if (result == mesh::RadioParamApplyResult::BUSY) {
        command_radio_apply_pending = true;
        command_radio_freq = new_freq;
        command_radio_bw = new_bw;
        command_radio_sf = sf;
        command_radio_cr = cr;
        command_radio_repeat = repeat;
        command_radio_apply_deadline = futureMillis(COMMAND_RADIO_APPLY_TIMEOUT_MS);
        MESH_DEBUG_PRINTLN("Deferred CMD_SET_RADIO_PARAMS while radio is busy");
        return;
      }
      if (result == mesh::RadioParamApplyResult::FAILED) {
        // Persisted settings remain authoritative after a rejected change or
        // a hardware apply that had to be rolled back.
        saved_radio_apply_pending = true;
        radio_apply_retry_at = 0;
        radio_apply_failures = 0;
        writeErrFrame(ERR_CODE_ILLEGAL_ARG);
        return;
      }

      finishRadioParamApply(new_freq, new_bw, sf, cr, repeat);
    } else {
      MESH_DEBUG_PRINTLN("Error: CMD_SET_RADIO_PARAMS: f=%d, bw=%d, sf=%d, cr=%d", freq, bw, (uint32_t)sf,
                         (uint32_t)cr);
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    }
  } else if (cmd_frame[0] == CMD_SET_RADIO_TX_POWER) {
    if (len < 2) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      return;
    }
    int8_t power = (int8_t)cmd_frame[1];
    if (power < -9 || power > MAX_LORA_TX_POWER) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else {
      _prefs.tx_power_dbm = power;
      savePrefs();
      if (_radio_available) radio_driver.setTxPower(_prefs.tx_power_dbm);
      writeOKFrame();
    }
  } else if (cmd_frame[0] == CMD_SET_TUNING_PARAMS) {
    if (len < 9) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      return;
    }
    int i = 1;
    uint32_t rx, af;
    memcpy(&rx, &cmd_frame[i], 4);
    i += 4;
    memcpy(&af, &cmd_frame[i], 4);
    i += 4;
    _prefs.rx_delay_base = ((float)rx) / 1000.0f;
    _prefs.airtime_factor = ((float)af) / 1000.0f;
    savePrefs();
    writeOKFrame();
  } else if (cmd_frame[0] == CMD_GET_TUNING_PARAMS) {
    uint32_t rx = _prefs.rx_delay_base * 1000, af = _prefs.airtime_factor * 1000;
    int i = 0;
    out_frame[i++] = RESP_CODE_TUNING_PARAMS;
    memcpy(&out_frame[i], &rx, 4); i += 4;
    memcpy(&out_frame[i], &af, 4); i += 4;
    _serial->writeFrame(out_frame, i);
  } else if (cmd_frame[0] == CMD_SET_OTHER_PARAMS) {
    if (len < 2) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      return;
    }
    _prefs.manual_add_contacts = cmd_frame[1];
    if (len >= 3) {
      _prefs.telemetry_mode_base = cmd_frame[2] & 0x03; // v5+
      _prefs.telemetry_mode_loc = (cmd_frame[2] >> 2) & 0x03;
      _prefs.telemetry_mode_env = (cmd_frame[2] >> 4) & 0x03;

      if (len >= 4) {
        _prefs.advert_loc_policy = cmd_frame[3];
        if (len >= 5) {
          _prefs.multi_acks = cmd_frame[4];
        }
      }
    }
    updateGpsTelemetryPolicy();
    savePrefs();
    writeOKFrame();
  } else if (cmd_frame[0] == CMD_SET_PATH_HASH_MODE && len >= 3 && cmd_frame[1] == 0) {
    if (cmd_frame[2] >= 3) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else {
      _prefs.path_hash_mode = cmd_frame[2];
      savePrefs();
      writeOKFrame();
    }
  } else if (cmd_frame[0] == CMD_REBOOT && len >= 7 && memcmp(&cmd_frame[1], "reboot", 6) == 0) {
    // Non-nRF stores use the legacy monolithic file and therefore do not
    // report dirty pages.  The lazy-write timer is still proof that RAM holds
    // newer contact data which must be persisted before rebooting.
    if (dirty_contacts_expiry || _store->hasPendingContactWrites()) {
      if (!_store->flushContactWrites(this, save_filter)) {
        writeErrFrame(ERR_CODE_FILE_IO_ERROR);
        return;
      }
    }
    board.reboot();
  } else if (cmd_frame[0] == CMD_GET_BATT_AND_STORAGE) {
    uint8_t reply[11];
    int i = 0;
    reply[i++] = RESP_CODE_BATT_AND_STORAGE;
    uint16_t battery_millivolts = board.getBattMilliVolts();
    uint32_t used = _store->getStorageUsedKb();
    uint32_t total = _store->getStorageTotalKb();
    memcpy(&reply[i], &battery_millivolts, 2); i += 2;
    memcpy(&reply[i], &used, 4); i += 4;
    memcpy(&reply[i], &total, 4); i += 4;
    _serial->writeFrame(reply, i);
  } else if (cmd_frame[0] == CMD_EXPORT_PRIVATE_KEY) {
#if ENABLE_PRIVATE_KEY_EXPORT
    uint8_t reply[65];
    reply[0] = RESP_CODE_PRIVATE_KEY;
    self_id.writeTo(&reply[1], 64);
    _serial->writeFrame(reply, 65);
#else
    writeDisabledFrame();
#endif
  } else if (cmd_frame[0] == CMD_IMPORT_PRIVATE_KEY && len >= 65) {
#if ENABLE_PRIVATE_KEY_IMPORT
    if (!mesh::LocalIdentity::validatePrivateKey(&cmd_frame[1])) {
        writeErrFrame(ERR_CODE_ILLEGAL_ARG); // invalid key
    } else {
        mesh::LocalIdentity identity;
        identity.readFrom(&cmd_frame[1], 64);
        if (_store->saveMainIdentity(identity)) {
          self_id = identity;
          writeOKFrame();
          // re-load contacts, to invalidate ecdh shared_secrets
          stopContactsIterator();
          resetContacts();
          _store->loadContacts(this);
          updateGpsTelemetryPolicy();
        } else {
          writeErrFrame(ERR_CODE_FILE_IO_ERROR);
        }
    }
#else
    writeDisabledFrame();
#endif
  } else if (cmd_frame[0] == CMD_SEND_RAW_DATA && len >= 6) {
    int i = 1;
    uint8_t path_len = cmd_frame[i++];
    if (path_len >= 0 && mesh::Packet::isValidPathLen(path_len)) {
      uint8_t path[MAX_PATH_SIZE];
      i += mesh::Packet::writePath(path, &cmd_frame[i], path_len);
      if (i + 4 > len) {  // min payload 4 bytes
        writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      } else {
        auto pkt = createRawData(&cmd_frame[i], len - i);
        if (pkt) {
          sendDirect(pkt, path, path_len);
          writeOKFrame();
        } else {
          writeErrFrame(ERR_CODE_TABLE_FULL);
        }
      }
    } else {
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD); // flood, not supported (yet)
    }
  } else if (cmd_frame[0] == CMD_SEND_LOGIN && len >= 1 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    char *password = (char *)&cmd_frame[1 + PUB_KEY_SIZE];
    cmd_frame[len] = 0; // ensure null terminator in password
    if (recipient) {
      uint32_t est_timeout;
      int result = sendLogin(*recipient, password, est_timeout);
      if (result == MSG_SEND_FAILED) {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      } else {
        clearPendingReqs();
        memcpy(&pending_login, recipient->id.pub_key, 4); // match this to onContactResponse()
        out_frame[0] = RESP_CODE_SENT;
        out_frame[1] = (result == MSG_SEND_SENT_FLOOD) ? 1 : 0;
        memcpy(&out_frame[2], &pending_login, 4);
        memcpy(&out_frame[6], &est_timeout, 4);
        _serial->writeFrame(out_frame, 10);
      }
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND); // contact not found
    }
  } else if (cmd_frame[0] == CMD_SEND_ANON_REQ && len > 1 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    ContactInfo anon;
    if (recipient == NULL) { // FIRMWARE_VER_CODE 13+,  allow non-contact requests
      memset(&anon, 0, sizeof(anon));
#if defined(NRF52_PLATFORM)
      anon.storage_slot = mesh::storage::CONTACT_SLOT_NONE;
#endif
      memcpy(anon.id.pub_key, pub_key, PUB_KEY_SIZE);
      anon.out_path_len = 0;   // default to zero-hop direct
      anon.type = ADV_TYPE_NONE;  // unknown
      anon.lastmod = getRTCClock()->getCurrentTime();

      if (addContact(anon)) recipient = &anon;
    }
    uint8_t *data = &cmd_frame[1 + PUB_KEY_SIZE];
    if (recipient) {
      uint32_t tag, est_timeout;
      int result = sendAnonReq(*recipient, data, len - (1 + PUB_KEY_SIZE), tag, est_timeout);
      if (result == MSG_SEND_FAILED) {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      } else {
        clearPendingReqs();
        pending_req = tag; // match this to onContactResponse()
        out_frame[0] = RESP_CODE_SENT;
        out_frame[1] = (result == MSG_SEND_SENT_FLOOD) ? 1 : 0;
        memcpy(&out_frame[2], &tag, 4);
        memcpy(&out_frame[6], &est_timeout, 4);
        _serial->writeFrame(out_frame, 10);
      }
    } else {
      writeErrFrame(ERR_CODE_TABLE_FULL); // contacts full
    }
  } else if (cmd_frame[0] == CMD_SEND_STATUS_REQ && len >= 1 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    if (recipient) {
      uint32_t tag, est_timeout;
      int result = sendRequest(*recipient, REQ_TYPE_GET_STATUS, tag, est_timeout);
      if (result == MSG_SEND_FAILED) {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      } else {
        clearPendingReqs();
        pending_status = tag; // match the reflected tag in onContactResponse()
        out_frame[0] = RESP_CODE_SENT;
        out_frame[1] = (result == MSG_SEND_SENT_FLOOD) ? 1 : 0;
        memcpy(&out_frame[2], &tag, 4);
        memcpy(&out_frame[6], &est_timeout, 4);
        _serial->writeFrame(out_frame, 10);
      }
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND); // contact not found
    }
  } else if (cmd_frame[0] == CMD_SEND_PATH_DISCOVERY_REQ && len >= 2 + PUB_KEY_SIZE && cmd_frame[1] == 0) {
    uint8_t *pub_key = &cmd_frame[2];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    if (recipient) {
      uint32_t tag, est_timeout;
      // 'Path Discovery' is just a special case of flood + Telemetry req
      uint8_t req_data[9];
      req_data[0] = REQ_TYPE_GET_TELEMETRY_DATA;
      req_data[1] = ~(TELEM_PERM_BASE);  // NEW: inverse permissions mask (ie. we only want BASE telemetry)
      memset(&req_data[2], 0, 3);  // reserved
      getRNG()->random(&req_data[5], 4);   // random blob to help make packet-hash unique
      auto save = recipient->out_path_len;    // temporarily force sendRequest() to flood
      recipient->out_path_len = OUT_PATH_UNKNOWN;
      int result = sendRequest(*recipient, req_data, sizeof(req_data), tag, est_timeout);
      recipient->out_path_len = save;
      if (result == MSG_SEND_FAILED) {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      } else {
        clearPendingReqs();
        pending_discovery = tag; // match this in onContactResponse()
        out_frame[0] = RESP_CODE_SENT;
        out_frame[1] = (result == MSG_SEND_SENT_FLOOD) ? 1 : 0;
        memcpy(&out_frame[2], &tag, 4);
        memcpy(&out_frame[6], &est_timeout, 4);
        _serial->writeFrame(out_frame, 10);
      }
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND); // contact not found
    }
  } else if (cmd_frame[0] == CMD_SEND_TELEMETRY_REQ && len >= 4 + PUB_KEY_SIZE) {  // can deprecate, in favour of CMD_SEND_BINARY_REQ
    uint8_t *pub_key = &cmd_frame[4];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    if (recipient) {
      uint32_t tag, est_timeout;
      int result = sendRequest(*recipient, REQ_TYPE_GET_TELEMETRY_DATA, tag, est_timeout);
      if (result == MSG_SEND_FAILED) {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      } else {
        clearPendingReqs();
        pending_telemetry = tag; // match this in onContactResponse()
        out_frame[0] = RESP_CODE_SENT;
        out_frame[1] = (result == MSG_SEND_SENT_FLOOD) ? 1 : 0;
        memcpy(&out_frame[2], &tag, 4);
        memcpy(&out_frame[6], &est_timeout, 4);
        _serial->writeFrame(out_frame, 10);
      }
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND); // contact not found
    }
  } else if (cmd_frame[0] == CMD_SEND_TELEMETRY_REQ && len == 4) {  // 'self' telemetry request
    telemetry.reset();
    telemetry.addVoltage(TELEM_CHANNEL_SELF, (float)board.getBattMilliVolts() / 1000.0f);
    float temperature = board.getMCUTemperature();
    if(!isnan(temperature)) { // Supported boards with built-in temperature sensor. ESP32-C3 may return NAN
      telemetry.addTemperature(TELEM_CHANNEL_SELF, temperature); // Built-in MCU Temperature
    }

    // query other sensors -- target specific
    sensors.querySensors(0xFF, telemetry);

    int i = 0;
    out_frame[i++] = PUSH_CODE_TELEMETRY_RESPONSE;
    out_frame[i++] = 0; // reserved
    memcpy(&out_frame[i], self_id.pub_key, 6);
    i += 6; // pub_key_prefix
    uint8_t tlen = telemetry.getSize();
    memcpy(&out_frame[i], telemetry.getBuffer(), tlen);
    i += tlen;
    _serial->writeFrame(out_frame, i);
  } else if (cmd_frame[0] == CMD_SEND_BINARY_REQ && len >= 2 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    if (recipient) {
      uint8_t *req_data = &cmd_frame[1 + PUB_KEY_SIZE];
      uint32_t tag, est_timeout;
      int result = sendRequest(*recipient, req_data, len - (1 + PUB_KEY_SIZE), tag, est_timeout);
      if (result == MSG_SEND_FAILED) {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      } else {
        clearPendingReqs();
        pending_req = tag; // match this in onContactResponse()
        out_frame[0] = RESP_CODE_SENT;
        out_frame[1] = (result == MSG_SEND_SENT_FLOOD) ? 1 : 0;
        memcpy(&out_frame[2], &tag, 4);
        memcpy(&out_frame[6], &est_timeout, 4);
        _serial->writeFrame(out_frame, 10);
      }
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND); // contact not found
    }
  } else if (cmd_frame[0] == CMD_HAS_CONNECTION && len >= 1 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    if (hasConnectionTo(pub_key)) {
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND);
    }
  } else if (cmd_frame[0] == CMD_LOGOUT && len >= 1 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    stopConnection(pub_key);
    writeOKFrame();
  } else if (cmd_frame[0] == CMD_GET_CHANNEL && len >= 2) {
    uint8_t channel_idx = cmd_frame[1];
    ChannelDetails channel;
    if (getChannel(channel_idx, channel)) {
      int i = 0;
      out_frame[i++] = RESP_CODE_CHANNEL_INFO;
      out_frame[i++] = channel_idx;
      strcpy((char *)&out_frame[i], channel.name);
      i += 32;
      memcpy(&out_frame[i], channel.channel.secret, 16);
      i += 16; // NOTE: only 128-bit supported
      _serial->writeFrame(out_frame, i);
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND);
    }
  } else if (cmd_frame[0] == CMD_SET_CHANNEL && len >= 2 + 32 + 32) {
    writeErrFrame(ERR_CODE_UNSUPPORTED_CMD); // not supported (yet)
  } else if (cmd_frame[0] == CMD_SET_CHANNEL && len >= 2 + 32 + 16) {
    uint8_t channel_idx = cmd_frame[1];
    ChannelDetails channel;
    StrHelper::strncpy(channel.name, (char *)&cmd_frame[2], 32);
    memset(channel.channel.secret, 0, sizeof(channel.channel.secret));
    memcpy(channel.channel.secret, &cmd_frame[2 + 32], 16); // NOTE: only 128-bit supported
    if (setChannel(channel_idx, channel)) {
      saveChannels();
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND); // bad channel_idx
    }
  } else if (cmd_frame[0] == CMD_SIGN_START) {
    out_frame[0] = RESP_CODE_SIGN_START;
    out_frame[1] = 0; // reserved
    uint32_t len = MAX_SIGN_DATA_LEN;
    memcpy(&out_frame[2], &len, 4);
    _serial->writeFrame(out_frame, 6);

    if (sign_data) {
      free(sign_data);
    }
    sign_data = (uint8_t *)malloc(MAX_SIGN_DATA_LEN);
    sign_data_len = 0;
  } else if (cmd_frame[0] == CMD_SIGN_DATA && len > 1) {
    if (sign_data == NULL || sign_data_len + (len - 1) > MAX_SIGN_DATA_LEN) {
      writeErrFrame(sign_data == NULL ? ERR_CODE_BAD_STATE : ERR_CODE_TABLE_FULL); // error: too long
    } else {
      memcpy(&sign_data[sign_data_len], &cmd_frame[1], len - 1);
      sign_data_len += (len - 1);
      writeOKFrame();
    }
  } else if (cmd_frame[0] == CMD_SIGN_FINISH) {
    if (sign_data) {
      self_id.sign(&out_frame[1], sign_data, sign_data_len);

      free(sign_data); // don't need sign_data now
      sign_data = NULL;

      out_frame[0] = RESP_CODE_SIGNATURE;
      _serial->writeFrame(out_frame, 1 + SIGNATURE_SIZE);
    } else {
      writeErrFrame(ERR_CODE_BAD_STATE);
    }
  } else if (cmd_frame[0] == CMD_SEND_TRACE_PATH && len > 10 && len - 10 <= MAX_PACKET_PAYLOAD - 9) {
    uint8_t path_len = len - 10;
    uint8_t flags = cmd_frame[9];
    uint8_t path_sz = flags & 0x03;  // NEW v1.11+
    if ((path_len >> path_sz) > MAX_PATH_SIZE || (path_len % (1 << path_sz)) != 0) { // make sure is multiple of path_sz
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else {
      uint32_t tag, auth;
      memcpy(&tag, &cmd_frame[1], 4);
      memcpy(&auth, &cmd_frame[5], 4);
      auto pkt = createTrace(tag, auth, flags);
      if (pkt) {
        // Compute before handing ownership to sendDirect(), which releases the
        // packet itself if validation or queueing fails.
        uint32_t t = _radio->getEstAirtimeFor(9 + path_len + 2);
        uint32_t est_timeout = calcDirectTimeoutMillisFor(t, path_len >> path_sz);
        if (sendDirect(pkt, &cmd_frame[10], path_len)) {
          out_frame[0] = RESP_CODE_SENT;
          out_frame[1] = 0;
          memcpy(&out_frame[2], &tag, 4);
          memcpy(&out_frame[6], &est_timeout, 4);
          _serial->writeFrame(out_frame, 10);
        } else {
          writeErrFrame(ERR_CODE_TABLE_FULL);
        }
      } else {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      }
    }
  } else if (cmd_frame[0] == CMD_SET_DEVICE_PIN && len >= 5) {

    // get pin from command frame
    uint32_t pin;
    memcpy(&pin, &cmd_frame[1], 4);

    // ensure pin is zero, or a valid 6 digit pin
    if (pin == 0 || (pin >= 100000 && pin <= 999999)) {
      _prefs.ble_pin = pin;
      savePrefs();
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    }
  } else if (cmd_frame[0] == CMD_GET_CUSTOM_VARS) {
    out_frame[0] = RESP_CODE_CUSTOM_VARS;
    char *dp = (char *)&out_frame[1];
    for (int i = 0; i < sensors.getNumSettings() && dp - (char *)&out_frame[1] < 140; i++) {
      if (i > 0) {
        *dp++ = ',';
      }
      strcpy(dp, sensors.getSettingName(i));
      dp = strchr(dp, 0);
      *dp++ = ':';
      strcpy(dp, sensors.getSettingValue(i));
      dp = strchr(dp, 0);
    }
    _serial->writeFrame(out_frame, dp - (char *)out_frame);
  } else if (cmd_frame[0] == CMD_SET_CUSTOM_VAR && len >= 4) {
    cmd_frame[len] = 0;
    char *sp = (char *)&cmd_frame[1];
    char *np = strchr(sp, ':'); // look for separator char
    if (np) {
      *np++ = 0; // modify 'cmd_frame', replace ':' with null
      bool success = sensors.setSettingValue(sp, np);
      if (success) {
        #if ENV_INCLUDE_GPS == 1
        // Update node preferences for GPS settings
        if (strcmp(sp, "gps") == 0) {
          _prefs.gps_enabled = (np[0] == '1') ? 1 : 0;
          savePrefs();
        } else if (strcmp(sp, "gps_interval") == 0) {
          uint32_t interval_seconds = atoi(np);
          _prefs.gps_interval = constrain(interval_seconds, 0, 86400);
          savePrefs();
        }
        #endif
        writeOKFrame();
      } else {
        writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      }
    } else {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    }
  } else if (mesh::companion::isFemRxGainGet(cmd_frame[0])) {
    if (len != 1) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else if (!board.canControlLoRaFemLna()) {
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
    } else {
      out_frame[0] = RESP_CODE_OK;
      uint8_t value = board.isLoRaFemLnaEnabled() ? 1 : 0;
      memcpy(&out_frame[1], &value, 1);
      _serial->writeFrame(out_frame, 2);
    }
  } else if (mesh::companion::isFemRxGainSet(cmd_frame[0])) {
    uint8_t value = len >= 2 ? cmd_frame[1] : 0;
    if (len != 2) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else if (!board.canControlLoRaFemLna()) {
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
    } else if (value <= 1) {
      if (applyAndSaveFemRxGain(value != 0)) {
        writeOKFrame();
      } else {
        writeErrFrame(ERR_CODE_BAD_STATE);
      }
    } else {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    }
  } else if (mesh::companion::isRadioRxGainGet(cmd_frame[0])) {
    if (len != 1) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else if (!radio_driver.supportsRxBoostedGainMode()) {
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
    } else {
      out_frame[0] = RESP_CODE_OK;
      out_frame[1] = _prefs.rx_boosted_gain ? 1 : 0;
      _serial->writeFrame(out_frame, 2);
    }
  } else if (mesh::companion::isRadioRxGainSet(cmd_frame[0])) {
    uint8_t value = len >= 2 ? cmd_frame[1] : 0;
    if (len != 2 || value > 1) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else if (!radio_driver.supportsRxBoostedGainMode()) {
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
    } else if (!applyAndSaveRxBoostedGain(value != 0)) {
      writeErrFrame(ERR_CODE_BAD_STATE);
    } else {
      writeOKFrame();
    }
  } else if (mesh::companion::isWiFiPowerSaveGet(cmd_frame[0])) {
    if (len != 1) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else {
#if defined(ESP32) && defined(WIFI_SSID)
      out_frame[0] = RESP_CODE_OK;
      out_frame[1] = getCompanionWiFiPowerSave();
      _serial->writeFrame(out_frame, 2);
#else
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
#endif
    }
  } else if (mesh::companion::isWiFiPowerSaveSet(cmd_frame[0])) {
    if (len != 2) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else {
#if defined(ESP32) && defined(WIFI_SSID)
      const uint8_t value = cmd_frame[1];
      if (value > mesh::wifi::kPowerSaveMax) {
        writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      } else {
        char reply[160];
        if (applyAndSaveWiFiPowerSaving(
                companionWiFiPowerSaveName(value), reply, sizeof(reply))) {
          writeOKFrame();
        } else {
          writeErrFrame(ERR_CODE_BAD_STATE);
        }
      }
#else
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
#endif
    }
  } else if (mesh::companion::isBluetoothNameGet(cmd_frame[0])) {
    if (len != 1) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else {
      out_frame[0] = RESP_CODE_OK;
      out_frame[1] = mesh::companion::hasCustomBluetoothName(
          _prefs.bluetooth_name) ? 1 : 0;
      char* effective_name = reinterpret_cast<char*>(&out_frame[2]);
      mesh::companion::formatBluetoothName(
          effective_name, MAX_FRAME_SIZE - 1, _prefs.bluetooth_name,
          BLE_NAME_PREFIX, _prefs.node_name);
      _serial->writeFrame(out_frame, 2 + strlen(effective_name));
    }
  } else if (mesh::companion::isBluetoothNameSet(cmd_frame[0])) {
    const size_t name_len = len - 1;
    if (name_len > mesh::companion::BLUETOOTH_NAME_MAX_BYTES
        || memchr(&cmd_frame[1], 0, name_len) != NULL) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else {
      char name[mesh::companion::BLUETOOTH_NAME_SIZE] = {0};
      if (name_len != 0) memcpy(name, &cmd_frame[1], name_len);
      if (name_len != 0 && !mesh::companion::isValidBluetoothName(name)) {
        writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      } else if (!saveBluetoothNameOverride(name)) {
        writeErrFrame(ERR_CODE_BAD_STATE);
      } else {
        writeOKFrame();
      }
    }
  } else if (cmd_frame[0]
             == mesh::companion::CMD_EXEC_LOCAL_OTA_CONTROL) {
#if defined(COMPANION_RADIO_FULL)
    const size_t command_len = len - 1;
    if (!mesh::companion::isBleOtaControlCommandAllowed(
            &cmd_frame[1], command_len)) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else {
      char command[MAX_FRAME_SIZE] = {0};
      memcpy(command, &cmd_frame[1], command_len);
      char reply[MAX_FRAME_SIZE] = {0};
      if (!handleLocalControlCommand(command, reply, sizeof(reply))) {
        writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
      } else {
        out_frame[0] = RESP_CODE_OK;
        const size_t reply_len = strnlen(reply, MAX_FRAME_SIZE - 2);
        out_frame[1] = static_cast<uint8_t>(reply_len);
        memcpy(&out_frame[2], reply, reply_len);
        _serial->writeFrame(out_frame, 2 + reply_len);
      }
    }
#else
    writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
#endif
  } else if (cmd_frame[0] == mesh::companion::CMD_BLE_MOTA_SOURCE) {
#if defined(COMPANION_RADIO_FULL)
    if (len != 2) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    } else if (_mota_source_control == NULL) {
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
    } else {
      const mesh::companion::MotaSourceAction action =
          static_cast<mesh::companion::MotaSourceAction>(cmd_frame[1]);
      char control_reply[96] = {0};
      bool action_ok = true;
      if (action == mesh::companion::MotaSourceAction::Start) {
        action_ok = _mota_source_control->start(control_reply,
                                                sizeof(control_reply));
      } else if (action == mesh::companion::MotaSourceAction::Stop) {
        action_ok = _mota_source_control->stop(control_reply,
                                               sizeof(control_reply));
      } else if (action != mesh::companion::MotaSourceAction::Status) {
        writeErrFrame(ERR_CODE_ILLEGAL_ARG);
        return;
      }

      if (!action_ok) {
        writeErrFrame(ERR_CODE_BAD_STATE);
      } else {
        const mesh::companion::MotaSourceStatus status =
            _mota_source_control->status();
        uint8_t flags = 0;
        if (status.channel_ready) {
          flags |= mesh::companion::MOTA_SOURCE_FLAG_CHANNEL_READY;
        }
        if (status.attached) {
          flags |= mesh::companion::MOTA_SOURCE_FLAG_ATTACHED;
        }
        if (status.another_link_active) {
          flags |=
              mesh::companion::MOTA_SOURCE_FLAG_ANOTHER_LINK_ACTIVE;
        }
        out_frame[0] = RESP_CODE_OK;
        out_frame[1] = cmd_frame[1];
        out_frame[2] = flags;
        out_frame[3] = static_cast<uint8_t>(status.offered & 0xFF);
        out_frame[4] = static_cast<uint8_t>(status.offered >> 8);
        out_frame[5] = static_cast<uint8_t>(status.advertised & 0xFF);
        out_frame[6] = static_cast<uint8_t>(status.advertised >> 8);
        out_frame[7] = static_cast<uint8_t>(status.packets_sent & 0xFF);
        out_frame[8] = static_cast<uint8_t>((status.packets_sent >> 8) & 0xFF);
        out_frame[9] = static_cast<uint8_t>((status.packets_sent >> 16) & 0xFF);
        out_frame[10] = static_cast<uint8_t>((status.packets_sent >> 24) & 0xFF);
        _serial->writeFrame(out_frame, 11);
      }
    }
#else
    writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
#endif
  } else if (cmd_frame[0] == CMD_GET_ADVERT_PATH && len >= PUB_KEY_SIZE+2) {
    // FUTURE use:  uint8_t reserved = cmd_frame[1];
    uint8_t *pub_key = &cmd_frame[2];
    AdvertPath* found = NULL;
    for (int i = 0; i < ADVERT_PATH_TABLE_SIZE; i++) {
      auto p = &advert_paths[i];
      if (memcmp(p->pubkey_prefix, pub_key, sizeof(p->pubkey_prefix)) == 0) {
        found = p;
        break;
      }
    }
    if (found) {
      int i = 0;
      out_frame[i++] = RESP_CODE_ADVERT_PATH;
      memcpy(&out_frame[i], &found->recv_timestamp, 4); i += 4;
      out_frame[i++] = found->path_len;
      i += mesh::Packet::writePath(&out_frame[i], found->path, found->path_len);
      _serial->writeFrame(out_frame, i);
    } else {
      writeErrFrame(ERR_CODE_NOT_FOUND);
    }
  } else if (cmd_frame[0] == CMD_GET_STATS && len >= 2) {
    uint8_t stats_type = cmd_frame[1];
    if (stats_type == STATS_TYPE_CORE) {
      int i = 0;
      out_frame[i++] = RESP_CODE_STATS;
      out_frame[i++] = STATS_TYPE_CORE;
      uint16_t battery_mv = board.getBattMilliVolts();
      uint32_t uptime_secs = _ms->getMillis() / 1000;
      uint8_t queue_len = (uint8_t)_mgr->getOutboundCount(0xFFFFFFFF);
      memcpy(&out_frame[i], &battery_mv, 2); i += 2;
      memcpy(&out_frame[i], &uptime_secs, 4); i += 4;
      memcpy(&out_frame[i], &_err_flags, 2); i += 2;
      out_frame[i++] = queue_len;
      _serial->writeFrame(out_frame, i);
    } else if (stats_type == STATS_TYPE_RADIO) {
      int i = 0;
      out_frame[i++] = RESP_CODE_STATS;
      out_frame[i++] = STATS_TYPE_RADIO;
      int16_t noise_floor = (int16_t)_radio->getNoiseFloor();
      int8_t last_rssi = (int8_t)radio_driver.getLastRSSI();
      int8_t last_snr = (int8_t)(radio_driver.getLastSNR() * 4); // scaled by 4 for 0.25 dB precision
      uint32_t tx_air_secs = getTotalAirTime() / 1000;
      uint32_t rx_air_secs = getReceiveAirTime() / 1000;
      memcpy(&out_frame[i], &noise_floor, 2); i += 2;
      out_frame[i++] = last_rssi;
      out_frame[i++] = last_snr;
      memcpy(&out_frame[i], &tx_air_secs, 4); i += 4;
      memcpy(&out_frame[i], &rx_air_secs, 4); i += 4;
      _serial->writeFrame(out_frame, i);
    } else if (stats_type == STATS_TYPE_PACKETS) {
      int i = 0;
      out_frame[i++] = RESP_CODE_STATS;
      out_frame[i++] = STATS_TYPE_PACKETS;
      uint32_t recv = radio_driver.getPacketsRecv();
      uint32_t sent = radio_driver.getPacketsSent();
      uint32_t n_sent_flood = getNumSentFlood();
      uint32_t n_sent_direct = getNumSentDirect();
      uint32_t n_recv_flood = getNumRecvFlood();
      uint32_t n_recv_direct = getNumRecvDirect();
      uint32_t n_recv_errors = radio_driver.getPacketsRecvErrors();
      memcpy(&out_frame[i], &recv, 4); i += 4;
      memcpy(&out_frame[i], &sent, 4); i += 4;
      memcpy(&out_frame[i], &n_sent_flood, 4); i += 4;
      memcpy(&out_frame[i], &n_sent_direct, 4); i += 4;
      memcpy(&out_frame[i], &n_recv_flood, 4); i += 4;
      memcpy(&out_frame[i], &n_recv_direct, 4); i += 4;
      memcpy(&out_frame[i], &n_recv_errors, 4); i += 4;
      _serial->writeFrame(out_frame, i);
    } else {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG); // invalid stats sub-type
    }
  } else if (cmd_frame[0] == CMD_FACTORY_RESET && len >= 6 && memcmp(&cmd_frame[1], "reset", 5) == 0) {
    if (_serial) {
      MESH_DEBUG_PRINTLN("Factory reset: disabling serial interface to prevent reconnects (BLE/WiFi)");
      _serial->disable(); // Phone app disconnects before we can send OK frame so it's safe here
    }
    bool success = _store->formatFileSystem();
    if (success) {
      writeOKFrame();
      delay(1000);
      board.reboot();  // doesn't return
    } else {
      writeErrFrame(ERR_CODE_FILE_IO_ERROR);
    }
  } else if (cmd_frame[0] == CMD_SET_FLOOD_SCOPE_KEY && len >= 2 && cmd_frame[1] == 0) {
    if (len >= 2 + 16) {
      memcpy(send_scope.key, &cmd_frame[2], sizeof(send_scope.key));  // set scope override TransportKey
    } else {
      memset(send_scope.key, 0, sizeof(send_scope.key));  // reset scope override
    }
    send_unscoped = false;
    writeOKFrame();
  } else if (cmd_frame[0] == CMD_SET_FLOOD_SCOPE_KEY && len >= 2 && cmd_frame[1] == 1) {  // ver 12+
    send_unscoped = true;
    writeOKFrame();
  } else if (cmd_frame[0] == CMD_SET_DEFAULT_FLOOD_SCOPE && len >= 1) {
    if (len >= 1+31+16) {
      const void* terminator = memchr(&cmd_frame[1], 0, 31);
      size_t n = terminator == NULL ? 31 : (const uint8_t*)terminator - &cmd_frame[1];
      if (n > 0 && n < 31) {
        memcpy(_prefs.default_scope_name, &cmd_frame[1], n);
        _prefs.default_scope_name[n] = 0;
        memcpy(_prefs.default_scope_key, &cmd_frame[1+31], 16);
        savePrefs();
        writeOKFrame();
      } else {
        writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      }
    } else {
      memset(_prefs.default_scope_name, 0, sizeof(_prefs.default_scope_name));  // set default scope to null
      memset(_prefs.default_scope_key, 0, sizeof(_prefs.default_scope_key));
      savePrefs();
      writeOKFrame();
    }
  } else if (cmd_frame[0] == CMD_GET_DEFAULT_FLOOD_SCOPE) {
    out_frame[0] = RESP_CODE_DEFAULT_FLOOD_SCOPE;
    if (strlen(_prefs.default_scope_name) > 0) {
      memcpy(&out_frame[1], _prefs.default_scope_name, 31);
      memcpy(&out_frame[1+31], _prefs.default_scope_key, 16);
      _serial->writeFrame(out_frame, 1+31+16);
    } else {
      _serial->writeFrame(out_frame, 1);   // no name or key means null
    }
  } else if (cmd_frame[0] == CMD_SEND_CONTROL_DATA && len >= 2 && (cmd_frame[1] & 0x80) != 0) {
    auto resp = createControlData(&cmd_frame[1], len - 1);
    if (resp) {
      sendZeroHop(resp);
      writeOKFrame();
    } else {
      writeErrFrame(ERR_CODE_TABLE_FULL);
    }
  } else if (cmd_frame[0] == CMD_SET_AUTOADD_CONFIG) {
    if (len < 2) {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      return;
    }
    _prefs.autoadd_config = cmd_frame[1];
    if (len >= 3) {
      _prefs.autoadd_max_hops = min(cmd_frame[2], (uint8_t)64);
    }
    savePrefs();
    writeOKFrame();
  } else if (cmd_frame[0] == CMD_GET_AUTOADD_CONFIG) {
    int i = 0;
    out_frame[i++] = RESP_CODE_AUTOADD_CONFIG;
    out_frame[i++] = _prefs.autoadd_config;
    out_frame[i++] = _prefs.autoadd_max_hops;
    _serial->writeFrame(out_frame, i);
  } else if (cmd_frame[0] == CMD_GET_ALLOWED_REPEAT_FREQ) {
    int i = 0;
    out_frame[i++] = RESP_ALLOWED_REPEAT_FREQ;
    for (int k = 0; k < sizeof(repeat_freq_ranges)/sizeof(repeat_freq_ranges[0]) && i + 8 < sizeof(out_frame); k++) {
      auto r = &repeat_freq_ranges[k];
      memcpy(&out_frame[i], &r->lower_freq, 4); i += 4;
      memcpy(&out_frame[i], &r->upper_freq, 4); i += 4;
    }
    _serial->writeFrame(out_frame, i);
  } else if (cmd_frame[0] == CMD_SEND_RAW_PACKET && len >= 4) {
    auto pkt = obtainNewPacket();
    if (pkt) {
      uint8_t priority = cmd_frame[1];
      if (tryParsePacket(pkt, &cmd_frame[2], len - 2)) {
        sendPacket(pkt, priority, 0);
        writeOKFrame();
      } else {
        releasePacket(pkt);
        writeErrFrame(ERR_CODE_ILLEGAL_ARG);
      }
    } else {
      writeErrFrame(ERR_CODE_TABLE_FULL);
    }
  } else {
    writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
    MESH_DEBUG_PRINTLN("ERROR: unknown command: %02X", cmd_frame[0]);
  }
}

static bool save_filter(const ContactInfo& c) {
  return c.type != ADV_TYPE_NONE;   // don't save the transient/anon entries
}

void MyMesh::saveContacts() {
  const bool success = _store->saveContacts(this, save_filter);
  dirty_contacts_expiry = (!success || _store->hasPendingContactWrites())
      ? futureMillis(1000) : 0;
}

void MyMesh::scheduleContactWrite(const ContactInfo& contact) {
  if (_store->markContactDirty(contact)) {
    dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
  }
}

void MyMesh::scheduleContactWriteAfterRelease(const ContactInfo& contact) {
  if (_store->releaseContact(contact)) {
    dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
  }
}

bool MyMesh::applyAndSaveFemRxGain(bool enabled) {
  if (!board.canControlLoRaFemLna()) return false;

  const bool previous_hardware = board.isLoRaFemLnaEnabled();
  const uint8_t previous_pref = _prefs.radio_fem_rxgain;
  const uint8_t previous_override = _prefs.radio_fem_rxgain_override;
  const bool changed = previous_hardware != enabled;
  if (!board.setLoRaFemLnaEnabled(enabled)) return false;

  if (changed && _radio_available) _radio->recalibrateNoiseFloor();
  _prefs.radio_fem_rxgain = enabled ? 1 : 0;
  _prefs.radio_fem_rxgain_override = 1;
  if (!savePrefs()) {
    _prefs.radio_fem_rxgain = previous_pref;
    _prefs.radio_fem_rxgain_override = previous_override;
    board.setLoRaFemLnaEnabled(previous_hardware);
    if (changed && _radio_available) _radio->recalibrateNoiseFloor();
    return false;
  }
  return true;
}

bool MyMesh::applyAndSaveFemTxGain(bool enabled) {
  if (!board.canControlLoRaFemPaGain()) return false;
  const bool previous_hardware = board.isLoRaFemPaGainEnabled();
  const uint8_t previous_pref = _prefs.radio_fem_txgain;
  if (!board.setLoRaFemPaGainEnabled(enabled)) return false;

  _prefs.radio_fem_txgain = enabled ? 1 : 0;
  if (!savePrefs()) {
    _prefs.radio_fem_txgain = previous_pref;
    board.setLoRaFemPaGainEnabled(previous_hardware);
    return false;
  }
  return true;
}

bool MyMesh::applyAndSaveRxBoostedGain(bool enabled) {
  if (!radio_driver.supportsRxBoostedGainMode()
      || (_radio_available && !radio_driver.setRxBoostedGainMode(enabled))) {
    return false;
  }

  const uint8_t previous_pref = _prefs.rx_boosted_gain;
  _prefs.rx_boosted_gain = enabled ? 1 : 0;
  if (!savePrefs()) {
    _prefs.rx_boosted_gain = previous_pref;
    if (_radio_available) {
      radio_driver.setRxBoostedGainMode(previous_pref != 0);
    }
    return false;
  }
  return true;
}

bool MyMesh::saveBluetoothNameOverride(const char* name) {
  if (name == NULL) return false;

  char previous[sizeof(_prefs.bluetooth_name)];
  memcpy(previous, _prefs.bluetooth_name, sizeof(previous));
  memset(_prefs.bluetooth_name, 0, sizeof(_prefs.bluetooth_name));
  if (name[0] != 0) {
    StrHelper::strncpy(_prefs.bluetooth_name, name,
                       sizeof(_prefs.bluetooth_name));
  }

  if (savePrefs()) return true;
  memcpy(_prefs.bluetooth_name, previous, sizeof(_prefs.bluetooth_name));
  return false;
}

bool MyMesh::applyAndSaveBluetoothName(const char* value, char* reply,
                                       size_t reply_size) {
  if (reply == NULL || reply_size == 0) return false;

  const bool use_default = value == NULL || value[0] == 0;
  if (!use_default && !mesh::companion::isValidBluetoothName(value)) {
    snprintf(reply, reply_size,
             "Error: Bluetooth name must be 1-%u valid UTF-8 bytes without control characters",
             (unsigned)mesh::companion::BLUETOOTH_NAME_MAX_BYTES);
    return false;
  }

  if (!saveBluetoothNameOverride(use_default ? "" : value)) {
    snprintf(reply, reply_size, "Error: Bluetooth name save failed");
    return false;
  }

  if (use_default) {
    snprintf(reply, reply_size,
             "OK - Bluetooth name follows %s<node name>; reboot required",
             BLE_NAME_PREFIX);
  } else {
    snprintf(reply, reply_size,
             "OK - Bluetooth name saved as '%s'; reboot required", value);
  }
  return true;
}

void MyMesh::formatBluetoothNameStatus(char* reply, size_t reply_size) const {
  if (reply == NULL || reply_size == 0) return;

  char effective_name[64];
  mesh::companion::formatBluetoothName(
      effective_name, sizeof(effective_name), _prefs.bluetooth_name,
      BLE_NAME_PREFIX, _prefs.node_name);
  snprintf(reply, reply_size, "> %s (%s)", effective_name,
           mesh::companion::hasCustomBluetoothName(_prefs.bluetooth_name)
               ? "custom" : "default from node name");
}

#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
void MyMesh::formatEspNowChannel(char* reply, size_t reply_size) const {
  if (reply == NULL || reply_size == 0) return;
  const uint8_t saved = mesh::wifi::loadConfiguredEspNowChannel();
  const uint8_t active = mesh::wifi::activeEspNowChannel();
  if (saved == active) {
    snprintf(reply, reply_size, "> %u (saved and active)",
             (unsigned)saved);
  } else {
    snprintf(reply, reply_size,
             "> saved %u, active %u; reboot required",
             (unsigned)saved, (unsigned)active);
  }
}

bool MyMesh::applyAndSaveEspNowChannel(const char* value, char* reply,
                                       size_t reply_size) {
  if (reply == NULL || reply_size == 0) return false;
  uint8_t channel = 0;
  if (!mesh::wifi::parseEspNowChannel(value, channel)) {
    snprintf(reply, reply_size, "Error: ESP-NOW channel must be 1-13");
    return false;
  }
  // Snapshot the boot channel before writing NVS so even a CLI-only image
  // cannot reinterpret the newly-saved setting as active in this boot.
  const uint8_t active = mesh::wifi::activeEspNowChannel();
  if (!mesh::wifi::saveConfiguredEspNowChannel(channel)) {
    snprintf(reply, reply_size, "Error: failed to save ESP-NOW channel");
    return false;
  }

  if (channel == active) {
    snprintf(reply, reply_size,
             "OK - ESP-NOW channel %u saved and active",
             (unsigned)channel);
  } else {
    snprintf(reply, reply_size,
             "OK - ESP-NOW channel %u saved; active %u; reboot required",
             (unsigned)channel, (unsigned)active);
  }
  return true;
}
#endif

bool MyMesh::applyAndSavePowerSaving(const char* value, char* reply) {
  bool enabled;
  if (strcmp(value, "on") == 0) {
    enabled = true;
  } else if (strcmp(value, "off") == 0) {
    enabled = false;
  } else {
    strcpy(reply, "Error: use powersaving on or powersaving off");
    return false;
  }

  _prefs.powersaving_enabled = enabled ? 1 : 0;
  sensors.setPowerSavingEnabled(enabled);
  savePrefs();
  snprintf(reply, 160, "OK - powersaving %s", enabled ? "on" : "off");
  return true;
}

#if defined(ESP32) && defined(WIFI_SSID)
void MyMesh::syncWiFiPowerSaving() {
  const uint8_t mode = getCompanionWiFiPowerSave();
#ifdef WITH_MQTT_BRIDGE
  _mqtt_prefs.wifi_power_save = mode;
#endif
#ifdef WITH_WEBCONFIG
  if (_webconfig) _webconfig->reloadStandaloneWiFi();
#endif
}

void MyMesh::formatWiFiPowerSaving(char* reply, size_t reply_size) const {
  if (!reply || reply_size == 0) return;
  snprintf(reply, reply_size, "> %s", getCompanionWiFiPowerSaveName());
}

bool MyMesh::applyAndSaveWiFiPowerSaving(const char* value, char* reply,
                                         size_t reply_size) {
  if (!reply || reply_size == 0) return false;
  uint8_t mode = mesh::wifi::kDefaultPowerSave;
  if (value && strcmp(value, "min") == 0) {
    mode = mesh::wifi::kPowerSaveMin;
  } else if (value && strcmp(value, "none") == 0) {
    mode = mesh::wifi::kPowerSaveNone;
  } else if (value && strcmp(value, "max") == 0) {
    mode = mesh::wifi::kPowerSaveMax;
  } else {
    snprintf(reply, reply_size,
             "Error: power save must be none, min, or max");
    return false;
  }

  const CompanionWiFiPowerSaveResult result =
      setCompanionWiFiPowerSave(mode);
  if (result == CompanionWiFiPowerSaveResult::PrimaryEspNowConflict) {
    snprintf(reply, reply_size,
             "Error: power save max is unavailable while ESP-NOW is the primary radio");
    return false;
  }
  if (result == CompanionWiFiPowerSaveResult::BluetoothConflict) {
    snprintf(reply, reply_size,
             "Error: power save none is unavailable while Bluetooth is active");
    return false;
  }
  if (result == CompanionWiFiPowerSaveResult::InvalidMode) {
    snprintf(reply, reply_size,
             "Error: power save must be none, min, or max");
    return false;
  }
  if (result == CompanionWiFiPowerSaveResult::StorageError) {
    snprintf(reply, reply_size, "Error: failed to save WiFi power save");
    return false;
  }

  syncWiFiPowerSaving();
  if (result == CompanionWiFiPowerSaveResult::Applied) {
    snprintf(reply, reply_size, "OK - WiFi power save set to %s", value);
  } else {
    snprintf(reply, reply_size,
             "OK - saved; WiFi power save applies on next connection");
  }
  return true;
}
#endif

bool MyMesh::applyAndSaveRxPowerSaving(const char* value, char* reply) {
  if (!radio_driver.supportsRxPowerSaving()) {
    strcpy(reply, "Error: RX power saving unsupported");
    return false;
  }

  uint8_t enabled = _prefs.rx_powersaving_enabled;
  uint8_t level = _prefs.rx_ps_level;
  uint8_t preamble = _prefs.rx_ps_preamble;
  uint32_t rx_us = _prefs.rx_ps_rx_us;
  uint32_t sleep_us = _prefs.rx_ps_sleep_us;
  bool level_requested = false;
  bool manual_requested = false;

  mesh::cli::RxPowerSavingArguments parsed = {};
  if (!mesh::cli::parseRxPowerSavingArgumentsStrict(value, parsed)) {
    strcpy(reply, "Error: use off, level 1-10, or RX/SLEEP microseconds");
    return false;
  }

  if (parsed.mode == mesh::cli::RxPowerSavingArgumentMode::Off) {
    enabled = 0;
  } else if (parsed.mode
             == mesh::cli::RxPowerSavingArgumentMode::Conservative) {
    enabled = 1;
    level = RX_POWERSAVING_CONSERVATIVE_LEVEL;
    preamble = RX_POWERSAVING_PROFILE_PREAMBLE;
    level_requested = true;
  } else if (parsed.mode == mesh::cli::RxPowerSavingArgumentMode::Balanced) {
    enabled = 1;
    level = RX_POWERSAVING_BALANCED_LEVEL;
    preamble = RX_POWERSAVING_PROFILE_PREAMBLE;
    level_requested = true;
  } else if (parsed.mode == mesh::cli::RxPowerSavingArgumentMode::Level) {
    if (parsed.level < 1 || parsed.level > 10) {
      strcpy(reply, parsed.preamble == 0
          ? "Error: level must be 1-10"
          : "Error: level must be 1-10; preamble must be 16 or 32");
      return false;
    }
    if (parsed.preamble != 0
        && parsed.preamble != 16 && parsed.preamble != 32) {
      strcpy(reply, "Error: level must be 1-10; preamble must be 16 or 32");
      return false;
    }
    enabled = 1;
    level = static_cast<uint8_t>(parsed.level);
    preamble = static_cast<uint8_t>(parsed.preamble);
    level_requested = true;
  } else if (parsed.mode == mesh::cli::RxPowerSavingArgumentMode::Manual) {
    if (parsed.rx_us < RX_POWERSAVING_MIN_MANUAL_PERIOD_US
        || parsed.rx_us > RX_POWERSAVING_MAX_PERIOD_US
        || parsed.sleep_us < RX_POWERSAVING_MIN_MANUAL_PERIOD_US
        || parsed.sleep_us > RX_POWERSAVING_MAX_PERIOD_US) {
      snprintf(reply, 160, "Error: RX/SLEEP must be %lu-%lu us",
               (unsigned long)RX_POWERSAVING_MIN_MANUAL_PERIOD_US,
               (unsigned long)RX_POWERSAVING_MAX_PERIOD_US);
      return false;
    }
    enabled = 1;
    rx_us = parsed.rx_us;
    sleep_us = parsed.sleep_us;
    level = 0;
    preamble = 0;
    manual_requested = true;
  }

  if (level_requested) {
    if (level < 1 || level > 10
        || (preamble != 0 && preamble != 16 && preamble != 32)
        || !recalcRxPowerSavingFromLevel(level, _prefs.sf, _prefs.bw, preamble,
                                         &rx_us, &sleep_us)) {
      strcpy(reply, "Error: level must be 1-10; preamble must be auto, 16, or 32");
      return false;
    }
  }
  if ((manual_requested || enabled)
      && (!isValidRxPowerSavingPeriod(rx_us)
          || !isValidRxPowerSavingPeriod(sleep_us))) {
    snprintf(reply, 160, "Error: RX/SLEEP must be %lu-%lu us",
             (unsigned long)RX_POWERSAVING_MIN_MANUAL_PERIOD_US,
             (unsigned long)RX_POWERSAVING_MAX_PERIOD_US);
    return false;
  }

  if (_radio_available
      && !radio_driver.setRxPowerSaving(enabled != 0, rx_us, sleep_us)) {
    strcpy(reply, "Error: radio busy; retry");
    return false;
  }

  _prefs.rx_powersaving_enabled = enabled;
  _prefs.rx_ps_rx_us = rx_us;
  _prefs.rx_ps_sleep_us = sleep_us;
  _prefs.rx_ps_level = level;
  _prefs.rx_ps_preamble = preamble;
  savePrefs();
  snprintf(reply, 160, "OK - %s,%lu,%lu",
           enabled ? "on" : "off", (unsigned long)rx_us,
           (unsigned long)sleep_us);
  return true;
}

void MyMesh::appendRxPowerSavingAdjustmentNote(char* reply, size_t reply_size,
                                                uint8_t sf, float bw) const {
  if (!reply || reply_size == 0 || !_prefs.rx_powersaving_enabled
      || _prefs.rx_ps_level < 1 || _prefs.rx_ps_level > 10) {
    return;
  }

  uint32_t rx_us = 0;
  uint32_t sleep_us = 0;
  uint8_t effective_level = 0;
  uint8_t effective_preamble = 0;
  if (!recalcRxPowerSavingFromLevel(
          _prefs.rx_ps_level, sf, bw, _prefs.rx_ps_preamble,
          &rx_us, &sleep_us, &effective_level, &effective_preamble)) {
    return;
  }

  const size_t used = strlen(reply);
  if (used >= reply_size - 1) return;

  if (rxPowerSavingUsesContinuousFallback(rx_us, sleep_us)) {
    snprintf(reply + used, reply_size - used,
             "; RXPS continuous-fast (no safe level %u-10)",
             (unsigned)_prefs.rx_ps_level);
    return;
  }

  const uint8_t requested_preamble = _prefs.rx_ps_preamble == 0
      ? rxPowerSavingPreambleForParams(sf, bw) : _prefs.rx_ps_preamble;
  if (effective_level == _prefs.rx_ps_level
      && effective_preamble == requested_preamble) {
    return;
  }

  snprintf(reply + used, reply_size - used,
           "; RXPS effective level %u, preamble %u (saved minimum %u)",
           (unsigned)effective_level, (unsigned)effective_preamble,
           (unsigned)_prefs.rx_ps_level);
}

#ifdef ENABLE_USB_INTERFACE
Stream& MyMesh::terminalOutput() {
  return _terminal_output != NULL ? *_terminal_output : Serial;
}

void MyMesh::resetTerminalSession() {
  _terminal_recipient_set = false;
  memset(_terminal_recipient_key, 0, sizeof(_terminal_recipient_key));
  clearTerminalLogin();
  clearTerminalCommand();
  clearTerminalTrace();

  // A reply from an abandoned TCP or USB terminal session must not be shown
  // to whichever terminal connects next. Keep the radio retry alive for the
  // Binary Companion interface, but remove its old text-terminal ownership.
  for (int i = 0; i < EXPECTED_ACK_TABLE_SIZE; i++) {
    expected_ack_table[i].terminal_origin = false;
  }
}

void MyMesh::printTerminalBanner(bool show_binary_stop) {
  Stream& output = terminalOutput();
#if COMPANION_FEATURE_TEMP_RADIO || COMPANION_FEATURE_NETWORK_TERMINAL
  output.print("\r\n===== MeshCore Full Companion Terminal =====\r\n\r\n");
#else
  output.print("\r\n===== MeshCore Companion Terminal =====\r\n\r\n");
#endif
  output.printf("WELCOME  %s\r\n", _prefs.node_name);
  mesh::Utils::printHex(output, self_id.pub_key, PUB_KEY_SIZE);
  output.printf("\r\nCompanion %s\r\n", FIRMWARE_VERSION);
  output.print("  (enter 'help' for commands)\r\n");
  if (show_binary_stop) {
    output.print("  (+++MESHCORE-TERM-STOP returns to Binary mode)\r\n");
  } else {
    output.print("  (disconnect to close this TCP terminal)\r\n");
  }
  output.print("\r\n> ");
}

void MyMesh::enterTerminalMode() {
  _terminal_mode = true;
  _terminal_output = &Serial;
  resetTerminalSession();
  printTerminalBanner(true);
}

void MyMesh::exitTerminalMode() {
  _terminal_mode = false;
  resetTerminalSession();
  if (_terminal_output == &Serial) _terminal_output = NULL;
}

#if COMPANION_FEATURE_NETWORK_TERMINAL
bool MyMesh::enterNetworkTerminalMode(Stream& output) {
  if (_terminal_mode) return false;
  _terminal_output = &output;
  resetTerminalSession();
  printTerminalBanner(false);
  return true;
}

void MyMesh::exitNetworkTerminalMode(Stream& output) {
  if (_terminal_mode || _terminal_output != &output) return;
  resetTerminalSession();
  _terminal_output = NULL;
}

bool MyMesh::isNetworkTerminalMode(const Stream& output) const {
  return !_terminal_mode && _terminal_output == &output;
}
#endif

ContactInfo* MyMesh::getTerminalRecipient() {
  if (!_terminal_recipient_set) return NULL;

  ContactInfo* recipient = lookupContactByPubKey(_terminal_recipient_key, PUB_KEY_SIZE);
  if (recipient == NULL) {
    _terminal_recipient_set = false;
    memset(_terminal_recipient_key, 0, sizeof(_terminal_recipient_key));
  }
  return recipient;
}

void MyMesh::printTerminalPath(const ContactInfo& recipient) {
  terminalOutput().printf("  Path to %s: ", recipient.name);
  if (recipient.out_path_len == OUT_PATH_UNKNOWN) {
    terminalOutput().print("unknown (next send uses FLOOD)\r\n");
    return;
  }
  if (!mesh::Packet::isValidPathLen(recipient.out_path_len)) {
    terminalOutput().print("invalid\r\n");
    return;
  }

  const uint8_t hash_size = (recipient.out_path_len >> 6) + 1;
  const uint8_t hop_count = recipient.out_path_len & 63;
  if (hop_count == 0) {
    terminalOutput().print("direct (zero hop)\r\n");
    return;
  }

  for (uint8_t hop = 0; hop < hop_count; hop++) {
    if (hop != 0) terminalOutput().print(',');
    mesh::Utils::printHex(terminalOutput(),
                          &recipient.out_path[(size_t)hop * hash_size],
                          hash_size);
  }
  terminalOutput().printf(" (%u %s, %u-byte hashes; used by DIRECT sends)\r\n",
                (unsigned)hop_count, hop_count == 1 ? "hop" : "hops",
                (unsigned)hash_size);
}

void MyMesh::handleTerminalPath(ContactInfo& recipient,
                                const char* path_spec) {
  if (path_spec == NULL) {
    printTerminalPath(recipient);
    return;
  }

  mesh::cli::TerminalPath path;
  const mesh::cli::TerminalPathParseResult parsed =
      mesh::cli::parseTerminalPath(path_spec, _terminal_tmp_buf,
                                   sizeof(recipient.out_path), 63, path);
  switch (parsed) {
    case mesh::cli::TerminalPathParseResult::Valid:
      break;
    case mesh::cli::TerminalPathParseResult::Missing:
      terminalOutput().print("  ERROR: use path <direct|clear|hops separated by spaces or commas>\r\n");
      return;
    case mesh::cli::TerminalPathParseResult::InvalidPrefix:
      terminalOutput().print("  ERROR: each path hop must be 2, 4, or 6 hex digits\r\n");
      return;
    case mesh::cli::TerminalPathParseResult::MixedPrefixSize:
      terminalOutput().print("  ERROR: all path hops must use the same width\r\n");
      return;
    case mesh::cli::TerminalPathParseResult::InvalidSeparator:
      terminalOutput().print("  ERROR: separate path hops with spaces or commas\r\n");
      return;
    case mesh::cli::TerminalPathParseResult::TooManyHops:
      terminalOutput().print("  ERROR: path must contain at most 63 hops\r\n");
      return;
    case mesh::cli::TerminalPathParseResult::RouteTooLong:
      terminalOutput().print("  ERROR: path is too long\r\n");
      return;
  }

  memset(recipient.out_path, 0, sizeof(recipient.out_path));
  if (path.mode == mesh::cli::TerminalPathMode::Clear) {
    recipient.out_path_len = OUT_PATH_UNKNOWN;
  } else {
    recipient.out_path_len = mesh::Packet::copyPath(
        recipient.out_path, _terminal_tmp_buf, path.encoded_len);
  }
  scheduleContactWrite(recipient);
  printTerminalPath(recipient);
}

void MyMesh::rememberTerminalAck(ContactInfo& recipient, const char* text,
                                 uint32_t message_timestamp, uint32_t expected_ack,
                                 uint32_t est_timeout,
                                 const uint8_t packet_retry_key[MAX_HASH_SIZE],
                                 AckTableEntry* replacement_entry) {
  if (expected_ack == 0) {
    if (replacement_entry != NULL) {
      clearExpectedAck(*replacement_entry, false);
    }
    return;
  }

  AckTableEntry& entry = replacement_entry != NULL
      ? *replacement_entry
      : expected_ack_table[next_ack_idx];
  clearExpectedAck(entry, false);
  entry.msg_sent = _ms->getMillis();
  entry.expires_at = futureMillis(est_timeout);
  entry.ack = expected_ack;
  entry.message_timestamp = message_timestamp;
  entry.contact = &recipient;
  mesh::Utils::sha256(entry.text_fingerprint, sizeof(entry.text_fingerprint),
                      recipient.id.pub_key, PUB_KEY_SIZE,
                      (const uint8_t*)text, strlen(text));
  memcpy(entry.retry_key, packet_retry_key, sizeof(entry.retry_key));
  entry.terminal_origin = true;
  if (replacement_entry == NULL) {
    next_ack_idx = (next_ack_idx + 1) % EXPECTED_ACK_TABLE_SIZE;
  }
  expireExpectedAcks();
}

void MyMesh::importTerminalCard(char* command) {
  while (*command == ' ') command++;
  if (strncmp(command, "meshcore://", 11) != 0) {
    terminalOutput().print("  ERROR: invalid card format\r\n");
    return;
  }

  char* encoded = command + 11;
  char* end = encoded + strlen(encoded);
  while (end > encoded && !mesh::Utils::isHexChar(end[-1])) {
    *--end = 0;
  }

  size_t encoded_len = strlen(encoded);
  if (encoded_len == 0 || (encoded_len & 1) != 0
      || encoded_len / 2 > sizeof(_terminal_tmp_buf)) {
    terminalOutput().print("  ERROR: invalid card format\r\n");
    return;
  }

  size_t raw_len = encoded_len / 2;
  if (!mesh::Utils::fromHex(_terminal_tmp_buf, raw_len, encoded)
      || !importContact(_terminal_tmp_buf, raw_len)) {
    terminalOutput().print("  ERROR: invalid card\r\n");
    return;
  }

  terminalOutput().print("  OK - contact import queued\r\n");
}

void MyMesh::listTerminalChannels() {
  bool found = false;
  terminalOutput().print("Channels:\r\n");
  for (int i = 0; i < MAX_GROUP_CHANNELS; i++) {
    ChannelDetails channel;
    if (getChannel(i, channel) && channel.name[0] != 0) {
      terminalOutput().printf("  %d: %s\r\n", i, channel.name);
      found = true;
    }
  }
  if (!found) terminalOutput().print("  (none configured)\r\n");
}

void MyMesh::handleTerminalDisplayCommand(const char* arguments) {
  mesh::TerminalDisplayCommand display_command;
  const mesh::TerminalDisplayParseResult result =
      mesh::parseTerminalDisplayCommand(arguments, display_command);

  if (result == mesh::TerminalDisplayParseResult::InvalidCategory
      || result == mesh::TerminalDisplayParseResult::InvalidValue) {
    terminalOutput().print(
        "  ERROR: use show [adverts|channels|emergency] [on|off]\r\n");
    return;
  }

  if (result == mesh::TerminalDisplayParseResult::StatusAll) {
    terminalOutput().printf("  Terminal display: adverts %s, channels %s, emergency %s\r\n",
                  _terminal_display.isEnabled(
                      mesh::TerminalDisplayCategory::Adverts) ? "on" : "off",
                  _terminal_display.isEnabled(
                      mesh::TerminalDisplayCategory::Channels) ? "on" : "off",
                  _terminal_display.isEnabled(
                      mesh::TerminalDisplayCategory::Emergency) ? "on" : "off");
    return;
  }

  if (result == mesh::TerminalDisplayParseResult::Updated) {
    _terminal_display.setEnabled(display_command.category,
                                 display_command.enabled);
  }
  terminalOutput().printf("  Terminal display: %s %s\r\n",
                mesh::terminalDisplayCategoryName(display_command.category),
                _terminal_display.isEnabled(display_command.category)
                    ? "on" : "off");
}

void MyMesh::sendTerminalChannelMessage(ChannelDetails& channel,
                                        const char* text) {
  const size_t prefix_len = strlen(_prefs.node_name) + 2;  // "name: "
  const size_t max_text_len = prefix_len < MAX_TEXT_LEN
      ? MAX_TEXT_LEN - prefix_len : 0;
  const size_t text_len = text == NULL ? 0 : strlen(text);
  if (text_len == 0) {
    terminalOutput().print("  ERROR: message is empty\r\n");
  } else if (text_len > max_text_len) {
    terminalOutput().printf("  ERROR: message must be 1-%u UTF-8 bytes for this node name\r\n",
                  (unsigned)max_text_len);
  } else if (sendGroupMessage(getRTCClock()->getCurrentTimeUnique(),
                              channel.channel, _prefs.node_name, text,
                              text_len)) {
    terminalOutput().printf("  Sent to %s.\r\n", channel.name);
  } else {
    terminalOutput().print("  ERROR: unable to send\r\n");
  }
}

void MyMesh::printTerminalSendStatus(const char* operation,
                                     const ContactInfo& recipient, int result,
                                     uint32_t timeout_millis) {
  terminalOutput().printf("  %s sent to %s (", operation, recipient.name);
  if (result == MSG_SEND_SENT_FLOOD) {
    terminalOutput().print("FLOOD");
  } else {
    const uint8_t hop_count = recipient.out_path_len & 63;
    if (hop_count == 0) {
      terminalOutput().print("zero-hop DIRECT");
    } else {
      const uint8_t hash_size = (recipient.out_path_len >> 6) + 1;
      terminalOutput().print("DIRECT via path ");
      for (uint8_t hop = 0; hop < hop_count; hop++) {
        if (hop != 0) terminalOutput().print(',');
        mesh::Utils::printHex(
            terminalOutput(), &recipient.out_path[(size_t)hop * hash_size], hash_size);
      }
      terminalOutput().printf("; %u %s, %u-byte hashes", (unsigned)hop_count,
                    hop_count == 1 ? "hop" : "hops", (unsigned)hash_size);
    }
  }
  terminalOutput().printf(", timeout %lu ms)\r\n", (unsigned long)timeout_millis);
}

void MyMesh::clearTerminalLogin() {
  if (_terminal_login_pending && pending_login != 0
      && memcmp(&pending_login, _terminal_login_key,
                sizeof(_terminal_login_key)) == 0) {
    pending_login = 0;
  }
  _terminal_login_pending = false;
  memset(_terminal_login_key, 0, sizeof(_terminal_login_key));
  _terminal_login_expires_at = 0;
  _terminal_login_target[0] = 0;
}

void MyMesh::serviceTerminalLogin() {
  if (!_terminal_login_pending) return;
  const unsigned long now = _ms->getMillis();
  if (_terminal_login_expires_at != now
      && !millisHasNowPassed(_terminal_login_expires_at)) {
    return;
  }

  if (hasTerminalOutput()) {
    terminalOutput().printf("\r\n  ERROR: login to %s timed out (wrong password or no response).\r\n> ",
                  _terminal_login_target);
  }
  clearTerminalLogin();
}

void MyMesh::sendTerminalLogin(ContactInfo& recipient,
                               const char* password) {
  serviceTerminalLogin();
  if (_terminal_login_pending) {
    terminalOutput().printf("  ERROR: login to %s is still pending\r\n",
                  _terminal_login_target);
    return;
  }

  const size_t password_len = password == NULL ? 0 : strlen(password);
  if (password_len == 0 || password_len > 15) {
    terminalOutput().print("  ERROR: password must be 1-15 UTF-8 bytes\r\n");
    return;
  }

  uint32_t est_timeout = 0;
  const int result = sendLogin(recipient, password, est_timeout);
  if (result == MSG_SEND_FAILED) {
    terminalOutput().print("  ERROR: unable to send login\r\n");
    return;
  }

  clearPendingReqs();
  memcpy(&pending_login, recipient.id.pub_key, sizeof(pending_login));
  _terminal_login_pending = true;
  memcpy(_terminal_login_key, recipient.id.pub_key,
         sizeof(_terminal_login_key));
  const uint32_t timeout = est_timeout + est_timeout / 5;
  _terminal_login_expires_at = futureMillis(timeout);
  StrHelper::strzcpy(_terminal_login_target, recipient.name,
                     sizeof(_terminal_login_target));
  printTerminalSendStatus("Login", recipient, result, timeout);
}

void MyMesh::clearTerminalCommand() {
  _terminal_command.clear();
  _terminal_command_target[0] = 0;
}

void MyMesh::serviceTerminalCommand() {
  uint32_t elapsed_millis = 0;
  if (!_terminal_command.expire(_ms->getMillis(), elapsed_millis)) return;

  if (hasTerminalOutput()) {
    terminalOutput().printf("\r\n  ERROR: command to %s timed out after %lu ms.\r\n> ",
                  _terminal_command_target, (unsigned long)elapsed_millis);
  }
  _terminal_command_target[0] = 0;
}

void MyMesh::sendTerminalCommand(ContactInfo& recipient,
                                 const char* command) {
  serviceTerminalLogin();
  if (_terminal_login_pending) {
    terminalOutput().printf("  ERROR: login to %s is still pending\r\n",
                  _terminal_login_target);
    return;
  }

  serviceTerminalCommand();
  if (_terminal_command.isPending()) {
    terminalOutput().printf("  ERROR: command to %s is still pending\r\n",
                  _terminal_command_target);
    return;
  }

  // Be defensive if a caller passes the complete terminal line instead of
  // the already-parsed argument. Never transmit the local "cmd" wrapper.
  const char* on_air_command = command;
  const char* nested_command = NULL;
  if (mesh::cli::parseTerminalArgumentCommand(
          command, "cmd", nested_command)
      == mesh::cli::TerminalArgumentCommandMatch::Valid) {
    on_air_command = nested_command;
  }

  const size_t command_len =
      on_air_command == NULL ? 0 : strlen(on_air_command);
  if (command_len == 0 || command_len > MAX_CORRELATED_CLI_TEXT_LEN) {
    terminalOutput().printf("  ERROR: remote command must be 1-%u UTF-8 bytes\r\n",
                  (unsigned)MAX_CORRELATED_CLI_TEXT_LEN);
    return;
  }

  const uint32_t logical_request_id =
      getRTCClock()->getCurrentTimeUnique();
  const uint32_t timestamp = getRTCClock()->getCurrentTimeUnique();
  const uint32_t command_started_at = _ms->getMillis();
  uint32_t est_timeout = 0;
  // Legacy server roles used CLI_DATA for requests as well as replies. Keep
  // that interoperable request form for deployed Repeaters, Rooms, and
  // Sensors; Chat/Companion nodes need the explicit CLI_COMMAND direction.
  const bool is_server_role = recipient.type == ADV_TYPE_REPEATER
      || recipient.type == ADV_TYPE_ROOM
      || recipient.type == ADV_TYPE_SENSOR;
  const uint8_t txt_type = is_server_role
      ? TXT_TYPE_CLI_DATA : TXT_TYPE_CLI_COMMAND;
  const int result = sendCommandData(recipient, timestamp, 0,
                                     txt_type, on_air_command,
                                     est_timeout, logical_request_id);
  if (result == MSG_SEND_FAILED) {
    terminalOutput().print("  ERROR: unable to send remote command\r\n");
    return;
  }

  _terminal_command.begin(recipient.id.pub_key, command_started_at,
                          est_timeout);
  StrHelper::strzcpy(_terminal_command_target, recipient.name,
                     sizeof(_terminal_command_target));

  printTerminalSendStatus("Remote command", recipient, result, est_timeout);
}

void MyMesh::clearTerminalTrace() {
  _terminal_trace_pending = false;
  _terminal_trace_hash_size = 0;
  _terminal_trace_tag = 0;
  _terminal_trace_auth = 0;
  _terminal_trace_sent_at = 0;
  _terminal_trace_expires_at = 0;
  _terminal_trace_target[0] = 0;
}

void MyMesh::serviceTerminalTrace() {
  if (!_terminal_trace_pending) return;
  const unsigned long now = _ms->getMillis();
  if (_terminal_trace_expires_at != now
      && !millisHasNowPassed(_terminal_trace_expires_at)) {
    return;
  }

  if (hasTerminalOutput()) {
    terminalOutput().printf("\r\n  ERROR: trace to %s timed out.\r\n> ",
                  _terminal_trace_target);
  }
  clearTerminalTrace();
}

void MyMesh::sendTerminalTraceRoute(const uint8_t* route, uint8_t hash_size,
                                    uint8_t hop_count, const char* target) {
  serviceTerminalTrace();
  if (_terminal_trace_pending) {
    terminalOutput().printf("  ERROR: trace to %s is still pending\r\n",
                  _terminal_trace_target);
    return;
  }
  if (route == NULL || hop_count == 0 || hop_count >= MAX_PATH_SIZE) {
    terminalOutput().print("  ERROR: trace path must contain 1-63 prefixes\r\n");
    return;
  }

  const size_t route_byte_len = static_cast<size_t>(hash_size) * hop_count;
  if (route_byte_len > MAX_PACKET_PAYLOAD - 9) {
    terminalOutput().print("  ERROR: trace path is too long\r\n");
    return;
  }

  const uint8_t flags = mesh::traceFlagsForHashSize(hash_size);
  if (flags == 0xFF) {
    terminalOutput().print("  ERROR: trace hash size must be 1, 2, or 4 bytes\r\n");
    return;
  }

  const uint32_t airtime = _radio->getEstAirtimeFor(9 + route_byte_len + 2);
  if (airtime == 0) {
    terminalOutput().print("  ERROR: unable to estimate trace timeout\r\n");
    return;
  }
  const uint32_t base_timeout =
      calcDirectTimeoutMillisFor(airtime, hop_count);
  uint32_t trace_timeout = 0;
  if (!mesh::calculateTerminalTraceTimeoutMillis(base_timeout,
                                                 trace_timeout)) {
    terminalOutput().print("  ERROR: trace timeout is out of range\r\n");
    return;
  }

  uint32_t tag = 0;
  uint32_t auth = 0;
  getRNG()->random((uint8_t*)&tag, sizeof(tag));
  getRNG()->random((uint8_t*)&auth, sizeof(auth));
  mesh::Packet* packet = createTrace(tag, auth, flags);
  if (packet == NULL) {
    terminalOutput().print("  ERROR: unable to allocate trace packet\r\n");
    return;
  }

  if (!sendDirect(packet, route, static_cast<uint8_t>(route_byte_len))) {
    terminalOutput().print("  ERROR: unable to send trace\r\n");
    return;
  }

  _terminal_trace_pending = true;
  _terminal_trace_hash_size = hash_size;
  _terminal_trace_tag = tag;
  _terminal_trace_auth = auth;
  _terminal_trace_sent_at = _ms->getMillis();
  _terminal_trace_expires_at = futureMillis(trace_timeout);
  StrHelper::strzcpy(_terminal_trace_target, target,
                     sizeof(_terminal_trace_target));
  terminalOutput().printf("  Trace sent to %s (%u route hops, timeout %lu ms)\r\n",
                target, (unsigned)hop_count,
                (unsigned long)trace_timeout);
}

void MyMesh::sendTerminalTrace(ContactInfo& recipient) {
  if (recipient.out_path_len == OUT_PATH_UNKNOWN
      || !mesh::Packet::isValidPathLen(recipient.out_path_len)) {
    terminalOutput().print("  ERROR: recipient has no valid direct path\r\n");
    return;
  }

  const bool include_endpoint = recipient.type == ADV_TYPE_REPEATER
      || recipient.type == ADV_TYPE_ROOM;
  mesh::RoundTripTracePath route;
  if (!mesh::buildRoundTripTracePath(
          recipient.out_path, recipient.out_path_len, recipient.id.pub_key,
          include_endpoint, _terminal_tmp_buf, MAX_PACKET_PAYLOAD - 9,
          route)) {
    terminalOutput().print("  ERROR: recipient has no traceable round-trip path\r\n");
    return;
  }
  if (route.hop_count >= MAX_PATH_SIZE) {
    terminalOutput().print("  ERROR: round-trip trace path is too long\r\n");
    return;
  }

  sendTerminalTraceRoute(_terminal_tmp_buf, route.hash_size, route.hop_count,
                         recipient.name);
}

void MyMesh::sendTerminalRawTrace(const char* arguments) {
  mesh::RawTracePath route;
  const mesh::RawTracePathParseResult parsed = mesh::parseRawTracePath(
      arguments, _terminal_tmp_buf, MAX_PACKET_PAYLOAD - 9,
      MAX_PATH_SIZE - 1, route);
  switch (parsed) {
    case mesh::RawTracePathParseResult::Valid:
      break;
    case mesh::RawTracePathParseResult::MissingHashSize:
    case mesh::RawTracePathParseResult::MissingPrefixes:
      terminalOutput().print("  ERROR: use trace path <1|2|4> <prefixes...>\r\n");
      return;
    case mesh::RawTracePathParseResult::InvalidHashSize:
      terminalOutput().print("  ERROR: trace hash size must be 1, 2, or 4 bytes\r\n");
      return;
    case mesh::RawTracePathParseResult::InvalidPrefix:
      terminalOutput().printf("  ERROR: every prefix must be exactly %u hex digits\r\n",
                    (unsigned)route.hash_size * 2);
      return;
    case mesh::RawTracePathParseResult::TooManyHops:
      terminalOutput().print("  ERROR: trace path must contain at most 63 prefixes\r\n");
      return;
    case mesh::RawTracePathParseResult::RouteTooLong:
      terminalOutput().print("  ERROR: trace path is too long\r\n");
      return;
  }

  char target[32];
  snprintf(target, sizeof(target), "raw %u-byte path",
           (unsigned)route.hash_size);
  sendTerminalTraceRoute(_terminal_tmp_buf, route.hash_size, route.hop_count,
                         target);
}

void MyMesh::handleTerminalCommand(char* command) {
  while (*command == ' ' || *command == '\t') command++;
  if (*command == 0) return;
  mesh::cli::normalizeCommandVerb(command);

  char local_reply[160];
  if (handleLocalControlCommand(command, local_reply, sizeof(local_reply))) {
    terminalOutput().printf("  %s\r\n", local_reply);
    return;
  }

  mesh::cli::TerminalChannelMessage channel_message;
  const mesh::cli::TerminalChannelCommandMatch channel_match =
      mesh::cli::parseTerminalChannelMessage(command, channel_message);
  const char* login_password = NULL;
  const mesh::cli::TerminalArgumentCommandMatch login_match =
      mesh::cli::parseTerminalArgumentCommand(command, "login",
                                              login_password);
  const char* remote_command = NULL;
  const mesh::cli::TerminalArgumentCommandMatch command_match =
      mesh::cli::parseTerminalArgumentCommand(command, "cmd",
                                              remote_command);
  const char* path_spec = NULL;
  const mesh::cli::TerminalArgumentCommandMatch path_match =
      mesh::cli::parseTerminalArgumentCommand(command, "path", path_spec);
  const char* recipient_prefix = NULL;
  const mesh::cli::TerminalArgumentCommandMatch recipient_match =
      mesh::cli::parseTerminalArgumentCommand(command, "to",
                                              recipient_prefix);
  const char* display_arguments = NULL;
  const mesh::cli::TerminalArgumentCommandMatch display_match =
      mesh::cli::parseTerminalArgumentCommand(command, "show",
                                              display_arguments);

  if (display_match != mesh::cli::TerminalArgumentCommandMatch::NoMatch) {
    handleTerminalDisplayCommand(
        display_match == mesh::cli::TerminalArgumentCommandMatch::Valid
            ? display_arguments : NULL);
  } else if (strcmp(command, "channels") == 0) {
    listTerminalChannels();
  } else if (channel_match != mesh::cli::TerminalChannelCommandMatch::NoMatch) {
    if (channel_match != mesh::cli::TerminalChannelCommandMatch::Valid) {
      terminalOutput().print("  ERROR: use channel <name-or-slot> <message>\r\n");
      return;
    }

    ChannelDetails channel;
    bool found = false;
    size_t requested_index = 0;
    if (mesh::cli::parseTerminalChannelIndex(
            channel_message, MAX_GROUP_CHANNELS, requested_index)) {
      found = getChannel((int)requested_index, channel)
          && channel.name[0] != 0;
    } else {
      for (int i = 0; i < MAX_GROUP_CHANNELS; i++) {
        if (getChannel(i, channel) && channel.name[0] != 0
            && mesh::cli::terminalChannelNameMatches(channel_message,
                                                     channel.name)) {
          found = true;
          break;
        }
      }
    }

    if (!found) {
      terminalOutput().print("  ERROR: channel not found (use 'channels')\r\n");
    } else {
      sendTerminalChannelMessage(channel, channel_message.text);
    }
  } else if (strncmp(command, "send ", 5) == 0) {
    ContactInfo* recipient = getTerminalRecipient();
    const char* text = command + 5;
    if (recipient == NULL) {
      terminalOutput().print("  ERROR: no recipient selected (use 'to' first)\r\n");
    } else if (*text == 0 || strlen(text) > MAX_TEXT_LEN) {
      terminalOutput().printf("  ERROR: message must be 1-%u characters\r\n", (unsigned)MAX_TEXT_LEN);
    } else {
      uint32_t expected_ack = 0;
      uint32_t est_timeout = 0;
      uint32_t message_timestamp = getRTCClock()->getCurrentTimeUnique();
      uint8_t text_fingerprint[MAX_HASH_SIZE] = { 0 };
      uint8_t packet_retry_key[MAX_HASH_SIZE] = { 0 };
      mesh::Utils::sha256(text_fingerprint, sizeof(text_fingerprint),
                          recipient->id.pub_key, PUB_KEY_SIZE,
                          (const uint8_t*)text, strlen(text));
      AckTableEntry* replacement_entry =
          findPendingTextMessage(text_fingerprint, message_timestamp);
      int result = sendMessage(*recipient, message_timestamp, 0, text,
                               expected_ack, est_timeout, packet_retry_key,
                               NULL, text_fingerprint);
      if (result == MSG_SEND_FAILED) {
        terminalOutput().print("  ERROR: unable to send\r\n");
      } else {
        rememberTerminalAck(*recipient, text, message_timestamp, expected_ack,
                            est_timeout, packet_retry_key, replacement_entry);
        terminalOutput().printf("  message sent - %s\r\n",
                      result == MSG_SEND_SENT_FLOOD ? "FLOOD" : "DIRECT");
      }
    }
  } else if (strncmp(command, "public ", 7) == 0) {
    ChannelDetails channel;
    const char* text = command + 7;
    if (!getChannel(0, channel) || channel.name[0] == 0) {
      terminalOutput().print("  ERROR: Public channel is unavailable\r\n");
    } else {
      sendTerminalChannelMessage(channel, text);
    }
  } else if (strcmp(command, "list") == 0 || strncmp(command, "list ", 5) == 0) {
    int count = command[4] == ' ' ? atoi(command + 5) : 0;
    scanRecentContacts(count, this);
  } else if (strcmp(command, "clock") == 0) {
    DateTime dt(getRTCClock()->getCurrentTime());
    terminalOutput().printf("%02d:%02d - %d/%d/%d UTC\r\n",
                  dt.hour(), dt.minute(), dt.day(), dt.month(), dt.year());
  } else if (strncmp(command, "time ", 5) == 0) {
    uint32_t timestamp = strtoul(command + 5, NULL, 10);
    uint32_t current = getRTCClock()->getCurrentTime();
    if (timestamp >= current) {
      getRTCClock()->setCurrentTime(timestamp);
#ifdef COMPANION_MESH_CLOCK_SYNC
      _clock_sync.onManualClockSet();
#endif
      terminalOutput().print("  OK - clock set\r\n");
    } else {
      terminalOutput().print("  ERROR: clock cannot go backwards\r\n");
    }
  } else if (recipient_match
             == mesh::cli::TerminalArgumentCommandMatch::Valid) {
    ContactInfo* recipient = NULL;
    if (strlen(recipient_prefix) < sizeof(ContactInfo::name)) {
      recipient = searchContactsByPrefix(recipient_prefix);
    }
    if (recipient == NULL || recipient->type == ADV_TYPE_NONE) {
      terminalOutput().print("  ERROR: name prefix not found\r\n");
    } else {
      memcpy(_terminal_recipient_key, recipient->id.pub_key, PUB_KEY_SIZE);
      _terminal_recipient_set = true;
      terminalOutput().printf("  Recipient %s selected\r\n", recipient->name);
    }
  } else if (recipient_match
             == mesh::cli::TerminalArgumentCommandMatch::MissingArgument) {
    ContactInfo* recipient = getTerminalRecipient();
    if (recipient != NULL) {
      terminalOutput().printf("  Current recipient: %s\r\n", recipient->name);
    } else {
      terminalOutput().print("  No recipient selected\r\n");
    }
  } else if (path_match
             != mesh::cli::TerminalArgumentCommandMatch::NoMatch) {
    ContactInfo* recipient = getTerminalRecipient();
    if (recipient == NULL) {
      terminalOutput().print("  ERROR: no recipient selected (use 'to' first)\r\n");
    } else {
      handleTerminalPath(
          *recipient,
          path_match == mesh::cli::TerminalArgumentCommandMatch::Valid
              ? path_spec : NULL);
    }
  } else if (login_match
             != mesh::cli::TerminalArgumentCommandMatch::NoMatch) {
    ContactInfo* recipient = getTerminalRecipient();
    if (login_match
        != mesh::cli::TerminalArgumentCommandMatch::Valid) {
      terminalOutput().print("  ERROR: use login <admin-password>\r\n");
    } else if (recipient == NULL) {
      terminalOutput().print("  ERROR: no recipient selected (use 'to' first)\r\n");
    } else {
      sendTerminalLogin(*recipient, login_password);
    }
  } else if (command_match
             != mesh::cli::TerminalArgumentCommandMatch::NoMatch) {
    ContactInfo* recipient = getTerminalRecipient();
    if (command_match
        != mesh::cli::TerminalArgumentCommandMatch::Valid) {
      terminalOutput().print("  ERROR: use cmd <remote-command>\r\n");
    } else if (recipient == NULL) {
      terminalOutput().print("  ERROR: no recipient selected (use 'to' first)\r\n");
    } else {
      sendTerminalCommand(*recipient, remote_command);
    }
  } else if (strcmp(command, "trace path") == 0
             || strncmp(command, "trace path ", 11) == 0
             || strncmp(command, "trace path\t", 11) == 0) {
    sendTerminalRawTrace(command + 10);
  } else if (strcmp(command, "trace") == 0
             || strncmp(command, "trace ", 6) == 0) {
    ContactInfo* recipient = NULL;
    if (command[5] == ' ') {
      const char* prefix = command + 6;
      while (*prefix == ' ') prefix++;
      if (*prefix != 0 && strlen(prefix) < sizeof(ContactInfo::name)) {
        recipient = searchContactsByPrefix(prefix);
      }
      if (recipient == NULL || recipient->type == ADV_TYPE_NONE) {
        terminalOutput().print("  ERROR: recipient prefix not found\r\n");
        return;
      }
    } else {
      recipient = getTerminalRecipient();
      if (recipient == NULL) {
        terminalOutput().print("  ERROR: no recipient selected (use 'to' first)\r\n");
        return;
      }
    }
    sendTerminalTrace(*recipient);
  } else if (strcmp(command, "advert") == 0) {
    terminalOutput().print(advert() ? "  advert sent (zero hop)\r\n"
                          : "  ERROR: unable to send advert\r\n");
  } else if (strcmp(command, "reset path") == 0) {
    ContactInfo* recipient = getTerminalRecipient();
    if (recipient == NULL) {
      terminalOutput().print("  ERROR: no recipient selected\r\n");
    } else {
      resetPathTo(*recipient);
      scheduleContactWrite(*recipient);
      terminalOutput().print("  Done.\r\n");
    }
  } else if (strcmp(command, "card") == 0) {
    mesh::Packet* packet = _prefs.advert_loc_policy == ADVERT_LOC_NONE
        ? createSelfAdvert(_prefs.node_name)
        : createSelfAdvert(_prefs.node_name, sensors.node_lat, sensors.node_lon);
    if (packet == NULL) {
      terminalOutput().print("  ERROR: unable to create card\r\n");
    } else {
      packet->header |= ROUTE_TYPE_FLOOD;
      uint8_t raw_len = packet->writeTo(_terminal_tmp_buf);
      releasePacket(packet);
      terminalOutput().print("meshcore://");
      mesh::Utils::printHex(terminalOutput(), _terminal_tmp_buf, raw_len);
      terminalOutput().print("\r\n");
    }
  } else if (strncmp(command, "import ", 7) == 0) {
    importTerminalCard(command + 7);
  } else if (strcmp(command, "powersaving") == 0
             || strcmp(command, "get powersaving") == 0) {
    terminalOutput().printf("  powersaving %s\r\n",
                  _prefs.powersaving_enabled ? "on" : "off");
#if MESH_USB_LOGGING_AVAILABLE
  } else if (strcmp(command, "get usb.logging") == 0) {
    terminalOutput().printf(
        "  usb.logging %s; port: %s%s\r\n",
        mesh::isUsbLoggingEnabled() ? "on" : "off",
        mesh::usbLoggingPortDescription(),
        mesh::usbLoggingInterfaceRestartRequired()
            ? " (reboot required to change USB interfaces)" : "");
#endif
  } else if (strncmp(command, "powersaving ", 12) == 0) {
    char reply[160];
    applyAndSavePowerSaving(command + 12, reply);
    terminalOutput().printf("  %s\r\n", reply);
  } else if (strcmp(command, "get radio.rxps.config") == 0) {
    if (!radio_driver.supportsRxPowerSaving()) {
      terminalOutput().print("  ERROR: RX power saving is unsupported on this radio\r\n");
    } else {
      terminalOutput().printf(
          "  radio.rxps.config %s,level=%u,preamble=%u,rx=%lu,sleep=%lu\r\n",
          _prefs.rx_powersaving_enabled ? "on" : "off",
          (unsigned)_prefs.rx_ps_level,
          (unsigned)_prefs.rx_ps_preamble,
          (unsigned long)_prefs.rx_ps_rx_us,
          (unsigned long)_prefs.rx_ps_sleep_us);
    }
  } else if (strcmp(command, "get radio.rxps") == 0) {
    if (!radio_driver.supportsRxPowerSaving()) {
      terminalOutput().print("  ERROR: RX power saving is unsupported on this radio\r\n");
    } else {
      uint8_t effective_level = 0;
      uint8_t effective_preamble = 0;
      uint32_t effective_rx_us = _prefs.rx_ps_rx_us;
      uint32_t effective_sleep_us = _prefs.rx_ps_sleep_us;
      uint8_t active_sf = _prefs.sf;
      float active_bw = _prefs.bw;
#if COMPANION_FEATURE_TEMP_RADIO
      if (isTempRadioActive()) {
        active_sf = _temp_radio_sf;
        active_bw = _temp_radio_bw;
      }
#endif
      if (_prefs.rx_ps_level != 0) {
        recalcRxPowerSavingFromLevel(
            _prefs.rx_ps_level, active_sf, active_bw,
            _prefs.rx_ps_preamble, &effective_rx_us, &effective_sleep_us,
            &effective_level, &effective_preamble);
      }
      char effective_suffix[48] = {};
      const bool adjusted_level = effective_level != 0
          && effective_level != _prefs.rx_ps_level;
      const bool adjusted_preamble = effective_preamble != 0
          && effective_preamble != _prefs.rx_ps_preamble;
      if (adjusted_level && adjusted_preamble) {
        snprintf(effective_suffix, sizeof(effective_suffix),
                 ",effective-level=%u,effective-preamble=%u",
                 (unsigned)effective_level, (unsigned)effective_preamble);
      } else if (adjusted_level) {
        snprintf(effective_suffix, sizeof(effective_suffix),
                 ",effective-level=%u", (unsigned)effective_level);
      } else if (adjusted_preamble) {
        snprintf(effective_suffix, sizeof(effective_suffix),
                 ",effective-preamble=%u", (unsigned)effective_preamble);
      }
      terminalOutput().printf("  radio.rxps %s,level=%u,preamble=%u,rx=%lu,sleep=%lu%s%s\r\n",
                    _prefs.rx_powersaving_enabled ? "on" : "off",
                    (unsigned)_prefs.rx_ps_level,
                    (unsigned)_prefs.rx_ps_preamble,
                    (unsigned long)effective_rx_us,
                    (unsigned long)effective_sleep_us,
                    radio_driver.isRxPowerSavingContinuousFallback()
                        ? ",mode=continuous-fast" : "",
                    effective_suffix);
    }
#if defined(ESP32) && defined(WIFI_SSID)
  } else if (strcmp(command, "get wifi.powersave") == 0) {
    char reply[160];
    formatWiFiPowerSaving(reply, sizeof(reply));
    terminalOutput().printf("  %s\r\n", reply);
#endif
  } else if (strcmp(command, "get radio.rxgain") == 0) {
    if (!radio_driver.supportsRxBoostedGainMode()) {
      terminalOutput().print("  ERROR: RX boosted gain is unsupported on this radio\r\n");
    } else {
      terminalOutput().printf("  radio.rxgain %s\r\n",
                    _prefs.rx_boosted_gain ? "on" : "off");
    }
  } else if (strcmp(command, "get radio.fem.rxgain") == 0) {
    if (!board.canControlLoRaFemLna()) {
      terminalOutput().print("  ERROR: FEM RX gain control is unsupported on this board\r\n");
    } else {
      terminalOutput().printf("  FEM RX gain: %s\r\n",
                    board.isLoRaFemLnaEnabled() ? "on" : "off");
    }
  } else if (strcmp(command, "get radio.fem.txgain") == 0) {
    if (!board.canControlLoRaFemPaGain()) {
      terminalOutput().print("  ERROR: FEM TX gain control is unsupported on this board\r\n");
    } else {
      terminalOutput().printf("  FEM TX gain: %s\r\n",
                    board.isLoRaFemPaGainEnabled() ? "on" : "off");
    }
  } else if (strncmp(command, "set ", 4) == 0) {
    const char* config = command + 4;
#if MESH_USB_LOGGING_AVAILABLE
    if (strncmp(config, "usb.logging", 11) == 0
        && (config[11] == 0 || config[11] == ' '
            || config[11] == '\t')) {
      const char* value = config + 11;
      while (*value == ' ' || *value == '\t') value++;
      bool enabled = false;
      bool reboot_if_needed = false;
      bool valid = true;
      if (strcmp(value, "on") == 0) {
        enabled = true;
      } else if (strcmp(value, "off") == 0) {
        enabled = false;
      } else if (strcmp(value, "on reboot") == 0) {
        enabled = true;
        reboot_if_needed = true;
      } else if (strcmp(value, "off reboot") == 0) {
        enabled = false;
        reboot_if_needed = true;
      } else {
        valid = false;
      }

      if (!valid) {
        terminalOutput().print(
            "  ERROR: use set usb.logging <on|off> [reboot]\r\n");
      } else {
        _prefs.usb_logging_enabled = enabled ? 1 : 0;
        mesh::setUsbLoggingEnabled(enabled);
        if (!savePrefs()) {
          terminalOutput().print(
              "  ERROR: USB logging changed for this boot but save failed\r\n");
        } else if (!mesh::saveUsbLoggingBootPreference(enabled)) {
          terminalOutput().print(
              "  ERROR: setting saved, but next-boot USB interface state could not be saved\r\n");
        } else if (mesh::usbLoggingInterfaceRestartRequired()) {
          if (reboot_if_needed) {
            terminalOutput().printf(
                "  OK - USB logging %s (saved); rebooting to change USB interfaces\r\n",
                enabled ? "on" : "off");
            _scheduled_reboot_at = futureMillis(1000);
          } else {
            terminalOutput().printf(
                "  OK - USB logging %s (saved); reboot required to change USB interfaces\r\n",
                enabled ? "on" : "off");
          }
        } else {
          terminalOutput().printf("  OK - USB logging %s (saved)\r\n",
                                  enabled ? "on" : "off");
        }
      }
    } else
#endif
    if (strncmp(config, "powersaving ", 12) == 0) {
      char reply[160];
      applyAndSavePowerSaving(config + 12, reply);
      terminalOutput().printf("  %s\r\n", reply);
    } else if (strncmp(config, "radio.rxps ", 11) == 0) {
      char reply[160];
      applyAndSaveRxPowerSaving(config + 11, reply);
      terminalOutput().printf("  %s\r\n", reply);
#if defined(ESP32) && defined(WIFI_SSID)
    } else if (strncmp(config, "wifi.powersave", 14) == 0
               && (config[14] == 0 || config[14] == ' '
                   || config[14] == '\t')) {
      const char* value = config + 14;
      while (*value == ' ' || *value == '\t') value++;
      char reply[160];
      applyAndSaveWiFiPowerSaving(value, reply, sizeof(reply));
      terminalOutput().printf("  %s\r\n", reply);
#endif
    } else if (strncmp(config, "radio.rxgain", 12) == 0
               && (config[12] == 0 || config[12] == ' '
                   || config[12] == '\t')) {
      const char* value = config + 12;
      while (*value == ' ' || *value == '\t') value++;
      if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) {
        terminalOutput().print("  ERROR: use set radio.rxgain <on|off>\r\n");
      } else if (!radio_driver.supportsRxBoostedGainMode()) {
        terminalOutput().print("  ERROR: RX boosted gain is unsupported on this radio\r\n");
      } else if (!applyAndSaveRxBoostedGain(strcmp(value, "on") == 0)) {
        terminalOutput().print("  ERROR: radio busy; retry\r\n");
      } else {
        terminalOutput().printf("  OK - radio.rxgain %s\r\n", value);
      }
    } else if (strncmp(config, "af ", 3) == 0) {
      float parsed = 0.0f;
      if (!mesh::cli::parseDecimalStrict(config + 3, parsed)
          || parsed < 0.0f || parsed > 9.0f) {
        terminalOutput().print("  ERROR: airtime factor must be 0-9\r\n");
      } else {
        _prefs.airtime_factor = parsed;
        savePrefs();
        terminalOutput().print("  OK\r\n");
      }
    } else if (strncmp(config, "name ", 5) == 0 && config[5] != 0) {
      StrHelper::strncpy(_prefs.node_name, config + 5, sizeof(_prefs.node_name));
      savePrefs();
      terminalOutput().print("  OK\r\n");
    } else if (strncmp(config, "lat ", 4) == 0) {
      float parsed = 0.0f;
      if (!mesh::cli::parseDecimalStrict(config + 4, parsed)
          || parsed < -90.0f || parsed > 90.0f) {
        terminalOutput().print("  ERROR: latitude must be -90 to 90\r\n");
      } else {
        sensors.node_lat = parsed;
        savePrefs();
        terminalOutput().print("  OK\r\n");
      }
    } else if (strncmp(config, "lon ", 4) == 0) {
      float parsed = 0.0f;
      if (!mesh::cli::parseDecimalStrict(config + 4, parsed)
          || parsed < -180.0f || parsed > 180.0f) {
        terminalOutput().print("  ERROR: longitude must be -180 to 180\r\n");
      } else {
        sensors.node_lon = parsed;
        savePrefs();
        terminalOutput().print("  OK\r\n");
      }
    } else if (strncmp(config, "tx ", 3) == 0) {
      _prefs.tx_power_dbm = constrain(atoi(config + 3), -9, MAX_LORA_TX_POWER);
      savePrefs();
      terminalOutput().print("  OK - reboot to apply\r\n");
    } else if (strncmp(config, "freq ", 5) == 0) {
      float parsed = 0.0f;
      if (!mesh::cli::parseDecimalStrict(config + 5, parsed)
          || parsed < 150.0f || parsed > 2500.0f) {
        terminalOutput().print("  ERROR: frequency must be 150-2500 MHz\r\n");
      } else {
        _prefs.freq = parsed;
        savePrefs();
        terminalOutput().print("  OK - reboot to apply\r\n");
      }
    } else if (strncmp(config, "radio.fem.rxgain", 16) == 0
               && (config[16] == 0 || config[16] == ' '
                   || config[16] == '\t')) {
      const char* value = config + 16;
      while (*value == ' ' || *value == '\t') value++;
      if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) {
        terminalOutput().print("  ERROR: use set radio.fem.rxgain <on|off>\r\n");
      } else if (!board.canControlLoRaFemLna()) {
        terminalOutput().print("  ERROR: FEM RX gain control is unsupported on this board\r\n");
      } else if (!applyAndSaveFemRxGain(strcmp(value, "on") == 0)) {
        terminalOutput().print("  ERROR: failed to apply FEM RX gain\r\n");
      } else {
        terminalOutput().printf("  OK - FEM RX gain %s\r\n", value);
      }
    } else if (strncmp(config, "radio.fem.txgain", 16) == 0
               && (config[16] == 0 || config[16] == ' '
                   || config[16] == '\t')) {
      const char* value = config + 16;
      while (*value == ' ' || *value == '\t') value++;
      if (strcmp(value, "on") != 0 && strcmp(value, "off") != 0) {
        terminalOutput().print("  ERROR: use set radio.fem.txgain <on|off>\r\n");
      } else if (!board.canControlLoRaFemPaGain()) {
        terminalOutput().print("  ERROR: FEM TX gain control is unsupported on this board\r\n");
      } else if (!applyAndSaveFemTxGain(strcmp(value, "on") == 0)) {
        terminalOutput().print("  ERROR: failed to apply FEM TX gain\r\n");
      } else {
        terminalOutput().printf("  OK - FEM TX gain %s\r\n", value);
      }
    } else {
      // The terminal has a few presentation-specific setters above, while
      // handleCommand() owns the shared radio-pref and board-specific command
      // surface used by framed/rescue clients.  Use it only as the fallback so
      // Full Companion does not develop holes such as `set radio ...` without
      // changing the established terminal behavior of `set tx`, `set name`,
      // or the WiFi commands.
      if (handleCommand(command, 0, local_reply)) {
        terminalOutput().printf("  %s\r\n", local_reply);
      } else {
        terminalOutput().printf("  ERROR: unknown setting: %s\r\n", config);
      }
    }
  } else if (strcmp(command, "reboot") == 0) {
    terminalOutput().print("  OK - rebooting in 1 second\r\n");
    _scheduled_reboot_at = futureMillis(1000);
  } else if (strcmp(command, "ver") == 0) {
    terminalOutput().printf("Companion %s (protocol %u, build %s)\r\n",
                  FIRMWARE_VERSION, (unsigned)FIRMWARE_VER_CODE, FIRMWARE_BUILD_DATE);
  } else if (strcmp(command, "help") == 0) {
    terminalOutput().print("Commands:\r\n");
    terminalOutput().print("  board\r\n");
    terminalOutput().print("  version\r\n");
#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS
    terminalOutput().print("  memory\r\n");
#endif
    terminalOutput().print("  get display.rotation\r\n");
    terminalOutput().print("  set display.rotation <0|90|180|270>\r\n");
    terminalOutput().print("  set {name|lat|lon|freq|tx|af} {value}\r\n");
    terminalOutput().print("  get bluetooth.name\r\n");
    terminalOutput().print("  set bluetooth.name <name|default>\r\n");
    terminalOutput().print("  set pin <0-999999>\r\n");
    terminalOutput().print("  powersaving [on|off]\r\n");
#if MESH_USB_LOGGING_AVAILABLE
    terminalOutput().print("  get usb.logging\r\n");
    terminalOutput().print("  set usb.logging <on|off> [reboot]\r\n");
#endif
#if defined(ESP32) && defined(WIFI_SSID)
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
    terminalOutput().print("  get companion.transport\r\n");
    terminalOutput().print("  set companion.transport <wifi|ble>\r\n");
#endif
    terminalOutput().print("  get wifi.powersave\r\n");
    terminalOutput().print("  set wifi.powersave <none|min|max>\r\n");
#ifdef WITH_WEBCONFIG
    terminalOutput().print("  get wifi.{ssid|status}\r\n");
    terminalOutput().print("  set wifi.ssid <network name>\r\n");
    terminalOutput().print("  set wifi.pwd <password>\r\n");
    terminalOutput().print("  get webui\r\n");
    terminalOutput().print("  set webui <on|off>\r\n");
    terminalOutput().print("  start webconfig [ap]\r\n");
    terminalOutput().print("  stop webconfig\r\n");
#endif
#endif
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
    terminalOutput().print("  get espnow.channel\r\n");
    terminalOutput().print("  set espnow.channel <1-13>\r\n");
#endif
    terminalOutput().print("  get radio.rxps\r\n");
    terminalOutput().print("  get radio.rxps.config\r\n");
    terminalOutput().print("  set radio.rxps <off|on|level 1-10 [preamble 16|32]|rx_us sleep_us>\r\n");
    terminalOutput().print("  get radio.cad\r\n");
    terminalOutput().print("  set radio.cad <on|off>\r\n");
    terminalOutput().print("  set radio.cad timings <scan_ms|auto> <retry_ms|auto> <max_ms|auto>\r\n");
    terminalOutput().print("  get radio.rxgain\r\n");
    terminalOutput().print("  set radio.rxgain <on|off>\r\n");
    terminalOutput().print("  get radio.fem.rxgain\r\n");
    terminalOutput().print("  set radio.fem.rxgain <on|off>\r\n");
    terminalOutput().print("  get radio.fem.txgain\r\n");
    terminalOutput().print("  set radio.fem.txgain <on|off>\r\n");
    terminalOutput().print("  card\r\n");
    terminalOutput().print("  import <meshcore://card>\r\n");
    terminalOutput().print("  clock\r\n");
    terminalOutput().print("  time <epoch-seconds>\r\n");
    terminalOutput().print("  list [n]\r\n");
    terminalOutput().print("  show [adverts|channels|emergency] [on|off]\r\n");
    terminalOutput().print("  to [recipient name or prefix]\r\n");
    terminalOutput().print("  path [direct|clear|hops separated by spaces or commas]\r\n");
    terminalOutput().print("  send <text>\r\n");
    terminalOutput().print("  login <admin-password>\r\n");
    terminalOutput().print("  cmd <remote-command>\r\n");
    terminalOutput().print("  trace [recipient name or prefix]\r\n");
    terminalOutput().print("  trace path <1|2|4> <prefixes...>\r\n");
    terminalOutput().print("  advert\r\n");
    terminalOutput().print("  reset path\r\n");
    terminalOutput().print("  public <text>\r\n");
    terminalOutput().print("  channels\r\n");
    terminalOutput().print("  channel <name-or-slot> <text>\r\n");
#if COMPANION_FEATURE_TEMP_RADIO || COMPANION_FEATURE_OTA_CLI
#if COMPANION_FEATURE_TEMP_RADIO
    terminalOutput().print("  tempradio [freq,bw,sf,cr,minutes]\r\n");
    terminalOutput().print("  normalradio\r\n");
#endif
#if COMPANION_FEATURE_OTA_CLI
    terminalOutput().print("  ota {status|ls|announce|folder|config|...}\r\n");
#endif
#endif
    terminalOutput().print("  reboot\r\n");
    terminalOutput().print("  ver\r\n");
    if (_terminal_mode) {
      terminalOutput().print("  +++MESHCORE-TERM-STOP\r\n");
    } else {
      terminalOutput().print("  disconnect (closes the TCP terminal)\r\n");
    }
  } else if (handleCommand(command, 0, local_reply)) {
    // Fill the same safe shared-command surface for getters (`get name`,
    // `get radio`, `get tx`, and variant commands).  Terminal-only commands
    // above still win, including the richer `ver` response and reboot flow.
    terminalOutput().printf("  %s\r\n", local_reply);
  } else {
    terminalOutput().printf("  ERROR: unknown command: %s\r\n", command);
  }
}
#endif

void MyMesh::enterCLIRescue() {
  _cli_rescue = true;
  cli_command[0] = 0;
  Serial.println("========= CLI Rescue =========");
}

static bool isCompanionRadioPrefsCommand(const char* command) {
  if (command == NULL) return false;

  static const char* const exact_commands[] = {
    "get radio", "get freq", "get af", "get dutycycle", "get tx",
    "get rxdelay", "get path.hash.mode", "get multi.acks"
  };
  for (const char* candidate : exact_commands) {
    if (strcmp(command, candidate) == 0) return true;
  }

  static const char* const set_prefixes[] = {
    "set radio ", "set af ", "set dutycycle ", "set rxdelay ",
    "set path.hash.mode ", "set multi.acks "
  };
  for (const char* prefix : set_prefixes) {
    if (strncmp(command, prefix, strlen(prefix)) == 0) return true;
  }
  return false;
}

bool MyMesh::handleCommand(const char* command, uint32_t sender_timestamp,
                           char* reply) {
  if (command == NULL || reply == NULL) return false;
  size_t reply_capacity = 160;
  while (*command == ' ' || *command == '\t') command++;

  if (strlen(command) > 3 && command[2] == '|') {
    // Optional two-character request prefix used by Companion CLI clients.
    memcpy(reply, command, 3);
    reply += 3;
    reply_capacity -= 3;
    *reply = 0;
    command += 3;
    while (*command == ' ' || *command == '\t') command++;
  }

  if (handleLocalControlCommand(command, reply, reply_capacity)) return true;

  if (strncmp(command, "set tx ", 7) == 0) {
    int32_t parsed = 0;
    if (!mesh::cli::parseIntegerStrict(command + 7, parsed)) {
      strcpy(reply, "Error: invalid TX power");
    } else if (parsed < -9 || parsed > MAX_LORA_TX_POWER) {
      snprintf(reply, reply_capacity, "Error: TX power must be -9 to %d",
               MAX_LORA_TX_POWER);
    } else {
      const int8_t previous = _prefs.tx_power_dbm;
      const int8_t requested = static_cast<int8_t>(parsed);
      if (_radio_available && !radio_driver.setTxPower(requested)) {
        strcpy(reply, "Error: radio busy or TX power rejected");
      } else {
        _prefs.tx_power_dbm = requested;
        if (!savePrefs()) {
          _prefs.tx_power_dbm = previous;
          if (_radio_available) radio_driver.setTxPower(previous);
          strcpy(reply, "Error: TX power changed but save failed");
        } else {
          strcpy(reply, "OK");
        }
      }
    }
    return true;
  }

  if (isCompanionRadioPrefsCommand(command)) {
    const float previous_freq = _prefs.freq;
    const float previous_bw = _prefs.bw;
    const uint8_t previous_sf = _prefs.sf;
    const uint8_t previous_cr = _prefs.cr;
    const float previous_af = _prefs.airtime_factor;
    const float previous_rxdelay = _prefs.rx_delay_base;
    const uint8_t previous_hash_mode = _prefs.path_hash_mode;
    const uint8_t previous_multi_acks = _prefs.multi_acks;
    const uint32_t previous_rx_us = _prefs.rx_ps_rx_us;
    const uint32_t previous_sleep_us = _prefs.rx_ps_sleep_us;

    if (!_prefs.getRadioPrefs()->handleCommand(
            command, sender_timestamp, reply)) {
      return false;
    }
    if (_prefs.getRadioPrefs()->isDirty()) {
      if (strncmp(command, "set radio ", 10) == 0) {
        recalcRxPowerSavingFromLevel(
            _prefs.rx_ps_level, _prefs.sf, _prefs.bw,
            _prefs.rx_ps_preamble, &_prefs.rx_ps_rx_us,
            &_prefs.rx_ps_sleep_us);
      }
      if (!savePrefs()) {
        _prefs.freq = previous_freq;
        _prefs.bw = previous_bw;
        _prefs.sf = previous_sf;
        _prefs.cr = previous_cr;
        _prefs.airtime_factor = previous_af;
        _prefs.rx_delay_base = previous_rxdelay;
        _prefs.path_hash_mode = previous_hash_mode;
        _prefs.multi_acks = previous_multi_acks;
        _prefs.rx_ps_rx_us = previous_rx_us;
        _prefs.rx_ps_sleep_us = previous_sleep_us;
        _prefs.clearDirty();
        strcpy(reply, "Error: setting changed but save failed");
      }
    }
    return true;
  }

  // Hook for future variant-specific commands not covered by the shared,
  // runtime-aware FEM handlers above.
  if (board.handleCommand(command, sender_timestamp, reply)) {
    if (_prefs.isDirty() && !savePrefs()) {
      strcpy(reply, "Error: board setting changed but save failed");
    }
    return true;
  }

  if (strncmp(command, "set name ", 9) == 0) {
    const char* name = command + 9;
    if (!AdvertDataParser::isValidName(name)) {
      strcpy(reply, "Error, bad chars");
    } else {
      char previous[sizeof(_prefs.node_name)];
      memcpy(previous, _prefs.node_name, sizeof(previous));
      StrHelper::strncpy(_prefs.node_name, name, sizeof(_prefs.node_name));
      if (!savePrefs()) {
        memcpy(_prefs.node_name, previous, sizeof(_prefs.node_name));
        strcpy(reply, "Error: name changed but save failed");
      } else {
        strcpy(reply, "OK");
      }
    }
    return true;
  }
  if (strcmp(command, "get name") == 0) {
    snprintf(reply, reply_capacity, "> %s", _prefs.node_name);
    return true;
  }

  if (strcmp(command, "ver") == 0) {
    snprintf(reply, reply_capacity, "%s (Build: %s)", FIRMWARE_VERSION,
             FIRMWARE_BUILD_DATE);
    return true;
  }

  return false;
}

void MyMesh::checkCLIRescueCmd() {
  int len = strlen(cli_command);
  while (Serial.available() && len < sizeof(cli_command)-1) {
    char c = Serial.read();
    if (c != '\n') {
      cli_command[len++] = c;
      cli_command[len] = 0;
    }
    Serial.print(c);  // echo
  }
  if (len == sizeof(cli_command)-1) {  // command buffer full
    cli_command[sizeof(cli_command)-1] = '\r';
  }

  if (len > 0 && cli_command[len - 1] == '\r') {  // received complete line
    cli_command[len - 1] = 0;  // replace newline with C string null terminator

    reply_buf[0] = 0;
    if (handleCommand(cli_command, 0, reply_buf)) {
      // command was handled, print reply output
      Serial.print("  "); Serial.print(reply_buf); Serial.println();
    } else if (strcmp(cli_command, "rebuild") == 0) {
      bool success = _store->formatFileSystem();
      if (success) {
        _store->saveMainIdentity(self_id);
        savePrefs();
        saveContacts();
        saveChannels();
        Serial.println("  > erase and rebuild done");
      } else {
        Serial.println("  Error: erase failed");
      }
    } else if (strcmp(cli_command, "erase") == 0) {
      bool success = _store->formatFileSystem();
      if (success) {
        Serial.println("  > erase done");
      } else {
        Serial.println("  Error: erase failed");
      }
    } else if (memcmp(cli_command, "ls", 2) == 0) {

      // get path from command e.g: "ls /adafruit"
      const char *path = &cli_command[3];

      bool is_fs2 = false;
      if (memcmp(path, "UserData/", 9) == 0) {
        path += 8; // skip "UserData"
      } else if (memcmp(path, "ExtraFS/", 8) == 0) {
        path += 7; // skip "ExtraFS"
        is_fs2 = true;
      }
      Serial.printf("Listing files in %s\n", path);

      // log each file and directory
      File root = _store->openRead(path);
      if (is_fs2 == false) {
        if (root) {
          File file = root.openNextFile();
          while (file) {
            if (file.isDirectory()) {
              Serial.printf("[dir]  UserData%s/%s\n", path, file.name());
            } else {
              Serial.printf("[file] UserData%s/%s (%d bytes)\n", path, file.name(), file.size());
            }
            // move to next file
            file = root.openNextFile();
          }
          root.close();
        }
      }

      if (is_fs2 == true || strlen(path) == 0 || strcmp(path, "/") == 0) {
        if (_store->getSecondaryFS() != nullptr) {
          File root2 = _store->openRead(_store->getSecondaryFS(), path);
          File file = root2.openNextFile();
          while (file) {
            if (file.isDirectory()) {
              Serial.printf("[dir]  ExtraFS%s/%s\n", path, file.name());
            } else {
              Serial.printf("[file] ExtraFS%s/%s (%d bytes)\n", path, file.name(), file.size());
            }
            // move to next file
            file = root2.openNextFile();
          }
          root2.close();
        }
      }
    } else if (memcmp(cli_command, "cat", 3) == 0) {

      // get path from command e.g: "cat /contacts3"
      const char *path = &cli_command[4];

      bool is_fs2 = false;
      if (memcmp(path, "UserData/", 9) == 0) {
        path += 8; // skip "UserData"
      } else if (memcmp(path, "ExtraFS/", 8) == 0) {
        path += 7; // skip "ExtraFS"
        is_fs2 = true;
      } else {
        Serial.println("Invalid path provided, must start with UserData/ or ExtraFS/");
        cli_command[0] = 0;
        return;
      }

      // log file content as hex
      File file = _store->openRead(path);
      if (is_fs2 == true) {
        file = _store->openRead(_store->getSecondaryFS(), path);
      }
      if(file){

        // get file content
        int file_size = file.available();
        uint8_t buffer[file_size];
        file.read(buffer, file_size);

        // print hex
        mesh::Utils::printHex(Serial, buffer, file_size);
        Serial.print("\n");

        file.close();

      }

    } else if (memcmp(cli_command, "rm ", 3) == 0) {
      // get path from command e.g: "rm /adv_blobs"
      const char *path = &cli_command[3];
      MESH_DEBUG_PRINTLN("Removing file: %s", path);
      // ensure path is not empty, or root dir
      if(!path || strlen(path) == 0 || strcmp(path, "/") == 0){
        Serial.println("Invalid path provided");
      } else {
      bool is_fs2 = false;
      if (memcmp(path, "UserData/", 9) == 0) {
        path += 8; // skip "UserData"
      } else if (memcmp(path, "ExtraFS/", 8) == 0) {
        path += 7; // skip "ExtraFS"
        is_fs2 = true;
      }

        // remove file
        bool removed;
        if (is_fs2) {
          MESH_DEBUG_PRINTLN("Removing file from ExtraFS: %s", path);
          removed = _store->removeFile(_store->getSecondaryFS(), path);
        } else {
          MESH_DEBUG_PRINTLN("Removing file from UserData: %s", path);
          removed = _store->removeFile(path);
        }
        if(removed){
          Serial.println("File removed");
        } else {
          Serial.println("Failed to remove file");
        }

      }

    } else if (strcmp(cli_command, "reboot") == 0) {
      board.reboot();  // doesn't return
    } else {
      Serial.println("  Error: unknown command");
    }

    cli_command[0] = 0;  // reset command buffer
  }
}

void MyMesh::checkSerialInterface() {
  size_t len = _serial->checkRecvFrame(cmd_frame);
  if (!_serial->isConnected()) {
    stopContactsIterator();
    cancelPendingRadioParamApply();
    return;
  }

  if (len > 0) {
    handleCmdFrame(len);
  } else if (_iter_started              // check if our ContactsIterator is 'running'
             && !_serial->isWriteBusy() // don't spam the Serial Interface too quickly!
  ) {
    ContactInfo contact;
    if (_iter.hasNext(this, contact)) {
      if (contact.lastmod > _iter_filter_since) { // apply the 'since' filter
        writeContactRespFrame(RESP_CODE_CONTACT, contact);
        if (contact.lastmod > _most_recent_lastmod) {
          _most_recent_lastmod = contact.lastmod; // save for the RESP_CODE_END_OF_CONTACTS frame
        }
      }
    } else { // EOF
      out_frame[0] = RESP_CODE_END_OF_CONTACTS;
      memcpy(&out_frame[1], &_most_recent_lastmod,
             4); // include the most recent lastmod, so app can update their 'since'
      _serial->writeFrame(out_frame, 5);
      stopContactsIterator();
    }
  //} else if (!_serial->isWriteBusy()) {
  //  checkConnections();    // TODO - deprecate the 'Connections' stuff
  }
}

void MyMesh::loop() {
#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
  if (_mqtt_bridge) _mqtt_bridge->servicePendingClockCorrection();
#endif
  if (_scheduled_reboot_at != 0
      && millisHasNowPassed(_scheduled_reboot_at)) {
    _scheduled_reboot_at = 0;
    board.reboot();
    return;
  }
#if COMPANION_FEATURE_TEMP_RADIO
  serviceTempRadio();
#endif
  BaseChatMesh::loop();
#ifdef COMPANION_MESH_CLOCK_SYNC
  _clock_sync.loop();
#endif
#ifdef ENABLE_USB_INTERFACE
  serviceTerminalLogin();
  serviceTerminalCommand();
  serviceTerminalTrace();
#endif
  if (!command_radio_apply_pending && saved_radio_apply_pending && !hasOutbound()
#if COMPANION_FEATURE_TEMP_RADIO
      && !_temp_radio_applied && _temp_radio_set_at == 0
      && _temp_radio_revert_at == 0
#endif
      && (!radio_apply_retry_at || millisHasNowPassed(radio_apply_retry_at))) {
    // A power-saving wake can enter begin() with a complete packet already
    // waiting. Preserve that packet, then apply the persisted radio settings
    // once the receive/response path is idle.
    radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain);
    if (applySavedRadioParams()) {
      radio_driver.setTxPower(_prefs.tx_power_dbm);
      saved_radio_apply_pending = false;
      radio_apply_retry_at = 0;
      radio_apply_failures = 0;
    } else {
      radio_apply_retry_at = futureMillis(nextRadioApplyRetryDelay(radio_apply_failures));
    }
  }
  if (has_next_ack_expiry
      && (next_ack_expiry == _ms->getMillis() || millisHasNowPassed(next_ack_expiry))) {
    expireExpectedAcks();
  }
  if (emergency_client_repeat_packet != NULL && millisHasNowPassed(emergency_client_repeat_send_at)) {
    mesh::Packet* pkt = emergency_client_repeat_packet;
    emergency_client_repeat_packet = NULL;
    sendPacket(pkt, 1, 0);
  }

  if (_cli_rescue) {
    checkCLIRescueCmd();
  } else {
    checkSerialInterface();
  }
  servicePendingRadioParamApply();

  // is there are pending dirty contacts write needed?
  if (dirty_contacts_expiry && millisHasNowPassed(dirty_contacts_expiry)) {
    const bool success = _store->serviceContactWrites(this, save_filter);
    if (!success || _store->hasPendingContactWrites()) {
      dirty_contacts_expiry = futureMillis(success ? CONTACT_PAGE_WRITE_GAP : 1000);
    } else {
      dirty_contacts_expiry = 0;
    }
  }

#ifdef DISPLAY_CLASS
  if (_ui) _ui->setHasConnection(_serial->isConnected());
#endif
}

bool MyMesh::advert() {
  mesh::Packet* pkt;
  if (_prefs.advert_loc_policy == ADVERT_LOC_NONE) {
    pkt = createSelfAdvert(_prefs.node_name);
  } else {
    pkt = createSelfAdvert(_prefs.node_name, sensors.node_lat, sensors.node_lon);
  }
  if (pkt) {
    sendZeroHop(pkt);
    return true;
  } else {
    return false;
  }
}

// To check if there is pending work
bool MyMesh::hasPendingWork() const {
  if (_radio_available
      && (radio_driver.isWatchdogObserving()
          || radio_driver.isCalibratingNoiseFloor())) return true;
  return (_serial != NULL && _serial->hasPendingIO())
      || (_iter_started && _serial != NULL && _serial->isConnected())
      || command_radio_apply_pending
      || hasQueuedWorkDue() || hasRetryWorkDue()
      || (saved_radio_apply_pending
          && (!radio_apply_retry_at || millisHasNowPassed(radio_apply_retry_at)))
      || (dirty_contacts_expiry != 0 && millisHasNowPassed(dirty_contacts_expiry))
      || (emergency_client_repeat_packet != NULL
          && millisHasNowPassed(emergency_client_repeat_send_at))
#if COMPANION_FEATURE_TEMP_RADIO
      || (_temp_radio_set_at != 0 && millisHasNowPassed(_temp_radio_set_at))
      || (_temp_radio_revert_at != 0 && millisHasNowPassed(_temp_radio_revert_at))
      || (_temp_radio_retry_at != 0 && millisHasNowPassed(_temp_radio_retry_at))
#endif
      ;
}
