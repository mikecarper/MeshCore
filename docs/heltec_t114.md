<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/heltec_t114/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Heltec T114 screen detection

Choose **Heltec T114** and your firmware role in the [firmware picker](firmware_picker.md).
New T114 images with automatic screen detection work on boards with or without
the optional stock 1.14-inch screen. There is no separate screen selection.
For Companion, use the Full image for USB and Bluetooth support together.
The KISS modem role remains headless.

At startup, the firmware checks whether the screen responds before starting
the display. The backlight stays off during the check. If no screen responds,
the display stays powered off and the node continues without a screen.
The selected role's supported connections and radio functions continue to work
normally.

A detected screen uses the existing display settings and button wake behavior.
Detection does not reset saved screen timeouts, power-saving preferences,
identity, or radio settings. To add or remove the screen, power the board off
first, then restart it after changing the hardware.

## Updating an older T114

Use USB when moving from an older `Heltec_t114_without_display` firmware target
to the canonical `Heltec_t114` target. The old and new target names have
different LoRa update identities; a package built for one is not an update for
the other. Follow the normal update procedure and preserve your settings;
screen detection does not require a factory reset or a new bootloader.

Previously published firmware remains unchanged. The picker identifies
automatic detection from the selected release's verified firmware metadata;
an old display-enabled image does not gain detection from its filename.

See the [implementation and validation record](test-results/heltec_t114_display_detection.md)
for the startup checks and hardware-testing boundary.
