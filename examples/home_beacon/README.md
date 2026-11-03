# Experimental ESP32 home beacon

This standalone firmware is a starting point for an ESP32 at home. It is not a
MeshCore repeater and does not yet connect to the tracker: the tracker-side BLE
scanner and provisioning are still needed. Build the `esp32_home_beacon` environment
and connect its USB serial port at 115200 baud. Hardware qualification is still
required. It requires at least 4 MB of flash; its large single-app partition
does not support firmware updates over the air, so update it over USB.

On first boot, the serial setup asks for an SSID, a WPA2-or-better Wi-Fi password,
an 8-hex-character beacon ID, and a randomly generated shared key of 32 or 64
hex characters. The firmware does not echo entries or print credentials. Turn
off **local echo** in the serial terminal as well. Generate a different random
key and ID for each gateway, and store them securely for later tracker setup.
For example, `python -c "import secrets; print(secrets.token_hex(4)); print(secrets.token_hex(32))"`
prints a new ID and 256-bit key; protect that terminal output. Type `provision`
over USB to replace the settings; this restarts into setup. `status` shows
whether Wi-Fi is connected and reports a detected clock rollback.

Credentials and keys are stored in the ESP32's NVS. NVS is not automatically
encrypted: use device flash encryption and physical protections appropriate to
the installation. Wi-Fi connection and time synchronization are required
before advertising; a lost connection or expired time sync stops advertising.
The authenticated broadcast uses the experimental manufacturer ID `0xffff`,
which is suitable for local development, and a nominal 250 ms BLE interval.
BLE adds random advertising delay and broadcasts on three channels, so a
short receiver scan can still miss packets.

The packet includes an authenticated 30-second time slot, with a fixed expiry
no more than two slots after its start. If the clock moves backward, including
from an ordinary time correction, the gateway stops advertising for the rest
of that run. USB `status` reports this condition. Check the time source, then
restart the gateway to try again. The high-water mark is currently RAM-only;
restarting clears it and does not establish authenticated time. **Standard
SNTP is unauthenticated:** an attacker able to alter it can corrupt freshness;
Wi-Fi security alone does not authenticate an Internet time server. Live RF
relays and stolen keys are not prevented. The tracker must reject an unset or
untrusted clock, and must never treat a missing beacon alone as evidence that
the dog is lost. Manual lost always has priority.

The verifier can accept multiple independently provisioned beacons, so one
fresh authenticated signal can represent home across a larger yard. Receiver
integration still needs a trusted tracker clock, secure key provisioning and
storage, and coordination of BLE scanning with Find My advertising and DFU.
The current freshness window expires within 60 seconds, so maintaining home
presence requires regular scans; their effect on battery life has not been
measured. No scan is guaranteed to hear a particular beacon.
