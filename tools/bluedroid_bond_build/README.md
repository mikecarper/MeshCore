# Bluedroid archive reproduction

These tools reproduce the matched ESP32-S3 objects listed in the checked-in
manifest. New MCU or SDK combinations require their own object comparisons,
build checks and functional tests before adding their hashes.

For the initial ESP32-S3 investigation, the installed framework identified itself
as Arduino-ESP32 2.0.17, the compiler reported crosstool-NG
`esp-2021r2-patch5` GCC 8.4.0, and the local ESP-IDF source checkout was commit
`e499576efdb086551abe309a72899302f82077b7`. The stock S3 `libbt.a` SHA-256 was
`1cc7d2371f7df850b9cdfcb5f4f688f8bffbac007fd6de5828589f84b7d32af7`; the
`dio_qspi/include/sdkconfig.h` hash was
`6fb31ef78f105d86f3c48a9ca23fa2fe583549ad305fa487667703c6c40ddf18`.
These fingerprints describe the examined installation, not blanket approval for
any MCU or framework package. Other MCUs and newer cores need separate work.

The local rebuild of the **unmodified** `btc_dm.c`, `bta_dm_act.c` and `btm_sec.c`
objects used `-mlongcalls -ggdb -Os -std=gnu99 -ffunction-sections
-fdata-sections -freorder-blocks -fstack-protector -fstrict-volatile-bitfields
-fno-jump-tables -fno-tree-switch-conversion -DESP_PLATFORM -D_GNU_SOURCE
-include sdkconfig.h`, with ordered headers from that matching IDF checkout and
the installed S3 framework. Its allocated sections, relocations and global symbols
matched stock after accounting for generated function-name section numbering;
`compare_objects.py` repeats those checks on the object files. It requires
`pyelftools`. This does not prove changed code, whole-SDK ABI, or hardware
behavior.

`build_s3_candidate.py` records the compiler options and ordered include paths
for this exact SDK and toolchain. It checks their hashes, verifies the three
unmodified objects against the stock library, builds generated sources in an
isolated directory, and checks the archive member list and every member after
replacement. Two independent local runs yielded identical archive hashes; this
is not independent verification on another machine. Invoke it with the IDF
`components/bt` source tree, installed S3 SDK directory, toolchain `bin`
directory and an output archive path that does not exist. For example, from
PowerShell with those paths in `$btSource`, `$s3Sdk` and `$s3Toolchain`:

```powershell
python tools/bluedroid_bond_build/build_s3_candidate.py `
  --idf-bt $btSource --sdk $s3Sdk --toolchain-bin $s3Toolchain `
  --output C:/temp/mesh-bt-s3-candidate/libbt.a
```

The builder produces a candidate archive; it never modifies the installed SDK
or updates the manifest. `package_objects.py` verifies the candidate differs
only in the intended objects and creates the distributable ZIP. The manifest
binds stock, ZIP, individual objects and final reconstructed archive hashes.
See `docs/test-results/bluedroid_bond_preservation.md` for completed validation
and limits. A matching hash alone does not establish runtime behavior.
