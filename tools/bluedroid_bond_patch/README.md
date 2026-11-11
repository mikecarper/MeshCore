# Bluedroid bond preservation

The matched ESP32-S3 Arduino 2.0.17 builds retain their existing Bluedroid bond
database. Failed authentication clears staged pairing data and requests a host
restart instead of deleting stored keys. A new successful pairing still uses
the SDK's existing storage path. Application code retires each disconnected
host session before accepting the next, draining delayed callbacks.

`generate.py` verifies the pinned input source hashes and writes modified
sources into a separate directory. The three unmodified upstream files and
their license are included here. The complete matching ESP-IDF headers are
needed by `../bluedroid_bond_build/build_s3_candidate.py` to reproduce the objects.
Only the three rebuilt objects are packaged; normal builds reconstruct and
verify a private copy of the installed library without modifying the SDK.

`scripts/bluedroid_bond_link.py` enables the fix for BLE builds using the matched
ESP32-S3 SDK. NimBLE and other MCUs retain their existing implementations.
Different SDK archives are never mixed with these objects. An explicitly
requested unsupported combination fails the build.

Use an app-only update with a compatible partition layout to retain NVS.
This does not migrate between Bluedroid and NimBLE, change the normal successful
pairing eviction policy, or make successful replacement writes power-loss
atomic. Host tests exercise the generated storage handler and adapter lifecycle;
hardware checks cover upgrade continuity and encrypted reconnects.
