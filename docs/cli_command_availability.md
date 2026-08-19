# CLI Command Availability Matrix

This page is the command-by-command companion to
[CLI Availability by Firmware Build](cli_build_matrix.md). Each command name
links to its detailed documentation.

The tables cover the text administration CLI used by repeater, room-server,
sensor, and bridge firmware. Companion firmware uses the companion protocol,
KISS firmware uses framed KISS/TNC commands, and terminal-chat firmware has its
own interface, so those build roles are not represented here.

Build columns mean:

- **Standard** - the ordinary non-MQTT artifact, without the `logging` or
  explicit `ota` filename marker.
- **Logging** - the ordinary non-MQTT `-logging-` artifact. Logging does not
  remove commands by itself.
- **LoRa OTA** - the explicit `-ota-` repeater or repeater-bridge artifact. Its
  optional external-sensor drivers are removed, but onboard GPS is retained.
- **FULL** - the expanded-partition ESP32 artifact with LoRa OTA and the
  complete parser. MQTT observers and ESP-NOW bridges always use this profile.
- **FULL logging** - the expanded-partition ESP32 non-MQTT artifact with
  logging, LoRa OTA, and the complete parser.

Cell values mean:

- **Yes** - the parser includes the command. The Scope column still applies.
- **No** - the profile does not expose the command.
- **Feature** - available only when the target compiles the feature or hardware
  named in Scope.
- **Serial** - available only from the local serial console.
- **Manifest** - available only when the MQTT target defines
  `OTA_MANIFEST_BASE`.
- **Limited** - the family exists, but the limitation in Scope applies.

Runtime state can still make an included command fail. Examples include no GPS
fix, no WiFi connection, an inactive bridge, or an nRF52 bootloader without
`.mota` apply support.

## nRF52

