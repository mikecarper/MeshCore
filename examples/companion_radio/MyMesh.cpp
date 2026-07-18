#include "MyMesh.h"

#include <Arduino.h> // needed for PlatformIO
#include <Mesh.h>

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

#ifndef RXPS_FIXED_ENABLED
#define RXPS_FIXED_ENABLED 0
#endif
#ifndef RXPS_FIXED_LEVEL
#define RXPS_FIXED_LEVEL 2
#endif
#ifndef RXPS_FIXED_PREAMBLE
#define RXPS_FIXED_PREAMBLE 16
#endif

#if RXPS_FIXED_ENABLED
#error "RXPS must remain disabled for companion firmware"
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
// NOTE: CMD range 44..49 parked, potentially for WiFi operations
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
#define CMD_GET_RADIO_FEM_RXGAIN      66
#define CMD_SET_RADIO_FEM_RXGAIN      67

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

#define MAX_CHANNEL_DATA_LENGTH       (MAX_FRAME_SIZE - 9)

#define SEND_TIMEOUT_BASE_MILLIS        500
#define FLOOD_SEND_TIMEOUT_FACTOR       16.0f
#define DIRECT_SEND_PERHOP_FACTOR       6.0f
#define DIRECT_SEND_PERHOP_EXTRA_MILLIS 250
#define LAZY_CONTACTS_WRITE_DELAY       5000
#define EXPECTED_ACK_RETRY_RECHECK_MILLIS 1000

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

#ifndef EMERGENCY_CLIENT_REPEAT_HOLD_MS
#define EMERGENCY_CLIENT_REPEAT_HOLD_MS 120000UL
#endif
#ifndef EMERGENCY_CLIENT_REPEAT_JITTER_MS
#define EMERGENCY_CLIENT_REPEAT_JITTER_MS 15000UL
#endif

#define PUBLIC_GROUP_PSK                "izOH6cXN6mrJ5e26oRXNcg=="

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
  return buf[0] == RESP_CODE_CHANNEL_MSG_RECV || buf[0] == RESP_CODE_CHANNEL_MSG_RECV_V3 ||
         buf[0] == RESP_CODE_CHANNEL_DATA_RECV;
}

void MyMesh::addToOfflineQueue(const uint8_t frame[], int len) {
  if (offline_queue_len >= OFFLINE_QUEUE_SIZE) {
    MESH_DEBUG_PRINTLN("WARN: offline_queue is full!");
    int pos = 0;
    while (pos < offline_queue_len) {
      if (offline_queue[pos].isChannelMsg()) {
        for (int i = pos; i < offline_queue_len - 1; i++) { // delete oldest channel msg from queue
          offline_queue[i] = offline_queue[i + 1];
        }
        MESH_DEBUG_PRINTLN("INFO: removed oldest channel message from queue.");
        offline_queue[offline_queue_len - 1].len = len;
        memcpy(offline_queue[offline_queue_len - 1].buf, frame, len);
        return;
      }
      pos++;
    }
    MESH_DEBUG_PRINTLN("INFO: no channel messages to remove from queue.");
  } else {
    offline_queue[offline_queue_len].len = len;
    memcpy(offline_queue[offline_queue_len].buf, frame, len);
    offline_queue_len++;
  }
}

int MyMesh::getFromOfflineQueue(uint8_t frame[]) {
  if (offline_queue_len > 0) {         // check offline queue
    size_t len = offline_queue[0].len; // take from top of queue
    memcpy(frame, offline_queue[0].buf, len);

    offline_queue_len--;
    for (int i = 0; i < offline_queue_len; i++) { // delete top item from queue
      offline_queue[i] = offline_queue[i + 1];
    }
    return len;
  }
  return 0; // queue is empty
}

float MyMesh::getAirtimeBudgetFactor() const {
  return _prefs.airtime_factor;
}

bool MyMesh::getCADEnabled() const {
  return true; // hardware CAD before TX (no CLI toggle on companion; enabled by default)
}

