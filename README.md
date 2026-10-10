## Halo Keymind Cascade Branch Changes over Stock

* **[Two LoRa profiles on one device](https://mikecarper.github.io/MeshCore/radio_profiles/).** Time-share one transceiver between two frequencies and/or modulation settings. Choose transmission profiles per contact or channel, enable or isolate crossover traffic, schedule profiles, and use a temporary update profile alongside the normal network. Includes timing calibration and separate R2 screen pages.
* **[Firmware updates over LoRa](https://mikecarper.github.io/MeshCore/lora_ota_automation/).** Update repeaters, room servers, and sensors over LoRa on supported firmware and storage layouts.
* **[Repeater forwarding policies](https://mikecarper.github.io/MeshCore/flood_filtering/).** Actions include dropping, rate limiting, assigning scopes, selecting retries, and controlling forwarding priority. Separate policies cover ordinary radio forwarding, bridges, and profile crossover.
* **[Private fleet management](https://mikecarper.github.io/MeshCore/fleet_management/).** Deploy filtering, secondary-radio changes, radio schedules, and clock updates to a whole fleet, existing configured or home regions, a GPS center and radius, one node, or a semicolon-separated target list. The publisher signs each complete command once, splitting longer requests across two packets; receivers hold its public verification key, with replay protection and acknowledgements before radio handoff.
* **[Flood retries that account for network topology](https://mikecarper.github.io/MeshCore/halo_keymind_settings/#flood-and-advert-settings).** Configure retry counts, path limits, packet-type limits, target prefixes, ignored repeaters, and channel-specific eligibility. Bridge buckets keep retrying until traffic is heard on the intended sides of a relay or the retry budget expires. Qualifying echoes cancel unnecessary attempts.
* **[Enhanced direct messages and direct-route retries](https://mikecarper.github.io/MeshCore/halo_keymind_settings/#direct-retry-settings).** Adapt the coding rate across bounded retry attempts and cancel unnecessary retries when a qualifying echo is heard. [Single Key DMs](https://mikecarper.github.io/MeshCore/companion_protocol/#private-text-when-only-the-sender-knows-the-recipient) let compatible Companions send private text when only the sender knows the recipient's key, using a signed, encrypted introduction. The recipient accepts the sender through the app or opts into automatic acceptance; compact STM32WL builds omit this feature.
* **[Enhanced telemetry history](https://mikecarper.github.io/MeshCore/telemetry_decoder/).** Send compact temperature and battery-voltage snapshots of up to 165 samples per packet, plus external-voltage history in chunks. Read retained GPS history through the CLI; GPS history is not included in the automatic telemetry packets. Decode snapshots and CLI history in the browser.
* **[Room topics and access controls](https://mikecarper.github.io/MeshCore/cli_commands/#view-or-change-the-room-topic).** Save a room announcement across reboots and deliver it through the existing app message format while preserving unread chat catch-up. Public read-only login and explicit ACL roles are respected; guest-password login cannot demote an admin.
* **[Full firmware profiles, Companion transports, and ESP32 bridges](https://mikecarper.github.io/MeshCore/full_profiles/).** Full combines the exact board and role's supported features with runtime controls: Companion transports and firmware serving, or qualified ESP32 infrastructure services and MQTT/ESP-NOW/RS-232 bridges. Includes capacity exceptions, matching application slots, exact-board/role partition migration packages, and nRF52 Full/Reduced supported-sensor packages with LoRa OTA. The picker links supported migration routes; USB layout changes use the matching merged image, since an app-only image cannot change the partition table.
* **[Operational reporting and alerts](https://mikecarper.github.io/MeshCore/management_reports/).** Adds optional management reports with firmware version, bootloader version, LoRa update capabilities, uptime, and historical voltage and temperature ranges.
* **[Set time using the LoRa network](https://mikecarper.github.io/MeshCore/cli_commands/#estimate-and-correct-infrastructure-node-time-after-startup).** Infrastructure nodes can estimate time from fresh mesh evidence and correct their clocks when a consensus is available. Successful manual, GPS, or NTP synchronization takes precedence until reboot.
* **[Companion Bluetooth privacy and recovery improvements](https://mikecarper.github.io/MeshCore/companion_radio_full/#interfaces).** Configurable MAC address policies, paired-peer stealth behavior, and changes to reconnect, subscription, and startup handling on supported Bluetooth targets.
* **[Programmable Companion radio notifications](https://mikecarper.github.io/MeshCore/notifications/).** Build per-contact, room, or channel rules for the radio's vibration, LED, melody, screen, and supported GPIO outputs. Select rules by client connection state and configure repeat/stop behavior. Available on ESP32, nRF52, and RP2040 Companions with the required outputs.
* **[agessaman Observer - integrated ESP32 MQTT observation](https://mikecarper.github.io/MeshCore/WiFi/#mqtt-observer-setup).** Publish packets from the node over WiFi to a configured MQTT broker, without a separate USB host bridge.
* **[IoTThinks PowerSaving - broader power-saving controls](https://mikecarper.github.io/MeshCore/role_feature_switches/#infrastructure-power-saving-and-bridges).** Integrates device sleep, LoRa receive duty cycling, WiFi modem-sleep policies, and GPS acquisition/cache schedules across supported roles. These controls are independently configurable; actual savings depend on hardware and workload.
* **[LiFePO4 low-voltage cutoff and protection](https://mikecarper.github.io/MeshCore/cli_commands/#configure-nrf52-battery-protection).** On supported nRF52 power-management boards with a text CLI, the saved LiFePO4 profile sets a 2.9 V boot lock and 2.7 V running cutoff. Three valid low readings 30 seconds apart trigger protective shutdown; external power bypasses these checks. Use compatible charging hardware: selecting a battery profile does not change the charger.

Get the correct firmware file here for your hardware:
**[Firmware picker](https://mikecarper.github.io/MeshCore/firmware_picker/)**

Related source projects:

* [agessaman Observer firmware](https://github.com/agessaman/MeshCore/tree/observer-firmware-dev): embedded WiFi/MQTT observation.
* [Cisien meshcoretomqtt](https://github.com/Cisien/meshcoretomqtt): a host-side Python USB/serial packet-log publisher.
* [IoTThinks PowerSaving branch](https://github.com/IoTThinks/MeshCore/tree/PowerSaving-v17): power-saving integration.

---

## About MeshCore

MeshCore is a lightweight, portable C++ library that enables multi-hop packet routing for embedded projects using LoRa and other packet radios. It is designed for developers who want to create resilient, decentralized communication networks that work without the internet.

## What is MeshCore?

MeshCore now supports a range of LoRa devices, allowing for easy flashing without the need to compile firmware manually. Users can flash a pre-built binary using tools like Adafruit ESPTool and interact with the network through a serial console.
MeshCore provides the ability to create wireless mesh networks, similar to Meshtastic and Reticulum but with a focus on lightweight multi-hop packet routing for embedded projects. Unlike Meshtastic, which is tailored for casual LoRa communication, or Reticulum, which offers advanced networking, MeshCore balances simplicity with scalability, making it ideal for custom embedded solutions, where devices (nodes) can communicate over long distances by relaying messages through intermediate nodes. This is especially useful in off-grid, emergency, or tactical situations where traditional communication infrastructure is unavailable.

> **Upstream Observer WiFi and MQTT** - Observer firmware, release notes, and browser-based
> flashing are available at [observer.gessaman.com](https://observer.gessaman.com/).
> See [WiFi and MQTT by Firmware Type](./docs/WiFi.md) for the
> role/build matrix and setup overview. For the complete MQTT command and
> broker reference, see the [MQTT Implementation Guide](./MQTT_IMPLEMENTATION.md).

## Key Features

* Multi-Hop Packet Routing
  * Devices can forward messages across multiple nodes, extending range beyond a single radio's reach.
  * Supports up to a configurable number of hops to balance network efficiency and prevent excessive traffic.
  * Companion nodes do not repeat by default. Supported Companion builds can opt into bounded client repeating on permitted frequencies, while dedicated repeaters remain the normal way to extend coverage.
* Supports LoRa Radios - Works with Heltec, RAK Wireless, and other LoRa-based hardware.
* Decentralized & Resilient - No central server or internet required; the network is self-healing.
* Low Power Consumption - Ideal for battery-powered or solar-powered devices.
* Simple to Deploy - Pre-built example applications make it easy to get started.

## What Can You Use MeshCore For?

* Off-Grid Communication: Stay connected even in remote areas.
* Emergency Response & Disaster Recovery: Set up instant networks where infrastructure is down.
* Outdoor Activities: Hiking, camping, and adventure racing communication.
* Tactical & Security Applications: Military, law enforcement, and private security use cases.
* IoT & Sensor Networks: Collect data from remote sensors and relay it back to a central location.

## How to Get Started

- Watch the [MeshCore QuickStart Playlist](https://www.youtube.com/watch?v=iaFltojJrAc&list=PLshzThxhw4O4WU_iZo3NmNZOv6KMrUuF9) by The Comms Channel
- Watch the [MeshCore Technical Presentation](https://www.youtube.com/watch?v=OwmkVkZQTf4) by Liam Cottle.
- Read through our [Frequently Asked Questions](./docs/faq.md) and [Documentation](https://docs.meshcore.io).
- Flash the MeshCore firmware on a supported device.
- Connect with a supported client.

For developers:

- Install [PlatformIO](https://docs.platformio.org) in [Visual Studio Code](https://code.visualstudio.com).
- Clone and open the MeshCore repository in Visual Studio Code.
- Install the pinned build-time compressors with `python -m pip install -r requirements-build.txt`.
- See the example applications you can modify and run:
  - [Companion Radio](./examples/companion_radio) - For use with an external chat app, over BLE, USB or Wi-Fi.
  - [KISS Modem](./examples/kiss_modem) - Serial KISS protocol bridge for host applications. ([protocol docs](./docs/kiss_modem_protocol.md))
  - [Simple Repeater](./examples/simple_repeater) - Extends network coverage by relaying messages.
  - [Simple Room Server](./examples/simple_room_server) - A simple BBS server for shared Posts.
  - [Simple Secure Chat](./examples/simple_secure_chat) - Secure terminal based text communication between devices.
  - [Simple Sensor](./examples/simple_sensor) - Remote sensor node with telemetry and alerting.

The Simple Secure Chat example can be interacted with through the Serial Monitor in Visual Studio Code, or with a Serial USB Terminal on Android.

## MeshCore Flasher

We have prebuilt firmware ready to flash on supported devices.

- Launch https://meshcore.io/flasher
- Select a supported device
- Flash one of the firmware types:
  - Companion, Repeater or Room Server
- Once flashing is complete, you can connect with one of the MeshCore clients below.

## MeshCore Clients

**Companion Firmware**

The companion firmware can be connected to via BLE, USB or Wi-Fi depending on the firmware type you flashed.

- Web: https://app.meshcore.nz
- Android: https://play.google.com/store/apps/details?id=com.liamcottle.meshcore.android
- iOS: https://apps.apple.com/us/app/meshcore/id6742354151?platform=iphone
- NodeJS: https://github.com/meshcore-dev/meshcore.js
- Python: https://github.com/meshcore-dev/meshcore-cli

**Repeater and Room Server Firmware**

The repeater and room server firmware can be set up via USB in the web config tool.

- https://config.meshcore.io

They can also be managed via LoRa in the mobile app by using the Remote Management feature.

## Hardware Compatibility

MeshCore is designed for devices listed in the [MeshCore Flasher](https://meshcore.io/flasher)

## License

MeshCore is open-source software released under the MIT License. You are free to use, modify, and distribute it for personal and commercial projects.

## Contributing

Please submit PR's using 'dev' as the base branch!
For minor changes just submit your PR and we'll try to review it, but for anything more 'impactful' please open an Issue first and start a discussion. It is better to sound out what it is you want to achieve first, and try to come to a consensus on what the best approach is, especially when it impacts the structure or architecture of this codebase.

Here are some general principles you should try to adhere to:
* Keep it simple. Please, don't think like a high-level lang programmer. Think embedded, and keep code concise, without any unnecessary layers.
* No dynamic memory allocation, except during setup/begin functions.
* Follow the repository's `.clang-format` and the surrounding source style. Do not retroactively reformat unrelated code; that creates noisy diffs and makes functional changes harder to review.

Help us prioritize! Please react with thumbs-up to issues/PRs you care about most. We look at reaction counts when planning work.

### Running unit tests

To run unit tests, run the following command:

```bash
pio test --environment native --verbose
```

Run only one PlatformIO process in a checkout at a time. Do not overlap
`pio test`, `pio run`, uploads, cleans, or scripts that invoke PlatformIO, even
for different environments: they share and may clean `.pio/build`, which can
interrupt another build and produce misleading failures.

## Road-Map / To-Do

There are a number of fairly major features in the pipeline, with no particular time-frames attached yet. In very rough chronological order:
- [X] Companion radio: UI redesign
- [X] Repeater + Room Server: add ACL's (like Sensor Node has)
- [X] Standardise Bridge mode for repeaters
- [ ] Repeater/Bridge: Standardise the Transport Codes for zoning/filtering
- [X] Core + Repeater: enhanced zero-hop neighbour discovery
- [X] Core + Full Companion: round-trip trace and manual path support
- [X] Companion: opt-in off-grid client repeat mode
- [ ] Companion + Apps: support for multiple sub-meshes
- [ ] Core + Apps: support for LZW message compression
- [X] Core: adaptive CR (Coding Rate) for direct retries using recently heard SNR
- [ ] Core: new framework for hosting multiple virtual nodes on one physical device
- [ ] V2 protocol spec: discussion and consensus around V2 packet protocol, including path hashes, new encryption specs, etc

## Get Support

- Report bugs and request features on the [GitHub Issues](https://github.com/meshcore-dev/MeshCore/issues) page.
- Find additional guides and components on [my site](https://buymeacoffee.com/ripplebiz).
- Join [MeshCore Discord](https://meshcore.gg) to chat with the developers and get help from the community.
