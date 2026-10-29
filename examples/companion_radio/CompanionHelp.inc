// Reusable help fragments. The caller selects printing or aggregation.
// Keep feature gates here so both paths expose the same commands and options.
#if !defined(MESH_COMPANION_HELP_BEGIN) || !defined(MESH_COMPANION_HELP_END) \
|| !defined(MESH_COMPANION_HELP_LINE_BEGIN) || !defined(MESH_COMPANION_HELP_LINE_END)
#error "CompanionHelp.inc requires all four help emission macros"
#endif
MESH_COMPANION_HELP_BEGIN
    "Commands:\r\n"
    "  stats-core / stats-radio / stats-radio-diag / stats-packets\r\n"
    "  get prv.key\r\n"
#if defined(ESP32_PLATFORM) && defined(WIFI_SSID)
    "  password <new-password> (shared setup WiFi, WebConfig and WiFi OTA)\r\n"
    "  get password (management password status)\r\n"
#else
    "  get password\r\n"
#endif
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  erase (settings+identity)\r\n"
    "  board\r\n"
    "  version\r\n"
    "  get storage.layout\r\n"
MESH_COMPANION_HELP_END
#if MESH_ENABLE_TX_BEACONS
MESH_COMPANION_HELP_BEGIN
    "  get beacon [slot] / set beacon <slot> <meshcore|meshtext> ...\r\n"
    "  delete beacon <slot|all> / set beacon.interval <12|24|36|48> (default 24h)\r\n"
MESH_COMPANION_HELP_END
#endif
#if MESH_ENABLE_MESHTEXT_CHAT
MESH_COMPANION_HELP_BEGIN
    "  get mt.status / get mt.region / get mt.slot / get mt.hops\r\n"
    "  get mt.node / get mt.shortname\r\n"
    "  get mt.rebroadcast / set mt.rebroadcast <on|off> (default off)\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set mt.region <auto|us|eu868|eu433|anz>\r\n"
    "  set mt.slot <0|1-based frequency slot>\r\n"
    "  set mt.hops <0..7> (default 7)\r\n"
    "  set mt.node <!8hex|default>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set mt.shortname <1..4 UTF-8 characters|default>\r\n"
    "  send mt.nodeinfo <channel slot> (auto after first chat)\r\n"
    "  set mt.preset <longfast|shortturbo|...>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set mt.channel <slot> <mt-name> <32/64 hex digits|AQ==>\r\n"
    "  get mt.channel <slot> / delete mt.channel <slot>\r\n"
    "  App #mt-longfast/#mt-shortturbo: radio2\r\n"
MESH_COMPANION_HELP_END
#endif
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
MESH_COMPANION_HELP_BEGIN
    "  get flash.health\r\n"
MESH_COMPANION_HELP_END
#endif
#if COMPANION_FEATURE_READER
MESH_COMPANION_HELP_BEGIN
    "  get reader <chapter>[:<verse>[-<last>]] [page]\r\n"
MESH_COMPANION_HELP_END
#endif
MESH_COMPANION_HELP_BEGIN
    "  get/set tx.media auto|radio|radio2|both|off\r\n"
    "  get/set tx.ssh auto|radio|radio2|both|off\r\n"
MESH_COMPANION_HELP_END
#if MESH_ENABLE_ETHERNET_TUNNEL
MESH_COMPANION_HELP_BEGIN
    "  get ethernet / get ethernet.radio\r\n"
    "  set ethernet.radio <MHz>,<kHz>,<SF>,<CR>[,<preamble>]\r\n"
    "  set ethernet on|off (saved; trusted-LAN KISS TCP 5003, radio2 only)\r\n"
MESH_COMPANION_HELP_END
#endif
#if defined(OTA_RAK15002_SD_STORE) && defined(ETHERNET_USE_RAK13800)
MESH_COMPANION_HELP_BEGIN
    "  get eth / set eth on|off (default off; boot)\r\n"
MESH_COMPANION_HELP_END
#endif
#if defined(NRF52_PLATFORM) && defined(OTA_SD_STORE)
MESH_COMPANION_HELP_BEGIN
    "  get sdcard [free|ls|dir [page]|format|erase|*]\r\n"
    "  set sdcard format|erase [--force]\r\n"
    "  ota cache [on|off]\r\n"
MESH_COMPANION_HELP_END
#endif
MESH_COMPANION_HELP_BEGIN
    "  power / get pwrmgt.support / get pwrmgt.source / get pwrmgt.bootmv\r\n"
    "  get system.watchdog\r\n"
    "  get bootloader.ver\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  get pwrmgt.bootreason\r\n"
