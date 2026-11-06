# OTAFIX WebAssembly research prototype

This is **not wired into the updater or authorized for use on hardware**.
The modules compile the real `ota_delta_check_and_apply` code from the
following tags in `https://github.com/mikecarper/Adafruit_nRF52_Bootloader_OTAFIX`:

| Release | Peeled commit |
| --- | --- |
| 2.4.3 | `243b061f60130b8f2d0b930af6c49955be8ca537` |
| 2.4.6 | `250559c2cc7087066e9920fbeafbca5aacba0ea4` |
| 2.4.9 | `46d89a6f550b97053d6a44f55f41d371ec19d68c` |
| 2.4.11 | `c63b00d88ea9322c9d1c73a8dd505550f479c5fe` |
| 2.4.12 | `28e098afa330aa7c5507404b00213e9e4a06ba41` |

The source directories contain unmodified files from these commits and their
upstream MIT licenses. Each `src/detools/detools.c` and `.h` has its own BSD
2-Clause license notice. The upstream SHA-256 implementation declares itself
public domain. The separate release inventory records official binary identities;
the same release tag alone does not establish binary/source equivalence.

The adapter models internal NOR flash and the bootloader's retained registers.
It deliberately rejects QSPI, SD and hybrid-RAM source reads: these modules
must only be tested with the internal-flash handoff. It does not emulate the
processor, hardware timing, resets, or all physical flash failure modes. Each
apply needs a fresh module instance, an exact verified source image, a matching
bootloader build/profile, and full output comparison. The module must never be
selected based on version alone.

Build with WASI SDK 34.0 for x86_64 Linux:

```sh
WASI_SDK_PATH=/path/to/wasi-sdk-34.0-x86_64-linux python3 build.py
```

The official `wasi-sdk-34.0-x86_64-linux.tar.gz` used here has SHA-256
`b761e3a0721dbae9c09a0059e5fdb2bf917d1b4a8a7b430fb3b5aafb0984b2c4`.
The resulting modules have no host imports. The build models the released
RAK3401 and RAK4631 compile profiles, with the legacy S140 6.1.1 application
base at `0x26000`; profile-specific preprocessor flags are in `build.py`.
The 2.4.6 and 2.4.9 internal RAK builds include their tagged 64 KiB RAM-arena
compile flag; the simulator still rejects hybrid-RAM handoffs. The 2.4.11 and
2.4.12 releases use adaptive profiles. Hybrid packages remain
unsupported by the adapter. `worker.js` is a research harness only and does
not authenticate or identify a target device.

After building, run the C/WASM and real-browser checks from the repository's
`test` directory. Node and a Chromium-family browser are required:

```sh
MESHCORE_REQUIRE_OTAFIX_WASM=1 python -m unittest test_otafix_wasm test_otafix_wasm_browser -v
```

The environment flag makes missing prerequisites or modules fail the tests
instead of skipping them. Both checks compare exact reconstructed bytes; the
browser test checks two consecutive generated updates on every module. These
tests do not demonstrate hardware equivalence or authorize live updates.
