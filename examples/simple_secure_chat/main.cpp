#include <Arduino.h>   // needed for PlatformIO
#include <Mesh.h>
#if MESH_PACKET_LOGGING
  #include <helpers/SerialPacketLog.h>
#endif

#if defined(NRF52_PLATFORM)
  #include <InternalFileSystem.h>
#elif defined(RP2040_PLATFORM)
  #include <LittleFS.h>
#elif defined(ESP32)
  #include <SPIFFS.h>
#endif

#include <helpers/ArduinoHelpers.h>
#include <helpers/IdentityGeneration.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/SimpleMeshTables.h>
#include <helpers/IdentityStore.h>
#if defined(ESP32_PLATFORM)
  #include <helpers/ESP32TrueRandom.h>
#endif
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
  #include <helpers/esp32/WiFiRadioPolicy.h>
#endif
#include <RTClib.h>
#include <target.h>

/* ---------------------------------- CONFIGURATION ------------------------------------- */

#define FIRMWARE_VER_TEXT   "v2 (build: 4 Feb 2025)"

#ifndef LORA_FREQ
  #define LORA_FREQ   915.0
#endif
#ifndef LORA_BW
  #define LORA_BW     250
#endif
#ifndef LORA_SF
  #define LORA_SF     10
#endif
#ifndef LORA_CR
  #define LORA_CR      5
#endif
#ifndef LORA_TX_POWER
  #define LORA_TX_POWER  20
#endif

#ifndef MAX_CONTACTS
  #define MAX_CONTACTS         100
#endif

#include <helpers/BaseChatMesh.h>

#define SEND_TIMEOUT_BASE_MILLIS          500
#define FLOOD_SEND_TIMEOUT_FACTOR         16.0f
#define DIRECT_SEND_PERHOP_FACTOR         6.0f
#define DIRECT_SEND_PERHOP_EXTRA_MILLIS   250

#define  PUBLIC_GROUP_PSK  "izOH6cXN6mrJ5e26oRXNcg=="

// Believe it or not, this std C function is busted on some platforms!
static uint32_t _atoi(const char* sp) {
  uint32_t n = 0;
  while (*sp && *sp >= '0' && *sp <= '9') {
    n *= 10;
    n += (*sp++ - '0');
  }
  return n;
}

/* -------------------------------------------------------------------------------------- */

struct NodePrefs {  // persisted to file
  float airtime_factor;
  char node_name[32];
  double node_lat, node_lon;
  float freq;
  int8_t tx_power_dbm;
  uint8_t unused[3];
  uint8_t usb_logging_enabled;
};

class MyMesh : public BaseChatMesh, ContactVisitor {
  FILESYSTEM* _fs;
  NodePrefs _prefs;
  uint32_t expected_ack_crc;
  ChannelDetails* _public;
  unsigned long last_msg_sent;
  ContactInfo* curr_recipient;
  char command[512+10];
  uint8_t tmp_buf[256];
  char hex_buf[512];

  const char* getTypeName(uint8_t type) const {
    if (type == ADV_TYPE_CHAT) return "Chat";
    if (type == ADV_TYPE_REPEATER) return "Repeater";
    if (type == ADV_TYPE_ROOM) return "Room";
    return "??";  // unknown
  }

  void loadContacts() {
    if (_fs->exists("/contacts")) {
    #if defined(RP2040_PLATFORM)
      File file = _fs->open("/contacts", "r");
    #else
      File file = _fs->open("/contacts");
    #endif
      if (file) {
        bool full = false;
        while (!full) {
          ContactInfo c;
          uint8_t pub_key[32];
          uint8_t unused;
          uint32_t reserved;

          bool success = (file.read(pub_key, 32) == 32);
          success = success && (file.read((uint8_t *) &c.name, 32) == 32);
          success = success && (file.read(&c.type, 1) == 1);
          success = success && (file.read(&c.flags, 1) == 1);
          success = success && (file.read(&unused, 1) == 1);
          success = success && (file.read((uint8_t *) &reserved, 4) == 4);
          success = success && (file.read((uint8_t *) &c.out_path_len, 1) == 1);
          success = success && (file.read((uint8_t *) &c.last_advert_timestamp, 4) == 4);
          success = success && (file.read(c.out_path, 64) == 64);
          c.gps_lat = c.gps_lon = 0;   // not yet supported

          if (!success) break;  // EOF

          c.id = mesh::Identity(pub_key);
          c.lastmod = 0;
          if (!addContact(c)) full = true;
        }
        file.close();
      }
    }
  }