#if COMPANION_FEATURE_MEMORY_DIAGNOSTICS
    "  memory\r\n"
#endif
    "  get/set display.rotation <0|90|180|270>\r\n"
#ifdef MESHCORE_HAS_COLOR_DISPLAY
    "  get/set display.night <auto|on|off>\r\n"
    "  get/set display.night.delay <-180..180> (minutes after sunset; default 60)\r\n"
#endif
#if defined(OLED_AUTO_DETECT) && OLED_AUTO_DETECT && defined(DISPLAY_CLASS)
    "  get/set display.controller <auto|ssd1306|sh1106> (restart)\r\n"
#endif
#ifdef PIN_BUZZER
    "  get/set buzzer on|off\r\n"
#endif
#if defined(PIN_VIBRATION) || defined(HAS_DRV2605)
    "  get/set vibration on|off\r\n"
#endif
MESH_COMPANION_HELP_END
#if COMPANION_FEATURE_NOTIFICATIONS
MESH_COMPANION_HELP_BEGIN
    "  get notify / get notify.rules / get notify.gpio.pins\r\n"
    "  set notify.{sound|vibration|led|screen|gpio} on|off\r\n"
    "  set notify.<output> <all|contact:key|room:key|channel:slot> <pattern>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set notify.remote <contact:key|room:key> on|off (!notify, <=15s, no GPIO)\r\n"
    "  notify.test <target> / notify.stop / notify.delete <target>\r\n"
MESH_COMPANION_HELP_END
#endif
MESH_COMPANION_HELP_BEGIN
    "  get/set display.inbox <history|pending|unread>\r\n"
    "  get/set display.touch <on|off> (boot)\r\n"
    "  get public.key\r\n"
    "  set {name|lat|lon|freq|tx|af} {value}\r\n"
    "  get wifi|espnow|2.4ghz\r\n"
    "  set wifi|espnow on|off [force] (force: off)\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set 2.4ghz on [all|force]|off [force] (boot)\r\n"
    "  get/set bluetooth.name <name|default>\r\n"
MESH_COMPANION_HELP_END
#if defined(BLE_PIN_CODE)
MESH_COMPANION_HELP_BEGIN
    "  get bluetooth / get ble\r\n"
    "  set bluetooth <on|off> [force] (force: off)\r\n"
    "  get bluetooth.mac\r\n"
    "  set bluetooth.mac <address|random|random-every-boot|random-after-connect|default>\r\n"
    "  get/set bluetooth.stealth on|off\r\n"
MESH_COMPANION_HELP_END
#endif
MESH_COMPANION_HELP_BEGIN
#if MESH_ENABLE_LOST_REPLY
    "  get/set lost.reply <off|no|yes>\r\n"
#endif
    "  set pin <0-999999>\r\n"
    "  powersaving [on|off]\r\n"
#if MESH_USB_LOGGING_AVAILABLE
    "  get/set usb.logging <on|off|stream> [reboot]\r\n"
    "  get/set usb.debug <on|off>\r\n"
#if MESH_USB_CONSOLE_COOPERATIVE
    "  get usb.watchdog.last\r\n"
    "  get/set usb.watchdog <off|on|auto> (default auto)\r\n"
#endif
#endif
MESH_COMPANION_HELP_END
#if MESH_USB_LOGGING_AVAILABLE && !defined(MESH_DUAL_CDC_LOGGING)
MESH_COMPANION_HELP_BEGIN
    "  get/set usb.logging.always <on|off>\r\n"
MESH_COMPANION_HELP_END
#endif
#if defined(ESP32) && defined(WIFI_SSID)
#if defined(COMPANION_RADIO_FULL)
MESH_COMPANION_HELP_BEGIN
    "  start ota [ap] (WiFi firmware update on port 8080)\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  stop ota\r\n"
MESH_COMPANION_HELP_END
#endif
#if defined(COMPANION_EXCLUSIVE_WIFI_BLE)
MESH_COMPANION_HELP_BEGIN
    "  get companion.transport\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set companion.transport <wifi|ble>\r\n"
MESH_COMPANION_HELP_END
#endif
MESH_COMPANION_HELP_BEGIN
    "  get wifi.powersave\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set wifi.powersave <none|min|max>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  get wifi.protocol\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  get wifi.status (connection, saved/boot/live settings)\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set wifi.protocol <bg|bgn> (saved; reboot required)\r\n"