| Area | Command | Scope | Standard | Logging | LoRa OTA |
|---|---|---|---|---|---|
| Operational | [`reboot`](cli_commands.md#reboot-the-node) | All text CLI roles | Yes | Yes | Yes |
| Operational | [`poweroff`; `shutdown`](cli_commands.md#power-off-the-node) | Board power-off support | Yes | Yes | Yes |
| Operational | [`uf2reset`](cli_commands.md#enter-the-uf2-bootloader-nrf52-only) | Local serial; supported UF2 boards | Serial | Serial | Serial |
| Operational | [`clkreboot`](cli_commands.md#reset-the-clock-and-reboot) | All text CLI roles | Yes | Yes | Yes |
| Operational | [`clock sync`](cli_commands.md#sync-the-clock-with-the-remote-device) | All text CLI roles | Yes | Yes | Yes |
| Operational | [`clock`](cli_commands.md#display-current-time-in-utc) | All text CLI roles | Yes | Yes | Yes |
| Operational | [`time <epoch_seconds>`](cli_commands.md#set-the-time-to-a-specific-timestamp) | Clock only moves forward | Yes | Yes | Yes |
| Operational | [`advert`](cli_commands.md#send-a-flood-advert) | Advert-capable role | Yes | Yes | Yes |
| Operational | [`advert.zerohop`](cli_commands.md#send-a-zero-hop-advert) | Advert-capable role | Yes | Yes | Yes |
| Operational | [`erase`](cli_commands.md#erasefactory-reset) | Local serial | Serial | Serial | Serial |
| Information | [`ver`](cli_commands.md#get-the-version) | All text CLI roles | Yes | Yes | Yes |
| Information | [`board`](cli_commands.md#show-the-hardware-name) | All text CLI roles | Yes | Yes | Yes |
| Diagnostics | [`memory`](#memory) | ESP32 only | No | No | No |
| Diagnostics | [`sensor`](#sensor-hardware-summary) | Hardware wiring summary | Yes | Yes | Yes |
| Diagnostics | [`powerlog`](#powerlog) | Reset, shutdown, and boot-voltage summary | Yes | Yes | Yes |
| Neighbors | [`neighbors`](cli_commands.md#list-nearby-neighbors) | Role with a neighbor table | Yes | Yes | Yes |
| Neighbors | [`neighbor.remove <pubkey_prefix>`](cli_commands.md#remove-a-neighbor) | Role with a mutable neighbor table | Yes | Yes | Yes |
| Neighbors | [`discover.neighbors`](cli_commands.md#discover-zero-hop-neighbors) | Repeater; some MQTT room servers | Yes | Yes | Yes |
| Neighbors | [`discover.scopes`](cli_commands.md#discover-neighbor-scopes-mqtt-observer-neighbors-feature) | MQTT observer with compiled neighbor support | No | No | No |
| Statistics | [`clear stats`](cli_commands.md#clear-stats) | All full-parser text CLI roles | Yes | Yes | Yes |
| Statistics | [`stats-core`](cli_commands.md#stats-core) | Local serial | Serial | Serial | Serial |
| Statistics | [`stats-radio`](cli_commands.md#stats-radio) | Local serial | Serial | Serial | Serial |
| Statistics | [`stats-radio-diag`](#stats-radio-diag) | Local serial | Serial | Serial | Serial |
| Statistics | [`stats-packets`](cli_commands.md#stats-packets) | Local serial | Serial | Serial | Serial |
| Statistics | [`get telemetry.temp/volt`; optional GPS history; `get/set telemetry.tx`](cli_commands.md#read-repeater-telemetry-history) | Non-STM32 repeater; GPS commands require a GPS provider; remote access requires administrator | Yes | Yes | Yes |
| Logging | [`log start`; `log stop`; `log erase`](cli_commands.md#logging) | Storage-backed roles retain data; other roles can return empty data | Yes | Yes | Yes |
| Logging | [`log`](cli_commands.md#print-the-captured-log-to-the-serial-terminal) | Local serial | Serial | Serial | Serial |
| Logging | [`get/set usb.logging`](cli_commands.md#control-live-usb-logging) | Logging artifacts; session-only live USB output gate | No | Yes | No |
| Radio | [`get radio`; `set radio ...`](cli_commands.md#view-or-change-this-nodes-radio-parameters) | All text CLI roles | Yes | Yes | Yes |
| Radio | [`get tx`; `set tx <dbm>`](cli_commands.md#view-or-change-this-nodes-transmit-power) | Board TX-power limits apply | Yes | Yes | Yes |
| Radio | [`tempradio ...`; `normalradio`](cli_commands.md#change-the-radio-parameters-for-a-set-duration) | Full parser | Yes | Yes | Yes |
| Radio | [`get/set/del radioat`; `get/set/del tempradioat`](cli_commands.md#schedule-radio-parameter-changes) | Full parser | Yes | Yes | Yes |
| Radio | [`get freq`; `set freq <mhz>`](cli_commands.md#view-or-change-this-nodes-frequency) | `set` is local serial only | Yes | Yes | Yes |
| Radio | [`get/set radio.rxgain`](cli_commands.md#view-or-change-this-nodes-rx-boosted-gain-mode-sx12xx-and-lr1110-v1141) | Supported radio | Feature | Feature | Feature |
| Radio | [`get/set radio.fem.rxgain`](cli_commands.md#view-or-change-the-lora-fem-receive-path-gain-state-on-supported-boards) | Controllable LoRa FEM | Feature | Feature | Feature |
| Radio | [`get/set radio.fem.txgain`](cli_commands.md#view-or-change-the-lora-fem-transmit-path-gain-state-on-supported-boards) | Controllable LoRa FEM | Feature | Feature | Feature |
| Radio | [`get/set radio.rxps`; `get rxps.wd`](#radio-rxps) | RX power-saving support | Feature | Feature | Feature |
| System | [`get/set name`](cli_commands.md#view-or-change-this-nodes-name) | All text CLI roles | Yes | Yes | Yes |
| System | [`get/set lat`](cli_commands.md#view-or-change-this-nodes-latitude) | All text CLI roles | Yes | Yes | Yes |
| System | [`get/set lon`](cli_commands.md#view-or-change-this-nodes-longitude) | All text CLI roles | Yes | Yes | Yes |
| System | [`get/set prv.key`](cli_commands.md#view-or-change-this-nodes-identity-private-key) | `get` is local serial only | Yes | Yes | Yes |
| System | [`password <new_password>`](cli_commands.md#change-this-nodes-admin-password) | Administrator | Yes | Yes | Yes |
| System | [`get/set guest.password`](cli_commands.md#view-or-change-this-nodes-guest-password) | Role with guest administration | Yes | Yes | Yes |
| System | [`get/set owner.info`](cli_commands.md#view-or-change-this-nodes-owner-info) | All text CLI roles | Yes | Yes | Yes |
| System | [`get/set adc.multiplier`](cli_commands.md#fine-tune-the-battery-reading) | Board ADC override support | Feature | Feature | Feature |
| System | [`send text.flood <message>`](cli_commands.md#send-a-repeater-flood-text) | Repeater | Yes | Yes | Yes |
| System | [`get/set battery.alert`; `get battery.alert.region`](cli_commands.md#view-or-change-battery-alert-state) | Repeater | Yes | Yes | Yes |
| System | [`get/set battery.alert.low`; `get/set battery.alert.critical`](cli_commands.md#view-or-change-battery-alert-thresholds) | Repeater | Yes | Yes | Yes |
| System | [`get/set rx.watchdog`](cli_commands.md#enable-or-disable-the-rx-inactivity-watchdog-repeater-only) | Repeater | Yes | Yes | Yes |
| System | [`get/set system.watchdog`](cli_commands.md#enable-or-disable-the-nrf52-system-watchdog) | nRF52 | Yes | Yes | Yes |
| System | [`get public.key`](cli_commands.md#view-this-nodes-public-key) | All text CLI roles | Yes | Yes | Yes |
| System | [`get role`](cli_commands.md#view-this-nodes-configured-role) | All text CLI roles | Yes | Yes | Yes |
| System | [`powersaving`; `powersaving on/off`](cli_commands.md#view-or-change-this-nodes-power-saving-flag) | Supported repeater board | Feature | Feature | Feature |
| System | [`get/set reboot.interval`](#reboot-interval) | Full parser | Yes | Yes | Yes |
| Clock sync | [`get/set clock.sync.*`; `clock.sync.mesh now`](cli_commands.md#estimate-and-correct-infrastructure-node-time-after-startup) | Repeater, sensor, and room server; `clock.sync.internet` needs MQTT repeater | Yes | Yes | Yes |
| Routing | [`get/set repeat`](cli_commands.md#view-or-change-this-nodes-repeat-flag) | Forwarding-capable role | Yes | Yes | Yes |
| Routing | [`get/set path.hash.mode`](cli_commands.md#view-or-change-this-nodes-advert-path-hash-size) | Role that supports path-hash selection | Yes | Yes | Yes |
| Routing | [`get/set loop.detect`](cli_commands.md#view-or-change-this-nodes-loop-detection) | Repeater | Yes | Yes | Yes |
| Routing | [`get/set txdelay`](cli_commands.md#view-or-change-the-retransmit-delay-factor-for-flood-traffic) | All text CLI roles, including bridges | Yes | Yes | Yes |
| Routing | [`get/set direct.txdelay`](cli_commands.md#view-or-change-the-retransmit-delay-factor-for-direct-traffic) | Full parser | Yes | Yes | Yes |
| Routing | [`get/set rxdelay`](cli_commands.md#experimental-view-or-change-the-processing-delay-for-received-traffic) | All text CLI roles, including bridges | Yes | Yes | Yes |
| Routing | [`get/set dutycycle`](cli_commands.md#view-or-change-the-duty-cycle-limit) | Full parser | Yes | Yes | Yes |
| Routing | [`get/set af`](cli_commands.md#view-or-change-the-airtime-factor-duty-cycle-limit) | Full parser | Yes | Yes | Yes |
| Routing | [`get/set int.thresh`](cli_commands.md#view-or-change-the-local-interference-threshold) | All text CLI roles | Yes | Yes | Yes |
| Routing | [`get/set cad`](cli_commands.md#enable-or-disable-hardware-channel-activity-detection-cad) | Radio CAD support | Feature | Feature | Feature |
| Routing | [`get/set agc.reset.interval`](cli_commands.md#view-or-change-the-agc-reset-interval) | All text CLI roles | Yes | Yes | Yes |
| Routing | [`get/set radio.watchdog`](cli_commands.md#view-or-change-the-radio-watchdog-interval-mqtt-observer-only) | MQTT observer | No | No | No |
| Routing | [`get/set multi.acks`](cli_commands.md#enable-or-disable-multi-acks-support) | Full parser | Yes | Yes | Yes |
| Routing | [`get/set flood.advert.interval`](cli_commands.md#view-or-change-the-flood-advert-interval) | Repeater | Yes | Yes | Yes |
| Routing | [`get/set advert.interval`](cli_commands.md#view-or-change-the-zero-hop-advert-interval) | Advert-capable role | Yes | Yes | Yes |
| Routing | [`get/set flood.max`](cli_commands.md#limit-the-number-of-hops-for-a-flood-message) | Repeater | Yes | Yes | Yes |
| Routing | [`get/set flood.max.unscoped`](cli_commands.md#limit-the-number-of-hops-for-an-unscoped-flood-message) | Repeater | Yes | Yes | Yes |
| Routing | [`get/set flood.max.advert`](cli_commands.md#limit-the-number-of-hops-for-an-advert-flood-message) | Repeater | Yes | Yes | Yes |
| Routing | [`get/set flood.channel.data`; `get/set flood.channel.data.hops`](cli_commands.md#forward-flood-group-data-packets-on-repeaters) | Repeater | Yes | Yes | Yes |
| Routing | [`get/set/del flood.channel.scope*`](cli_commands.md#force-a-transport-scope-onto-floods) | Repeater | Yes | Yes | Yes |
| Routing | [`get/set/del flood.channel.scope.require*`](cli_commands.md#require-valid-incoming-scopes-only-on-selected-channels) | Repeater | Yes | Yes | Yes |
| Routing | [`get/set/del flood.rule*`; `get/set/del flood.filter*`; `get/set/del flood.filter.blacklist*`](cli_commands.md#change-persistent-flood-rules-in-the-field) | Repeater; `flood.rule`/`flood.filter` also on FULL ESP32 room server (no blacklist) | Yes | Yes | Yes |
| Routing | [`get/set/del flood.moderation*`](cli_commands.md#moderate-flood-group-text-by-channel-sender-and-source-path) | Repeater | Yes | Yes | Yes |
| Routing | [`get/set outpath`](halo_keymind_settings.md#direct-path-overrides) | Repeater remote-client context | Yes | Yes | Yes |
| Routing | [`get/set altpath`](halo_keymind_settings.md#direct-path-overrides) | Repeater remote-client context | Yes | Yes | Yes |
| ACL | [`setperm <pubkey> <permissions>`](cli_commands.md#add-update-or-remove-permissions-for-a-companion) | Repeater, room server, or sensor | Yes | Yes | Yes |
| ACL | [`get acl`](cli_commands.md#view-the-current-acl) | Local serial | Serial | Serial | Serial |
| ACL | [`get/set allow.read.only`](cli_commands.md#view-or-change-this-room-servers-read-only-flag) | Room server | Yes | Yes | No |
| Regions | [`region load`](cli_commands.md#bulk-load-region-lists); [`region save`](cli_commands.md#save-any-changes-to-regions-made-since-reboot) | Role with region storage | Yes | Yes | Yes |
| Regions | [`region allowf`](cli_commands.md#allow-a-region); [`region denyf`](cli_commands.md#block-a-region) | Role with region storage | Yes | Yes | Yes |
| Regions | [`region get`](cli_commands.md#show-information-for-a-region); [`region list`](cli_commands.md#view-all-regions); [`region`](cli_commands.md#dump-all-defined-regions-and-flood-permissions) | Role with region storage | Yes | Yes | Yes |
| Regions | [`region home`](cli_commands.md#view-or-change-the-home-region-for-this-node); [`region default`](cli_commands.md#view-or-change-the-default-scope-region-for-this-node) | Role with region storage | Yes | Yes | Yes |
| Regions | [`region put`](cli_commands.md#create-a-new-region); [`region def`](cli_commands.md#define-region-hierarchy-single-line); [`region remove`](cli_commands.md#remove-a-region) | Role with region storage | Yes | Yes | Yes |
| Retry | [`get/set direct.retry`](cli_commands.md#view-or-change-direct-retry-state); [`get/set direct.retry.heard`](cli_commands.md#view-or-change-direct-retry-heard-table-gate) | Role with basic retry support | Yes | Yes | Yes |
| Retry | [`get/set retry.preset`](cli_commands.md#view-or-apply-a-retry-preset) | Role with retry support | Yes | Yes | Yes |
| Retry | [`get/set flood.retry.count`](cli_commands.md#view-or-change-flood-retry-count); [`get/set flood.retry.path`](cli_commands.md#view-or-change-flood-retry-path-gate); [`get/set flood.retry.group.path`](cli_commands.md#view-or-change-the-group-data-flood-retry-path-gate) | Repeater | Yes | Yes | Yes |
| Retry | [`get/set flood.retry.advert`](cli_commands.md#view-or-change-flood-retry-advert-handling); [`get/set flood.retry.prefixes`](cli_commands.md#view-or-change-flood-retry-target-prefixes); [`get/set flood.retry.ignore`](cli_commands.md#view-or-change-flood-retry-ignored-prefixes) | Repeater | Yes | Yes | Yes |
| Retry | [`get/set flood.retry.bridge`](cli_commands.md#view-or-change-flood-retry-bridge-mode); [`get/set flood.retry.bucket`](cli_commands.md#view-or-change-flood-retry-bridge-buckets) | Repeater bridge retry support | Feature | Feature | Feature |
| Retry | [`get/set direct.retry.count`](cli_commands.md#view-or-change-direct-retry-count); [`get/set direct.retry.base`](cli_commands.md#view-or-change-direct-retry-base-delay); [`get/set direct.retry.step`](cli_commands.md#view-or-change-direct-retry-step-delay) | Role with retry support | Yes | Yes | Yes |
| Retry | [`get/set direct.retry.margin`](cli_commands.md#view-or-change-direct-retry-snr-margin); [`get/set direct.retry.cr`](cli_commands.md#view-or-change-adaptive-direct-retry-coding-rate) | Role with retry support | Yes | Yes | Yes |
| Retry | [`get/set/clear recent.repeater`; `get recent.repeaters`](cli_commands.md#view-seed-or-clear-the-recent-repeater-table) | Repeater | Yes | Yes | Yes |
| GPS | [`gps`; `gps on/off`](cli_commands.md#view-or-change-gps-state) | Compiled GPS | Feature | Feature | Feature |
| GPS | [`gps sync`](cli_commands.md#sync-this-nodes-clock-with-gps-time) | Compiled GPS | Feature | Feature | Feature |
| GPS | [`gps setloc`](cli_commands.md#set-this-nodes-location-based-on-the-gps-coordinates) | Compiled GPS | Feature | Feature | Feature |
| GPS | [`gps advert [none/share/prefs]`](cli_commands.md#view-or-change-the-gps-advert-policy) | Compiled GPS | Feature | Feature | Feature |
| Sensors | [`get/set telemetry.access`](cli_commands.md#view-or-change-telemetry-access-mode) | Sensor-capable role | Feature | Feature | Limited |
| Sensors | [`sensor list [start]`](cli_commands.md#view-the-list-of-sensors-on-this-node) | Compiled sensor manager; OTA omits external sensors | Feature | Feature | Limited |
| Sensors | [`sensor get`; `sensor set`](cli_commands.md#view-or-change-the-value-of-a-sensor) | Compiled sensor setting; OTA omits external sensors | Feature | Feature | Limited |
| Sensors | [`io [r/s/t]<hex>`](#sensor-io) | Simple sensor role with board GPIO support | Feature | Feature | No |
| Bridge | [`get bridge.type`](cli_commands.md#view-the-compiled-bridge-type) | Compiled bridge | Feature | Feature | Feature |
| Bridge | [`get/set bridge.enabled`](cli_commands.md#view-or-change-the-bridge-enabled-flag) | Compiled bridge | Feature | Feature | Feature |
| Bridge | [`get/set bridge.delay`](cli_commands.md#add-a-delay-to-packets-routed-through-this-bridge) | Compiled bridge | Feature | Feature | Feature |
| Bridge | [`get/set bridge.source`](cli_commands.md#view-or-change-the-source-of-packets-bridged-to-the-external-interface) | Compiled bridge | Feature | Feature | Feature |
| Bridge | [`get/set bridge.baud`](cli_commands.md#view-or-change-the-speed-of-the-bridge-rs-232-only) | RS-232 bridge | Feature | Feature | Feature |
| Bridge | [`get/set bridge.channel`](cli_commands.md#view-or-change-the-channel-used-for-bridging-espnow-only) | ESP-NOW is ESP32 only | No | No | No |
| Bridge | [`get/set bridge.secret`](cli_commands.md#set-the-esp-now-secret) | ESP-NOW is ESP32 only | No | No | No |
| Board | [`get bootloader.ver`](cli_commands.md#view-the-bootloader-version-nrf52-only) | nRF52 bootloader metadata | Yes | Yes | Yes |
| Board | [`get pwrmgt.support`; `get pwrmgt.source`; `get pwrmgt.bootreason`; `get pwrmgt.bootmv`](nrf52_power_management.md#cli-commands) | Board power-management implementation | Feature | Feature | Feature |
| Ethernet | [`eth.status`](cli_commands.md#view-ethernet-connection-status) | Ethernet target | Feature | Feature | No |
| Browser OTA | [`start ota [ap]`; `stop ota`](cli_commands.md#start-or-stop-an-over-the-air-ota-firmware-update) | ESP32 browser uploader | No | No | No |
| WebConfig | [`start webconfig [ap]`; `stop webconfig`; `get/set webui`](cli_commands.md#browser-configuration-portal-esp32-repeater-and-room-server) | ESP32 WebConfig | No | No | No |
| WiFi | [`get/set wifi.ssid`; `set wifi.pwd`; `get wifi.status`; `get/set wifi.powersave`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#wifi-commands) | ESP32 WiFi | No | No | No |
| WiFi | [`get/set wifi.cli`](cli_commands.md#browser-configuration-portal-esp32-repeater-and-room-server) | ESP32 WebConfig | No | No | No |
| LoRa OTA | [`ota help`; `ota ?`; `ota h`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes |
| LoRa OTA | [`ota`; `ota status`; `ota st`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes |
| LoRa OTA | [`ota stats`](ota_user_guide.md#1-see-what-im-running-and-whether-anything-is-going-on) | LoRa OTA build | No | No | Yes |
| LoRa OTA | [`ota ls`; `ota neighbors`; `ota nbrs`; `ota updates`; `ota n`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes |
| LoRa OTA | [`ota get`; `ota pull`; `ota download`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build; nRF52 installs in-place deltas | No | No | Yes |
| LoRa OTA | [`ota install`; `ota apply`; `ota applydelta`](ota_protocol.md#11-cli-surface-otaclicpp) | Compatible bootloader and completed update | No | No | Yes |
| LoRa OTA | [`ota rescue install <base_hash16>`](ota_protocol.md#12-apply-bootloader-contract) | Internal-flash nRF52 LoRa OTA build with failed app-side EndF validation | No | No | Feature |
| LoRa OTA | [`ota cancel`; `ota drop`; `ota stop`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes |
| LoRa OTA | [`ota announce`; `ota adv`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes |
| LoRa OTA | [`ota self`; `ota id`](ota_protocol.md#11-cli-surface-otaclicpp) | Firmware with EndF trailer | No | No | Yes |
| LoRa OTA | [`ota folder`; `ota fold`](ota_protocol.md#11-cli-surface-otaclicpp) | `on` needs compiled folder transport | No | No | Feature |
| LoRa OTA | [`ota config`; `ota cfg`; `ota set`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes |
| LoRa OTA | [`ota key`; `ota keys`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes |
| LoRa OTA | [`ota dev ...`](ota_protocol.md#11-cli-surface-otaclicpp) | Developer diagnostics | No | No | Yes |
| MQTT | [`get/set mqttN.preset`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-slot-commands) | MQTT observer | No | No | No |
| MQTT | [`get/set mqttN.server`; `get/set mqttN.port`; `get/set mqttN.username`; `get/set mqttN.password`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-slot-commands) | MQTT observer | No | No | No |
| MQTT | [`get/set mqttN.token`; `get/set mqttN.topic`; `get/set mqttN.audience`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-slot-commands) | MQTT observer | No | No | No |
| MQTT | [`get mqttN.diag`](#mqtt-slot-diagnostics) | MQTT observer | No | No | No |
| MQTT | [`get/set mqtt.origin`; `get/set mqtt.iata`; `get mqtt.presets`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-shared-commands) | MQTT observer | No | No | No |
| MQTT | [`get mqtt.stats`](#mqtt-stats) | MQTT observer | No | No | No |
| MQTT | [`get/set mqtt.status`; `get/set mqtt.packets`; `get/set mqtt.raw`; `get/set mqtt.interval`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-shared-commands) | MQTT observer | No | No | No |
| MQTT | [`get/set mqtt.rx`](cli_commands.md#view-or-change-mqtt-rx-packet-uplinking); [`get/set mqtt.tx`](cli_commands.md#view-or-change-mqtt-tx-packet-uplinking) | MQTT observer | No | No | No |
| MQTT | [`get/set mqtt.neighbors`](cli_commands.md#view-or-change-periodic-neighbors-publishing-mqtt-observer-neighbors-feature); [`get/set mqtt.neighbors.interval`](cli_commands.md#view-or-change-the-neighbors-publish-interval-mqtt-observer-neighbors-feature) | MQTT observer with compiled neighbor support | No | No | No |
| MQTT | [`get/set mqtt.ntp`](cli_commands.md#view-or-change-the-ntp-server-mqtt-observer-only) | MQTT observer | No | No | No |
| MQTT | [`get mqtt.ntp.diag`](cli_commands.md#diagnose-ntp-server-connectivity-mqtt-observer-only) | Full MQTT observer | No | No | No |
| MQTT | [`get/set timezone`; `get/set timezone.offset`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#timezone-commands) | MQTT observer | No | No | No |
| MQTT | [`get/set mqtt.analyzer.us`; `get/set mqtt.analyzer.eu`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#migration-from-old-configuration) | Legacy MQTT aliases | No | No | No |
| MQTT | [`get/set mqtt.owner`; `get/set mqtt.email`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-shared-commands) | MQTT observer; `get` is local serial only | No | No | No |
| MQTT | [`get mqtt.config.valid`](#mqtt-config-valid) | MQTT observer | No | No | No |
| SNMP | [`get/set snmp`; `get/set snmp.community`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_SNMP.md#cli-commands) | MQTT target compiled with SNMP | No | No | No |
| Alerts | [`get/set alert`; `get/set alert.psk`; `get/set alert.hashtag`; `get/set alert.region`; `get/set alert.wifi`; `get/set alert.mqtt`; `get/set alert.interval`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/ALERTS.md#cli) | MQTT observer | No | No | No |
| Alerts | [`alert test [message]`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/ALERTS.md#cli) | MQTT observer with configured alert channel | No | No | No |
| TLS | [`tls.bundletest <host>`](#tls-bundle-test) | MQTT target with embedded certificate bundle | No | No | No |
| Manifest OTA | [`ota check`](#manifest-ota) | MQTT target with `OTA_MANIFEST_BASE` | No | No | No |
| Manifest OTA | [`ota update`](#manifest-ota) | MQTT target with `OTA_MANIFEST_BASE` | No | No | No |

## ESP32

| Area | Command | Scope | Standard | Logging | LoRa OTA | FULL | FULL logging |
|---|---|---|---|---|---|---|---|
| Operational | [`reboot`](cli_commands.md#reboot-the-node) | All text CLI roles | Yes | Yes | Yes | Yes | Yes |
| Operational | [`poweroff`; `shutdown`](cli_commands.md#power-off-the-node) | Board power-off support | Yes | Yes | Yes | Yes | Yes |
| Operational | [`uf2reset`](cli_commands.md#enter-the-uf2-bootloader-nrf52-only) | nRF52 only | No | No | No | No | No |
| Operational | [`clkreboot`](cli_commands.md#reset-the-clock-and-reboot) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Operational | [`clock sync`](cli_commands.md#sync-the-clock-with-the-remote-device) | All text CLI roles | Yes | Yes | Yes | Yes | Yes |
| Operational | [`clock`](cli_commands.md#display-current-time-in-utc) | All text CLI roles | Yes | Yes | Yes | Yes | Yes |
| Operational | [`time <epoch_seconds>`](cli_commands.md#set-the-time-to-a-specific-timestamp) | Clock only moves forward | Yes | Yes | Yes | Yes | Yes |
| Operational | [`advert`](cli_commands.md#send-a-flood-advert) | Advert-capable role | Yes | Yes | Yes | Yes | Yes |
| Operational | [`advert.zerohop`](cli_commands.md#send-a-zero-hop-advert) | Advert-capable role | Yes | Yes | Yes | Yes | Yes |
| Operational | [`erase`](cli_commands.md#erasefactory-reset) | Local serial | Serial | Serial | Serial | Serial | Serial |
| Information | [`ver`](cli_commands.md#get-the-version) | All text CLI roles | Yes | Yes | Yes | Yes | Yes |
| Information | [`board`](cli_commands.md#show-the-hardware-name) | All text CLI roles | Yes | Yes | Yes | Yes | Yes |
| Diagnostics | [`memory`](#memory) | ESP32 heap and PSRAM summary | Yes | Yes | Yes | Yes | Yes |
| Diagnostics | [`sensor`](#sensor-hardware-summary) | Hardware wiring summary | Yes | Yes | Yes | Yes | Yes |
| Diagnostics | [`powerlog`](#powerlog) | Reset-reason summary | Yes | Yes | Yes | Yes | Yes |
| Neighbors | [`neighbors`](cli_commands.md#list-nearby-neighbors) | Role with a neighbor table | Yes | Yes | Yes | Yes | Yes |
| Neighbors | [`neighbor.remove <pubkey_prefix>`](cli_commands.md#remove-a-neighbor) | Role with a mutable neighbor table | Yes | Yes | Yes | Yes | Yes |
| Neighbors | [`discover.neighbors`](cli_commands.md#discover-zero-hop-neighbors) | Repeater; some MQTT room servers | Yes | Yes | Yes | Yes | Yes |
| Neighbors | [`discover.scopes`](cli_commands.md#discover-neighbor-scopes-mqtt-observer-neighbors-feature) | MQTT observer with compiled neighbor support | No | No | No | Feature | No |
| Statistics | [`clear stats`](cli_commands.md#clear-stats) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Statistics | [`stats-core`](cli_commands.md#stats-core) | Local serial | Serial | Serial | Serial | Serial | Serial |
| Statistics | [`stats-radio`](cli_commands.md#stats-radio) | Local serial | Serial | Serial | Serial | Serial | Serial |
| Statistics | [`stats-radio-diag`](#stats-radio-diag) | Local serial | Serial | Serial | Serial | Serial | Serial |
| Statistics | [`stats-packets`](cli_commands.md#stats-packets) | Local serial | Serial | Serial | Serial | Serial | Serial |
| Statistics | [`get telemetry.temp/volt`; optional GPS history; `get/set telemetry.tx`](cli_commands.md#read-repeater-telemetry-history) | Non-STM32 repeater; GPS commands require a GPS provider; remote access requires administrator | Yes | Yes | Yes | Yes | Yes |
| Logging | [`log start`; `log stop`; `log erase`](cli_commands.md#logging) | Storage-backed roles retain data | Yes | Yes | Yes | Yes | Yes |
| Logging | [`log`](cli_commands.md#print-the-captured-log-to-the-serial-terminal) | Local serial | Serial | Serial | Serial | Serial | Serial |
| Logging | [`get/set usb.logging`](cli_commands.md#control-live-usb-logging) | Logging artifacts; session-only live USB output gate | No | Yes | No | No | Yes |
| Radio | [`get radio`; `set radio ...`](cli_commands.md#view-or-change-this-nodes-radio-parameters) | All text CLI roles | Yes | Yes | Yes | Yes | Yes |
| Radio | [`get tx`; `set tx <dbm>`](cli_commands.md#view-or-change-this-nodes-transmit-power) | Board TX-power limits apply | Yes | Yes | Yes | Yes | Yes |
| Radio | [`tempradio ...`; `normalradio`](cli_commands.md#change-the-radio-parameters-for-a-set-duration) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Radio | [`get/set/del radioat`; `get/set/del tempradioat`](cli_commands.md#schedule-radio-parameter-changes) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Radio | [`get freq`; `set freq <mhz>`](cli_commands.md#view-or-change-this-nodes-frequency) | `set` is local serial only | Yes | Yes | Yes | Yes | Yes |
| Radio | [`get/set radio.rxgain`](cli_commands.md#view-or-change-this-nodes-rx-boosted-gain-mode-sx12xx-and-lr1110-v1141) | Supported radio | Feature | Feature | Feature | Feature | Feature |
| Radio | [`get/set radio.fem.rxgain`](cli_commands.md#view-or-change-the-lora-fem-receive-path-gain-state-on-supported-boards) | Controllable LoRa FEM | Feature | Feature | Feature | Feature | Feature |
| Radio | [`get/set radio.fem.txgain`](cli_commands.md#view-or-change-the-lora-fem-transmit-path-gain-state-on-supported-boards) | Controllable LoRa FEM | Feature | Feature | Feature | Feature | Feature |
| Radio | [`get/set radio.rxps`; `get rxps.wd`](#radio-rxps) | Full parser and RX power-saving support | Feature | Feature | Feature | Feature | Feature |
| System | [`get/set name`](cli_commands.md#view-or-change-this-nodes-name) | All full-parser text CLI roles | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set lat`](cli_commands.md#view-or-change-this-nodes-latitude) | All full-parser text CLI roles | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set lon`](cli_commands.md#view-or-change-this-nodes-longitude) | All full-parser text CLI roles | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set prv.key`](cli_commands.md#view-or-change-this-nodes-identity-private-key) | `get` is local serial only | Yes | Yes | Yes | Yes | Yes |
| System | [`password <new_password>`](cli_commands.md#change-this-nodes-admin-password) | Administrator | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set guest.password`](cli_commands.md#view-or-change-this-nodes-guest-password) | Role with guest administration | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set owner.info`](cli_commands.md#view-or-change-this-nodes-owner-info) | Full parser | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set adc.multiplier`](cli_commands.md#fine-tune-the-battery-reading) | Board ADC override support | Feature | Feature | Feature | Feature | Feature |
| System | [`send text.flood <message>`](cli_commands.md#send-a-repeater-flood-text) | Repeater | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set battery.alert`; `get battery.alert.region`](cli_commands.md#view-or-change-battery-alert-state) | Repeater | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set battery.alert.low`; `get/set battery.alert.critical`](cli_commands.md#view-or-change-battery-alert-thresholds) | Repeater | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set rx.watchdog`](cli_commands.md#enable-or-disable-the-rx-inactivity-watchdog-repeater-only) | Repeater | Yes | Yes | Yes | Yes | Yes |
| System | [`get/set system.watchdog`](cli_commands.md#enable-or-disable-the-nrf52-system-watchdog) | nRF52 only | No | No | No | No | No |
| System | [`get public.key`](cli_commands.md#view-this-nodes-public-key) | Full parser | Yes | Yes | Yes | Yes | Yes |
| System | [`get role`](cli_commands.md#view-this-nodes-configured-role) | All text CLI roles | Yes | Yes | Yes | Yes | Yes |
| System | [`powersaving`; `powersaving on/off`](cli_commands.md#view-or-change-this-nodes-power-saving-flag) | Supported repeater board | Feature | Feature | Feature | Feature | Feature |
| System | [`get/set reboot.interval`](#reboot-interval) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Clock sync | [`get/set clock.sync.*`; `clock.sync.mesh now`](cli_commands.md#estimate-and-correct-infrastructure-node-time-after-startup) | Repeater, sensor, and room server; `clock.sync.internet` needs MQTT repeater | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set repeat`](cli_commands.md#view-or-change-this-nodes-repeat-flag) | Forwarding-capable role | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set path.hash.mode`](cli_commands.md#view-or-change-this-nodes-advert-path-hash-size) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set loop.detect`](cli_commands.md#view-or-change-this-nodes-loop-detection) | Repeater, full common parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set txdelay`](cli_commands.md#view-or-change-the-retransmit-delay-factor-for-flood-traffic) | All text CLI roles, including bridges | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set direct.txdelay`](cli_commands.md#view-or-change-the-retransmit-delay-factor-for-direct-traffic) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set rxdelay`](cli_commands.md#experimental-view-or-change-the-processing-delay-for-received-traffic) | All text CLI roles, including bridges | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set dutycycle`](cli_commands.md#view-or-change-the-duty-cycle-limit) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set af`](cli_commands.md#view-or-change-the-airtime-factor-duty-cycle-limit) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set int.thresh`](cli_commands.md#view-or-change-the-local-interference-threshold) | All text CLI roles | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set cad`](cli_commands.md#enable-or-disable-hardware-channel-activity-detection-cad) | Radio CAD support | Feature | Feature | Feature | Feature | Feature |
| Routing | [`get/set agc.reset.interval`](cli_commands.md#view-or-change-the-agc-reset-interval) | All text CLI roles | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set radio.watchdog`](cli_commands.md#view-or-change-the-radio-watchdog-interval-mqtt-observer-only) | MQTT observer | No | No | No | Yes | No |
| Routing | [`get/set multi.acks`](cli_commands.md#enable-or-disable-multi-acks-support) | Full parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set flood.advert.interval`](cli_commands.md#view-or-change-the-flood-advert-interval) | Repeater, full common parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set advert.interval`](cli_commands.md#view-or-change-the-zero-hop-advert-interval) | Full common parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set flood.max`](cli_commands.md#limit-the-number-of-hops-for-a-flood-message) | Repeater, full common parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set flood.max.unscoped`](cli_commands.md#limit-the-number-of-hops-for-an-unscoped-flood-message) | Repeater, full common parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set flood.max.advert`](cli_commands.md#limit-the-number-of-hops-for-an-advert-flood-message) | Repeater, full common parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set flood.channel.data`; `get/set flood.channel.data.hops`](cli_commands.md#forward-flood-group-data-packets-on-repeaters) | Repeater, full common parser | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set/del flood.channel.scope*`](cli_commands.md#force-a-transport-scope-onto-floods) | Repeater role handler | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set/del flood.channel.scope.require*`](cli_commands.md#require-valid-incoming-scopes-only-on-selected-channels) | Repeater role handler | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set/del flood.rule*`; `get/set/del flood.filter*`; `get/set/del flood.filter.blacklist*`](cli_commands.md#change-persistent-flood-rules-in-the-field) | Repeater role handler; `flood.rule`/`flood.filter` also on FULL ESP32 room server (no blacklist) | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set/del flood.moderation*`](cli_commands.md#moderate-flood-group-text-by-channel-sender-and-source-path) | Repeater role handler | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set outpath`](halo_keymind_settings.md#direct-path-overrides) | Repeater remote-client context | Yes | Yes | Yes | Yes | Yes |
| Routing | [`get/set altpath`](halo_keymind_settings.md#direct-path-overrides) | Repeater remote-client context | Yes | Yes | Yes | Yes | Yes |
| ACL | [`setperm <pubkey> <permissions>`](cli_commands.md#add-update-or-remove-permissions-for-a-companion) | Repeater, room server, or sensor | Yes | Yes | Yes | Yes | Yes |
| ACL | [`get acl`](cli_commands.md#view-the-current-acl) | Local serial | Serial | Serial | Serial | Serial | Serial |
| ACL | [`get/set allow.read.only`](cli_commands.md#view-or-change-this-room-servers-read-only-flag) | Room server | Yes | Yes | No | Yes | Yes |
| Regions | [`region load`](cli_commands.md#bulk-load-region-lists); [`region save`](cli_commands.md#save-any-changes-to-regions-made-since-reboot) | Role with region storage | Yes | Yes | Yes | Yes | Yes |
| Regions | [`region allowf`](cli_commands.md#allow-a-region); [`region denyf`](cli_commands.md#block-a-region) | Role with region storage | Yes | Yes | Yes | Yes | Yes |
| Regions | [`region get`](cli_commands.md#show-information-for-a-region); [`region list`](cli_commands.md#view-all-regions); [`region`](cli_commands.md#dump-all-defined-regions-and-flood-permissions) | Role with region storage | Yes | Yes | Yes | Yes | Yes |
| Regions | [`region home`](cli_commands.md#view-or-change-the-home-region-for-this-node); [`region default`](cli_commands.md#view-or-change-the-default-scope-region-for-this-node) | Role with region storage | Yes | Yes | Yes | Yes | Yes |
| Regions | [`region put`](cli_commands.md#create-a-new-region); [`region def`](cli_commands.md#define-region-hierarchy-single-line); [`region remove`](cli_commands.md#remove-a-region) | Role with region storage | Yes | Yes | Yes | Yes | Yes |
| Retry | [`get/set direct.retry`](cli_commands.md#view-or-change-direct-retry-state); [`get/set direct.retry.heard`](cli_commands.md#view-or-change-direct-retry-heard-table-gate) | Role with basic retry support | Yes | Yes | Yes | Yes | Yes |
| Retry | [`get/set retry.preset`](cli_commands.md#view-or-apply-a-retry-preset) | Role with retry support | Yes | Yes | Yes | Yes | Yes |
| Retry | [`get/set flood.retry.count`](cli_commands.md#view-or-change-flood-retry-count); [`get/set flood.retry.path`](cli_commands.md#view-or-change-flood-retry-path-gate); [`get/set flood.retry.group.path`](cli_commands.md#view-or-change-the-group-data-flood-retry-path-gate) | Repeater | Yes | Yes | Yes | Yes | Yes |
| Retry | [`get/set flood.retry.advert`](cli_commands.md#view-or-change-flood-retry-advert-handling); [`get/set flood.retry.prefixes`](cli_commands.md#view-or-change-flood-retry-target-prefixes); [`get/set flood.retry.ignore`](cli_commands.md#view-or-change-flood-retry-ignored-prefixes) | Repeater | Yes | Yes | Yes | Yes | Yes |
| Retry | [`get/set flood.retry.bridge`](cli_commands.md#view-or-change-flood-retry-bridge-mode); [`get/set flood.retry.bucket`](cli_commands.md#view-or-change-flood-retry-bridge-buckets) | Repeater bridge retry support | Feature | Feature | Feature | Feature | Feature |
| Retry | [`get/set direct.retry.count`](cli_commands.md#view-or-change-direct-retry-count); [`get/set direct.retry.base`](cli_commands.md#view-or-change-direct-retry-base-delay); [`get/set direct.retry.step`](cli_commands.md#view-or-change-direct-retry-step-delay) | Role with retry support | Yes | Yes | Yes | Yes | Yes |
| Retry | [`get/set direct.retry.margin`](cli_commands.md#view-or-change-direct-retry-snr-margin); [`get/set direct.retry.cr`](cli_commands.md#view-or-change-adaptive-direct-retry-coding-rate) | Role with retry support | Yes | Yes | Yes | Yes | Yes |
| Retry | [`get/set/clear recent.repeater`; `get recent.repeaters`](cli_commands.md#view-seed-or-clear-the-recent-repeater-table) | Repeater | Yes | Yes | Yes | Yes | Yes |
| GPS | [`gps`; `gps on/off`](cli_commands.md#view-or-change-gps-state) | Compiled onboard GPS | Feature | Feature | Feature | Feature | Feature |
| GPS | [`gps sync`](cli_commands.md#sync-this-nodes-clock-with-gps-time) | Compiled onboard GPS | Feature | Feature | Feature | Feature | Feature |
| GPS | [`gps setloc`](cli_commands.md#set-this-nodes-location-based-on-the-gps-coordinates) | Compiled onboard GPS | Feature | Feature | Feature | Feature | Feature |
| GPS | [`gps advert [none/share/prefs]`](cli_commands.md#view-or-change-the-gps-advert-policy) | Compiled onboard GPS | Feature | Feature | Feature | Feature | Feature |
| Sensors | [`get/set telemetry.access`](cli_commands.md#view-or-change-telemetry-access-mode) | Sensor-capable full parser | Feature | Feature | Limited | Feature | Feature |
| Sensors | [`sensor list [start]`](cli_commands.md#view-the-list-of-sensors-on-this-node) | Compiled sensor manager; `no_external_sensors` omits optional drivers | Feature | Feature | Limited | Feature | Feature |
| Sensors | [`sensor get`; `sensor set`](cli_commands.md#view-or-change-the-value-of-a-sensor) | Compiled sensor setting; `no_external_sensors` omits optional drivers | Feature | Feature | Limited | Feature | Feature |
| Sensors | [`io [r/s/t]<hex>`](#sensor-io) | Simple sensor role with board GPIO support | Feature | Feature | No | No | Feature |
| Bridge | [`get bridge.type`](cli_commands.md#view-the-compiled-bridge-type) | Compiled bridge | Feature | Feature | Feature | Yes | Feature |
| Bridge | [`get/set bridge.enabled`](cli_commands.md#view-or-change-the-bridge-enabled-flag) | Compiled bridge | Feature | Feature | Feature | Yes | Feature |
| Bridge | [`get/set bridge.delay`](cli_commands.md#add-a-delay-to-packets-routed-through-this-bridge) | Compiled bridge | Feature | Feature | Feature | Yes | Feature |
| Bridge | [`get/set bridge.source`](cli_commands.md#view-or-change-the-source-of-packets-bridged-to-the-external-interface) | Compiled bridge | Feature | Feature | Feature | Yes | Feature |
| Bridge | [`get/set bridge.baud`](cli_commands.md#view-or-change-the-speed-of-the-bridge-rs-232-only) | RS-232 bridge | Feature | Feature | Feature | No | Feature |
| Bridge | [`get/set bridge.channel`](cli_commands.md#view-or-change-the-channel-used-for-bridging-espnow-only) | ESP-NOW bridge | No | No | No | Feature | Feature |
| Bridge | [`get/set bridge.secret`](cli_commands.md#set-the-esp-now-secret) | ESP-NOW bridge | No | No | No | Feature | Feature |
| Board | [`get bootloader.ver`](cli_commands.md#view-the-bootloader-version-nrf52-only) | nRF52 only | No | No | No | No | No |
| Board | [`get pwrmgt.support`; `get pwrmgt.source`; `get pwrmgt.bootreason`; `get pwrmgt.bootmv`](nrf52_power_management.md#cli-commands) | nRF52 only | No | No | No | No | No |
| Ethernet | [`eth.status`](cli_commands.md#view-ethernet-connection-status) | Ethernet target | Feature | Feature | No | No | Feature |
| Browser OTA | [`start ota [ap]`; `stop ota`](cli_commands.md#start-or-stop-an-over-the-air-ota-firmware-update) | Compiled browser uploader | No | No | Yes | Feature | Feature |
| WebConfig | [`start webconfig [ap]`; `stop webconfig`; `get/set webui`](cli_commands.md#browser-configuration-portal-esp32-repeater-and-room-server) | Compiled WebConfig | No | No | No | Feature | Feature |
| WiFi | [`get/set wifi.ssid`; `set wifi.pwd`; `get wifi.status`; `get/set wifi.powersave`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#wifi-commands) | MQTT WiFi, or standalone FULL WebConfig; standalone has no `get wifi.pwd` | No | No | No | Yes | Feature |
| WiFi | [`get/set wifi.cli`](cli_commands.md#browser-configuration-portal-esp32-repeater-and-room-server) | Compiled WebConfig | No | No | No | Feature | Feature |
| LoRa OTA | [`ota help`; `ota ?`; `ota h`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota`; `ota status`; `ota st`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota stats`](ota_user_guide.md#1-see-what-im-running-and-whether-anything-is-going-on) | LoRa OTA build | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota ls`; `ota neighbors`; `ota nbrs`; `ota updates`; `ota n`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota get`; `ota pull`; `ota download`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota install`; `ota apply`; `ota applydelta`](ota_protocol.md#11-cli-surface-otaclicpp) | Compatible completed update | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota rescue install <base_hash16>`](ota_protocol.md#12-apply-bootloader-contract) | Internal-flash nRF52 LoRa OTA build with failed app-side EndF validation | No | No | Feature | No | No |
| LoRa OTA | [`ota cancel`; `ota drop`; `ota stop`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota announce`; `ota adv`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota self`; `ota id`](ota_protocol.md#11-cli-surface-otaclicpp) | Firmware with EndF trailer | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota folder`; `ota fold`](ota_protocol.md#11-cli-surface-otaclicpp) | `on` needs compiled serial or TCP folder transport | No | No | Feature | Feature | Feature |
| LoRa OTA | [`ota config`; `ota cfg`; `ota set`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota key`; `ota keys`](ota_protocol.md#11-cli-surface-otaclicpp) | LoRa OTA build | No | No | Yes | Yes | Yes |
| LoRa OTA | [`ota dev ...`](ota_protocol.md#11-cli-surface-otaclicpp) | Developer diagnostics | No | No | Yes | Yes | Yes |
| MQTT | [`get/set mqttN.preset`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-slot-commands) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get/set mqttN.server`; `get/set mqttN.port`; `get/set mqttN.username`; `get/set mqttN.password`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-slot-commands) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get/set mqttN.token`; `get/set mqttN.topic`; `get/set mqttN.audience`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-slot-commands) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get mqttN.diag`](#mqtt-slot-diagnostics) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get/set mqtt.origin`; `get/set mqtt.iata`; `get mqtt.presets`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-shared-commands) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get mqtt.stats`](#mqtt-stats) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get/set mqtt.status`; `get/set mqtt.packets`; `get/set mqtt.raw`; `get/set mqtt.interval`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-shared-commands) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get/set mqtt.rx`](cli_commands.md#view-or-change-mqtt-rx-packet-uplinking); [`get/set mqtt.tx`](cli_commands.md#view-or-change-mqtt-tx-packet-uplinking) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get/set mqtt.neighbors`](cli_commands.md#view-or-change-periodic-neighbors-publishing-mqtt-observer-neighbors-feature); [`get/set mqtt.neighbors.interval`](cli_commands.md#view-or-change-the-neighbors-publish-interval-mqtt-observer-neighbors-feature) | MQTT observer with compiled neighbor support | No | No | No | Feature | No |
| MQTT | [`get/set mqtt.ntp`](cli_commands.md#view-or-change-the-ntp-server-mqtt-observer-only) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get mqtt.ntp.diag`](cli_commands.md#diagnose-ntp-server-connectivity-mqtt-observer-only) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get/set timezone`; `get/set timezone.offset`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#timezone-commands) | MQTT observer | No | No | No | Yes | No |
| MQTT | [`get/set mqtt.analyzer.us`; `get/set mqtt.analyzer.eu`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#migration-from-old-configuration) | Legacy MQTT aliases | No | No | No | Yes | No |
| MQTT | [`get/set mqtt.owner`; `get/set mqtt.email`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_IMPLEMENTATION.md#mqtt-shared-commands) | MQTT observer; `get` is local serial only | No | No | No | Yes | No |
| MQTT | [`get mqtt.config.valid`](#mqtt-config-valid) | MQTT observer | No | No | No | Yes | No |
| SNMP | [`get/set snmp`; `get/set snmp.community`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/MQTT_SNMP.md#cli-commands) | MQTT target compiled with SNMP | No | No | No | Feature | No |
| Alerts | [`get/set alert`; `get/set alert.psk`; `get/set alert.hashtag`; `get/set alert.region`; `get/set alert.wifi`; `get/set alert.mqtt`; `get/set alert.interval`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/ALERTS.md#cli) | MQTT observer | No | No | No | Yes | No |
| Alerts | [`alert test [message]`](https://github.com/mikecarper/MeshCore/blob/keymindCascade/ALERTS.md#cli) | MQTT observer with configured alert channel | No | No | No | Yes | No |
| TLS | [`tls.bundletest <host>`](#tls-bundle-test) | MQTT target with embedded certificate bundle | No | No | No | Feature | No |
| Manifest OTA | [`ota check`](#manifest-ota) | MQTT target with `OTA_MANIFEST_BASE` | No | No | No | Manifest | No |
| Manifest OTA | [`ota update`](#manifest-ota) | MQTT target with `OTA_MANIFEST_BASE` | No | No | No | Manifest | No |

## Supplemental command notes

These short entries cover implemented commands that do not yet have their own
section in the primary command reference.

### `memory`

ESP32 only. Prints free heap, minimum heap, largest allocation, queue depth,
internal heap, and PSRAM totals.

### `sensor` hardware summary

Prints the configured I2C pins and GPS serial pins. This is a wiring/build
diagnostic, distinct from `sensor list`, `sensor get`, and `sensor set`.

### `powerlog`

Prints the last reset reason. nRF52 also prints the captured shutdown reason and
boot voltage.

### `stats-radio-diag`

Local-serial diagnostic that prints the extended radio diagnostic counters.

<a id="radio-rxps"></a>
### `radio.rxps` and `rxps.wd`

`get radio.rxps` reports RX power-saving state and timing.
`set radio.rxps ...` changes that configuration. `get rxps.wd` reports the soft
and hard RX power-saving watchdog counters.

<a id="reboot-interval"></a>
### `reboot.interval`

`get reboot.interval` shows the scheduled reboot interval. Use
`set reboot.interval <hours>` for 1-255 hours, or
`set reboot.interval 0` to disable it.

<a id="sensor-io"></a>
### Sensor `io`

The simple-sensor role exposes its board GPIO word as hexadecimal. `io` reads
it; `io <hex>` replaces it; and `io r<hex>`, `io s<hex>`, or `io t<hex>`
clear, set, or toggle selected bits.

<a id="mqtt-slot-diagnostics"></a>
### `get mqttN.diag`

Prints the runtime diagnostic summary for MQTT slot N, including its connection
state and failure history.

<a id="mqtt-stats"></a>
### `get mqtt.stats`

Prints the MQTT bridge runtime statistics summary.

<a id="mqtt-config-valid"></a>
### `get mqtt.config.valid`

Reports whether the saved MQTT configuration has the minimum values needed to
run.

<a id="tls-bundle-test"></a>
### `tls.bundletest`

`tls.bundletest <host[:port]|url>` tests the embedded TLS certificate bundle
against a remote host without changing the MQTT configuration.

<a id="manifest-ota"></a>
### Manifest `ota check` and `ota update`

On MQTT targets with `OTA_MANIFEST_BASE`, `ota check` fetches and checks the
target manifest. `ota update` downloads and applies the compatible update
selected by that manifest.
