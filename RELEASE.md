# Releasing Firmware

For the USA Cascade 1.17.1.5 matrix, use
[the option 3 release instructions](docs/releases/1.17.1.5.md).
The [Full Companion feature guide](docs/full_companion_features.md) is intended
to accompany the release assets.

GitHub Actions is set up to automatically build and release firmware.

It will automatically build firmware when one of the following tag formats are pushed.

- `companion-v1.0.0`
- `repeater-v1.0.0`
- `room-server-v1.0.0`

> NOTE: replace `v1.0.0` with the version you want to release as.

- You can push one, or more tags on the same commit, and they will all build separately.
- Once the firmware has been built, a new (draft) GitHub Release will be created.
- You will need to update the release notes, and publish it.