MESH_COMPANION_HELP_END
#if !defined(COMPANION_EXCLUSIVE_WIFI_BLE)
MESH_COMPANION_HELP_BEGIN
    "  get wifi.mode\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set wifi.mode <normal|wifi-only> (saved; reboot required)\r\n"
MESH_COMPANION_HELP_END
#endif
#ifdef WITH_WEBCONFIG
MESH_COMPANION_HELP_BEGIN
    "  get wifi.{ssid|pwd}\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set wifi.ssid <network name>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set wifi.pwd <password>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  get webui\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set webui <on|off>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  start webconfig [ap]\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  stop webconfig\r\n"
MESH_COMPANION_HELP_END
#endif
#endif
#if defined(MESH_PRIMARY_ESPNOW) && MESH_PRIMARY_ESPNOW
MESH_COMPANION_HELP_BEGIN
    "  get espnow.channel\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  set espnow.channel <1-13>\r\n"
MESH_COMPANION_HELP_END
#endif
// Keep native USB producer writes within 256 bytes.
#if !defined(MESH_RADIO_SWITCH_TUNING) || MESH_RADIO_SWITCH_TUNING
MESH_COMPANION_HELP_BEGIN
    "  get radio.fast / get radio.fast.caps\r\n"
    "  set radio.fast <on|off> / set radio.fast.spi <0|2|4|8|9|16>\r\n"
MESH_COMPANION_HELP_END
#endif
MESH_COMPANION_HELP_BEGIN
    "  get radio.rxps.config\r\n"
    "  get/set radio.rxps <off|on|level 1-10 [preamble 16|32]|rx_us sleep_us>\r\n"
    "  get/set radio.cad <on|off>\r\n"
    "  set radio.cad timings <scan_ms|auto> <retry_ms|auto> <max_ms|auto>\r\n"
    "  get/set radio.rxgain <on|off>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  get rxps.wd\r\n"
    "  get/set radio.rxps.rfrx_disabled <on|off> (boot)\r\n"
    "  clear stats\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  get/set radio.fem.rxgain <on|off>\r\n"
    "  get/set radio.fem.txgain <on|off>\r\n"
    "  get/set flood.retry.count <0-15>\r\n"
    "  get/set flood.retry.path <0-63|off>\r\n"
    "  get/set flood.retry.group.path <0-63|off>\r\n"
    "  get/set flood.retry.advert <on|off>\r\n"
    "  card\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  import <meshcore://card>\r\n"
    "  clock\r\n"
    "  time <epoch-seconds>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  list [n]\r\n"
    "  show [adverts|channels|emergency] [on|off]\r\n"
    "  to [name|prefix]\r\n"
    "  path [direct|clear|hop,...]\r\n"
    "  send <text>\r\n"
    "  login <admin-password>\r\n"
    "  cmd <remote-command>\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  trace [name|prefix]\r\n"
    "  trace path <1|2|4> <prefixes...>\r\n"
    "  advert\r\n"
    "  reset path\r\n"
    "  public <text>\r\n"
    "  channels\r\n"
    "  channel <name-or-slot> <text>\r\n"
MESH_COMPANION_HELP_END
#if defined(NRF52_PLATFORM) && defined(EXTRAFS) && !defined(QSPIFLASH)
MESH_COMPANION_HELP_BEGIN
    "  repair extrafs (erases ExtraFS)\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  scan extrafs erase (USB; reboot; erases pages)\r\n"
MESH_COMPANION_HELP_END
#endif
#if COMPANION_FEATURE_TEMP_RADIO || COMPANION_FEATURE_OTA_CLI
#if COMPANION_FEATURE_TEMP_RADIO
MESH_COMPANION_HELP_BEGIN
    "  tempradio [freq,bw,sf,cr,minutes]\r\n"
MESH_COMPANION_HELP_END
MESH_COMPANION_HELP_BEGIN
    "  normalradio\r\n"
MESH_COMPANION_HELP_END
#endif
#if COMPANION_FEATURE_OTA_CLI
MESH_COMPANION_HELP_BEGIN
    "  ota {status|ls|announce|folder|config|...}\r\n"
MESH_COMPANION_HELP_END
#endif
#endif
MESH_COMPANION_HELP_BEGIN
    "  reboot\r\n"
    "  ver\r\n"
MESH_COMPANION_HELP_END
#if defined(NRF52_PLATFORM)
MESH_COMPANION_HELP_LINE_BEGIN
    "  uf2reset"
MESH_COMPANION_HELP_LINE_END
#endif
