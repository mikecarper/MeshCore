# Repository Agent Instructions

## PlatformIO commands are single-process only

Run only one PlatformIO process in this checkout at a time. This includes every
`pio run`, `pio test`, upload, clean, and scripted command that invokes
PlatformIO, even when the commands target different environments.

PlatformIO reuses and may clean the shared `.pio/build` tree. Concurrent
PlatformIO processes can delete another process's object directories and cause
misleading compiler or linker failures. Wait for the active PlatformIO command
to finish before starting the next one.
