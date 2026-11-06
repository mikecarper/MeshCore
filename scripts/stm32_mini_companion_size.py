"""Retain Mini Companion size and dead-section policy in final LTO codegen."""

Import("env")

if env["PIOENV"] == "wio-e5-mini_companion_radio_usb":
    # Compile-time sections do not survive monolithic LTO code generation unless
    # requested again here. Keep dead virtual/default bodies independently
    # collectible after their unneeded constructor vtables are discarded.
    env.Append(LINKFLAGS=["-fno-tree-loop-im", "-ffunction-sections", "-fdata-sections",
                          "-fno-tree-pre", "-fno-tree-slsr", "-fno-code-hoisting"])