  void saveContacts() {
#if defined(NRF52_PLATFORM)
    _fs->remove("/contacts");
    File file = _fs->open("/contacts", FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
    File file = _fs->open("/contacts", "w");
#else
    File file = _fs->open("/contacts", "w", true);
#endif
    if (file) {
      ContactsIterator iter = startContactsIterator();
      ContactInfo c;
      uint8_t unused = 0;
      uint32_t reserved = 0;

      while (iter.hasNext(this, c)) {
        bool success = (file.write(c.id.pub_key, 32) == 32);
        success = success && (file.write((uint8_t *) &c.name, 32) == 32);
        success = success && (file.write(&c.type, 1) == 1);
        success = success && (file.write(&c.flags, 1) == 1);
        success = success && (file.write(&unused, 1) == 1);
        success = success && (file.write((uint8_t *) &reserved, 4) == 4);
        success = success && (file.write((uint8_t *) &c.out_path_len, 1) == 1);
        success = success && (file.write((uint8_t *) &c.last_advert_timestamp, 4) == 4);
        success = success && (file.write(c.out_path, 64) == 64);

        if (!success) break;  // write failed
      }
      file.close();
    }
  }

  void setClock(uint32_t timestamp) {
    uint32_t curr = getRTCClock()->getCurrentTime();
    if (timestamp > curr) {
      getRTCClock()->setCurrentTime(timestamp);
      Serial.println("   (OK - clock set!)");
    } else {
      Serial.println("   (ERR: clock cannot go backwards)");
    }
  }

  void importCard(const char* command) {
    while (*command == ' ') command++;   // skip leading spaces
    if (memcmp(command, "meshcore://", 11) == 0) {
      command += 11;  // skip the prefix
      char *ep = strchr(command, 0);  // find end of string
      while (ep > command) {
        ep--;
        if (mesh::Utils::isHexChar(*ep)) break;  // found tail end of card
        *ep = 0;  // remove trailing spaces and other junk
      }
      int len = strlen(command);
      if (len % 2 == 0) {
        len >>= 1;  // halve, for num bytes
        if (mesh::Utils::fromHex(tmp_buf, len, command)) {
          importContact(tmp_buf, len);
          return;
        }
      }
    }
    Serial.println("   error: invalid format");
  }

protected:
  float getAirtimeBudgetFactor() const override {
    return _prefs.airtime_factor;
  }

  int calcRxDelay(float score, uint32_t air_time) const override {
    return 0;  // disable rxdelay
  }

  bool allowPacketForward(const mesh::Packet* packet) override {
    return true;
  }

  uint8_t getDefaultTxCodingRate() const override {
    return LORA_CR;
  }

  void onDiscoveredContact(ContactInfo& contact, bool is_new, uint8_t path_len, const uint8_t* path) override {
    // TODO: if not in favs,  prompt to add as fav(?)

    Serial.printf("ADVERT from -> %s\n", contact.name);
    Serial.printf("  type: %s\n", getTypeName(contact.type));
    Serial.print("   public key: "); mesh::Utils::printHex(Serial, contact.id.pub_key, PUB_KEY_SIZE); Serial.println();

    saveContacts();
  }

  void onContactPathUpdated(const ContactInfo& contact) override {
    Serial.printf("PATH to: %s, path_len=%d\n", contact.name, (uint32_t) contact.out_path_len);
    saveContacts();
  }

  ContactInfo* processAck(const uint8_t *data) override {
    if (memcmp(data, &expected_ack_crc, 4) == 0) {     // got an ACK from recipient
      Serial.printf("   Got ACK! (round trip: %d millis)\n", _ms->getMillis() - last_msg_sent);
      // NOTE: the same ACK can be received multiple times!
      expected_ack_crc = 0;  // reset our expected hash, now that we have received ACK
      return NULL;  // TODO: really should return ContactInfo pointer 
    }

    //uint32_t crc;
    //memcpy(&crc, data, 4);
    //MESH_DEBUG_PRINTLN("unknown ACK received: %08X (expected: %08X)", crc, expected_ack_crc);
    return NULL;
  }

  void onMessageRecv(const ContactInfo& from, mesh::Packet* pkt, uint32_t sender_timestamp, const char *text) override {
    Serial.printf("(%s) MSG -> from %s\n", pkt->isRouteDirect() ? "DIRECT" : "FLOOD", from.name);
    Serial.printf("   %s\n", text);

    if (strcmp(text, "clock sync") == 0) {  // special text command
      setClock(sender_timestamp + 1);
    }
  }

  void onCommandDataRecv(const ContactInfo& from, mesh::Packet* pkt, uint32_t sender_timestamp, const char *text) override {
  }

  void onCLICommandRecv(const ContactInfo& contact, mesh::Packet* pkt, uint32_t sender_timestamp, const char *text, char* reply) override {
  }

  void onSignedMessageRecv(const ContactInfo& from, mesh::Packet* pkt, uint32_t sender_timestamp, const uint8_t *sender_prefix, const char *text) override {
  }

  void onChannelMessageRecv(const mesh::GroupChannel& channel, mesh::Packet* pkt, uint32_t timestamp, const char *text) override {
    if (pkt->isRouteDirect()) {
      Serial.printf("PUBLIC CHANNEL MSG -> (Direct!)\n");
    } else {
      Serial.printf("PUBLIC CHANNEL MSG -> (Flood) hops %d\n", pkt->path_len);
    }
    Serial.printf("   %s\n", text);
  }

  uint8_t onContactRequest(const ContactInfo& contact, uint32_t sender_timestamp, const uint8_t* data, uint8_t len, uint8_t* reply) override {
    return 0;  // unknown
  }

  void onContactResponse(const ContactInfo& contact, const uint8_t* data, uint8_t len) override {
    // not supported
  }

  uint32_t calcFloodTimeoutMillisFor(uint32_t pkt_airtime_millis) const override {
    return SEND_TIMEOUT_BASE_MILLIS + (FLOOD_SEND_TIMEOUT_FACTOR * pkt_airtime_millis);
  }
  uint32_t calcDirectTimeoutMillisFor(uint32_t pkt_airtime_millis, uint8_t path_len) const override {
    uint8_t path_hash_count = path_len & 63;
    return SEND_TIMEOUT_BASE_MILLIS + 
         ( (pkt_airtime_millis*DIRECT_SEND_PERHOP_FACTOR + DIRECT_SEND_PERHOP_EXTRA_MILLIS) * (path_hash_count + 1));
  }

  void onSendTimeout() override {
    Serial.println("   ERROR: timed out, no ACK.");
  }

public:
  MyMesh(mesh::Radio& radio, StdRNG& rng, mesh::RTCClock& rtc, SimpleMeshTables& tables)
     : BaseChatMesh(radio, *new ArduinoMillis(), rng, rtc, *new StaticPoolPacketManager(16), tables)
  {
    // defaults
    memset(&_prefs, 0, sizeof(_prefs));
    _prefs.airtime_factor = 2.0;    // one third
    strcpy(_prefs.node_name, "NONAME");
    _prefs.freq = LORA_FREQ;
    _prefs.tx_power_dbm = LORA_TX_POWER;
    _prefs.usb_logging_enabled = 1;

    command[0] = 0;
    curr_recipient = NULL;
  }

  float getFreqPref() const { return _prefs.freq; }
  int8_t getTxPowerPref() const { return _prefs.tx_power_dbm; }

  void begin(FILESYSTEM& fs) {
    _fs = &fs;

    BaseChatMesh::begin();

  #if defined(NRF52_PLATFORM)
    IdentityStore store(fs, "");
  #elif defined(RP2040_PLATFORM)
    IdentityStore store(fs, "/identity");
    store.begin();
  #else
    IdentityStore store(fs, "/identity");
  #endif
    const bool needs_identity = !store.load("_main", self_id, _prefs.node_name, sizeof(_prefs.node_name))
        || mesh::hasReservedIdentityPrefix(self_id);  // legacy: node_name was from identity file
    bool identity_ready = true;
    if (needs_identity) {
      // Need way to get some entropy to seed RNG
      Serial.println("Press ENTER to generate key:");
      char c = 0;
      while (c != '\n') {   // wait for ENTER to be pressed
        if (Serial.available()) c = Serial.read();
      }
      ((StdRNG *)getRNG())->begin(millis());

    #if defined(ESP32_PLATFORM)
      identity_ready = mesh::generateUsableLocalIdentity(self_id, radio_new_identity);
    #else
      identity_ready = mesh::generateUsableLocalIdentity(
          self_id, [this]() { return mesh::LocalIdentity(getRNG()); });
    #endif
      if (identity_ready) store.save("_main", self_id);
    }

  #if defined(ESP32_PLATFORM)
    mesh::discardESP32TrueRandom();
  #endif
    if (!identity_ready) {
      MESH_DEBUG_PRINTLN("Identity generation exhausted all attempts; rebooting");
      board.reboot();
      return;
    }

    // load persisted prefs
    if (_fs->exists("/node_prefs")) {
    #if defined(RP2040_PLATFORM)
      File file = _fs->open("/node_prefs", "r");
    #else
      File file = _fs->open("/node_prefs");
    #endif
      if (file) {
        file.read((uint8_t *) &_prefs, sizeof(_prefs));
        file.close();
      }
    }

#if MESH_USB_LOGGING_AVAILABLE
    _prefs.usb_logging_enabled = constrain(_prefs.usb_logging_enabled, 0, 1);
    mesh::setUsbLoggingEnabled(_prefs.usb_logging_enabled != 0);
#endif

    loadContacts();
    _public = addChannel("Public", PUBLIC_GROUP_PSK); // pre-configure Andy's public channel
  }

  void savePrefs() {
#if defined(NRF52_PLATFORM)
    _fs->remove("/node_prefs");
    File file = _fs->open("/node_prefs", FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
    File file = _fs->open("/node_prefs", "w");
#else
    File file = _fs->open("/node_prefs", "w", true);
#endif
    if (file) {
      file.write((const uint8_t *)&_prefs, sizeof(_prefs));
      file.close();
    }
  }

  void showWelcome() {
    Serial.println("===== MeshCore Chat Terminal =====");
    Serial.println();
    Serial.printf("WELCOME  %s\n", _prefs.node_name);
    mesh::Utils::printHex(Serial, self_id.pub_key, PUB_KEY_SIZE);
    Serial.println();
    Serial.println("   (enter 'help' for basic commands)");
    Serial.println();
  }

  void sendSelfAdvert(int delay_millis) {
    auto pkt = createSelfAdvert(_prefs.node_name, _prefs.node_lat, _prefs.node_lon);
    if (pkt) {
      sendFlood(pkt, delay_millis);
    }
  }

  // ContactVisitor
  void onContactVisit(const ContactInfo& contact) override {
    Serial.printf("   %s - ", contact.name);
    char tmp[40];
    int32_t secs = contact.last_advert_timestamp - getRTCClock()->getCurrentTime();
    AdvertTimeHelper::formatRelativeTimeDiff(tmp, secs, false);
    Serial.println(tmp);
  }

  void handleCommand(const char* command) {
    while (*command == ' ') command++;  // skip leading spaces

    if (memcmp(command, "send ", 5) == 0) {
      if (curr_recipient) {
        const char *text = &command[5];
        uint32_t est_timeout;

        int result = sendMessage(*curr_recipient, getRTCClock()->getCurrentTime(), 0, text, expected_ack_crc, est_timeout);
        if (result == MSG_SEND_FAILED) {
          Serial.println("   ERROR: unable to send.");
        } else {
          last_msg_sent = _ms->getMillis();
          Serial.printf("   (message sent - %s)\n", result == MSG_SEND_SENT_FLOOD ? "FLOOD" : "DIRECT");
        }
      } else {
        Serial.println("   ERROR: no recipient selected (use 'to' cmd).");
      }
    } else if (memcmp(command, "public ", 7) == 0) {  // send GroupChannel msg
      uint8_t temp[5+MAX_TEXT_LEN+32];
      uint32_t timestamp = getRTCClock()->getCurrentTime();
      memcpy(temp, &timestamp, 4);   // mostly an extra blob to help make packet_hash unique
      temp[4] = 0;  // attempt and flags

      sprintf((char *) &temp[5], "%s: %s", _prefs.node_name, &command[7]);  // <sender>: <msg>
      temp[5 + MAX_TEXT_LEN] = 0;  // truncate if too long

      int len = strlen((char *) &temp[5]);
      auto pkt = createGroupDatagram(PAYLOAD_TYPE_GRP_TXT, _public->channel, temp, 5 + len);
      if (pkt) {
        sendFlood(pkt);
        Serial.println("   Sent.");
      } else {
        Serial.println("   ERROR: unable to send");
      }
    } else if (memcmp(command, "list", 4) == 0) {  // show Contact list, by most recent
      int n = 0;
      if (command[4] == ' ') {  // optional param, last 'N'
        n = atoi(&command[5]);
      }
      scanRecentContacts(n, this);
    } else if (strcmp(command, "clock") == 0) {    // show current time
      uint32_t now = getRTCClock()->getCurrentTime();
      DateTime dt = DateTime(now);
      Serial.printf(   "%02d:%02d - %d/%d/%d UTC\n", dt.hour(), dt.minute(), dt.day(), dt.month(), dt.year());
    } else if (memcmp(command, "time ", 5) == 0) {  // set time (to epoch seconds)
      uint32_t secs = _atoi(&command[5]);
      setClock(secs);
    } else if (memcmp(command, "to ", 3) == 0) {  // set current recipient
      curr_recipient = searchContactsByPrefix(&command[3]);
      if (curr_recipient) {
        Serial.printf("   Recipient %s now selected.\n", curr_recipient->name);
      } else {
        Serial.println("   Error: Name prefix not found.");
      }
    } else if (strcmp(command, "to") == 0) {    // show current recipient
      if (curr_recipient) {
         Serial.printf("   Current: %s\n", curr_recipient->name);
      } else {
         Serial.println("   Err: no recipient selected");
      }
    } else if (strcmp(command, "advert") == 0) {
      auto pkt = createSelfAdvert(_prefs.node_name, _prefs.node_lat, _prefs.node_lon);
      if (pkt) {
        sendZeroHop(pkt);
        Serial.println("   (advert sent, zero hop).");
      } else {
        Serial.println("   ERR: unable to send");
      }
    } else if (strcmp(command, "reset path") == 0) {
      if (curr_recipient) {
        resetPathTo(*curr_recipient);
        saveContacts();
        Serial.println("   Done.");
      }
    } else if (memcmp(command, "card", 4) == 0) {
      Serial.printf("Hello %s\n", _prefs.node_name);
      auto pkt = createSelfAdvert(_prefs.node_name, _prefs.node_lat, _prefs.node_lon);
      if (pkt) {
        uint8_t len =  pkt->writeTo(tmp_buf);
        releasePacket(pkt);  // undo the obtainNewPacket()

        mesh::Utils::toHex(hex_buf, tmp_buf, len);
        Serial.println("Your MeshCore biz card:");
        Serial.print("meshcore://"); Serial.println(hex_buf);
        Serial.println();
      } else {
        Serial.println("  Error");
      }
    } else if (memcmp(command, "import ", 7) == 0) {
      importCard(&command[7]);
    }
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
    else if (strcmp(command, "get espnow.channel") == 0) {
      const uint8_t saved = mesh::wifi::loadConfiguredEspNowChannel();
      const uint8_t active = mesh::wifi::activeEspNowChannel();
      if (saved == active) {
        Serial.printf("> %u (saved and active)\n", (unsigned)saved);
      } else {
        Serial.printf("> saved %u, active %u; reboot required\n",
                      (unsigned)saved, (unsigned)active);
      }
    } else if (strncmp(command, "set espnow.channel", 18) == 0
               && (command[18] == 0 || command[18] == ' '
                   || command[18] == '\t')) {
      const char* value = command + 18;
      while (*value == ' ' || *value == '\t') value++;
      uint8_t channel = 0;
      if (!mesh::wifi::parseEspNowChannel(value, channel)) {
        Serial.println("Error: ESP-NOW channel must be 1-13");
      } else {
        // Snapshot the boot channel before writing NVS. The running radio
        // remains on that channel until restart, keeping this node joined to
        // its current peers for the rest of this boot.
        const uint8_t active = mesh::wifi::activeEspNowChannel();
        if (!mesh::wifi::saveConfiguredEspNowChannel(channel)) {
          Serial.println("Error: failed to save ESP-NOW channel");
        } else if (channel == active) {
          Serial.printf("OK - ESP-NOW channel %u saved and active\n",
                        (unsigned)channel);
        } else {
          Serial.printf(
              "OK - ESP-NOW channel %u saved; active %u; reboot required\n",
              (unsigned)channel, (unsigned)active);
        }
      }
    }
#endif
    else if (strcmp(command, "get usb.logging") == 0) {
#if MESH_USB_LOGGING_AVAILABLE
      Serial.printf("  usb.logging %s\n",
                    mesh::isUsbLoggingEnabled() ? "on" : "off");
#else
      Serial.println("  ERROR: USB logging is unavailable");
#endif
    } else if (memcmp(command, "set ", 4) == 0) {
      const char* config = &command[4];
      if (strcmp(config, "usb.logging on") == 0
          || strcmp(config, "usb.logging off") == 0) {
#if MESH_USB_LOGGING_AVAILABLE
        const bool enabled = strcmp(config, "usb.logging on") == 0;
        _prefs.usb_logging_enabled = enabled ? 1 : 0;
        mesh::setUsbLoggingEnabled(enabled);
        savePrefs();
        Serial.printf("  OK - USB logging %s (saved)\n",
                      enabled ? "on" : "off");
#else
        Serial.println("  ERROR: USB logging is unavailable");
#endif
      } else if (memcmp(config, "af ", 3) == 0) {
        _prefs.airtime_factor = atof(&config[3]);
        savePrefs();
        Serial.println("  OK");
      } else if (memcmp(config, "name ", 5) == 0) {
        StrHelper::strncpy(_prefs.node_name, &config[5], sizeof(_prefs.node_name));
        savePrefs();
        Serial.println("  OK");
      } else if (memcmp(config, "lat ", 4) == 0) {
        _prefs.node_lat = atof(&config[4]);
        savePrefs();
        Serial.println("  OK");
      } else if (memcmp(config, "lon ", 4) == 0) {
        _prefs.node_lon = atof(&config[4]);
        savePrefs();
        Serial.println("  OK");
      } else if (memcmp(config, "tx ", 3) == 0) {
        _prefs.tx_power_dbm = atoi(&config[3]);
        savePrefs();
        Serial.println("  OK - reboot to apply");
      } else if (memcmp(config, "freq ", 5) == 0) {
        _prefs.freq = atof(&config[5]);
        savePrefs();
        Serial.println("  OK - reboot to apply");
      } else {
        Serial.printf("  ERROR: unknown config: %s\n", config);
      }
    } else if (memcmp(command, "ver", 3) == 0) {
      Serial.println(FIRMWARE_VER_TEXT);
    } else if (memcmp(command, "help", 4) == 0) {
      Serial.println("Commands:");
      Serial.println("   set {name|lat|lon|freq|tx|af} {value}");
      Serial.println("   get usb.logging");
      Serial.println("   set usb.logging {on|off}");
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
      Serial.println("   get espnow.channel");
      Serial.println("   set espnow.channel <1-13>");
#endif
      Serial.println("   card");
      Serial.println("   import {biz card}");
      Serial.println("   clock");
      Serial.println("   time <epoch-seconds>");
      Serial.println("   list {n}");
      Serial.println("   to <recipient name or prefix>");
      Serial.println("   to");
      Serial.println("   send <text>");
      Serial.println("   advert");
      Serial.println("   reset path");
      Serial.println("   public <text>");
    } else {
      Serial.print("   ERROR: unknown command: "); Serial.println(command);
    }
  }

  void loop() {
    BaseChatMesh::loop();

    int len = strlen(command);
    while (Serial.available() && len < sizeof(command)-1) {
      char c = Serial.read();
      if (c != '\n') { 
        command[len++] = c;
        command[len] = 0;
      }
      Serial.print(c);
    }
    if (len == sizeof(command)-1) {  // command buffer full
      command[sizeof(command)-1] = '\r';
    }

    if (len > 0 && command[len - 1] == '\r') {  // received complete line
      command[len - 1] = 0;  // replace newline with C string null terminator

      handleCommand(command);
      command[0] = 0;  // reset command buffer
    }
  }
};

StdRNG fast_rng;
SimpleMeshTables tables;
MyMesh the_mesh(radio_driver, fast_rng, rtc_clock, tables);

void halt() {
  while (1) ;
}

void setup() {
  Serial.begin(115200);
#if MESH_PACKET_LOGGING
  mesh::serialLogBegin();
#endif

  board.begin();

#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.begin();
#endif

  int radioinit_attempts = 0;
  while (!radio_init()) {
    ++radioinit_attempts;
    MESH_DEBUG_PRINTLN("Radio init failed! (attempt %d)", radioinit_attempts);
    if (radioinit_attempts >= 3) {
      MESH_DEBUG_PRINTLN("Radio init failed 3x - rebooting");
      board.reboot();
    }
    delay(500);
  }

  fast_rng.begin(radio_driver.getRngSeed());

#if defined(NRF52_PLATFORM)
  InternalFS.begin();
  the_mesh.begin(InternalFS);
#elif defined(RP2040_PLATFORM)
  LittleFS.begin();
  the_mesh.begin(LittleFS);
#elif defined(ESP32)
  SPIFFS.begin(true);
  the_mesh.begin(SPIFFS);
#else
  #error "need to define filesystem"
#endif

  radio_driver.setParams(the_mesh.getFreqPref(), LORA_BW, LORA_SF, LORA_CR);
  radio_driver.setTxPower(the_mesh.getTxPowerPref());

  the_mesh.showWelcome();

  // send out initial Advertisement to the mesh
#if ENABLE_ADVERT_ON_BOOT == 1
  the_mesh.sendSelfAdvert(1200);   // add slight delay
#endif
}

void loop() {
#if defined(NRF52_PLATFORM)
  board.feedWatchdog();
#endif
  the_mesh.loop();
  rtc_clock.tick();
#ifdef HAS_EXTERNAL_WATCHDOG
  external_watchdog.loop();
#endif
}