int MyMesh::getInterferenceThreshold() const {
  return 0; // disabled for now, until currentRSSI() problem is resolved
}

#if RXPS_FIXED_ENABLED
static uint32_t ceilPositiveFloat(float value) {
  uint32_t rounded = (uint32_t)value;
  return value > (float)rounded ? rounded + 1 : rounded;
}

static bool calcFixedRxPowerSaving(uint8_t sf, float bw, uint32_t* rx_us, uint32_t* sleep_us) {
  if (RXPS_FIXED_LEVEL < 1 || RXPS_FIXED_LEVEL > 10 || sf < 5 || sf > 12 ||
      bw <= 0.0f || (RXPS_FIXED_PREAMBLE != 16 && RXPS_FIXED_PREAMBLE != 32)) {
    return false;
  }

  const float symbol_us = (1000.0f * (float)(1UL << sf)) / bw;
  const float amount = (float)(RXPS_FIXED_LEVEL - 1) / 9.0f;
  const float rx_start_symbols = RXPS_FIXED_PREAMBLE == 16 ? 12.0f : 16.0f;
  const float sleep_start_symbols = RXPS_FIXED_PREAMBLE == 16 ? 2.0f : 15.0f;
  const float rx_edge_symbols = 8.0f;
  const float sleep_edge_symbols = (float)RXPS_FIXED_PREAMBLE + 4.25f - 8.0f;

  const float rx_symbols = rx_start_symbols + amount * (rx_edge_symbols - rx_start_symbols);
  const float sleep_symbols = sleep_start_symbols + amount * (sleep_edge_symbols - sleep_start_symbols);

  *rx_us = ceilPositiveFloat(rx_symbols * symbol_us);
  *sleep_us = (uint32_t)(sleep_symbols * symbol_us);
  return true;
}

static mesh::RadioParamApplyResult applyFixedRadioParams(float freq, float bw, uint8_t sf, uint8_t cr) {
  uint32_t rx_us, sleep_us;
  if (!calcFixedRxPowerSaving(sf, bw, &rx_us, &sleep_us)) {
    MESH_DEBUG_PRINTLN("RX Power Saving fixed profile invalid");
    return mesh::RadioParamApplyResult::FAILED;
  }

  uint32_t timings[2] = {rx_us, sleep_us};
  const bool supports_rxps = radio_driver.supportsRxPowerSaving();
  // Keep ordinary radio configuration working on companion targets whose
  // radio does not implement RX duty cycling. This matches the former
  // setParams()+setRxPowerSaving() behavior while retaining one atomic
  // transition on radios that do support it.
  mesh::RadioParamApplyResult result =
      radio_driver.trySetParams(freq, bw, sf, cr, supports_rxps ? timings : NULL);
  MESH_DEBUG_PRINTLN("RX Power Saving fixed level %d p%d: %s (%lu/%lu us)",
                     RXPS_FIXED_LEVEL,
                     RXPS_FIXED_PREAMBLE,
                     result == mesh::RadioParamApplyResult::APPLIED
                         ? (supports_rxps ? "Enabled" : "Unsupported")
                         : (result == mesh::RadioParamApplyResult::BUSY ? "Busy" : "Apply failed"),
                     (unsigned long)rx_us,
                     (unsigned long)sleep_us);
  return result;
}
#endif
int MyMesh::calcRxDelay(float score, uint32_t air_time) const {
  if (_prefs.rx_delay_base <= 0.0f) return 0;
  return (int)((pow(_prefs.rx_delay_base, 0.85f - score) - 1.0) * air_time);
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
  _store->deleteBlobByKey(pub_key, PUB_KEY_SIZE); // delete from storage
  if (_serial->isConnected()) {
    out_frame[0] = PUSH_CODE_CONTACT_DELETED;
    memcpy(&out_frame[1], pub_key, PUB_KEY_SIZE);
    _serial->writeFrame(out_frame, 1 + PUB_KEY_SIZE);
  }
}

void MyMesh::onContactsFull() {
  if (_serial->isConnected()) {
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

  if (!is_new) dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY); // only schedule lazy write for contacts that are in contacts[]
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

void MyMesh::onContactPathUpdated(const ContactInfo &contact) {
  out_frame[0] = PUSH_CODE_PATH_UPDATED;
  memcpy(&out_frame[1], contact.id.pub_key, PUB_KEY_SIZE);
  _serial->writeFrame(out_frame, 1 + PUB_KEY_SIZE); // NOTE: app may not be connected

  dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
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

MyMesh::AckTableEntry* MyMesh::findPendingTextMessage(const uint8_t text_fingerprint[MAX_HASH_SIZE]) {
  expireExpectedAcks();
  for (int i = 0; i < EXPECTED_ACK_TABLE_SIZE; i++) {
    AckTableEntry& entry = expected_ack_table[i];
    if (entry.ack != 0
        && memcmp(entry.text_fingerprint, text_fingerprint, MAX_HASH_SIZE) == 0) {
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
                          uint32_t sender_timestamp, const uint8_t *extra, int extra_len, const char *text) {
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
  return _prefs.client_repeat != 0;
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
  queueMessage(from, TXT_TYPE_PLAIN, pkt, sender_timestamp, NULL, 0, text);
}

void MyMesh::onCommandDataRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                               const char *text) {
  markConnectionActive(from); // in case this is from a server, and we have a connection
  queueMessage(from, TXT_TYPE_CLI_DATA, pkt, sender_timestamp, NULL, 0, text);
}

void MyMesh::onSignedMessageRecv(const ContactInfo &from, mesh::Packet *pkt, uint32_t sender_timestamp,
                                 const uint8_t *sender_prefix, const char *text) {
  markConnectionActive(from);
  // from.sync_since change needs to be persisted
  dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
  queueMessage(from, TXT_TYPE_SIGNED_PLAIN, pkt, sender_timestamp, sender_prefix, 4, text);
}

void MyMesh::onChannelMessageRecv(const mesh::GroupChannel &channel, mesh::Packet *pkt, uint32_t timestamp,
                                  const char *text) {
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
#ifdef DISPLAY_CLASS
  // Get the channel name from the channel index
  const char *channel_name = "Unknown";
  ChannelDetails channel_details;
  if (getChannel(channel_idx, channel_details)) {
    channel_name = channel_details.name;
  }
  if (_ui) _ui->newMsg(path_len, channel_name, text, offline_queue_len);
#endif

  if (pkt->isRouteFlood() && memcmp(channel.secret, EMERGENCY_CHANNEL_SECRET, sizeof(EMERGENCY_CHANNEL_SECRET)) == 0) {
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
  uint32_t tag;
  memcpy(&tag, data, 4);

  if (pending_login && memcmp(&pending_login, contact.id.pub_key, 4) == 0) { // check for login response
    // yes, is response to pending sendLogin()
    pending_login = 0;

    int i = 0;
    if (memcmp(&data[4], "OK", 2) == 0) { // legacy Repeater login OK response
      out_frame[i++] = PUSH_CODE_LOGIN_SUCCESS;
      out_frame[i++] = 0; // legacy: is_admin = false
      memcpy(&out_frame[i], contact.id.pub_key, 6);
      i += 6;                                     // pub_key_prefix
    } else if (data[4] == RESP_SERVER_LOGIN_OK) { // new login response
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
  } else if (len > 4 && // check for status response
             pending_status &&
             memcmp(&pending_status, contact.id.pub_key, 4) == 0 // legacy matching scheme
                                                                 // FUTURE: tag == pending_status
  ) {
    pending_status = 0;

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
  out_frame[i++] = (int8_t)(_radio->getLastSNR() * 4);
  out_frame[i++] = (int8_t)(_radio->getLastRSSI());
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
  out_frame[i++] = (int8_t)(_radio->getLastSNR() * 4);
  out_frame[i++] = (int8_t)(_radio->getLastRSSI());
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
      _serial(NULL), telemetry(MAX_PACKET_PAYLOAD - 4), _store(&store), _ui(ui), _iter(0) {
  _iter_started = false;
  _cli_rescue = false;
  saved_radio_apply_pending = false;
  radio_apply_retry_at = 0;
  radio_apply_failures = 0;
  command_radio_apply_pending = false;
  command_radio_freq = 0.0f;
  command_radio_bw = 0.0f;
  command_radio_sf = 0;
  command_radio_cr = 0;
  command_radio_repeat = 0;
  command_radio_apply_deadline = 0;
  offline_queue_len = 0;
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
  memset(&_prefs, 0, sizeof(_prefs));
  _prefs.airtime_factor = 1.0; // one half
  strcpy(_prefs.node_name, "NONAME");
  _prefs.freq = LORA_FREQ;
  _prefs.sf = LORA_SF;
  _prefs.bw = LORA_BW;
  _prefs.cr = LORA_CR;
  _prefs.multi_acks = DEFAULT_MULTI_ACKS;
  _prefs.manual_add_contacts = DEFAULT_MANUAL_ADD_CONTACTS;
  _prefs.tx_power_dbm = LORA_TX_POWER;
  _prefs.gps_enabled = 0;       // GPS disabled by default
  _prefs.gps_interval = 0;      // No automatic GPS updates by default
  _prefs.autoadd_config = DEFAULT_AUTOADD_CONFIG;
  _prefs.path_hash_mode = DEFAULT_PATH_HASH_MODE;
  //_prefs.rx_delay_base = 10.0f;  enable once new algo fixed
#if defined(USE_SX1262) || defined(USE_SX1268)
#ifdef SX126X_RX_BOOSTED_GAIN
  _prefs.rx_boosted_gain = SX126X_RX_BOOSTED_GAIN;
#else
  _prefs.rx_boosted_gain = 1; // enabled by default
#endif
#endif
  _prefs.radio_fem_rxgain = 1;

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

void MyMesh::begin(bool has_display) {
  BaseChatMesh::begin();

  if (!_store->loadMainIdentity(self_id)) {
    self_id = radio_new_identity(); // create new random identity
    int count = 0;
    while (count < 10 && (self_id.pub_key[0] == 0x00 || self_id.pub_key[0] == 0xFF)) { // reserved id hashes
      self_id = radio_new_identity();
      count++;
    }
    _store->saveMainIdentity(self_id);
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
  _prefs.gps_enabled = constrain(_prefs.gps_enabled, 0, 1);  // Ensure boolean 0 or 1
  _prefs.gps_interval = constrain(_prefs.gps_interval, 0, 86400);  // Max 24 hours
  _prefs.autoadd_config &= AUTO_ADD_OVERWRITE_OLDEST | AUTO_ADD_CHAT | AUTO_ADD_REPEATER | AUTO_ADD_ROOM_SERVER | AUTO_ADD_SENSOR;
  _prefs.path_hash_mode = constrain(_prefs.path_hash_mode, 0, 2);
  _prefs.radio_fem_rxgain = constrain(_prefs.radio_fem_rxgain, 0, 1);

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
  updateGpsTelemetryPolicy();
  bootstrapRTCfromContacts();
  addChannel("Public", PUBLIC_GROUP_PSK); // pre-configure Andy's public channel
  _store->loadChannels(this);

  saved_radio_apply_pending = !applySavedRadioParams();
  if (!saved_radio_apply_pending) {
    radio_driver.setTxPower(_prefs.tx_power_dbm);
    radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain);
  }
  board.setLoRaFemLnaEnabled(_prefs.radio_fem_rxgain);
  MESH_DEBUG_PRINTLN("RX Boosted Gain Mode: %s",
                     radio_driver.getRxBoostedGainMode() ? "Enabled" : "Disabled");
  // NOTE: no FEM LNA wiring here — companion has its own NodePrefs without
  // radio_fem_rxgain, matching upstream (which also doesn't wire companion).

#if defined(WITH_MQTT_BRIDGE) && defined(ESP32_PLATFORM) && defined(WIFI_SSID)
  applyMQTTDefaults(&_mqtt_prefs);
  _mqtt_configured = CompanionMqttSetupPortal::loadStoredConfig(_mqtt_prefs);
  if (!_mqtt_configured) applyMQTTDefaults(&_mqtt_prefs);

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
#endif

#ifdef WITH_WEBCONFIG
  void* web_mqtt_prefs = nullptr;
#ifdef WITH_MQTT_BRIDGE
  web_mqtt_prefs = &_mqtt_prefs;
#endif
  _webconfig = new WebConfigServer(this, web_mqtt_prefs, false,
                                   self_id.pub_key, FIRMWARE_VERSION,
                                   "companion", board.getManufacturerName());
#endif
}

mesh::RadioParamApplyResult MyMesh::tryApplyRadioParams(float freq, float bw, uint8_t sf, uint8_t cr) {
#if RXPS_FIXED_ENABLED
  return applyFixedRadioParams(freq, bw, sf, cr);
#else
  return radio_driver.trySetParams(freq, bw, sf, cr);
#endif
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

const char *MyMesh::getNodeName() {
  return _prefs.node_name;
}
NodePrefs *MyMesh::getNodePrefs() {
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
      Serial.println("MQTT companion: bridge started");
    } else {
      Serial.println("MQTT companion: bridge could not start");
    }
  }
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
  if (!value || !value[0]) return false;
  char* end = nullptr;
  double parsed = strtod(value, &end);
  if (!end || *end != 0 || !isfinite(parsed) || parsed < min_value || parsed > max_value) return false;
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
  s.repeat = _prefs.client_repeat != 0;
  s.capabilities = WebConfigServer::CAP_LOCATION | WebConfigServer::CAP_AIRTIME
      | WebConfigServer::CAP_RX_DELAY | WebConfigServer::CAP_RX_GAIN
      | WebConfigServer::CAP_REPEAT;
}

bool MyMesh::startWebConfig(bool force_ap, char* reply) {
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

bool MyMesh::isWebConfigSetupActive() const {
  return _webconfig && _webconfig->mode() == WebConfigServer::MODE_SETUP
      && !_webconfig->isStopping();
}

void MyMesh::rebootNow() {
  board.reboot();
}

void MyMesh::onConfigBatchStart() {
  _wc_mqtt_dirty = false;
}

void MyMesh::onConfigBatchEnd() {
#ifdef WITH_MQTT_BRIDGE
  if (_wc_mqtt_dirty) {
    CompanionMqttSetupPortal::saveStoredConfig(_mqtt_prefs);
    if (_mqtt_started && _mqtt_bridge) _mqtt_bridge->end();
    _mqtt_started = false;
    MQTTPrefs verified;
    _mqtt_configured = CompanionMqttSetupPortal::loadStoredConfig(verified);
    if (_mqtt_configured) _mqtt_prefs = verified;
  }
#endif
  _wc_mqtt_dirty = false;
}

void MyMesh::execCommand(char* cmd, char* reply) {
  reply[0] = 0;
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
    int sf, cr;
    char extra;
    if (sscanf(value, "%f,%f,%d,%d%c", &freq, &bw, &sf, &cr, &extra) != 4
        || !isfinite(freq) || !isfinite(bw) || freq < 150.0f || freq > 2500.0f
        || bw < 7.0f || bw > 500.0f || sf < 5 || sf > 12 || cr < 5 || cr > 8) {
      strcpy(reply, "Error: radio must be freq,bw,sf,cr");
    } else {
      _prefs.freq = freq;
      _prefs.bw = bw;
      _prefs.sf = static_cast<uint8_t>(sf);
      _prefs.cr = static_cast<uint8_t>(cr);
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
      radio_driver.setTxPower(_prefs.tx_power_dbm);
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
        _prefs.rx_boosted_gain = enabled;
        radio_driver.setRxBoostedGainMode(enabled);
      } else {
        _prefs.client_repeat = enabled;
      }
      savePrefs();
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
    memcpy(&out_frame[i], &_prefs.ble_pin, 4);
    i += 4;
    memset(&out_frame[i], 0, 12);
    strcpy((char *)&out_frame[i], FIRMWARE_BUILD_DATE);
    i += 12;
    StrHelper::strzcpy((char *)&out_frame[i], board.getManufacturerName(), 40);
    i += 40;
    StrHelper::strzcpy((char *)&out_frame[i], FIRMWARE_VERSION, 20);
    i += 20;
    out_frame[i++] = _prefs.client_repeat;   // v9+
    out_frame[i++] = _prefs.path_hash_mode;  // v10+
    _serial->writeFrame(out_frame, i);
  } else if (cmd_frame[0] == CMD_APP_START &&
             len >= 8) { // sent when app establishes connection, respond with node ID
    //  cmd_frame[1..7]  reserved future
    char *app_name = (char *)&cmd_frame[8];
    cmd_frame[len] = 0; // make app_name null terminated
    MESH_DEBUG_PRINTLN("App %s connected", app_name);

    _iter_started = false; // stop any left-over ContactsIterator
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
    if (recipient && (txt_type == TXT_TYPE_PLAIN || txt_type == TXT_TYPE_CLI_DATA)) {
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
        replacement_entry = findPendingTextMessage(text_fingerprint);
      }

      int result;
      uint32_t expected_ack;
      if (txt_type == TXT_TYPE_CLI_DATA) {
        msg_timestamp = getRTCClock()->getCurrentTimeUnique(); // Use node's RTC instead of app timestamp to avoid tripping replay protection
        result = sendCommandData(*recipient, msg_timestamp, attempt, text, est_timeout);
        expected_ack = 0; // no Ack expected
      } else {
        result = sendMessage(*recipient, msg_timestamp, attempt, text, expected_ack, est_timeout,
                             packet_retry_key,
                             replacement_entry != NULL ? replacement_entry->retry_key : NULL);
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
          // Reusing a circular-table slot is only ACK bookkeeping. The evicted
          // message still owns its lower-level retry sequence; only a semantic
          // same-text replacement (cleared above) or a received ACK may cancel it.
          clearExpectedAck(entry, false);
          entry.msg_sent = _ms->getMillis(); // add to circular table
          entry.expires_at = futureMillis(est_timeout);
          entry.ack = expected_ack;
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

      uint8_t reply[5];
      reply[0] = RESP_CODE_CONTACTS_START;
      uint32_t count = getNumContacts(); // total, NOT filtered count
      memcpy(&reply[1], &count, 4);
      _serial->writeFrame(reply, 5);

      // start iterator
      _iter = startContactsIterator();
      _iter_started = true;
      _most_recent_lastmod = 0;
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
      dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
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
      dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
      updateGpsTelemetryPolicy();
      writeOKFrame();
    } else {
      ContactInfo contact;
      updateContactFromFrame(contact, last_mod, cmd_frame, len);
      contact.lastmod = last_mod;
      contact.sync_since = 0;
      if (addContact(contact)) {
        dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
        updateGpsTelemetryPolicy();
        writeOKFrame();
      } else {
        writeErrFrame(ERR_CODE_TABLE_FULL);
      }
    }
  } else if (cmd_frame[0] == CMD_REMOVE_CONTACT && len >= 1 + PUB_KEY_SIZE) {
    uint8_t *pub_key = &cmd_frame[1];
    ContactInfo *recipient = lookupContactByPubKey(pub_key, PUB_KEY_SIZE);
    if (recipient && removeContact(*recipient)) {
      _store->deleteBlobByKey(pub_key, PUB_KEY_SIZE);
      dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
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
      radio_driver.setTxPower(_prefs.tx_power_dbm);
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
    if (dirty_contacts_expiry) { // is there are pending dirty contacts write needed?
      saveContacts();
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
          _iter_started = false;
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
    int8_t path_len = cmd_frame[i++];
    if (path_len >= 0 && i + path_len + 4 <= len) { // minimum 4 byte payload
      uint8_t *path = &cmd_frame[i];
      i += path_len;
      auto pkt = createRawData(&cmd_frame[i], len - i);
      if (pkt) {
        sendDirect(pkt, path, path_len);
        writeOKFrame();
      } else {
        writeErrFrame(ERR_CODE_TABLE_FULL);
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
        // FUTURE:  pending_status = tag;  // match this in onContactResponse()
        memcpy(&pending_status, recipient->id.pub_key, 4); // legacy matching scheme
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
  } else if (cmd_frame[0] == CMD_GET_RADIO_FEM_RXGAIN) {
    if (!board.canControlLoRaFemLna()) {
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
    } else {
      out_frame[0] = RESP_CODE_OK;
      uint8_t value = board.isLoRaFemLnaEnabled() ? 1 : 0;
      memcpy(&out_frame[1], &value, 1);
      _serial->writeFrame(out_frame, 2);
    }
  } else if (cmd_frame[0] == CMD_SET_RADIO_FEM_RXGAIN && len >= 2) {
    uint8_t value = cmd_frame[1];
    if (!board.canControlLoRaFemLna()) {
      writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
    } else if (value <= 1) {
      _prefs.radio_fem_rxgain = value;
      if (board.setLoRaFemLnaEnabled(value != 0)) {
        savePrefs();
        writeOKFrame();
      } else {
        writeErrFrame(ERR_CODE_UNSUPPORTED_CMD);
      }
    } else {
      writeErrFrame(ERR_CODE_ILLEGAL_ARG);
    }
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
  _store->saveContacts(this, save_filter);
}

void MyMesh::enterCLIRescue() {
  _cli_rescue = true;
  cli_command[0] = 0;
  Serial.println("========= CLI Rescue =========");
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

    if (memcmp(cli_command, "set ", 4) == 0) {
      const char* config = &cli_command[4];
      if (memcmp(config, "pin ", 4) == 0) {
        _prefs.ble_pin = atoi(&config[4]);
        savePrefs();
        Serial.printf("  > pin is now %06d\n", _prefs.ble_pin);
      } else {
        Serial.printf("  Error: unknown config: %s\n", config);
      }
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
    _iter_started = false;
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
      _iter_started = false;
    }
  //} else if (!_serial->isWriteBusy()) {
  //  checkConnections();    // TODO - deprecate the 'Connections' stuff
  }
}

void MyMesh::loop() {
  BaseChatMesh::loop();
  if (!command_radio_apply_pending && saved_radio_apply_pending && !hasOutbound()
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
    saveContacts();
    dirty_contacts_expiry = 0;
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
  if (radio_driver.isWatchdogObserving() || radio_driver.isCalibratingNoiseFloor()) return true;
  return (_serial != NULL && _serial->hasPendingIO())
      || (_iter_started && _serial != NULL && _serial->isConnected())
      || command_radio_apply_pending
      || hasQueuedWorkDue() || hasRetryWorkDue()
      || (saved_radio_apply_pending
          && (!radio_apply_retry_at || millisHasNowPassed(radio_apply_retry_at)))
      || (dirty_contacts_expiry != 0 && millisHasNowPassed(dirty_contacts_expiry))
      || (emergency_client_repeat_packet != NULL
          && millisHasNowPassed(emergency_client_repeat_send_at));
}
